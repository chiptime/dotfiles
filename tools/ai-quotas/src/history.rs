//! Append-only JSONL advice history: one file per UTC day under
//! `<state>/history/`. The server is the sole writer (single :47623 binder);
//! `stamp` never writes here. Paths are built only from the UTC date —
//! provider ids never reach the filesystem (R8).

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};

use crate::balancer::Advice;

/// Hard per-line cap: one serialized advice line stays under 4 KiB (R8).
const MAX_LINE_BYTES: usize = 4096;

/// `<state_dir>/history/YYYY-MM-DD.jsonl` for the UTC date of `at`.
pub fn history_path(state_dir: &Path, at: DateTime<Utc>) -> PathBuf {
    state_dir.join("history").join(format!("{}.jsonl", at.format("%Y-%m-%d")))
}

/// Append one advice line to the day's history file (R8). Best effort: an
/// unreadable directory or an oversized line is skipped — history never
/// crashes advice serving.
pub fn append(state_dir: &Path, at: DateTime<Utc>, advice: &Advice) {
    let line = history_line(at, advice);
    if line.len() >= MAX_LINE_BYTES {
        return;
    }
    let _ = std::fs::create_dir_all(state_dir.join("history"));
    // O_APPEND keeps concurrent single-writer appends whole; the sole writer
    // is this server, but append mode is still the correct primitive.
    let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(history_path(state_dir, at))
    else {
        return;
    };
    let _ = file.write_all(line.as_bytes());
}

/// Serialize advice as one JSON line stamped with the append time.
fn history_line(at: DateTime<Utc>, advice: &Advice) -> String {
    let mut value = serde_json::to_value(advice).unwrap_or(serde_json::json!({}));
    if let serde_json::Value::Object(map) = &mut value {
        map.insert("recorded_at".into(), serde_json::json!(at.to_rfc3339()));
    }
    format!("{value}\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::balancer::{reason, Advice, ADVICE_SCHEMA};
    use chrono::TimeZone;
    use tempfile::tempdir;

    fn advice(requested: &str, recommended: &str) -> Advice {
        Advice {
            schema: ADVICE_SCHEMA,
            requested_model: requested.into(),
            tier: "frontier".into(),
            recommended_model: recommended.into(),
            switch: false,
            reason: reason::TIER_UNKNOWN,
            advice_age_seconds: 0,
            requested_remaining: None,
            recommended_remaining: None,
        }
    }

    fn at() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 8, 26, 14, 3, 0).unwrap()
    }

    /// Every file under `root` as relative paths, to detect escapes.
    fn all_files(root: &Path) -> Vec<String> {
        fn walk(dir: &Path, prefix: String, out: &mut Vec<String>) {
            for entry in std::fs::read_dir(dir).unwrap() {
                let entry = entry.unwrap();
                let rel = format!("{prefix}{}", entry.file_name().to_string_lossy());
                if entry.path().is_dir() {
                    walk(&entry.path(), format!("{rel}/"), out);
                } else {
                    out.push(rel);
                }
            }
        }
        let mut out = Vec::new();
        walk(root, String::new(), &mut out);
        out.sort();
        out
    }

    #[test]
    fn traversal_providers_stay_inside_the_daily_history_file() {
        let dir = tempdir().unwrap();
        append(dir.path(), at(), &advice("../../../etc/passwd", "a/b"));
        assert_eq!(
            all_files(dir.path()),
            vec!["history/2026-08-26.jsonl".to_string()],
            "provider ids containing ../ or / must never reach the filesystem path"
        );
        let body =
            std::fs::read_to_string(dir.path().join("history/2026-08-26.jsonl")).unwrap();
        let line: serde_json::Value = serde_json::from_str(body.trim_end()).unwrap();
        assert_eq!(line["requested_model"], "../../../etc/passwd", "id stays data");
    }

    #[test]
    fn one_line_per_advice_with_daily_rotation() {
        let dir = tempdir().unwrap();
        append(dir.path(), at(), &advice("zai/glm-4.7", "zai/glm-4.7"));
        append(dir.path(), at(), &advice("zai/glm-4.7", "zai/glm-4.7"));
        let later = Utc.with_ymd_and_hms(2026, 8, 27, 1, 0, 0).unwrap();
        append(dir.path(), later, &advice("claude/x", "claude/x"));

        let day1 =
            std::fs::read_to_string(dir.path().join("history/2026-08-26.jsonl")).unwrap();
        assert_eq!(day1.lines().count(), 2, "O_APPEND: one line per advice");
        let first: serde_json::Value = serde_json::from_str(day1.lines().next().unwrap()).unwrap();
        assert_eq!(first["schema"], "ai-quotas/balancer-advice@1");
        assert!(first["recorded_at"].as_str().unwrap().starts_with("2026-08-26T14:03"));
        assert!(dir.path().join("history/2026-08-27.jsonl").is_file(), "daily rotation");
    }

    #[test]
    fn unreadable_state_dir_and_oversized_lines_are_skipped() {
        let dir = tempdir().unwrap();
        // State dir path blocked by a regular file: create_dir_all cannot succeed.
        std::fs::write(dir.path().join("blocker"), b"x").unwrap();
        append(&dir.path().join("blocker"), at(), &advice("zai/glm-4.7", "zai/glm-4.7"));
        // Oversized (>= 4 KiB) advice lines are skipped whole, never truncated.
        append(dir.path(), at(), &advice(&"x".repeat(5000), "zai/glm-4.7"));
        assert!(!dir.path().join("history").exists());
    }
}
