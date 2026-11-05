# TikTok Ingest — Operational Procedures

Audience: the operator running the pipeline locally. This document
describes what the code ACTUALLY does today (verified against the CLI and
the test suite), the meaning and limits of verification verdicts, and the
review procedures around `backlog.jsonl`. Scope authority remains
[MVP-PRD.md](MVP-PRD.md) and [PRD.md](PRD.md); implementation details live
in [README.md](README.md).

Nothing in this document requires GPU, network, services or browser access
unless a step is explicitly operator-authorized. Fetch, preparation,
inference and verification commands perform external work when invoked;
the operator authorizes them, and the pipeline executes their code.

## 1. Real end-to-end flow

```
operator browser session (AUTHORIZED, out of band)
        │ page HTML snapshots
        ▼
inventory-collect ──► <state>/collections/<key>.json   (merge-only scan state)
                  └─► inventory.json entries (input_origin: "collection")
        ▼
inventory-plan ──► document-only classification of items:
        │           new_processable / processed_complete / partial_resumable /
        │           rejected / unsupported_photo   (executes NOTHING)
        ▼
fetch-url <url> ──► gated extraction (pinned yt-dlp) + media validation
        │           + content-addressed cache + fetch.json/meta.json
        ▼
prepare <id> ──► frames (exact PTS) + 16 kHz mono WAV in the pinned container
        ▼
vision <id> ──► vision via resident llm-hub chat (qwen-3.8-27b) ──► video.md
        ▼
audio <id> ──► llm-hub-whisper WS transcription (large-v3, es) ──► audio.md
        ▼
synthesize <id> --from-file synthesis.json ──► validated + scrubbed synthesis.json
        │           (the pipeline NEVER synthesizes by itself; no model calls)
        ▼
verify <id> ──► bounded retrieval of ONLY the recorded candidate URLs,
        │       mechanical verdicts, verification.json
        └─────► backlog.jsonl append (atomic, duplicate-guarded)
        ▼
operator review of backlog.jsonl (pending → accepted | rejected)
```

Key honesty rules encoded at every boundary:

- `inventory-plan` output is a document; "observed" never means "processed".
- Extraction is never degraded or bypassed; any gate failure is `blocked`.
- Synthesis documents are operator-supplied (`--from-file`); a missing
  modality is an explicit failure, and credential-looking strings abort
  persistence entirely.
- Verification fetches ONLY the `candidate_urls` recorded in the synthesis
  document (per claim or per entity). Nothing is auto-searched; text from
  media or fetched pages never becomes a fetch target.

## 2. Stage `complete` vs claim `confirmed`

These are DIFFERENT axes and must never be conflated:

- **Stage outcome `complete`** (in `meta.json` and `status`): the stage ran
  to its normal end. `verify` completes even when EVERY verdict is
  `unverifiable` or `unnamed` — a completed verification is one where all
  claims received their mechanical verdict and the backlog entry was
  emitted, not one where claims turned out true.
- **Claim verdict `confirmed`**: a fetched first-party source contained a
  mechanical supporting passage (or an equal-or-higher GitHub star count).
  Only the verdict matrix of section 3 can produce it.

A backlog entry whose claims are all `unverifiable` is a valid, complete
result. The verdicts ARE the product; `complete` just means the machinery
finished honestly.

### Automatic synthesis and provenance

The automatic text-API path treats model output as candidate content, not
as independent verification: supplied model verdicts are cleared before
the validated synthesis document is persisted. The verifier still applies
its existing rules to explicitly supplied `--from-file` documents. An
operator-supplied or historical `confirmed` verdict is not, on its own,
evidence that an external source was consulted; inspect the recorded reason
and fetch records in `verification.json`. Existing artifacts and backlog
entries are not rewritten by this change.

The automatic prompt requests one evidence-supported actionable per idea
and excludes social-engagement requests. A narrow local filter removes
some obvious standalone follow, like, subscribe and share requests; mixed or ambiguous
items are kept for review. The original evidence remains available, and
the existing backlog's single `actionable` string is unchanged as a format.

## 3. Verdicts and reasons (rules version 2)

`VERIFY_RULES_VERSION` is `"2"`. Verdict values (PRD section 6.6):
`confirmed`, `overstated`, `unverifiable`, `contradicted`, `unnamed`.

| Verdict | Meaning | Produced when |
|---|---|---|
| `confirmed` | Primary source supports the claim | Mechanical token match in a fetched source, or star count ≥ claimed |
| `overstated` | Real but weaker than presented | Star count < claimed but non-zero |
| `contradicted` | Primary source disagrees | Source reports 0 stars (mechanical only; a missing passage is NEVER a contradiction) |
| `unverifiable` | Could not be checked | See reason taxonomy below |
| `unnamed` | The entity could NOT be identified | No candidate URL AND the synthesis document identifies no entity at all |

### `unverifiable` reason taxonomy

The `reason` field distinguishes WHY (full trail in `verification.json`;
the backlog entry carries the verdict only):

- `no_candidate_urls: the claim names identified entity/entities "…"` —
  the entity IS identified (its name from the synthesis document appears
  mechanically in the claim) but no first-party candidate URL was
  supplied. This is the rules-v2 separation: absence of sources is not
  evidence of absence of a name.
