//! Reads quota record files from the state directory.
//!
//! Every `*.json` file in the state dir is one record; the file stem is the
//! provider id. Known file-mode providers without a file are reported as
//! `Missing`. This module never panics on bad input.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Duration, Utc};

use crate::schema::Record;

/// How a provider's data is obtained.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Records are produced locally (files written by `stamp` or local tooling).
    File,
    /// Records are pulled from a remote API (server stages, later).
    Pull,
}

/// A provider this tool knows about.
pub struct KnownProvider {
    pub id: &'static str,
    pub display_name: &'static str,
    pub mode: Mode,
}

pub const KNOWN_PROVIDERS: &[KnownProvider] = &[
    KnownProvider { id: "claude", display_name: "Claude Pro", mode: Mode::Pull },
    KnownProvider { id: "gemini", display_name: "Google Gemini", mode: Mode::File },
    KnownProvider { id: "chatgpt", display_name: "ChatGPT Plus", mode: Mode::Pull },
    KnownProvider { id: "opencode", display_name: "OpenCode Go", mode: Mode::Pull },
    KnownProvider { id: "deepseek", display_name: "DeepSeek API", mode: Mode::Pull },
    KnownProvider { id: "zai", display_name: "Z.ai Coding Plan MAX", mode: Mode::Pull },
];

/// Display name from the static provider list, if known.
pub fn known_display_name(id: &str) -> Option<&'static str> {
    KNOWN_PROVIDERS.iter().find(|p| p.id == id).map(|p| p.display_name)
}

/// Freshness of a record relative to its TTL window.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    /// `now <= fetched_at + ttl`
    Ok,
    /// Past its TTL.
    Stale,
    /// File present but unparseable, invalid or missing required fields.
    Error,
    /// Known file-mode provider with no file.
    Missing,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::Ok => "ok",
            State::Stale => "stale",
            State::Error => "error",
            State::Missing => "missing",
        }
    }
}

/// Per-record read result: the parsed record plus its computed state.
#[derive(Debug, Clone, PartialEq)]
pub struct Status {
    pub provider: String,
    pub state: State,
    /// Parsed record; `None` for Error/Missing.
    pub record: Option<Record>,
    /// Seconds since `fetched_at`; `None` when there is no usable record.
    // Consumed by the server/UI stages; keep it in the stage-1 contract.
    #[allow(dead_code)]
    pub age_seconds: Option<u64>,
    /// Error detail when `state == Error`.
    pub detail: Option<String>,
    /// Best display name (from record, or the known-provider list).
    pub display_name: Option<String>,
}

/// Resolve the state dir: `AI_QUOTAS_STATE_DIR`, else
/// `$XDG_STATE_HOME/ai-quotas`, else `~/.local/state/ai-quotas`.
pub fn state_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("AI_QUOTAS_STATE_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    if let Some(xdg) = std::env::var_os("XDG_STATE_HOME") {
        if !xdg.is_empty() {
            return PathBuf::from(xdg).join("ai-quotas");
        }
    }
    if let Some(home) = std::env::var_os("HOME") {
        if !home.is_empty() {
            return PathBuf::from(home).join(".local").join("state").join("ai-quotas");
        }
    }
    // Last-resort fallback so callers never have to handle absence.
    PathBuf::from(".ai-quotas-state")
}

/// Read all records using the env-resolved state dir.
pub fn read_all() -> Vec<Status> {
    read_dir_status(&state_dir())
}

