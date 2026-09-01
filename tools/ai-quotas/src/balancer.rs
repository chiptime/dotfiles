//! Tier-safe balancer policy: pure scoring over merged quota statuses.
//! The server route (Phase 2) feeds `reader::merge_statuses` into `advise`; no I/O here.

#![allow(dead_code)] // consumed by the /api/balancer/recommend route in Phase 2

use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::reader::{State, Status};

/// Schema tag stamped on every advice payload (R1).
pub const ADVICE_SCHEMA: &str = "ai-quotas/balancer-advice@1";

/// `reason` domain of the advice payload.
pub mod reason {
    pub const CONFIG_MISSING: &str = "config_missing";
    pub const TIER_UNKNOWN: &str = "tier_unknown";
    pub const INSUFFICIENT_DATA: &str = "insufficient_data";
    pub const NO_ELIGIBLE_CANDIDATE: &str = "no_eligible_candidate";
    pub const REQUESTED_BELOW_FLOOR: &str = "requested_below_floor";
    pub const REQUESTED_HEALTHY: &str = "requested_healthy";
}

/// Parsed `balancer.json`; defaults mirror the shipped template (R9).
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub priority: Vec<String>,
    #[serde(default = "default_floor")]
    pub floor: f64,
    #[serde(default = "default_margin")]
    pub margin: f64,
    /// Tier name -> model id -> quota provider id.
    #[serde(default)]
    pub tiers: BTreeMap<String, BTreeMap<String, String>>,
    #[serde(default = "default_notice_interval")]
    pub notice_interval: u64,
}

fn default_floor() -> f64 { 0.10 }
fn default_margin() -> f64 { 0.10 }
fn default_notice_interval() -> u64 { 900 }

impl Config {
    /// Name of the tier containing `model`, if any (R3).
    pub fn tier_of(&self, model: &str) -> Option<&str> {
        self.tiers.iter().find(|(_, models)| models.contains_key(model)).map(|(t, _)| t.as_str())
    }
}

/// Resolve the config path: `AI_QUOTAS_BALANCER_CONFIG` (non-empty), else `~/.config/ai-stack/balancer.json` (R9).
pub fn config_path() -> PathBuf {
    if let Some(path) = std::env::var_os("AI_QUOTAS_BALANCER_CONFIG").filter(|p| !p.is_empty()) {
        return PathBuf::from(path);
    }
    match std::env::var_os("HOME").filter(|h| !h.is_empty()) {
        Some(home) => PathBuf::from(home).join(".config").join("ai-stack").join("balancer.json"),
        _ => PathBuf::from(".balancer.json"),
    }
}

/// Load the config; absent or unparseable yields `None` so advice fails open (R9).
pub fn load_config() -> Option<Config> {
    let bytes = std::fs::read(config_path()).ok()?;
    serde_json::from_slice(&bytes).ok()
}

/// One advisory payload (R1). `*_remaining` are additive: present only when computable.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Advice {
    pub schema: &'static str,
    pub requested_model: String,
    pub tier: String,
    pub recommended_model: String,
    pub switch: bool,
    pub reason: &'static str,
    pub advice_age_seconds: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requested_remaining: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recommended_remaining: Option<f64>,
}

