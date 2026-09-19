//! tg-commons: modelos y utilidades compartidas del worksapce.

pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
