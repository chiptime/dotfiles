//! Z.ai coding-plan pull source: usage windows from the monitor API.

use std::fs;
use std::path::PathBuf;

use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::Value;

use super::{
    error_record, http_agent, missing_record, non_empty_env, ok_record, ProviderRecord, QuotaSource,
};
use crate::schema::{Kind, Record, Source};

pub const PROVIDER_ID: &str = "zai";
pub const DISPLAY_NAME: &str = "Z.ai Coding Plan MAX";
const QUOTA_URL: &str = "https://api.z.ai/api/monitor/usage/quota/limit";

/// auth.json entry ids tried in order.
const AUTH_IDS: &[&str] = &["zai-coding-plan", "zai", "z-ai", "z.ai", "zhipu", "zhipuai"];
/// String fields tried in order inside an entry object.
const AUTH_FIELDS: &[&str] = &["apiKey", "api_key", "token", "key", "accessToken", "auth_token"];

/// HTTP-backed Z.ai source; `api_key: None` means: fall back to auth.json.
pub struct ZaiSource {
    api_key: Option<String>,
    auth_path: PathBuf,
    agent: ureq::Agent,
}

impl ZaiSource {
    /// `ZAI_API_KEY` env first (empty string = unset), else the opencode
    /// auth.json under `$HOME/.local/share/opencode/auth.json`.
    pub fn from_env() -> Self {
        let auth_path = std::env::var_os("HOME")
            .filter(|home| !home.is_empty())
            .map(|home| PathBuf::from(home).join(".local/share/opencode/auth.json"))
            .unwrap_or_else(|| PathBuf::from(".local/share/opencode/auth.json"));
        Self::new(non_empty_env("ZAI_API_KEY"), auth_path)
    }

    pub fn new(api_key: Option<String>, auth_path: PathBuf) -> Self {
        Self {
            api_key,
            auth_path,
            agent: http_agent(),
        }
    }

    /// Env key wins; otherwise parse auth.json and extract the Z.ai entry.
    fn resolve_key(&self) -> Option<String> {
        if let Some(key) = &self.api_key {
            return Some(key.clone());
        }
        let text = fs::read_to_string(&self.auth_path).ok()?;
        let json: Value = serde_json::from_str(&text).ok()?;
        extract_zai_key(&json)
    }

    /// Whether a usable credential exists (env var or auth.json entry).
    pub fn has_credentials(&self) -> bool {
        self.resolve_key().is_some()
    }
}

impl QuotaSource for ZaiSource {
    fn fetch(&self) -> Vec<ProviderRecord> {
        let Some(key) = self.resolve_key() else {
            return vec![missing_record(PROVIDER_ID, DISPLAY_NAME, "no credentials")];
        };
        match http_get_quota(&self.agent, &key) {
            Ok(json) => map_response(&json, Utc::now()),
            Err(detail) => vec![error_record(PROVIDER_ID, DISPLAY_NAME, detail)],
        }
    }
}

fn http_get_quota(agent: &ureq::Agent, token: &str) -> Result<Value, String> {
    agent
        .get(QUOTA_URL)
        // Verified quirk: the monitor API wants the RAW token, no Bearer prefix.
        .set("Authorization", token)
        .set("Accept-Language", "en-US,en")
        .set("Content-Type", "application/json")
        .call()
        .map_err(|e| format!("quota request failed: {e}"))?
        .into_json::<Value>()
        .map_err(|e| format!("failed to decode quota response: {e}"))
}

/// Pure: resolve the Z.ai key from a parsed auth.json value. Entry ids are
/// tried in priority order; an entry is either a plain string or an object
/// whose first present string field (among `AUTH_FIELDS`) wins.
pub fn extract_zai_key(json: &Value) -> Option<String> {
    for id in AUTH_IDS {
        let Some(entry) = json.get(id) else {
            continue;
        };
        match entry {
            Value::String(s) if !s.is_empty() => return Some(s.clone()),
            Value::Object(map) => {
                for field in AUTH_FIELDS {
                    if let Some(Value::String(s)) = map.get(*field) {
                        if !s.is_empty() {
                            return Some(s.clone());
                        }
                    }
                }
            }
            _ => {}
        }
    }
    None
}

#[derive(Deserialize)]
struct QuotaResponse {
    #[serde(default)]
    level: Option<String>,
    #[serde(default)]
    limits: Vec<QuotaLimit>,
}

#[derive(Deserialize)]
struct QuotaLimit {
    #[serde(rename = "type")]
    limit_type: String,
    #[serde(default)]
    unit: Option<i64>,
    #[serde(default)]
    number: Option<i64>,
    #[serde(default)]
    percentage: Option<f64>,
    #[serde(rename = "nextResetTime", default)]
    next_reset_ms: Option<i64>,
    #[serde(rename = "currentValue", default)]
    current_value: Option<f64>,
    #[serde(default)]
    usage: Option<f64>,
}

