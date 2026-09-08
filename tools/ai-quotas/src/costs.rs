//! Local cost scans: estimate real spend from LOCAL Codex and Claude usage
//! logs, priced via the models.dev catalog (parity with CodexBar's
//! "configurable local cost scans", ported to Linux).
//!
//! Data sources (verified against real corpora):
//! - Codex: `$CODEX_HOME/sessions` (default `~/.codex/sessions`) and
//!   `~/.codex/archived_sessions`, `rollout-*.jsonl` files. Lines with
//!   `payload.type == "token_count"` carry CUMULATIVE
//!   `info.total_token_usage`; per-event deltas come from a monotonic
//!   per-file watermark (a regression = session fork -> re-baseline, counting
//!   the current totals as one lump). The billed model comes from the most
//!   recent `turn_context.payload.model`; events seen before any
//!   `turn_context` are skipped rather than guessed. `cached_input_tokens`
//!   is a SUBSET of `input_tokens` (verified: input + output == total), so
//!   billable full-rate input = input - cached.
//! - Claude: `$CLAUDE_CONFIG_DIR/projects`, `~/.config/claude/projects` and
//!   `~/.claude/projects` (deduped), `*.jsonl` transcripts. Lines of type
//!   `assistant` with a `message.usage` object carry per-call classes
//!   (Anthropic reports cache tokens SEPARATELY from `input_tokens`).
//!   Streaming chunks are deduped by `(message.id, requestId)`, last wins.
//!
//! Pricing: models.dev `api.json` (USD per 1M tokens), cached on disk for
//! 24h with stale-on-failure fallback. Costs are accumulated as integer
//! micro-USD (u64) — money never rides in f64 accumulators.
//!
//! Incremental scans: per-file results are persisted in
//! `scan-offsets.json` keyed by (mtime, size); unchanged files are replayed
//! from the persisted token totals instead of being re-read. Hard budgets:
//! 256 MiB per file, 512 MiB per refresh, newest files first.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::RwLock;
use std::time::{Duration, Instant};

use chrono::{DateTime, Local, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::providers::{fetch_with_retry, http_agent, CACHE_TTL};

pub const CODEX: &str = "codex";
pub const CLAUDE: &str = "claude";
pub const OPENCODE: &str = "opencode";

/// Providers whose stored OpenCode cost is REAL money (pay-as-you-go API).
/// Everything else is treated as subscription-covered and merely valued at
/// catalog price. Configured in ~/.config/ai-stack/costs.json:
///   { "paid_providers": ["deepseek"] }
fn paid_providers() -> Vec<String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let base = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| Path::new(&home).join(".config"));
    let path = base.join("ai-stack/costs.json");
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|v| {
            serde_json::from_value::<Vec<String>>(v.get("paid_providers").cloned()?).ok()
        })
        .unwrap_or_default()
}

/// Local OpenCode SQLite database (WAL live db; opened READ-ONLY).
fn opencode_db_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    let base = std::env::var("XDG_DATA_HOME")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| Path::new(&home).join(".local/share"));
    let path = base.join("opencode/opencode.db");
    path.is_file().then_some(path)
}

/// Scan OpenCode spend straight from its local SQLite DB. OpenCode
/// pre-computes per-message cost (models.dev pricing applied at write time)
/// for BILLED usage; plan usage arrives with cost 0 and is valued at
/// catalog price by the caller. Returns one row per day x provider/model x
/// billing mode.
fn scan_opencode_db(path: &Path) -> Result<Vec<OpencodeRow>, String> {
    use rusqlite::OpenFlags;
    let conn = rusqlite::Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| format!("open {}: {e}", path.display()))?;
    let mut stmt = conn
        .prepare(
            "SELECT date(json_extract(data,'$.time.created')/1000,'unixepoch','localtime') d,
                    json_extract(data,'$.providerID') p,
                    json_extract(data,'$.modelID') m,
                    json_extract(data,'$.cost') > 0 b,
                    SUM(json_extract(data,'$.cost')) c,
                    SUM(json_extract(data,'$.tokens.input')),
                    SUM(json_extract(data,'$.tokens.output')),
                    SUM(json_extract(data,'$.tokens.reasoning')),
                    SUM(json_extract(data,'$.tokens.cache.read')),
                    SUM(json_extract(data,'$.tokens.cache.write'))
             FROM message
             WHERE json_extract(data,'$.role')='assistant'
               AND json_extract(data,'$.time.created') IS NOT NULL
             GROUP BY d, p, m, b"
        )
        .map_err(|e| format!("prepare: {e}"))?;
    let mut out: Vec<OpencodeRow> = Vec::new();
    let rows = stmt
        .query_map([], |row| {
            let day: String = row.get(0)?;
            let provider: String = row.get(1)?;
            let model: String = row.get(2)?;
            let billed: bool = row.get::<_, i64>(3)? != 0;
            let cost: f64 = row.get(4)?;
            let input: f64 = row.get(5).unwrap_or(0.0);
            let output: f64 = row.get(6).unwrap_or(0.0);
            let reasoning: f64 = row.get(7).unwrap_or(0.0);
            let cache_read: f64 = row.get(8).unwrap_or(0.0);
            let cache_write: f64 = row.get(9).unwrap_or(0.0);
            Ok((day, provider, model, billed, cost, input, output, reasoning, cache_read, cache_write))
        })
        .map_err(|e| format!("query: {e}"))?;
    for row in rows.flatten() {
        let (day, provider, model, billed, cost, input, output, reasoning, cache_read, cache_write) = row;
        // Reasoning tokens are billed as output by the providers.
        let tokens = TokenClasses {
            input: input as u64,
            cache_read: cache_read as u64,
            cache_write: cache_write as u64,
            output: (output + reasoning) as u64,
        };
        out.push(OpencodeRow {
            day,
            model: format!("{provider}/{model}"),
            billed_micros: if billed { (cost * 1_000_000.0).round() as u64 } else { 0 },
            covered_micros: 0, // valued by the caller via the pricing catalog
            tokens,
        });
    }
    Ok(out)
}

/// Hard cap for one log file (Codex rollouts can grow unbounded).
const MAX_FILE_BYTES: u64 = 256 * 1024 * 1024;
/// Hard cap for one full refresh across every provider.
const MAX_SCAN_BYTES: u64 = 512 * 1024 * 1024;
/// models.dev catalog TTL.
const PRICING_TTL_HOURS: i64 = 24;
const PRICING_URL: &str = "https://models.dev/api.json";

// ---------------------------------------------------------------------------
// Token classes and aggregation
// ---------------------------------------------------------------------------

/// The four billed token classes per model per day.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TokenClasses {
    pub input: u64,
    pub cache_read: u64,
    pub cache_write: u64,
    pub output: u64,
}

impl TokenClasses {
    fn is_zero(&self) -> bool {
        self.input == 0 && self.cache_read == 0 && self.cache_write == 0 && self.output == 0
    }

    fn add(&mut self, other: &TokenClasses) {
        self.input += other.input;
        self.cache_read += other.cache_read;
        self.cache_write += other.cache_write;
        self.output += other.output;
    }

