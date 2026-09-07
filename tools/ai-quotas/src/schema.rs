//! Record types for quota state files.
//!
//! One JSON file per record; see `Record` for the schema. Parsing is lenient:
//! unknown fields are ignored and required-field violations surface as normal
//! serde errors so the reader can report them as `Error` states.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Quota semantics of a record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Window,
    Daily,
    Balance,
}

impl Kind {
    /// Parse a CLI `--kind` value (case-insensitive).
    pub fn parse(s: &str) -> Result<Kind, String> {
        match s.to_ascii_lowercase().as_str() {
            "window" => Ok(Kind::Window),
            "daily" => Ok(Kind::Daily),
            "balance" => Ok(Kind::Balance),
            other => Err(format!(
                "invalid kind '{other}' (expected window, daily or balance)"
            )),
        }
    }
}

/// Where the record data came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Source {
    #[serde(rename = "manual")]
    Manual,
    #[serde(rename = "local-log")]
    LocalLog,
    #[serde(rename = "api")]
    Api,
}

impl Source {
    /// Parse a CLI `--source` value (case-insensitive).
    pub fn parse(s: &str) -> Result<Source, String> {
        match s.to_ascii_lowercase().as_str() {
            "manual" => Ok(Source::Manual),
            "local-log" => Ok(Source::LocalLog),
            "api" => Ok(Source::Api),
            other => Err(format!(
                "invalid source '{other}' (expected manual, local-log or api)"
            )),
        }
    }
}

/// Default TTL (seconds) applied when a record carries no `ttl_seconds`.
pub fn default_ttl(source: Source) -> u64 {
    match source {
        Source::Manual => 21_600,
        Source::LocalLog => 600,
        Source::Api => 300,
    }
}

/// One earned reset credit with its own expiry. Providers map each entry of
/// their per-credit endpoints into this row; unknown/irrelevant fields are
/// dropped at the mapping layer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ResetCredit {
    /// When this credit expires and becomes unusable, when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<DateTime<Utc>>,
    /// Provider display title, when given (e.g. "Full reset (Weekly + 5 hr)").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
}

/// Earned "usage limit resets" surfaced by a provider: one-shot credits that
/// restore a capped window when spent. Optional extras are omitted from the
/// JSON entirely when absent, so old state files and readers stay compatible.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ResetCredits {
    /// Number of earned resets currently usable.
    pub available: u64,
    /// Resets applicable to the current (active) cap, when the provider says.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub applicable: Option<u64>,
    /// Earliest expiry among the available resets, when known. Derived from
    /// the per-credit rows via [`ResetCredits::with_credits`].
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<DateTime<Utc>>,
    /// One row per available credit, each with its own expiry. Old payloads
    /// predate this list: absent keys parse as empty and empty lists are
    /// omitted from serialized JSON.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub credits: Vec<ResetCredit>,
}

impl ResetCredits {
    /// Build reset credits from a per-credit list, deriving `expires_at` as
    /// the earliest credit expiry (None when no credit carries one) so every
    /// provider stays consistent.
    pub fn with_credits(available: u64, applicable: Option<u64>, credits: Vec<ResetCredit>) -> Self {
        Self {
            available,
            applicable,
            expires_at: credits.iter().filter_map(|c| c.expires_at).min(),
            credits,
        }
    }
}

/// A single quota record.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Record {
    pub provider: String,
    pub kind: Kind,
    pub used: Option<f64>,
    pub limit: Option<f64>,
    pub unit: Option<String>,
    pub label: Option<String>,
    pub display_name: Option<String>,
    pub currency: Option<String>,
    pub resets_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reset_credits: Option<ResetCredits>,
    pub fetched_at: DateTime<Utc>,
    pub ttl_seconds: Option<u64>,
    pub source: Source,
}

