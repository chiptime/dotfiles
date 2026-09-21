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

## What Milestone 1 contains

- `src/tiktok_ingest/config.py` — adopted defaults as constants and frozen
  dataclasses: `VISION_MODEL` (`qwen3.5:9b`, with `qwen3.8:27b` reserved for
  high-density rereads only, never default), `WHISPER_MODEL` (`large-v3`),
  `WHISPER_LANGUAGE` (`es`, fixed), full-audio-primary transcript policy
  (native subtitles auxiliary), hybrid sampling (64 baseline + 8 targeted),
  operating budgets (batch 5, media 10 min / 250 MiB, fetch 5 min, HTTP 2
  concurrent metadata / 24 h TTL, CPU prep 20 min / 2 GiB, vision 10 min,
  audio 10 min, 10 GiB working cache) and isolation/gate constants
  (isolated Ollama `0.34.1` on loopback, global `0.14.2` untouched, 20480
  MiB free-VRAM gate, 512 MiB swap-delta limit, 12288 MiB VRAM delta for
  `qwen3.5:9b`, never Whisper+vision GPU-resident together).
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
  `localhost/voice-assistant_whisper:latest`. The FULL immutable image ID
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
- `src/tiktok_ingest/pipeline.py` — **direct-URL input mode**: one
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

- `src/tiktok_ingest/vision.py` — gated local vision over prepared frames
  through the ISOLATED Ollama runtime (`qwen3.5:9b`, loopback 11435):
  benchmark resource gates via a one-window `GateLedger` (free VRAM, VRAM
  delta, swap delta, zero CPU offload, unload verification), bounded
  targeted rereads (≤ 8) inside the same residency, whisper-stopped
  precheck (read-only), `video.md` with visible uncertainty flags, stage
  record + resume fingerprint. No retries after a gate fires.
  Gate-failure cleanup: any non-complete ending (fired gate, handled
  error, missed deadline, operator interrupt) runs ONE bounded,
  idempotent unload attempt for this stage's model on this isolated
  server (never new inference, never other sessions' processes), then
  verifies empty `/api/ps` residency and observed GPU release. The
  cleanup report is recorded in `resource_gates["vision"]["cleanup"]`
  and the outcome; the original failure and any cleanup failure are
  BOTH preserved, and release is claimed only with positive evidence.
  Whisper is never stopped or restored by this code.
- `src/tiktok_ingest/whisper_client.py` — full-audio transcription through
  the reused whisper WebSocket service (`large-v3`, fixed `es`, one
  connection per job, health precheck): `audio.md` preserving method,
  timing provenance and no-speech-vs-missing distinctions; fails closed.
- `src/tiktok_ingest/ollama_runtime.py` — explicit `install-ollama`
  (sha256 AND byte-size verified, isolated `HOME`/`OLLAMA_MODELS`) and
  `import-model` (digest-verified copy from the global store, read-only
  source). Never runs implicitly.

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
  distinct from an honestly empty collection.
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
  --container-attr data-testid \
  --container-value collection-container \
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
  --container-attr data-testid --container-value collection-container
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
network, GPU, podman, ollama or model weights. Every external boundary
(subprocess, HTTP) is a fake in tests.

## Explicitly NOT implemented yet (Milestone 5)

- **M5 — Small pilot and handoff:** operator-selected clips, acceptance
  results, operational instructions for a real authorized run.

Also not implemented in this batch: the LIVE browser collection scan —
the offline collection-inventory core now exists (see "Collection
inventory (offline core)"), but capturing page snapshots still requires
the operator's separately authorized browser session, and enumeration
never authorizes downloads or inference. GPU inference commands and
resource gates exist, but the fixture suite does not exercise real GPU
work. Running those commands requires separate operator authorization;
adjacent workloads must not be stopped implicitly.
Synthesis is never performed by this pipeline itself: the operator (or
an out-of-band model run they authorize) supplies the document via
`--from-file`, and the pipeline only validates, scrubs and persists it.
