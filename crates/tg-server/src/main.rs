use tg_server::{config, db, state};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let settings = config::Settings::from_env()?;
    println!(
        "tg-server | data_dir={} | public_url={}",
        settings.data_dir.display(),
        settings.public_url
    );

    let db = db::open(&settings.db_path)?;
    let state = state::AppState::new(settings.clone(), db);
    let app = tg_server::app(state);

    let addr = format!("{}:{}", settings.host, settings.port);
    println!("listening on http://{addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
