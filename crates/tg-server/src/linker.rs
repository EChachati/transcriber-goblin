//! Integración del linker puro (`tg-linker`) con el CRDT y la BD: equivalente a
//! `server/app/linker.py`.

use std::collections::BTreeSet;

use rusqlite::{params, Connection};
use serde_json::json;
use tg_linker::keywords::keywords_tfidf;
use tg_linker::{link_doc, LinkEdit, NoteSpec};

use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

pub fn load_notes(db: &Connection) -> ApiResult<Vec<NoteSpec>> {
    let mut stmt = db.prepare(
        "SELECT d.id, d.title,
                COALESCE((SELECT GROUP_CONCAT(a.alias, '||') FROM aliases a
                          WHERE a.doc_id = d.id), '')
         FROM docs d",
    )?;
    let rows = stmt
        .query_map([], |r| {
            let aliases_raw: String = r.get(2)?;
            let aliases: Vec<String> = aliases_raw
                .split("||")
                .filter(|a| !a.is_empty())
                .map(String::from)
                .collect();
            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, aliases))
        })?
        .collect::<Result<Vec<_>, _>>()
        .map_err(ApiError::from)?;
    Ok(rows
        .into_iter()
        .map(|(id, title, aliases)| NoteSpec { id, title, aliases })
        .collect())
}

/// Ejecuta el linker sobre todas las notas. `target` = "crdt" (default) | "mirror".
pub async fn run_linker(
    st: &AppState,
    target: &str,
    user_id: &str,
) -> ApiResult<serde_json::Value> {
    if target != "crdt" && target != "mirror" {
        return Err(ApiError::bad_request("target must be 'crdt' or 'mirror'"));
    }
    let db = st.db.lock().await;
    let notes = load_notes(&db)?;

    let allowed: BTreeSet<String> = db
        .prepare("SELECT doc_id FROM doc_access WHERE user_id = ?1")?
        .query_map(params![user_id], |r| r.get::<_, String>(0))?
        .collect::<Result<BTreeSet<_>, _>>()
        .map_err(ApiError::from)?;
    let auto_filter = allowed.clone();

    let mut auto_out = Vec::new();
    for note in &notes {
        let content = match target {
            "crdt" => {
                let doc = st.crdt.open_or_create(&note.id).await.map_err(as_api)?;
                doc.read_text().await
            }
            _ => read_mirror(&st.settings.mirror_dir, &note.id),
        };
        if content.trim().is_empty() {
            continue;
        }
        let res = link_doc(&content, &note.id, &notes);
        for p in &res.proposals {
            record_proposal(
                &db,
                p.doc_id.as_str(),
                &p.target_id,
                &p.literal,
                p.confidence,
                "pending",
            )?;
        }
        for a in &res.auto {
            record_proposal(&db, note.id.as_str(), &a.target_id, "auto", 1.0, "applied")?;
        }

        if res.auto.is_empty() {
            continue;
        }
        match target {
            "crdt" => {
                let doc = st.crdt.open_or_create(&note.id).await.map_err(as_api)?;
                doc.apply_edits(res.auto.iter().map(|a| a.edit.clone()).collect())
                    .await
                    .map_err(as_api)?;
            }
            _ => {
                let mut new_content = content.clone();
                apply_edits_inplace(
                    &mut new_content,
                    &res.auto.iter().map(|a| a.edit.clone()).collect::<Vec<_>>(),
                );
                write_mirror(&st.settings.mirror_dir, &note.id, &new_content);
            }
        }
        let added: Vec<String> = res
            .auto
            .iter()
            .map(|a| a.edit.replacement.clone())
            .filter(|r| r.starts_with("[["))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        auto_out.push(json!({ "doc_id": note.id, "links": added }));
    }

    let pending = pending_proposals(&db)?
        .into_iter()
        .filter(|p| allowed.contains(p["doc_id"].as_str().unwrap_or("")))
        .collect::<Vec<_>>();

    Ok(json!({
        "auto": auto_out.into_iter().filter(|a| auto_filter.contains(a["doc_id"].as_str().unwrap_or(""))).collect::<Vec<_>>(),
        "proposals": pending,
        "notes": notes.len(),
    }))
}

/// Aplica edits en orden descendente sobre un String (offsets byte del original).
pub fn apply_edits_inplace(content: &mut String, edits: &[LinkEdit]) {
    let mut sorted = edits.to_vec();
    sorted.sort_by_key(|e| std::cmp::Reverse(e.start));
    for e in &sorted {
        content.replace_range(e.start..e.end, &e.replacement);
    }
}

pub fn read_mirror(mirror_dir: &std::path::Path, doc_id: &str) -> String {
    let path = mirror_dir.join(format!("{doc_id}.md"));
    std::fs::read_to_string(path).unwrap_or_default()
}

pub fn write_mirror(mirror_dir: &std::path::Path, doc_id: &str, content: &str) {
    let target = mirror_dir.join(format!("{doc_id}.md"));
    if target.exists() && std::fs::read_to_string(&target).is_ok_and(|c| c == content) {
        return;
    }
    let _ = std::fs::create_dir_all(mirror_dir);
    let tmp = target.with_extension("tmp");
    let _ = std::fs::write(&tmp, content);
    let _ = std::fs::rename(&tmp, &target);
}

