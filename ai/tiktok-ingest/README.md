# TikTok Ingest — Implementation (Milestones 1–4)

Status: fixture-only implementation slice. The test suite performs **zero
network access, zero podman calls, zero GPU work, no installs, no
downloads**. Python 3.14, standard library only (`unittest`, no
third-party dependencies).

Scope authority: [MVP-PRD.md](MVP-PRD.md) section 7 (milestone rows 1-2)
and [PRD.md](PRD.md) sections 6.2, 6.4, 7.1 and 8 (state layout and data
contracts). Operator-facing operational procedures (end-to-end flow,
backlog review lifecycle, verify semantics and their limits) live in
[OPERATIONS.md](OPERATIONS.md).

## Model serving — llm-hub (2026-11 migration)

Every model call goes to the RESIDENT llm-hub services; tiktok-ingest
manages no model runtime at all (no Ollama, no GPU lease, no swap retry,
no container lifecycle — that machinery was removed):

- **llm-hub-chat** — vision (`/v1/chat/completions`, model `qwen-3.8-27b`,
  resident multimodal) at `http://127.0.0.1:8081/v1`, and the same server
  family serves synthesis through the separately configured text API
  (`TIKTOK_INGEST_TEXT_*` env).
- **llm-hub-whisper** — transcription at `ws://127.0.0.1:8765/ws/transcribe`
  with its health endpoint at `http://127.0.0.1:8766/health`
  (faster-whisper large-v3, CUDA float16, always running; both models fit
  in 24 GiB next to each other).

