//! Provider incident polling from statuspage.io-style status pages.
//!
//! Polls `<base>/api/v2/summary.json` for each mapped provider, derives an
//! overall severity from the worst active component, and caches results with
//! the same TTL / stale-on-failure pattern as the quota `CachedSource`.
//! Adding a provider is one row in [`STATUS_PAGES`]: the poller, `/api/status`
//! and `check --json` all pick it up automatically.

use std::collections::BTreeMap;
use std::sync::RwLock;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::providers::{fetch_with_retry, http_agent, CACHE_TTL};

/// Provider id -> status page base URL. One row covers a new provider end to
/// end (polling, `/api/status`, `check --json` fields, dashboard badges).
const STATUS_PAGES: &[(&str, &str)] = &[
    ("claude", "https://status.claude.com"),
    ("chatgpt", "https://status.openai.com"),
];

/// Incident severity, ordered calmest -> worst (`Ord` drives the
/// worst-active-component rollup). Maintenance ranks below degradation: it
/// is informational, not an outage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Severity {
    None,
    Maintenance,
    Minor,
    Major,
    Critical,
}

impl Severity {
    /// Wire/API spelling (statuspage indicator vocabulary).
    pub fn as_str(self) -> &'static str {
        match self {
            Severity::None => "none",
            Severity::Maintenance => "maintenance",
            Severity::Minor => "minor",
            Severity::Major => "major",
            Severity::Critical => "critical",
        }
    }

    /// Parse a statuspage TOP-LEVEL indicator string ("none".."critical").
    /// Same severity scale as components, different wire words — components
    /// use [`map_component_status`], page summaries use this.
    pub fn from_indicator(raw: &str) -> Option<Severity> {
        match raw {
            "none" => Some(Severity::None),
            "maintenance" => Some(Severity::Maintenance),
            "minor" => Some(Severity::Minor),
            "major" => Some(Severity::Major),
            "critical" => Some(Severity::Critical),
            _ => None,
        }
    }
}

/// Map a statuspage component status string exactly. `None` means unknown:
/// callers treat it conservatively as calm but keep the raw string in detail.
pub fn map_component_status(raw: &str) -> Option<Severity> {
    match raw {
        "operational" => Some(Severity::None),
        "under_maintenance" => Some(Severity::Maintenance),
        "degraded_performance" => Some(Severity::Minor),
        "partial_outage" => Some(Severity::Major),
        "major_outage" | "full_outage" => Some(Severity::Critical),
        _ => None,
    }
}

/// One provider's incident snapshot, as served by `/api/status` and read by
/// `check --json`.
#[derive(Debug, Clone, PartialEq)]
pub struct ProviderStatus {
    /// Worst active component severity. `None` (the Option) only when the
    /// page could never be polled successfully — serialized as null.
    pub indicator: Option<Severity>,
    /// Human summary from the page (its overall status description).
    pub summary: String,
    /// Fetch time of this snapshot (the original fetch time when stale).
    pub updated_at: DateTime<Utc>,
    /// True when this is preserved last-good data after a failed refresh.
    pub stale: bool,
    /// Description of the last fetch failure, when any.
    pub error: Option<String>,
    /// Raw unknown component status strings: conservative severity, full data.
    pub detail: Option<String>,
}

impl ProviderStatus {
    /// Serialize for `/api/status` and `check --json` consumers. `error` and
    /// `detail` keys ride along only when present; `indicator` is null when
    /// the provider was never polled successfully.
    pub fn to_json(&self) -> Value {
        let mut obj = Map::new();
        obj.insert(
            "indicator".into(),
            json!(self.indicator.map(|sev| sev.as_str())),
        );
        obj.insert("summary".into(), json!(self.summary));
        obj.insert("updated_at".into(), json!(self.updated_at.to_rfc3339()));
        obj.insert("stale".into(), json!(self.stale));
        if let Some(error) = &self.error {
            obj.insert("error".into(), json!(error));
        }
        if let Some(detail) = &self.detail {
            obj.insert("detail".into(), json!(detail));
        }
        Value::Object(obj)
    }
}

/// `(status_indicator, status_summary)` pair for `check --json`: both null
/// unless the provider was polled successfully (stale last-good data still
/// counts — it is real data, just old).
pub fn check_fields(snapshot: Option<&ProviderStatus>) -> (Value, Value) {
    match snapshot {
        Some(s) if s.indicator.is_some() => (
            json!(s.indicator.map(|sev| sev.as_str())),
            json!(s.summary),
        ),
        _ => (Value::Null, Value::Null),
    }
}

