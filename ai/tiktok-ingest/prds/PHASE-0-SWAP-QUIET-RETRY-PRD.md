# PRD — Phase 0 Addendum: Bounded Vision Swap-Retry After Measured Quiet

**Status:** DRAFT. This PRD records product requirements only. It authorizes
NO implementation, NO execution, NO network/browser/GPU/service operation, NO
production state mutation, NO commit and NO push. It does not modify the
original Phase 0 task checklist and does not claim any feature is implemented.
**Owner:** Bruno. **Date:** 2026-09-30.
**Decision authority:** the confirmed conversation decision ledger restated in
section 2 (D-01..D-10). Confirmed decisions are not re-opened here.
**Context:** [PHASE-0-ONE-COMMAND-PRD.md](PHASE-0-ONE-COMMAND-PRD.md),
[PRD.md](../PRD.md), [MVP-PRD.md](../MVP-PRD.md),
[README.md](../README.md), [OPERATIONS.md](../OPERATIONS.md) (implemented
contracts). All file:line citations were verified against the working tree on
2026-09-30, which contains uncommitted consent-redesign changes; those changes
are preserved untouched and this document assumes them as the current baseline.

## 1. Context and problem

During an authorized real `collection-run` over a nine-video collection,
processed in batches of at most five, four videos completed and five ended
partial after the vision stage's
`swap_delta` gate fired. The gate limit is 512 MiB of systemwide swap I/O per
phase, measured as the delta of cumulative `/proc/vmstat` counters
(`pswpin + pswpout`, scaled by page size) from an immediate per-phase baseline
(`src/tiktok_ingest/gates.py:94-104`, `src/tiktok_ingest/vision.py:741-744`,
`src/tiktok_ingest/vision.py:815-825`). Observed failing deltas from the
authorized session's console evidence (recorded 2026-09-30; operator-recorded,
not re-derivable from the repository):

| Observed swap I/O delta | In MiB | Overshoot over 512 MiB |
|---|---|---|
| 556,335,104 B | 530.56 MiB | +18.56 MiB |
| 553,463,808 B | 527.82 MiB | +15.82 MiB |
| 538,726,400 B | 513.77 MiB | +1.77 MiB |

These are small overshoots relative to the configured budget
(1.77–18.56 MiB over 512 MiB). This observation alone does not establish
the severity or cause of the memory pressure. In particular:

- The counters are **systemwide**. They cannot attribute paging to any
  process, so the historical culprit is unknowable from this evidence.
