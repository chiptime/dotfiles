//! Pull-provider infrastructure: the `QuotaSource` trait, a TTL cache
//! wrapper, and shared result constructors.

pub mod chatgpt;
pub mod claude;
pub mod deepseek;
pub mod opencode;
pub mod zai;

use std::sync::RwLock;
use std::time::{Duration, Instant};

use crate::reader::State;
use crate::schema::Record;

/// One pull-provider result. Reuses the stage-1 `Status` shape verbatim
/// (record + computed state + detail) so file-mode and pull-mode records flow
/// through the same rendering path.
pub type ProviderRecord = crate::reader::Status;

/// A source of quota records fetched over the network.
pub trait QuotaSource {
    /// Fetch current records. Implementations must never panic: credential
    /// gaps surface as `Missing`, failures as `Error` records.
    fn fetch(&self) -> Vec<ProviderRecord>;
}

/// Default cache TTL for pull sources (10 minutes to protect against excessive upstream queries).
pub const CACHE_TTL: Duration = Duration::from_secs(600);

/// Build a shared HTTP agent with a 10s overall request timeout.
pub(crate) fn http_agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(10))
        .build()
}

/// Read an env var, treating empty/whitespace-only values as unset.
pub(crate) fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

/// Build a `Missing`-state result (no credentials — not an error).
pub fn missing_record(provider: &str, display_name: &str, detail: &str) -> ProviderRecord {
    ProviderRecord {
        provider: provider.to_string(),
        state: State::Missing,
        record: None,
        age_seconds: None,
        detail: Some(detail.to_string()),
        display_name: Some(display_name.to_string()),
    }
}

/// Run one fallible HTTP fetch; on failure, pause briefly and retry once,
/// reporting the FIRST error if both attempts fail. Live-verified: pull
/// blips (claude/chatgpt "Network Error") are transient and clear within
/// seconds — the retry keeps the last-good data from degrading to Stale.
pub fn fetch_with_retry<T>(f: impl Fn() -> Result<T, String>) -> Result<T, String> {
    match f() {
        Ok(v) => Ok(v),
        Err(first) => {
            std::thread::sleep(std::time::Duration::from_secs(2));
            f().map_err(|_| first)
        }
    }
}

/// Build an `Error`-state result (fetch or parse failure).
pub fn error_record(provider: &str, display_name: &str, detail: String) -> ProviderRecord {
    ProviderRecord {
        provider: provider.to_string(),
        state: State::Error,
        record: None,
        age_seconds: None,
        detail: Some(detail),
        display_name: Some(display_name.to_string()),
    }
}

/// Wrap a successfully mapped record as an `Ok` result.
pub fn ok_record(record: Record, detail: Option<String>) -> ProviderRecord {
    ProviderRecord {
        provider: record.provider.clone(),
        state: State::Ok,
        display_name: record.display_name.clone(),
        record: Some(record),
        age_seconds: None,
        detail,
    }
}

#[derive(Default)]
struct CacheState {
    /// Anchor instant of the last live fetch (rate-limit window).
    last_fetch: Option<Instant>,
    /// Result served within the TTL window.
    cached: Option<Vec<ProviderRecord>>,
    /// Last all-`Ok` result, used for stale-on-failure fallback.
    last_success: Option<Vec<ProviderRecord>>,
}

/// Wraps a source so the live fetch runs at most once per TTL window; callers
/// inside the window share the cached result. Safe for concurrent threads
/// (RwLock; lock poisoning is recovered from, never panicked on).
pub struct CachedSource<S> {
    inner: S,
    ttl: Duration,
    state: RwLock<CacheState>,
}

impl<S: QuotaSource> CachedSource<S> {
    pub fn with_ttl(inner: S, ttl: Duration) -> Self {
        Self {
            inner,
            ttl,
            state: RwLock::new(CacheState::default()),
        }
    }

    /// Return cached records if fresh within TTL; otherwise perform a live fetch.
    pub fn fetch_cached(&self) -> Vec<ProviderRecord> {
        self.fetch()
    }