- `no_candidate_urls: … identity … could not be determined …` — the
  document names entities but none appears in this claim. Conservative
  outcome: a missing name match is never read as proof the claim is
  unnamed, and no semantic matching is attempted.
- `every candidate retrieval failed: <url>: <error>` — all candidate
  retrievals failed (404, refused URL, timeout, size cap…).
- `fetched N candidate source(s); no supporting passage: …` — sources
  fetched but no mechanical passage matched.
- `<url>: no star count found` — star claim, source fetched, count absent.

### Operator-supplied verdicts

A synthesis claim may carry its own `verdict` (one of the five). Verify
preserves it VERBATIM — value, note and evidence — and marks the reason
`kept operator-supplied verdict; …`. It is never relabeled as an
independent system check. Operator verdicts take precedence over the
unnamed/unverifiable branching.

### Rules versioning and historical results

`VERIFY_RULES_VERSION` participates in the verify/emit stage fingerprints.
Bumping it (as done for rules v2) makes any recorded pre-change verify
result invalid: a new `verify` run recomputes verdicts instead of silently
reusing the old ones. Fetch, prepare, vision, audio and synthesis
fingerprints are independent and are NOT invalidated. Existing
`verification.json` files keep their recorded `rules_version`, so their
generation is always auditable.

## 4. Backlog review procedure

`backlog.jsonl` is a local, append-only JSONL queue (PRD section 8.1). One
line per video, `id` = stable video ID. Fields include `status`,
`classification`, `entities`, `claims`, `fit`, `actionable`, `artifacts`.

- `status` is operator-edited: `pending` → `accepted` | `rejected`
  (`BACKLOG_STATUSES`). The pipeline always appends new entries as
  `pending` and never edits status itself.
- **Accepted**: reviewed and approved by the operator. Downstream use
  (Notion/Gertru) is manual and operator-controlled; the pipeline never
  writes to external systems.