impl Record {
    /// Build a record with all optional fields empty. When `ttl_seconds` is
    /// `None` the default for `source` is applied.
    pub fn new(
        provider: String,
        kind: Kind,
        source: Source,
        fetched_at: DateTime<Utc>,
        ttl_seconds: Option<u64>,
    ) -> Self {
        Self {
            provider,
            kind,
            source,
            fetched_at,
            ttl_seconds: Some(ttl_seconds.unwrap_or_else(|| default_ttl(source))),
            used: None,
            limit: None,
            unit: None,
            label: None,
            display_name: None,
            currency: None,
            resets_at: None,
            reset_credits: None,
        }
    }

    /// Semantic validation. Only fatal rule: `Balance` records must not carry
    /// `resets_at` (a balance does not reset). Everything else is tolerated.
    pub fn validate(&self) -> Result<(), String> {
        if self.kind == Kind::Balance && self.resets_at.is_some() {
            return Err(format!(
                "balance record for provider '{}' must not set resets_at",
                self.provider
            ));
        }
        Ok(())
    }

    /// TTL to use for freshness checks; falls back to the source default.
    pub fn effective_ttl(&self) -> u64 {
        self.ttl_seconds.unwrap_or_else(|| default_ttl(self.source))
    }

    /// `used / limit * 100` when both are set, the limit is positive and the
    /// kind is not `Balance`; otherwise `None`.
    pub fn used_percent(&self) -> Option<f64> {
        if self.kind == Kind::Balance {
            return None;
        }
        match (self.used, self.limit) {
            (Some(used), Some(limit)) if limit > 0.0 => Some(used / limit * 100.0),
            _ => None,
        }
    }
}

impl<'de> Deserialize<'de> for Record {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        // Mirror struct: identical to Record but plain-derived, so deserialization
        // can normalize ttl_seconds with the per-source default before returning.
        #[derive(Deserialize)]
        struct Raw {
            provider: String,
            kind: Kind,
            #[serde(default)]
            used: Option<f64>,
            #[serde(default)]
            limit: Option<f64>,
            #[serde(default)]
            unit: Option<String>,
            #[serde(default)]
            label: Option<String>,
            #[serde(default)]
            display_name: Option<String>,
            #[serde(default)]
            currency: Option<String>,
            #[serde(default)]
            resets_at: Option<DateTime<Utc>>,
            #[serde(default)]
            reset_credits: Option<ResetCredits>,
            fetched_at: DateTime<Utc>,
            #[serde(default)]
            ttl_seconds: Option<u64>,
            source: Source,
        }