/// Read all records from an explicit directory. Never panics; an absent or
/// unreadable dir simply yields `Missing` for every known file-mode provider.
pub fn read_dir_status(dir: &Path) -> Vec<Status> {
    let now = Utc::now();
    let mut by_provider: HashMap<String, Vec<Status>> = HashMap::new();

    if let Ok(entries) = fs::read_dir(dir) {
        let mut paths: Vec<PathBuf> = entries
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().and_then(|e| e.to_str()) == Some("json"))
            .collect();
        paths.sort();
        for path in paths {
            let stem = path
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            if stem.is_empty() {
                continue;
            }
            let mut status = file_status(&path, &stem, now);
            // The record's own `provider` field is authoritative once parsed;
            // the file stem is only a fallback (unreadable/invalid files) and
            // a naming hint. Multiple files may share one provider (one file
            // per window, e.g. gemini-5h.json + gemini-weekly.json).
            let provider = status
                .record
                .as_ref()
                .map(|r| r.provider.trim().to_string())
                .filter(|p| !p.is_empty())
                .unwrap_or_else(|| stem.clone());
            status.provider = provider.clone();
            by_provider.entry(provider).or_default().push(status);
        }
    }

    // Known file-mode providers first, in list order; synthesized Missing for
    // those without any record, then any remaining custom/unknown files.
    let mut out = Vec::new();
    for known in KNOWN_PROVIDERS.iter().filter(|k| k.mode == Mode::File) {
        match by_provider.remove(known.id) {
            Some(mut statuses) => out.append(&mut statuses),
            None => out.push(Status {
                provider: known.id.to_string(),
                state: State::Missing,
                record: None,
                age_seconds: None,
                detail: None,
                display_name: Some(known.display_name.to_string()),
            }),
        }
    }
    let mut rest: Vec<Status> = by_provider.into_values().flatten().collect();
    rest.sort_by(|a, b| a.provider.cmp(&b.provider));
    out.extend(rest);
    out
}

/// Classify a single record file.
fn file_status(path: &Path, provider: &str, now: DateTime<Utc>) -> Status {
    let error = |detail: String| Status {
        provider: provider.to_string(),
        state: State::Error,
        record: None,
        age_seconds: None,
        detail: Some(detail),
        display_name: known_display_name(provider).map(str::to_string),
    };

    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(e) => return error(format!("failed to read {}: {e}", path.display())),
    };
    let record: Record = match serde_json::from_slice(&bytes) {
        Ok(record) => record,
        Err(e) => return error(format!("invalid record: {e}")),
    };
    if let Err(msg) = record.validate() {
        return error(msg);
    }

    let age_seconds = (now - record.fetched_at).num_seconds().max(0) as u64;
    let state = if now <= record.fetched_at + Duration::seconds(record.effective_ttl() as i64) {
        State::Ok
    } else {
        State::Stale
    };
    Status {
        provider: provider.to_string(),
        state,
        display_name: record.display_name.clone(),
        record: Some(record),
        age_seconds: Some(age_seconds),
        detail: None,
    }
}