- **Rejected (permanent)**: a rejection MUST be accompanied by a blocklist
  entry (`blocklist.json`, append-only). A blocked ID is skipped by every
  future run and `verify` refuses it outright (`blocked`, "no flag can
  override an operator rejection"). `--force` can NEVER remove or bypass a
  blocklist entry; the `unblock` operation exists only to raise an explicit
  error and make the rule testable.

Review loop (manual, per entry): read the entry and its referenced
`artifacts/…/verification.json` for the mechanical reason trail → set
`status` → if rejected, add the blocklist entry with a reason.

### Review CLI: `backlog-list/show/decide/plan/apply`

The manual loop above is automated by five subcommands (all accept
`--state-root`; review state such as decisions and receipts lives under
`<state>/reviews/`, never in the pipeline layout):

| Command | Purpose |
|---|---|
| `backlog-list [--status S]` | Read-only listing with each raw line's `entry_sha256` (the concurrency token) |
| `backlog-show <id>` | Read-only detail: entry, claims, provenance and the mechanical `verification.json` reasons |
| `backlog-decide <id> --decision {pending,accepted,rejected} [--reason R] --decisions-file F` | Record ONE explicit decision into the versioned JSONL document; binds the current line hash. `rejected` REQUIRES `--reason` (it becomes the blocklist reason) |
| `backlog-plan --decisions-file F` | Dry run: per-ID change, expected post-entry hashes, expected whole-file backlog hash, blocklist additions, conflicts. Writes nothing (exit 1 if conflicts) |
| `backlog-apply --decisions-file F` | Apply only explicit, still-current decisions (exit 0; errors exit 2) |

Decisions document format (`tiktok-ingest/backlog-decision@1`): strict
JSONL — ONE compact JSON object per line; pretty-printed multi-line JSON
is refused loudly (a real past incident). Required fields: `schema`,
`id`, `decision`, `decided_at` (ISO-8601), `entry_sha256` (lowercase
64-hex sha256 of the raw backlog line WITHOUT the trailing newline, as
`backlog-list` reports it); optional `reason` (required and non-empty
for `rejected`). Unknown fields are refused, so a future format change
must bump the schema string. One decision per `id`: duplicates are
refused as an ambiguous decision set.

`backlog-apply` guarantees, in phases:

1. **Plan precheck**: any stale hash, unknown ID, duplicate backlog ID,
   or an accept/pending decision on a permanently blocklisted ID aborts
   the whole apply with ZERO data writes (nothing has been published at
   this point).
2. **Backups**: every target file that already exists is copied to
   `<state>/reviews/backlog-apply-<UTC stamp>/backup/<relative-path>`
   with EXCLUSIVE creation (`O_EXCL` — a collision aborts instead of
   overwriting) and the copy's sha256 is verified against the source
   BEFORE any data write. A file that does not exist yet (first-ever
   `blocklist.json`) is recorded as "new file".
3. **Pre-publication revalidation**: EVERY rewritten entry's hash is
   re-checked on the exact bytes that will generate the new backlog,
   BEFORE any publication. A change that landed between planning and
   publication aborts with zero data writes — an old decision is never
   applied to new content (a fresh re-read alone, without this
   comparison, is NOT protection).
4. **Publication order**: `blocklist.json` BEFORE `backlog.jsonl`
   (fail-safe: an interrupted reject leaves an over-blocked ID, never a
   "rejected backlog line without a permanent blocklist entry").
5. **Partial publication receipt is best-effort**: after the first
   publication, a detected conflict or write failure attempts to write a
   `status: "partial"` receipt naming exactly which files were
   published (blocklist entries, backlog or not) plus backups and
   recovery steps. The error says "partial publication"; it never
   claims "zero writes" once something was published. Receipt persistence
   can also fail (for example, unavailable audit storage); absence of a
   receipt is not proof that nothing was published.
6. **Post-write verification is about OUR bytes only**: the re-read
   proves that the bytes we published are still on disk. It detects an
   external write that landed AFTER our replace; it CANNOT detect a
   third-party change that our `os.replace` already overwrote — that
   loss is silent. See the concurrency limits below.
7. **Receipt**: `apply-receipt.json` in the audit dir records
   `status` (`complete` | `partial` | `noop`), applied /
   already-applied IDs, blocklist results, per-file before/after
   sha256, backup paths + hashes and the recovery instruction.
8. **Idempotency**: re-applying confirmed decisions is a no-op — zero
   data writes, no new backups; only a `status: "noop"` receipt
   (written exclusively, never overwriting a prior receipt).

Recovery: re-run `backlog-apply` (each decision is hash-guarded and the
blocklist append is idempotent, so an interrupted run converges), or
restore the exclusive backups listed in the receipt and re-plan. A
`status: "partial"` receipt documents exactly what was published when a
run was interrupted after its first write. Acceptance never touches
claim verdicts: `accepted` is an operator review state, NOT `confirmed`
— only the verification verdict matrix produces `confirmed`.

#### Concurrency: real guarantees and real limits

- There is NO cooperative lock in the product. The existing writers
  (`Backlog.append` during verify/emit, `Blocklist.reject`) publish
  whole files through `atomic_write_bytes` and would ignore any lock
  only this CLI took — so none is added: a partial lock is a false
  guarantee, not a safety mechanism.
- The control is **optimistic and hash-based**: a decision binds
  `entry_sha256`; apply revalidates every rewritten entry's hash on the
   publish-source bytes BEFORE any publication. This detects changes to
   rewritten entries visible at that revalidation read, not every change
   up to publication. The residual race below still applies.
- **Known residual race**: the window between the revalidation read and
  `os.replace` cannot be closed without an exclusive-writer window
  honored by ALL writers. The product has none; none is faked. Two
  precise sub-cases:
  - A writer landing AFTER our replace is DETECTED by the post-write
    re-read (bytes on disk differ from our payload) and registered as a
    partial publication.
  - A writer landing BEFORE our replace is silently DESTROYED by it
    (our published bytes match our expected payload, so verification
    passes while the foreign change is gone). This loss is NOT
    detectable by re-reading; only an exclusive-writer window shared by
    every writer would remove it.
- Apply is NOT a multi-file transaction: each file replacement is
  atomic, the sequence is not. Interrupted runs converge by idempotent
  re-run, or are restored from the exclusive backups recorded in the
  receipt (`status: "partial"` receipts document the mid-way state).

### Historical rules-v2 reconciliation: completed

The authorized historical reconciliation completed on 2026-09-21:
seven entries remain accepted, and all 13 affected claims were reconciled
across two applications (eight in two game entries, then five in three
other entries). Two unaffected historical v1 results remain valid history.
Accepted does not mean that every claim is confirmed. This scope is closed;
do not repeat the migration because an older backup lacks prestate
`meta.json` — that is a retained audit limitation, not pending work.
Personal receipts and backlog data remain outside Git under the local
state root.

### Future rules-change reconciliation procedure

**Fact first:** re-running `verify` does NOT update an existing backlog
entry. Emission is duplicate-guarded by stable video ID
(`Backlog.append` returns `skipped_duplicate` and writes nothing). After
the rules-v2 bump, a re-run recomputes `verification.json` with corrected
verdicts, reports the emit as a duplicate, and the old backlog line stays
as it was.

For a future rules change, use this procedure only with explicit operator
review and an exclusive-writer window honored by all writers. It is not
an instruction to repeat the completed historical reconciliation:

1. List affected IDs: entries whose `artifacts` run directory now contains
   a `verification.json` with `rules_version: "2"` while the backlog line's
   claims still carry affected older verdicts. Compare the actual claims;
   a historical version alone does not establish that correction is needed.
2. For each ID, build the corrected claim list from the new
   `verification.json` (`claims[].verdict/evidence/note`; the mechanical
   `reason` stays in `verification.json`).
3. Rewrite ONLY that JSONL line in place, preserving `id`, `url`,
   `author`, `ingested_at`, `classification`, `entities`, `fit`,
   `actionable`, `artifacts` and — critically — any operator-edited
   `status`. Update `claims` only. Keep a pre-edit copy of the line (or of
   the whole file) alongside for audit.
4. Never delete-and-re-emit: re-emission would create a fresh entry with
   `status: pending` and a new `ingested_at`, losing operator state.
5. Alternatively, if a line must be regenerated from scratch: require the
   operator to re-apply the status edit afterwards, and treat the old line
   as superseded (manual decision, never automatic).

## 5. Commands (verified against the CLI)

All commands: `python3 -m tiktok_ingest <command> …`, each accepts
`--state-root` (default `~/.local/state/tiktok-ingest/`); fetch/install
also accept `--share-root`. Verified command set:

| Command | Purpose |
|---|---|
| `install-extractor` | Create local venv + pinned `yt-dlp==2026.08.19` (explicit, never implicit) |
| `fetch-url <url>` | Canonical URL → gated extraction → validated media + cache |
| `prepare <id>` | Frames + normalized WAV in the pinned container |
| `vision <id>` | Vision stage through resident llm-hub chat → `video.md` |
| `audio <id>` | llm-hub-whisper WS transcription → `audio.md` |
| `synthesize <id> --from-file F` | Validate + scrub + persist a supplied synthesis document |
| `verify <id>` | Bounded verification + backlog emission (`--force` re-runs past matching fingerprints) |
| `status [<id>]` | Persisted per-video/per-stage outcomes + backlog emission state |
| `inventory-collect <url> --html F …` | Merge one operator-captured page snapshot (`--html -` reads stdin) |
| `inventory-status <url-or-key>` | Inspect persisted scan state |
| `inventory-plan <url-or-key>` | Print the document-only processing plan |
| `backlog-list [--status S]` | List backlog entries with per-line hashes (read-only) |
| `backlog-show <id>` | Entry detail with claims and verification reasons (read-only) |
| `backlog-decide <id> --decision D [--reason R] --decisions-file F` | Record one explicit operator decision (versioned JSONL) |
| `backlog-plan --decisions-file F` | Dry run with IDs, changes and expected hashes |
| `backlog-apply --decisions-file F` | Apply explicit, still-current decisions (verified backups, receipt, idempotent) |
| `guided-run` | Consent-gated coordination of one tanda through the existing stages (see section 8) |
| `collection-run <url>` | Phase 0 one-command journey: browser inventory → phase-first resource windows → automatic synthesis → pending entries (see section 9) |
| `summary [--collection U] [--json] [--report latest\|PATH]` | Read-only per-video stage table + derived next step, from live state or a saved report (see section 9.6) |
| `web [--port 8484] [--no-browser]` | Local web interface on 127.0.0.1 only (see section 10); `ti web` forwards here |

`verify --force` re-runs verification when fingerprints match; backlog
appends remain duplicate-guarded (see section 4). `synthesize --force`
re-runs past a matching synthesis fingerprint.

### Saved run reports

Every finished (non-dry-run) `collection-run` persists its report JSON
atomically to `<state-root>/reports/<collection-key>/<UTC-timestamp>.json`
(timestamp shape `2026-10-12T09-55-16Z`, the same as run directories) and
narrates the saved path to stderr; stdout stays the unchanged report
JSON. Reports are machine-local and are never written inside the
repository. `summary --report latest` reads the newest report of a named
collection; `summary --report <path>` reads an explicit file.

## 6. Model serving — llm-hub (2026-11 migration)

Every model call goes to the RESIDENT llm-hub services. tiktok-ingest
starts, stops, loads and unloads nothing: no Ollama, no GPU lease, no
swap gates and no container lifecycle remain in this package (the former
gate-cleanup runbook and swap-retry runbook were removed with that
machinery).

- **llm-hub-chat** (user unit `llm-hub-chat.service`): vision at
  `http://127.0.0.1:8081/v1` (OpenAI-compatible chat completions, model
  `qwen-3.8-27b` — the same resident multimodal model). If unreachable
  or not serving the model, the vision stage records `blocked` with the
  instruction `systemctl --user start llm-hub-chat`.
- **llm-hub-whisper** (user unit `llm-hub-whisper.service`):
  transcription at `ws://127.0.0.1:8765/ws/transcribe`, health at
  `http://127.0.0.1:8766/health` (faster-whisper large-v3, CUDA float16,
  always running). An unhealthy service blocks audio with the
  instruction `systemctl --user start llm-hub-whisper`.

Both units are enabled by llm-hub's `install.sh`; both models fit
together in 24 GiB, so there is no VRAM coordination between stages and
no idle-sleep interplay to manage. A failed model request is final for
its window (no auto-retry); re-invocation resumes with completed stages
reused. FFmpeg/ffprobe for CPU preparation still run via podman from
llm-hub's whisper image (`localhost/llm-hub_whisper-service:latest`,
network-isolated, read-only).

