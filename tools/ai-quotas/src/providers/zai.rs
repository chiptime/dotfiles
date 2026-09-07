//! Z.ai coding-plan pull source: usage windows from the monitor API.

use std::fs;
use std::path::PathBuf;

use chrono::{DateTime, FixedOffset, NaiveDateTime, Utc};
use serde::Deserialize;
use serde_json::Value;

use super::{
    error_record, http_agent, missing_record, non_empty_env, ok_record, ProviderRecord, QuotaSource,
};
use crate::schema::{Kind, Record, ResetCredit, ResetCredits, Source};

pub const PROVIDER_ID: &str = "zai";
pub const DISPLAY_NAME: &str = "Z.ai Coding Plan MAX";
const QUOTA_URL: &str = "https://api.z.ai/api/monitor/usage/quota/limit";
/// Earned "usage limit resets" (customer-package resets), authenticated the
/// same way as the monitor endpoint.
const RESETS_URL: &str = "https://api.z.ai/api/biz/customer-package-reset/list?targetType=PERSONAL";
/// Label of the primary TOKENS_LIMIT row; reset credits ride on this record.
const FIVE_HOUR_LABEL: &str = "5h window";

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
            Ok(json) => {
                let mut records = map_response(&json, Utc::now());
                // Secondary endpoint (earned usage-limit resets) is
                // best-effort: any failure there must never degrade the
                // main quota records.
                merge_resets(&mut records, http_get_resets(&self.agent, &key));
                records
            }
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

fn http_get_resets(agent: &ureq::Agent, token: &str) -> Result<Value, String> {
    agent
        .get(RESETS_URL)
        // Same verified auth quirk as the monitor API: RAW token, no Bearer.
        .set("Authorization", token)
        .set("Accept", "application/json")
        .set("Accept-Language", "en-US,en")
        .call()
        .map_err(|e| format!("resets request failed: {e}"))?
        .into_json::<Value>()
        .map_err(|e| format!("failed to decode resets response: {e}"))
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
    /// Per-model usage breakdown (observed on TIME_LIMIT rows).
    #[serde(rename = "usageDetails", default)]
    usage_details: Vec<UsageDetail>,
}

#[derive(Deserialize)]
struct UsageDetail {
    #[serde(rename = "modelCode")]
    model_code: Option<String>,
    #[serde(default)]
    usage: Option<f64>,
}

/// One earned reset credit in the customer-package-reset payload. Both
/// windows ride as plain arrays; unknown fields are ignored.
#[derive(Deserialize)]
struct ResetEntry {
    #[serde(default)]
    available: bool,
    /// Naive server-local timestamp, e.g. "2026-10-01 23:59:59".
    #[serde(rename = "expireTime", default)]
    expire_time: Option<String>,
}

/// `data` payload of the customer-package-reset endpoint.
#[derive(Deserialize)]
struct ResetListData {
    #[serde(rename = "fiveHourResets", default)]
    five_hour_resets: Vec<ResetEntry>,
    #[serde(rename = "weekResets", default)]
    week_resets: Vec<ResetEntry>,
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
                (Some(3), Some(5)) => FIVE_HOUR_LABEL.to_string(),
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
        // Per-model breakdown rides in `detail` so the UI can show it
        // alongside the absolute request counts.
        let mut detail = level_detail.clone().unwrap_or_default();
        let breakdown: Vec<String> = limit
            .usage_details
            .iter()
            .filter_map(|d| {
                Some(format!(
                    "{} {}",
                    d.model_code.as_deref().unwrap_or("?"),
                    d.usage?
                ))
            })
            .collect();
        if !breakdown.is_empty() {
            if !detail.is_empty() {
                detail.push_str(" · ");
            }
            detail.push_str(&breakdown.join(" · "));
        }
        out.push(ok_record(
            record,
            if detail.is_empty() { None } else { Some(detail) },
        ));
    }
    out
}

fn fmt_code(value: Option<i64>) -> String {
    value
        .map(|n| n.to_string())
        .unwrap_or_else(|| "?".to_string())
}

