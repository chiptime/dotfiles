//! Claude Code pull source: usage windows from Anthropic's OAuth usage API.

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

pub const PROVIDER_ID: &str = "claude";
pub const DISPLAY_NAME: &str = "Claude Pro";
const USAGE_URL: &str = "https://api.anthropic.com/api/oauth/usage";

/// HTTP-backed Claude source; `access_token: None` means: fall back to ~/.claude/.credentials.json.
pub struct ClaudeSource {
    access_token: Option<String>,
    creds_path: PathBuf,
    agent: ureq::Agent,
}

impl ClaudeSource {
    /// `CLAUDE_ACCESS_TOKEN` env first (empty string = unset), else the
    /// ~/.claude/.credentials.json file.
    pub fn from_env() -> Self {
        let creds_path = std::env::var_os("HOME")
            .filter(|home| !home.is_empty())
            .map(|home| PathBuf::from(home).join(".claude/.credentials.json"))
            .unwrap_or_else(|| PathBuf::from(".claude/.credentials.json"));
        Self::new(non_empty_env("CLAUDE_ACCESS_TOKEN"), creds_path)
    }

    pub fn new(access_token: Option<String>, creds_path: PathBuf) -> Self {
        Self {
            access_token,
            creds_path,
            agent: http_agent(),
        }
    }

    /// Env token wins; otherwise parse ~/.claude/.credentials.json and extract accessToken.
    fn resolve_token(&self) -> Option<String> {
        if let Some(token) = &self.access_token {
            return Some(token.clone());
        }
        let text = fs::read_to_string(&self.creds_path).ok()?;
        let json: Value = serde_json::from_str(&text).ok()?;
        extract_claude_token(&json)
    }

    /// Whether a usable credential exists (env var or credentials file).
    pub fn has_credentials(&self) -> bool {
        self.resolve_token().is_some()
    }
}

impl QuotaSource for ClaudeSource {
    fn fetch(&self) -> Vec<ProviderRecord> {
        let Some(token) = self.resolve_token() else {
            return vec![missing_record(PROVIDER_ID, DISPLAY_NAME, "no credentials")];
        };
        let agent = &self.agent;
        match super::fetch_with_retry(|| http_get_usage(agent, &token)) {
            Ok(json) => map_response(&json, Utc::now()),
            Err(detail) => vec![error_record(PROVIDER_ID, DISPLAY_NAME, detail)],
        }
    }
}

fn http_get_usage(agent: &ureq::Agent, token: &str) -> Result<Value, String> {
    agent
        .get(USAGE_URL)
        .set("Authorization", &format!("Bearer {token}"))
        .set("anthropic-version", "2023-06-01")
        .set("User-Agent", "claude-code/2.1.224")
        .set("Accept", "application/json")
        .call()
        .map_err(|e| format!("usage request failed: {e}"))?
        .into_json::<Value>()
        .map_err(|e| format!("failed to decode usage response: {e}"))
}