## 7. What is tested and what is not

### Live smoke test (opt-in)

```bash
TIKTOK_INGEST_LIVE=1 TIKTOK_INGEST_LIVE_URL=<short video> \
TIKTOK_INGEST_LIVE_COLLECTION=<collection url> \
PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_live_smoke -v
```

It never runs in the default suite: every test needs `TIKTOK_INGEST_LIVE=1`.
With only that variable set, the environment checks still run (read-only:
llm-hub chat answering and serving the vision model, llm-hub-whisper's
health endpoint, and the pinned yt-dlp gate). The video and collection
parts also need their URL variable and skip without it. The video part
runs `ingest` end to end in a temporary state root (your real state is
untouched); every model call goes to the resident llm-hub services and
nothing is started or stopped. The collection part opens the headed
browser (log in first) and is the canary for TikTok layout changes.

Tested (fixture-only, zero network/GPU/podman/model weights):

- The Phase 0 application layer with every external boundary doubled:
  the `collection-run` PHASE-FIRST driver (global order across more
  than five ids — all preparation, then all audio, then all vision,
  then eligible synthesis; resource windows of cap
  5; preparation-based audio eligibility; a failed vision window staying
  closed with its remaining same-window ids pending; exclusion of
  completed/rejected ids; resume without stage
  repetition; second run with zero
  re-processing and zero duplicate entries), the TWO-decision
  authorization contract (browser grant naming the readiness
  checkpoint and the snapshot/audit writes; press-Enter readiness that
  is not an authorization; whole-current-run plan grant over the exact
  frozen ids/URLs with stage reuse states; covered stage windows
  audited as `covered_by_run_plan`; unknown kinds, excess ids and
  unapproved destinations forwarded to the
  human and never auto-granted; ONE grouped verification window with
  the exact validated URLs), the browser boundary
  (run-scoped profile created/removed, readiness checkpoint,
  denial/EOF paths that capture nothing, no
  login-automation surface, container-scoped capture with
  recommendations excluded, DOM change failing explicitly with the
  audit HTML preserved, honest close/cleanup reporting on success,
  error and Ctrl-C), the OpenAI-compatible synthesis adapter (env
  configuration with names-only diagnostics, path/secret scrubbing of
  the outbound payload, tools/retrieval disabled in the request,
  single-attempt semantics, timeout/refusal/malformed resumable stops,
  results persisted only through the real strict validation), the
  llm-hub migration invariants (a run with vision+audio issues no
  podman lifecycle command and probes no Ollama endpoint; the
  audio health precheck naming the systemd unit), the
  collection audio-before-vision ordering,
  and the launcher (usage, forwarding, exit codes, no secrets, no
  installs).