Both are user systemd units — `llm-hub-chat.service` and
`llm-hub-whisper.service` (enabled by llm-hub's `install.sh`). If a
service is down, the corresponding stage blocks fail-closed with the
exact `systemctl --user start <unit>` instruction; tiktok-ingest never
starts, stops or inspects any container. FFmpeg/ffprobe (CPU preparation)
run via podman from llm-hub's whisper image
(`localhost/llm-hub_whisper-service:latest`).

## What Milestone 1 contains

- `src/tiktok_ingest/config.py` — adopted defaults as constants and frozen
  dataclasses: `LLM_HUB_CHAT_API_BASE` / `VISION_MODEL` (`qwen-3.8-27b`
  served by llm-hub llama-chat), `WHISPER_MODEL` (`large-v3`),
  `WHISPER_LANGUAGE` (`es`, fixed), full-audio-primary transcript policy
  (native subtitles auxiliary), hybrid sampling (64 baseline + 8 targeted),
  operating budgets (batch 5, media 10 min / 250 MiB, fetch 5 min, HTTP 2
  concurrent metadata / 24 h TTL, CPU prep 20 min / 2 GiB, vision 15 min,
  audio 10 min, 10 GiB working cache) and the llm-hub-whisper WS/health
  endpoints. (The former isolated-Ollama provisioning and VRAM/swap gate
  constants were removed by the 2026-11 llm-hub migration.)
- `src/tiktok_ingest/contracts.py` — stage outcome enum (`complete`,
  `partial`, `blocked`, `unsupported`, `failed`, `budget_exceeded`,
  `not_applicable`; every outcome carries a reason), fixed taxonomy roots
  (unknown classification is `null` plus an explanation, never a sixth
  root), and validated dataclasses with JSON (de)serialization for
  inventory entries, job manifests (`meta.json`), `video.md`/`audio.md`
  provenance headers, backlog entries (PRD section 8.1 shape), blocklist
  entries, taxonomy entries and processed entries.
- `src/tiktok_ingest/state.py` — configurable state root (default
  `~/.local/state/tiktok-ingest/` with `backlog.jsonl`, `blocklist.json`,
  `taxonomy.json`, `processed.json`, `inventory.json`, `cache/`,
  `runs/<run-id>/<video-id>/`), atomic writes (temp file + fsync +
  `os.replace`, temp cleanup on failure), duplicate-guarded backlog
  appends, append-only permanent blocklist (`--force` can NEVER override a
  rejection), accumulating normalized taxonomy (lowercase + hyphen
  normalization, idempotent re-add), processed/inventory stores, and cache
  keying by content hash + stage fingerprint.
- `src/tiktok_ingest/resume.py` — stage fingerprints (input content hash +
  tool/model/config versions), downstream invalidation over the canonical
  order `prepare -> vision -> audio -> synthesis -> verify -> emit` (changing
  a stage invalidates only that stage and its downstream), and idempotent
  backlog emission keyed by the stable video ID so a resumed run cannot
  create duplicates.

## What Milestone 2 adds

- `src/tiktok_ingest/extractor.py` — **gated pinned extractor**:
  `yt-dlp==2026.08.19` resolved from a local venv OUTSIDE Git (default
  `~/.local/share/tiktok-ingest/extractor-venv`). Gate verification runs
  BEFORE any execution: exact version pin, App-API and challenge-solving
  paths hard-blocked (the in-venv probe must locate AND patch an
  enforcement point per protected area — an area with zero located symbols
  fails closed), ambient cookies and cookie export refused at the argument
  level. ANY gate failure yields `blocked`; extraction is never degraded,
  bypassed or retried automatically. Outcomes per PRD section 6.2
  (`denied` / `unsupported` / `challenge` / `blocked` / `complete` /
  `failed` / `budget_exceeded`). oEmbed metadata over stdlib HTTP with a
  24 h TTL cache persisted through the state/cache modules (fetch time +
  outcome recorded; missing metadata never blocks media processing).
- `src/tiktok_ingest/bootstrap.py` — **explicit environment bootstrap**:
  `install-extractor` creates the local venv and pip-installs the pinned
  requirement with `--no-deps`. NEVER runs implicitly (processing modules
  do not import it; asserted by a test). No sudo, no root, no global
  installs, no committed binaries.
- `src/tiktok_ingest/validation.py` — **shared-media validation**:
  signature magic bytes (HTML-served-as-video is an explicit
  `unsupported`), ffprobe stream inspection (real video stream + real
  audio track; attached cover art never counts), duration ≤ 10 min,
  size ≤ 250 MiB (over budget is `budget_exceeded`, never silent
  truncation), and sha256 binding of accepted bytes.
- `src/tiktok_ingest/sampling.py` — **pure hybrid baseline selection**
  (benchmark methodology section 3.2: 2 FPS scan, 4 s uniform gap, scene
  threshold 0.3, text-region threshold 0.15, merge window 0.08 s, budget
  ≤ 64, crop candidates ≤ 16 marked as METADATA ONLY). Milestone-2
  guarantees: the supported duration is sampled end to end (the final
  timeline candidate always survives thinning) and uncovered intervals are
  exposed in the sampling manifest.
- `src/tiktok_ingest/prepare.py` — **CPU preparation in the pinned
  container**: FFmpeg/ffprobe run via podman from
  `localhost/llm-hub_whisper-service:latest` (llm-hub's whisper image; the
  same ffmpeg 4.4.2 as the benchmark). The FULL immutable image ID
  is resolved read-only at runtime before first use and recorded
  (`51b152706d…` is a cross-check prefix only — never a digest source);
  resolution failure fails closed. Containers run `--network none`,
  `--read-only` with tmpfs `/tmp`. One integrity pass, hybrid selection,
  batched exact-PTS frame extraction (native resolution, source PTS +
  selection reasons + per-frame sha256), audio normalized from the SAME
  media file to 16 kHz mono signed 16-bit PCM WAV. Stage deadline 20 min
  and 2 GiB derived-output cap per clip are enforced between steps; both
  surface as explicit `budget_exceeded` outcomes. All artifacts persist
  atomically; `meta.json` records stage outcomes, hashes, versions and
  timings.
- `src/tiktok_ingest/fetch.py` — **direct-URL input mode**: one
  canonical `https://www.tiktok.com/@author/video/<id>` URL → blocklist
  check → metadata/extraction cache checks → gated extraction → validation
  → content-addressed cache → `fetch.json` + `meta.json` + inventory
  entry with `input_origin: "direct-url"` (never claims collection
  membership). An unchanged repeated run performs ZERO re-extraction and
  ZERO re-preparation (fetch fingerprint + media hash verification +
  prepare fingerprint + artifact-intactness checks).
- `src/tiktok_ingest/cli.py` / `__main__.py` — stdlib argparse CLI:
  `python -m tiktok_ingest {install-extractor,fetch-url,prepare,status}`.

## What Milestone 3 adds

- `src/tiktok_ingest/vision.py` — vision over prepared frames through the
  RESIDENT llm-hub chat service (OpenAI-compatible
  `/v1/chat/completions`, model `qwen-3.8-27b`, one request per frame
  plus bounded targeted rereads ≤ 8): readiness precheck (llm-hub must
  answer `GET /v1/models` and serve the model, else `blocked`),
  `video.md` with visible uncertainty flags, stage record + resume
  fingerprint, 15-minute stage deadline. This stage never loads,
  unloads, starts or stops any model or container. (Superseded: the
  former isolated-Ollama runtime, VRAM/swap gates, whisper-stopped
  precheck and unload-verification cleanup — removed 2026-11.)
- `src/tiktok_ingest/whisper_client.py` — full-audio transcription through
  the RESIDENT llm-hub-whisper WebSocket service (`large-v3`, fixed `es`,
  one connection per job, health precheck at
  `http://127.0.0.1:8766/health`): `audio.md` preserving method, timing
  provenance and no-speech-vs-missing distinctions; fails closed with a
  message naming `systemctl --user start llm-hub-whisper`.

## What Milestone 4 adds

- `src/tiktok_ingest/synthesis.py` — **validated-document synthesis
  ingestion** (`synthesize <id> --from-file F`): reads the sanitized
  `video.md` + `audio.md` (a missing modality is an explicit failure,
  never an empty success), validates the operator-supplied document
  against a strict dataclass schema (fixed taxonomy roots, the five PRD
  6.6 verdicts or null, `metadata` as a claim evidence source, confidence
  0..1), scrubs credential-looking strings (on detection NOTHING is
  persisted and the reason names the pattern kind, never the secret), and
  persists `synthesis.json` with a resume fingerprint (media sha + schema
  version + source hash). The pipeline holds NO credentials and performs
  NO model calls: without `--from-file` the stage is `blocked`.
- `src/tiktok_ingest/verify.py` — **bounded safe verification and local
  emission** (`verify <id>`): a pure safe-URL validator (public http(s)
  only; loopback, private, link-local/metadata, unique-local and
  IPv4-mapped IPv6, credential-bearing URLs, non-default ports refused;
  every redirect hop validated BEFORE it is fetched, with urllib
  auto-follow disabled) plus resolver checks so friendly names that
  resolve into refused ranges fail closed; bounded retrieval (2 MiB cap,
  15 s timeout, 3-redirect cap, `User-Agent: tiktok-ingest/+local`) over
  an injectable `urlopen`; mechanical passage matching and a pure GitHub
  star-count parser; the full verdict matrix with the identity/source
  separation — `unnamed` means the entity could NOT be identified (the
  synthesis document names no entity), while an IDENTIFIED entity
  without candidate sources is `unverifiable` with an explicit
  `no_candidate_urls` reason (absence of URLs is never read as absence
  of a name; identity comes only from an entity name mechanically
  appearing in the claim — no semantic matching, nothing auto-searched),
  fetch failure/404 is `unverifiable` with the reason, numeric
  comparisons record both numbers (confirmed/overstated/contradicted)
  and a missing passage is NEVER a contradiction. Operator-supplied
  verdicts are preserved verbatim with an explicit operator-provenance
  reason (never passed off as independent system checks). Emits the PRD
  section 8.1 backlog entry through the existing state module with
  taxonomy subgroup normalization, atomic append and duplicate-emission
  prevention (unchanged rerun = zero new entries). No external task
  writes. The mechanical decision trail (`reason`, fetches) lives in
  `verification.json` per run; the backlog `Claim` contract carries the
  verdict only. `VERIFY_RULES_VERSION` (config.py, currently `"2"`)
  fingerprints the rules generation: bumping it invalidates recorded
  verify/emit fingerprints so stale verdicts are never silently reused,
  without touching fetch/prepare/vision/audio/synthesis stages.
- `src/tiktok_ingest/config.py` additions — verification retrieval caps
  (`VERIFY_MAX_RESPONSE_BYTES`, `VERIFY_TIMEOUT_SECONDS`,
  `VERIFY_MAX_REDIRECTS`, `VERIFY_ALLOWED_SCHEMES`, `VERIFY_USER_AGENT`).
  No endpoints, no keys.
- `status` now reports the synthesis/verify/emit stage outcomes and the
  backlog emission state per video.

## Backlog review CLI

Operator review of `backlog.jsonl` is automated by five subcommands
(full semantics, decision format and honest concurrency limits in
[OPERATIONS.md](OPERATIONS.md) section 4):

```bash
python3 -m tiktok_ingest backlog-list [--status pending|accepted|rejected]
python3 -m tiktok_ingest backlog-show <id>
python3 -m tiktok_ingest backlog-decide <id> --decision pending|accepted|rejected \
    [--reason R] --decisions-file decisions.jsonl
python3 -m tiktok_ingest backlog-plan  --decisions-file decisions.jsonl   # dry run
python3 -m tiktok_ingest backlog-apply --decisions-file decisions.jsonl
```

Decisions are explicit operator records (versioned strict JSONL, schema
`tiktok-ingest/backlog-decision@1`), each bound to the sha256 of the raw
backlog line. Apply revalidates every hash on the bytes it publishes
BEFORE writing anything, creates exclusive verified backups, publishes
`blocklist.json` before `backlog.jsonl`, and writes a `complete`,
`partial` or `noop` receipt (partial receipts are best-effort and may fail
if audit storage is unavailable); re-runs are idempotent. It is NOT a
multi-file transaction and there is NO product-wide lock: a writer
racing AFTER our replace is detected, but a third-party change our
replace already overwrote is NOT detectable — an exclusive-writer
window honored by all writers remains necessary and does not exist
(see OPERATIONS.md). Nothing here performs network, GPU or service work.

## Guided operation (`guided-run`)

`src/tiktok_ingest/guided.py` (driver) and `src/tiktok_ingest/engine.py`
(stage engine, no consent or TUI) add ONE thin coordinator — not a
framework, not a scheduler, no agent skill: it sequences the EXISTING
stage functions for one operator-confirmed tanda behind ONE upfront
run-plan authorization, and derives all progress from the product's
own manifests and fingerprints (no second state authority; the command
persists nothing of its own).

```bash
# Plan only: selection + per-stage state, zero prompts, zero effects:
python3 -m tiktok_ingest guided-run --collection "<url-or-key>" --dry-run
python3 -m tiktok_ingest guided-run --ids <id>[,<id>...] --dry-run

# Consent-gated execution (interactive terminal required):
python3 -m tiktok_ingest guided-run --collection "<url-or-key>" [--limit N]
python3 -m tiktok_ingest guided-run --ids <id>[,<id>...] \
    [--synthesis <id>=<path>] [--synthesis <id>=<path> ...]
```

Selection rules (always recomputed from CURRENT state — a previously
printed plan is never authority): plan mode takes `new_processable`
items capped at the batch limit (default 5, hard maximum 5); explicit
`--ids` accepts new and `partial_resumable` ids and rejects unknown,
duplicated or over-cap selections; permanently blocklisted ids and ids
already complete (emit fingerprint) are excluded with their reason and
are never re-inferred implicitly.

Consent asks exactly ONE run-plan window covering the whole tanda: it
names every frozen id and canonical URL, the full stage sequence, the
batch limits, and the text API endpoint/model when automatic synthesis
applies. On grant, the covered per-phase windows (`tanda`, `prepare`,
`fetch`, `gpu`, and the configured `synthesis-api`) are discharged
automatically (audited as covered, never fabricated human yeses).
Verification keeps its OWN grouped decision: candidate URLs only exist
after synthesis, so ONE separate verification window names every exact
validated URL before any retrieval. Denial, EOF, cancellation or a
non-interactive environment stop without assuming consent; there is no
global `--yes`; an authorization never persists into a later invocation.
Any window whose ids/destinations fall outside the planned scope is
asked as genuinely new scope and never auto-granted. Vision and audio
are plain requests to the resident llm-hub services: nothing is
started, stopped, loaded or unloaded, and a missing service blocks
fail-closed with the `systemctl --user start llm-hub-chat` /
`llm-hub-whisper` instruction. Each stage narrates a start/finish line with elapsed
time and the run ends with a readable summary table; all of that UI is
stderr-only, so the stdout JSON report keeps its exact shape.

On an interactive terminal both `guided-run` and `collection-run` render
a live **curses dashboard** (stdlib only — no third-party dependency): a
header bar with the run/phase/elapsed, a per-id stage matrix whose state
cells are colored (complete green, failed red, running yellow, pending
dim), and a log panel. Every consent window is shown in place as a
**bordered, titled modal** — a title line, the full window text, and a
footer stating the exact key hint — that scrolls on small terminals.
Color is used only when the terminal reports color support; otherwise the
SAME layout renders monochrome. The consent contract is unchanged — only
`y`/`yes` grants, EOF/read-error/non-TTY denies, and the whole window text
(title, ids, destinations, operations, limits, effects) is composed and
visible. The TUI draws on the terminal screen only: stdout stays
the parseable JSON report, printed after the curses session ends. When
curses cannot start or stdin/stdout is not a TTY (CI, a pipe,
`TERM=dumb`), both commands fall back to the EXACT line-based prompts and
stderr narration above; `--dry-run` and every non-TTY path are byte-
identical to before.

The synthesis boundary is a hard stop: without `--synthesis ID=PATH`
the run reports the `video.md`/`audio.md` paths and the exact resume
command, and NEVER generates, edits or auto-fills a document
(`synthesize --from-file` ingestion only; no model calls, no new
credentials). Stage order is fixed: fetch → prepare → vision (all ids)
→ verified unload → audio (all ids) → synthesis → verify; a fired gate
or any non-complete vision outcome ends the GPU window for every
remaining id (no auto-retry). Verify appends `pending` backlog entries
exactly as the standalone stage does; guided-run never edits statuses
or the blocklist and ends by referring to the backlog review CLI. Like
every writer, it requires an exclusive-writer operational window (see
OPERATIONS.md).

## Phase 0 — One collection, one command (`collection-run`)

Phase 0 adds ONE application command over the engine above. It
coordinates; it implements no stage logic of its own (ROADMAP work
packages 0.1–0.4; see `prds/PHASE-0-ONE-COMMAND-PRD.md`).

```bash
# Thin launcher (fail-fast, no logic, forwards args + exit code):
./run-collection.sh "https://www.tiktok.com/@author/collection/name-id"
# which dispatches to:
python3 -m tiktok_ingest collection-run "<collection-url>" [--dry-run] \
    [--limit N] [--declared-count N] [--end-evidence TEXT]
```

Journey: read-only preflight (dependencies, local state, exclusive
writer, planned destinations) → headed Playwright browser with a
DEDICATED run-scoped profile under the ONE browser grant (the operator
handles login/captcha by hand and signals readiness with a press-Enter
CHECKPOINT that is not an authorization — the browser controller
exposes only open/read/close, so login automation is impossible by
construction) → collection-scoped capture through the EXISTING
`collection.py` contracts (recommendations counted, never items;
declared/observed/end/access recorded separately; DOM change fails
explicitly; audit HTML persisted) → browser closed and profile removed
before any media work → ONE whole-current-run plan grant over the
FROZEN inventory → collection-wide PHASE-FIRST ordering through the
EXISTING `guided-run` coordinator (stage windows discharged by the
run-plan grant): preparation ALL → audio ALL → vision ALL → eligible
synthesis ALL, each phase crossed in bounded resource windows of at
most five ids → ONE grouped verification authorization → local
`pending` backlog entries only.

Key boundaries:

- **Phase-first resource windows**: the inventory is FROZEN once after
  the capture. The whole frozen selection is processed in GLOBAL phase
  order — preparation ALL → audio ALL → vision ALL → eligible synthesis
  ALL. Fixed slices of at most five ids are RESOURCE WINDOWS visited
  ONCE per phase (a preparation window, an audio window, a vision
  window), never complete-pipeline batches. NO synthesis request is
  made until every vision window has ended. Audio eligibility is
  preparation completion, NOT vision completion. A failed vision window
  stays closed: its remaining same-window ids stay pending while later
  frozen normal windows keep the existing attempt policy. Only a
  boundary failure, an interrupt, or a global precondition
  blocks ALL later effects, grouped verification included. The loop is
  bounded by construction and ids that become eligible later are never
  absorbed without a new scope decision. Progress reporting is a view
  derived from the product's stores — never a second ledger.
- **Automatic synthesis** (`src/tiktok_ingest/synthesis_api.py`): runs
  as its OWN collection phase, only after the vision phase has ended
  for the whole frozen selection; each id still needs COMPLETE vision
  and audio evidence. ONE OpenAI-compatible adapter
  (`POST {base}/chat/completions`) configured
  ONLY through the documented environment variables
  `TIKTOK_INGEST_TEXT_API_BASE_URL`, `TIKTOK_INGEST_TEXT_API_KEY`,
  `TIKTOK_INGEST_TEXT_MODEL` (optional
  `TIKTOK_INGEST_TEXT_API_TIMEOUT_SECONDS`, default 120). Values are
  never persisted and are scrubbed from every error. The request
  carries ONLY evidence text derived from `video.md`/`audio.md`
  (paths and credential-looking strings stripped, per-modality cap);
  tools, provider-side retrieval and command execution are disabled
  (`tools: []`, `tool_choice: "none"`); structured JSON is requested.
  The result is a CANDIDATE only: it is persisted solely through the
  existing `run_synthesis_stage` validation (`SynthesisDocument`
  contract + credential scrub + resume fingerprint). Timeout, refusal,
  malformed output, denial or missing configuration are RESUMABLE
  synthesis stops — completed vision/audio are never repeated and no
  classification is ever manufactured.
- **Consent** (user-approved two-decision contract): ONE browser grant
  opens the headed browser and names the isolated profile, the audit
  HTML write and the scan/inventory merge; a press-Enter READINESS
  checkpoint after manual login (a readiness signal, NOT a second
  authorization) starts the capture under that grant. ONE
  whole-current-run plan grant then covers the FROZEN inventory —
  exact eligible ids and canonical URLs with stage reuse states,
  the collection-wide phase order (preparation ALL → audio ALL →
  vision ALL → eligible synthesis ALL) crossed in resource windows of
  at most five ids, the synthesis endpoint/model, and the retry policy.
  Stage windows
  provably inside that scope (tanda, fetch, prepare, gpu,
  whisper-stop, whisper-restore, synthesis-api) are discharged by the
  run authorization wrapper and audited as `covered_by_run_plan` —
  never fabricated human yeses; unknown kinds, excess ids, unapproved
  destinations or whisper config drift are forwarded to the human as
  genuinely new scope and never auto-granted. Verification candidate
  URLs are unknown until synthesis, so verification is deferred past
  every phase and ONE grouped authorization shows every exact validated
  URL before any retrieval. Denial/EOF/non-TTY/Ctrl-C stop without
  assuming consent; there is no global `--yes`; a grant never persists
  into a later window, resource slice or invocation. Standalone
  `guided-run` uses the SAME ONE-window run plan over its own selection
  (there is no browser phase): ONE run-plan grant covers the tanda and
  the covered phase windows are discharged from it, while verification
  keeps its separate grouped decision.
- **Dry run**: `--dry-run` prints preflight + the document-only plan
  with zero browser, zero network, zero containers, zero GPU, zero API
  calls and zero writes.
- **Report persistence**: every finished (non-dry-run) invocation saves
  its report JSON atomically under
  `<state-root>/reports/<collection-key>/<UTC-timestamp>.json`
  (`2026-10-12T09-55-16Z`, the same shape as the run directories) and
  narrates the saved path to stderr. stdout stays the unchanged,
  parseable report JSON. Reports are NEVER written inside the repository.

Exit codes: 0 success; 1 failures/interruption/hard blocks; 2 usage
errors (invalid or missing URL rejected BEFORE any effect).

## Local web interface (`ti web`)

`src/tiktok_ingest/web/` serves a loopback-only interface over the same
state: collections, runs, blocks, evidence and backlog review. It adds no
engine and no storage; see OPERATIONS.md section 10.

## Reading a run later (`summary`)

`summary` is a READ-ONLY view of the durable per-video stage state. It
prints one row per item with its `fetch prepare audio vision synthesis
verify` states, backlog status, author handle (from the canonical URL)
and a derived `next` step — so a run whose audio completed but whose
vision failed is obvious at a glance (`vision pending (audio saved)`).

```bash
# Human table over LIVE durable state (all processed items):
python3 -m tiktok_ingest summary

# One collection's plan items, with counts:
python3 -m tiktok_ingest summary --collection "<collection-url-or-key>"

# Structured JSON for scripting:
python3 -m tiktok_ingest summary --collection "<key>" --json

# Read a SAVED report instead of live state (browse a past run):
python3 -m tiktok_ingest summary --collection "<key>" --report latest
python3 -m tiktok_ingest summary --report ~/.local/state/tiktok-ingest/reports/<key>/2026-10-12T09-55-16Z.json
```

`summary` reuses the SAME stage derivation as `guided-run`/`collection-run`
(no second ledger) and writes NOTHING: no state, manifest or backlog
writes, and no browser, network, GPU, container or model is touched. A
saved report records stage snapshots but not per-item backlog status, so
`--report` shows `backlog: n/a`.

## Collection inventory (offline core)

`src/tiktok_ingest/collection.py` implements the offline half of the
MVP-PRD section 3.A inventory: honest enumeration of ONE explicitly
selected TikTok collection from page snapshots (HTML) captured by an
AUTHORIZED operator browser session. The module never opens a browser,
performs network I/O or fetches media. Honesty rules, all encoded and
tested:

- Only container-scoped links become items. Recommendation links
  outside the declared container are counted in
  `out_of_container_count` and NEVER become items.
- Declared count, observed count, end-of-list evidence, access markers
  and status are recorded separately and never inferred from each
  other.
- A stopped scroll, a timeout, a repeated-ID cap, or an accidental
  `declared == observed` match never prove completeness: status
  `complete` requires recorded end-of-list evidence (an honestly empty
  collection with end evidence is a valid complete empty result).
- "Collection isn't available" plus "log in" is ACCESS-block evidence:
  status `blocked`. It does NOT prove deletion or privacy and is
  distinct from an honestly empty collection. Markers must appear in
  RENDERED text nodes: script/style/template/noscript payloads,
  attribute values, comments and explicitly hidden
  (`hidden`/`aria-hidden`) subtrees are never evidence (the live page
  ships the unavailability string as script i18n JSON on every page).
- A changed DOM (container marker missing) fails explicitly with
  `CollectionDOMError` — never a fake empty list.
- Scan state is MERGE-ONLY: a later partial observation never removes
  known items and never infers removals; `first_seen_at` and the
  original kind are preserved, raw hrefs accumulate, and the capture
  history is append-only.
- Enumeration never authorizes downloads or inference. The live browser
  session requires separate operator authorization.

Scan state persists under `<state-root>/collections/<collection-key>.json`
(the key is the first 16 hex chars of the sha256 of the canonical
collection URL), written atomically as `{"version": 1, "scan": {...}}`.
Inventory rows are created/updated through the EXISTING
`InventoryEntry` contract (`input_origin: "collection"`; pre-existing
direct-url entries only GAIN the collection association and keep their
origin, completeness, discovery time and counts). The processing plan is
a document only: it classifies observed items against the existing
blocklist and processed stores (`new_processable`, `processed_complete`,
`partial_resumable`, `rejected`, `unsupported_photo`), never treats
"observed" as "processed" and executes nothing.

Commands (all accept `--state-root`):

```bash
# Merge one page snapshot captured by the AUTHORIZED operator browser
# session (this code never captures anything itself):
python3 -m tiktok_ingest inventory-collect \
  "https://www.tiktok.com/@author/collection/<slug>" \
  --html page-snapshot.html \
  --container-attr data-e2e \
  --container-value collection-item-list \
  [--declared-count N] [--end-evidence TEXT] [--stop-reason TEXT] \
  [--blocked-marker "verify to continue"]   # repeatable

# Inspect the persisted scan state (by collection URL or 16-hex key):
python3 -m tiktok_ingest inventory-status "<collection-url-or-key>"

# Print the document-only processing plan (requires scan state):
python3 -m tiktok_ingest inventory-plan "<collection-url-or-key>"
```

Exit codes for `inventory-collect`: 0 normally, 1 when the merged
status is `blocked` (state and inventory are still persisted) and 2 on
errors (invalid URL, DOM change, unreadable snapshot — state stays
untouched). `--html -` reads the snapshot from stdin.

## Running

```bash
# 1. Explicitly install the pinned extractor into the local share root
#    (outside Git; never runs implicitly; no sudo):
python3 -m tiktok_ingest install-extractor

# 2. Fetch and validate one canonical video URL:
python3 -m tiktok_ingest fetch-url https://www.tiktok.com/@author/video/<id>

# 2b. Offline collection inventory: merge page snapshots captured by an
#     AUTHORIZED operator browser session into scan state + inventory,
#     then inspect status and the document-only plan (see "Collection
#     inventory (offline core)"):
python3 -m tiktok_ingest inventory-collect "<collection-url>" --html snapshot.html \
  --container-attr data-e2e --container-value collection-item-list
python3 -m tiktok_ingest inventory-status "<collection-url-or-key>"
python3 -m tiktok_ingest inventory-plan "<collection-url-or-key>"

# 3. Prepare CPU artifacts (frames + 16 kHz mono WAV) for a fetched ID:
python3 -m tiktok_ingest prepare <video-id>

# 4. Local inference (vision, then audio; explicit authorization gates):
python3 -m tiktok_ingest vision <video-id>
python3 -m tiktok_ingest audio <video-id>

# 5. Ingest an explicitly supplied synthesis document (no model calls):
python3 -m tiktok_ingest synthesize <video-id> --from-file synthesis.json

# 6. Verify claims against the document's candidate first-party URLs and
#    emit the local backlog entry:
python3 -m tiktok_ingest verify <video-id>

# 7. Inspect persisted outcomes:
python3 -m tiktok_ingest status [<video-id>]

# 8. Operator backlog review (explicit decisions -> dry run -> apply):
python3 -m tiktok_ingest backlog-list
python3 -m tiktok_ingest backlog-show <id>
python3 -m tiktok_ingest backlog-decide <id> --decision accepted --decisions-file d.jsonl
python3 -m tiktok_ingest backlog-plan --decisions-file d.jsonl
python3 -m tiktok_ingest backlog-apply --decisions-file d.jsonl

# 9. Guided coordination of one tanda through the stages above
#     (consent-gated; see "Guided operation (guided-run)"):
python3 -m tiktok_ingest guided-run --collection "<url-or-key>" --dry-run
python3 -m tiktok_ingest guided-run --collection "<url-or-key>"
python3 -m tiktok_ingest guided-run --ids <id>[,<id>...] --synthesis <id>=<path>

# 10. Phase 0 one-command journey (browser inventory + phase-first
#     resource windows + automatic synthesis + pending delivery; see
#     "Phase 0 — One collection, one command"):
./run-collection.sh "<collection-url>"
python3 -m tiktok_ingest collection-run "<collection-url>" --dry-run
```

Every command accepts `--state-root` (default
`~/.local/state/tiktok-ingest/`) and the fetch/install commands accept
`--share-root` (default `~/.local/share/tiktok-ingest/`). Runtime state
and the extractor venv live OUTSIDE Git; durable code lives in this
repository. These commands are not executed implicitly: installation,
downloads and inference require explicit operator authorization. The
container image, extractor venv and any authorized run remain
subject to separate operator authorization per the MVP-PRD.

## Run the tests

```bash
cd ~/.dotfiles/ai/tiktok-ingest
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s src/tiktok_ingest/tests -t src -v
```

(`src/` is not itself an importable package, and Python 3.11+ `unittest
discover` rejects namespace-package start directories, so the start
directory is the test package and `src` is put on `PYTHONPATH`.)

Expected result: all tests pass (`OK`). The suite is fixture-only: no
network, GPU, podman or model weights. Every external boundary
(subprocess, HTTP) is a fake in tests.

## Explicitly NOT implemented yet (Milestone 5)

- **M5 — Small pilot and handoff:** operator-selected clips, acceptance
  results, operational instructions for a real authorized run.

Also not exercised yet: the REAL trial of the Phase 0 command — the
live browser scan, automatic synthesis against a configured text API
and the whole one-command journey exist and are fixture-tested (see
"Phase 0 — One collection, one command"), but running them for real
requires separate operator authorization per the Phase 0 PRD. Model
requests go to the resident llm-hub services; the fixture suite does
not exercise real GPU work. Adjacent workloads are never touched. Synthesis through
`guided-run` remains operator-supplied (`--from-file`); only
`collection-run` adds the automatic OpenAI-compatible adapter, and its
output still passes the same strict validation.
