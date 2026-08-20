//! OpenCode Go pull source: usage windows from the opencode.ai zen API.

use std::fs;
use std::path::PathBuf;

use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::Value;

use super::{
    error_record, http_agent, missing_record, non_empty_env, ok_record, ProviderRecord,
    QuotaSource,
};
use crate::schema::{Kind, Record, Source};

pub const PROVIDER_ID: &str = "opencode";
pub const DISPLAY_NAME: &str = "OpenCode Go";
const USAGE_URL: &str = "https://opencode.ai/zen/go/v1/usage";

/// HTTP-backed OpenCode Go source; falls back to ~/.local/share/opencode/auth.json.
pub struct OpenCodeSource {
    api_key: Option<String>,
    auth_path: PathBuf,
    agent: ureq::Agent,
}

impl OpenCodeSource {
    /// `OPENCODE_API_KEY` or `OPENCODE_GO_API_KEY` env first, else ~/.local/share/opencode/auth.json.
    pub fn from_env() -> Self {
        let auth_path = std::env::var_os("HOME")
            .filter(|home| !home.is_empty())
            .map(|home| PathBuf::from(home).join(".local/share/opencode/auth.json"))
            .unwrap_or_else(|| PathBuf::from(".local/share/opencode/auth.json"));
        let key = non_empty_env("OPENCODE_API_KEY")
            .or_else(|| non_empty_env("OPENCODE_GO_API_KEY"));
        Self::new(key, auth_path)
    }

    pub fn new(api_key: Option<String>, auth_path: PathBuf) -> Self {
        Self {
            api_key,
            auth_path,
            agent: http_agent(),
        }
    }

    fn resolve_key(&self) -> Option<String> {
        if let Some(key) = &self.api_key {
            return Some(key.clone());
        }
        let text = fs::read_to_string(&self.auth_path).ok()?;
        let json: Value = serde_json::from_str(&text).ok()?;
        extract_opencode_key(&json)
    }

    pub fn has_credentials(&self) -> bool {
        self.resolve_key().is_some()
    }
}

impl QuotaSource for OpenCodeSource {
    fn fetch(&self) -> Vec<ProviderRecord> {
        let Some(key) = self.resolve_key() else {
            return vec![missing_record(PROVIDER_ID, DISPLAY_NAME, "no credentials")];
        };
        match http_get_usage(&self.agent, &key) {
            Ok(json) => map_response(&json, Utc::now()),
            Err(detail) => vec![error_record(PROVIDER_ID, DISPLAY_NAME, detail)],
        }
    }
}

fn http_get_usage(agent: &ureq::Agent, key: &str) -> Result<Value, String> {
    agent
        .get(USAGE_URL)
        .set("Authorization", &format!("Bearer {key}"))
        .set("User-Agent", "opencode/1.18.18")
        .set("Accept", "application/json")
        .call()
        .map_err(|e| format!("opencode usage request failed: {e}"))?
        .into_json::<Value>()
        .map_err(|e| format!("failed to decode opencode usage response: {e}"))
}

pub fn extract_opencode_key(json: &Value) -> Option<String> {
    for id in &["opencode-go", "opencode"] {
        if let Some(entry) = json.get(*id) {
            if let Some(s) = entry.as_str() {
                if !s.is_empty() {
                    return Some(s.to_string());
                }
            } else if let Some(map) = entry.as_object() {
                for field in &["key", "apiKey", "token"] {
                    if let Some(Value::String(s)) = map.get(*field) {
                        if !s.is_empty() {
                            return Some(s.clone());
                        }
                    }
                }
            }
        }
    }
    None
}

#[derive(Deserialize)]
struct OpenCodeUsageResponse {
    usage: Option<OpenCodeUsageData>,
}

#[derive(Deserialize)]
struct OpenCodeUsageData {
    rolling: Option<OpenCodeWindow>,
    weekly: Option<OpenCodeWindow>,
    monthly: Option<OpenCodeWindow>,
}

#[derive(Deserialize)]
struct OpenCodeWindow {
    percent: Option<f64>,
    #[serde(rename = "resetsAt")]
    resets_at: Option<String>,
}

pub fn map_response(json: &Value, fetched_at: DateTime<Utc>) -> Vec<ProviderRecord> {
    let parsed: OpenCodeUsageResponse = match serde_json::from_value(json.clone()) {
        Ok(parsed) => parsed,
        Err(e) => {
            return vec![error_record(
                PROVIDER_ID,
                DISPLAY_NAME,
                format!("invalid opencode usage response: {e}"),
            )]
        }
    };

    let Some(usage) = parsed.usage else {
        return vec![error_record(
            PROVIDER_ID,
            DISPLAY_NAME,
            "response contained no usage data".to_string(),
        )];
    };

    let mut out = Vec::new();

    let windows = [
        ("5h window", usage.rolling),
        ("Weekly", usage.weekly),
        ("Monthly", usage.monthly),
    ];

    for (label, window_opt) in windows {
        if let Some(w) = window_opt {
            if let Some(used) = w.percent {
                let mut record = Record::new(
                    PROVIDER_ID.to_string(),
                    Kind::Window,
                    Source::Api,
                    fetched_at,
                    None,
                );
                record.label = Some(label.to_string());
                record.display_name = Some(DISPLAY_NAME.to_string());
                record.used = Some(used);
                record.limit = Some(100.0);
                record.unit = Some("percent".to_string());
                record.resets_at = w.resets_at.as_deref().and_then(|s| {
                    DateTime::parse_from_rfc3339(s)
                        .ok()
                        .map(|dt| dt.with_timezone(&Utc))
                });
                out.push(ok_record(record, None));
            }
        }
    }

    if out.is_empty() {
        vec![error_record(
            PROVIDER_ID,
            DISPLAY_NAME,
            "no usage windows found in response".to_string(),
        )]
    } else {
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn maps_valid_opencode_fixture() {
        let fixture = json!({
            "usage": {
                "rolling": {
                    "status": "ok",
                    "percent": 98,
                    "resetsAt": "2026-08-20T18:16:41.571Z"
                },
                "weekly": {
                    "status": "ok",
                    "percent": 67,
                    "resetsAt": "2026-08-24T00:00:00.571Z"
                },
                "monthly": {
                    "status": "ok",
                    "percent": 33,
                    "resetsAt": "2026-09-17T07:13:01.571Z"
                }
            }
        });
        let now = Utc::now();
        let records = map_response(&fixture, now);
        assert_eq!(records.len(), 3);
        assert_eq!(records[0].record.as_ref().unwrap().label.as_deref(), Some("5h window"));
        assert_eq!(records[0].record.as_ref().unwrap().used, Some(98.0));
        assert_eq!(records[1].record.as_ref().unwrap().label.as_deref(), Some("Weekly"));
        assert_eq!(records[1].record.as_ref().unwrap().used, Some(67.0));
        assert_eq!(records[2].record.as_ref().unwrap().label.as_deref(), Some("Monthly"));
        assert_eq!(records[2].record.as_ref().unwrap().used, Some(33.0));
    }

    #[test]
    fn extract_opencode_key_works() {
        let fixture = json!({
            "opencode-go": {
                "type": "api",
                "key": "oc-go-test-key"
            }
        });
        assert_eq!(extract_opencode_key(&fixture), Some("oc-go-test-key".to_string()));
    }
}