/// `summary.json` payload (only the fields we consume; the rest is ignored).
#[derive(Deserialize)]
struct SummaryPage {
    #[serde(default)]
    status: Option<PageStatus>,
    #[serde(default)]
    components: Vec<PageComponent>,
}

#[derive(Deserialize)]
struct PageStatus {
    indicator: Option<String>,
    description: Option<String>,
}

#[derive(Deserialize)]
struct PageComponent {
    name: Option<String>,
    status: Option<String>,
}

/// Pure: map a summary.json body into a snapshot. Overall severity is the
/// worst active component status (`operational` components are ignored);
/// with no ranked component, the page's top-level indicator is the fallback.
/// Unknown component status strings never affect severity but are preserved
/// in `detail`.
pub fn map_summary(body: &str, fetched_at: DateTime<Utc>) -> Result<ProviderStatus, String> {
    let page: SummaryPage =
        serde_json::from_str(body).map_err(|e| format!("invalid summary.json: {e}"))?;

    let mut worst: Option<Severity> = None;
    let mut affected: Vec<String> = Vec::new();
    let mut unknown: Vec<String> = Vec::new();
    for component in &page.components {
        let name = component.name.as_deref().unwrap_or("unnamed");
        let raw = component.status.as_deref().unwrap_or("");
        match map_component_status(raw) {
            Some(Severity::None) => {}
            Some(sev) => {
                affected.push(format!("{name}: {raw}"));
                if worst.is_none_or(|w| sev > w) {
                    worst = Some(sev);
                }
            }
            None if !raw.is_empty() => unknown.push(format!("{name}: {raw}")),
            None => {}
        }
    }

    let page_status = page
        .status
        .unwrap_or(PageStatus { indicator: None, description: None });
    let top_level = page_status
        .indicator
        .as_deref()
        .and_then(Severity::from_indicator);
    let indicator = worst.or(top_level).unwrap_or(Severity::None);
    let summary = page_status
        .description
        .filter(|d| !d.trim().is_empty())
        .unwrap_or_else(|| {
            if affected.is_empty() {
                "all systems operational".to_string()
            } else {
                format!("affected: {}", affected.join("; "))
            }
        });

    Ok(ProviderStatus {
        indicator: Some(indicator),
        summary,
        updated_at: fetched_at,
        stale: false,
        error: None,
        detail: if unknown.is_empty() {
            None
        } else {
            Some(format!(
                "unknown component status: {}",
                unknown.join("; ")
            ))
        },
    })
}