    fn to_json(self) -> Value {
        json!({
            "input": self.input,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "output": self.output,
        })
    }
}

/// Signature of one file line scanner (Codex or Claude).
type LineScanner = fn(
    Box<dyn Iterator<Item = std::io::Result<Vec<u8>>> + Send>,
    &mut u64,
) -> (DayModelMap, u64);

/// One price row (USD per 1M tokens) from models.dev.
#[derive(Debug, Clone, Copy, Default)]
pub struct ModelPrice {
    pub input: f64,
    pub output: f64,
    pub cache_read: f64,
    pub cache_write: f64,
}

impl ModelPrice {
    /// Cost of one token bundle in integer micro-USD. Because prices are
    /// USD per 1M tokens, `tokens * price` IS micro-USD
    /// (USD = tokens * price / 1e6; micros = USD * 1e6). Rounded once.
    pub fn cost_micros(&self, t: &TokenClasses) -> u64 {
        let micros = t.input as f64 * self.input
            + t.cache_read as f64 * self.cache_read
            + t.cache_write as f64 * self.cache_write
            + t.output as f64 * self.output;
        micros.round() as u64
    }
}

/// Flat model-id -> price catalog built from the models.dev payload.
/// Keys are often provider-prefixed ("azure/gpt-5.5"), so a suffix index
/// (last path segment -> full key) backs the lookup.
#[derive(Debug, Clone, Default)]
pub struct PricingCatalog {
    prices: BTreeMap<String, ModelPrice>,
    by_suffix: BTreeMap<String, String>,
}

impl PricingCatalog {
    /// Rebuild a catalog from a flat price map (disk cache path), keeping
    /// the suffix index consistent with the keys.
    fn with_prices(prices: BTreeMap<String, ModelPrice>) -> Self {
        let by_suffix = prices
            .keys()
            .map(|k| (Self::last_segment(k), k.clone()))
            .collect();
        Self { prices, by_suffix }
    }

    /// Build from the raw models.dev `api.json` value. Returns an error when
    /// the payload fails the plausibility gate (must contain at least one
    /// priced model under BOTH the `anthropic` and `openai` slugs) — a
    /// truncated or redesigned payload must never silently zero out costs.
    pub fn from_api_json(v: &Value) -> Result<Self, String> {
        let Some(slugs) = v.as_object() else {
            return Err("models.dev payload is not an object".into());
        };
        let mut catalog = Self::default();
        let mut priced_slugs: BTreeSet<&str> = BTreeSet::new();
        for (slug, provider) in slugs {
            let Some(models) = provider.get("models").and_then(Value::as_object) else {
                continue;
            };
            for (id, model) in models {
                let Some(cost) = model.get("cost").and_then(Value::as_object) else {
                    continue;
                };
                let num = |key: &str| {
                    cost.get(key).and_then(Value::as_f64).unwrap_or(0.0)
                };
                let price = ModelPrice {
                    input: num("input"),
                    output: num("output"),
                    cache_read: num("cache_read"),
                    cache_write: num("cache_write"),
                };
                // First slug wins on id collisions; normalized lookups later.
                catalog
                    .prices
                    .entry(id.clone())
                    .or_insert(price);
                catalog
                    .by_suffix
                    .entry(Self::last_segment(id))
                    .or_insert_with(|| id.clone());
                priced_slugs.insert(slug.as_str());
            }
        }
        for required in ["anthropic", "openai"] {
            if !priced_slugs.contains(required) {
                return Err(format!(
                    "models.dev payload implausibility: no priced models under '{required}'"
                ));
            }
        }
        Ok(catalog)
    }

    /// Price lookup with model-id normalization: exact -> provider-prefix
    /// strip -> trailing `-YYYYMMDD` date strip -> suffix match (catalog
    /// keys are often provider-prefixed). Among candidates, entries with a
    /// positive price win: coding-plan slugs carry 0-cost duplicates of the
    /// same model and must not shadow the API price when valuing usage.
    pub fn lookup(&self, raw: &str) -> Option<ModelPrice> {
        let mut fallback: Option<ModelPrice> = None;
        // One candidate = exact key, then its suffix alias. A positive price
        // wins immediately; zero-priced duplicates (coding-plan slugs) are
        // kept only as a last-resort fallback.
        for candidate in normalize_candidates(raw) {
            if let Some(price) = self.prices.get(candidate.as_str()).copied() {
                if price.input + price.output + price.cache_read + price.cache_write > 0.0 {
                    return Some(price);
                }
                fallback = fallback.or(Some(price));
            }
            if let Some(key) = self.by_suffix.get(candidate.as_str()) {
                if let Some(price) = self.prices.get(key.as_str()).copied() {
                    if price.input + price.output + price.cache_read + price.cache_write > 0.0 {
                        return Some(price);
                    }
                    fallback = fallback.or(Some(price));
                }
            }
        }
        fallback
    }

    /// Last `/`-separated segment of a model key.
    fn last_segment(key: &str) -> String {
        key.rsplit('/').next().unwrap_or(key).to_string()
    }
}

/// Lookup candidates for one raw model id, best first.
fn normalize_candidates(raw: &str) -> Vec<String> {
    let mut out = vec![raw.to_string()];
    let stripped_prefix = raw
        .split_once('/')
        .map(|(_, rest)| rest)
        .unwrap_or(raw)
        .to_string();
    if stripped_prefix != raw {
        out.push(stripped_prefix.clone());
    }
    let strip_date = |s: &str| -> Option<String> {
        if s.len() <= 9 {
            return None;
        }
        let (head, tail) = s.split_at(s.len() - 9);
        let is_dated = tail.starts_with('-') && tail[1..].bytes().all(|b| b.is_ascii_digit());
        is_dated.then(|| head.to_string())
    };
    let mut probe = raw.to_string();
    // Allow ids carrying BOTH a prefix and a date suffix.
    for _ in 0..2 {
        if let Some(stripped) = strip_date(&probe) {
            out.push(stripped.clone());
            probe = stripped;
        } else {
            break;
        }
    }
    if let Some(stripped) = strip_date(&stripped_prefix) {
        out.push(stripped);
    }
    out
}

// ---------------------------------------------------------------------------
// Per-file scan results (persisted incremental state)
// ---------------------------------------------------------------------------

/// day -> model -> token classes contributed by ONE file.
type DayModelMap = BTreeMap<String, BTreeMap<String, TokenClasses>>;

/// Persisted scan result for one log file. Unchanged files (same mtime +
/// size) replay these totals instead of being re-read.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileEntry {
    provider: String,
    mtime_secs: i64,
    mtime_nanos: u32,
    size: u64,
    days: DayModelMap,
}

impl FileEntry {
    fn matches(&self, meta: &std::fs::Metadata) -> bool {
        let (secs, nanos) = mtime_of(meta);
        self.mtime_secs == secs && self.mtime_nanos == nanos && self.size == meta.len()
    }
}

fn mtime_of(meta: &std::fs::Metadata) -> (i64, u32) {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| (d.as_secs() as i64, d.subsec_nanos()))
        .unwrap_or((0, 0))
}