- The guided-run coordinator with stage doubles and scripted consent:
  selection from plan and explicit ids, batch cap and its hard errors,
  exclusion of blocked/photo/complete ids, freshness against current
  state, zero prompts/calls on dry run and on every denial path
  (including EOF and non-interactive stdin), window contents naming
  exactly the ids/destinations of that invocation, vision → health
  precheck → audio ordering, a failed request ending the vision window
  without retry, interrupt paths, the synthesis boundary (missing,
  malformed and valid documents; exact candidate-URL consent), pending
  entries left untouched, resume reusing complete stages without
  repetition, and CLI wiring/exit codes. The CLI wiring test proves the
  `guided-run` command builds the plain production stage bundle (no
  lifecycle hooks exist) and narrates the summary on
  stderr; prompt formatting and per-stage narration are asserted
  directly, and the end-of-run summary reuses the `summary` row/next
  derivation.

- The full verdict matrix including the rules-v2 identity separation
  (identified-without-URLs → `unverifiable`/`no_candidate_urls`;
  no-entity → `unnamed`; indeterminate identity → conservative
  `unverifiable`; entity-inherited URLs through failure and no-support
  paths; operator verdict provenance; serialization round-trips through
  the `Claim`/`BacklogEntry` contracts; rules-version bump invalidating
  recorded verify/emit fingerprints).
- Safe-URL validation, bounded retrieval with fakes, star parser,
  taxonomy normalization, atomic writes, duplicate-guarded emission,
  blocklist permanence, collection-inventory honesty rules, and the
  bounded container probes with operation-order logging.
- The backlog review CLI: listing/detail, strict decisions format with
  malformed-input refusal (including the multi-line-JSON-as-JSONL
  incident), duplicate/unknown IDs, concurrent-change detection, stale
  decisions, accept preserving everything except status, reject/
  blocklist coherence, failure between writes and idempotent recovery,
  collision-free exclusive backups with hash verification, idempotent
  re-application and CLI wiring/exit codes.

Not tested by the suite (by design): real extraction against TikTok,
real model requests to the llm-hub services, the live browser scan, and
any end-to-end run with real state. Those require separate operator
authorization per the MVP-PRD and remain manual procedures.

## 8. Guided runs (`guided-run`)

`guided-run` coordinates the existing stages for one tanda; it adds no
new stage logic, keeps no state of its own and writes nothing directly
(every write still comes from the existing stage functions). The
persisted manifests and fingerprints remain the ONLY authority: the
report it prints is a summary, never a second ledger.

### Selection and freshness

- `--collection` selects the plan's `new_processable` items, capped at
  the batch limit (default 5, hard maximum 5; remaining items stay
  pending for a later batch).
- `--ids` selects explicit new or `partial_resumable` ids. Unknown,
  duplicated or over-cap selections are hard errors; blocklisted ids
  and ids with an emit fingerprint (already complete) are excluded with
  their reason — complete stages are reused, never re-inferred.
- Classification is ALWAYS recomputed at invocation time against the
  current blocklist/processed stores: a plan printed earlier is a
  document, not authority over state that has since changed.

### Authorization (ONE run-plan window, then covered discharges)