/// Merge file-mode statuses with pull-mode statuses without duplication.
/// If a provider is actively fetched by pull-mode, pull-mode results take precedence.
pub fn merge_statuses(file_statuses: Vec<Status>, pull_statuses: Vec<Status>) -> Vec<Status> {
    let mut out = Vec::new();
    let pull_providers: std::collections::HashSet<String> = pull_statuses
        .iter()
        .filter(|s| s.state != State::Missing)
        .map(|s| s.provider.clone())
        .collect();

    // Add file records for providers not provided by live pull
    for fs in file_statuses {
        if !pull_providers.contains(&fs.provider) {
            out.push(fs);
        }
    }

    // Add pull records
    for ps in pull_statuses {
        if ps.state == State::Missing {
            let has_file = out.iter().any(|s| s.provider == ps.provider);
            if !has_file {
                out.push(ps);
            }
        } else {
            out.push(ps);
        }
    }

    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn claude_json(fetched_at: DateTime<Utc>, ttl_seconds: u64) -> String {
        format!(
            r#"{{"provider":"claude","kind":"window","used":1,"limit":10,"display_name":"Claude Pro","fetched_at":"{fetched_at}","ttl_seconds":{ttl_seconds},"source":"manual"}}"#,
            fetched_at = fetched_at.to_rfc3339(),
        )
    }

    #[test]
    fn fresh_record_is_ok() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("claude.json"), claude_json(Utc::now(), 600)).unwrap();

        let statuses = read_dir_status(dir.path());
        let s = statuses.iter().find(|s| s.provider == "claude").unwrap();
        assert_eq!(s.state, State::Ok);
        assert!(s.record.is_some());
        assert!(s.age_seconds.unwrap() < 60);
        assert_eq!(s.display_name.as_deref(), Some("Claude Pro"));
    }

    #[test]
    fn expired_record_is_stale() {
        let dir = tempdir().unwrap();
        let two_hours_ago = Utc::now() - Duration::hours(2);
        fs::write(dir.path().join("claude.json"), claude_json(two_hours_ago, 600)).unwrap();

        let statuses = read_dir_status(dir.path());
        let s = statuses.iter().find(|s| s.provider == "claude").unwrap();
        assert_eq!(s.state, State::Stale);
        assert!(s.age_seconds.unwrap() >= 7_200);
    }

    #[test]
    fn garbage_file_is_error() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("claude.json"), b"\x00not json at all {{{").unwrap();

        let statuses = read_dir_status(dir.path());
        let s = statuses.iter().find(|s| s.provider == "claude").unwrap();
        assert_eq!(s.state, State::Error);
        assert!(s.record.is_none());
        assert!(s.detail.as_deref().unwrap().contains("invalid record"));
    }

    #[test]
    fn balance_with_resets_at_is_error() {
        let dir = tempdir().unwrap();
        let json = r#"{"provider":"claude","kind":"balance","used":5,"limit":10,"resets_at":"2026-08-20T18:30:00Z","fetched_at":"2026-08-20T14:05:12Z","source":"api"}"#;
        fs::write(dir.path().join("claude.json"), json).unwrap();

        let statuses = read_dir_status(dir.path());
        let s = statuses.iter().find(|s| s.provider == "claude").unwrap();
        assert_eq!(s.state, State::Error);
        assert!(s.detail.is_some());
    }

    #[test]
    fn missing_known_file_provider_is_synthesized() {
        let dir = tempdir().unwrap();
        let now = Utc::now();
        let custom = format!(
            r#"{{"provider":"custom","kind":"window","used":1,"limit":10,"fetched_at":"{now}","source":"manual"}}"#
        );
        fs::write(dir.path().join("custom.json"), custom).unwrap();

        let statuses = read_dir_status(dir.path());
        let s = statuses.iter().find(|s| s.provider == "gemini").unwrap();
        assert_eq!(s.state, State::Missing);
        assert!(s.record.is_none());
        assert!(s.age_seconds.is_none());
        assert_eq!(s.display_name.as_deref(), Some("Google Gemini"));
    }

    #[test]
    fn multiple_files_may_share_a_provider() {
        let dir = tempdir().unwrap();
        let now = Utc::now();
        for (file, label) in [
            ("gemini-5h.json", "5h window"),
            ("gemini-weekly.json", "Weekly"),
        ] {
            let json = format!(
                r#"{{"provider":"gemini","kind":"window","used":5.0,"limit":100.0,"unit":"percent","label":"{label}","display_name":"Google Gemini","fetched_at":"{now}","source":"local-log"}}"#
            );
            fs::write(dir.path().join(file), json).unwrap();
        }

        let statuses = read_dir_status(dir.path());
        let gemini: Vec<&Status> = statuses.iter().filter(|s| s.provider == "gemini").collect();
        assert_eq!(gemini.len(), 2, "two records grouped under one provider");
        assert!(gemini.iter().all(|s| matches!(s.state, State::Ok)));
        let labels: Vec<Option<String>> = gemini
            .iter()
            .map(|s| s.record.as_ref().and_then(|r| r.label.clone()))
            .collect();
        assert!(labels.contains(&Some("5h window".to_string())));
        assert!(labels.contains(&Some("Weekly".to_string())));
        // The known provider is NOT synthesized as Missing when it has records.
        assert!(!statuses
            .iter()
            .any(|s| s.provider == "gemini" && matches!(s.state, State::Missing)));
    }

    #[test]
    fn absent_dir_yields_all_file_providers_missing() {
        let statuses = read_dir_status(Path::new("/nonexistent-ai-quotas-dir"));
        let file_mode: Vec<&Status> = statuses.iter().filter(|s| s.state == State::Missing).collect();
        assert_eq!(file_mode.len(), 1, "one Missing per known file-mode provider");
        assert!(statuses.iter().all(|s| s.state == State::Missing));
        assert!(file_mode.iter().any(|s| s.provider == "gemini"));
        // Pull-mode providers are never synthesized as Missing.
        assert!(statuses.iter().all(|s| s.provider != "deepseek" && s.provider != "zai" && s.provider != "claude" && s.provider != "chatgpt" && s.provider != "opencode"));
    }

    #[test]
    fn non_json_files_are_ignored() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("claude.txt"), "garbage").unwrap();
        let statuses = read_dir_status(dir.path());
        assert!(statuses.iter().all(|s| s.state == State::Missing));
    }
}
