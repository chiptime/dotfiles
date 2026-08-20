//! CLI: `serve` (default) runs the local dashboard, `stamp` writes a record
//! file, `check` prints status.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use chrono::{DateTime, Utc};

use crate::reader;
use crate::schema::{Kind, Record, Source};
use crate::server;

/// Entry point. Returns the process exit code.
pub fn run<I>(args: I) -> i32
where
    I: IntoIterator<Item = String>,
{
    let mut args = args.into_iter();
    // No subcommand -> serve (the dashboard is the primary interface).
    let command = args.next().unwrap_or_else(|| "serve".to_string());
    let rest: Vec<String> = args.collect();
    match command.as_str() {
        "serve" => run_serve(rest),
        "stamp" => run_stamp(rest),
        "check" => run_check(),
        other => {
            eprintln!("error: unknown subcommand '{other}' (expected serve, stamp or check)");
            2
        }
    }
}

fn run_serve(rest: Vec<String>) -> i32 {
    if !rest.is_empty() {
        eprintln!("error: serve takes no arguments (port comes from AI_QUOTAS_PORT)");
        return 2;
    }
    let port = match server::parse_port(std::env::var("AI_QUOTAS_PORT").ok().as_deref()) {
        Ok(port) => port,
        Err(e) => {
            eprintln!("error: {e}");
            return 2;
        }
    };
    let manager = Arc::new(crate::providers::PullManager::from_env());
    let pull: server::PullFn = Arc::new(move |force| {
        if force {
            manager.fetch_force()
        } else {
            manager.fetch_cached()
        }
    });
    let bind = format!("127.0.0.1:{port}");
    match server::serve(&bind, reader::state_dir(), pull) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("error: {e}");
            1
        }
    }
}

fn run_stamp(args: Vec<String>) -> i32 {
    let parsed = match StampArgs::parse(args) {
        Ok(parsed) => parsed,
        Err(e) => {
            eprintln!("error: {e}");
            return 2;
        }
    };
    match stamp(&parsed, &reader::state_dir()) {
        Ok(path) => {
            println!("{}", path.display());
            0
        }
        Err(e) => {
            eprintln!("error: {e}");
            1
        }
    }
}

fn run_check() -> i32 {
    let file_statuses = reader::read_all();
    let pull_statuses = crate::providers::pull_all();
    let statuses = reader::merge_statuses(file_statuses, pull_statuses);
    for status in &statuses {
        print_status(status);
    }
    0
}

fn print_status(status: &reader::Status) {
    let percent = status
        .record
        .as_ref()
        .and_then(|r| r.used_percent())
        .map(|p| p.to_string())
        .unwrap_or_else(|| "-".to_string());
    let display_name = status
        .display_name
        .clone()
        .or_else(|| reader::known_display_name(&status.provider).map(str::to_string))
        .unwrap_or_else(|| "-".to_string());
    println!(
        "{}\t{}\t{}\t{}",
        status.provider,
        status.state.as_str(),
        percent,
        display_name
    );
    if let Some(detail) = &status.detail {
        eprintln!("! {}: {}", status.provider, detail);
    }
}

/// Parsed `stamp` arguments.
#[derive(Debug, Clone)]
pub struct StampArgs {
    provider: String,
    kind: Kind,
    source: Source,
    used: Option<f64>,
    limit: Option<f64>,
    unit: Option<String>,
    label: Option<String>,
    display_name: Option<String>,
    currency: Option<String>,
    resets_at: Option<DateTime<Utc>>,
    ttl: Option<u64>,
    file: Option<String>,
}

fn next_value<I>(it: &mut I, flag: &str) -> Result<String, String>
where
    I: Iterator<Item = String>,
{
    it.next().ok_or_else(|| format!("{flag} requires a value"))
}

fn parse_f64(flag: &str, raw: &str) -> Result<f64, String> {
    raw.parse::<f64>()
        .map_err(|e| format!("invalid value for {flag}: '{raw}' ({e})"))
}

fn parse_u64(flag: &str, raw: &str) -> Result<u64, String> {
    raw.parse::<u64>()
        .map_err(|e| format!("invalid value for {flag}: '{raw}' ({e})"))
}

fn parse_rfc3339(raw: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(raw)
        .map(|d| d.with_timezone(&Utc))
        .map_err(|e| format!("invalid RFC 3339 timestamp '{raw}' ({e})"))
}

