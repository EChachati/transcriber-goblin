use rusqlite::params;
use serde_json::json;

use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

pub fn doc_exists(db: &rusqlite::Connection, doc_id: &str) -> ApiResult<bool> {
    Ok(db
        .query_row("SELECT 1 FROM docs WHERE id = ?1", params![doc_id], |_| {
            Ok(())
        })
        .is_ok())
}

pub fn has_access(db: &rusqlite::Connection, doc_id: &str, user_id: &str) -> ApiResult<bool> {
    Ok(db
        .query_row(
            "SELECT 1 FROM doc_access WHERE doc_id = ?1 AND user_id = ?2",
            params![doc_id, user_id],
            |_| Ok(()),
        )
        .is_ok())
}

pub fn check_access(db: &rusqlite::Connection, doc_id: &str, user_id: &str) -> ApiResult<()> {
    if !doc_exists(db, doc_id)? {
        return Err(ApiError::not_found("doc not found"));
    }
    if !has_access(db, doc_id, user_id)? {
        return Err(ApiError::forbidden("no access to this doc"));
    }
    Ok(())
}

pub fn is_sha256(v: &str) -> bool {
    v.len() == 64 && v.chars().all(|c| c.is_ascii_hexdigit())
}

/// Forma del client token (formato y-sweet) servido por este server: el plugin
/// deriva la URL de ws desde `url` + `token` (ver `websocketUrl` en api.ts), por
/// eso `url` no lleva query.
pub fn client_token(st: &AppState, doc_id: &str) -> serde_json::Value {
    let token = crate::auth::doc_token(doc_id, &st.settings);
    let http_base = st.settings.http_base();
    let ws_base = st.settings.public_url.trim_end_matches('/').to_string();
    json!({
        "docId": doc_id,
        "baseUrl": format!("{http_base}/d/{doc_id}"),
        "url": format!("{ws_base}/d/{doc_id}/ws"),
        "token": token,
    })
}