`guided-run` builds a run plan from its selection and asks exactly ONE
`run-plan` authorization window before anything runs. The window names
every frozen id and canonical URL, the full stage sequence, the batch
limit, and — when automatic synthesis applies — the text API
endpoint/model. On grant, the covered per-phase windows are discharged
automatically and audited (never fabricated human yeses):

- `tanda`, `prepare`, `gpu` — every id inside the frozen inventory;
- `fetch` — every destination an exact frozen canonical URL;
- `synthesis-api` — the endpoint and EXACT model named in the plan.

Anything outside that scope — a new id, an unapproved URL or a different
endpoint/model — is asked as genuinely new scope and is NEVER
auto-granted.

**Verification keeps its own grouped decision**: candidate URLs only
exist after synthesis, so ONE separate `verify` window lists every exact
validated URL before any retrieval.

The covered `gpu` window keeps its existing shape: vision requests to
the resident llm-hub chat service for all pending ids, then audio
through the resident llm-hub-whisper service. Ids whose vision was
already complete are named for transcription outright; ids that reach
audio only when their vision completes inside the window are
CONDITIONAL (a vision failure or a denial means that id's audio does
not run); ids whose audio is already complete are reused, never
re-transcribed. A failed model request is FINAL for the window (no
auto-retry); nothing is started, stopped, loaded or unloaded by the
run.

Denial, EOF, Ctrl-C or a non-interactive environment stop without
assuming consent (only a literal `yes` grants a window; there is no
global `--yes`). An authorization NEVER persists: every invocation asks
the run-plan window again (and, when it applies, the separate grouped
verification window).

### Consent prompts, progress and the end-of-run summary

Each consent window prints an aligned block whose first line is its
one-line intent, followed by the existing ids/destinations, operations,
limits and effects sections, then the `authorize this window? [yes/no]`
prompt. A denial names the window and states that nothing ran; EOF and a
non-interactive stdin report that consent is never assumed. Stages
narrate a `stage <name> start`/`stage <name> finish ... (reused=..., Ns)`
pair on stderr with the real outcome and measured duration (no fabricated
heartbeat or percentage), and the run ends with a readable summary table
(one row per selected id: per-stage states, author and the derived next
step). All of this UI is stderr-only: the stdout JSON report keeps its
exact shape.

#### Interactive terminal UI (curses) and mandatory fallback

On a TTY, `guided-run` and `collection-run` render a live curses
dashboard — a header bar (run/phase/elapsed), a per-id stage matrix with
state-colored cells (complete green, failed red, running yellow, pending
dim) and a log panel — and show each consent window as an in-place,
**bordered, titled modal** (title line, the full window text, and a
footer with the explicit key hint) that scrolls on small terminals. Color
appears only when the terminal reports color support and otherwise the
SAME layout renders monochrome. The consent contract is UNCHANGED: only
the literal `y`/`yes` grants; EOF, a read error and a non-interactive
stdin deny; the whole window text (title, ids, destinations, operations,
limits, effects) is composed and shown. The TUI draws on the terminal
screen only and NEVER writes to stdout — the JSON report prints only
after the curses session ends, so stdout stays parseable.

| Situation | Behavior |
|-----------|----------|
| TTY + usable `TERM` | curses dashboard + bordered, titled consent modals |
| TTY without color support | same layout, monochrome (no attribute is applied) |
| Non-TTY, pipe, CI, `TERM=dumb` | EXACT line-based prompts and stderr narration (unchanged) |
| `curses` cannot start | same line-based fallback as non-TTY |
| `--dry-run` | zero prompts, zero writes; no TUI is ever started |

The fallback is the SAME `ConsoleConsentProvider` + stderr narration used
before this change, so behavior on any non-TTY path is byte-identical.

### Resident model services (no coordination)

Vision and audio are plain requests to the resident llm-hub services
(section 6); `guided-run` never stops, starts, inspects or restores any
container — there is no whisper precondition for vision. Audio keeps its
health precheck: before transcription, `llm-hub-whisper`'s health
endpoint must answer; if unhealthy, every pending audio id is `blocked`
with the `systemctl --user start llm-hub-whisper` resume instruction.
Non-interactive stdin and EOF still fail closed.

### Synthesis boundary (hard stop)

`synthesize --from-file` is validated-document ingestion ONLY. Without
`--synthesis ID=PATH` the run ends each completed video in a
`waiting_for_synthesis` state, reporting the `video.md`/`audio.md`
paths and the exact resume command. Documents are never generated,
edited or auto-filled; no text-model calls or credentials exist here. A
malformed document is a per-id `failed` outcome and no verify consent
is asked for that id.

### Failure, cleanup and resume policy

Simple and conservative: network/CPU failures are per-id (other ids
continue); ANY non-complete vision outcome (error, interrupt) ends the
vision window for every remaining id — no further vision requests this
invocation, no auto-retry. There is no service cleanup to perform (the
llm-hub units stay resident); partial results are preserved by the
stages themselves. An abrupt kill leaves nothing to roll back: recover
from the evidence of the real state (`status`, stage fingerprints) —
never from a promise. Re-invoking requires NEW authorization for the
pending operations.

