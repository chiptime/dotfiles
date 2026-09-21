# ai-quotas

`ai-quotas` is a tiny local-only dashboard for AI provider quotas and balances. It watches a directory of JSON record files (written by hooks, statuslines or this tool's `stamp` command), optionally enriches them with live data pulled from provider APIs (DeepSeek wallet balance, Z.ai coding-plan usage windows), and serves a self-contained dark-themed web UI plus a JSON API on `127.0.0.1`. Nothing leaves the machine: no telemetry, no remote storage, no external UI dependencies.

## Record schema (the hook contract)

One JSON file per record in the state dir (e.g. `claude.json`; extra windows may use any name like `gemini-5h.json`). The record's `provider` field is authoritative; the file stem is only a fallback. Multiple files may share one provider. Unknown fields are ignored.

| Name | Type | Required | Meaning |
|---|---|---|---|
| `provider` | string | yes | Provider id; for file-mode records it is the file stem. |
| `kind` | `"window"` \| `"daily"` \| `"balance"` | yes | Quota semantics. `window`/`daily` render percent; `balance` renders an amount. |
| `source` | `"manual"` \| `"local-log"` \| `"api"` | yes | Where the data came from; also sets the default TTL. |
| `fetched_at` | RFC 3339 timestamp | yes | When the data was obtained. Freshness (`ok` vs `stale`) is computed from this plus the TTL. |
| `used` | number | no | Used amount. For API percent windows this is the percent used. |
| `limit` | number | no | Total/window limit. For `kind: balance` this is the balance amount. |
| `unit` | string | no | Unit label, e.g. `requests`, `percent`, `currency`. |
| `label` | string | no | Subtitle on the card, e.g. `5h window`, `MCP monthly`. |
| `display_name` | string | no | Card title override; otherwise the known-provider display name or the provider id. |
| `currency` | string | no | ISO currency code for balance records, e.g. `CNY`, `USD`. |
| `resets_at` | RFC 3339 timestamp | no | When the window resets. **Forbidden for `kind: balance`** (validation error). |
| `ttl_seconds` | number | no | Freshness window. Defaults per source: `manual` 21600, `local-log` 600, `api` 300. |

## Ingest modes

1. **Manual** — you call `ai-quotas stamp` yourself (default `source: manual`, 6h TTL).
2. **Local log** — a hook (statusline, wrapper script) writes records with `--source local-log` (10min default TTL) whenever you use a provider.
3. **API pull** — the server/check fetches live data from Claude Code, OpenAI Codex (ChatGPT Plus), OpenCode Go, DeepSeek and Z.ai through cached adapters; no files involved.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `AI_QUOTAS_STATE_DIR` | `$XDG_STATE_HOME/ai-quotas` else `~/.local/state/ai-quotas` | Record file directory. |
| `AI_QUOTAS_PORT` | `47623` | Port for `serve`. |
| `AI_QUOTAS_HOST` | `127.0.0.1` | Bind address for `serve`. Set to `0.0.0.0` to expose the dashboard to the LAN (add a firewall rule for the port). |
| `CLAUDE_ACCESS_TOKEN` | unset | Claude OAuth token; when unset, falls back to `~/.claude/.credentials.json`. |
| `CHATGPT_ACCESS_TOKEN` / `CODEX_ACCESS_TOKEN` | unset | ChatGPT / Codex access token; when unset, falls back to `~/.codex/auth.json`. |
| `OPENCODE_API_KEY` / `OPENCODE_GO_API_KEY` | unset | OpenCode Go API key; when unset, falls back to `~/.local/share/opencode/auth.json`. |
| `DEEPSEEK_API_KEY` | unset | DeepSeek API key; when unset, falls back to `~/.local/share/opencode/auth.json`. |
| `ZAI_API_KEY` | unset | Z.ai API key; when unset, the adapter falls back to `~/.local/share/opencode/auth.json`. |

## CLI

```
ai-quotas                 # same as `serve`
ai-quotas serve           # dashboard + JSON API on 127.0.0.1:$AI_QUOTAS_PORT
ai-quotas check           # print provider/state/percent/name for all records (files + pull)
ai-quotas stamp --provider claude --kind window --used 62 --limit 100 \
    --unit requests --label "5h window" --display-name "Claude Pro" \
    --resets-at 2026-08-20T18:30:00Z --ttl 600 --source local-log
```

`stamp` flags: `--provider`, `--kind`, `--source` (required semantics; source defaults to `manual`), `--used`, `--limit`, `--unit`, `--label`, `--display-name`, `--currency`, `--resets-at`, `--ttl`, `--file` (override the file name).

## Build

```
cargo build --release
# binary: target/release/ai-quotas
```

## systemd (user) example

Example only — nothing is installed by this repo. Adjust the binary path:

```ini
[Unit]
Description=AI Quotas dashboard

[Service]
ExecStart=/home/you/.dotfiles/tools/ai-quotas/target/release/ai-quotas serve
Environment=AI_QUOTAS_PORT=47623
Restart=on-failure

[Install]
WantedBy=default.target
```

## Writing a hook

Any tool that can run a shell command can feed the dashboard. Example: a Claude Code statusline hook stamping the 5h window usage on every prompt:

```sh
/path/to/target/release/ai-quotas stamp --provider claude --kind window --source local-log \
  --used "$USED" --limit 100 --unit requests --label "5h window" \
  --display-name "Claude Pro" --resets-at "$RESETS_ISO"
```

`$USED` is the number of requests used; `$RESETS_ISO` the window reset as an RFC 3339 UTC timestamp (e.g. `date -u -d '+3 hours' +%Y-%m-%dT%H:%M:%SZ`). Within a second the card appears at `http://127.0.0.1:47623`.

## Adding a new provider

### 1. File mode (Auto-discovery — zero code required)

Any tool, hook, or script can drop a JSON record file into `$AI_QUOTAS_STATE_DIR` (by default `~/.local/state/ai-quotas/`) or use `ai-quotas stamp`.

**The dashboard automatically discovers and renders new files without recompiling or restarting:**
- **Grouping:** Records are grouped by their `"provider"` field.
- **Group Title:** Uses the record's `"display_name"` (falling back to the provider ID).
- **Cards Placement:** Standard window labels (`5h window`, `Weekly`, `Monthly`, `Daily`) automatically render as main cards in the group's primary row. Any other label falls into *"Otras cuotas"*.
- **Visual Order:** Providers not listed in the default ordering appear at the end of the dashboard, sorted alphabetically.

#### Declarative configuration (`~/.config/ai-quotas/config.json`):
You can customize ordering, placeholders, capacity, and custom window labels purely via JSON without editing code or recompiling:

```json
{
  "order": ["claude", "zai", "chatgpt", "opencode", "gemini", "gemini-3p", "myai", "deepseek"],
  "capacity": { "claude": 1.0, "zai": 20.0, "gemini": 1.0, "myai": 2.0 },
  "file_providers": [
    { "id": "gemini", "display_name": "Google Gemini" },
    { "id": "gemini-3p", "display_name": "Antigravity (Claude)" },
    { "id": "myai", "display_name": "My Custom AI" }
  ],
  "main_labels": ["5h window", "weekly", "monthly", "daily", "myai 12h"],
  "weekly_labels": { "myai": "myai weekly" }
}
```

- **`order`**: Exact column ordering of providers in the dashboard.
- **`capacity`**: Relative capacity weight for the weekly pace ranking.
- **`file_providers`**: File-mode providers that should display a `Sin datos` (Missing) card when their file is absent.
- **`main_labels`**: Labels treated as primary cards (preventing them from falling into *"Otras cuotas"*).
- **`weekly_labels`**: Per-provider custom weekly label for ritmo sparkline and closes (defaults to `"weekly"`).
- **Path**: Resolved from `$AI_QUOTAS_CONFIG`, else `~/.config/ai-quotas/config.json`. Changes apply immediately on the next page refresh without restarting the service.

> 📖 **Guía en español para no técnicos:** Consulta [docs/CONFIG_GUIDE.md](docs/CONFIG_GUIDE.md) para ver ejemplos ilustrativos y recetas paso a paso.

### 2. API Pull mode (Native Rust adapter)

If `ai-quotas` should query an upstream provider API directly over HTTP:
1. **Adapter:** Create `src/providers/<provider>.rs` implementing `QuotaSource` (`fetch()`, credentials resolution, and HTTP query with `fetch_with_retry`).
2. **Manager:** Expose the module in `src/providers/mod.rs` and add a `CachedSource` field to `PullManager`.
3. **Registry:** Add `KnownProvider { id: "...", display_name: "...", mode: Mode::Pull }` to `KNOWN_PROVIDERS` in `src/reader.rs`.
4. **UI:** Add to `ORDER` and `CAPACITY` in `static/index.html`.
5. **Incidents (optional):** If the provider has a public status page (Statuspage.io or OneUptime), add a row to `STATUS_PAGES` in `src/status.rs`.

