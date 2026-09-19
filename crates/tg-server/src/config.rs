use std::path::PathBuf;

use anyhow::Result;

/// Configuración por env vars (igual esquema que el `config.py`; sin `YSWEET_*`).
#[derive(Clone)]
pub struct Settings {
    pub admin_token: String,
    pub data_dir: PathBuf,
    pub db_path: PathBuf,
    pub attachments_dir: PathBuf,
    pub mirror_dir: PathBuf,
    pub crdt_dir: PathBuf,
    /// Base pública para la superficie y-sweet (el plugin conecta ahí). ws://host:port
    pub public_url: String,
    pub host: String,
    pub port: u16,
    #[allow(dead_code)]
    pub mirror_interval_secs: u64,
}

impl Settings {
    pub fn from_env() -> Result<Self> {
        let data_dir = PathBuf::from(std::env::var("DATA_DIR").unwrap_or_else(|_| "./data".into()));
        let admin_token = std::env::var("ADMIN_TOKEN").unwrap_or_else(|_| "dev-admin".to_string());
        let host = std::env::var("TG_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
        let port = std::env::var("TG_PORT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(8791);
        let public_url =
            std::env::var("TG_PUBLIC_URL").unwrap_or_else(|_| format!("ws://localhost:{port}"));
        let mirror_interval_secs = std::env::var("MIRROR_INTERVAL")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(30);

        let attachments_dir = data_dir.join("attachments");
        let mirror_dir = data_dir.join("mirror");
        let crdt_dir = data_dir.join("crdt");
        let db_path = data_dir.join("goblin.db");

        for d in [&data_dir, &attachments_dir, &mirror_dir, &crdt_dir] {
            std::fs::create_dir_all(d)?;
        }

        Ok(Settings {
            admin_token,
            data_dir,
            db_path,
            attachments_dir,
            mirror_dir,
            crdt_dir,
            public_url,
            host,
            port,
            mirror_interval_secs,
        })
    }

    /// base HTTP (para `baseUrl`/`url` de la superficie y-sweet).
    pub fn http_base(&self) -> String {
        if let Some(rest) = self.public_url.strip_prefix("wss://") {
            format!("https://{rest}")
        } else {
            self.public_url.replace("ws://", "http://")
        }
    }
}
