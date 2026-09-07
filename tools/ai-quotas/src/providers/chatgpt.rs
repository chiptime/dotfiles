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
use crate::schema::{Kind, Record, ResetCredit, ResetCredits, Source};

pub const PROVIDER_ID: &str = "chatgpt";
pub const DISPLAY_NAME: &str = "ChatGPT Plus";
const USAGE_URL: &str = "https://chatgpt.com/backend-api/wham/usage";
/// Per-credit reset rows (each earned reset carries its own expiry).
const RESETS_URL: &str = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits";

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
        let agent = &self.agent;
        match super::fetch_with_retry(|| http_get_usage(agent, &token, account_id.as_deref())) {
            Ok(usage) => {
                // Best-effort second endpoint: per-credit expiry rows. ANY
                // failure there (HTTP or parse) degrades to the counts-only
                // shape from wham/usage and never becomes an Error record.
                let resets = super::fetch_with_retry(|| {
                    http_get_resets(agent, &token, account_id.as_deref())
                })
                .ok();
                map_response_with_resets(&usage, resets.as_ref(), Utc::now())
            }
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

/// Same auth shape as the usage call (Bearer + account id + codex-cli UA).
fn http_get_resets(agent: &ureq::Agent, token: &str, account_id: Option<&str>) -> Result<Value, String> {
    let mut req = agent
        .get(RESETS_URL)
        .set("Authorization", &format!("Bearer {token}"))
        .set("User-Agent", "codex-cli/0.144.6")
        .set("Accept", "application/json");

    if let Some(acc) = account_id {
        req = req.set("chatgpt-account-id", acc);
    }

    req.call()
        .map_err(|e| format!("reset credits request failed: {e}"))?
        .into_json::<Value>()
        .map_err(|e| format!("failed to decode reset credits response: {e}"))
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
    /// Earned usage-limit resets (top level). May be absent or null; the
    /// counts themselves may also be missing individually.
    #[serde(default)]
    rate_limit_reset_credits: Option<WhamResetCredits>,
}

/// `rate_limit_reset_credits` payload: earned one-shot resets usable after
/// hitting a cap. All fields lenient (absent/null -> None).
#[derive(Deserialize)]
struct WhamResetCredits {
    #[serde(default)]
    available_count: Option<u64>,
    #[serde(default)]
    applicable_available_count: Option<u64>,
}

/// One entry of the `rate-limit-reset-credits` payload: a single earned
/// reset with its own lifecycle. Lenient by design — unknown fields are
/// ignored and every mapped field is optional.
#[derive(Deserialize)]
struct ResetsCreditEntry {
    /// Only "available" entries are usable; spent ones say "redeemed".
    #[serde(default)]
    status: Option<String>,
    /// RFC3339 UTC, e.g. "2026-09-20T23:34:55.784003Z".
    #[serde(default)]
    expires_at: Option<String>,
    /// Human title, e.g. "Full reset (Weekly + 5 hr)".
    #[serde(default)]
    title: Option<String>,
}

/// `rate-limit-reset-credits` payload: one row per earned reset plus a
/// server-computed available count.
#[derive(Deserialize)]
struct ResetsResponse {
    #[serde(default)]
    credits: Vec<ResetsCreditEntry>,
    #[serde(default)]
    available_count: Option<u64>,
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

/// Pure mapping of a wham usage body into records (no network). The
/// per-credit resets payload (second endpoint) is optional: `None` keeps
/// the counts-only shape.
pub fn map_response_with_resets(
    usage: &Value,
    resets: Option<&Value>,
    fetched_at: DateTime<Utc>,
) -> Vec<ProviderRecord> {
    let parsed: WhamResponse = match serde_json::from_value(usage.clone()) {
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
            let label = wham_window_label(&w, fetched_at);
            let mut record = Record::new(
                PROVIDER_ID.to_string(),
                Kind::Window,
                Source::Api,
                fetched_at,
                None,
            );
            record.label = Some(label.to_string());
            record.display_name = Some(display_name.clone());
            record.used = Some(used);
            record.limit = Some(100.0);
            record.unit = Some("percent".to_string());
            record.resets_at = w.reset_at.and_then(|ts| DateTime::from_timestamp(ts, 0));
            // Earned resets describe the subscription as a whole; put them on
            // the PRIMARY window only. Null/missing available_count -> absent.
            // When the per-credit endpoint answered, its rows and count win;
            // wham/usage stays the source for `applicable`.
            record.reset_credits = with_per_credit_rows(
                reset_credits(&parsed.rate_limit_reset_credits),
                resets,
            );
            out.push(ok_record(record, None));
        }
    }

    if let Some(w) = rate_limit.secondary_window {
        if let Some(used) = w.used_percent {
            // Live-verified quirk: the weekly window can arrive as the
            // SECONDARY window with a far-out reset — label it the same way.
            let label = wham_window_label(&w, fetched_at);
            let mut record = Record::new(
                PROVIDER_ID.to_string(),
                Kind::Window,
                Source::Api,
                fetched_at,
                None,
            );
            record.label = Some(label.to_string());
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

/// Label one wham rate-limit window. Weekly when the declared size says so,
/// or when the reset is >30h away (a 5h window cannot reset that far out).
/// Live-verified: the API sometimes omits/shrinks `limit_window_seconds` and
/// has been seen delivering the weekly window as the secondary one.
fn wham_window_label(w: &WhamWindow, fetched_at: DateTime<Utc>) -> &'static str {
    let is_weekly = match w.limit_window_seconds {
        Some(secs) if secs >= 259_200 => true,
        _ => w
            .reset_at
            .and_then(|ts| DateTime::from_timestamp(ts, 0))
            .map(|t| (t - fetched_at).num_hours() > 30)
            .unwrap_or(false),
    };
    if is_weekly {
        "Weekly"
    } else {
        "5h window"
    }
}

/// Pure: map the wham `rate_limit_reset_credits` block into schema reset
/// credits. Absent block, or a null/missing `available_count`, yields `None`
/// (no earned-reset data is not the same as zero resets).
fn reset_credits(raw: &Option<WhamResetCredits>) -> Option<ResetCredits> {
    let raw = raw.as_ref()?;
    let available = raw.available_count?;
    Some(ResetCredits {
        available,
        applicable: raw.applicable_available_count,
        expires_at: None,
        credits: Vec::new(),
    })
}

/// Pure: map the reset-credits payload into the available count plus one row
/// per AVAILABLE credit (redeemed/redeem-started entries are excluded). The
/// count comes from `available_count`, falling back to counting the mapped
/// rows when the field is missing. `None` when the payload is unusable OR
/// carries no signal at all (no count and no entries) — an empty body must
/// not erase counts already known from wham/usage.
fn map_resets_payload(json: &Value) -> Option<(u64, Vec<ResetCredit>)> {
    let parsed: ResetsResponse = serde_json::from_value(json.clone()).ok()?;
    let credits: Vec<ResetCredit> = parsed
        .credits
        .iter()
        .filter(|c| c.status.as_deref() == Some("available"))
        .map(|c| ResetCredit {
            expires_at: c.expires_at.as_deref().and_then(parse_rfc3339),
            title: c.title.clone(),
        })
        .collect();
    if parsed.available_count.is_none() && credits.is_empty() {
        return None;
    }
    let available = parsed.available_count.unwrap_or(credits.len() as u64);
    Some((available, credits))
}

/// Pure: combine the counts-only wham credits with the per-credit resets
/// payload. A usable payload wins for `available` and adds the credit rows
/// (with the derived earliest expiry); wham/usage remains the source for
/// `applicable`. Without a usable payload the counts-only shape is returned
/// unchanged.
fn with_per_credit_rows(base: Option<ResetCredits>, resets: Option<&Value>) -> Option<ResetCredits> {
    let Some((available, credits)) = resets.and_then(map_resets_payload) else {
        return base;
    };
    let applicable = base.as_ref().and_then(|b| b.applicable);
    Some(ResetCredits::with_credits(available, applicable, credits))
}

/// RFC3339 UTC instant (e.g. "2026-09-20T23:34:55.784003Z"); unparsable
/// strings degrade to None rather than failing the whole payload.
fn parse_rfc3339(raw: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(raw).ok().map(|dt| dt.with_timezone(&Utc))
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

    fn ts(raw: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(raw).unwrap().with_timezone(&Utc)
    }

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
        let records = map_response_with_resets(&fixture, None, now);
        assert_eq!(records.len(), 1);
        let r = &records[0];
        assert_eq!(r.record.as_ref().unwrap().used, Some(35.0));
        assert_eq!(r.record.as_ref().unwrap().label.as_deref(), Some("Weekly"));
        assert_eq!(r.record.as_ref().unwrap().display_name.as_deref(), Some("ChatGPT Plus"));
    }

    #[test]
    fn weekly_labeled_by_reset_distance_when_size_missing() {
        let now = Utc::now();
        let reset_94h = (now + chrono::Duration::hours(94)).timestamp();
        let fixture = json!({
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 43.0,
                    "reset_at": reset_94h
                },
                "secondary_window": {
                    "used_percent": 0.0,
                    "reset_at": (now + chrono::Duration::hours(5)).timestamp()
                }
            }
        });
        let records = map_response_with_resets(&fixture, None, now);
        assert_eq!(records.len(), 2);
        let labels: Vec<&str> = records
            .iter()
            .map(|r| r.record.as_ref().unwrap().label.as_deref().unwrap())
            .collect();
        // The 94h-reset window is Weekly even without limit_window_seconds;
        // the 5h-reset one stays 5h.
        assert_eq!(labels, vec!["Weekly", "5h window"]);

        // Mirror case: weekly arriving as the SECONDARY window (seen live).
        let fixture_swapped = json!({
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 0.0,
                    "reset_at": (now + chrono::Duration::hours(5)).timestamp()
                },
                "secondary_window": {
                    "used_percent": 43.0,
                    "reset_at": reset_94h
                }
            }
        });
        let swapped = map_response_with_resets(&fixture_swapped, None, now);
        let labels: Vec<&str> = swapped
            .iter()
            .map(|r| r.record.as_ref().unwrap().label.as_deref().unwrap())
            .collect();
        assert_eq!(labels, vec!["5h window", "Weekly"]);
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

    #[test]
    fn reset_credits_mapped_onto_primary_window_only() {
        let fixture = json!({
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {"used_percent": 35.0, "reset_at": 1787815692},
                "secondary_window": {"used_percent": 0.0, "reset_at": 1787815692}
            },
            "rate_limit_reset_credits": {
                "available_count": 3,
                "applicable_available_count": 0
            }
        });
        let records = map_response_with_resets(&fixture, None, Utc::now());
        assert_eq!(records.len(), 2);
        let primary = records[0].record.as_ref().unwrap();
        let secondary = records[1].record.as_ref().unwrap();
        assert_eq!(
            primary.reset_credits,
            Some(ResetCredits {
                available: 3,
                applicable: Some(0),
                expires_at: None,
                credits: Vec::new()
            })
        );
        assert_eq!(secondary.reset_credits, None, "primary window only");
    }

    #[test]
    fn reset_credits_absent_or_null_means_none() {
        // Field completely absent.
        let fixture = json!({
            "rate_limit": {"primary_window": {"used_percent": 35.0}}
        });
        let records = map_response_with_resets(&fixture, None, Utc::now());
        assert_eq!(records[0].record.as_ref().unwrap().reset_credits, None);

        // Field present but null, and available_count null inside a block.
        let fixture_null = json!({
            "rate_limit": {"primary_window": {"used_percent": 35.0}},
            "rate_limit_reset_credits": null
        });
        let records = map_response_with_resets(&fixture_null, None, Utc::now());
        assert_eq!(records[0].record.as_ref().unwrap().reset_credits, None);

        let fixture_null_count = json!({
            "rate_limit": {"primary_window": {"used_percent": 35.0}},
            "rate_limit_reset_credits": {"available_count": null}
        });
        let records = map_response_with_resets(&fixture_null_count, None, Utc::now());
        assert_eq!(records[0].record.as_ref().unwrap().reset_credits, None);
    }

    #[test]
    fn reset_credits_available_without_applicable() {
        // Live shape sometimes only carries available_count.
        let fixture = json!({
            "rate_limit": {"primary_window": {"used_percent": 10.0}},
            "rate_limit_reset_credits": {"available_count": 1}
        });
        let records = map_response_with_resets(&fixture, None, Utc::now());
        assert_eq!(
            records[0].record.as_ref().unwrap().reset_credits,
            Some(ResetCredits {
                available: 1,
                applicable: None,
                expires_at: None,
                credits: Vec::new()
            })
        );
    }

    /// Live-verified shape of the wham rate-limit-reset-credits payload
    /// (fields shortened per entry; unknown fields included to prove they
    /// are ignored).
    fn resets_fixture() -> serde_json::Value {
        json!({
            "credits": [
                {"id": "RateLimitResetCredit_1", "reset_type": "codex_rate_limits",
                 "is_supported_by_plan": true, "status": "available",
                 "granted_at": "2026-08-21T23:34:55.784003Z",
                 "expires_at": "2026-09-20T23:34:55.784003Z",
                 "redeem_started_at": null, "redeemed_at": null,
                 "profile_user_id": "Codex Team",
                 "title": "Full reset (Weekly + 5 hr)",
                 "description": "Thanks for using Codex!"},
                {"id": "RateLimitResetCredit_2", "status": "available",
                 "expires_at": "2026-09-19T10:00:00Z",
                 "title": "Full reset (Weekly + 5 hr)"},
                {"id": "RateLimitResetCredit_3", "status": "available",
                 "expires_at": "2026-09-21T08:30:00Z",
                 "title": "Full reset (Weekly + 5 hr)"}
            ],
            "available_count": 3,
            "total_earned_count": 0,
            "immediate_reset_purchase_eligible": false,
            "history_enabled": false
        })
    }

    #[test]
    fn resets_payload_maps_three_credits_with_earliest_expiry() {
        let usage = json!({
            "plan_type": "plus",
            "rate_limit": {"primary_window": {"used_percent": 35.0, "reset_at": 1787815692}},
            "rate_limit_reset_credits": {
                "available_count": 3,
                "applicable_available_count": 0
            }
        });
        let records = map_response_with_resets(&usage, Some(&resets_fixture()), Utc::now());
        let primary = records[0].record.as_ref().unwrap();
        let rc = primary.reset_credits.as_ref().unwrap();
        assert_eq!(rc.available, 3, "available_count from the resets payload");
        assert_eq!(rc.applicable, Some(0), "applicable still from wham/usage");
        assert_eq!(rc.credits.len(), 3);
        assert_eq!(
            rc.credits[0].title.as_deref(),
            Some("Full reset (Weekly + 5 hr)")
        );
        assert_eq!(
            rc.credits[0].expires_at,
            Some(ts("2026-09-20T23:34:55.784003Z")),
            "RFC3339 with fractional seconds"
        );
        // Derived earliest expiry among the rows: 19 Sep beats 20/21 Sep.
        assert_eq!(rc.expires_at, Some(ts("2026-09-19T10:00:00Z")));
    }

    #[test]
    fn resets_payload_excludes_redeemed_entries() {
        let resets = json!({
            "credits": [
                {"status": "available", "expires_at": "2026-09-20T23:34:55Z", "title": "Full reset"},
                {"status": "redeemed", "expires_at": "2026-09-01T00:00:00Z", "title": "Spent one"},
                {"status": "available", "expires_at": "2026-09-25T00:00:00Z", "title": "Full reset"}
            ],
            "available_count": 2
        });
        let usage = json!({
            "rate_limit": {"primary_window": {"used_percent": 10.0}},
            "rate_limit_reset_credits": {"available_count": 2}
        });
        let records = map_response_with_resets(&usage, Some(&resets), Utc::now());
        let rc = records[0].record.as_ref().unwrap().reset_credits.as_ref().unwrap();
        assert_eq!(rc.available, 2);
        assert_eq!(rc.credits.len(), 2, "redeemed entry never becomes a row");
        assert!(rc
            .credits
            .iter()
            .all(|c| c.expires_at != Some(ts("2026-09-01T00:00:00Z"))));
    }

    #[test]
    fn resets_failure_or_garbage_falls_back_to_counts_only() {
        let usage = json!({
            "rate_limit": {"primary_window": {"used_percent": 35.0}},
            "rate_limit_reset_credits": {"available_count": 3, "applicable_available_count": 0}
        });

        // Endpoint failed entirely (None payload): identical credits shape.
        let degraded = map_response_with_resets(&usage, None, Utc::now());
        assert_eq!(
            degraded[0].record.as_ref().unwrap().reset_credits,
            map_response_with_resets(&usage, None, Utc::now())[0].record.as_ref().unwrap().reset_credits
        );
        let rc = degraded[0].record.as_ref().unwrap().reset_credits.as_ref().unwrap();
        assert_eq!(rc.available, 3);
        assert!(rc.credits.is_empty(), "counts-only shape kept");

        // Garbage payload (fails to parse): same graceful degradation.
        let garbage = json!("not the resets payload");
        let degraded = map_response_with_resets(&usage, Some(&garbage), Utc::now());
        assert_eq!(degraded[0].record.as_ref().unwrap().reset_credits, Some(ResetCredits {
            available: 3,
            applicable: Some(0),
            expires_at: None,
            credits: Vec::new(),
        }));

        // Signal-free payload ({}): must not erase known counts either.
        let empty = json!({});
        let degraded = map_response_with_resets(&usage, Some(&empty), Utc::now());
        assert_eq!(degraded[0].record.as_ref().unwrap().reset_credits.as_ref().unwrap().available, 3);
    }

    #[test]
    fn resets_missing_available_count_falls_back_to_counting() {
        let resets = json!({
            "credits": [
                {"status": "available", "expires_at": "2026-09-20T23:34:55Z"},
                {"status": "redeemed", "expires_at": "2026-09-01T00:00:00Z"},
                {"status": "available", "expires_at": "2026-09-25T00:00:00Z"}
            ]
        });
        // No wham block at all: the resets payload alone still yields credits,
        // counted from the available entries (2), applicable unknown.
        let usage = json!({
            "rate_limit": {"primary_window": {"used_percent": 10.0}}
        });
        let records = map_response_with_resets(&usage, Some(&resets), Utc::now());
        let rc = records[0].record.as_ref().unwrap().reset_credits.as_ref().unwrap();
        assert_eq!(rc.available, 2, "counted from available entries");
        assert_eq!(rc.applicable, None);
        assert_eq!(rc.credits.len(), 2);
        assert_eq!(rc.expires_at, Some(ts("2026-09-20T23:34:55Z")));
    }
}