        let raw = Raw::deserialize(deserializer)?;
        Ok(Record {
            provider: raw.provider,
            kind: raw.kind,
            used: raw.used,
            limit: raw.limit,
            unit: raw.unit,
            label: raw.label,
            display_name: raw.display_name,
            currency: raw.currency,
            resets_at: raw.resets_at,
            reset_credits: raw.reset_credits,
            fetched_at: raw.fetched_at,
            ttl_seconds: Some(raw.ttl_seconds.unwrap_or_else(|| default_ttl(raw.source))),
            source: raw.source,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"{"provider":"claude","kind":"window","used":62,"limit":100,"unit":"requests","label":"5h window","display_name":"Claude Pro","currency":null,"resets_at":"2026-08-20T18:30:00Z","fetched_at":"2026-08-20T14:05:12Z","ttl_seconds":600,"source":"local-log"}"#;

    fn minimal_json(kind: &str, source: &str) -> String {
        format!(
            r#"{{"provider":"test","kind":"{kind}","source":"{source}","fetched_at":"2026-08-20T14:05:12Z"}}"#
        )
    }

    #[test]
    fn parses_all_three_kinds() {
        let cases = [
            ("window", Kind::Window),
            ("daily", Kind::Daily),
            ("balance", Kind::Balance),
        ];
        for (raw, expected) in cases {
            let r: Record = serde_json::from_str(&minimal_json(raw, "manual")).unwrap();
            assert_eq!(r.kind, expected, "kind string {raw}");
        }
    }

    #[test]
    fn parses_full_sample() {
        let r: Record = serde_json::from_str(SAMPLE).unwrap();
        assert_eq!(r.provider, "claude");
        assert_eq!(r.used, Some(62.0));
        assert_eq!(r.limit, Some(100.0));
        assert_eq!(r.unit.as_deref(), Some("requests"));
        assert_eq!(r.label.as_deref(), Some("5h window"));
        assert_eq!(r.display_name.as_deref(), Some("Claude Pro"));
        assert_eq!(r.currency, None);
        assert!(r.resets_at.is_some());
        assert_eq!(r.source, Source::LocalLog);
        assert_eq!(r.ttl_seconds, Some(600));
    }

    #[test]
    fn ttl_defaults_applied_per_source_on_parse() {
        let r: Record = serde_json::from_str(&minimal_json("window", "manual")).unwrap();
        assert_eq!(r.ttl_seconds, Some(21_600));
        let r: Record = serde_json::from_str(&minimal_json("daily", "local-log")).unwrap();
        assert_eq!(r.ttl_seconds, Some(600));
        let r: Record = serde_json::from_str(&minimal_json("balance", "api")).unwrap();
        assert_eq!(r.ttl_seconds, Some(300));
    }

    #[test]
    fn explicit_ttl_preserved_on_parse() {
        let json = r#"{"provider":"p","kind":"daily","source":"api","fetched_at":"2026-08-20T14:05:12Z","ttl_seconds":1234}"#;
        let r: Record = serde_json::from_str(json).unwrap();
        assert_eq!(r.ttl_seconds, Some(1234));
    }

    #[test]
    fn constructor_applies_ttl_default_when_none() {
        let r = Record::new("p".into(), Kind::Daily, Source::Api, Utc::now(), None);
        assert_eq!(r.ttl_seconds, Some(300));
        let r = Record::new("p".into(), Kind::Daily, Source::Api, Utc::now(), Some(1200));
        assert_eq!(r.ttl_seconds, Some(1200));
    }

    #[test]
    fn unknown_json_fields_ignored() {
        let json = r#"{"provider":"p","kind":"daily","source":"api","fetched_at":"2026-08-20T14:05:12Z","zzz":{"nested":[1,2]},"future_field":true}"#;
        assert!(serde_json::from_str::<Record>(json).is_ok());
    }

    #[test]
    fn invalid_kind_string_is_parse_error() {
        let json = r#"{"provider":"p","kind":"weekly","source":"api","fetched_at":"2026-08-20T14:05:12Z"}"#;
        assert!(serde_json::from_str::<Record>(json).is_err());
    }

    #[test]
    fn missing_required_field_is_parse_error() {
        // No fetched_at.
        let json = r#"{"provider":"p","kind":"daily","source":"api"}"#;
        assert!(serde_json::from_str::<Record>(json).is_err());
        // No kind.
        let json = r#"{"provider":"p","source":"api","fetched_at":"2026-08-20T14:05:12Z"}"#;
        assert!(serde_json::from_str::<Record>(json).is_err());
    }

    #[test]
    fn balance_validation() {
        let mut r = Record::new("p".into(), Kind::Balance, Source::Api, Utc::now(), None);
        assert!(r.validate().is_ok());
        r.resets_at = Some(Utc::now());
        assert!(r.validate().is_err());

        let mut w = Record::new("p".into(), Kind::Window, Source::Api, Utc::now(), None);
        w.resets_at = Some(Utc::now());
        assert!(w.validate().is_ok()); // ignored for non-balance kinds
    }

    #[test]
    fn used_percent_normal() {
        let mut r = Record::new("p".into(), Kind::Window, Source::Manual, Utc::now(), None);
        r.used = Some(62.0);
        r.limit = Some(100.0);
        let pct = r.used_percent().unwrap();
        assert!((pct - 62.0).abs() < 1e-9);
    }

    #[test]
    fn used_percent_zero_limit_is_none() {
        let mut r = Record::new("p".into(), Kind::Window, Source::Manual, Utc::now(), None);
        r.used = Some(5.0);
        r.limit = Some(0.0);
        assert!(r.used_percent().is_none());
    }

    #[test]
    fn used_percent_balance_is_none() {
        let mut r = Record::new("p".into(), Kind::Balance, Source::Manual, Utc::now(), None);
        r.used = Some(5.0);
        r.limit = Some(10.0);
        assert!(r.used_percent().is_none());
    }

    #[test]
    fn used_percent_missing_values_are_none() {
        let mut r = Record::new("p".into(), Kind::Daily, Source::Manual, Utc::now(), None);
        r.limit = Some(10.0);
        assert!(r.used_percent().is_none());
        r.used = Some(3.0);
        r.limit = None;
        assert!(r.used_percent().is_none());
    }

    #[test]
    fn reset_credits_default_to_none_and_old_files_parse() {
        // Legacy state file without the field: fully backward compatible.
        let r: Record = serde_json::from_str(SAMPLE).unwrap();
        assert_eq!(r.reset_credits, None);

        let r = Record::new("p".into(), Kind::Window, Source::Api, Utc::now(), None);
        assert_eq!(r.reset_credits, None);
    }

    #[test]
    fn reset_credits_round_trip_and_skip_absent_extras() {
        let mut r = Record::new("p".into(), Kind::Window, Source::Api, Utc::now(), None);
        r.reset_credits = Some(ResetCredits {
            available: 3,
            applicable: None,
            expires_at: None,
            credits: Vec::new(),
        });
        let json = serde_json::to_value(&r).unwrap();
        assert_eq!(json["reset_credits"]["available"], 3);
        assert!(json["reset_credits"].get("applicable").is_none());
        assert!(json["reset_credits"].get("expires_at").is_none());
        assert!(json["reset_credits"].get("credits").is_none());

        let back: Record = serde_json::from_value(json).unwrap();
        assert_eq!(back, r);
    }

    #[test]
    fn reset_credits_legacy_json_without_credits_parses() {
        // State written before per-credit rows existed: the missing key
        // deserializes to an empty list and serializes back omitted.
        let legacy = r#"{"available":2,"applicable":1,"expires_at":"2026-09-20T23:34:55Z"}"#;
        let credits: ResetCredits = serde_json::from_str(legacy).unwrap();
        assert_eq!(credits.available, 2);
        assert!(credits.credits.is_empty());
        let json = serde_json::to_value(&credits).unwrap();
        assert!(json.get("credits").is_none(), "empty credits key omitted");
    }

    #[test]
    fn with_credits_derives_earliest_expiry() {
        let credit = |days: i64| ResetCredit {
            expires_at: Some(Utc::now() + chrono::Duration::days(days)),
            title: Some(format!("reset +{days}d")),
        };
        let earliest = credit(5);
        let credits = ResetCredits::with_credits(2, Some(1), vec![credit(30), earliest.clone(), credit(12)]);
        assert_eq!(credits.available, 2);
        assert_eq!(credits.applicable, Some(1));
        assert_eq!(credits.expires_at, earliest.expires_at, "earliest wins");

        // No credit carries an expiry -> no derived one either.
        let plain = ResetCredits::with_credits(1, None, vec![ResetCredit::default()]);
        assert_eq!(plain.expires_at, None);

        // Empty list behaves like the counts-only shape.
        let empty = ResetCredits::with_credits(0, None, Vec::new());
        assert_eq!(empty.expires_at, None);
        assert!(empty.credits.is_empty());
    }

    #[test]
    fn reset_credits_with_credits_round_trips() {
        let credits = ResetCredits::with_credits(
            1,
            None,
            vec![ResetCredit {
                expires_at: Some(Utc::now()),
                title: Some("Full reset (Weekly + 5 hr)".into()),
            }],
        );
        let json = serde_json::to_value(&credits).unwrap();
        assert_eq!(json["credits"].as_array().unwrap().len(), 1);
        assert_eq!(json["credits"][0]["title"], "Full reset (Weekly + 5 hr)");
        let back: ResetCredits = serde_json::from_value(json).unwrap();
        assert_eq!(back, credits);
    }

    #[test]
    fn reset_credits_absent_key_skipped_on_serialize() {
        let r = Record::new("p".into(), Kind::Window, Source::Api, Utc::now(), None);
        let json = serde_json::to_value(&r).unwrap();
        assert!(json.get("reset_credits").is_none(), "no key when None");
    }
}