pub fn record_proposal(
    db: &Connection,
    doc_id: &str,
    target_id: &str,
    alias: &str,
    confidence: f64,
    status: &str,
) -> ApiResult<()> {
    db.execute(
        "INSERT INTO linker_proposals (doc_id, target_id, alias, confidence, reason, status)
         VALUES (?1, ?2, ?3, ?4, 'auto-detected', ?5)
         ON CONFLICT(doc_id, target_id, alias)
         DO UPDATE SET confidence = excluded.confidence, status = excluded.status",
        params![doc_id, target_id, alias, confidence, status],
    )?;
    Ok(())
}

pub fn pending_proposals(db: &Connection) -> ApiResult<Vec<serde_json::Value>> {
    let mut stmt = db.prepare(
        "SELECT p.rowid AS id, p.doc_id, p.target_id, p.alias, p.confidence, p.reason, p.status,
                d.title AS target_title
         FROM linker_proposals p JOIN docs d ON d.id = p.target_id
         WHERE p.status = 'pending'
         ORDER BY p.confidence DESC",
    )?;
    let rows = stmt
        .query_map([], |r| {
            Ok(json!({
                "id": r.get::<_, i64>("id")?,
                "doc_id": r.get::<_, String>("doc_id")?,
                "target_id": r.get::<_, String>("target_id")?,
                "alias": r.get::<_, String>("alias")?,
                "confidence": r.get::<_, f64>("confidence")?,
                "reason": r.get::<_, String>("reason")?,
                "status": r.get::<_, String>("status")?,
                "target_title": r.get::<_, String>("target_title")?,
            }))
        })?
        .collect::<Result<Vec<_>, _>>()
        .map_err(ApiError::from)?;
    Ok(rows)
}

/// Contenido a efectos de keywords/índice: preferencia al CRDT si tiene texto, si no
/// al espejo (docs poblados por mirror/import antes de conectarse al CRDT).
pub async fn read_best(st: &AppState, doc_id: &str) -> String {
    if let Some(doc) = st.crdt.get(doc_id).await {
        let t = doc.read_text().await;
        if !t.trim().is_empty() {
            return t;
        }
    }
    read_mirror(&st.settings.mirror_dir, doc_id)
}

/// Keywords deterministas TF-IDF (corpus = textos a los que el usuario tiene acceso).
pub async fn keywords_for_doc(
    st: &AppState,
    doc_id: &str,
    user_id: &str,
    top_n: usize,
) -> ApiResult<Vec<String>> {
    let db = st.db.lock().await;
    let owns: bool = db
        .query_row(
            "SELECT 1 FROM doc_access WHERE doc_id=?1 AND user_id=?2",
            params![doc_id, user_id],
            |_| Ok(()),
        )
        .is_ok();
    if !owns {
        return Err(ApiError::forbidden("no access to this doc"));
    }
    let corpus_ids: Vec<String> = db
        .prepare("SELECT doc_id FROM doc_access WHERE user_id=?1")?
        .query_map(params![user_id], |r| r.get(0))?
        .collect::<Result<_, _>>()
        .map_err(ApiError::from)?;
    drop(db);

    let text = read_best(st, doc_id).await;
    if text.trim().is_empty() {
        return Ok(Vec::new());
    }
    let mut corpus: Vec<String> = Vec::new();
    for id in corpus_ids {
        let t = read_best(st, &id).await;
        if !t.trim().is_empty() {
            corpus.push(t);
        }
    }
    let refs: Vec<&str> = corpus.iter().map(|s| s.as_str()).collect();
    Ok(keywords_tfidf(&text, &refs, top_n))
}

fn as_api(e: anyhow::Error) -> ApiError {
    ApiError::from(e)
}

/// Parsea los wikilinks `[[...]]` de un contenido, en orden de aparición.
pub fn extract_wikilinks(content: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = content;
    while let Some(idx) = rest.find("[[") {
        let after = &rest[idx + 2..];
        if let Some(close) = after.find("]]") {
            out.push(after[..close].to_string());
            rest = &after[close + 2..];
        } else {
            break;
        }
    }
    out
}

/// Localiza la primera mención que coincide con `alias` (normalizado, tolerante a
/// acentos/plural) y devuelve su rango en offsets de byte. Paridad con `_match_literal`.
pub fn match_literal_offset(content: &str, alias: &str) -> Option<(usize, usize)> {
    use tg_linker::normalize::{normalize, normalize_word, tokenize_with_positions};
    let variant = normalize(alias);
    let parts: Vec<&str> = variant.split(' ').filter(|s| !s.is_empty()).collect();
    if parts.is_empty() {
        return None;
    }
    let tokens = tokenize_with_positions(content);
    let keys: Vec<String> = tokens.iter().map(|t| normalize_word(t.lit)).collect();
    if parts.len() > keys.len() {
        return None;
    }
    for win in 0..=keys.len() - parts.len() {
        let mut ok = true;
        for (k, p) in parts.iter().enumerate() {
            if keys[win + k] != *p {
                ok = false;
                break;
            }
        }
        if ok {
            return Some((tokens[win].start, tokens[win + parts.len() - 1].end));
        }
    }
    None
}