    /// Force a live fetch immediately, bypassing cache TTL and updating cache.
    pub fn fetch_force(&self) -> Vec<ProviderRecord> {
        let fetched = self.inner.fetch();
        let all_ok = !fetched.is_empty() && fetched.iter().all(|r| r.state == State::Ok);
        let mut guard = self.state.write().unwrap_or_else(|p| p.into_inner());
        let output = if all_ok {
            fetched.clone()
        } else if let Some(previous) = guard.last_success.clone() {
            let detail = failure_detail(&fetched);
            previous
                .into_iter()
                .map(|mut r| {
                    r.state = State::Stale;
                    r.detail = Some(detail.clone());
                    r
                })
                .collect()
        } else {
            fetched.clone()
        };
        if all_ok {
            guard.last_success = Some(fetched);
        }
        guard.cached = Some(output.clone());
        guard.last_fetch = Some(Instant::now());
        output
    }
}

fn fresh_cache(state: &CacheState, ttl: Duration) -> Option<Vec<ProviderRecord>> {
    let at = state.last_fetch?;
    let cached = state.cached.as_ref()?;
    (at.elapsed() < ttl).then(|| cached.clone())
}

impl<S: QuotaSource> QuotaSource for CachedSource<S> {
    fn fetch(&self) -> Vec<ProviderRecord> {
        {
            let guard = self.state.read().unwrap_or_else(|p| p.into_inner());
            if let Some(records) = fresh_cache(&guard, self.ttl) {
                return records;
            }
        }
        let mut guard = self.state.write().unwrap_or_else(|p| p.into_inner());
        if let Some(records) = fresh_cache(&guard, self.ttl) {
            return records;
        }
        let fetched = self.inner.fetch();
        let all_ok = !fetched.is_empty() && fetched.iter().all(|r| r.state == State::Ok);
        let output = if all_ok {
            fetched.clone()
        } else if let Some(previous) = guard.last_success.clone() {
            // Failure with a previous success on record: serve it Stale,
            // carrying the failure description in `detail`.
            let detail = failure_detail(&fetched);
            previous
                .into_iter()
                .map(|mut r| {
                    r.state = State::Stale;
                    r.detail = Some(detail.clone());
                    r
                })
                .collect()
        } else {
            fetched.clone()
        };
        if all_ok {
            guard.last_success = Some(fetched);
        }
        guard.cached = Some(output.clone());
        guard.last_fetch = Some(Instant::now());
        output
    }
}

/// Unified manager holding long-lived CachedSource instances.
pub struct PullManager {
    pub claude: CachedSource<claude::ClaudeSource>,
    pub chatgpt: CachedSource<chatgpt::ChatGptSource>,
    pub opencode: CachedSource<opencode::OpenCodeSource>,
    pub deepseek: CachedSource<deepseek::DeepSeekSource>,
    pub zai: CachedSource<zai::ZaiSource>,
}

impl PullManager {
    pub fn from_env() -> Self {
        Self {
            claude: CachedSource::with_ttl(claude::ClaudeSource::from_env(), CACHE_TTL),
            chatgpt: CachedSource::with_ttl(chatgpt::ChatGptSource::from_env(), CACHE_TTL),
            opencode: CachedSource::with_ttl(opencode::OpenCodeSource::from_env(), CACHE_TTL),
            deepseek: CachedSource::with_ttl(deepseek::DeepSeekSource::from_env(), CACHE_TTL),
            zai: CachedSource::with_ttl(zai::ZaiSource::from_env(), CACHE_TTL),
        }
    }

    pub fn fetch_cached(&self) -> Vec<ProviderRecord> {
        let mut out = Vec::new();
        out.extend(self.claude.fetch_cached());
        out.extend(self.chatgpt.fetch_cached());
        out.extend(self.opencode.fetch_cached());
        out.extend(self.deepseek.fetch_cached());
        out.extend(self.zai.fetch_cached());
        out
    }

    pub fn fetch_force(&self) -> Vec<ProviderRecord> {
        let mut out = Vec::new();
        out.extend(self.claude.fetch_force());
        out.extend(self.chatgpt.fetch_force());
        out.extend(self.opencode.fetch_force());
        out.extend(self.deepseek.fetch_force());
        out.extend(self.zai.fetch_force());
        out
    }
}

/// Convenience pull_all function using a one-off or default fetch.
pub fn pull_all() -> Vec<ProviderRecord> {
    PullManager::from_env().fetch_cached()
}