/// Pure mapping of a quota API response body into records (no network).
///
/// Semantics (verified against the reference plugin): `percentage` is the
/// percent USED. TOKENS_LIMIT `unit=3,number=5` is the 5h window and
/// `unit=6,number=1` the weekly window (both percent of 100). TIME_LIMIT is
/// the MCP monthly allowance where the field `usage` is the TOTAL limit and
/// `currentValue` the used amount (confusing naming, confirmed). Live
/// responses carry `nextResetTime` (Unix MILLISECONDS) on all limit types
/// including TIME_LIMIT; it is mapped whenever present.
pub fn map_response(json: &Value, fetched_at: DateTime<Utc>) -> Vec<ProviderRecord> {
    // The live API envelops the payload: {"code":200,"msg":"...","data":{...}}.
    // Unwrap when present; a bare body is also accepted.
    let body = json.get("data").unwrap_or(json);
    let parsed: QuotaResponse = match serde_json::from_value(body.clone()) {
        Ok(parsed) => parsed,
        Err(e) => {
            return vec![error_record(
                PROVIDER_ID,
                DISPLAY_NAME,
                format!("invalid quota response: {e}"),
            )]
        }
    };
    let level_detail = parsed
        .level
        .as_deref()
        .map(|level| format!("plan level: {level}"));

    let mut out = Vec::new();
    for limit in &parsed.limits {
        let is_time_limit = limit.limit_type == "TIME_LIMIT";
        let label = match limit.limit_type.as_str() {
            "TOKENS_LIMIT" => match (limit.unit, limit.number) {
                (Some(3), Some(5)) => "5h window".to_string(),
                (Some(6), Some(1)) => "Weekly".to_string(),
                (unit, number) => format!(
                    "Token usage(unit={},number={})",
                    fmt_code(unit),
                    fmt_code(number)
                ),
            },
            "TIME_LIMIT" => "MCP monthly".to_string(),
            // Unknown limit types are skipped, not guessed at.
            _ => continue,
        };

        let mut record = Record::new(
            PROVIDER_ID.to_string(),
            Kind::Window,
            Source::Api,
            fetched_at,
            None,
        );
        record.label = Some(label);
        record.display_name = Some(DISPLAY_NAME.to_string());
        if is_time_limit {
            record.used = limit.current_value;
            record.limit = limit.usage;
            record.unit = Some("requests".to_string());
        } else {
            record.used = limit.percentage;
            record.limit = Some(100.0);
            record.unit = Some("percent".to_string());
        }
        // Live-verified: TIME_LIMIT ("MCP monthly") rows also carry
        // nextResetTime; map it for every limit type when present.
        record.resets_at = limit.next_reset_ms.and_then(DateTime::from_timestamp_millis);
        out.push(ok_record(record, level_detail.clone()));
    }
    out
}

