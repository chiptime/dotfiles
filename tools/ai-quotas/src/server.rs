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
use crate::status::StatusPoller;
use crate::{balancer, history};

const INDEX_HTML: &str = include_str!("../static/index.html");
pub const DEFAULT_PORT: u16 = 47_623;

/// Shared, thread-safe provider of live pull records (e.g. `pull_all` or `PullManager`).
/// The bool parameter specifies whether a force refresh (bypassing TTL cache) was requested.
pub type PullFn = Arc<dyn Fn(bool) -> Vec<Status> + Send + Sync>;

/// Bind and serve forever. Returns an error only when binding fails.
pub fn serve(bind_addr: &str, dir: PathBuf, pull: PullFn, status: Arc<StatusPoller>) -> Result<(), String> {
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
    run_loop(server, dir, pull, status)
}

/// Accept loop: one thread per request, panics isolated per request.
pub fn run_loop(server: tiny_http::Server, dir: PathBuf, pull: PullFn, status: Arc<StatusPoller>) -> ! {
    let server = Arc::new(server);
    let dir = Arc::new(dir);
    loop {
        let request = match server.recv() {
            Ok(request) => request,
            Err(_) => continue,
        };
        let dir = Arc::clone(&dir);
        let pull = Arc::clone(&pull);
        let status = Arc::clone(&status);
        std::thread::spawn(move || handle(request, &dir, &pull, &status));
    }
}