/// Pure: fold one round of fetch results over the previous snapshots.
/// Failures preserve the last good entry marked stale; a provider that never
/// succeeded gets an unavailable placeholder (null indicator, error text as
/// summary) so the envelope stays well-formed without network.
fn merge_results(
    previous: &BTreeMap<String, ProviderStatus>,
    results: Vec<(&'static str, Result<ProviderStatus, String>)>,
    now: DateTime<Utc>,
) -> BTreeMap<String, ProviderStatus> {
    let mut out = BTreeMap::new();
    for (provider, result) in results {
        match result {
            Ok(status) => {
                out.insert(provider.to_string(), status);
            }
            Err(detail) => match previous.get(provider) {
                Some(good) => {
                    let mut stale = good.clone();
                    stale.stale = true;
                    stale.error = Some(detail);
                    out.insert(provider.to_string(), stale);
                }
                None => {
                    out.insert(
                        provider.to_string(),
                        ProviderStatus {
                            indicator: None,
                            summary: format!("status unavailable: {detail}"),
                            updated_at: now,
                            stale: false,
                            error: Some(detail),
                            detail: None,
                        },
                    );
                }
            },
        }
    }
    out
}

/// Injectable fetch round: provider id -> mapped snapshot or failure detail.
/// Real implementations hit the network; tests stub this.
type FetchAll =
    Box<dyn Fn(&ureq::Agent) -> Vec<(&'static str, Result<ProviderStatus, String>)> + Send + Sync>;

#[derive(Default)]
struct CacheState {
    /// Anchor instant of the last live poll (TTL window).
    last_fetch: Option<Instant>,
    /// Result served within the TTL window.
    cached: Option<BTreeMap<String, ProviderStatus>>,
    /// Last good snapshot per provider, for stale-on-failure fallback.
    last_success: BTreeMap<String, ProviderStatus>,
}

/// TTL-cached poller over the status pages, mirroring the quota
/// `CachedSource` behavior: live fetch at most once per TTL window, and on
/// failure the last good snapshot is served stale instead of being dropped.
/// Safe for concurrent threads (RwLock; lock poisoning is recovered from).
pub struct StatusPoller {
    agent: ureq::Agent,
    ttl: Duration,
    fetch_all: FetchAll,
    state: RwLock<CacheState>,
}

impl Default for StatusPoller {
    fn default() -> Self {
        Self::new()
    }
}

impl StatusPoller {
    /// Poller over the built-in [`STATUS_PAGES`] table.
    pub fn new() -> Self {
        Self::with_fetcher(Box::new(default_fetch_all))
    }

    /// Stub/injectable constructor (tests swap the network round out).
    pub(crate) fn with_fetcher(fetch_all: FetchAll) -> Self {
        Self {
            agent: http_agent(),
            ttl: CACHE_TTL,
            fetch_all,
            state: RwLock::new(CacheState::default()),
        }
    }

    /// Snapshot for every mapped provider, refreshed at most once per TTL
    /// window. Never fails: failed providers come back stale or as
    /// well-formed placeholders.
    pub fn fetch(&self) -> BTreeMap<String, ProviderStatus> {
        {
            let guard = self.state.read().unwrap_or_else(|p| p.into_inner());
            if let Some(map) = fresh(&guard, self.ttl) {
                return map;
            }
        }
        let mut guard = self.state.write().unwrap_or_else(|p| p.into_inner());
        if let Some(map) = fresh(&guard, self.ttl) {
            return map;
        }
        let results = (self.fetch_all)(&self.agent);
        let merged = merge_results(&guard.last_success, results, Utc::now());
        for (provider, snapshot) in &merged {
            if snapshot.error.is_none() {
                guard
                    .last_success
                    .insert(provider.clone(), snapshot.clone());
            }
        }
        guard.cached = Some(merged.clone());
        guard.last_fetch = Some(Instant::now());
        merged
    }
}

fn fresh(state: &CacheState, ttl: Duration) -> Option<BTreeMap<String, ProviderStatus>> {
    let at = state.last_fetch?;
    let cached = state.cached.as_ref()?;
    (at.elapsed() < ttl).then(|| cached.clone())
}

/// GET `<base>/api/v2/summary.json` with the shared 10s-timeout agent and
/// retry-once pattern, then map the body. Any failure is a plain `String`.
fn fetch_one(agent: &ureq::Agent, base: &str) -> Result<ProviderStatus, String> {
    let url = format!("{base}/api/v2/summary.json");
    fetch_with_retry(|| {
        agent
            .get(&url)
            .set("Accept", "application/json")
            .call()
            .map_err(|e| format!("status request failed: {e}"))?
            .into_string()
            .map_err(|e| format!("failed to read status response: {e}"))
    })
    .and_then(|body| map_summary(&body, Utc::now()))
}

/// Fetch every mapped provider's page (one retry-once round each).
fn default_fetch_all(
    agent: &ureq::Agent,
) -> Vec<(&'static str, Result<ProviderStatus, String>)> {
    STATUS_PAGES
        .iter()
        .map(|&(provider, base)| (provider, fetch_one(agent, base)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE_OK: &str = r#"{"page":{},"status":{"indicator":"none","description":"All Systems Operational"},"components":[{"name":"API","status":"operational"},{"name":"Dashboard","status":"operational"}]}"#;

    const FIXTURE_OUTAGE: &str = r#"{"status":{"indicator":"major","description":"Partial system outage"},"components":[{"name":"API","status":"operational"},{"name":"Login","status":"degraded_performance"},{"name":"Claude app","status":"partial_outage"},{"name":"Legacy","status":"full_outage"}]}"#;

    #[test]
    fn component_status_mapping_is_exact() {
        assert_eq!(map_component_status("operational"), Some(Severity::None));
        assert_eq!(
            map_component_status("degraded_performance"),
            Some(Severity::Minor)
        );
        assert_eq!(map_component_status("partial_outage"), Some(Severity::Major));
        assert_eq!(map_component_status("major_outage"), Some(Severity::Critical));
        assert_eq!(map_component_status("full_outage"), Some(Severity::Critical));
        assert_eq!(
            map_component_status("under_maintenance"),
            Some(Severity::Maintenance)
        );
        assert_eq!(map_component_status("aliens"), None);
        assert_eq!(map_component_status(""), None);
    }

    #[test]
    fn severity_orders_worst_last() {
        assert!(Severity::None < Severity::Maintenance);
        assert!(Severity::Maintenance < Severity::Minor);
        assert!(Severity::Minor < Severity::Major);
        assert!(Severity::Major < Severity::Critical);
    }

    #[test]
    fn parses_all_operational_fixture() {
        let s = map_summary(FIXTURE_OK, Utc::now()).unwrap();
        assert_eq!(s.indicator, Some(Severity::None));
        assert_eq!(s.summary, "All Systems Operational");
        assert!(!s.stale);
        assert!(s.error.is_none());
        assert!(s.detail.is_none());
    }

    #[test]
    fn rolls_up_worst_component_and_records_unknown() {
        let s = map_summary(FIXTURE_OUTAGE, Utc::now()).unwrap();
        assert_eq!(s.indicator, Some(Severity::Critical), "full_outage wins");
        assert_eq!(s.summary, "Partial system outage");

        // Unknown component string: ignored for severity, kept in detail.
        let fixture = r#"{"status":{"indicator":"minor"},"components":[{"name":"API","status":"degraded_performance"},{"name":"Warp","status":"quantum_flux"}]}"#;
        let s = map_summary(fixture, Utc::now()).unwrap();
        assert_eq!(s.indicator, Some(Severity::Minor));
        let detail = s.detail.unwrap();
        assert!(detail.contains("quantum_flux"), "raw string preserved: {detail}");
        assert!(!detail.contains("degraded_performance"));
    }

    #[test]
    fn maintenance_only_page_maps_to_maintenance() {
        let fixture = r#"{"status":{"indicator":"maintenance","description":"Scheduled maintenance"},"components":[{"name":"API","status":"operational"},{"name":"Auth","status":"under_maintenance"}]}"#;
        let s = map_summary(fixture, Utc::now()).unwrap();
        assert_eq!(s.indicator, Some(Severity::Maintenance));
        assert_eq!(s.summary, "Scheduled maintenance");
    }

    #[test]
    fn no_components_falls_back_to_top_level_indicator() {
        let fixture = r#"{"status":{"indicator":"critical","description":""}}"#;
        let s = map_summary(fixture, Utc::now()).unwrap();
        assert_eq!(s.indicator, Some(Severity::Critical));
        // Empty description with no affected components -> calm fallback text.
        assert_eq!(s.summary, "all systems operational");

        // Page without a status object at all: parse succeeds, calm snapshot.
        let bare = map_summary(r#"{"components":[]}"#, Utc::now()).unwrap();
        assert_eq!(bare.indicator, Some(Severity::None));
    }

    #[test]
    fn invalid_body_is_error() {
        assert!(map_summary("not json", Utc::now()).is_err());
        assert!(map_summary("123", Utc::now()).is_err());
    }

    #[test]
    fn top_level_indicator_parses_own_vocabulary() {
        assert_eq!(Severity::from_indicator("none"), Some(Severity::None));
        assert_eq!(Severity::from_indicator("major"), Some(Severity::Major));
        assert_eq!(Severity::from_indicator("critical"), Some(Severity::Critical));
        assert_eq!(Severity::from_indicator("partial_outage"), None);
    }

    #[test]
    fn merge_preserves_last_good_marks_stale_and_placeholders() {
        let good = map_summary(FIXTURE_OK, Utc::now()).unwrap();
        let mut previous = BTreeMap::new();
        previous.insert("claude".to_string(), good.clone());

        let results = vec![
            (
                "claude",
                Err::<ProviderStatus, String>("status request failed: dns".to_string()),
            ),
            (
                "chatgpt",
                Err("status request failed: timeout".to_string()),
            ),
        ];
        let merged = merge_results(&previous, results, Utc::now());

        let claude = &merged["claude"];
        assert_eq!(claude.indicator, good.indicator, "last good preserved");
        assert_eq!(claude.summary, good.summary);
        assert_eq!(claude.updated_at, good.updated_at, "original fetch time kept");
        assert!(claude.stale);
        assert_eq!(claude.error.as_deref(), Some("status request failed: dns"));

        let chatgpt = &merged["chatgpt"];
        assert!(chatgpt.indicator.is_none(), "never-polled stays null");
        assert!(!chatgpt.stale);
        assert!(chatgpt.summary.contains("status unavailable"));
        assert_eq!(chatgpt.error.as_deref(), Some("status request failed: timeout"));
    }

    #[test]
    fn merge_accepts_successes_over_previous() {
        let good = map_summary(FIXTURE_OK, Utc::now()).unwrap();
        let mut previous = BTreeMap::new();
        previous.insert("claude".to_string(), good);

        let worse = ProviderStatus {
            indicator: Some(Severity::Major),
            summary: "outage now".into(),
            updated_at: Utc::now(),
            stale: false,
            error: None,
            detail: None,
        };
        let merged = merge_results(
            &previous,
            vec![("claude", Ok(worse))],
            Utc::now(),
        );
        let claude = &merged["claude"];
        assert_eq!(claude.indicator, Some(Severity::Major));
        assert!(!claude.stale);
        assert!(claude.error.is_none());
    }

    #[test]
    fn snapshot_json_shape_omits_absent_error_and_detail() {
        let ok = ProviderStatus {
            indicator: Some(Severity::None),
            summary: "All Systems Operational".into(),
            updated_at: Utc::now(),
            stale: false,
            error: None,
            detail: None,
        };
        let v = ok.to_json();
        assert_eq!(v["indicator"], "none");
        assert_eq!(v["summary"], "All Systems Operational");
        assert_eq!(v["stale"], false);
        assert!(DateTime::parse_from_rfc3339(v["updated_at"].as_str().unwrap()).is_ok());
        assert!(v.get("error").is_none(), "no error key when absent");
        assert!(v.get("detail").is_none(), "no detail key when absent");

        let never = ProviderStatus {
            indicator: None,
            summary: "status unavailable: offline".into(),
            updated_at: Utc::now(),
            stale: false,
            error: Some("offline".into()),
            detail: None,
        };
        let v = never.to_json();
        assert!(v["indicator"].is_null(), "never-polled indicator is null");
        assert_eq!(v["error"], "offline");
    }

    #[test]
    fn check_fields_null_unless_polled() {
        let good = ProviderStatus {
            indicator: Some(Severity::Major),
            summary: "Partial system outage".into(),
            updated_at: Utc::now(),
            stale: false,
            error: None,
            detail: None,
        };
        let (ind, sum) = check_fields(Some(&good));
        assert_eq!(ind, "major");
        assert_eq!(sum, "Partial system outage");

        let never = ProviderStatus {
            indicator: None,
            summary: "status unavailable: offline".into(),
            updated_at: Utc::now(),
            stale: false,
            error: None,
            detail: None,
        };
        let (ind, sum) = check_fields(Some(&never));
        assert!(ind.is_null() && sum.is_null());

        let (ind, sum) = check_fields(None);
        assert!(ind.is_null() && sum.is_null());
    }

    /// Thread-safe call counter: FetchAll requires Send + Sync.
    fn counting_poller(
        counter: std::sync::Arc<std::sync::atomic::AtomicU32>,
        ttl: Duration,
    ) -> StatusPoller {
        StatusPoller {
            agent: http_agent(),
            ttl,
            fetch_all: Box::new(move |_| {
                counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                vec![(
                    "claude",
                    Ok(ProviderStatus {
                        indicator: Some(Severity::None),
                        summary: "All Systems Operational".into(),
                        updated_at: Utc::now(),
                        stale: false,
                        error: None,
                        detail: None,
                    }),
                )]
            }),
            state: RwLock::new(CacheState::default()),
        }
    }

    #[test]
    fn poller_serves_cache_within_ttl() {
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicU32::new(0));
        let poller = counting_poller(std::sync::Arc::clone(&calls), CACHE_TTL);
        let first = poller.fetch();
        let second = poller.fetch();
        assert_eq!(first, second);
        assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1, "one live poll");
    }

    #[test]
    fn poller_refetches_after_ttl_expiry() {
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicU32::new(0));
        let poller = counting_poller(std::sync::Arc::clone(&calls), Duration::ZERO);
        let _ = poller.fetch();
        let _ = poller.fetch();
        assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 2, "TTL expired");
    }

    #[test]
    fn poller_survives_failure_then_serves_stale_then_recovers() {
        // First round fails, second succeeds: placeholder -> stale -> good.
        let round = std::sync::Arc::new(std::sync::atomic::AtomicU32::new(0));
        let poller = StatusPoller {
            agent: http_agent(),
            ttl: Duration::ZERO,
            fetch_all: Box::new(move |_| {
                let n = round.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                if n == 0 {
                    vec![("claude", Err("down".to_string()))]
                } else {
                    vec![(
                        "claude",
                        Ok(ProviderStatus {
                            indicator: Some(Severity::Minor),
                            summary: "degraded".into(),
                            updated_at: Utc::now(),
                            stale: false,
                            error: None,
                            detail: None,
                        }),
                    )]
                }
            }),
            state: RwLock::new(CacheState::default()),
        };

        let first = poller.fetch();
        assert!(first["claude"].indicator.is_none());

        let second = poller.fetch();
        assert_eq!(second["claude"].indicator, Some(Severity::Minor));
        assert!(!second["claude"].stale);
        assert!(second["claude"].error.is_none());
    }
}