/// On-disk incremental state (`scan-offsets.json`).
#[derive(Debug, Default, Serialize, Deserialize)]
struct OffsetStore {
    files: BTreeMap<String, FileEntry>,
}

fn load_offsets(path: &Path) -> OffsetStore {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn save_offsets(path: &Path, store: &OffsetStore) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(body) = serde_json::to_vec(store) {
        let tmp = path.with_extension("json.tmp");
        if std::fs::write(&tmp, &body).is_ok() {
            let _ = std::fs::rename(&tmp, path);
        }
    }
}

// ---------------------------------------------------------------------------
// Scanners (pure over line iterators -> testable)
// ---------------------------------------------------------------------------

/// One cumulative token-usage snapshot inside a Codex rollout.
#[derive(Debug, Clone, Copy)]
struct CodexCumulative {
    input: u64,
    cached: u64,
    output: u64,
}

/// Fold one Codex `token_count` event into the watermark. `total_token_usage`
/// is cumulative per file; a regression in any component means a session
/// fork — re-baseline by counting the current totals as one lump.
fn codex_delta(
    prev: &mut Option<CodexCumulative>,
    cur: CodexCumulative,
) -> (u64, u64, u64) {
    let (delta_input, delta_cached, delta_output) = match prev {
        Some(p) if cur.input >= p.input && cur.cached >= p.cached && cur.output >= p.output => {
            (
                cur.input - p.input,
                cur.cached - p.cached,
                cur.output - p.output,
            )
        }
        // Fork/reset: the current snapshot is the whole new lineage so far.
        _ => (cur.input, cur.cached, cur.output),
    };
    *prev = Some(cur);
    (delta_input, delta_cached, delta_output)
}

/// Scan one Codex rollout file, honoring the remaining byte budget.
/// Returns (day -> model -> classes, bytes_read). Events without a billed
/// model (no `turn_context` seen yet) are skipped, never guessed.
fn scan_codex_lines(
    lines: impl Iterator<Item = std::io::Result<Vec<u8>>>,
    budget: &mut u64,
) -> (DayModelMap, u64) {
    let mut days = DayModelMap::new();
    let mut model: Option<String> = None;
    let mut prev: Option<CodexCumulative> = None;
    let mut read = 0u64;
    for line in lines {
        let Ok(bytes) = line else { break };
        read += bytes.len() as u64;
        if *budget <= read {
            break; // partial file: consistent as long as mtime+size hold
        }
        let Ok(rec) = serde_json::from_slice::<Value>(&bytes) else {
            continue;
        };
        match rec.get("type").and_then(Value::as_str) {
            Some("turn_context") => {
                if let Some(name) = rec
                    .get("payload")
                    .and_then(|p| p.get("model"))
                    .and_then(Value::as_str)
                {
                    model = Some(name.to_string());
                }
            }
            Some("event_msg") => {
                let payload = rec.get("payload");
                let is_count = payload
                    .and_then(|p| p.get("type"))
                    .and_then(Value::as_str)
                    == Some("token_count");
                if !is_count {
                    continue;
                }
                let Some(info) = payload.and_then(|p| p.get("info")).filter(|v| !v.is_null())
                else {
                    continue;
                };
                let Some(tot) = info.get("total_token_usage") else {
                    continue;
                };
                let num = |key: &str| {
                    tot.get(key).and_then(Value::as_u64).unwrap_or(0)
                };
                let cur = CodexCumulative {
                    input: num("input_tokens"),
                    cached: num("cached_input_tokens"),
                    output: num("output_tokens"),
                };
                let (d_in, d_cached, d_out) = codex_delta(&mut prev, cur);
                // cached is a subset of input: bill the remainder at full rate.
                let classes = TokenClasses {
                    input: d_in.saturating_sub(d_cached),
                    cache_read: d_cached,
                    cache_write: 0,
                    output: d_out,
                };
                if classes.is_zero() {
                    continue;
                }
                let Some(billed) = model.clone() else {
                    continue; // no turn_context yet: skip rather than guess
                };
                if let Some(day) = day_key(rec.get("timestamp").and_then(Value::as_str)) {
                    days.entry(day)
                        .or_default()
                        .entry(billed)
                        .or_default()
                        .add(&classes);
                }
            }
            _ => {}
        }
    }
    (days, read.min(*budget))
}

/// Scan one Claude transcript file. Streaming chunks of the same API call
/// repeat `(message.id, requestId)`; the LAST occurrence wins (it carries the
/// final usage totals). Returns (day -> model -> classes, bytes_read).
fn scan_claude_lines(
    lines: impl Iterator<Item = std::io::Result<Vec<u8>>>,
    budget: &mut u64,
) -> (DayModelMap, u64) {
    // key -> (model, day, classes); insertion order irrelevant, last wins.
    let mut calls: HashMap<(String, String), (String, String, TokenClasses)> = HashMap::new();
    let mut read = 0u64;
    for line in lines {
        let Ok(bytes) = line else { break };
        read += bytes.len() as u64;
        if *budget <= read {
            break;
        }
        // Cheap pre-filter before parsing: "assistant" must appear somewhere.
        // Spaced and compact `"type"` spellings both pass; the strict type
        // check after the parse rejects text that merely mentions the word.
        if !bytes.windows(b"assistant".len()).any(|w| w == b"assistant") {
            continue;
        }
        let Ok(rec) = serde_json::from_slice::<Value>(&bytes) else {
            continue;
        };
        if rec.get("type").and_then(Value::as_str) != Some("assistant") {
            continue;
        }
        let message = rec.get("message");
        let Some(usage) = message.and_then(|m| m.get("usage")) else {
            continue;
        };
        let Some(model) = message
            .and_then(|m| m.get("model"))
            .and_then(Value::as_str)
            .map(str::to_string)
        else {
            continue;
        };
        let num = |key: &str| usage.get(key).and_then(Value::as_u64).unwrap_or(0);
        let classes = TokenClasses {
            input: num("input_tokens"),
            cache_read: num("cache_read_input_tokens"),
            cache_write: num("cache_creation_input_tokens"),
            output: num("output_tokens"),
        };
        if classes.is_zero() {
            continue; // synthetic/error chunks contribute nothing
        }
        let Some(day) = day_key(rec.get("timestamp").and_then(Value::as_str)) else {
            continue;
        };
        let msg_id = message
            .and_then(|m| m.get("id"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let req_id = rec
            .get("requestId")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        calls.insert((msg_id, req_id), (model, day, classes));
    }
    let mut days = DayModelMap::new();
    for (_, (model, day, classes)) in calls {
        days.entry(day).or_default().entry(model).or_default().add(&classes);
    }
    (days, read.min(*budget))
}

/// Local calendar day key ("YYYY-MM-DD") for an RFC 3339 timestamp.
fn day_key(raw: Option<&str>) -> Option<String> {
    let ts = DateTime::parse_from_rfc3339(raw?).ok()?;
    Some(ts.with_timezone(&Local).date_naive().to_string())
}

// ---------------------------------------------------------------------------
// Cost scan result
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Default)]
pub struct DayTotals {
    pub cost_micros: u64,
    /// Part of `cost_micros` actually BILLED (stored cost > 0 in OpenCode).
    /// The remainder was covered by a subscription and valued at catalog.
    pub billed_micros: u64,
    pub tokens: TokenClasses,
}

