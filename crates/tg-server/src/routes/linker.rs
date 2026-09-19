use axum::extract::{Path, Query, State};
use axum::http::HeaderMap;
use axum::http::StatusCode;
use axum::routing::get;
use axum::routing::post;
use axum::{Json, Router};
use rusqlite::params;
use serde::Deserialize;
use serde_json::json;
use tg_linker::LinkEdit;

use crate::auth;
use crate::error::{ApiError, ApiResult};
use crate::linker as linker_svc;
use crate::routes::support::{check_access, doc_exists, has_access};
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/linker/graph", get(graph))
        .route("/linker/{doc_id}/keywords", get(keywords))
        .route(
            "/linker/{doc_id}/aliases",
            get(list_aliases).post(add_alias).delete(remove_alias),
        )
        .route("/linker/proposals", get(list_proposals))
        .route("/linker/proposals/{proposal_id}", post(act_proposal))
        .route("/linker/run", post(run))
}

async fn graph(
    headers: HeaderMap,
    State(st): State<AppState>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    let mut stmt = db.prepare(
        "SELECT d.id, d.title FROM docs d
         JOIN doc_access a ON a.doc_id = d.id WHERE a.user_id = ?1",
    )?;
    let rows: Vec<(String, String)> = stmt
        .query_map(params![user_id], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<Result<_, _>>()
        .map_err(ApiError::from)?;
    drop(stmt);
    drop(db);

    let nodes: Vec<_> = rows
        .iter()
        .map(|(id, title)| json!({ "id": id, "title": title }))
        .collect();

    let mut edges = Vec::new();
    for (id, _) in &rows {
        let content = linker_svc::read_mirror(&st.settings.mirror_dir, id);
        for target_title in linker_svc::extract_wikilinks(&content) {
            edges.push(json!({ "source": id, "target_title": target_title }));
        }
    }
    Ok(Json(json!({ "nodes": nodes, "edges": edges })))
}

#[derive(Deserialize)]
struct TopN {
    top_n: Option<usize>,
}

async fn keywords(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
    Query(q): Query<TopN>,
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
    let words = linker_svc::keywords_for_doc(&st, &doc_id, &user_id, q.top_n.unwrap_or(10)).await?;
    Ok(Json(json!({ "doc_id": doc_id, "keywords": words })))
}

async fn list_aliases(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    check_access(&db, &doc_id, &user_id)?;
    let mut stmt =
        db.prepare("SELECT alias, source FROM aliases WHERE doc_id = ? ORDER BY alias")?;
    let aliases = stmt
        .query_map(params![doc_id], |r| {
            Ok(json!({ "alias": r.get::<_, String>(0)?, "source": r.get::<_, String>(1)? }))
        })?
        .collect::<Result<Vec<_>, _>>()
        .map_err(ApiError::from)?;
    drop(stmt);
    drop(db);
    Ok(Json(json!({ "doc_id": doc_id, "aliases": aliases })))
}

#[derive(Deserialize)]
struct AliasBody {
    doc_id: String,
    alias: String,
}

async fn add_alias(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
    Json(body): Json<AliasBody>,
) -> ApiResult<Json<serde_json::Value>> {
    if body.doc_id != doc_id {
        return Err(ApiError::bad_request("doc_id mismatch"));
    }
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    check_access(&db, &doc_id, &user_id)?;
    let alias = body.alias.trim().to_string();
    if alias.is_empty() {
        return Err(ApiError::bad_request("alias cannot be empty"));
    }
    match db.execute(
        "INSERT INTO aliases (doc_id, alias, source) VALUES (?1, ?2, 'manual')",
        params![doc_id, alias],
    ) {
        Ok(_) => Ok(Json(json!({ "doc_id": doc_id, "alias": alias }))),
        Err(rusqlite::Error::SqliteFailure(e, _))
            if e.code == rusqlite::ErrorCode::ConstraintViolation =>
        {
            Err(ApiError::conflict("alias already exists"))
        }
        Err(e) => Err(ApiError::from(e)),
    }
}

async fn remove_alias(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
    Json(body): Json<AliasBody>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    check_access(&db, &doc_id, &user_id)?;
    db.execute(
        "DELETE FROM aliases WHERE doc_id = ? AND alias = ?",
        params![doc_id, body.alias],
    )?;
    drop(db);
    Ok(Json(json!({ "removed": true })))
}

#[derive(Deserialize)]
struct ListProposals {
    status: Option<String>,
}

async fn list_proposals(
    headers: HeaderMap,
    State(st): State<AppState>,
    Query(q): Query<ListProposals>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    let mut stmt = db.prepare(
        "SELECT p.rowid AS id, p.doc_id, p.target_id, p.alias, p.confidence, p.reason,
                d.title AS target_title
         FROM linker_proposals p JOIN docs d ON d.id = p.target_id
         WHERE p.status = ?1 AND p.doc_id IN (
             SELECT doc_id FROM doc_access WHERE user_id = ?2
         )
         ORDER BY p.confidence DESC",
    )?;
    let rows = stmt
        .query_map(
            params![q.status.as_deref().unwrap_or("pending"), user_id],
            |r| {
                Ok(json!({
                    "id": r.get::<_, i64>("id")?,
                    "doc_id": r.get::<_, String>("doc_id")?,
                    "target_id": r.get::<_, String>("target_id")?,
                    "alias": r.get::<_, String>("alias")?,
                    "confidence": r.get::<_, f64>("confidence")?,
                    "reason": r.get::<_, String>("reason")?,
                    "target_title": r.get::<_, String>("target_title")?,
                }))
            },
        )?
        .collect::<Result<Vec<_>, _>>()
        .map_err(ApiError::from)?;
    drop(stmt);
    drop(db);
    Ok(Json(json!(rows)))
}

#[derive(Deserialize)]
struct ProposalAction {
    doc_id: String,
    action: String,
}

async fn act_proposal(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(proposal_id): Path<String>,
    Json(body): Json<ProposalAction>,
) -> ApiResult<Json<serde_json::Value>> {
    if !(body.action == "apply" || body.action == "dismiss") {
        return Err(ApiError::bad_request("action must be 'apply' or 'dismiss'"));
    }
    if body.doc_id.is_empty() {
        return Err(ApiError::bad_request("doc_id required"));
    }
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    let (p_doc, p_alias) = match db.query_row(
        "SELECT doc_id, alias FROM linker_proposals WHERE rowid = ?",
        params![proposal_id],
        |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
    ) {
        Ok(v) => v,
        Err(rusqlite::Error::QueryReturnedNoRows) => {
            return Err(ApiError::not_found("proposal not found"))
        }
        Err(e) => return Err(ApiError::from(e)),
    };
    if !has_access(&db, &p_doc, &user_id)? {
        return Err(ApiError::forbidden("no access to this doc"));
    }

    if body.action == "dismiss" {
        db.execute(
            "UPDATE linker_proposals SET status = 'dismissed' WHERE rowid = ?",
            params![proposal_id],
        )?;
        drop(db);
        return Ok(Json(json!({ "ok": true })));
    }

    let (target_id, target_title) = {
        let mut stmt = db.prepare(
            "SELECT p.target_id, d.title FROM linker_proposals p JOIN docs d ON d.id = p.target_id
             WHERE p.rowid = ?",
        )?;
        let row = stmt.query_row(params![proposal_id], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
        });
        match row {
            Ok(v) => v,
            Err(rusqlite::Error::QueryReturnedNoRows) => {
                return Err(ApiError::not_found("proposal not found"))
            }
            Err(e) => return Err(ApiError::from(e)),
        }
    };
    drop(db);

    let crdt_doc = st.crdt.open_or_create(&p_doc).await.map_err(db_err)?;
    let content = crdt_doc.read_text().await;
    let wikilink = format!("[[{target_title}]]");
    if content.contains(&wikilink) {
        return Ok(Json(json!({ "ok": true })));
    }
    let Some((start, end)) = linker_svc::match_literal_offset(&content, &p_alias) else {
        return Ok(Json(json!({ "ok": false })));
    };
    crdt_doc
        .apply_edits(vec![LinkEdit {
            start,
            end,
            replacement: wikilink,
            target_id,
        }])
        .await
        .map_err(db_err)?;

    let db = st.db.lock().await;
    db.execute(
        "UPDATE linker_proposals SET status = 'applied' WHERE rowid = ?",
        params![proposal_id],
    )?;
    drop(db);
    Ok(Json(json!({ "ok": true })))
}

#[derive(Deserialize)]
struct RunParams {
    target: Option<String>,
}

async fn run(
    headers: HeaderMap,
    State(st): State<AppState>,
    Query(q): Query<RunParams>,
) -> ApiResult<Json<serde_json::Value>> {
    let db = st.db.lock().await;
    let (user_id, _) = auth::current_user(&headers, &db)?;
    drop(db);
    let out = linker_svc::run_linker(&st, q.target.as_deref().unwrap_or("crdt"), &user_id).await?;
    Ok(Json(out))
}

fn db_err(e: anyhow::Error) -> ApiError {
    ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}