/// Handle one request: route inside `catch_unwind`, always answer something.
fn handle(request: tiny_http::Request, dir: &Path, pull: &PullFn, status: &StatusPoller) {
    let (code, content_type, body) = {
        let dir = dir.to_path_buf();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            route(&request, &dir, pull, status)
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
fn route(request: &tiny_http::Request, dir: &Path, pull: &PullFn, status: &StatusPoller) -> (u16, String, String) {
    let raw_url = request.url();
    let path = raw_url.split('?').next().unwrap_or("/");
    let is_refresh = raw_url.contains("refresh=1") || raw_url.contains("refresh=true") || path == "/api/refresh";
    match path {
        "/" => (200, "text/html; charset=utf-8".into(), INDEX_HTML.into()),
        "/api/health" => (200, "application/json".into(), "{\"status\":\"ok\"}".into()),
        "/api/quotas" | "/api/refresh" => (200, "application/json".into(), quotas_body(dir, pull, is_refresh)),
        "/api/status" => (200, "application/json".into(), status_body(status)),
        "/api/history" => (200, "application/json".into(), history_body(dir)),
        "/api/balancer/recommend" => match query_param(raw_url, "model").filter(|m| !m.is_empty()) {
            Some(model) => (200, "application/json".into(), balancer_recommend_body(dir, pull, &model)),
            None => (400, "application/json".into(), "{\"error\":\"missing model query parameter\"}".into()),
        },
        _ => (404, "text/plain; charset=utf-8".into(), "not found\n".into()),
    }
}

/// Build the `/api/quotas` payload: file records read fresh, pull records
/// via the cached wrappers, one flat JSON object per status. Also appends a
/// throttled (max one per hour) history snapshot under the state dir so the
/// UI can show weekly closes, ritmo trends and learned window durations.
fn quotas_body(dir: &Path, pull: &PullFn, force_refresh: bool) -> String {
    let now = Utc::now();
    let file_statuses = reader::read_dir_status(dir);
    let pull_statuses = pull(force_refresh);
    let statuses = reader::merge_statuses(file_statuses, pull_statuses);
    append_history_snapshot(dir, &statuses, now);
    let records: Vec<Value> = statuses.iter().map(|s| status_to_json(s, now)).collect();
    serde_json::to_string(&json!({ "generated_at": now.to_rfc3339(), "records": records }))
        .unwrap_or_else(|_| "{\"generated_at\":\"\",\"records\":[]}".to_string())
}

/// `/api/status` payload: latest incident snapshot per mapped provider.
/// Served from the TTL-cached `StatusPoller` (stale-on-failure preserved),
/// so the response stays well-formed even without network — failures come
/// back as null-indicator entries, never a 500.
fn status_body(status: &StatusPoller) -> String {
    let providers: Map<String, Value> = status
        .fetch()
        .iter()
        .map(|(provider, snapshot)| (provider.clone(), snapshot.to_json()))
        .collect();
    serde_json::to_string(&json!({ "providers": providers }))
        .unwrap_or_else(|_| "{\"providers\":{}}".to_string())
}

/// Last history write instant (process-wide throttle: 1 snapshot per hour).
static LAST_HISTORY_WRITE: std::sync::OnceLock<std::sync::Mutex<Option<chrono::DateTime<Utc>>>> =
    std::sync::OnceLock::new();

/// Append one JSONL line per usable record to `<dir>/history.jsonl`, at most
/// once per hour. Best effort: history is an enhancement, never a failure.
fn append_history_snapshot(dir: &Path, statuses: &[Status], now: DateTime<Utc>) {
    let cell = LAST_HISTORY_WRITE.get_or_init(|| std::sync::Mutex::new(None));
    if let Ok(mut guard) = cell.lock() {
        let due = guard
            .map(|last| (now - last).num_hours() >= 1)
            .unwrap_or(true);
        if !due {
            return;
        }
        *guard = Some(now);
    }
    let mut lines = String::new();
    for s in statuses {
        if s.record.is_none() {
            continue;
        }
        lines.push_str(&history_entry(s, now).to_string());
        lines.push('\n');
    }
    if !lines.is_empty() {
        use std::io::Write;
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("history.jsonl"))
        {
            let _ = file.write_all(lines.as_bytes());
        }
    }
}

/// Pure: one history JSONL entry for a usable status (no throttling, no I/O).
/// Earned reset credits ride along only when present (skip when None).
fn history_entry(status: &Status, now: DateTime<Utc>) -> Value {
    let record = status.record.as_ref().expect("history entries need a record");
    let mut entry = json!({
        "ts": now.to_rfc3339(),
        "provider": status.provider,
        "label": record.label,
        "state": status.state.as_str(),
        "used_percent": record.used_percent(),
        "resets_at": record.resets_at.map(|t| t.to_rfc3339()),
    });
    if let Some(credits) = &record.reset_credits {
        if let Ok(value) = serde_json::to_value(credits) {
            entry["reset_credits"] = value;
        }
    }
    entry
}

/// `/api/history` payload: parsed JSONL snapshot lines (bounded tail read).
fn history_body(dir: &Path) -> String {
    let bytes = match std::fs::read(dir.join("history.jsonl")) {
        Ok(bytes) => bytes,
        Err(_) => return "{\"history\":[]}".to_string(),
    };
    // Bound to the last 256 KiB; older data is irrelevant to the UI.
    let start = bytes.len().saturating_sub(256 * 1024);
    let text = String::from_utf8_lossy(&bytes[start..]);
    let history: Vec<Value> = text
        .lines()
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();
    serde_json::to_string(&json!({ "history": history }))
        .unwrap_or_else(|_| "{\"history\":[]}".to_string())
}

/// Build the `/api/balancer/recommend` body: the merged quota truth through
/// the pure policy (R1), then one best-effort JSONL history line under the
/// same state dir (R8 — this server is the sole history writer).
fn balancer_recommend_body(dir: &Path, pull: &PullFn, model: &str) -> String {
    let now = Utc::now();
    let statuses = reader::merge_statuses(reader::read_dir_status(dir), pull(false));
    let advice = balancer::advise(balancer::load_config().as_ref(), model, &statuses);
    history::append(dir, now, &advice);
    serde_json::to_string(&advice).unwrap_or_else(|_| "{}".to_string())
}

/// Extract one percent-decoded query parameter from a raw request URL.
fn query_param(raw_url: &str, key: &str) -> Option<String> {
    let query = raw_url.split_once('?')?.1;
    for pair in query.split('&') {
        let Some((k, v)) = pair.split_once('=') else { continue };
        if k == key {
            return Some(percent_decode(v));
        }
    }
    None
}

/// Decode `%XX` escapes and `+` in one query component, lossily.
fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[i + 1..i + 3])
                    .ok()
                    .and_then(|h| u8::from_str_radix(h, 16).ok());
                match hex {
                    Some(byte) => {
                        out.push(byte);
                        i += 3;
                    }
                    None => {
                        out.push(bytes[i]);
                        i += 1;
                    }
                }
            }
            byte => {
                out.push(byte);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
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

    /// Network-free poller for route tests: empty mapping -> `{"providers":{}}`.
    fn empty_status_poller() -> Arc<StatusPoller> {
        Arc::new(StatusPoller::with_fetcher(Box::new(|_| vec![])))
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
    fn status_json_emits_reset_credits_only_when_present() {
        use crate::schema::ResetCredits;

        let mut with = Record::new("chatgpt".into(), Kind::Window, Source::Api, at(), None);
        with.reset_credits = Some(ResetCredits {
            available: 3,
            applicable: Some(0),
            expires_at: Some(at()),
            credits: Vec::new(),
        });
        let v = status_to_json(&crate::providers::ok_record(with, None), at());
        assert_eq!(v["reset_credits"]["available"], 3);
        assert_eq!(v["reset_credits"]["applicable"], 0);
        assert_eq!(v["reset_credits"]["expires_at"], "2026-08-20T14:05:12Z");

        // Absent field: the key is skipped entirely, old clients unaffected.
        let without = Record::new("claude".into(), Kind::Window, Source::Api, at(), None);
        let v = status_to_json(&crate::providers::ok_record(without, None), at());
        assert!(v.get("reset_credits").is_none());
    }

    #[test]
    fn history_entry_carries_reset_credits_when_present() {
        use crate::schema::ResetCredits;

        let mut record = Record::new("chatgpt".into(), Kind::Window, Source::Api, at(), None);
        record.label = Some("5h window".into());
        record.reset_credits = Some(ResetCredits {
            available: 1,
            applicable: None,
            expires_at: None,
            credits: Vec::new(),
        });
        let status = crate::providers::ok_record(record, None);
        let entry = history_entry(&status, at());
        assert_eq!(entry["reset_credits"]["available"], 1);
        assert!(entry["reset_credits"].get("applicable").is_none());
        assert!(entry["reset_credits"].get("expires_at").is_none());

        // The whole ResetCredits (per-credit rows included) serializes into
        // the history line, so UI history stays lossless.
        let mut record = Record::new("chatgpt".into(), Kind::Window, Source::Api, at(), None);
        record.label = Some("5h window".into());
        record.reset_credits = Some(ResetCredits::with_credits(
            2,
            None,
            vec![crate::schema::ResetCredit {
                expires_at: Some(at()),
                title: Some("Full reset (Weekly + 5 hr)".into()),
            }],
        ));
        let entry = history_entry(&crate::providers::ok_record(record, None), at());
        assert_eq!(entry["reset_credits"]["available"], 2);
        assert_eq!(entry["reset_credits"]["expires_at"], "2026-08-20T14:05:12Z");
        assert_eq!(entry["reset_credits"]["credits"].as_array().unwrap().len(), 1);
        assert_eq!(
            entry["reset_credits"]["credits"][0]["title"],
            "Full reset (Weekly + 5 hr)"
        );

        // No credits -> no key at all.
        let record = Record::new("claude".into(), Kind::Window, Source::Api, at(), None);
        let entry = history_entry(&crate::providers::ok_record(record, None), at());
        assert!(entry.get("reset_credits").is_none());
        assert!(entry.get("resets_at").is_some(), "baseline shape kept");
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
        std::thread::spawn(move || run_loop(server, state_dir, pull, empty_status_poller()));

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

    #[test]
    fn balancer_recommend_route_serves_advice_shape_and_history() {
        let dir = tempdir().unwrap();
        let server = tiny_http::Server::http("127.0.0.1:0").unwrap();
        let base = format!("http://{}", server.server_addr().to_ip().unwrap());
        let pull: PullFn = Arc::new(|_force| vec![]);
        let state_dir = dir.path().to_path_buf();
        std::thread::spawn(move || run_loop(server, state_dir, pull, empty_status_poller()));

        // Unknown model or missing config: either way advice fails open (R1/R9).
        let advice: Value = ureq::get(&format!("{base}/api/balancer/recommend?model=x"))
            .call()
            .unwrap()
            .into_json()
            .unwrap();
        assert_eq!(advice["schema"], "ai-quotas/balancer-advice@1");
        assert_eq!(advice["switch"], false);
        assert_eq!(advice["requested_model"], "x");
        assert_eq!(advice["recommended_model"], "x");
        assert!(advice["advice_age_seconds"].is_u64());

        // Percent-encoded slash decodes back into the full model id.
        let encoded: Value =
            ureq::get(&format!("{base}/api/balancer/recommend?model=openai%2Fgpt-5.2"))
                .call()
                .unwrap()
                .into_json()
                .unwrap();
        assert_eq!(encoded["requested_model"], "openai/gpt-5.2");
        assert_eq!(encoded["recommended_model"], "openai/gpt-5.2");

        // Missing model parameter is a client error, not advice.
        match ureq::get(&format!("{base}/api/balancer/recommend")).call() {
            Err(ureq::Error::Status(code, _)) => assert_eq!(code, 400),
            other => panic!("expected 400, got {other:?}"),
        }

        // Every advice lands as one JSONL line under the state dir (R8).
        let history_dir = dir.path().join("history");
        let mut files: Vec<_> = fs::read_dir(&history_dir)
            .unwrap()
            .map(|e| e.unwrap().path())
            .collect();
        assert_eq!(files.len(), 1, "one daily history file");
        let body = fs::read_to_string(files.remove(0)).unwrap();
        assert_eq!(body.lines().count(), 2, "one line per advice served");
        assert!(body.contains("ai-quotas/balancer-advice@1"));
    }

    #[test]
    fn balancer_recommend_missing_config_and_empty_state_fail_open() {
        let dir = tempdir().unwrap();
        let pull: PullFn = Arc::new(|_force| vec![]);

        // Missing config: switch:false, no error (R9).
        std::env::set_var("AI_QUOTAS_BALANCER_CONFIG", dir.path().join("missing.json"));
        let v: Value =
            serde_json::from_str(&balancer_recommend_body(dir.path(), &pull, "openai/gpt-5.2"))
                .unwrap();
        assert_eq!(v["reason"], "config_missing");
        assert_eq!(v["switch"], false);
        assert_eq!(v["recommended_model"], "openai/gpt-5.2");
        assert!(v.get("requested_remaining").is_none());

        // Config present but no telemetry for the requested provider.
        std::fs::write(
            dir.path().join("balancer.json"),
            include_str!("../config/balancer.example.json"),
        )
        .unwrap();
        std::env::set_var("AI_QUOTAS_BALANCER_CONFIG", dir.path().join("balancer.json"));
        let v: Value =
            serde_json::from_str(&balancer_recommend_body(dir.path(), &pull, "openai/gpt-5.2"))
                .unwrap();
        assert_eq!(v["reason"], "insufficient_data");
        assert_eq!(v["switch"], false);
        assert!(v.get("requested_remaining").is_none());
        assert!(v.get("recommended_remaining").is_none());
        std::env::remove_var("AI_QUOTAS_BALANCER_CONFIG");
    }

    #[test]
    fn status_route_serves_snapshot_shape_with_no_store() {
        let dir = tempdir().unwrap();
        let status = Arc::new(StatusPoller::with_fetcher(Box::new(|_| {
            vec![(
                "claude",
                Ok(crate::status::ProviderStatus {
                    indicator: Some(crate::status::Severity::Major),
                    summary: "Partial system outage".to_string(),
                    updated_at: Utc::now(),
                    stale: false,
                    error: None,
                    detail: None,
                }),
            )]
        })));
        let server = tiny_http::Server::http("127.0.0.1:0").unwrap();
        let base = format!("http://{}", server.server_addr().to_ip().unwrap());
        let pull: PullFn = Arc::new(|_force| vec![]);
        let state_dir = dir.path().to_path_buf();
        std::thread::spawn(move || run_loop(server, state_dir, pull, status));

        let response = ureq::get(&format!("{base}/api/status")).call().unwrap();
        assert_eq!(response.status(), 200);
        assert_eq!(response.header("Cache-Control"), Some("no-store"));
        let v: Value = response.into_json().unwrap();
        let claude = &v["providers"]["claude"];
        assert_eq!(claude["indicator"], "major");
        assert_eq!(claude["summary"], "Partial system outage");
        assert_eq!(claude["stale"], false);
        assert!(claude.get("error").is_none(), "no error key on success");
        assert!(
            DateTime::parse_from_rfc3339(claude["updated_at"].as_str().unwrap()).is_ok(),
            "updated_at is RFC3339"
        );
    }

    #[test]
    fn status_route_stays_wellformed_when_every_fetch_fails() {
        let dir = tempdir().unwrap();
        let status = Arc::new(StatusPoller::with_fetcher(Box::new(|_| {
            vec![
                ("claude", Err("status request failed: dns".to_string())),
                ("chatgpt", Err("status request failed: timeout".to_string())),
            ]
        })));
        let server = tiny_http::Server::http("127.0.0.1:0").unwrap();
        let base = format!("http://{}", server.server_addr().to_ip().unwrap());
        let pull: PullFn = Arc::new(|_force| vec![]);
        let state_dir = dir.path().to_path_buf();
        std::thread::spawn(move || run_loop(server, state_dir, pull, status));

        // No network must never become a 500: a well-formed envelope with
        // null indicators and the failure text as summary.
        let response = ureq::get(&format!("{base}/api/status")).call().unwrap();
        assert_eq!(response.status(), 200);
        let v: Value = response.into_json().unwrap();
        for key in ["claude", "chatgpt"] {
            let entry = &v["providers"][key];
            assert!(entry["indicator"].is_null(), "never-polled indicator is null");
            assert!(
                entry["summary"].as_str().unwrap().contains("status unavailable"),
                "failure described in summary"
            );
            assert!(
                entry["error"]
                    .as_str()
                    .unwrap()
                    .starts_with("status request failed")
            );
        }
    }
}
