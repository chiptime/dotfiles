use std::fs;
use std::path::PathBuf;

use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::Value;

use super::{error_record, http_agent, missing_record, non_empty_env, ok_record, ProviderRecord, QuotaSource};
use crate::schema::{Kind, Record, Source};

pub const PROVIDER_ID: &str = "deepseek";
pub const DISPLAY_NAME: &str = "DeepSeek API";
const BALANCE_URL: &str = "https://api.deepseek.com/user/balance";

/// HTTP-backed DeepSeek source; falls back to ~/.local/share/opencode/auth.json.
pub struct DeepSeekSource {
    api_key: Option<String>,
    auth_path: PathBuf,
    agent: ureq::Agent,
}

impl DeepSeekSource {
    /// Resolve the API key from `DEEPSEEK_API_KEY`, else ~/.local/share/opencode/auth.json.
    pub fn from_env() -> Self {
        let auth_path = std::env::var_os("HOME")
            .filter(|home| !home.is_empty())
            .map(|home| PathBuf::from(home).join(".local/share/opencode/auth.json"))
            .unwrap_or_else(|| PathBuf::from(".local/share/opencode/auth.json"));
        Self::new(non_empty_env("DEEPSEEK_API_KEY"), auth_path)
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
        extract_deepseek_key(&json)
    }

    /// Whether `DEEPSEEK_API_KEY` was provided or present in auth.json.
    pub fn has_credentials(&self) -> bool {
        self.resolve_key().is_some()
    }
}

pub fn extract_deepseek_key(json: &Value) -> Option<String> {
    for id in &["deepseek", "deepseek-api", "deepseekai"] {
        if let Some(entry) = json.get(*id) {
            if let Some(s) = entry.as_str() {
                if !s.is_empty() {
                    return Some(s.to_string());
                }
            } else if let Some(map) = entry.as_object() {
                for field in &["key", "apiKey", "api_key", "token"] {
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

impl QuotaSource for DeepSeekSource {
    fn fetch(&self) -> Vec<ProviderRecord> {
        let Some(key) = self.resolve_key() else {
            return vec![missing_record(PROVIDER_ID, DISPLAY_NAME, "no credentials")];
        };
        match http_get_balance(&self.agent, &key) {
            Ok(json) => map_response(&json, Utc::now()),
            Err(detail) => vec![error_record(PROVIDER_ID, DISPLAY_NAME, detail)],
        }
    }
}

fn http_get_balance(agent: &ureq::Agent, key: &str) -> Result<serde_json::Value, String> {
    agent
        .get(BALANCE_URL)
        .set("Authorization", &format!("Bearer {key}"))
        .call()
        .map_err(|e| format!("balance request failed: {e}"))?
        .into_json::<serde_json::Value>()
        .map_err(|e| format!("failed to decode balance response: {e}"))
}

#[derive(Deserialize)]
struct BalanceResponse {
    #[serde(default)]
    balance_infos: Vec<BalanceInfo>,
}

#[derive(Deserialize)]
struct BalanceInfo {
    currency: String,
    total_balance: String,
}

/// Pure mapping of a balance API response body into records (no network).
/// Amounts arrive as strings and are parsed to f64. When several currencies
/// are present a USD entry is preferred, else the first entry.
pub fn map_response(json: &serde_json::Value, fetched_at: DateTime<Utc>) -> Vec<ProviderRecord> {
    let parsed: BalanceResponse = match serde_json::from_value(json.clone()) {
        Ok(parsed) => parsed,
        Err(e) => {
            return vec![error_record(
                PROVIDER_ID,
                DISPLAY_NAME,
                format!("invalid balance response: {e}"),
            )]
        }
    };
    let info = match parsed
        .balance_infos
        .iter()
        .find(|i| i.currency == "USD")
        .or_else(|| parsed.balance_infos.first())
    {
        Some(info) => info,
        None => {
            return vec![error_record(
                PROVIDER_ID,
                DISPLAY_NAME,
                "balance response contained no balance_infos".to_string(),
            )]
        }
    };
    let total = match info.total_balance.parse::<f64>() {
        Ok(total) => total,
        Err(e) => {
            return vec![error_record(
                PROVIDER_ID,
                DISPLAY_NAME,
                format!("invalid total_balance '{}': {e}", info.total_balance),
            )]
        }
    };

    let mut record = Record::new(
        PROVIDER_ID.to_string(),
        Kind::Balance,
        Source::Api,
        fetched_at,
        None,
    );
    record.limit = Some(total);
    record.currency = Some(info.currency.clone());
    record.unit = Some("currency".to_string());
    record.display_name = Some(DISPLAY_NAME.to_string());
    vec![ok_record(record, None)]
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

    #[test]
    fn maps_single_cny_entry() {
        let json = serde_json::json!({
            "is_available": true,
            "balance_infos": [{
                "currency": "CNY",
                "total_balance": "110.00",
                "granted_balance": "10.00",
                "topped_up_balance": "100.00"
            }]
        });
        let records = map_response(&json, at());
        assert_eq!(records.len(), 1);
        let status = &records[0];
        assert_eq!(status.state, State::Ok);
        assert_eq!(status.provider, "deepseek");
        assert_eq!(status.display_name.as_deref(), Some("DeepSeek API"));
        let record = status.record.as_ref().unwrap();
        assert_eq!(record.kind, Kind::Balance);
        assert_eq!(record.source, Source::Api);
        assert_eq!(record.used, None);
        assert_eq!(record.limit, Some(110.0));
        assert_eq!(record.currency.as_deref(), Some("CNY"));
        assert_eq!(record.unit.as_deref(), Some("currency"));
        assert_eq!(record.label, None);
        assert_eq!(record.resets_at, None);
        assert_eq!(record.fetched_at, at());
    }

    #[test]
    fn multi_entry_prefers_usd() {
        let json = serde_json::json!({
            "is_available": true,
            "balance_infos": [
                {"currency": "CNY", "total_balance": "110.00"},
                {"currency": "USD", "total_balance": "50.25"}
            ]
        });
        let records = map_response(&json, at());
        assert_eq!(records.len(), 1);
        let record = records[0].record.as_ref().unwrap();
        assert_eq!(record.currency.as_deref(), Some("USD"));
        assert_eq!(record.limit, Some(50.25));
    }

    #[test]
    fn missing_creds_yields_single_missing_record() {
        let records = DeepSeekSource::new(None, PathBuf::from("/nonexistent/auth.json")).fetch();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].state, State::Missing);
        assert_eq!(records[0].provider, "deepseek");
        assert_eq!(records[0].display_name.as_deref(), Some("DeepSeek API"));
        assert_eq!(records[0].detail.as_deref(), Some("no credentials"));
        assert!(records[0].record.is_none());
    }

    #[test]
    fn no_balance_infos_is_error() {
        let json = serde_json::json!({"is_available": true, "balance_infos": []});
        let records = map_response(&json, at());
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].state, State::Error);
        assert!(records[0].detail.as_deref().unwrap().contains("no balance_infos"));
    }

    #[test]
    fn unparseable_body_is_error() {
        let json = serde_json::json!([1, 2, 3]);
        let records = map_response(&json, at());
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].state, State::Error);
        assert!(records[0].detail.as_deref().unwrap().contains("invalid balance response"));
    }
}