/// Pure advisory scoring (R1-R4, R9). `statuses` is the merged quota truth.
/// Deterministic: highest remaining wins; ties break by `priority[]`, then
/// model id. Switch only when the requested provider is confirmed below
/// `floor + margin` and an eligible same-tier peer exists.
pub fn advise(config: Option<&Config>, requested_model: &str, statuses: &[Status]) -> Advice {
    // Worst-case freshness: the advice is as old as its oldest input record.
    let age = statuses.iter().map(|s| s.age_seconds.unwrap_or(0)).max().unwrap_or(0);
    let Some(config) = config else { return keep(requested_model, "", reason::CONFIG_MISSING, None, age) };
    let Some(tier) = config.tier_of(requested_model) else { return keep(requested_model, "", reason::TIER_UNKNOWN, None, age) };
    let models = &config.tiers[tier];
    let threshold = config.floor + config.margin;
    let tier_has_data = models.values().any(|p| statuses.iter().any(|s| s.provider == *p));
    if !tier_has_data {
        return keep(requested_model, tier, reason::INSUFFICIENT_DATA, None, age);
    }
    // Only an all-Ok requested provider with a computable fraction can be
    // judged; anything else is insufficient data (fail open, no switch).
    let requested = models.get(requested_model).map(|p| provider_view(statuses, p));
    let requested_remaining = requested.as_ref().filter(|v| v.all_ok).and_then(|v| v.remaining);
    match requested_remaining {
        Some(r) if r >= threshold => return keep(requested_model, tier, reason::REQUESTED_HEALTHY, Some(r), age),
        // Confirmed below floor: look for an eligible peer.
        Some(_) => {}
        None => return keep(requested_model, tier, reason::INSUFFICIENT_DATA, None, age),
    }
    let priority_of = |p: &str| config.priority.iter().position(|x| x == p).unwrap_or(usize::MAX);
    let mut best: Option<(&String, &String, f64)> = None;
    for (model, provider) in models {
        if model.as_str() == requested_model { continue; }
        let view = provider_view(statuses, provider);
        if !view.present || !view.all_ok { continue; }
        let Some(r) = view.remaining else { continue };
        if r < threshold { continue; }
        let wins = match best {
            // BTreeMap order keeps (remaining, priority) ties deterministic.
            None => true,
            Some((_, bp, br)) => r > br || (r == br && priority_of(provider) < priority_of(bp)),
        };
        if wins { best = Some((model, provider, r)); }
    }
    match best {
        Some((model, _, r)) => Advice {
            schema: ADVICE_SCHEMA,
            requested_model: requested_model.to_string(),
            tier: tier.to_string(),
            recommended_model: model.clone(),
            switch: true,
            reason: reason::REQUESTED_BELOW_FLOOR,
            advice_age_seconds: age,
            requested_remaining,
            recommended_remaining: Some(r),
        },
        None => keep(requested_model, tier, reason::NO_ELIGIBLE_CANDIDATE, requested_remaining, age),
    }
}

/// Build the no-switch shape: keep the requested model, mirror its fraction.
fn keep(requested_model: &str, tier: &str, reason: &'static str, remaining: Option<f64>, age: u64) -> Advice {
    Advice {
        schema: ADVICE_SCHEMA,
        requested_model: requested_model.to_string(),
        tier: tier.to_string(),
        recommended_model: requested_model.to_string(),
        switch: false,
        reason,
        advice_age_seconds: age,
        requested_remaining: remaining,
        recommended_remaining: remaining,
    }
}

/// One provider's aggregated view across its statuses. `all_ok` enforces R2
/// (any non-Ok record drops the provider); `remaining` is the minimum
/// fraction across records that carry one (R2).
struct ProviderView {
    present: bool,
    all_ok: bool,
    remaining: Option<f64>,
}

fn provider_view(statuses: &[Status], provider: &str) -> ProviderView {
    let mut view = ProviderView { present: false, all_ok: true, remaining: None };
    for status in statuses.iter().filter(|s| s.provider == provider) {
        view.present = true;
        if status.state != State::Ok { view.all_ok = false; }
        if let Some(r) = record_remaining(status) {
            view.remaining = Some(view.remaining.map_or(r, |min| min.min(r)));
        }
    }
    view
}

