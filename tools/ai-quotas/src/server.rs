//! Local HTTP server: embedded dashboard UI + JSON API over the state dir.
//!
//! Binds 127.0.0.1 only. File records are read fresh on every request; pull
//! providers go through the existing `CachedSource` wrappers (45s TTL), so
//! concurrent requests share caches without hammering upstream APIs. Each
//! request is handled on its own thread and wrapped in `catch_unwind`, so a
//! panic in one request answers 500 instead of killing the process.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};

use crate::reader::{self, Status};

const INDEX_HTML: &str = include_str!("../static/index.html");
pub const DEFAULT_PORT: u16 = 47_623;

/// Shared, thread-safe provider of live pull records (e.g. `pull_all` or `PullManager`).
/// The bool parameter specifies whether a force refresh (bypassing TTL cache) was requested.
pub type PullFn = Arc<dyn Fn(bool) -> Vec<Status> + Send + Sync>;

/// Bind and serve forever. Returns an error only when binding fails.
pub fn serve(bind_addr: &str, dir: PathBuf, pull: PullFn) -> Result<(), String> {
    let server = tiny_http::Server::http(bind_addr)
        .map_err(|e| format!("failed to bind {bind_addr}: {e}"))?;
    println!("ai-quotas listening on http://{}", server.server_addr());
    println!("state dir: {}", dir.display());
    println!(
        "pull credentials: claude: {}, chatgpt: {}, opencode: {}, deepseek: {}, zai: {}",
        if crate::providers::claude::ClaudeSource::from_env().has_credentials() {
            "yes"
        } else {
            "no"
        },
        if crate::providers::chatgpt::ChatGptSource::from_env().has_credentials() {
            "yes"
        } else {
            "no"
        },
        if crate::providers::opencode::OpenCodeSource::from_env().has_credentials() {
            "yes"
        } else {
            "no"
        },
        if crate::providers::deepseek::DeepSeekSource::from_env().has_credentials() {
            "yes"
        } else {
            "no"
        },
        if crate::providers::zai::ZaiSource::from_env().has_credentials() {
            "yes"
        } else {
            "no"
        },
    );
    run_loop(server, dir, pull)
}

/// Accept loop: one thread per request, panics isolated per request.
pub fn run_loop(server: tiny_http::Server, dir: PathBuf, pull: PullFn) -> ! {
    let server = Arc::new(server);
    let dir = Arc::new(dir);
    loop {
        let request = match server.recv() {
            Ok(request) => request,
            Err(_) => continue,
        };
        let dir = Arc::clone(&dir);
        let pull = Arc::clone(&pull);
        std::thread::spawn(move || handle(request, &dir, &pull));
    }
}

/// Handle one request: route inside `catch_unwind`, always answer something.
fn handle(request: tiny_http::Request, dir: &Path, pull: &PullFn) {
    let (code, content_type, body) = {
        let dir = dir.to_path_buf();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            route(&request, &dir, pull)
        }));
        match result {
            Ok(response) => response,
            Err(_) => (500, "text/plain; charset=utf-8".to_string(), "internal error\n".to_string()),
        }
    };
    let header = tiny_http::Header::from_bytes(&b"Content-Type"[..], content_type.as_bytes())
        .expect("static Content-Type header bytes are valid");
    // Data is read fresh per request; forbid any intermediary/browser caching
    // so a long-lived tab never serves itself stale numbers.
    let no_store = tiny_http::Header::from_bytes(&b"Cache-Control"[..], &b"no-store"[..])
        .expect("static Cache-Control header bytes are valid");
    let response = tiny_http::Response::from_string(body)
        .with_status_code(code)
        .with_header(header)
        .with_header(no_store);
    let _ = request.respond(response);
}

/// Pure routing: (status, content type, body).
fn route(request: &tiny_http::Request, dir: &Path, pull: &PullFn) -> (u16, String, String) {
    let raw_url = request.url();
    let path = raw_url.split('?').next().unwrap_or("/");
    let is_refresh = raw_url.contains("refresh=1") || raw_url.contains("refresh=true") || path == "/api/refresh";
    match path {
        "/" => (200, "text/html; charset=utf-8".into(), INDEX_HTML.into()),
        "/api/health" => (200, "application/json".into(), "{\"status\":\"ok\"}".into()),
        "/api/quotas" | "/api/refresh" => (200, "application/json".into(), quotas_body(dir, pull, is_refresh)),
        _ => (404, "text/plain; charset=utf-8".into(), "not found\n".into()),
    }
}

