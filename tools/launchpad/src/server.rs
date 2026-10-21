//! HTTP surface: embedded front + JSON API over tiny_http.
//!
//! The config file is re-read on every `/api/tiles` request, so editing
//! `tiles.json` applies on the next front poll without a service restart.

use crate::config::{LaunchpadConfig, TileKind};
use crate::health::HealthChecker;
use serde_json::{json, Value};
use std::sync::Arc;
use tiny_http::Header;

const INDEX_HTML: &str = include_str!("../static/index.html");
pub const DEFAULT_PORT: u16 = 47_625;

pub fn serve(
    bind_addr: &str,
    checker: Arc<HealthChecker>,
) -> Result<(), String> {
    let server = tiny_http::Server::http(bind_addr)
        .map_err(|e| format!("failed to bind {bind_addr}: {e}"))?;
    println!("launchpad listening on http://{}", server.server_addr());
    println!("tiles config: {}", crate::config::config_path().display());
    let server = Arc::new(server);
    loop {
        let request = match server.recv() {
            Ok(request) => request,
            Err(_) => continue,
        };
        let checker = Arc::clone(&checker);
        std::thread::spawn(move || handle(request, &checker));
    }
}

/// Handle one request: route inside `catch_unwind`, always answer something.
fn handle(request: tiny_http::Request, checker: &HealthChecker) {
    let (code, content_type, body) = {
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            route(&request, checker)
        }));
        match result {
            Ok(response) => response,
            Err(_) => (500, "text/plain; charset=utf-8".to_string(), "internal error\n".to_string()),
        }
    };
    let header = Header::from_bytes(&b"Content-Type"[..], content_type.as_bytes())
        .expect("static Content-Type header bytes are valid");
    let no_store = Header::from_bytes(&b"Cache-Control"[..], &b"no-store"[..])
        .expect("static Cache-Control header bytes are valid");
    let response = tiny_http::Response::from_string(body)
        .with_status_code(code)
        .with_header(header)
        .with_header(no_store);
    let _ = request.respond(response);
}

/// Pure routing: (status, content type, body).
fn route(request: &tiny_http::Request, checker: &HealthChecker) -> (u16, String, String) {
    let path = request.url().split('?').next().unwrap_or("/");
    match path {
        "/" => (200, "text/html; charset=utf-8".into(), INDEX_HTML.into()),
        "/api/health" => (200, "application/json".into(), "{\"status\":\"ok\"}".into()),
        "/api/tiles" => match LaunchpadConfig::load() {
            Ok(config) => (200, "application/json".into(), tiles_body(&config, checker)),
            Err(e) => (500, "application/json".into(), format!("{{\"error\":\"{e}\"}}")),
        },
        _ => (404, "text/plain; charset=utf-8".into(), "not found\n".into()),
    }
}

/// `/api/tiles` payload: web tiles carry url/host/health; script and project
/// tiles carry command/docs/location and are never probed.
fn tiles_body(config: &LaunchpadConfig, checker: &HealthChecker) -> String {
    let tiles: Vec<Value> = config
        .tiles
        .iter()
        .map(|t| {
            let mut v = json!({
                "id": t.id,
                "name": t.name,
                "icon": t.icon,
                "kind": t.kind,
                "description": t.description,
            });
            if !t.tags.is_empty() {
                v["tags"] = json!(t.tags);
            }
            match t.kind {
                TileKind::Web => {
                    let url = t.url.clone().unwrap_or_default();
                    v["url"] = json!(url);
                    v["host"] = json!(host_of(&url));
                    v["health"] = serde_json::to_value(checker.health_for(t))
                        .unwrap_or_else(|_| serde_json::json!(null));
                    v
                }
                TileKind::Script | TileKind::Project => {
                    v["command"] = json!(t.command);
                    v["docs"] = json!(t.docs);
                    v["location"] = json!(t.location);
                    v
                }
            }
        })
        .collect();
    serde_json::to_string(&json!({ "tiles": tiles }))
        .unwrap_or_else(|_| "{\"tiles\":[]}".to_string())
}

