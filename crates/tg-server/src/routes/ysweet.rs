//! Superficie compatible con y-sweet servida por este mismo server (arquitectura B):
//! el plugin Obsidian y el transcriber no distinguen. Autenticación de /d/* con el
//! token de doc (HMAC determinista); /doc/* con el bearer de admín.

use std::sync::Arc;

use axum::extract::{Path, Query, State, WebSocketUpgrade};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::json;
use tokio::sync::Mutex;
use yrs_axum::broadcast::BroadcastGroup;
use yrs_axum::ws::{AxumSink, AxumStream};

use crate::auth;
use crate::error::{ApiError, ApiResult};
use crate::routes::support::client_token;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/doc/new", post(doc_new))
        .route("/doc/{doc_id}/auth", post(doc_auth))
        .route("/d/{doc_id}/as-update", get(as_update))
        .route("/d/{doc_id}/update", post(apply_update_route))
        .route("/d/{doc_id}/ws/{ws_id}", get(ws_connect))
}

#[derive(Deserialize)]
struct DocNew {
    #[serde(alias = "docId")]
    doc_id: Option<String>,
}

async fn doc_new(
    headers: HeaderMap,
    State(st): State<AppState>,
    Json(body): Json<DocNew>,
) -> ApiResult<Json<serde_json::Value>> {
    if !auth::is_admin(&headers, &st.settings) {
        return Err(ApiError::unauthorized("admin token required"));
    }
    let doc_id = match body.doc_id {
        Some(id) if !id.is_empty() => id,
        _ => auth::random_hex(8),
    };
    st.crdt.open_or_create(&doc_id).await.map_err(as_api)?;
    Ok(Json(json!({ "docId": doc_id })))
}

async fn doc_auth(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
) -> ApiResult<Json<serde_json::Value>> {
    if !auth::is_admin(&headers, &st.settings) {
        return Err(ApiError::unauthorized("admin token required"));
    }
    st.crdt.open_or_create(&doc_id).await.map_err(as_api)?;
    Ok(Json(client_token(&st, &doc_id)))
}

fn require_doc_token(headers: &HeaderMap, st: &AppState, doc_id: &str) -> ApiResult<()> {
    let supplied = auth::token_from_bearer(headers)
        .ok_or_else(|| ApiError::unauthorized("doc token required"))?;
    if !auth::verify_doc_token(doc_id, supplied, &st.settings) {
        return Err(ApiError::unauthorized("invalid doc token"));
    }
    Ok(())
}

async fn as_update(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
) -> ApiResult<Response> {
    require_doc_token(&headers, &st, &doc_id)?;
    let doc = st.crdt.open_or_create(&doc_id).await.map_err(as_api)?;
    let bytes = doc.state_update().await;
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/octet-stream")
        .body(axum::body::Body::from(bytes))
        .map_err(|e| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))
}

async fn apply_update_route(
    headers: HeaderMap,
    State(st): State<AppState>,
    Path(doc_id): Path<String>,
    body: axum::body::Body,
) -> ApiResult<Json<serde_json::Value>> {
    require_doc_token(&headers, &st, &doc_id)?;
    let bytes = axum::body::to_bytes(body, 64 * 1024 * 1024)
        .await
        .map_err(|e| ApiError::new(StatusCode::BAD_REQUEST, e.to_string()))?;
    let doc = st.crdt.open_or_create(&doc_id).await.map_err(as_api)?;
    doc.apply_update_bytes(&bytes).await.map_err(as_api)?;
    Ok(Json(json!({ "ok": true })))
}

#[derive(Deserialize)]
struct WsQuery {
    token: Option<String>,
}

async fn ws_connect(
    State(st): State<AppState>,
    Path((doc_id, _ws_id)): Path<(String, String)>,
    Query(q): Query<WsQuery>,
    ws: WebSocketUpgrade,
) -> ApiResult<impl IntoResponse> {
    let token = q
        .token
        .ok_or_else(|| ApiError::unauthorized("doc token required"))?;
    if !auth::verify_doc_token(&doc_id, &token, &st.settings) {
        return Err(ApiError::unauthorized("invalid doc token"));
    }
    let doc = st.crdt.open_or_create(&doc_id).await.map_err(as_api)?;
    let bcast = doc.bcast.clone();
    Ok(ws.on_upgrade(move |socket| peer(socket, bcast)))
}

async fn peer(ws: axum::extract::ws::WebSocket, bcast: Arc<BroadcastGroup>) {
    let (sink, stream) = ws.split();
    let sink = Arc::new(Mutex::new(AxumSink::from(sink)));
    let stream = AxumStream::from(stream);
    let sub = bcast.subscribe(sink, stream);
    let _ = sub.completed().await;
}

fn as_api(e: anyhow::Error) -> ApiError {
    ApiError::from(e)
}