/// Build the `/api/quotas` payload: file records read fresh, pull records
/// via the cached wrappers, one flat JSON object per status.
fn quotas_body(dir: &Path, pull: &PullFn, force_refresh: bool) -> String {
    let now = Utc::now();
    let file_statuses = reader::read_dir_status(dir);
    let pull_statuses = pull(force_refresh);
    let statuses = reader::merge_statuses(file_statuses, pull_statuses);
    let records: Vec<Value> = statuses.iter().map(|s| status_to_json(s, now)).collect();
    serde_json::to_string(&json!({ "generated_at": now.to_rfc3339(), "records": records }))
        .unwrap_or_else(|_| "{\"generated_at\":\"\",\"records\":[]}".to_string())
}

/// Flatten one status into a JSON object: all record fields (when present)
/// plus `state`, `age_seconds`, `used_percent` and an optional `detail`.
/// The display name falls back to the known-provider list so the UI always
/// has a card title.
pub fn status_to_json(status: &Status, now: DateTime<Utc>) -> Value {
    let mut obj = Map::new();
    obj.insert("provider".into(), json!(status.provider));
    obj.insert("state".into(), json!(status.state.as_str()));
    if let Some(record) = &status.record {
        // Inline record fields first; status-level keys overwrite below.
        if let Value::Object(fields) = serde_json::to_value(record).unwrap_or(Value::Null) {
            for (key, value) in fields {
                obj.insert(key, value);
            }
        }
        if let Some(percent) = record.used_percent() {
            obj.insert("used_percent".into(), json!(percent));
        }
        // Pull records carry no age_seconds; derive it from fetched_at.
        let age = status
            .age_seconds
            .unwrap_or_else(|| (now - record.fetched_at).num_seconds().max(0) as u64);
        obj.insert("age_seconds".into(), json!(age));
    }
    if let Some(detail) = &status.detail {
        obj.insert("detail".into(), json!(detail));
    }
    let display_name = status
        .display_name
        .clone()
        .or_else(|| reader::known_display_name(&status.provider).map(str::to_string));
    if let Some(display_name) = display_name {
        obj.insert("display_name".into(), json!(display_name));
    }
    Value::Object(obj)
}