impl StampArgs {
    fn parse(args: Vec<String>) -> Result<StampArgs, String> {
        let mut provider = None;
        let mut kind = None;
        let mut source = None;
        let mut used = None;
        let mut limit = None;
        let mut unit = None;
        let mut label = None;
        let mut display_name = None;
        let mut currency = None;
        let mut resets_at = None;
        let mut ttl = None;
        let mut file = None;

        let mut it = args.into_iter();
        while let Some(arg) = it.next() {
            match arg.as_str() {
                "--provider" => provider = Some(next_value(&mut it, "--provider")?),
                "--kind" => kind = Some(Kind::parse(&next_value(&mut it, "--kind")?)?),
                "--used" => used = Some(parse_f64("--used", &next_value(&mut it, "--used")?)?),
                "--limit" => limit = Some(parse_f64("--limit", &next_value(&mut it, "--limit")?)?),
                "--unit" => unit = Some(next_value(&mut it, "--unit")?),
                "--label" => label = Some(next_value(&mut it, "--label")?),
                "--display-name" => {
                    display_name = Some(next_value(&mut it, "--display-name")?)
                }
                "--currency" => currency = Some(next_value(&mut it, "--currency")?),
                "--resets-at" => {
                    resets_at = Some(parse_rfc3339(&next_value(&mut it, "--resets-at")?)?)
                }
                "--ttl" => ttl = Some(parse_u64("--ttl", &next_value(&mut it, "--ttl")?)?),
                "--source" => source = Some(Source::parse(&next_value(&mut it, "--source")?)?),
                "--file" => file = Some(next_value(&mut it, "--file")?),
                other => return Err(format!("unknown argument '{other}'")),
            }
        }

        let provider = provider.ok_or("missing required --provider")?;
        let kind = kind.ok_or("missing required --kind")?;
        let source = source.unwrap_or(Source::Manual);
        if kind == Kind::Balance && resets_at.is_some() {
            return Err("--resets-at is not valid for kind 'balance'".to_string());
        }

        Ok(StampArgs {
            provider,
            kind,
            source,
            used,
            limit,
            unit,
            label,
            display_name,
            currency,
            resets_at,
            ttl,
            file,
        })
    }
}

/// Reject file names that could escape the state dir or break the reader.
fn safe_file_name(name: &str) -> Result<(), String> {
    let invalid = name.is_empty()
        || name == "."
        || name == ".."
        || name.starts_with('.')
        || name.contains('/')
        || name.contains('\\')
        || name.contains('\0');
    if invalid {
        return Err(format!("invalid file name '{name}'"));
    }
    Ok(())
}

/// Build the record and write it atomically. Returns the final path.
pub fn stamp(args: &StampArgs, dir: &Path) -> Result<PathBuf, String> {
    let file_name = args
        .file
        .clone()
        .unwrap_or_else(|| format!("{}.json", args.provider));
    safe_file_name(&file_name)?;

    let mut record = Record::new(
        args.provider.clone(),
        args.kind,
        args.source,
        Utc::now(),
        args.ttl,
    );
    record.used = args.used;
    record.limit = args.limit;
    record.unit = args.unit.clone();
    record.label = args.label.clone();
    record.display_name = args.display_name.clone();
    record.currency = args.currency.clone();
    record.resets_at = args.resets_at;

    let json = serde_json::to_string_pretty(&record)
        .map_err(|e| format!("failed to serialize record: {e}"))?;
    atomic_write(dir, &file_name, &json)?;
    Ok(dir.join(&file_name))
}

/// Write via a temp file in the SAME directory, then rename over the target.
fn atomic_write(dir: &Path, file_name: &str, contents: &str) -> Result<(), String> {
    fs::create_dir_all(dir)
        .map_err(|e| format!("failed to create state dir {}: {e}", dir.display()))?;
    let target = dir.join(file_name);

    let mut last_error: Option<String> = None;
    for attempt in 0..5 {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let tmp = dir.join(format!(".{file_name}.tmp.{}.{attempt}", nanos % 1_000_000));
        match write_exclusive(&tmp, contents) {
            Ok(()) => {
                return fs::rename(&tmp, &target).map_err(|e| {
                    let _ = fs::remove_file(&tmp);
                    format!("failed to finalize {}: {e}", target.display())
                });
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                last_error = Some(e.to_string());
                continue;
            }
            Err(e) => return Err(format!("failed to write {}: {e}", tmp.display())),
        }
    }
    Err(format!(
        "failed to create a temp file for {file_name}: {}",
        last_error.unwrap_or_else(|| "unknown error".to_string())
    ))
}