/// One day x provider/model row as exposed by /api/costs (drives client-side
/// range filtering, provider accordions and the billed/covered split).
#[derive(Debug, Clone)]
pub struct OpencodeRow {
    pub day: String,
    pub model: String,
    pub billed_micros: u64,
    pub covered_micros: u64,
    pub tokens: TokenClasses,
}

#[derive(Debug, Clone, Default)]
pub struct ProviderCosts {
    pub days: BTreeMap<String, DayTotals>,
    pub models: BTreeMap<String, DayTotals>,
    /// Month -> model -> totals (drives the per-provider / per-model
    /// month-to-date breakdown in the dashboard and menus).
    pub months: BTreeMap<String, BTreeMap<String, DayTotals>>,
    pub unpriced_models: BTreeSet<String>,
}

impl ProviderCosts {
    /// Attribute one day x model contribution into days/models/months.
    fn attribute(
        &mut self,
        day: &str,
        model: &str,
        tokens: &TokenClasses,
        cost_micros: u64,
        billed_micros: u64,
    ) {
        let apply = |t: &mut DayTotals| {
            t.tokens.add(tokens);
            t.cost_micros += cost_micros;
            t.billed_micros += billed_micros;
        };
        apply(self.days.entry(day.to_string()).or_default());
        apply(self.models.entry(model.to_string()).or_default());
        let month = &day[..day.len().min(7)];
        apply(
            self.months
                .entry(month.to_string())
                .or_default()
                .entry(model.to_string())
                .or_default(),
        );
    }
}

/// One completed scan across every provider.
#[derive(Debug, Clone)]
pub struct CostScan {
    pub generated_at: DateTime<Utc>,
    pub providers: BTreeMap<String, ProviderCosts>,
    /// Day x provider/model x billing-mode rows for the OpenCode scan,
    /// exposed via /api/costs so the dashboard can recompute any range.
    pub opencode_rows: Vec<OpencodeRow>,
    pub pricing_stale: bool,
    pub pricing_error: Option<String>,
    pub stats: String,
}

impl CostScan {
    /// USD figures for `check --json`: (today, month-to-date), both null
    /// when the provider has no scan data.
    pub fn check_fields(&self, provider: &str) -> (Value, Value) {
        let Some(p) = self.providers.get(provider) else {
            return (Value::Null, Value::Null);
        };
        let today = Local::now().date_naive().to_string();
        let month_start = &today[..7];
        let today_usd = p
            .days
            .get(&today)
            .map(|d| micros_usd(d.cost_micros));
        let mtd_usd: f64 = p
            .days
            .iter()
            .filter(|(day, _)| day.as_str() >= month_start)
            .map(|(_, d)| d.cost_micros)
            .sum::<u64>() as f64
            / 1_000_000.0;
        (
            today_usd.map(|v| json!(v)).unwrap_or(Value::Null),
            json!(round4(mtd_usd)),
        )
    }

    /// `/api/costs` payload. Providers without any data are omitted so the
    /// dashboard can degrade gracefully. `only` restricts which scan
    /// providers are exposed (visibility policy: opencode only, today).
    pub fn to_json_only(&self, only: &[&str]) -> Value {
        let mut filtered = self.clone();
        filtered.providers.retain(|id, _| only.contains(&id.as_str()));
        let mut body = filtered.to_json();
        if only.contains(&OPENCODE) {
            let rows: Vec<Value> = self
                .opencode_rows
                .iter()
                .map(|r| {
                    json!({
                        "d": r.day,
                        "m": r.model,
                        "billed": round4(micros_usd(r.billed_micros)),
                        "covered": round4(micros_usd(r.covered_micros)),
                    })
                })
                .collect();
            if let Some(oc) = body
                .get_mut("providers")
                .and_then(|p| p.get_mut(OPENCODE))
            {
                oc["rows"] = Value::Array(rows);
            }
        }
        body
    }

    /// `/api/costs` payload. Providers without any data are omitted so the
    /// dashboard can degrade gracefully.
    pub fn to_json(&self) -> Value {
        let now = Local::now().date_naive();
        let today = now.to_string();
        let month_start = &today[..7];
        let mut providers = Map::new();
        for (id, p) in &self.providers {
            if p.days.is_empty() {
                continue;
            }
            let days: Map<String, Value> = p
                .days
                .iter()
                .map(|(day, t)| {
                    (
                        day.clone(),
                        json!({ "usd": round4(micros_usd(t.cost_micros)), "tokens": t.tokens.to_json() }),
                    )
                })
                .collect();
            let models: Map<String, Value> = p
                .models
                .iter()
                .map(|(model, t)| {
                    (
                        model.clone(),
                        json!({ "usd": round4(micros_usd(t.cost_micros)), "tokens": t.tokens.to_json() }),
                    )
                })
                .collect();
            let months: Map<String, Value> = p
                .months
                .iter()
                .map(|(month, models)| {
                    let per_model: Map<String, Value> = models
                        .iter()
                        .map(|(model, t)| (model.clone(), json!(round4(micros_usd(t.cost_micros)))))
                        .collect();
                    (month.clone(), Value::Object(per_model))
                })
                .collect();
            let today_usd = p.days.get(&today).map(|d| micros_usd(d.cost_micros));
            let mtd: u64 = p
                .days
                .iter()
                .filter(|(day, _)| day.as_str() >= month_start)
                .map(|(_, d)| d.cost_micros)
                .sum();
            providers.insert(
                id.clone(),
                json!({
                    "today_usd": today_usd.map(round4),
                    "month_to_date_usd": round4(micros_usd(mtd)),
                    "days": days,
                    "models": models,
                    "months": months,
                    "unpriced_models": p.unpriced_models.iter().collect::<Vec<_>>(),
                }),
            );
        }
        let mut body = json!({
            "generated_at": self.generated_at.to_rfc3339(),
            "providers": providers,
        });
        if self.pricing_stale || self.pricing_error.is_some() {
            body["pricing"] = json!({
                "stale": self.pricing_stale,
                "error": self.pricing_error,
            });
        }
        body
    }
}

fn micros_usd(micros: u64) -> f64 {
    micros as f64 / 1_000_000.0
}

fn round4(v: f64) -> f64 {
    (v * 10_000.0).round() / 10_000.0
}

// ---------------------------------------------------------------------------
// Pricing storage (disk cache + network refresh)
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize)]
struct PricingCache {
    fetched_at: DateTime<Utc>,
    prices: BTreeMap<String, ModelPrice>,
}

// serde needs helper impls for ModelPrice inside PricingCache:
mod price_serde {
    use super::ModelPrice;
    use serde::{Deserialize, Deserializer, Serialize, Serializer};

    #[derive(Serialize, Deserialize)]
    struct Row {
        input: f64,
        output: f64,
        cache_read: f64,
        cache_write: f64,
    }

