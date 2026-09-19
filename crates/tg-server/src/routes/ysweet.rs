//! Superficie compatible con y-sweet servida por este mismo server (arquitectura B):
//! el plugin Obsidian y el transcriber no distinguen. Autenticación de /d/* con el
//! token de doc (HMAC determinista); /doc/* con el bearer de admín.
//!
//! El peer WS implementa y-protocols a mano (codificación de yjs, sin el prefijo de
//! longitud que yrs usa en SyncStep1) para ser compatible byte a byte con el plugin.

use std::sync::Arc;

use axum::extract::ws::{Message as WsMessage, WebSocket};
use axum::extract::{Path, Query, State, WebSocketUpgrade};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::json;
use yrs::updates::decoder::Decode as _;
use yrs::updates::encoder::Encode as _;
use yrs::{ReadTxn, StateVector, Transact};

use crate::auth;
use crate::crdt::CrdtDoc;
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
    Ok(ws.on_upgrade(move |socket| peer(socket, doc)))
}

/// Varint unsigned de yjs en `data[i..]`; devuelve valor y nueva posición.
fn read_var(data: &[u8], i: &mut usize) -> Option<u64> {
    let mut val: u64 = 0;
    let mut shift = 0;
    loop {
        let b = *data.get(*i)?;
        *i += 1;
        val |= ((b & 0x7f) as u64) << shift;
        if b & 0x80 == 0 {
            return Some(val);
        }
        shift += 7;
        if shift >= 63 {
            return None;
        }
    }
}

fn push_var(out: &mut Vec<u8>, mut val: u64) {
    loop {
        let mut b = (val & 0x7f) as u8;
        val >>= 7;
        if val > 0 {
            b |= 0x80;
        }
        out.push(b);
        if val == 0 {
            return;
        }
    }
}

async fn encoderized_sync_step1(doc: &CrdtDoc) -> Option<Vec<u8>> {
    // [0 messageSync, 0 SyncStep1, count varint, (client, clock)* ] formato yjs.
    let out = {
        let aw = doc.awareness.read().await;
        let txn = aw.doc().transact();
        let sv = txn.state_vector();
        let sv_bytes = sv.encode_v1();
        let mut out = vec![0u8, 0];
        out.extend_from_slice(&sv_bytes);
        out
    };
    Some(out)
}

/// Respuesta a un SyncStep1 del cliente: Step2 con el diff desde su state vector.
async fn sync_step2_reply(doc: &CrdtDoc, client_sv: StateVector) -> Option<Vec<u8>> {
    let update = {
        let aw = doc.awareness.read().await;
        let txn = aw.doc().transact();
        txn.encode_state_as_update_v1(&client_sv)
    };
    let mut out = vec![0u8, 1];
    push_var(&mut out, update.len() as u64);
    out.extend_from_slice(&update);
    Some(out)
}

async fn peer(ws: WebSocket, doc: Arc<CrdtDoc>) {
    let (mut sink, mut stream) = ws.split();
    let mut rx = doc.subscribe();

    // El server inicia pidiendo el estado no sincronizado del cliente (como y-sweet).
    if let Some(bytes) = encoderized_sync_step1(&doc).await {
        if sink.send(WsMessage::Binary(bytes.into())).await.is_err() {
            return;
        }
    }

    loop {
        tokio::select! {
            incoming = stream.next() => {
                let Some(Ok(msg)) = incoming else { break };
                let WsMessage::Binary(data) = msg else { continue };
                let _ = handle_peer_message(&doc, data.to_vec()).await;
            }
            forward = rx.recv() => {
                let Ok(bytes) = forward else { break };
                if sink.send(WsMessage::Binary(bytes.into())).await.is_err() {
                    break;
                }
            }
        }
    }
}

async fn handle_peer_message(doc: &CrdtDoc, data: Vec<u8>) -> Result<(), ()> {
    let mut i = 0;
    let tag = read_var(&data, &mut i).ok_or(())?;
    if tag != 0 {
        // 1 = awareness (presencia): se ignora por ahora (TODO: apply para online status).
        return Ok(());
    }
    let st = read_var(&data, &mut i).ok_or(())?;
    match st {
        0 => {
            // SyncStep1 del cliente: state vector en formato yjs (= sv.encode_v1()).
            let sv = StateVector::decode_v1(&data[i..]).map_err(|_| ())?;
            let reply = sync_step2_reply(doc, sv).await.ok_or(())?;
            doc.broadcast(reply);
        }
        1 | 2 => {
            // SyncStep2 o Update del cliente: [tipo, varint len, bytes].
            let len = read_var(&data, &mut i).ok_or(())? as usize;
            let end = i.checked_add(len).filter(|e| *e <= data.len()).ok_or(())?;
            let update = &data[i..end];
            doc.apply_update_bytes(update).await.map_err(|_| ())?;
        }
        _ => {}
    }
    Ok(())
}

fn as_api(e: anyhow::Error) -> ApiError {
    ApiError::from(e)
}
