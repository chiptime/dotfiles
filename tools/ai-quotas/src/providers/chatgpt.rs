//! OpenAI Codex / ChatGPT pull source: WHAM usage windows from backend-api.

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

pub const PROVIDER_ID: &str = "chatgpt";
pub const DISPLAY_NAME: &str = "ChatGPT Plus";
const USAGE_URL: &str = "https://chatgpt.com/backend-api/wham/usage";

/// HTTP-backed ChatGPT / Codex source; falls back to ~/.codex/auth.json.
pub struct ChatGptSource {
    access_token: Option<String>,
    account_id: Option<String>,
    auth_path: PathBuf,
    agent: ureq::Agent,
}

impl ChatGptSource {
    /// `CHATGPT_ACCESS_TOKEN` / `CODEX_ACCESS_TOKEN` env first, else ~/.codex/auth.json.
    pub fn from_env() -> Self {
        let auth_path = std::env::var_os("HOME")
            .filter(|home| !home.is_empty())
            .map(|home| PathBuf::from(home).join(".codex/auth.json"))
            .unwrap_or_else(|| PathBuf::from(".codex/auth.json"));
        let token = non_empty_env("CHATGPT_ACCESS_TOKEN").or_else(|| non_empty_env("CODEX_ACCESS_TOKEN"));
        let account_id = non_empty_env("CHATGPT_ACCOUNT_ID").or_else(|| non_empty_env("CODEX_ACCOUNT_ID"));
        Self::new(token, account_id, auth_path)
    }

    pub fn new(access_token: Option<String>, account_id: Option<String>, auth_path: PathBuf) -> Self {
        Self {
            access_token,
            account_id,
            auth_path,
            agent: http_agent(),
        }
    }

    fn resolve_credentials(&self) -> Option<(String, Option<String>)> {
        if let Some(token) = &self.access_token {
            return Some((token.clone(), self.account_id.clone()));
        }
        let text = fs::read_to_string(&self.auth_path).ok()?;
        let json: Value = serde_json::from_str(&text).ok()?;
        extract_codex_credentials(&json)
    }

    pub fn has_credentials(&self) -> bool {
        self.resolve_credentials().is_some()
    }
}

impl QuotaSource for ChatGptSource {
    fn fetch(&self) -> Vec<ProviderRecord> {
        let Some((token, account_id)) = self.resolve_credentials() else {
            return vec![missing_record(PROVIDER_ID, DISPLAY_NAME, "no credentials")];
        };
        match http_get_usage(&self.agent, &token, account_id.as_deref()) {
            Ok(json) => map_response(&json, Utc::now()),
            Err(detail) => vec![error_record(PROVIDER_ID, DISPLAY_NAME, detail)],
        }
    }
}

fn http_get_usage(agent: &ureq::Agent, token: &str, account_id: Option<&str>) -> Result<Value, String> {
    let mut req = agent
        .get(USAGE_URL)
        .set("Authorization", &format!("Bearer {token}"))
        .set("User-Agent", "codex-cli/0.144.6")
        .set("Accept", "application/json");

    if let Some(acc) = account_id {
        req = req.set("chatgpt-account-id", acc);
    }

    req.call()
        .map_err(|e| format!("wham usage request failed: {e}"))?
        .into_json::<Value>()
        .map_err(|e| format!("failed to decode wham usage response: {e}"))
}

