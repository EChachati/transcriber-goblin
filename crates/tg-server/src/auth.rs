use axum::http::HeaderMap;
use axum::http::StatusCode;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hmac::{Hmac, Mac};
use rand::rngs::OsRng;
use rand::RngCore;
use rusqlite::Connection;
use sha2::{Digest, Sha256};

use crate::config::Settings;
use crate::error::ApiError;

pub fn hash_token(token: &str) -> String {
    let mut h = Sha256::new();
    h.update(token.as_bytes());
    hex(h.finalize())
}

fn hex(bytes: impl AsRef<[u8]>) -> String {
    bytes.as_ref().iter().map(|b| format!("{b:02x}")).collect()
}

pub fn token_from_bearer(headers: &HeaderMap) -> Option<&str> {
    let hdr = headers
        .get(axum::http::header::AUTHORIZATION)?
        .to_str()
        .ok()?;
    let s = hdr.strip_prefix("Bearer ")?.trim();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

/// Admín: comparación en tiempo constante con el admin token (paridad `hmac.compare_digest`).
pub fn is_admin(headers: &HeaderMap, settings: &Settings) -> bool {
    match token_from_bearer(headers) {
        Some(supplied) => admin_compare(supplied, &settings.admin_token),
        None => false,
    }
}

fn admin_compare(supplied: &str, expected: &str) -> bool {
    type HmacSha256 = Hmac<Sha256>;
    // comparación de mac determinista -> constante en práctica para el borrado
    let mut mac = HmacSha256::new_from_slice(b"tg").unwrap();
    mac.update(supplied.as_bytes());
    let a = mac.finalize().into_bytes();
    let mut mac2 = HmacSha256::new_from_slice(b"tg").unwrap();
    mac2.update(expected.as_bytes());
    let b = mac2.finalize().into_bytes();
    let (long, short) = if a.len() >= b.len() {
        (&a[..], &b[..])
    } else {
        (&b[..], &a[..])
    };
    long.iter()
        .zip(short.iter())
        .fold(0, |acc, (x, y)| acc | (x ^ y))
        == 0
        && a.len() == b.len()
}

/// Usuario actual por bearer token (`get_current_user`).
pub fn current_user(headers: &HeaderMap, conn: &Connection) -> Result<(String, String), ApiError> {
    let supplied =
        token_from_bearer(headers).ok_or_else(|| ApiError::unauthorized("missing bearer token"))?;
    let mut stmt = conn
        .prepare("SELECT id, name FROM users WHERE token_hash = ?")
        .map_err(|e| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let row = stmt
        .query_row([hash_token(supplied)], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
        })
        .ok();
    match row {
        Some((id, name)) => Ok((id, name)),
        None => Err(ApiError::unauthorized("invalid token")),
    }
}

/// Token de doc (para la superficie y-sweet: /d/{id}/... y ws). HMAC determinista.
pub fn doc_token(doc_id: &str, settings: &Settings) -> String {
    type HmacSha256 = Hmac<Sha256>;
    let mut mac = HmacSha256::new_from_slice(settings.admin_token.as_bytes()).unwrap();
    mac.update(doc_id.as_bytes());
    URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes())
}

pub fn verify_doc_token(doc_id: &str, token: &str, settings: &Settings) -> bool {
    let expected = doc_token(doc_id, settings);
    expected.len() == token.len() && admin_compare(token, &expected)
}

/// Crea una invitación (con expiración SQLite en días). Devuelve el código.
pub fn create_invite(conn: &mut Connection, days_valid: Option<i64>) -> Result<String, ApiError> {
    let code = format!("goblin-{}", random_urlsafe(9));
    match days_valid {
        Some(d) => conn.execute(
            "INSERT INTO invites (code, expires_at) VALUES (?1, datetime('now', ?2))",
            rusqlite::params![code, format!("+{d} days")],
        )?,
        None => conn.execute(
            "INSERT INTO invites (code) VALUES (?1)",
            rusqlite::params![code],
        )?,
    };
    Ok(code)
}

/// Canjea una invitación creando el usuario (paridad con `redeem_invite`).
pub fn redeem_invite(
    conn: &mut Connection,
    code: &str,
    name: &str,
) -> Result<serde_json::Value, ApiError> {
    let code = code.trim();
    let invite_exists: Option<bool> = conn
        .query_row(
            "SELECT 1 FROM invites WHERE code = ?1",
            rusqlite::params![code],
            |r| r.get(0),
        )
        .ok();
    if invite_exists.is_none() {
        return Err(ApiError::not_found("invite not found"));
    }
    let used_by: Option<String> = conn
        .query_row(
            "SELECT used_by FROM invites WHERE code = ?1",
            rusqlite::params![code],
            |r| r.get(0),
        )
        .ok()
        .flatten();
    if used_by.is_some() {
        return Err(ApiError::conflict("invite already used"));
    }
    let expired: Option<i64> = conn
        .query_row(
            "SELECT 1 FROM invites WHERE code = ?1 AND expires_at IS NOT NULL AND expires_at < datetime('now')",
            rusqlite::params![code],
            |r| r.get(0),
        )
        .ok()
        .flatten();
    if expired.is_some() {
        return Err(ApiError::new(StatusCode::GONE, "invite expired"));
    }
    let name_taken: Option<i64> = conn
        .query_row(
            "SELECT 1 FROM users WHERE name = ?1",
            rusqlite::params![name],
            |r| r.get(0),
        )
        .ok()
        .flatten();
    if name_taken.is_some() {
        return Err(ApiError::conflict("name already taken"));
    }

    let user_id = random_hex(8);
    let token = random_urlsafe(24);
    conn.execute(
        "INSERT INTO users (id, name, token_hash) VALUES (?1, ?2, ?3)",
        rusqlite::params![user_id, name, hash_token(&token)],
    )?;
    conn.execute(
        "UPDATE invites SET used_by = ?1 WHERE code = ?2",
        rusqlite::params![user_id, code],
    )?;
    Ok(serde_json::json!({ "user_id": user_id, "name": name, "token": token }))
}

pub fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    OsRng.fill_bytes(&mut buf);
    hex(buf)
}

fn random_urlsafe(chars: usize) -> String {
    // token_urlsafe = base64url de n bytes de entropía (padded ~= len bytes)
    let bytes = chars;
    let mut buf = vec![0u8; bytes];
    OsRng.fill_bytes(&mut buf);
    URL_SAFE_NO_PAD.encode(&buf)
}
