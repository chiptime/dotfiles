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
vision <id> ──► gated local vision (isolated Ollama, qwen3.5:9b) ──► video.md
        ▼
audio <id> ──► whisper WS transcription (large-v3, es) ──► audio.md
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
| `install-ollama` | Download + verify (sha256 + size) isolated Ollama 0.34.1 (explicit) |
| `import-model` | Copy vision model from global store into isolated models dir (read-only source) |
| `fetch-url <url>` | Canonical URL → gated extraction → validated media + cache |
| `prepare <id>` | Frames + normalized WAV in the pinned container |
| `vision <id>` | Gated local vision stage → `video.md` |
| `audio <id>` | Whisper WS transcription → `audio.md` |
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

`verify --force` re-runs verification when fingerprints match; backlog
appends remain duplicate-guarded (see section 4). `synthesize --force`
re-runs past a matching synthesis fingerprint.

## 6. Gate cleanup and operational validation limits

Any non-complete vision ending (fired gate, handled error, missed
deadline, operator interrupt) triggers ONE bounded, idempotent unload
attempt for this stage's model on this isolated server, then verifies
empty `/api/ps` residency and observed GPU release. Original failure and
cleanup failure are BOTH preserved; release is claimed only with positive
evidence. The cleanup never stops other sessions' processes and never
stops the isolated server itself (`ollama_runtime.stop_server` is
CLI/operator-only).

Operationally validated so far (real GPU window): load → unload → release
mechanism against real VRAM, zero overshoot vs baseline. NOT yet
validated: cleanup after a REAL mid-inference gate fire (gates are never
provoked per PRD section 6) and the multi-frame interrupt path (double
tested with test doubles only).

Validation limits of the gates: they bound resource usage per stage; they
do NOT validate content quality. A quiet host may pass a swap gate that a
busy host fails — gate fires under ambient pressure are ambient, not
defects.

## 7. What is tested and what is not

Tested (582 tests in the 2026-09-21 local preflight, fixture-only,
zero network/GPU/podman/ollama):

- The full verdict matrix including the rules-v2 identity separation
  (identified-without-URLs → `unverifiable`/`no_candidate_urls`;
  no-entity → `unnamed`; indeterminate identity → conservative
  `unverifiable`; entity-inherited URLs through failure and no-support
  paths; operator verdict provenance; serialization round-trips through
  the `Claim`/`BacklogEntry` contracts; rules-version bump invalidating
  recorded verify/emit fingerprints).
- Safe-URL validation, bounded retrieval with fakes, star parser,
  taxonomy normalization, atomic writes, duplicate-guarded emission,
  blocklist permanence, collection-inventory honesty rules, gate-failure
  cleanup paths with operation-order logging.
- The backlog review CLI: listing/detail, strict decisions format with
  malformed-input refusal (including the multi-line-JSON-as-JSONL
  incident), duplicate/unknown IDs, concurrent-change detection, stale
  decisions, accept preserving everything except status, reject/
  blocklist coherence, failure between writes and idempotent recovery,
  collision-free exclusive backups with hash verification, idempotent
  re-application and CLI wiring/exit codes.

Not tested by the suite (by design): real extraction against TikTok, real
GPU inference, real whisper service, the live browser scan, and any
end-to-end run with real state. Those require separate operator
authorization per the MVP-PRD and remain manual procedures.
