//! tg-server: servidor HTTP (axum) con CRDT embebido (arquitectura B).
//!
//! Bin (`main.rs`) y tests de integración comparten esta lib.

pub mod auth;
pub mod config;
pub mod crdt;
pub mod db;
pub mod error;
pub mod linker;
pub mod routes;
pub mod state;

pub use state::AppState;

use axum::Router;
use tower_http::cors::{Any, CorsLayer};

/// Monta el router completo (rutas de usuario + linker + superficie y-sweet) con CORS
/// permisivo. Compartido por `main.rs` y los tests de integración.
pub fn app(state: AppState) -> Router {
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    Router::new()
        .merge(routes::router())
        .merge(routes::linker::router())
        .merge(routes::ysweet::router())
        .layer(cors)
        .with_state(state)
}
