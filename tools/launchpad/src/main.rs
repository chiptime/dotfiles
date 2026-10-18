//! launchpad — a personal launcher grid for local apps and services.
//!
//! Tiles come from `~/.config/launchpad/tiles.json` (repo master in the
//! dotfiles; `LAUNCHPAD_TILES` overrides). The server probes each tile's
//! health with a per-tile TTL cache; the front polls `/api/tiles`.

mod config;
mod health;
mod server;

use std::sync::Arc;

fn main() {
    let checker = Arc::new(health::HealthChecker::new());
    let port = std::env::var("LAUNCHPAD_PORT")
        .ok()
        .and_then(|p| p.trim().parse::<u16>().ok())
        .unwrap_or(server::DEFAULT_PORT);
    // Bind host defaults to loopback; set LAUNCHPAD_HOST=0.0.0.0 to expose
    // the launchpad to the LAN (pair with a firewall rule).
    let host = std::env::var("LAUNCHPAD_HOST")
        .ok()
        .map(|h| h.trim().to_string())
        .filter(|h| !h.is_empty())
        .unwrap_or_else(|| "127.0.0.1".to_string());
    let bind = format!("{host}:{port}");
    if let Err(e) = server::serve(&bind, Arc::clone(&checker)) {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}