fn write_exclusive(path: &Path, contents: &str) -> std::io::Result<()> {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    file.write_all(contents.as_bytes())?;
    file.sync_all()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::Source;
    use std::fs;
    use tempfile::tempdir;

    fn parse_args(args: &[&str]) -> Result<StampArgs, String> {
        StampArgs::parse(args.iter().map(|s| s.to_string()).collect())
    }

    #[test]
    fn stamp_writes_provider_json_and_roundtrips() {
        let dir = tempdir().unwrap();
        let args = parse_args(&[
            "--provider", "claude",
            "--kind", "window",
            "--used", "62",
            "--limit", "100",
            "--unit", "requests",
            "--label", "5h window",
            "--display-name", "Claude Pro",
            "--resets-at", "2026-08-20T18:30:00Z",
            "--ttl", "120",
        ])
        .unwrap();

        let path = stamp(&args, dir.path()).unwrap();
        assert_eq!(path, dir.path().join("claude.json"));
        assert!(path.is_file());

        let body = fs::read_to_string(&path).unwrap();
        let record: Record = serde_json::from_str(&body).unwrap();
        assert_eq!(record.provider, "claude");
        assert_eq!(record.kind, Kind::Window);
        assert_eq!(record.used, Some(62.0));
        assert_eq!(record.limit, Some(100.0));
        assert_eq!(record.unit.as_deref(), Some("requests"));
        assert_eq!(record.label.as_deref(), Some("5h window"));
        assert_eq!(record.display_name.as_deref(), Some("Claude Pro"));
        assert_eq!(record.ttl_seconds, Some(120));
        assert_eq!(record.source, Source::Manual);
        assert!(record.resets_at.is_some());
        assert!(record.fetched_at <= Utc::now());

        // Exactly one file: no temp leftovers.
        let entries: Vec<_> = fs::read_dir(dir.path()).unwrap().collect();
        assert_eq!(entries.len(), 1);
    }

    #[test]
    fn stamp_overwrites_and_still_leaves_no_temp_files() {
        let dir = tempdir().unwrap();
        let first = parse_args(&["--provider", "claude", "--kind", "daily", "--limit", "10"]).unwrap();
        stamp(&first, dir.path()).unwrap();
        let second = parse_args(&["--provider", "claude", "--kind", "daily", "--limit", "20"]).unwrap();
        stamp(&second, dir.path()).unwrap();

        let entries: Vec<_> = fs::read_dir(dir.path()).unwrap().collect();
        assert_eq!(entries.len(), 1);
        let body = fs::read_to_string(dir.path().join("claude.json")).unwrap();
        let record: Record = serde_json::from_str(&body).unwrap();
        assert_eq!(record.limit, Some(20.0));
    }

    #[test]
    fn stamp_creates_dir_and_reads_back_ok() {
        let dir = tempdir().unwrap();
        let nested = dir.path().join("state/nested");
        let args = parse_args(&["--provider", "zai", "--kind", "daily"]).unwrap();
        let path = stamp(&args, &nested).unwrap();
        assert!(path.is_file());

        let statuses = reader::read_dir_status(&nested);
        let s = statuses.iter().find(|s| s.provider == "zai").unwrap();
        assert_eq!(s.state, reader::State::Ok);
    }

    #[test]
    fn stamp_honors_file_override() {
        let dir = tempdir().unwrap();
        let args = parse_args(&[
            "--provider", "claude", "--kind", "daily", "--file", "custom.json",
        ])
        .unwrap();
        let path = stamp(&args, dir.path()).unwrap();
        assert_eq!(path, dir.path().join("custom.json"));
    }

    #[test]
    fn stamp_rejects_balance_with_resets_at() {
        assert!(parse_args(&[
            "--provider", "p", "--kind", "balance",
            "--resets-at", "2026-08-20T18:30:00Z",
        ])
        .is_err());
    }

    #[test]
    fn stamp_rejects_missing_required_args() {
        assert!(parse_args(&["--kind", "daily"]).is_err());
        assert!(parse_args(&["--provider", "p"]).is_err());
        assert!(parse_args(&[]).is_err());
    }

    #[test]
    fn stamp_rejects_bad_values() {
        assert!(parse_args(&["--provider", "p", "--kind", "weekly"]).is_err());
        assert!(parse_args(&["--provider", "p", "--kind", "daily", "--used", "abc"]).is_err());
        assert!(parse_args(&["--provider", "p", "--kind", "daily", "--ttl", "-5"]).is_err());
        assert!(parse_args(&[
            "--provider", "p", "--kind", "daily",
            "--resets-at", "not-a-timestamp",
        ])
        .is_err());
        // Unsafe file names are rejected at stamp time, not parse time.
        let dir = tempdir().unwrap();
        let evil = parse_args(&["--provider", "../evil", "--kind", "daily"]).unwrap();
        assert!(stamp(&evil, dir.path()).is_err());
        assert!(parse_args(&["--provider", "p", "--kind", "daily", "--file", "a/b.json"]).is_ok());
        let slash = parse_args(&[
            "--provider", "p", "--kind", "daily", "--file", "a/b.json",
        ])
        .unwrap();
        assert!(stamp(&slash, dir.path()).is_err());
    }
}