/// `(limit - used) / limit` when both are set and the limit is positive, clamped to `[0, 1]`.
fn record_remaining(status: &Status) -> Option<f64> {
    let record = status.record.as_ref()?;
    match (record.used, record.limit) {
        (Some(used), Some(limit)) if limit > 0.0 => Some(((limit - used) / limit).clamp(0.0, 1.0)),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{Kind, Record, Source};
    use chrono::{TimeZone, Utc};

    /// Ok status at `remaining` fraction (limit 100), zero age.
    fn ok(provider: &str, kind: Kind, remaining: f64) -> Status {
        let mut record = Record::new(provider.into(), kind, Source::Manual, Utc.timestamp_opt(0, 0).unwrap(), Some(600));
        record.used = Some(100.0 * (1.0 - remaining));
        record.limit = Some(100.0);
        Status { provider: provider.into(), state: State::Ok, record: Some(record), age_seconds: Some(0), detail: None, display_name: None }
    }

    /// The shipped template is a valid config — policy tests score it (R9).
    fn cfg() -> Config {
        serde_json::from_str(include_str!("../config/balancer.example.json")).unwrap()
    }

    /// chatgpt-backed requested model, driven below floor by most tests.
    const REQ: &str = "openai/gpt-5.2";

    #[test]
    fn missing_config_and_empty_state_fail_open() {
        let advice = advise(None, REQ, &[]);
        assert!(!advice.switch);
        assert_eq!(advice.reason, reason::CONFIG_MISSING);
        assert_eq!(advice.recommended_model, REQ);
        let empty = advise(Some(&cfg()), REQ, &[]);
        assert!(!empty.switch);
        assert_eq!(empty.reason, reason::INSUFFICIENT_DATA);
    }

    #[test]
    fn unmapped_model_is_tier_unknown_without_remaining_fields() {
        let advice = advise(Some(&cfg()), "grok/robin-4", &[]);
        assert!(!advice.switch);
        assert_eq!(advice.reason, reason::TIER_UNKNOWN);
        assert_eq!(advice.recommended_model, "grok/robin-4");
        let json = serde_json::to_value(&advice).unwrap();
        assert!(json.get("requested_remaining").is_none());
        assert!(json.get("recommended_remaining").is_none());
    }

    #[test]
    fn eligible_peer_recommended_with_remaining_fractions() {
        let advice = advise(Some(&cfg()), REQ, &[ok("chatgpt", Kind::Window, 0.05), ok("claude", Kind::Window, 0.85)]);
        assert!(advice.switch);
        assert_eq!(advice.reason, reason::REQUESTED_BELOW_FLOOR);
        assert_eq!(advice.recommended_model, "anthropic/claude-opus-5");
        assert_eq!(advice.tier, "frontier");
        let json = serde_json::to_value(&advice).unwrap();
        assert_eq!(json["schema"], "ai-quotas/balancer-advice@1");
        assert!((json["requested_remaining"].as_f64().unwrap() - 0.05).abs() < 1e-9);
        assert!((json["recommended_remaining"].as_f64().unwrap() - 0.85).abs() < 1e-9);
    }

    #[test]
    fn provider_remaining_is_min_across_kinds() {
        // claude's daily 0.90 cannot rescue its window 0.02 (< 0.20 floor):
        // zai wins at 0.50 even though claude's daily alone looks healthier.
        let statuses = [ok("chatgpt", Kind::Window, 0.05), ok("claude", Kind::Window, 0.02), ok("claude", Kind::Daily, 0.90), ok("zai", Kind::Window, 0.50)];
        let advice = advise(Some(&cfg()), REQ, &statuses);
        assert!(advice.switch);
        assert_eq!(advice.recommended_model, "zai/glm-4.7");
    }

    #[test]
    fn non_ok_or_low_peers_yield_no_eligible_candidate() {
        let broken = Status { state: State::Error, record: None, ..ok("claude", Kind::Window, 0.0) };
        let advice = advise(Some(&cfg()), REQ, &[ok("chatgpt", Kind::Window, 0.05), broken, ok("zai", Kind::Window, 0.10)]);
        assert!(!advice.switch);
        assert_eq!(advice.reason, reason::NO_ELIGIBLE_CANDIDATE);
        assert_eq!(advice.recommended_model, REQ);
    }

    #[test]
    fn priority_breaks_remaining_tie() {
        let advice = advise(Some(&cfg()), REQ, &[ok("chatgpt", Kind::Window, 0.05), ok("zai", Kind::Window, 0.80), ok("claude", Kind::Window, 0.80)]);
        assert!(advice.switch);
        assert_eq!(advice.recommended_model, "anthropic/claude-opus-5");
    }

    #[test]
    fn healthy_requested_is_kept_even_when_peer_is_higher() {
        let advice = advise(Some(&cfg()), "anthropic/claude-opus-5", &[ok("claude", Kind::Window, 0.30), ok("zai", Kind::Window, 0.85)]);
        assert!(!advice.switch);
        assert_eq!(advice.reason, reason::REQUESTED_HEALTHY);
        assert_eq!(advice.recommended_model, "anthropic/claude-opus-5");
    }
}
