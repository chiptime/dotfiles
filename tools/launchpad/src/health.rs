//! Server-side tile health: HTTP GET probe with a per-tile TTL cache.
//!
//! A probe is `up` when ANY HTTP response arrives (any status code — a 404
//! root still proves the service is listening) and `down` on transport
//! errors/timeouts. Results are cached per tile for its
//! `health_interval_secs`, so front polling never hammers the services.

use crate::config::TileConfig;
use serde::Serialize;
use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const PROBE_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum State {
    Up,
    Down,
}

#[derive(Debug, Clone, Serialize)]
pub struct Health {
    pub state: State,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub latency_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl Health {
    pub fn up(latency_ms: u64) -> Self {
        Self { state: State::Up, latency_ms: Some(latency_ms), error: None }
    }

    pub fn down(error: String) -> Self {
        Self { state: State::Down, latency_ms: None, error: Some(error) }
    }
}

/// Probe outcome injected by tests: latency in ms on response, error string
/// on transport failure.
pub type ProbeFn = Box<dyn Fn(&str) -> Result<u64, String> + Send + Sync>;

struct CacheEntry {
    health: Health,
    checked_at: Instant,
}

pub struct HealthChecker {
    cache: Mutex<BTreeMap<String, CacheEntry>>,
    probe: ProbeFn,
}

/// Default probe: one GET with a 2s timeout; any HTTP status counts as up.
fn default_probe(url: &str) -> Result<u64, String> {
    let started = Instant::now();
    let response = ureq::AgentBuilder::new()
        .timeout(PROBE_TIMEOUT)
        .build()
        .get(url)
        .call();
    match response {
        Ok(_) | Err(ureq::Error::Status(_, _)) => Ok(started.elapsed().as_millis() as u64),
        Err(e) => Err(e.to_string()),
    }
}

impl HealthChecker {
    pub fn new() -> Self {
        Self::with_probe(Box::new(default_probe))
    }

    pub fn with_probe(probe: ProbeFn) -> Self {
        Self { cache: Mutex::new(BTreeMap::new()), probe }
    }

    /// Health for one tile: fresh cache hit within its interval, else probe
    /// now and cache the result.
    pub fn health_for(&self, tile: &TileConfig) -> Health {
        let ttl = Duration::from_secs(tile.health_interval_secs);
        {
            let guard = self.cache.lock().unwrap_or_else(|p| p.into_inner());
            if let Some(entry) = guard.get(&tile.id) {
                if entry.checked_at.elapsed() < ttl {
                    return entry.health.clone();
                }
            }
        }
        let url = crate::config::LaunchpadConfig::health_url(tile);
        let health = match (self.probe)(url) {
            Ok(ms) => Health::up(ms),
            Err(e) => Health::down(e),
        };
        let mut guard = self.cache.lock().unwrap_or_else(|p| p.into_inner());
        guard.insert(
            tile.id.clone(),
            CacheEntry { health: health.clone(), checked_at: Instant::now() },
        );
        health
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tile(id: &str, interval_secs: u64) -> TileConfig {
        serde_json::from_str(&format!(
            r#"{{"id":"{id}","name":"N","url":"http://h/","health_interval_secs":{interval_secs}}}"#
        ))
        .unwrap()
    }

    #[test]
    fn fresh_cache_prevents_reprobe() {
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicU32::new(0));
        let calls2 = std::sync::Arc::clone(&calls);
        let checker = HealthChecker::with_probe(Box::new(move |_| {
            calls2.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Ok(12)
        }));
        let t = tile("a", 60);
        let first = checker.health_for(&t);
        let second = checker.health_for(&t);
        assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);
        assert_eq!(first.state, State::Up);
        assert_eq!(second.latency_ms, first.latency_ms);
    }

    #[test]
    fn expired_ttl_reprobes_and_updates() {
        let checker = HealthChecker::with_probe(Box::new(|_| Err("conn refused".into())));
        let t = tile("a", 0); // ttl 0s: never fresh -> always reprobe
        let first = checker.health_for(&t);
        assert_eq!(first.state, State::Down);
        assert!(first.error.as_deref().unwrap().contains("conn refused"));
        let second = checker.health_for(&t);
        assert_eq!(second.state, State::Down);
    }

    #[test]
    fn health_url_override_is_probed() {
        let probed: std::sync::Arc<Mutex<Vec<String>>> = std::sync::Arc::new(Mutex::new(Vec::new()));
        let sink = std::sync::Arc::clone(&probed);
        let checker = HealthChecker::with_probe(Box::new(move |url| {
            sink.lock().unwrap().push(url.to_string());
            Ok(5)
        }));
        let t: TileConfig = serde_json::from_str(
            r#"{"id":"a","name":"N","url":"http://h/","health_url":"http://h/api/health"}"#,
        )
        .unwrap();
        checker.health_for(&t);
        assert_eq!(*probed.lock().unwrap(), vec!["http://h/api/health".to_string()]);
    }
}