/// Pure: parse one naive `expireTime` ("YYYY-MM-DD HH:MM:SS") into a UTC
/// instant. The z.ai console serves server-local timestamps, observed to
/// follow Asia/Shanghai (UTC+8) — attach that fixed offset.
fn parse_expire_time(raw: &str) -> Option<DateTime<Utc>> {
    let naive = NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S").ok()?;
    let offset = FixedOffset::east_opt(8 * 3600)?;
    naive
        .and_local_timezone(offset)
        .single()
        .map(|dt| dt.with_timezone(&Utc))
}

/// Pure: map a customer-package-reset response into reset credits. Counts
/// entries with `available == true` across BOTH windows, emits one row per
/// available entry (each carrying its own expiry), and takes the earliest
/// expiry among AVAILABLE entries only (derived via the shared helper).
/// Empty lists are a valid answer (zero resets), an unusable payload yields
/// `None`.
pub fn map_resets(json: &Value) -> Option<ResetCredits> {
    // Same envelope convention as the monitor API: payload under "data",
    // bare bodies also accepted.
    let body = json.get("data").unwrap_or(json);
    let parsed: ResetListData = match serde_json::from_value(body.clone()) {
        Ok(parsed) => parsed,
        Err(_) => return None,
    };
    let mut available = 0u64;
    let mut credits = Vec::new();
    for entry in parsed
        .five_hour_resets
        .iter()
        .chain(parsed.week_resets.iter())
    {
        if !entry.available {
            continue;
        }
        available += 1;
        credits.push(ResetCredit {
            expires_at: entry.expire_time.as_deref().and_then(parse_expire_time),
            // The z.ai payload carries no human title per entry.
            title: None,
        });
    }
    Some(ResetCredits::with_credits(available, None, credits))
}