    impl Serialize for ModelPrice {
        fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
            Row {
                input: self.input,
                output: self.output,
                cache_read: self.cache_read,
                cache_write: self.cache_write,
            }
            .serialize(s)
        }
    }

    impl<'de> Deserialize<'de> for ModelPrice {
        fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
            let row = Row::deserialize(d)?;
            Ok(Self {
                input: row.input,
                output: row.output,
                cache_read: row.cache_read,
                cache_write: row.cache_write,
            })
        }
    }
}

// ---------------------------------------------------------------------------
// The scanner service (TTL-cached like StatusPoller)
// ---------------------------------------------------------------------------

type PricingFetch = Box<dyn Fn(&ureq::Agent) -> Result<Value, String> + Send + Sync>;

#[derive(Default)]
struct CacheState {
    last_fetch: Option<Instant>,
    cached: Option<std::sync::Arc<CostScan>>,
}

/// TTL-cached local cost scanner. Like `StatusPoller`, a refresh runs at most
/// once per TTL window and the scan never hard-fails: missing roots yield an
/// empty scan, pricing failures degrade to unpriced token counts.
pub struct CostScanner {
    roots: BTreeMap<&'static str, Vec<PathBuf>>,
    cache_dir: PathBuf,
    ttl: Duration,
    pricing_fetch: PricingFetch,
    state: RwLock<CacheState>,
}

impl CostScanner {
    /// Scanner over the real local roots with the default models.dev fetch.
    pub fn from_env() -> Self {
        let home = std::env::var("HOME").unwrap_or_default();
        let mut roots: BTreeMap<&'static str, Vec<PathBuf>> = BTreeMap::new();

        let codex_home = std::env::var("CODEX_HOME")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(&home).join(".codex"));
        roots.insert(
            CODEX,
            vec![codex_home.join("sessions"), codex_home.join("archived_sessions")],
        );

        let mut claude_roots = Vec::new();
        if let Some(dir) = std::env::var("CLAUDE_CONFIG_DIR")
            .ok()
            .filter(|v| !v.trim().is_empty())
        {
            claude_roots.push(Path::new(&dir).join("projects"));
        }
        claude_roots.push(Path::new(&home).join(".config/claude/projects"));
        claude_roots.push(Path::new(&home).join(".claude/projects"));
        roots.insert(CLAUDE, claude_roots);

        let cache_dir = std::env::var("XDG_CACHE_HOME")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(&home).join(".cache"))
            .join("ai-quotas");

        Self {
            roots,
            cache_dir,
            ttl: CACHE_TTL,
            pricing_fetch: Box::new(default_pricing_fetch),
            state: RwLock::new(CacheState::default()),
        }
    }

    #[cfg_attr(not(test), allow(dead_code))]
    /// Test constructor: explicit roots (possibly empty) and cache dir, with
    /// a pricing fetcher that always fails (place a fresh
    /// `models-dev.json` fixture in `cache_dir` to price deterministically).
    pub fn with_roots(roots: BTreeMap<&'static str, Vec<PathBuf>>, cache_dir: PathBuf) -> Self {
        Self {
            roots,
            cache_dir,
            ttl: CACHE_TTL,
            pricing_fetch: Box::new(|_| Err("network disabled in tests".into())),
            state: RwLock::new(CacheState::default()),
        }
    }

    /// Current scan, refreshed at most once per TTL window.
    pub fn fetch(&self) -> std::sync::Arc<CostScan> {
        {
            let guard = self.state.read().unwrap_or_else(|p| p.into_inner());
            if let Some(scan) = fresh(&guard, self.ttl) {
                return scan;
            }
        }
        let mut guard = self.state.write().unwrap_or_else(|p| p.into_inner());
        if let Some(scan) = fresh(&guard, self.ttl) {
            return scan;
        }
        let scan = std::sync::Arc::new(self.refresh());
        guard.cached = Some(std::sync::Arc::clone(&scan));
        guard.last_fetch = Some(Instant::now());
        scan
    }

    /// Full refresh: incremental file scan + pricing + aggregation.
    fn refresh(&self) -> CostScan {
        let started = Instant::now();
        let offsets_path = self.cache_dir.join("scan-offsets.json");
        let mut store = load_offsets(&offsets_path);

        // Discover current files per provider (newest first).
        let mut discovered: BTreeMap<&'static str, Vec<(PathBuf, std::fs::Metadata)>> =
            BTreeMap::new();
        for (provider, roots) in &self.roots {
            let mut files = Vec::new();
            for root in roots {
                collect_jsonl(root, 0, &mut files);
            }
            files.sort_by_key(|f| std::cmp::Reverse(mtime_of(&f.1)));
            discovered.insert(provider, files);
        }

        // Scan, reusing unchanged files from the offset store. The provider
        // comes from the root table key, not the file name.
        let mut budget = MAX_SCAN_BYTES;
        let mut files_scanned = 0u64;
        let mut bytes_read = 0u64;
        let mut keep: BTreeSet<String> = BTreeSet::new();
        for (provider, files) in &discovered {
            let scanner_fn: LineScanner = if *provider == CODEX {
                scan_codex_lines
            } else {
                scan_claude_lines
            };
            for (path, meta) in files {
                let key = path.to_string_lossy().into_owned();
                keep.insert(key.clone());
                if let Some(entry) = store.files.get(&key) {
                    if entry.matches(meta) {
                        continue;
                    }
                }
                if budget == 0 {
                    continue; // refresh budget exhausted; old entries persist
                }
                let file_budget = budget.min(MAX_FILE_BYTES);
                if let Ok((days, read)) = scan_path(path, file_budget, scanner_fn) {
                    let (secs, nanos) = mtime_of(meta);
                    store.files.insert(
                        key,
                        FileEntry {
                            provider: provider.to_string(),
                            mtime_secs: secs,
                            mtime_nanos: nanos,
                            size: meta.len(),
                            days,
                        },
                    );
                    files_scanned += 1;
                    bytes_read += read;
                    budget = budget.saturating_sub(read);
                } // unreadable file: keep prior entry untouched
            }
        }
        store.files.retain(|path, _| keep.contains(path));
        save_offsets(&offsets_path, &store);

        // Pricing: disk cache first (24h TTL), network refresh, stale fallback.
        let (catalog, pricing_stale, pricing_error) = self.load_pricing();

        // Fold per-provider totals and price them.
        let mut providers: BTreeMap<String, ProviderCosts> = BTreeMap::new();
        for entry in store.files.values() {
            let out = providers.entry(entry.provider.clone()).or_default();
            for (day, models) in &entry.days {
                for (model, classes) in models {
                    match catalog.lookup(model) {
                        Some(price) => {
                            let micros = price.cost_micros(classes);
                            out.attribute(day, model, classes, micros, micros)
                        }
                        None => {
                            out.attribute(day, model, classes, 0, 0);
                            out.unpriced_models.insert(model.clone());
                        }
                    }
                }
            }
        }

        let pricing_state = if pricing_error.is_some() {
            "unavailable"
        } else if pricing_stale {
            "stale"
        } else {
            "fresh"
        };
        // OpenCode: billed usage carries a precomputed cost; plan usage
        // arrives with cost 0 and is valued at catalog price for a coherent
        // comparison with the file scanners. Failure degrades to absent.
        let mut opencode_rows: Vec<OpencodeRow> = Vec::new();
        if let Some(db) = opencode_db_path() {
            match scan_opencode_db(&db) {
                Ok(rows) => {
                    let paid = paid_providers();
                    let mut out = ProviderCosts::default();
                    for mut row in rows {
                        // Billing truth is CONFIG, not the stored cost: plan
                        // providers (anthropic, zai, openai, opencode-go...)
                        // get their usage valued as covered; pay-as-you-go
                        // providers keep the stored cost as real money.
                        let provider = row.model.split('/').next().unwrap_or("");
                        if paid.iter().any(|p| provider == p.as_str()) {
                            row.covered_micros = 0;
                        } else {
                            row.covered_micros = if row.billed_micros > 0 {
                                row.billed_micros
                            } else {
                                match catalog.lookup(&row.model) {
                                    Some(price) => price.cost_micros(&row.tokens),
                                    None => {
                                        out.unpriced_models.insert(row.model.clone());
                                        0
                                    }
                                }
                            };
                            row.billed_micros = 0;
                        }
                        out.attribute(
                            &row.day,
                            &row.model,
                            &row.tokens,
                            row.billed_micros + row.covered_micros,
                            row.billed_micros,
                        );
                        opencode_rows.push(row);
                    }
                    providers.insert(OPENCODE.to_string(), out);
                }
                Err(e) => eprintln!("ai-quotas costs: opencode scan failed: {e}"),
            }
        }

        let scan = CostScan {
            generated_at: Utc::now(),
            providers,
            opencode_rows,
            pricing_stale,
            pricing_error,
            stats: format!(
                "{} files scanned, {} MiB read, pricing {}",
                files_scanned,
                bytes_read / (1024 * 1024),
                pricing_state,
            ),
        };
        eprintln!(
            "ai-quotas costs scan: {} ({}ms)",
            scan.stats,
            started.elapsed().as_millis()
        );
        scan
    }

    /// Load the pricing catalog: fresh disk cache wins; otherwise try the
    /// network and rewrite the cache; on failure fall back to any stale
    /// cache (flagged) or an empty catalog (flagged + error text).
    fn load_pricing(&self) -> (PricingCatalog, bool, Option<String>) {
        let path = self.cache_dir.join("models-dev.json");
        let cached = std::fs::read(&path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<PricingCache>(&bytes).ok());
        if let Some(cache) = &cached {
            if (Utc::now() - cache.fetched_at) < chrono::Duration::hours(PRICING_TTL_HOURS) {
                return (
                    PricingCatalog::with_prices(cache.prices.clone()),
                    false,
                    None,
                );
            }
        }
        match (self.pricing_fetch)(&http_agent()) {
            Ok(payload) => match PricingCatalog::from_api_json(&payload) {
                Ok(catalog) => {
                    if let Some(parent) = path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    let cache = PricingCache {
                        fetched_at: Utc::now(),
                        prices: catalog.prices.clone(),
                    };
                    if let Ok(body) = serde_json::to_vec(&cache) {
                        let _ = std::fs::write(&path, &body);
                    }
                    (catalog, false, None)
                }
                Err(e) => stale_or_empty(cached, &path, e),
            },
            Err(e) => stale_or_empty(cached, &path, e),
        }
    }
}

