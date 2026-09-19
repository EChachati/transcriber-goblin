use std::sync::Arc;

use rusqlite::Connection;
use tokio::sync::Mutex;

use crate::config::Settings;
use crate::crdt::CrdtStore;

/// Estado global de axum (clonable).
#[derive(Clone)]
pub struct AppState {
    pub settings: Settings,
    // rusqlite Connection no es Sync: serializamos acceso con un Mutex (decisión de
    // arquitectura: rusqlite + Mutex). Un lock por request; es el cuello de botella
    // aceptado para este volumen.
    pub db: Arc<Mutex<Connection>>,
    pub crdt: CrdtStore,
}

impl AppState {
    pub fn new(settings: Settings, db: Connection) -> Self {
        let crdt = CrdtStore::new(settings.crdt_dir.clone(), settings.mirror_dir.clone());
        Self {
            settings,
            db: Arc::new(Mutex::new(db)),
            crdt,
        }
    }
}