/// Parse `AI_QUOTAS_PORT`: absent/empty -> default, invalid -> error.
pub fn parse_port(env_value: Option<&str>) -> Result<u16, String> {
    match env_value.map(str::trim).filter(|v| !v.is_empty()) {
        None => Ok(DEFAULT_PORT),
        Some(raw) => raw
            .parse::<u16>()
            .map_err(|e| format!("invalid AI_QUOTAS_PORT '{raw}' ({e})")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    use crate::providers::missing_record;
    use crate::reader::State;
    use crate::schema::{Kind, Record, Source};

    fn at() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-08-20T14:05:12Z")
            .unwrap()
            .with_timezone(&Utc)
    }

    fn fresh_claude_json() -> String {
        format!(
            r#"{{"provider":"claude","kind":"window","used":62,"limit":100,"unit":"requests","label":"5h window","display_name":"Claude Pro","fetched_at":"{}","source":"manual"}}"#,
            Utc::now().to_rfc3339(),
        )
    }

    #[test]
    fn status_json_inlines_record_fields_plus_api_keys() {
        let mut record = Record::new("claude".into(), Kind::Window, Source::Manual, at(), None);
        record.used = Some(62.0);
        record.limit = Some(100.0);
        record.display_name = Some("Claude Pro".into());
        let status = Status {
            provider: "claude".into(),
            state: State::Ok,
            record: Some(record),
            age_seconds: Some(5),
            detail: None,
            display_name: Some("Claude Pro".into()),
        };
        let v = status_to_json(&status, at());
        assert_eq!(v["provider"], "claude");
        assert_eq!(v["state"], "ok");
        assert_eq!(v["kind"], "window");
        assert_eq!(v["source"], "manual");
        assert_eq!(v["used"], 62.0);
        assert_eq!(v["used_percent"], 62.0);
        assert_eq!(v["age_seconds"], 5);
        assert_eq!(v["display_name"], "Claude Pro");
        assert!(v.get("detail").is_none(), "no detail key when absent");
    }

    #[test]
    fn status_json_computes_age_for_pull_records() {
        let fetched = Utc::now() - chrono::Duration::seconds(90);
        let record = Record::new("zai".into(), Kind::Window, Source::Api, fetched, None);
        let status = crate::providers::ok_record(record, None);
        let v = status_to_json(&status, Utc::now());
        assert_eq!(v["age_seconds"], 90, "age derived from fetched_at");
    }

    #[test]
    fn status_json_missing_and_error_stay_minimal() {
        let missing = missing_record("deepseek", "DeepSeek API", "no credentials");
        let v = status_to_json(&missing, at());
        assert_eq!(v["state"], "missing");
        assert_eq!(v["provider"], "deepseek");
        assert_eq!(v["detail"], "no credentials");
        assert!(v.get("kind").is_none(), "no record fields without a record");

        let error = crate::providers::error_record("zai", "Z.ai", "quota request failed".into());
        let v = status_to_json(&error, at());
        assert_eq!(v["state"], "error");
        assert_eq!(v["detail"], "quota request failed");
    }

    #[test]
    fn status_json_balance_has_no_percent() {
        let mut record = Record::new("deepseek".into(), Kind::Balance, Source::Api, at(), None);
        record.limit = Some(110.0);
        record.currency = Some("CNY".into());
        let status = crate::providers::ok_record(record, None);
        let v = status_to_json(&status, at());
        assert_eq!(v["kind"], "balance");
        assert_eq!(v["currency"], "CNY");
        assert!(v.get("used_percent").is_none());
    }

    #[test]
    fn port_parsing() {
        assert_eq!(parse_port(None), Ok(DEFAULT_PORT));
        assert_eq!(parse_port(Some("")), Ok(DEFAULT_PORT));
        assert_eq!(parse_port(Some("  ")), Ok(DEFAULT_PORT));
        assert_eq!(parse_port(Some("48912")), Ok(48_912));
        assert!(parse_port(Some("70000")).is_err());
        assert!(parse_port(Some("abc")).is_err());
    }

    #[test]
    fn server_serves_health_quotas_index_and_404() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("claude.json"), fresh_claude_json()).unwrap();

        let server = tiny_http::Server::http("127.0.0.1:0").unwrap();
        let base = format!("http://{}", server.server_addr().to_ip().unwrap());
        let pull: PullFn = Arc::new(|_force| vec![missing_record("deepseek", "DeepSeek API", "no credentials")]);
        let state_dir = dir.path().to_path_buf();
        std::thread::spawn(move || run_loop(server, state_dir, pull));

        // health
        let health: Value = ureq::get(&format!("{base}/api/health"))
            .call()
            .unwrap()
            .into_json()
            .unwrap();
        assert_eq!(health["status"], "ok");

        // quotas: file record fresh + pull record merged
        let quotas: Value = ureq::get(&format!("{base}/api/quotas"))
            .call()
            .unwrap()
            .into_json()
            .unwrap();
        assert!(
            DateTime::parse_from_rfc3339(quotas["generated_at"].as_str().unwrap()).is_ok(),
            "generated_at is RFC3339"
        );
        let records = quotas["records"].as_array().unwrap();
        let claude = records.iter().find(|r| r["provider"] == "claude").unwrap();
        assert_eq!(claude["state"], "ok");
        assert_eq!(claude["used_percent"], 62.0);
        let deepseek = records.iter().find(|r| r["provider"] == "deepseek").unwrap();
        assert_eq!(deepseek["state"], "missing");

        // embedded index
        let index = ureq::get(&format!("{base}/")).call().unwrap();
        assert_eq!(index.status(), 200);
        assert!(index.header("Content-Type").unwrap().starts_with("text/html"));
        let body = index.into_string().unwrap();
        assert!(body.contains("AI Quotas"));

        // 404 (ureq surfaces non-2xx as Error::Status)
        let missing = match ureq::get(&format!("{base}/nope")).call() {
            Err(ureq::Error::Status(code, response)) => {
                assert_eq!(code, 404);
                response
            }
            other => panic!("expected 404, got {other:?}"),
        };
        assert_eq!(missing.header("Content-Type").unwrap(), "text/plain; charset=utf-8");
    }
}