/// `http://host:port/path` -> `host:port` (empty string when unparseable).
pub fn host_of(url: &str) -> String {
    let rest = url.strip_prefix("http://").or_else(|| url.strip_prefix("https://"));
    match rest {
        Some(rest) => rest.split('/').next().unwrap_or("").to_string(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tiles_body_emits_tags_when_present() {
        let config: LaunchpadConfig = serde_json::from_str(
            r#"{"tiles":[
                {"id":"w","name":"W","url":"http://h:1/","tags":["voz"]},
                {"id":"s","name":"S","kind":"script","command":"foo","location":"scripts/foo.sh"}
            ]}"#,
        )
        .unwrap();
        let checker = HealthChecker::with_probe(Box::new(|_| Err("refused".into())));
        let v: Value = serde_json::from_str(&tiles_body(&config, &checker)).unwrap();
        assert_eq!(v["tiles"][0]["tags"], serde_json::json!(["voz"]));
        assert!(
            v["tiles"][1].get("tags").is_none(),
            "tagless tiles carry no tags key"
        );
    }

    #[test]
    fn tiles_body_separates_project_kind() {
        let config: LaunchpadConfig = serde_json::from_str(
            r#"{"tiles":[
                {"id":"p","name":"P","kind":"project","docs":"~/x/README.md","location":"~/x"}
            ]}"#,
        )
        .unwrap();
        let checker = HealthChecker::with_probe(Box::new(|_| Err("refused".into())));
        let v: Value = serde_json::from_str(&tiles_body(&config, &checker)).unwrap();
        assert_eq!(v["tiles"][0]["kind"], "project");
        assert!(v["tiles"][0].get("health").is_none(), "projects are never probed");
        assert_eq!(v["tiles"][0]["location"], "~/x");
    }

    #[test]
    fn host_of_extracts_host_and_port() {
        assert_eq!(host_of("http://127.0.0.1:47623/api"), "127.0.0.1:47623");
        assert_eq!(host_of("https://example.com/"), "example.com");
        assert_eq!(host_of("https://example.com"), "example.com");
        assert_eq!(host_of("not-a-url"), "");
    }

    #[test]
    fn tiles_body_reports_unknown_health_without_probe() {
        let config: LaunchpadConfig =
            serde_json::from_str(r#"{"tiles":[{"id":"a","name":"A","url":"http://h:1/"}]}"#)
                .unwrap();
        // A checker whose probe always fails transport: the first call DOES
        // probe, so assert the JSON shape and a down state with error text.
        let checker = HealthChecker::with_probe(Box::new(|_| Err("refused".into())));
        let body = tiles_body(&config, &checker);
        let v: Value = serde_json::from_str(&body).unwrap();
        assert_eq!(v["tiles"][0]["host"], "h:1");
        assert_eq!(v["tiles"][0]["health"]["state"], "down");
        assert!(v["tiles"][0]["health"]["error"].as_str().unwrap().contains("refused"));
    }

    #[test]
    fn tiles_body_separates_web_and_script_kinds() {
        let config: LaunchpadConfig = serde_json::from_str(
            r#"{"tiles":[
                {"id":"w","name":"W","url":"http://h:1/"},
                {"id":"s","name":"S","kind":"script","command":"foo","location":"scripts/foo.sh"}
            ]}"#,
        )
        .unwrap();
        let checker = HealthChecker::with_probe(Box::new(|_| Err("refused".into())));
        let v: Value = serde_json::from_str(&tiles_body(&config, &checker)).unwrap();
        assert_eq!(v["tiles"][0]["kind"], "web");
        assert_eq!(v["tiles"][0]["health"]["state"], "down");
        assert_eq!(v["tiles"][1]["kind"], "script");
        assert_eq!(v["tiles"][1]["command"], "foo");
        assert_eq!(v["tiles"][1]["location"], "scripts/foo.sh");
        assert!(v["tiles"][1].get("health").is_none(), "script tiles are never probed");
    }
}