### Reporting and backlog

The report keeps stage outcomes separate from claim verdicts (section
2: stage `complete` is NOT claim `confirmed`). Verify appends `pending`
entries exactly as the standalone stage does (duplicate-guarded);
guided-run never accepts/rejects entries, never writes the blocklist,
and never runs `backlog-apply`. After a run, review with the existing
CLI: `backlog-list` → `backlog-show` → `backlog-decide` →
`backlog-plan` → `backlog-apply`.

### Concurrency

There is still no product-wide lock. guided-run is a writer like any
other (through the stage functions) and the review CLI rewrites
backlog/blocklist files: run them in an EXCLUSIVE-writer operational
window — never concurrently with each other or with a verify emission.
A partial lock taken only by guided-run would be a false guarantee, so
none was added.

## 9. Phase 0 one-command runs (`collection-run`)

`./run-collection.sh "<collection-url>"` (a thin fail-fast launcher)
dispatches to `python3 -m tiktok_ingest collection-run <url>`. The
command coordinates the existing engine; it adds no stage logic and no
second state.

### What happens, in order

1. **Preflight (read-only)**: Playwright availability (never
   installed by this pipeline), text-API configuration presence
   (missing env NAMES only, never values), current scan state, the
   exclusive-writer requirement and the planned destinations of the
   run.
2. **Browser inventory (decision 1 of 2)**: a headed Chromium through
    Playwright with a
    DEDICATED run-scoped profile (never your default profile or ambient
    sessions). You handle login/captcha manually — the controller can
    only open/read/close, so automation of login is impossible — and
    then signal readiness at a press-Enter CHECKPOINT that is a
    readiness signal, NOT a second authorization (the browser grant
    already named the capture and its audit/state writes). The capture
    applies the existing collection contracts: recommendations are
    counted, never items; declared/observed/end/access evidence is
    recorded separately; a changed DOM fails explicitly; the HTML is
    persisted under `<state>/collections/<key>-captures/` for audit.
    Verified live scope (2026-09-30 authenticated capture): the
    collection renders inside `div[data-e2e=collection-item-list]`
    (the production defaults); the notifications inbox renders
    elsewhere and its links stay `out_of_container_count`. Access
    markers match rendered text only — the page ships its
    unavailability string as script i18n JSON on every page, which is
    never evidence.
    A fresh capture with ZERO items over a stored scan that had items
    prints one `warning: collection <key> returned 0 items but
    previously had <N>; …` line on stderr (browser inventory also adds it
    to `cleanup_notes`; `inventory-collect` prints it too). Suspect a
    TikTok layout change. The merge stays union-only, nothing is dropped
    and the run continues.
    The browser closes and the profile is removed BEFORE any media
    work. An abrupt kill cannot guarantee cleanup: recover from real
    state, never from a promise.
3. **Run-plan grant (decision 2 of 2)**: right after the capture the
    eligible inventory is FROZEN (exact ids, canonical download URLs,
    stage reuse states). ONE window asks you to accept the WHOLE current
    run: the collection-wide phase order (preparation ALL → audio ALL →
    vision ALL → eligible synthesis ALL) crossed in resource windows of
    at most five ids, the stage pipeline, the synthesis
    endpoint/model, and the deferred grouped verification. Denial stops
    before any processing. Stage windows provably inside
    this scope are discharged by the grant and audited as
    `covered_by_run_plan`; unknown kinds, excess ids or unapproved
    destinations are asked as genuinely new
    scope — never auto-granted.
4. **Phase-first resource windows**: the frozen inventory is processed
    in GLOBAL phase order — preparation ALL → audio ALL → vision ALL →
    eligible synthesis ALL — not one complete pipeline per batch.
    Fixed slices of at most five ids are RESOURCE WINDOWS visited ONCE
    per phase (a preparation window, then audio windows, then vision
    windows). Each window runs through the existing `guided-run` phase
    machinery (section 8) and its stage windows are discharged by the
    plan grant; a fresh coordinator is built per window. No synthesis
    request is made until every vision window has ended. Audio
    eligibility is PREPARATION completion, not vision completion. A
    failed vision window stays closed and its remaining same-window ids
    stay pending; later frozen normal windows keep the existing attempt
    policy. Only a boundary failure, an interrupt, or a global
    precondition blocks ALL later effects, grouped verification
    included. Completed and blocklisted ids were already excluded by
    the freeze, the loop is bounded by construction and nothing outside
    the frozen scope is absorbed.
5. **Automatic synthesis**: runs as its OWN collection phase, only
    after the vision phase has ended for the whole frozen selection;
    each id still needs complete vision and audio evidence. Configured
    ONLY via
    `TIKTOK_INGEST_TEXT_API_BASE_URL`, `TIKTOK_INGEST_TEXT_API_KEY`,
    `TIKTOK_INGEST_TEXT_MODEL` (optional
    `TIKTOK_INGEST_TEXT_API_TIMEOUT_SECONDS`, default 120 s). Values
    are never persisted and are scrubbed from errors. One
    OpenAI-compatible request per id, tools/retrieval/execution
    disabled, structured JSON requested, carrying only sanitized
    `video.md`/`audio.md` text. The response is a CANDIDATE document:
    it is persisted only after passing the existing strict
    `SynthesisDocument` validation and credential scrub. Timeout,
    refusal, malformed output or missing configuration are
    resumable synthesis stops — vision/audio are never repeated.