fn fresh(state: &CacheState, ttl: Duration) -> Option<std::sync::Arc<CostScan>> {
    let at = state.last_fetch?;
    let cached = state.cached.as_ref()?;
    (at.elapsed() < ttl).then(|| std::sync::Arc::clone(cached))
}

fn stale_or_empty(
    cached: Option<PricingCache>,
    path: &Path,
    error: String,
) -> (PricingCatalog, bool, Option<String>) {
    match cached {
        Some(cache) => (
            PricingCatalog::with_prices(cache.prices),
            true,
            Some(format!("pricing refresh failed ({error}); using stale cache at {}", path.display())),
        ),
        None => (PricingCatalog::default(), false, Some(error)),
    }
}

fn is_codex_rollout(path: &Path) -> bool {
    path.file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.starts_with("rollout-") && n.ends_with(".jsonl"))
        .unwrap_or(false)
}

/// Recursively collect jsonl log files (bounded depth).
fn collect_jsonl(dir: &Path, depth: u8, out: &mut Vec<(PathBuf, std::fs::Metadata)>) {
    if depth > 6 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        match entry.metadata() {
            Ok(meta) if meta.is_dir() => collect_jsonl(&path, depth + 1, out),
            Ok(meta) if meta.is_file() => {
                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                let wanted = if name.ends_with(".jsonl") {
                    is_codex_rollout(&path) || !name.starts_with("rollout-")
                } else {
                    false
                };
                if wanted {
                    out.push((path, meta));
                }
            }
            _ => {}
        }
    }
}

fn scan_path(
    path: &Path,
    budget: u64,
    scanner: LineScanner,
) -> Result<(DayModelMap, u64), String> {
    let file = std::fs::File::open(path).map_err(|e| format!("open {}: {e}", path.display()))?;
    let mut remaining = budget;
    let lines = BufReader::new(file)
        .split(b'\n')
        .map(|r| r.map_err(std::io::Error::other));
    let (days, read) = scanner(Box::new(lines), &mut remaining);
    Ok((days, read))
}