pub fn extract_claude_token(json: &Value) -> Option<String> {
    json.get("claudeAiOauth")
        .and_then(|o| o.get("accessToken"))
        .and_then(|t| t.as_str())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

#[derive(Deserialize)]
struct ClaudeUsageResponse {
    five_hour: Option<ClaudeUsageWindow>,
    seven_day: Option<ClaudeUsageWindow>,
    /// Extra-usage / spend tracking (real money over the plan's cap).
    #[serde(default)]
    spend: Option<ClaudeSpend>,
}

#[derive(Deserialize)]
struct ClaudeUsageWindow {
    utilization: Option<f64>,
    resets_at: Option<String>,
}

/// Money amount as minor units with a decimal exponent (e.g. 1901 @ 2 -> 19.01).
#[derive(Deserialize)]
struct ClaudeMoney {
    amount_minor: i64,
    #[serde(default)]
    exponent: Option<u32>,
    #[serde(default)]
    currency: Option<String>,
}

#[derive(Deserialize)]
struct ClaudeSpend {
    used: Option<ClaudeMoney>,
    limit: Option<ClaudeMoney>,
    #[serde(default)]
    severity: Option<String>,
    #[serde(default)]
    disabled_reason: Option<String>,
}

fn money_value(m: &ClaudeMoney) -> f64 {
    let exp = m.exponent.unwrap_or(2);
    m.amount_minor as f64 / 10f64.powi(exp as i32)
}

pub fn map_response(json: &Value, fetched_at: DateTime<Utc>) -> Vec<ProviderRecord> {
    let parsed: ClaudeUsageResponse = match serde_json::from_value(json.clone()) {
        Ok(parsed) => parsed,
        Err(e) => {
            return vec![error_record(
                PROVIDER_ID,
                DISPLAY_NAME,
                format!("invalid usage response: {e}"),
            )]
        }
    };

    let mut out = Vec::new();

    if let Some(w) = parsed.five_hour {
        if let Some(used) = w.utilization {
            let mut record = Record::new(
                PROVIDER_ID.to_string(),
                Kind::Window,
                Source::Api,
                fetched_at,
                None,
            );
            record.label = Some("5h window".to_string());
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

    if let Some(w) = parsed.seven_day {
        if let Some(used) = w.utilization {
            let mut record = Record::new(
                PROVIDER_ID.to_string(),
                Kind::Window,
                Source::Api,
                fetched_at,
                None,
            );
            record.label = Some("Weekly".to_string());
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

    if let Some(spend) = parsed.spend {
        if let (Some(used), Some(limit)) = (spend.used.as_ref(), spend.limit.as_ref()) {
            let mut detail = spend
                .severity
                .as_deref()
                .map(|s| format!("severity: {s}"))
                .unwrap_or_default();
            if let Some(reason) = spend.disabled_reason.as_deref() {
                if !detail.is_empty() {
                    detail.push_str(" · ");
                }
                detail.push_str(&format!("disabled: {reason}"));
            }
            let mut record = Record::new(
                PROVIDER_ID.to_string(),
                Kind::Balance,
                Source::Api,
                fetched_at,
                None,
            );
            record.label = Some("Extra usage".to_string());
            record.display_name = Some(DISPLAY_NAME.to_string());
            record.used = Some(money_value(used));
            record.limit = Some(money_value(limit));
            record.currency = used.currency.clone().or_else(|| Some("EUR".to_string()));
            record.unit = Some("currency".to_string());
            out.push(ok_record(
                record,
                if detail.is_empty() { None } else { Some(detail) },
            ));
        }
    }

    if out.is_empty() {
        vec![error_record(
            PROVIDER_ID,
            DISPLAY_NAME,
            "usage response contained no recognizable windows".to_string(),
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
    fn maps_valid_usage_fixture() {
        let fixture = json!({
            "five_hour": {
                "utilization": 85.5,
                "resets_at": "2026-08-20T20:00:00Z"
            },
            "seven_day": {
                "utilization": 42.0,
                "resets_at": "2026-08-27T00:00:00Z"
            }
        });
        let now = Utc::now();
        let records = map_response(&fixture, now);
        assert_eq!(records.len(), 2);

        let h5 = records
            .iter()
            .find(|r| {
                r.record
                    .as_ref()
                    .and_then(|rec| rec.label.as_deref())
                    == Some("5h window")
            })
            .unwrap();
        assert_eq!(h5.record.as_ref().unwrap().used, Some(85.5));

        let w = records
            .iter()
            .find(|r| {
                r.record
                    .as_ref()
                    .and_then(|rec| rec.label.as_deref())
                    == Some("Weekly")
            })
            .unwrap();
        assert_eq!(w.record.as_ref().unwrap().used, Some(42.0));
    }

    #[test]
    fn maps_spend_extra_usage_into_balance_record() {
        let fixture = json!({
            "five_hour": { "utilization": 10.0, "resets_at": "2026-08-27T11:29:59Z" },
            "spend": {
                "used": { "amount_minor": 1901, "currency": "EUR", "exponent": 2 },
                "limit": { "amount_minor": 1700, "currency": "EUR", "exponent": 2 },
                "percent": 100,
                "severity": "critical",
                "disabled_reason": "org_level_disabled_until"
            }
        });
        let now = Utc::now();
        let records = map_response(&fixture, now);
        let spend = records
            .iter()
            .find(|r| r.record.as_ref().and_then(|rec| rec.label.as_deref()) == Some("Extra usage"))
            .expect("spend record present");
        let rec = spend.record.as_ref().unwrap();
        assert_eq!(rec.kind, Kind::Balance);
        assert_eq!(rec.used, Some(19.01));
        assert_eq!(rec.limit, Some(17.0));
        assert_eq!(rec.currency.as_deref(), Some("EUR"));
        assert_eq!(rec.resets_at, None); // balances never reset
        rec.validate().expect("balance without resets_at is valid");
        assert!(spend.detail.as_deref().unwrap().contains("severity: critical"));
    }

    #[test]
    fn extract_claude_token_works() {
        let fixture = json!({
            "claudeAiOauth": {
                "accessToken": "sk-ant-test-token-12345"
            }
        });
        assert_eq!(
            extract_claude_token(&fixture),
            Some("sk-ant-test-token-12345".to_string())
        );
        assert_eq!(extract_claude_token(&json!({})), None);
    }
}