/// Best-effort merge of the resets endpoint into already-mapped records:
/// the credits belong to the primary TOKENS_LIMIT (5h) record. ANY failure
/// (HTTP error, unusable payload, no 5h record) is swallowed so this
/// secondary endpoint can never degrade the main quota fetch.
fn merge_resets(records: &mut [ProviderRecord], resets: Result<Value, String>) {
    let Some(credits) = resets.ok().and_then(|json| map_resets(&json)) else {
        return;
    };
    for status in records.iter_mut() {
        if let Some(record) = &mut status.record {
            if record.label.as_deref() == Some(FIVE_HOUR_LABEL) {
                record.reset_credits = Some(credits);
                return;
            }
        }
    }
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
            assert!(status.detail.as_deref().unwrap().starts_with("plan level: pro"));
        }

        // The TIME_LIMIT row carries its per-model usage breakdown in `detail`.
        assert!(mcp.detail.as_deref().unwrap().contains("plan level: pro"));
        assert!(mcp.detail.as_deref().unwrap().contains("search-prime 5678"));
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

    fn resets_fixture() -> serde_json::Value {
        // Shape captured from the real customer-package-reset endpoint.
        serde_json::json!({
            "code": 200, "msg": "Operation successful", "success": true,
            "data": {
                "customerId": 123, "targetType": "PERSONAL",
                "organizationId": null, "projectId": null,
                "lastFiveHourResetTime": null, "lastWeekResetTime": null,
                "fiveHourResets": [],
                "weekResets": [
                    {"recordId": 288311, "expireTime": "2026-10-01 23:59:59", "available": true}
                ]
            }
        })
    }

    #[test]
    fn map_resets_parses_available_with_shanghai_offset() {
        let credits = map_resets(&resets_fixture()).unwrap();
        // 2026-10-01 23:59:59 at UTC+8 == 15:59:59 UTC the same day.
        assert_eq!(
            credits,
            ResetCredits {
                available: 1,
                applicable: None,
                expires_at: Some(ts("2026-10-01T15:59:59Z")),
                credits: vec![ResetCredit {
                    expires_at: Some(ts("2026-10-01T15:59:59Z")),
                    title: None
                }]
            }
        );
    }

    #[test]
    fn map_resets_counts_both_windows_and_picks_earliest_available() {
        let json = serde_json::json!({
            "data": {
                "fiveHourResets": [
                    {"recordId": 1, "expireTime": "2026-09-10 08:00:00", "available": true}
                ],
                "weekResets": [
                    {"recordId": 2, "expireTime": "2026-09-05 08:00:00", "available": true},
                    // Unavailable entries never count, even when earlier.
                    {"recordId": 3, "expireTime": "2026-08-01 08:00:00", "available": false},
                    {"recordId": 4, "expireTime": "2026-10-01 23:59:59", "available": true}
                ]
            }
        });
        let credits = map_resets(&json).unwrap();
        assert_eq!(credits.available, 3);
        assert_eq!(credits.expires_at, Some(ts("2026-09-05T00:00:00Z")));
        // One row per available entry (2 week + 1 five-hour), unavailable
        // ones excluded; the z.ai payload carries no titles.
        assert_eq!(credits.credits.len(), 3);
        assert!(credits.credits.iter().all(|c| c.title.is_none()));
        assert!(credits
            .credits
            .iter()
            .all(|c| c.expires_at != Some(ts("2026-08-01T00:00:00Z"))));
    }

    #[test]
    fn map_resets_empty_lists_are_zero_resets() {
        let json = serde_json::json!({
            "data": {"customerId": 1, "fiveHourResets": [], "weekResets": []}
        });
        let credits = map_resets(&json).unwrap();
        assert_eq!(
            credits,
            ResetCredits {
                available: 0,
                applicable: None,
                expires_at: None,
                credits: Vec::new()
            }
        );
    }

    #[test]
    fn map_resets_all_unavailable_is_zero_with_no_expiry() {
        let json = serde_json::json!({
            "data": {
                "fiveHourResets": [],
                "weekResets": [{"recordId": 9, "expireTime": "2026-10-01 23:59:59", "available": false}]
            }
        });
        let credits = map_resets(&json).unwrap();
        assert_eq!(credits.available, 0);
        assert_eq!(credits.expires_at, None, "expiry only among AVAILABLE entries");
    }

    #[test]
    fn map_resets_unparseable_payload_is_none() {
        assert_eq!(map_resets(&serde_json::json!("garbage")), None);
        assert_eq!(map_resets(&serde_json::json!({"data": {"weekResets": 42}})), None);
    }

    #[test]
    fn merge_resets_attaches_to_five_hour_record_only() {
        let json = serde_json::json!({
            "limits": [
                {"type":"TOKENS_LIMIT","unit":3,"number":5,"percentage":24.0},
                {"type":"TOKENS_LIMIT","unit":6,"number":1,"percentage":52.0}
            ]
        });
        let mut records = map_response(&json, at());
        merge_resets(&mut records, Ok(resets_fixture()));
        let five = by_label(&records, FIVE_HOUR_LABEL).record.as_ref().unwrap();
        assert_eq!(five.reset_credits.as_ref().unwrap().available, 1);
        let weekly = by_label(&records, "Weekly").record.as_ref().unwrap();
        assert_eq!(weekly.reset_credits, None);
    }

    #[test]
    fn merge_resets_failure_keeps_records_untouched() {
        let json = serde_json::json!({
            "limits": [{"type":"TOKENS_LIMIT","unit":3,"number":5,"percentage":24.0}]
        });
        // HTTP failure of the second endpoint: records intact, no credits.
        let mut records = map_response(&json, at());
        merge_resets(&mut records, Err("resets request failed: boom".to_string()));
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].state, State::Ok);
        assert_eq!(records[0].record.as_ref().unwrap().reset_credits, None);

        // Unusable payload: same outcome.
        let mut records = map_response(&json, at());
        merge_resets(&mut records, Ok(serde_json::json!("garbage")));
        assert_eq!(records[0].record.as_ref().unwrap().reset_credits, None);

        // Valid payload but no 5h record to attach to: nothing breaks.
        let json_no_five = serde_json::json!({
            "limits": [{"type":"TIME_LIMIT","percentage":12.3,"currentValue":123,"usage":1000}]
        });
        let mut records = map_response(&json_no_five, at());
        merge_resets(&mut records, Ok(resets_fixture()));
        assert_eq!(records[0].record.as_ref().unwrap().reset_credits, None);
        assert_eq!(records[0].state, State::Ok);
    }
}