/// Summarize why a fetch was not fully successful, from its Error records.
fn failure_detail(fetched: &[ProviderRecord]) -> String {
    let causes: Vec<String> = fetched
        .iter()
        .filter(|r| r.state == State::Error)
        .filter_map(|r| r.detail.clone())
        .collect();
    if causes.is_empty() {
        "fetch unsuccessful".to_string()
    } else {
        format!("fetch failed: {}", causes.join("; "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fetch_with_retry_recovers_transient_failure() {
        let attempts = std::cell::Cell::new(0);
        let r = fetch_with_retry(|| {
            attempts.set(attempts.get() + 1);
            if attempts.get() == 1 { Err("transient".to_string()) } else { Ok(7) }
        });
        assert_eq!(r.unwrap(), 7);
        assert_eq!(attempts.get(), 2);
    }

    #[test]
    fn fetch_with_retry_reports_first_error_when_both_fail() {
        let r = fetch_with_retry(|| Err::<i32, String>("first".to_string()));
        assert_eq!(r.unwrap_err(), "first");
    }
    use std::cell::{Cell, RefCell};

    use chrono::Utc;

    use crate::schema::{Kind, Source};

    /// Scripted source: pops the next result per fetch (repeats the last one).
    struct ScriptedSource {
        calls: Cell<u32>,
        script: RefCell<Vec<Vec<ProviderRecord>>>,
    }

    impl ScriptedSource {
        fn new(script: Vec<Vec<ProviderRecord>>) -> Self {
            Self {
                calls: Cell::new(0),
                script: RefCell::new(script),
            }
        }
    }

    impl QuotaSource for ScriptedSource {
        fn fetch(&self) -> Vec<ProviderRecord> {
            self.calls.set(self.calls.get() + 1);
            let mut script = self.script.borrow_mut();
            if script.len() > 1 {
                script.remove(0)
            } else {
                script[0].clone()
            }
        }
    }

    fn ok_rec(provider: &str, limit: f64) -> Vec<ProviderRecord> {
        let mut record = Record::new(provider.into(), Kind::Window, Source::Api, Utc::now(), None);
        record.limit = Some(limit);
        vec![ok_record(record, None)]
    }

    fn err_rec(detail: &str) -> Vec<ProviderRecord> {
        vec![error_record("test", "Test", detail.to_string())]
    }

    #[test]
    fn cache_hit_within_ttl_serves_cached_without_refetch() {
        let source = ScriptedSource::new(vec![ok_rec("p", 1.0)]);
        let cached = CachedSource::with_ttl(source, CACHE_TTL);
        let first = cached.fetch();
        let second = cached.fetch();
        assert_eq!(cached.inner.calls.get(), 1, "live fetch must run once");
        assert_eq!(first, second);
        assert_eq!(second[0].state, State::Ok);
    }

    #[test]
    fn cache_expiry_triggers_refetch() {
        let source = ScriptedSource::new(vec![ok_rec("p", 1.0), ok_rec("p", 2.0)]);
        let cached = CachedSource::with_ttl(source, Duration::ZERO);
        let first = cached.fetch();
        let second = cached.fetch();
        assert_eq!(cached.inner.calls.get(), 2);
        assert_eq!(first[0].record.as_ref().unwrap().limit, Some(1.0));
        assert_eq!(second[0].record.as_ref().unwrap().limit, Some(2.0));
    }

    #[test]
    fn failure_with_prior_success_serves_stale_with_detail() {
        let source = ScriptedSource::new(vec![ok_rec("p", 7.0), err_rec("boom")]);
        let cached = CachedSource::with_ttl(source, Duration::ZERO);
        assert_eq!(cached.fetch()[0].state, State::Ok);
        let stale = cached.fetch();
        assert_eq!(cached.inner.calls.get(), 2);
        assert_eq!(stale[0].state, State::Stale);
        assert_eq!(stale[0].record.as_ref().unwrap().limit, Some(7.0));
        assert!(stale[0].detail.as_deref().unwrap().contains("boom"));
    }

    #[test]
    fn failure_without_prior_success_returns_error() {
        let source = ScriptedSource::new(vec![err_rec("no network")]);
        let cached = CachedSource::with_ttl(source, Duration::ZERO);
        let out = cached.fetch();
        assert_eq!(out[0].state, State::Error);
        assert!(out[0].record.is_none());
        assert_eq!(out[0].detail.as_deref(), Some("no network"));
    }
}
