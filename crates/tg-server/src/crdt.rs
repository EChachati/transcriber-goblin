//! CRDT embebido (arquitectura B): un `yrs::Doc` por nota en memoria, persistido
//! como snapshot binario por doc, con broadcasting vía `yrs_axum::BroadcastGroup`
//! (reemplaza a y-sweet y al polling del mirror).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use tg_linker::LinkEdit;
use tokio::sync::RwLock;
use yrs::sync::Awareness;
use yrs::updates::decoder::Decode;
use yrs::{Doc, GetString, ReadTxn, StateVector, Text, Transact, Update};
use yrs_axum::broadcast::BroadcastGroup;
use yrs_axum::AwarenessRef;

/// Nota viva en memoria. Un `Doc` por doc; `BroadcastGroup` reenvía updates/awareness
/// por WS y alimenta el mirror write-through en `persist()`.
pub struct CrdtDoc {
    awareness: AwarenessRef,
    pub bcast: Arc<BroadcastGroup>,
    bin_path: PathBuf,
    mirror_path: PathBuf,
}

#[derive(Clone)]
pub struct CrdtStore {
    docs: Arc<RwLock<HashMap<String, Arc<CrdtDoc>>>>,
    crdt_dir: PathBuf,
    mirror_dir: PathBuf,
}

impl CrdtStore {
    pub fn new<S: Into<PathBuf>>(crdt_dir: S, mirror_dir: S) -> Self {
        Self {
            docs: Arc::new(RwLock::new(HashMap::new())),
            crdt_dir: crdt_dir.into(),
            mirror_dir: mirror_dir.into(),
        }
    }

    pub async fn get(&self, doc_id: &str) -> Option<Arc<CrdtDoc>> {
        self.docs.read().await.get(doc_id).cloned()
    }

    /// Devuelve (o crea) el doc. Carga el snapshot `.bin` si existe.
    pub async fn open_or_create(&self, doc_id: &str) -> Result<Arc<CrdtDoc>> {
        if let Some(d) = self.get(doc_id).await {
            return Ok(d);
        }
        let mut docs = self.docs.write().await;
        if let Some(d) = docs.get(doc_id) {
            return Ok(d.clone());
        }
        let bin_path = self.crdt_dir.join(format!("{doc_id}.bin"));
        let mirror_path = self.mirror_dir.join(format!("{doc_id}.md"));

        let doc = Doc::new();
        if let Ok(bytes) = std::fs::read(&bin_path) {
            if !bytes.is_empty() {
                let update = Update::decode_v1(&bytes)?;
                let mut txn = doc.transact_mut();
                txn.apply_update(update);
                txn.commit();
            }
        }
        // garantiza la raíz "content" (mejor: siempre existe tras el primer acceso)
        let _ = doc.get_or_insert_text("content");
        let awareness: AwarenessRef = Arc::new(RwLock::new(Awareness::new(doc)));
        let bcast = Arc::new(BroadcastGroup::new(awareness.clone(), 128).await);

        let crdt_doc = Arc::new(CrdtDoc {
            awareness,
            bcast,
            bin_path,
            mirror_path,
        });
        docs.insert(doc_id.to_string(), crdt_doc.clone());
        drop(docs);
        crdt_doc.persist().await?;
        Ok(crdt_doc)
    }
}

impl CrdtDoc {
    /// Contenido markdown actual del doc (texto crudo del CRDT).
    pub async fn read_text(&self) -> String {
        let aw = self.awareness.read().await;
        let text = aw.doc().get_or_insert_text("content");
        let txn = aw.doc().transact();
        text.get_string(&txn)
    }

    /// Snapshot completo (diff desde SV vacío) - para `/d/{id}/as-update`.
    pub async fn state_update(&self) -> Vec<u8> {
        let aw = self.awareness.read().await;
        let txn = aw.doc().transact();
        txn.encode_state_as_update_v1(&StateVector::default())
    }

    /// Aplica bytes y-sync v1 externos (POST /d/{id}/update o transcriber).
    pub async fn apply_update_bytes(&self, bytes: &[u8]) -> Result<()> {
        // `Update` no es Send: se decodifica ya dentro del lock (no cruza awaits).
        {
            let aw = self.awareness.write().await;
            let update = Update::decode_v1(bytes)?;
            let _text = aw.doc().get_or_insert_text("content");
            let mut txn = aw.doc().transact_mut();
            txn.apply_update(update);
            txn.commit();
            drop(txn);
        }
        self.persist().await
    }

    /// Aplica ediciones del linker (offsets de byte del `String` leído) en estricto
    /// orden descendente para no desplazar posiciones. Un solo txn.
    pub async fn apply_edits(&self, mut edits: Vec<LinkEdit>) -> Result<()> {
        edits.sort_by_key(|e| std::cmp::Reverse(e.start));
        {
            let aw = self.awareness.write().await;
            let text = aw.doc().get_or_insert_text("content");
            let mut txn = aw.doc().transact_mut();
            for e in edits {
                let current_len = text.len(&txn) as usize;
                debug_assert!(e.end <= current_len, "span fuera de rango");
                if e.end > current_len {
                    continue;
                }
                text.remove_range(&mut txn, e.start as u32, (e.end - e.start) as u32);
                text.insert(&mut txn, e.start as u32, &e.replacement);
            }
            txn.commit();
            drop(txn);
        }
        self.persist().await
    }

    /// Reemplaza el texto entero (aplicar propuesta o publish del transcriber).
    #[allow(dead_code)]
    pub async fn apply_full_text(&self, new_content: &str) -> Result<()> {
        {
            let aw = self.awareness.write().await;
            let text = aw.doc().get_or_insert_text("content");
            let mut txn = aw.doc().transact_mut();
            let len = text.len(&txn);
            text.remove_range(&mut txn, 0, len);
            if !new_content.is_empty() {
                text.insert(&mut txn, 0, new_content);
            }
            txn.commit();
            drop(txn);
        }
        self.persist().await
    }

    /// Escribe el espejo `.md` y el snapshot `.bin` de forma atómica (tmp+rename).
    pub async fn persist(&self) -> Result<()> {
        let (content, state) = {
            let aw = self.awareness.read().await;
            let text = aw.doc().get_or_insert_text("content");
            let txn = aw.doc().transact();
            (
                text.get_string(&txn),
                txn.encode_state_as_update_v1(&StateVector::default()),
            )
        };
        atomic_write(&self.mirror_path, content.as_bytes())?;
        atomic_write(&self.bin_path, &state)?;
        Ok(())
    }
}

fn atomic_write(path: &PathBuf, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}
