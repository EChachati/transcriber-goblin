//! Rutas de usuario: docs (CRUD + token), invitaciones, me, adjuntos, linker.
use axum::extract::{DefaultBodyLimit, Multipart, Path, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use rusqlite::params;
use serde::Deserialize;
use serde_json::json;
use sha2::Digest;

pub mod linker;
pub mod support;
pub mod ysweet;

use crate::auth;
use crate::error::{ApiError, ApiResult};
use crate::routes::support::{doc_exists, has_access, is_sha256};
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health", get(health))
        .route("/me", get(me))
        .route("/invites", post(new_invite))
        .route("/auth/redeem", post(redeem))
        .route("/docs", get(list_docs).post(create_doc))
        .route("/docs/{doc_id}/token", post(doc_token))
        .route("/attachments", post(upload_attachment))
        .route("/attachments/{sha256}", get(download_attachment))
        .layer(DefaultBodyLimit::max(100 * 1024 * 1024))
}

async fn health() -> Json<serde_json::Value> {
    Json(json!({ "ok": true, "service": "transcriber-goblin" }))
}

async fn me(headers: HeaderMap, State(st): State<AppState>) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (id, name) = auth::current_user(&headers, &db)?;
    Ok(Json(json!({ "id": id, "name": name })))
}

#[derive(Deserialize)]
struct InviteCreate {
    days_valid: Option<i64>,
}

async fn new_invite(
    headers: HeaderMap,
    State(st): State<AppState>,
    Json(body): Json<InviteCreate>,
) -> ApiResult<Json<serde_json::Value>> {
    if !auth::is_admin(&headers, &st.settings) {
        return Err(ApiError::unauthorized("admin token required"));
    }
    let mut db = st.db.lock().await;
    let code = auth::create_invite(&mut db, body.days_valid)?;
    let row = db
        .query_row(
            "SELECT * FROM invites WHERE code = ?1",
            params![code],
            |r| {
                Ok(json!({
                    "code": r.get::<_, String>("code")?,
                    "created_at": r.get::<_, String>("created_at")?,
                    "expires_at": r.get::<_, Option<String>>("expires_at")?,
                    "used_by": r.get::<_, Option<String>>("used_by")?,
                }))
            },
        )
        .map_err(api_db)?;
    Ok(Json(row))
}

#[derive(Deserialize)]
struct RedeemBody {
    code: String,
    name: String,
}

async fn redeem(
    State(st): State<AppState>,
    Json(body): Json<RedeemBody>,
) -> ApiResult<Json<serde_json::Value>> {
    let mut db = st.db.lock().await;
    let out = auth::redeem_invite(&mut db, &body.code, &body.name)?;
    Ok(Json(out))
}

#[derive(Deserialize)]
struct DocCreate {
    title: String,
}