fn fmt_code(value: Option<i64>) -> String {
    value
        .map(|n| n.to_string())
        .unwrap_or_else(|| "?".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reader::State;

    fn at() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-08-20T14:05:12Z")
            .unwrap()
            .with_timezone(&Utc)
    }

    fn ts(raw: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(raw).unwrap().with_timezone(&Utc)
    }

    fn by_label<'a>(records: &'a [ProviderRecord], label: &str) -> &'a ProviderRecord {
        records
            .iter()
            .find(|r| r.record.as_ref().unwrap().label.as_deref() == Some(label))
            .unwrap()
    }

    #[test]
    fn maps_full_three_limit_fixture() {
        let json = serde_json::json!({
            "level": "pro",
            "limits": [
                {"type":"TOKENS_LIMIT","unit":3,"number":5,"percentage":40.5,"nextResetTime":1737123456000i64},
                {"type":"TOKENS_LIMIT","unit":6,"number":1,"percentage":52.0,"nextResetTime":1737180000000i64},
                {"type":"TIME_LIMIT","percentage":12.3,"currentValue":123,"usage":1000,"usageDetails":[{"modelCode":"search-prime","usage":5678}]}
            ]
        });
        let records = map_response(&json, at());
        assert_eq!(records.len(), 3);

        let five = by_label(&records, "5h window");
        let five_rec = five.record.as_ref().unwrap();
        assert_eq!(five_rec.used, Some(40.5));
        assert_eq!(five_rec.limit, Some(100.0));
        assert_eq!(five_rec.unit.as_deref(), Some("percent"));
        assert_eq!(five_rec.resets_at, Some(ts("2025-01-17T14:17:36Z")));

        let weekly = by_label(&records, "Weekly");
        let weekly_rec = weekly.record.as_ref().unwrap();
        assert_eq!(weekly_rec.used, Some(52.0));
        assert_eq!(weekly_rec.limit, Some(100.0));
        assert_eq!(weekly_rec.unit.as_deref(), Some("percent"));
        assert_eq!(weekly_rec.resets_at, Some(ts("2025-01-18T06:00:00Z")));

        let mcp = by_label(&records, "MCP monthly");
        let mcp_rec = mcp.record.as_ref().unwrap();
        assert_eq!(mcp_rec.used, Some(123.0));
        assert_eq!(mcp_rec.limit, Some(1000.0));
        assert_eq!(mcp_rec.unit.as_deref(), Some("requests"));
        assert_eq!(mcp_rec.resets_at, None);

        for status in &records {
            assert_eq!(status.state, State::Ok);
            assert_eq!(status.provider, "zai");
            assert_eq!(status.display_name.as_deref(), Some("Z.ai Coding Plan MAX"));
            let record = status.record.as_ref().unwrap();
            assert_eq!(record.kind, Kind::Window);
            assert_eq!(record.source, Source::Api);
            assert_eq!(record.fetched_at, at());
            assert_eq!(status.detail.as_deref(), Some("plan level: pro"));
        }
    }

    #[test]
    fn generic_tokens_limit_label() {
        let json = serde_json::json!({
            "limits": [
                {"type":"TOKENS_LIMIT","unit":9,"number":2,"percentage":7.5,"nextResetTime":1737123456000i64}
            ]
        });
        let records = map_response(&json, at());
        assert_eq!(records.len(), 1);
        let record = records[0].record.as_ref().unwrap();
        assert_eq!(record.label.as_deref(), Some("Token usage(unit=9,number=2)"));
        assert_eq!(record.used, Some(7.5));
        assert_eq!(record.limit, Some(100.0));
        // No level in the body -> no detail.
        assert_eq!(records[0].detail, None);
    }

    #[test]
    fn extract_zai_key_plain_string_entry() {
        let json = serde_json::json!({"zai": "sk-plain"});
        assert_eq!(extract_zai_key(&json), Some("sk-plain".to_string()));
    }

    #[test]
    fn extract_zai_key_object_api_key_camel() {
        let json = serde_json::json!({"zhipu": {"apiKey": "sk-camel"}});
        assert_eq!(extract_zai_key(&json), Some("sk-camel".to_string()));
    }

    #[test]
    fn extract_zai_key_object_api_key_snake() {
        let json = serde_json::json!({"z.ai": {"api_key": "sk-snake"}});
        assert_eq!(extract_zai_key(&json), Some("sk-snake".to_string()));
    }

    #[test]
    fn extract_zai_key_priority_order() {
        let json = serde_json::json!({
            "zhipu": "sk-low",
            "zai": "sk-mid",
            "zai-coding-plan": "sk-high"
        });
        assert_eq!(extract_zai_key(&json), Some("sk-high".to_string()));
    }

    #[test]
    fn extract_zai_key_all_miss_is_none() {
        assert_eq!(extract_zai_key(&serde_json::json!({})), None);
        assert_eq!(
            extract_zai_key(&serde_json::json!({"openai": "sk-other"})),
            None
        );
        // Known id but no usable string field.
        assert_eq!(
            extract_zai_key(&serde_json::json!({"zai": {"apiKey": 123}})),
            None
        );
        assert_eq!(extract_zai_key(&serde_json::json!({"zai": ""})), None);
    }

    #[test]
    fn missing_creds_when_env_and_auth_json_absent() {
        let source = ZaiSource::new(None, PathBuf::from("/nonexistent-opencode-auth.json"));
        let records = source.fetch();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].state, State::Missing);
        assert_eq!(records[0].provider, "zai");
        assert_eq!(records[0].display_name.as_deref(), Some("Z.ai Coding Plan MAX"));
        assert_eq!(records[0].detail.as_deref(), Some("no credentials"));
        assert!(records[0].record.is_none());
    }

    #[test]
    fn unwraps_enveloped_live_response() {
        // Shape captured from the real API (values shortened).
        let json = serde_json::json!({
            "code": 200, "msg": "Operation successful", "success": true,
            "data": {
                "level": "max",
                "limits": [
                    {"type":"TIME_LIMIT","unit":5,"number":1,"usage":4000,"currentValue":315,"percentage":7,"nextResetTime":1787559464983i64},
                    {"type":"TOKENS_LIMIT","unit":3,"number":5,"percentage":24,"nextResetTime":1787241703240i64}
                ]
            }
        });
        let records = map_response(&json, at());
        assert_eq!(records.len(), 2);
        let mcp = by_label(&records, "MCP monthly");
        let mcp_rec = mcp.record.as_ref().unwrap();
        assert_eq!(mcp_rec.used, Some(315.0));
        assert_eq!(mcp_rec.limit, Some(4000.0));
        assert_eq!(
            mcp_rec.resets_at,
            DateTime::from_timestamp_millis(1_787_559_464_983),
            "TIME_LIMIT resets_at comes from nextResetTime when present"
        );
        let five = by_label(&records, "5h window");
        assert_eq!(five.record.as_ref().unwrap().used, Some(24.0));
        assert!(records.iter().all(|r| r.detail.as_deref() == Some("plan level: max")));
    }

    #[test]
    fn unparseable_body_is_error() {
        let json = serde_json::json!("not an object");
        let records = map_response(&json, at());
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].state, State::Error);
        assert!(records[0].detail.as_deref().unwrap().contains("invalid quota response"));
    }
}