pub fn extract_codex_credentials(json: &Value) -> Option<(String, Option<String>)> {
    let tokens = json.get("tokens")?;
    let access_token = tokens.get("access_token")?.as_str()?.to_string();
    if access_token.is_empty() {
        return None;
    }
    let account_id = tokens
        .get("account_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    Some((access_token, account_id))
}

#[derive(Deserialize)]
struct WhamResponse {
    #[serde(default)]
    plan_type: Option<String>,
    #[serde(default)]
    rate_limit: Option<WhamRateLimit>,
}

#[derive(Deserialize)]
struct WhamRateLimit {
    primary_window: Option<WhamWindow>,
    secondary_window: Option<WhamWindow>,
}

#[derive(Deserialize)]
struct WhamWindow {
    used_percent: Option<f64>,
    reset_at: Option<i64>,
    limit_window_seconds: Option<u64>,
}

pub fn map_response(json: &Value, fetched_at: DateTime<Utc>) -> Vec<ProviderRecord> {
    let parsed: WhamResponse = match serde_json::from_value(json.clone()) {
        Ok(parsed) => parsed,
        Err(e) => {
            return vec![error_record(
                PROVIDER_ID,
                DISPLAY_NAME,
                format!("invalid wham response: {e}"),
            )]
        }
    };

    let display_name = match parsed.plan_type.as_deref() {
        Some("plus") => "ChatGPT Plus".to_string(),
        Some(other) => format!("ChatGPT {}", capitalize(other)),
        None => DISPLAY_NAME.to_string(),
    };

    let mut out = Vec::new();
    let rate_limit = parsed.rate_limit.unwrap_or(WhamRateLimit {
        primary_window: None,
        secondary_window: None,
    });

    if let Some(w) = rate_limit.primary_window {
        if let Some(used) = w.used_percent {
            let label = match w.limit_window_seconds {
                Some(secs) if secs >= 259_200 => "Weekly".to_string(),
                _ => "5h window".to_string(),
            };
            let mut record = Record::new(
                PROVIDER_ID.to_string(),
                Kind::Window,
                Source::Api,
                fetched_at,
                None,
            );
            record.label = Some(label);
            record.display_name = Some(display_name.clone());
            record.used = Some(used);
            record.limit = Some(100.0);
            record.unit = Some("percent".to_string());
            record.resets_at = w.reset_at.and_then(|ts| DateTime::from_timestamp(ts, 0));
            out.push(ok_record(record, None));
        }
    }

    if let Some(w) = rate_limit.secondary_window {
        if let Some(used) = w.used_percent {
            let mut record = Record::new(
                PROVIDER_ID.to_string(),
                Kind::Window,
                Source::Api,
                fetched_at,
                None,
            );
            record.label = Some("5h window".to_string());
            record.display_name = Some(display_name);
            record.used = Some(used);
            record.limit = Some(100.0);
            record.unit = Some("percent".to_string());
            record.resets_at = w.reset_at.and_then(|ts| DateTime::from_timestamp(ts, 0));
            out.push(ok_record(record, None));
        }
    }

    if out.is_empty() {
        vec![error_record(
            PROVIDER_ID,
            DISPLAY_NAME,
            "wham response contained no rate limit windows".to_string(),
        )]
    } else {
        out
    }
}

fn capitalize(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        None => String::new(),
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn maps_valid_wham_fixture() {
        let fixture = json!({
            "plan_type": "plus",
            "rate_limit": {
                "allowed": true,
                "primary_window": {
                    "used_percent": 35.0,
                    "limit_window_seconds": 604800,
                    "reset_at": 1787815692
                },
                "secondary_window": null
            }
        });
        let now = Utc::now();
        let records = map_response(&fixture, now);
        assert_eq!(records.len(), 1);
        let r = &records[0];
        assert_eq!(r.record.as_ref().unwrap().used, Some(35.0));
        assert_eq!(r.record.as_ref().unwrap().label.as_deref(), Some("Weekly"));
        assert_eq!(r.record.as_ref().unwrap().display_name.as_deref(), Some("ChatGPT Plus"));
    }

    #[test]
    fn extract_codex_credentials_works() {
        let fixture = json!({
            "tokens": {
                "access_token": "secret-access-token",
                "account_id": "acc-123"
            }
        });
        assert_eq!(
            extract_codex_credentials(&fixture),
            Some(("secret-access-token".to_string(), Some("acc-123".to_string())))
        );
        assert_eq!(extract_codex_credentials(&json!({})), None);
    }
}