6. **Verification and delivery (one grouped decision)**: candidate URLs
    are unknown until synthesis, so ONE grouped window shows every
    exact validated URL accumulated over the run before any retrieval;
    a grant verifies each ready id once. Entries are appended as
    `pending` only; review stays with `backlog-list` →
    `backlog-show` → `backlog-decide` → `backlog-plan` →
    `backlog-apply`.

### Local model backend (llm-hub)

The local backend for BOTH vision and synthesis is llm-hub's
`llama-chat` (llama.cpp, Qwen3.8-27B multimodal, 64k context, reasoning
off) at `http://127.0.0.1:8081/v1`; vision calls it directly with model
`qwen-3.8-27b`, and the automatic synthesis adapter uses the
`TIKTOK_INGEST_TEXT_API_*` environment (base URL, key, model) exported
from `shell/exports.sh` (not secrets). `ai/tiktok-ingest/install.sh`
(idempotent) links and enables the user units
`ai/services/llm-hub-chat.service` and `llm-hub-whisper.service`, which
start the services after a host/WSL restart. Transcription runs on the
resident `llm-hub-whisper` (WS `:8765`, health `:8766`).

Both models are resident together (≈23 of 24 GiB measured with both
awake): there is no free-VRAM gate, no idle-sleep interplay and no
coordination between vision, synthesis and transcription any more. If a
service is down its stage blocks fail-closed with the exact
`systemctl --user start <unit>` instruction; start it and re-run —
completed stages are reused with zero re-inference.

### Exit codes

`0` success · `1` failures, interruption or hard blocks · `2` usage
errors (invalid/missing URL is rejected before any effect).

`--dry-run` prints preflight plus the document-only plan with zero
browser, network, container, GPU, API or write activity.

### 9.6 Reading a run later (`summary`)

The raw report JSON is large; `summary` renders the durable per-video
stage state as one row per item (`fetch prepare audio vision synthesis
verify`), the backlog status, the author handle (derived from the
canonical URL) and a derived `next` line — e.g. `vision pending (audio
saved)` when audio completed but vision stopped, `synthesis pending`, or
`complete`. The human table is the default; `--json` emits a structured
object for scripting.

```bash
python3 -m tiktok_ingest summary                       # all processed items
python3 -m tiktok_ingest summary --collection "<key>"  # one collection's plan items
python3 -m tiktok_ingest summary --collection "<key>" --json
python3 -m tiktok_ingest summary --collection "<key>" --report latest
```

`summary` reuses the SAME stage derivation as the run commands and is
READ-ONLY: it imports no write path and touches no browser, network,
GPU, container or model. With `--report latest`/`PATH` it reads a saved
report instead of live state; a saved report records stage snapshots but
not per-item backlog status, so those rows show `backlog: n/a`.

## 10. Local web interface (`ti web`)

`ti web` (or `python3 -m tiktok_ingest web [--port 8484] [--no-browser]
[--state-root DIR]`) serves the Phase 1 interface at
`http://127.0.0.1:8484/` and opens the browser. It binds to 127.0.0.1 only;
a busy port fails loudly, with no fallback. Ctrl+C stops it; an active web
run is cancelled and joined first so the engine's interruption handling
runs.

Views (all read from disk on every request, nothing cached as authority):
Collections (declared vs observed counts, status, end-of-list evidence,
access markers, plan), Runs (saved reports), Blocks (blocked stages with the
same `next` line `summary` prints, plus run-level stop reasons), Evidence
(video status and allow-listed artifacts) and Review (pending backlog).

Review mirrors the CLI: a decision (`accepted` or `rejected`, a rejection
needs a reason) is bound to the entry hash shown in the detail; "Preview
plan" shows what apply would do and "Apply" is bound to that previewed plan
(a changed plan is refused). Web decisions go to
`<state-root>/reviews/web-decisions.jsonl`. Decisions and apply are refused
while a web run is active. A CLI writer running at the same time is only
covered by the review module's hash checks, as in the terminal.

Runs: launching `ingest` from the web is built but OFF in this phase
(`POST /api/runs` answers 409) until the Phase 0 acceptance checklist is
closed; items 7 and 8 still lack live evidence. When enabled, each consent
window appears as a modal with the exact CLI fields and only Authorize or
Deny; browser login readiness is a "Ready" button. Narration streams over
server-sent events with the same lines the CLI prints. Cancel is
cooperative: it denies a pending window or interrupts at the next narration
line; a long stage call finishes first, bounded by its own timeout. Only one
run at a time. (Stale `active_gpu_lease.json` files left by pre-migration
runs are ignored, never deleted.)

Security: every non-GET request needs the per-start token from the page
(`X-TI-Token`); `Host` and `Origin` must be the loopback address; responses
carry a strict Content-Security-Policy, no-store, nosniff and frame denial.
This protects against other web pages in your browser, not against other
users or processes on the same machine (accepted single-operator boundary,
PRD section 12).