async fn create_doc(
    headers: HeaderMap,
    State(st): State<AppState>,
    Json(body): Json<DocCreate>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    let doc_id = auth::random_hex(8);
    // el CRDT se crea para que exista el ws/url antes de responder
    st.crdt
        .open_or_create(&doc_id)
        .await
        .map_err(|e| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    db.execute(
        "INSERT INTO docs (id, title, owner_id) VALUES (?1, ?2, ?3)",
        params![doc_id, body.title, user_id],
    )?;
    db.execute(
        "INSERT INTO doc_access (doc_id, user_id, role) VALUES (?1, ?2, 'owner')",
        params![doc_id, user_id],
    )?;
    Ok(Json(json!({ "id": doc_id, "title": body.title })))
}

async fn list_docs(
    headers: HeaderMap,
    State(st): State<AppState>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    let mut stmt = db
        .prepare(
            "SELECT d.id, d.title, d.created_at, a.role
             FROM docs d JOIN doc_access a ON a.doc_id = d.id
             WHERE a.user_id = ?1 ORDER BY d.created_at DESC",
        )
        .map_err(api_db)?;
    let rows = stmt
        .query_map(params![user_id], |r| {
            Ok(json!({
                "id": r.get::<_, String>("id")?,
                "title": r.get::<_, String>("title")?,
                "created_at": r.get::<_, String>("created_at")?,
                "role": r.get::<_, String>("role")?,
            }))
        })
        .map_err(api_db)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(api_db)?;
    Ok(Json(json!(rows)))
}

async fn doc_token(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    if !doc_exists(&db, &doc_id)? {
        return Err(ApiError::not_found("doc not found"));
    }
    if !has_access(&db, &doc_id, &user_id)? {
        return Err(ApiError::forbidden("no access to this doc"));
    }
    drop(db);
    // Asegura el doc en el CRDT (no debería faltar: se crea con el doc).
    st.crdt
        .open_or_create(&doc_id)
        .await
        .map_err(|e| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(support::client_token(&st, &doc_id)))
}

async fn upload_attachment(
    headers: HeaderMap,
    State(st): State<AppState>,
    mut multipart: Multipart,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;

    let mut field = multipart
        .next_field()
        .await
        .map_err(api_db)?
        .ok_or_else(|| ApiError::bad_request("missing 'file' field"))?;
    let filename = field.file_name().unwrap_or("unnamed").to_string();
    let content_type = field
        .content_type()
        .unwrap_or("application/octet-stream")
        .to_string();

    let mut sha = sha2::Sha256::new();
    let dir = &st.settings.attachments_dir;
    std::fs::create_dir_all(dir).map_err(api_db)?;
    let tmp = dir.join(format!("upload-{}", std::process::id()));
    let mut size: u64 = 0;
    {
        let mut out = std::fs::File::create(&tmp).map_err(api_db)?;
        while let Some(chunk) = field.chunk().await.map_err(api_db)? {
            sha.update(&chunk);
            size += chunk.len() as u64;
            use std::io::Write;
            out.write_all(&chunk).map_err(api_db)?;
        }
    }
    let digest = hex(sha.finalize());
    let (sub, file) = (&digest[..2], &digest);
    let folder = dir.join(sub);
    std::fs::create_dir_all(&folder).map_err(api_db)?;
    let final_path = folder.join(file);
    if !final_path.exists() {
        match std::fs::rename(&tmp, &final_path) {
            Ok(()) => {}
            Err(_) if final_path.exists() => {
                let _ = std::fs::remove_file(&tmp);
            }
            Err(e) => {
                let _ = std::fs::remove_file(&tmp);
                return Err(ApiError::new(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    e.to_string(),
                ));
            }
        }
    } else {
        let _ = std::fs::remove_file(&tmp);
    }
    db.execute(
        "INSERT INTO attachments (sha256, filename, content_type, size, uploaded_by)
         VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(sha256) DO UPDATE SET uploaded_by = excluded.uploaded_by",
        params![digest, filename, content_type, size, user_id],
    )?;
    Ok(Json(
        json!({ "sha256": digest, "size": size, "uri": format!("attachment://{digest}") }),
    ))
}

async fn download_attachment(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(sha256): Path<String>,
) -> ApiResult<impl IntoResponse> {
    if !is_sha256(&sha256) {
        return Err(ApiError::bad_request("invalid hash"));
    }
    let db = st.db.lock().await;
    let _ = auth::current_user(&headers, &db)?;
    let folder = st.settings.attachments_dir.join(&sha256[..2]);
    let path = folder.join(&sha256);
    if !path.exists() {
        return Err(ApiError::not_found("attachment not found"));
    }
    drop(db);
    let (content_type, filename) = {
        let db = st.db.lock().await;
        match db.query_row(
            "SELECT content_type, filename FROM attachments WHERE sha256 = ?1",
            params![sha256],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
        ) {
            Ok(v) => v,
            Err(_) => ("application/octet-stream".to_string(), sha256.clone()),
        }
    };
    let bytes = tokio::fs::read(&path).await.map_err(api_io)?;
    let res = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, content_type)
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"{filename}\""),
        )
        .body(axum::body::Body::from(bytes))
        .map_err(|e| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(res)
}

// ---- helpers compartidos ----

fn hex(bytes: impl AsRef<[u8]>) -> String {
    bytes.as_ref().iter().map(|b| format!("{b:02x}")).collect()
}

pub fn api_db(e: impl std::fmt::Display) -> ApiError {
    ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}

fn api_io(e: std::io::Error) -> ApiError {
    ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}