/// Default models.dev fetch with the shared agent + retry-once pattern.
/// models.dev rejects unknown default UAs, so an explicit product UA is set.
fn default_pricing_fetch(agent: &ureq::Agent) -> Result<Value, String> {
    fetch_with_retry(|| {
        agent
            .get(PRICING_URL)
            .set("Accept", "application/json")
            .set("User-Agent", "ai-quotas/0.1 (local cost scan)")
            .call()
            .map_err(|e| format!("models.dev request failed: {e}"))?
            .into_json::<Value>()
            .map_err(|e| format!("failed to parse models.dev payload: {e}"))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn lines_from(parts: Vec<String>) -> Box<dyn Iterator<Item = std::io::Result<Vec<u8>>> + Send> {
        let joined = parts.join("\n").into_bytes();
        Box::new(
            joined
                .split(|b| *b == b'\n')
                .map(|chunk| {
                    let mut v = chunk.to_vec();
                    v.push(b'\n');
                    Ok(v)
                })
                .collect::<Vec<_>>()
                .into_iter(),
        )
    }

    fn total(days: &DayModelMap) -> TokenClasses {
        let mut t = TokenClasses::default();
        for models in days.values() {
            for c in models.values() {
                t.add(c);
            }
        }
        t
    }

    const PRICE: ModelPrice = ModelPrice { input: 5.0, output: 30.0, cache_read: 0.5, cache_write: 1.25 };

    #[test]
    fn cost_micros_is_tokens_times_price() {
        // 1M input tokens at $5/1M = $5 = 5_000_000 micros.
        assert_eq!(PRICE.cost_micros(&TokenClasses { input: 1_000_000, ..Default::default() }), 5_000_000);
        // 2_000_000 output tokens at $30/1M = $60.
        assert_eq!(PRICE.cost_micros(&TokenClasses { output: 2_000_000, ..Default::default() }), 60_000_000);
        // Small numbers round once, never lose fractional micros silently.
        assert_eq!(PRICE.cost_micros(&TokenClasses { output: 1, ..Default::default() }), 30);
    }

    #[test]
    fn codex_watermark_deltas_and_fork_rebaseline() {
        let mut prev = None;
        // First snapshot: cumulative from zero.
        assert_eq!(
            codex_delta(&mut prev, CodexCumulative { input: 100, cached: 20, output: 10 }),
            (100, 20, 10)
        );
        // Monotonic growth: plain deltas.
        assert_eq!(
            codex_delta(&mut prev, CodexCumulative { input: 250, cached: 40, output: 15 }),
            (150, 20, 5)
        );
        // Regression (session fork): current totals count as one lump.
        assert_eq!(
            codex_delta(&mut prev, CodexCumulative { input: 90, cached: 10, output: 5 }),
            (90, 10, 5)
        );
        // Growth continues from the new baseline.
        assert_eq!(
            codex_delta(&mut prev, CodexCumulative { input: 190, cached: 10, output: 5 }),
            (100, 0, 0)
        );
    }

    #[test]
    fn codex_lines_use_turn_context_model_and_skip_unmodeled() {
        let mut budget = 1u64 << 30;
        let (days, _) = scan_codex_lines(
            lines_from(vec![
                r#"{"timestamp":"2026-09-07T08:00:00Z","type":"session_meta","payload":{}}"#.to_string(),
                // token_count BEFORE any turn_context: skipped (no model).
                r#"{"timestamp":"2026-09-07T08:00:01Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":500,"cached_input_tokens":0,"output_tokens":50,"reasoning_output_tokens":0,"total_tokens":550}}}}"#.to_string(),
                r#"{"timestamp":"2026-09-07T08:00:02Z","type":"turn_context","payload":{"model":"gpt-5.5"}}"#.to_string(),
                r#"{"timestamp":"2026-09-07T08:01:00Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":1500,"cached_input_tokens":300,"output_tokens":150,"reasoning_output_tokens":20,"total_tokens":1650}}}}"#.to_string(),
                // info:null events are skipped.
                r#"{"timestamp":"2026-09-07T08:02:00Z","type":"event_msg","payload":{"type":"token_count","info":null}}"#.to_string(),
            ]),
            &mut budget,
        );
        let t = total(&days);
        // Delta = (1500-500, 300-0, 150-50); cached subsets input.
        assert_eq!(t.input, 1000 - 300);
        assert_eq!(t.cache_read, 300);
        assert_eq!(t.output, 100);
        assert_eq!(t.cache_write, 0);
    }

    #[test]
    fn claude_lines_dedupe_by_msg_and_request_id_last_wins() {
        let mut budget = 1u64 << 30;
        let chunk = |id: &str, req: &str, out: u64| {
            serde_json::json!({
                "type": "assistant",
                "timestamp": "2026-09-07T09:00:00.000Z",
                "requestId": req,
                "message": {
                    "id": id,
                    "model": "claude-haiku-4-5-20251001",
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 5,
                        "output_tokens": out,
                    },
                },
            })
            .to_string()
        };
        let (days, _) = scan_claude_lines(
            lines_from(vec![
                chunk("msg_1", "req_1", 7),
                chunk("msg_1", "req_1", 9), // same call: last chunk wins
                chunk("msg_1", "req_2", 3), // different call: kept
                r#"{"type":"assistant","timestamp":"2026-09-07T09:01:00Z","requestId":null,"message":{"id":"msg_2","model":"claude-haiku-4-5-20251001","usage":{"input_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"output_tokens":0}}}"#.to_string(),
            ]),
            &mut budget,
        );
        let t = total(&days);
        assert_eq!(t.input, 20); // 10 + 10 (last of each call)
        assert_eq!(t.cache_read, 200);
        assert_eq!(t.cache_write, 10);
        assert_eq!(t.output, 12); // 9 + 3
    }

    #[test]
    fn normalization_finds_prefixed_and_dated_ids() {
        let mut prices = BTreeMap::new();
        prices.insert("gpt-5.5".to_string(), PRICE);
        prices.insert("claude-haiku-4-5".to_string(), PRICE);
        let catalog = PricingCatalog::with_prices(prices);
        assert!(catalog.lookup("gpt-5.5").is_some());
        assert!(catalog.lookup("openai/gpt-5.5").is_some(), "prefix stripped");
        assert!(catalog.lookup("claude-haiku-4-5-20251001").is_some(), "date stripped");
        assert!(catalog.lookup("openai/gpt-5.5-20260101").is_some(), "prefix + date");
        assert!(catalog.lookup("mystery-model").is_none());
    }

    #[test]
    fn catalog_plausibility_gate() {
        let good: Value = serde_json::json!({
            "anthropic": {"models": {"claude-haiku-4-5": {"cost": {"input": 1, "output": 5, "cache_read": 0.1, "cache_write": 1.25}}}},
            "openai": {"models": {"gpt-5.5": {"cost": {"input": 5, "output": 30, "cache_read": 0.5}}}}
        });
        assert!(PricingCatalog::from_api_json(&good).is_ok());

        let no_openai: Value = serde_json::json!({
            "anthropic": {"models": {"claude-haiku-4-5": {"cost": {"input": 1, "output": 5}}}}
        });
        assert!(PricingCatalog::from_api_json(&no_openai).is_err());
        assert!(PricingCatalog::from_api_json(&serde_json::json!(42)).is_err());
    }

    /// Full scanner against tempdir roots with a fresh pricing fixture:
    /// no network needed (the injected fetcher always fails, and the fixture
    /// is within TTL).
    #[test]
    fn scanner_scans_prices_and_reuses_offsets() {
        let tmp = tempfile::tempdir().unwrap();
        let cache_dir = tmp.path().join("cache");
        let codex_root = tmp.path().join("codex/sessions/2026/09/06");
        let claude_root = tmp.path().join("claude/projects/proj");
        std::fs::create_dir_all(&codex_root).unwrap();
        std::fs::create_dir_all(&claude_root).unwrap();

        std::fs::write(
            codex_root.join("rollout-a.jsonl"),
            concat!(
                r#"{"timestamp":"2026-09-06T10:00:00Z","type":"turn_context","payload":{"model":"gpt-5.5"}}"#,
                "\n",
                r#"{"timestamp":"2026-09-06T10:01:00Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":1000000,"cached_input_tokens":200000,"output_tokens":10000,"reasoning_output_tokens":0,"total_tokens":1010000}}}}"#,
                "\n",
            ),
        )
        .unwrap();
        std::fs::write(
            claude_root.join("session.jsonl"),
            concat!(
                r#"{"type":"assistant","timestamp":"2026-09-06T11:00:00Z","requestId":"r1","message":{"id":"m1","model":"claude-haiku-4-5-20251001","usage":{"input_tokens":1000000,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"output_tokens":1000000}}}"#,
                "\n",
            ),
        )
        .unwrap();

        // Fresh pricing fixture (within 24h TTL).
        std::fs::create_dir_all(&cache_dir).unwrap();
        let fixture = PricingCache {
            fetched_at: Utc::now(),
            prices: BTreeMap::from([
                ("gpt-5.5".to_string(), PRICE),
                ("claude-haiku-4-5".to_string(), PRICE),
                ("claude-sonnet-5".to_string(), PRICE),
            ]),
        };
        std::fs::write(
            cache_dir.join("models-dev.json"),
            serde_json::to_vec(&fixture).unwrap(),
        )
        .unwrap();

        let mut roots = BTreeMap::new();
        roots.insert(CODEX, vec![tmp.path().join("codex/sessions")]);
        roots.insert(CLAUDE, vec![tmp.path().join("claude/projects")]);
        let scanner = CostScanner::with_roots(roots, cache_dir.clone());

        let scan = scanner.fetch();
        // Codex: input (1M-200k) * $5 + 200k cache * $0.5 + 10k out * $30
        //      = 4M + 100k + 300k = 4_400_000 micros.
        let codex = &scan.providers[CODEX];
        assert_eq!(codex.days.values().map(|d| d.cost_micros).sum::<u64>(), 4_400_000);
        // Claude: 1M * $5 + 1M * $30 = 35_000_000 micros.
        let claude = &scan.providers[CLAUDE];
        assert_eq!(claude.days.values().map(|d| d.cost_micros).sum::<u64>(), 35_000_000);
        assert!(scan.pricing_error.is_none());
        assert!(!scan.pricing_stale);
        // Date-suffixed claude id resolved through normalization.
        assert!(claude.unpriced_models.is_empty());

        // Offset reuse: mutate nothing, rescan, expect identical totals and
        // a persisted store that lists both files.
        let scan2 = scanner.fetch();
        assert_eq!(
            scan2.providers[CODEX].days.values().map(|d| d.cost_micros).sum::<u64>(),
            4_400_000
        );
        let store: OffsetStore =
            serde_json::from_slice(&std::fs::read(cache_dir.join("scan-offsets.json")).unwrap())
                .unwrap();
        assert_eq!(store.files.len(), 2, "both files tracked");

        // Touching a file changes mtime -> rescanned on next refresh (TTL 0).
        let path = claude_root.join("session.jsonl");
        let mut f = std::fs::OpenOptions::new().append(true).open(&path).unwrap();
        writeln!(f).unwrap();
        drop(f);
        {
            let mut guard = scanner.state.write().unwrap();
            guard.last_fetch = None; // force refresh
        }
        let scan3 = scanner.fetch();
        // The trailing empty line adds no usage; totals must be unchanged.
        assert_eq!(
            scan3.providers[CLAUDE].days.values().map(|d| d.cost_micros).sum::<u64>(),
            35_000_000
        );
    }

    #[test]
    fn check_fields_and_to_json_shapes() {
        let scan = CostScan {
            generated_at: Utc::now(),
            opencode_rows: Vec::new(),
            providers: BTreeMap::from([(
                CODEX.to_string(),
                ProviderCosts {
                    days: BTreeMap::from([(
                        Local::now().date_naive().to_string(),
                        DayTotals { cost_micros: 1_500_000, billed_micros: 0, tokens: TokenClasses::default() },
                    )]),
                    models: BTreeMap::new(),
                    months: BTreeMap::new(),
                    unpriced_models: BTreeSet::new(),
                },
            )]),
            pricing_stale: false,
            pricing_error: None,
            stats: String::new(),
        };
        let (today, mtd) = scan.check_fields(CODEX);
        assert_eq!(today, json!(1.5));
        assert_eq!(mtd, json!(1.5));
        let (no_today, no_mtd) = scan.check_fields(CLAUDE);
        assert!(no_today.is_null() && no_mtd.is_null());

        let v = scan.to_json();
        let codex = &v["providers"][CODEX];
        assert_eq!(codex["today_usd"], json!(1.5));
        assert_eq!(codex["month_to_date_usd"], json!(1.5));
        assert!(v.get("pricing").is_none(), "no pricing key when healthy");
        let empty = CostScan {
            providers: BTreeMap::new(),
            ..scan.clone()
        };
        assert!(empty.to_json()["providers"].as_object().unwrap().is_empty());
    }

    #[test]
    fn opencode_scan_aggregates_from_sqlite() {
        let tmp = tempfile::tempdir().unwrap();
        let db_path = tmp.path().join("opencode.db");
        {
            let conn = rusqlite::Connection::open(&db_path).unwrap();
            conn.execute_batch(
                "CREATE TABLE message (id TEXT PRIMARY KEY, data TEXT);
                 INSERT INTO message (id, data) VALUES
                 ('m1', '{\"role\":\"assistant\",\"time\":{\"created\":1788792000000},\"providerID\":\"zai-coding-plan\",\"modelID\":\"glm-5.3-flash\",\"cost\":1.25,\"tokens\":{\"input\":1000,\"output\":200,\"reasoning\":50,\"cache\":{\"read\":300,\"write\":10}}}'),
                 ('m2', '{\"role\":\"assistant\",\"time\":{\"created\":1788878400000},\"providerID\":\"zai-coding-plan\",\"modelID\":\"glm-5.3-flash\",\"cost\":0.5,\"tokens\":{\"input\":500,\"output\":100,\"reasoning\":0,\"cache\":{\"read\":0,\"write\":0}}}'),
                 ('m3', '{\"role\":\"assistant\",\"time\":{\"created\":1788878400000},\"providerID\":\"anthropic\",\"modelID\":\"claude-sonnet-5\",\"cost\":2,\"tokens\":{\"input\":10,\"output\":20,\"reasoning\":0,\"cache\":{\"read\":0,\"write\":0}}}'),
                 ('m4', '{\"role\":\"user\",\"time\":{\"created\":1788792000000},\"cost\":99,\"tokens\":{\"input\":1,\"output\":1,\"reasoning\":0,\"cache\":{\"read\":0,\"write\":0}}}'),
                 ('m5', '{\"role\":\"assistant\",\"time\":{\"created\":1788792000000},\"providerID\":\"anthropic\",\"modelID\":\"claude-sonnet-5\",\"cost\":0,\"tokens\":{\"input\":999,\"output\":999,\"reasoning\":0,\"cache\":{\"read\":0,\"write\":0}}}');",
            )
            .unwrap();
        }
        let rows = scan_opencode_db(&db_path).unwrap();
        // m4 (user) excluded; one row per day x model x billing mode:
        // m1 billed, m2 billed, m3 billed, m5 covered (cost 0) => 4 rows.
        assert_eq!(rows.len(), 4);
        let total_billed: u64 = rows.iter().map(|r| r.billed_micros).sum();
        assert_eq!(total_billed, 3_750_000, "stored costs only: 1.25+0.5+2 USD");
        let covered = rows
            .iter()
            .find(|r| r.model.contains("claude-sonnet-5") && r.billed_micros == 0)
            .unwrap();
        assert_eq!(covered.tokens.output, 999);
        let billed_sonnet = rows
            .iter()
            .find(|r| r.model.contains("claude-sonnet-5") && r.billed_micros > 0)
            .unwrap();
        assert_eq!(billed_sonnet.billed_micros, 2_000_000);
        assert_eq!(billed_sonnet.tokens.output, 20);
        assert!(rows.iter().all(|r| r.model.contains('/')));
    }
}