- Ambient pressure alone can fire the gate on a busy host
  ([OPERATIONS.md](../OPERATIONS.md), "gate fires under ambient pressure are
  ambient"). Waiting may therefore help — or may not, if the paging is driven
  by the model workload itself. **One extra authorized trial is not proof
  that retry cures swap pressure, and this PRD promises no such cure.**
- The current contract is deliberately final: a fired gate closes the GPU
  window for the remaining IDs of the batch and is never retried
  (`src/tiktok_ingest/gates.py:182-183`, `src/tiktok_ingest/gates.py:378-387`,
  `src/tiktok_ingest/vision.py:23-24`,
  `src/tiktok_ingest/collection_run.py:515-527`,
  [OPERATIONS.md](../OPERATIONS.md) sections 8–10).

The operator confirmed a bounded product change: after a recoverable
vision `swap_delta` failure, offer **one** extra attempt per video per
invocation, only behind a **new explicit consent**, and only after the swap
activity has been **measured** to stabilize within a bounded, read-only
cooldown — restoring the shared Whisper service healthy before any human
wait, and telling the user plainly when the host does not recover. This PRD
consolidates that confirmed design into requirements.

## 2. Decision ledger (confirmed, stable IDs)

| ID | Confirmed decision |
|---|---|
| **D-01** | Retry only a recoverable vision `swap_delta` gate failure whose failure cleanup was positively verified. Offload, unload-verification, service-identity and other failures are never retried. |
| **D-02** | Exactly ONE extra attempt per video per invocation — not loop-until-clean. The retry sits behind a NEW explicit consent; the original run-plan grant continues to prohibit retries (no automatic retry — a new ask). Unknown or new scope still prompts; nothing is auto-granted. |
| **D-03** | Before any wait: close the failed GPU window (verified model unload, isolated server stop) and RESTORE the SAME known Whisper service, verified healthy, FIRST. No shared service is held down while the operator decides. |
| **D-04** | Cooldown is read-only and starts only after consent: minimum 60 s floor before any admission, hard maximum 180 s total from cooldown start, 5 s sampling interval, and 3 consecutive intervals with swap I/O ≤ 1 MiB/s. These defaults are proposed operational heuristics requiring validation, not efficacy proof. The measurement uses cumulative need (rate per interval from the cumulative counters), never "occupied swap is decreasing"; unreadable or decreasing counters fail closed. |
| **D-05** | Whisper is stopped AGAIN for the retry only under an explicitly named new lifecycle authority, with identical configuration re-checked (`same_service_config` semantics), and admission/VRAM re-checked after that transition. Final-conditions drift resets the stability count, NOT the time cap; pre-mutation healthy samples never guarantee post-change conditions. All waits stay bounded and the outage is disclosed. A new isolated Ollama instance is started only if one does not already answer; gates are unchanged. |
| **D-06** | Gates unchanged: ≤512 MiB systemwide swap I/O delta per attempt, ≥20,480 MiB free VRAM, zero CPU offload, current 600 s vision stage deadline. Each new attempt takes a fresh immediate baseline only at its start — no mid-attempt resetting. The cooldown has its own separate bounded budget. Accounting after a service transition must be explicit; oscillation never restarts the 180 s cap. The 60 s floor and the 3 stable windows may overlap (samples are collected throughout, admission happens no earlier than 60 s). No hidden added deadlines; every probe has bounded duration and accurate timeout semantics. |
| **D-07** | Retry re-runs vision only. Valid fetch/prepare results are reused; completed audio, synthesis and verification are never repeated. Frame-level reuse inside the retried vision stage is NOT proven — it is an explicit assumption and implementation discovery, never a promise. The failed stage's partial artifacts are not treated as complete, and recovery does not erase the original attempt or its failure evidence. |
| **D-08** | Deadline/cap exhaustion means the retry is NOT executed (the video stays pending, resumable). A failed second attempt ends the matter for this invocation: no third attempt, no re-prompt; state stays resumable and the status is explained. Minimal default: remaining same-GPU-window IDs are not executed after the gate fired — no silent skip, reorder, or automatic retry. Do not claim `collection-run --ids` exists (it does not; `--ids` exists only on `guided-run`, `src/tiktok_ingest/cli.py:624-630` vs `cli.py:656-718`); no invented workaround flags. How subsequent frozen batches and the current driver interact is an OPEN implementation detail if uncertain — without newly authorizing extra model loads after the final window. No global hard-stop expansion beyond the original draft unless separately approved; retry exhaustion (video/window-scoped) is distinct from service-restoration hard-stop (global). |
| **D-09** | Truthful UX: the initial pause states reason, video ID and budget; every 15 s the measured swap rate, the stable-interval count and the remaining time are reported; if the rate stays above threshold the output says it has NOT recovered; instability resets the stable count, never the 180 s cap; unavailable measurement is reported distinctly; expiry reports "retry not executed, video pending"; a failed second attempt reports "retry exhausted"; the actual re-attempt reports real start/finish durations and outcome. No fake percentages or heartbeat promises. Narration goes to stderr; the final stdout structured JSON keeps its current parseability. |
| **D-10** | No unattended workload cleanup: never `swapoff`, `drop_caches`, killing arbitrary containers, model switching, or gate relaxation — resource decisions belong to the operator. Only the specific approved Whisper lifecycle mutations are performed. Consent EOF/non-TTY fails closed; interrupted cleanup keeps UNKNOWN semantics; the current grouped verification may not proceed after a global restoration hard stop. |

## 3. Objectives and non-objectives

**Objectives**

- O1: Convert marginal, plausibly-transient `swap_delta` vision failures into
  ONE operator-authorized, measured, bounded second chance per video per
  invocation.
- O2: Keep every existing safety contract intact: gates, consent windows,
  service lifecycle authority, fail-closed parsing, honest outcome vocabulary.
- O3: Give the operator measured feedback during the wait and truthful final
  states in every terminal branch (denial, expiry, measurement failure,
  cleanup failure, retry success, retry exhaustion, interrupt).
- O4: Preserve complete auditability: the original failure evidence and the
  retry's own measurements are both durable.

**Non-objectives (out of scope, stated never implied)**

- N1: No automatic retries of any kind; no loop-until-clean; no third attempt.
- N2: No retry for non-`swap_delta` failures (offload, unload verification,
  service identity, fetch/prepare/audio/synthesis/verify failures).
- N3: No gate weakening, threshold tuning, or baseline manipulation.
- N4: No unattended host cleanup (swapoff, cache dropping, killing arbitrary
  workloads, model switching).
- N5: No claim — implicit or explicit — that waiting cures swap pressure or
  that observed marginal overshoots imply retry success.
- N6: No changes to the review flow, backlog semantics, or verdict matrix.
- N7: No SDD artifacts, phases, or implementation authorization from this
  document.

## 4. Architecture and trust boundaries

The retry is a bounded sub-flow inside the existing `collection-run` /
guided-coordination architecture, not a new framework:

1. **Original attempt** — unchanged vision stage: immediate phase baselines,
   gates after every request, one bounded idempotent cleanup on failure
   (`src/tiktok_ingest/vision.py:15-26`, `vision.py:730-755`, `vision.py:815-825`).
2. **Eligibility** — read-only evaluation from durable typed evidence
   (section 6); only a verified-cleanup `swap_delta` vision failure qualifies
   (D-01).
3. **Window closing + restoration** — the failed GPU window is closed
   positively (verified unload, isolated server stop) and the SAME known
   Whisper service is restored and verified healthy BEFORE any wait
   (D-03; `src/tiktok_ingest/whisper_lifecycle.py:196-280`,
   `src/tiktok_ingest/guided.py:314-347`).
4. **New consent** — a dedicated retry authorization window naming the video,
   the failure, the measured overshoot, and the full budget (D-02). The
   run-plan grant does NOT cover it; its own limits still say "a failed id is
   never reattempted this invocation" for everything outside this new ask
   (`src/tiktok_ingest/collection_run.py:515-527`).
5. **Read-only cooldown** — cumulative-counter sampling with the D-04 bounds;
   no mutation of any kind.
6. **Re-admission** — stop Whisper again under the retry grant, re-verify
   identical configuration, re-check admission gates from fresh immediate
   baselines (D-05, D-06).
7. **Retry attempt** — a normal vision stage run for that one video, gates
   unchanged; all other completed stages reused (D-07).

**Trust boundaries (unchanged in kind):** every external effect sits behind an
explicit authorization; EOF/non-TTY denial stops without assuming consent;
service lifecycle authority is bounded to the named known services; the GPU
window never adopts a preexisting isolated server; the exclusive-writer rule
holds; VLM output is evidence, never instructions.

## 5. Data schema and state machine

### 5.1 Additive durable evidence (proposed field names; additive-compatible)

- `meta.json` gate ledger entries: today `GateLedger.to_jsonable()` persists
  `{gate, passed, reason}` and OMITS the raw `detail`
  (`src/tiktok_ingest/gates.py:401-405`), while `GateResult` itself carries a
  typed `gate` id (`gates.py:182-186`) and `GateViolation` a typed `gate`,
  `reason` and `detail` (`gates.py:39-46`). Requirements: eligibility MUST use
  the persisted typed gate id (identity is already stored — prose parsing of
  reason strings MUST NOT be introduced); the ledger's persisted form SHOULD
  be extended additively with the raw samples/counters needed for cooldown
  auditing (e.g. cumulative `swap_io_bytes` values and timestamps). No
  existing field is renamed or removed; old manifests remain readable.
- Vision stage record: `StageRecord.attempts` exists and counts attempts
  (default 1, minimum 1 — `src/tiktok_ingest/contracts.py:252-277`). The
  retry cap MUST be enforced as a per-invocation extra-attempt rule; it MUST
  NOT be implemented as a blanket lifetime `attempts >= 2` rejection, which
  would silently block legitimate later invocations.
- Retry provenance: the retry attempt records its own start/finish
  timestamps, durations, measured cooldown samples, and outcome, reusing the
  existing manifest structure; no second job ledger is introduced
  (consistent with P0-FR-09 of the Phase 0 PRD).

### 5.2 State machine (per video, per invocation)

| # | From | Event | Guard | To | Durable record |
|---|---|---|---|---|---|
| T1 | vision attempt 1 | `swap_delta` gate fired | typed gate id is `swap_delta`; stage is vision; cleanup verified | `retry_eligible` | original GateResult + cleanup evidence preserved |
| T2 | `retry_eligible` | other gate/failure kind | — | terminal per existing rules (window ends) | unchanged vocabulary |
| T3 | `retry_eligible` | consent asked, operator denies / EOF / non-TTY | fail closed | `retry_declined_pending` | denial + reason; video pending, resumable |
| T4 | `retry_eligible` | consent granted | — | `cleanup_restore` | consent window identity + grant record |
| T5 | `cleanup_restore` | unload verified, isolated server stopped, SAME Whisper restored + healthy | identity/config match | `cooldown_sampling` | restoration + health evidence |
| T6 | `cleanup_restore` | any cleanup/restore/health step fails | — | `blocked_global` | actual partial service state; progress blocked; no rollback claim (existing P0-FR-11 semantics) |
| T7 | `cooldown_sampling` | each 5 s sample | counters readable | sample appended | cumulative values + timestamp |
| T8 | `cooldown_sampling` | 3 consecutive intervals ≤ 1 MiB/s AND elapsed ≥ 60 s | within 180 s cap | `readmission` | stable-window evidence |
| T9 | `cooldown_sampling` | interval > 1 MiB/s | — | `cooldown_sampling` (stable count reset to 0; cap NOT restarted) | reset recorded |
| T10 | `cooldown_sampling` | 180 s cap reached without T8 | — | `cooldown_expired` | "retry not executed, video pending" |
| T11 | `cooldown_sampling` | counter unreadable/implausible | fail closed | `measurement_unavailable` | distinct from expiry; retry not executed |
| T12 | `readmission` | Whisper stopped again, config identical, fresh admission gates pass | named retry authority | `retry_running` | re-admission evidence |
| T13 | `readmission` | config drift / final conditions differ | — | `cooldown_sampling` (stability count reset; cap NOT restarted) | drift recorded; no pre-mutation sample trusted |
| T14 | `retry_running` | stage completes | gates green | `retry_complete` | attempt 2 record; reused stages marked reused |
| T15 | `retry_running` | gate/error/deadline | — | `retry_exhausted` | attempt 2 failure evidence; no third attempt |
| T16 | any | operator interrupt | — | `interrupted_unknown` | UNKNOWN semantics reported honestly; nothing assumed |

Terminal states are honest and distinct: `retry_declined_pending`,
`blocked_global`, `cooldown_expired`, `measurement_unavailable`,
`retry_complete`, `retry_exhausted`, `interrupted_unknown`. The existing rule
that a fired gate ends the GPU window for the remaining same-window IDs is NOT
relaxed (D-08); retry exhaustion is video/window-scoped and is reported
differently from the global service-restoration hard-stop.

## 6. Functional requirements

RFC 2119 applies. Each requirement traces to ledger decisions (section 2) and
cites current evidence; anything not evidenced here is an ASSUMPTION
(section 13) or OPEN QUESTION (section 14).

- **FR-01 (D-01)** The application MUST offer a retry ONLY when the durable
  evidence shows a vision-stage failure whose typed gate id is `swap_delta`
  and whose failure cleanup was positively verified (unload verified, GPU
  release observed). It MUST NOT offer a retry for offload,
  unload-verification, service-identity, fetch, prepare, audio, synthesis or
  verify failures, nor for vision failures without verified cleanup.
  *Evidence:* typed gate ids `src/tiktok_ingest/gates.py:28-32`;
  `GateViolation.gate` `gates.py:39-46`; cleanup contract
  `src/tiktok_ingest/vision.py:21-26`.
- **FR-02 (D-02)** The application MUST provide at most ONE extra vision
  attempt per video per invocation. It MUST NOT loop, MUST NOT re-prompt
  after a failed second attempt in the same invocation, and MUST NOT treat
  the original run-plan grant as covering the retry. *Evidence:* current
  one-attempt contract `src/tiktok_ingest/collection_run.py:515-527`
  ("a failed id is never reattempted this invocation"; "no automatic retry
  of any operation").
- **FR-03 (D-02, D-09)** The retry MUST sit behind a NEW explicit consent
  window that names: the video ID, the failed gate and its measured
  overshoot in MiB, the full budget (cooldown bounds, gates, stage deadline),
  the Whisper stop/restore operations under the retry authority, and the
  effects of a second failure. EOF, denial or a non-interactive environment
  MUST stop without assuming consent. *Evidence:* consent window shape
  `src/tiktok_ingest/guided.py:103` and usage
  `src/tiktok_ingest/collection_run.py:220-253`.
- **FR-04 (D-02)** Unknown or new scope presented during the retry flow MUST
  be asked as genuinely new scope and MUST NOT be auto-granted; the retry
  consent covers exactly what it names. *Evidence:* run-plan limits language
  `src/tiktok_ingest/collection_run.py:522-527`.
- **FR-05 (D-03)** Before asking for retry consent or starting the cooldown,
  under the original window's already-granted cleanup/recovery authority,
  the application MUST
  close the failed GPU window (verified model unload; isolated Ollama server
  stop when this run started it) and then MUST restore the SAME known Whisper
  service and verify it healthy. The shared service MUST NOT be left stopped
  while the operator waits or decides. *Evidence:* stop/restore verification
  `src/tiktok_ingest/whisper_lifecycle.py:196-280`; health polling bound
  `src/tiktok_ingest/guided.py:314-347` (60 s ready deadline,
  `guided.py:89`).
- **FR-06 (D-03)** Restoration MUST positively match the recorded service
  identity and configuration (name, image, identity-relevant environment)
  before the cooldown starts; a mismatch is a restoration failure and blocks
  progress with the actual state reported. *Evidence:* `verify_known_service`
  and `same_service_config`
  `src/tiktok_ingest/whisper_lifecycle.py:145-174`.
- **FR-07 (D-04)** The cooldown MUST be read-only and bounded: sampling every
  5 s; admission eligible only at ≥ 60 s from cooldown start; hard total cap
  of 180 s from cooldown start; stability defined as 3 consecutive intervals
  with swap I/O ≤ 1 MiB/s. These values are proposed operational heuristics
  and MUST be validated (section 12) — they are not efficacy claims.
- **FR-08 (D-04)** The cooldown rate MUST be computed as the need per
  interval from the CUMULATIVE counters (`pswpin + pswpout` scaled by page
  size) — never from a decreasing occupied-swap reading. Unreadable,
  missing or implausible counters MUST fail closed (retry not executed) and
  be reported distinctly. *Evidence:* cumulative snapshot
  `src/tiktok_ingest/gates.py:94-104`; fail-closed parsing
  `gates.py:107-132`.
- **FR-09 (D-04, D-06)** An interval above the threshold MUST reset the
  consecutive-stable count to zero and MUST NOT restart the 180 s cap. The
  60 s floor and the 3 stable windows MAY overlap: samples are collected
  from cooldown start, and admission is evaluated no earlier than 60 s. No
  additional hidden deadlines MAY be introduced.
- **FR-10 (D-05)** Whisper MUST be stopped again for the retry only under the
  explicitly named retry lifecycle authority; the identical configuration
  MUST be re-checked at that moment; admission gates and free VRAM MUST be
  re-checked AFTER the transition, from fresh measurements. A new isolated
  Ollama instance MUST be started only if one does not already answer on the
  isolated port; existing adoption prohibitions are unchanged. *Evidence:*
  preexisting-server rule `src/tiktok_ingest/guided.py:273-274`,
  `vision.py` preconditions `src/tiktok_ingest/vision.py:15-20`.
- **FR-11 (D-05)** Final-conditions drift discovered at or after re-admission
  (e.g. VRAM no longer free, service state changed) MUST reset the stability
  count and return to sampling within the SAME 180 s cap; pre-mutation
  healthy samples MUST NOT be treated as guarantees for post-change
  conditions. All transition waits MUST be individually bounded with accurate
  timeout semantics, and the Whisper outage during the retry window MUST be
  disclosed in the narration.
- **FR-12 (D-06)** Each attempt MUST take its immediate baseline only at its
  own start; the retry attempt MUST NOT inherit or reset mid-attempt
  baselines. Swap accounting across the service transition MUST be explicit
  (which samples belong to which budget), and oscillation MUST NOT restart
  the cooldown cap. Per-probe durations MUST be bounded.
  *Evidence:* immediate-baseline rule `src/tiktok_ingest/vision.py:741-744`.
- **FR-13 (D-06)** The gates MUST remain unchanged for the retry attempt:
  ≤ 512 MiB systemwide swap I/O delta per attempt, ≥ 20,480 MiB free VRAM,
  zero CPU offload, and the current 600 s vision stage deadline. The cooldown
  budget is separate from the stage deadline and both are enforced
  independently. *Evidence:* `src/tiktok_ingest/config.py:211`, `config.py:213`,
  `config.py:185`, `src/tiktok_ingest/vision.py:33`, `vision.py:723`.
- **FR-14 (D-07)** The retry MUST re-run the vision stage only. Valid fetch
  and prepare results MUST be reused; completed audio, synthesis and
  verification MUST NOT be repeated. Frame-level reuse inside the retried
  vision stage is an ASSUMPTION (A-02), not a requirement.
- **FR-15 (D-07, D-01)** The failed attempt's partial artifacts MUST NOT be
  treated as complete, and the original attempt, its gate failure and its
  cleanup evidence MUST remain durable and unmodified; the retry is recorded
  as additional evidence, never as a replacement.
- **FR-16 (D-01, evidence)** Retry eligibility MUST be decided from the
  persisted typed gate field, not by parsing reason prose. The persisted gate
  ledger SHOULD be extended additively with raw counters/samples sufficient
  to audit the cooldown; existing consumers MUST keep parsing current
  manifests. *Evidence:* typed `GateResult.gate` `src/tiktok_ingest/gates.py:182-186`;
  `GateLedger.to_jsonable` currently omitting `detail`
  `gates.py:401-405`.
- **FR-17 (evidence, D-02)** Attempt accounting MUST enforce the cap as a
  per-invocation extra-attempt rule. It MUST NOT reuse
  `StageRecord.attempts` as a lifetime limit (a lifetime `attempts >= 2`
  rejection would block legitimate re-invocations). *Evidence:*
  `StageRecord.attempts` default/minimum `src/tiktok_ingest/contracts.py:259`,
  `contracts.py:274-277`.
- **FR-18 (D-08)** If the cooldown cap (or any bounded wait) is exhausted
  without stability, the retry MUST NOT be executed; the video MUST remain
  pending and resumable, and the output MUST say the retry was not executed.
  Remaining same-GPU-window IDs MUST NOT be silently skipped, reordered or
  retried; their state is reported per the existing window-ending rule.
  *Evidence:* `src/tiktok_ingest/OPERATIONS.md` sections 8–10 (fired gate
  ends the GPU window for remaining IDs).
- **FR-19 (D-08)** A failed second attempt MUST end retrying for this video
  in this invocation: no third attempt, no re-prompt. The application MUST
  keep resumable state and explain the current status, distinguishing
  video/window-scoped retry exhaustion from the global
  service-restoration hard-stop.
- **FR-20 (D-08)** The product MUST NOT invent selection flags:
  `collection-run` has no `--ids` today (positional URL, `--limit`,
  `--declared-count`, `--end-evidence`, `--dry-run` —
  `src/tiktok_ingest/cli.py:656-718`); `--ids` exists only on `guided-run`
  (`cli.py:624-630`). How subsequent frozen batches reconcile with the retry
  flow is OPEN (OQ-03); until decided, the conservative default applies: no
  automatic skip, no extra model loads after the final window.
- **FR-21 (D-09)** Progress narration MUST be truthful: the initial pause
  states reason, ID and budget; every 15 s it reports the measured swap rate,
  the stable-interval count and remaining time; sustained
  above-threshold rates are reported as NOT recovered; unavailable
  measurement is distinct from expiry; the actual re-attempt reports real
  start/finish durations and outcome. Fake percentages and heartbeat
  promises are forbidden. *Evidence:* existing start/finish narration pattern
  `src/tiktok_ingest/collection_run.py:283-300`; no-fabricated-heartbeat
  note `collection_run.py:279-283`.
- **FR-22 (D-09)** Narration MUST go to stderr; the final stdout structured
  JSON MUST remain parseable with its current shape extended only
  additively.
- **FR-23 (D-10)** The retry flow MUST NOT perform unattended workload
  cleanup: no `swapoff`, no `drop_caches`, no killing arbitrary containers,
  no model switching, no gate relaxation. Only the specific approved Whisper
  lifecycle mutations (stop/restore of the verified known service) are
  performed. *Evidence:* known-service bound
  `src/tiktok_ingest/whisper_lifecycle.py:36-56`, `whisper_lifecycle.py:145-163`.
- **FR-24 (D-02, scope integrity)** The retry authority MUST be bound to the
  exact scope fingerprints of the original attempt (model, media, sampling).
  Any fingerprint change means a NEW authorization question — it is not an
  approved retry. *Evidence:* fingerprint-based stage reuse contracts
  `src/tiktok_ingest/contracts.py:252-266`.
- **FR-25 (D-10)** Interrupted cleanup MUST preserve UNKNOWN semantics
  (state reported as uncertain, never assumed), and the current grouped
  verification MUST NOT proceed after a global restoration hard stop.
  *Evidence:* P0-FR-06/P0-FR-11 interrupt semantics,
  [PHASE-0-ONE-COMMAND-PRD.md](PHASE-0-ONE-COMMAND-PRD.md) sections 8–10.

**Requirement count:** 25 (FR-01..FR-25).

## 7. Security and threat modeling

| Threat | Vector | Mitigation (requirement) |
|---|---|---|
| Consent laundering | retry executed "because the run-plan already authorized the run" | FR-02, FR-03, FR-04: new named consent; run plan explicitly does not cover it |
| Authority creep over services | stopping/starting arbitrary or drifted services around the retry | FR-05, FR-06, FR-10, FR-23: verified known-service identity, identical config re-check, no arbitrary workload actions |
| Gate bypass | retry used to soften or re-threshold gates | FR-13: gates unchanged; N3; rollback section forbids weakening |
| Measurement spoof/confusion | occupied-swap heuristics, cherry-picked windows, mid-attempt baseline resets | FR-08, FR-09, FR-12: cumulative counters, fail-closed parsing, fixed baseline semantics |
| Deadline smuggling | hidden waits extending the window indefinitely (unbounded human wait) | FR-07, FR-11, FR-18: 180 s hard cap, bounded probes, expiry branch |
| Evidence tampering | overwriting the original failure to "make it disappear" | FR-15: append-only retry evidence; original preserved |
| Scope drift | retrying with different model/media/sampling under the old grant | FR-24: fingerprint binding; drift → new authorization |
| Output injection | VLM or provider text treated as instructions or narrated as evidence text | unchanged stage contracts (`vision.py` header: output is evidence, never instructions); narration never echoes evidence text |
| Secret leakage | credentials in narration or JSON | unchanged scrubbing contracts; retry adds no new egress (vision is local) |

## 8. UX contract and mock transcripts

> The transcripts below are **illustrative mocks written for this PRD**. They
> are NOT real run logs and must not be presented as observed behavior.

Mock A — cooldown with recovery (stderr):

```
vision swap-retry pause: id 7412… failed swap_delta gate (+18.56 MiB over
  512 MiB budget, cleanup verified). Whisper restored healthy; isolated
  server stopped. Waiting for swap to quiet: sampling every 5s, admission
  no earlier than 60s, hard stop at 180s. Nothing else runs meanwhile.
[15s] swap 2.31 MiB/s — above 1.00 MiB/s (stable 0/3), 165s left
[30s] swap 0.84 MiB/s — stable 1/3, 150s left
[45s] swap 0.62 MiB/s — stable 2/3, 135s left
[60s] swap 0.51 MiB/s — stable 3/3 at 60s floor: admitting retry
whisper stop (retry authority): verified identical config
stage vision start: 7412… (retry attempt 2, fresh baseline)
stage vision finish: 7412… complete (reused fetch, prepare; 512.4s)
```

Mock B — no recovery (stderr):

```
[15s] swap 4.02 MiB/s — above 1.00 MiB/s (stable 0/3), 165s left
[30s] swap 3.87 MiB/s — NOT recovered (stable 0/3), 150s left
…
[180s] cap reached without 3 stable intervals: retry not executed,
  video pending (resumable by re-invoking). Whisper remains healthy.
```

Mock C — second failure (stderr):

```
stage vision start: 7412… (retry attempt 2, fresh baseline)
stage vision finish: 7412… blocked (swap_delta, +9.3 MiB over budget)
retry exhausted: no third attempt this invocation; video stays pending.
```

Final stdout JSON keeps its current shape; retry outcomes extend it
additively (e.g. a per-video retry object with state, samples and attempt
durations — exact field names are implementation detail).

## 9. Resource and time budget table

| Budget | Value | Status |
|---|---|---|
| Cooldown sampling interval | 5 s | proposed heuristic (D-04) — validate |
| Cooldown admission floor | 60 s from cooldown start | proposed heuristic (D-04) — validate |
| Cooldown hard cap | 180 s total from cooldown start, never restarted | confirmed bound |
| Stability criterion | 3 consecutive intervals ≤ 1 MiB/s | proposed heuristic (D-04) — validate |
| Cooldown probe duration | individually bounded, accurate timeout semantics | required (FR-12) |
| Vision stage deadline (per attempt) | 600 s (unchanged) | existing (`config.py:185`) |
| Swap gate per attempt | 512 MiB systemwide I/O delta (unchanged) | existing (`config.py:213`) |
| Free VRAM gate | 20,480 MiB (unchanged) | existing (`config.py:211`) |
| Whisper stop grace | 30 s bounded escalation | existing (`whisper_lifecycle.py:56`) |
| Whisper restore-ready wait | 60 s bounded polling | existing (`guided.py:89`) |
| Extra attempts | ≤ 1 per video per invocation | confirmed (D-02) |
| Worst-case added wall time per retried video | ≈ cooldown ≤ 180 s + transitions + ≤ 600 s retry stage | derived bound, not a promise |

## 10. Verification and acceptance

Adapted V1–V3 levels for a local pipeline change (no install surface):

- **V1 — automated tests** (mocked clock, fake runners, fake `/proc/vmstat`
  sequences, fake consent): eligibility matrix (only verified-cleanup
  `swap_delta` vision failures), consent denial/EOF, cumulative-rate
  computation, counter-reset and unreadable-counter fail-closed branches,
  60/180 bounds with overlapping windows, drift reset, fresh-baseline
  accounting across the service transition, second-failure exhaustion (no
  third attempt, no re-prompt), stage reuse (fetch/prepare reused; no
  repeated audio/synthesis/verify), additive ledger fields and JSON
  parseability, interrupt UNKNOWN semantics, multi-batch scope isolation,
  and no-repetition of completed work.
- **V2 — mocked-clock acceptance harness**: scripted vmstat sequences and a
  fake service runner driving the full FSM of section 5.2 end-to-end
  (T1–T16), asserting terminal-state messages and durable records verbatim.
- **V3 — one separately authorized real GPU trial** on a small, explicitly
  authorized collection. No accuracy or recovery percentages are claimed;
  the trial observes whether the flow behaves as specified, not whether
  waiting cures swap pressure.

### RF-to-verification matrix

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | ✔ | ✔ | ✔ | eligibility matrix (only swap_delta + verified cleanup) |
| FR-02 | ✔ | ✔ | ✔ | one-attempt cap; no re-prompt after second failure |
| FR-03 | ✔ | ✔ | ✔ | consent content + denial/EOF fail-closed |
| FR-04 | ✔ | ✔ | — | new-scope prompt on drift |
| FR-05 | ✔ | ✔ | ✔ | window closed + healthy restore before wait |
| FR-06 | ✔ | ✔ | ✔ | identity/config mismatch blocks |
| FR-07 | ✔ | ✔ | ✔ | 5s/60s/180s/3×1MiB/s bounds |
| FR-08 | ✔ | ✔ | — | cumulative rate; unreadable counters fail closed |
| FR-09 | ✔ | ✔ | — | reset semantics; floor/cap overlap |
| FR-10 | ✔ | ✔ | ✔ | re-admission order and re-checks |
| FR-11 | ✔ | ✔ | — | drift returns to sampling, cap not restarted |
| FR-12 | ✔ | ✔ | — | fresh baseline; explicit transition accounting |
| FR-13 | ✔ | ✔ | ✔ | gates/deadline unchanged in retry |
| FR-14 | ✔ | ✔ | ✔ | stage reuse; no repeated completed stages |
| FR-15 | ✔ | ✔ | — | original evidence preserved |
| FR-16 | ✔ | ✔ | — | typed gate eligibility; additive fields |
| FR-17 | ✔ | ✔ | — | per-invocation cap (not lifetime attempts) |
| FR-18 | ✔ | ✔ | ✔ | expiry branch; pending video; no silent skip |
| FR-19 | ✔ | ✔ | ✔ | exhaustion messaging; resumable state |
| FR-20 | ✔ | — | — | CLI surface documented; no invented flags |
| FR-21 | ✔ | ✔ | ✔ | 15 s truthful narration; final messages |
| FR-22 | ✔ | ✔ | — | stderr/stdout split; JSON parseable |
| FR-23 | ✔ | ✔ | ✔ | no unattended cleanup actions |
| FR-24 | ✔ | ✔ | — | fingerprint change → new authorization |
| FR-25 | ✔ | ✔ | — | interrupt UNKNOWN; hard-stop interplay |

## 11. Rollout, backwards compatibility, rollback

- **Rollout:** additive only — new consent window, new retry sub-flow, new
  additive manifest fields. Existing manifests, fingerprints and stage
  contracts are untouched; the frozen-scope run-plan behavior is unchanged
  for every non-retry path.
- **Backwards compatibility:** old state directories remain fully readable;
  absence of retry fields means "no retry attempted". The final stdout JSON
  keeps its current parseability (FR-22).
- **Rollback:** removing the retry path restores the exact current behavior
  (gate fired → window ends, video pending). Rollback MUST NOT weaken any
  gate, threshold or consent rule; no rollback step may perform unattended
  cleanup (FR-23).

## 12. Success metrics (observable, no invented percentages)

- Every offered retry in V1/V2 traces to a verified-cleanup `swap_delta`
  vision failure (zero offers for other failure kinds).
- Every terminal branch (T3, T6, T10, T11, T14, T15, T16) is reachable in
  the V2 harness with the exact specified message.
- Consent denial never starts the cooldown; cooldown never mutates anything;
  the 180 s cap is never exceeded in the harness.
- The second attempt never repeats fetch/prepare/audio/synthesis/verify, and
  never creates a third attempt or a re-prompt.
- V3 (single authorized trial): flow behaves as specified end-to-end; the
  trial's recovery outcome — either way — is recorded as evidence about
  these heuristics, NOT as proof that retry cures swap pressure. The gate
  remains final for the original window in all cases.

## 13. Assumptions (explicit, unproven)

- **A-01:** Marginal overshoots (≈1.77–18.56 MiB over a 512 MiB budget) are
  plausibly ambient/transient. Unproven: systemwide counters cannot attribute
  cause, and model-driven paging would not quiet down.
- **A-02:** Frame-level reuse inside the retried vision stage is NOT proven;
  whether any partial frame work can be reused is an implementation
  discovery. The retry is specified as a fresh vision attempt until proven
  otherwise.
- **A-03:** The D-04 heuristics (5 s / 60 s / 180 s / 3×1 MiB/s) are
  reasonable starting points. They require validation via the trial; they are
  not derived from efficacy evidence.
- **A-04:** The 781-test baseline recorded for the working tree in the
  preceding implementation session was not re-run for this documentation-only
  task; it is session-recorded evidence, not a fresh measurement. The project
  uses the standard-library `unittest` runner, not pytest.

## 14. Open questions (genuine, with conservative defaults)

- **OQ-01:** After a SUCCESSFUL retry of one video, may the remaining
  same-window IDs be offered a path (skip-to-rest), or do they stay pending
  for a new invocation? Conservative default (not approved otherwise):
  they stay pending — no silent skip, no reordering (FR-18).
- **OQ-02:** If multiple videos fail `swap_delta` in one invocation, is the
  retry budget per-video-consent or pooled across failed IDs? Conservative
  default: one explicit consent per video, no pooling, no cross-video
  automatic application (D-02).
- **OQ-03:** How do subsequent frozen batches in the same invocation
  reconcile with a retry exhaustion, given no extra model loads are
  authorized after the final window? OPEN implementation detail (FR-20);
  the current driver behavior must be reconciled explicitly during design,
  without expanding any global hard stop beyond what was approved.
- **OQ-04:** Exact additive JSON field names and the retry-object shape in
  the final stdout document. Implementation detail, to be fixed in design
  under FR-16/FR-22 constraints.
- **OQ-05:** Scheduling and scope of the single V3 authorized real trial
  (which collection, when) — operator decision, separate authorization.

## 15. Modules affected (for later implementation planning only)

Real current modules and the areas a future change would touch; no new
symbols are invented here:

- `src/tiktok_ingest/gates.py` — additive persisted detail for gate results
  (currently omitted in `to_jsonable`, `gates.py:401-405`); typed ids stay.
- `src/tiktok_ingest/vision.py` — retry entry point around the existing
  bounded stage; baselines and cleanup reuse (`vision.py:730-755`).
- `src/tiktok_ingest/collection_run.py` — retry orchestration, consent
  window construction, narration (patterns at `collection_run.py:196-303`,
  `374-425`, `452-527`).
- `src/tiktok_ingest/guided.py` — coordination hooks and consent mechanics
  (`guided.py:244-291`, `314-347`).
- `src/tiktok_ingest/whisper_lifecycle.py` — reused as-is for stop/restore
  verification; no new service authority.
- `src/tiktok_ingest/config.py` — the D-04/D-06 heuristic constants as
  configuration.
- `src/tiktok_ingest/contracts.py` — additive retry provenance in stage
  records (`contracts.py:252-300`); per-invocation cap semantics.
- `src/tiktok_ingest/cli.py` — surface documentation only; no invented
  flags (`cli.py:624-718`).
- Tests under `src/tiktok_ingest/tests/` per section 10.

## 16. References

- [PHASE-0-ONE-COMMAND-PRD.md](PHASE-0-ONE-COMMAND-PRD.md) — Phase 0 scope,
  consent architecture, P0-FR numbering this addendum builds on.
- [PRD.md](../PRD.md) / [MVP-PRD.md](../MVP-PRD.md) — evidence boundaries,
  resource gates, service rules.
- [OPERATIONS.md](../OPERATIONS.md) — implemented gate/window/whisper
  contracts and their honesty rules.
- [PHASE-1-LOCAL-WEB-PRD.md](PHASE-1-LOCAL-WEB-PRD.md) /
  [PHASE-2-DESTINATIONS-PRD.md](PHASE-2-DESTINATIONS-PRD.md) — later phases,
  unaffected.

**End of DRAFT — no implementation is authorized by this document.**
