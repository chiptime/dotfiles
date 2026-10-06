# tiktok-ingest swap-quiet-retry (Phase 0 addendum) — ODD task artifact

Status: **IMPLEMENTED + ONE BOUNDED FUNCTIONAL CORRECTION APPLIED
(2026-09-30, from independent requirements verification — NOT RDD, which
stays OFF) — delivery decision still PENDING (no commits/PR requested or
made).** The user's original explicit authorization: "lee completo el
fichero, entiendolo e implementalo
ai/tiktok-ingest/prds/PHASE-0-SWAP-QUIET-RETRY-PRD.md" — the USER REQUEST
is the implementation authority (the PRD is the requirements document, not
the authorizer). Delegated direct local implementation, NOT SDD; RDD OFF
(clone-local). All 12 tasks implemented with observed offline proof; full
suite after the correction: **828 tests OK** (baseline 781 + 47 feature
tests; zero regressions). V3 (real-GPU trial) EXPLICITLY DEFERRED —
separate operator authorization (OQ-05). Sibling ODD docs and the
pre-existing dirty baseline (guided.py, collection_run.py, cli.py, tests,
docs — the run-consent work) preserved; vision.py untouched.

## Validator correction (bounded, 2026-09-30)

Independent requirements verification found ONE confirmed T16 deviation
plus four concrete readmission-accounting gaps; all fixed in ONE bounded
pass with regressions, nothing else touched:

| Finding | Correction | Regression (observed) |
|---|---|---|
| T16: `human.ask` and the pre-consent verification sat OUTSIDE the per-video interrupt scope — a KeyboardInterrupt there propagated with state `retry_eligible` and no durable record | ONE per-video interrupt scope now wraps verification + consent + cooldown; pre-mutation phases record `interrupted_unknown` with a "before any mutation" message | `test_t16_interrupt_during_preconsent_verification`, `test_t16_interrupt_during_consent_ask` (no consent asked, no sampling, no stop), `test_journey_consent_interrupt_skips_grouped_verification` (run-level: report interrupted, grouped verification skipped, only 1 original vision call) |
| Interrupted readmission stop must never trigger an unsafe restore (stop result UNKNOWN) | Preserved and now test-locked | `test_t16_interrupt_during_readmission_stop_never_restores` (no `whisper-restore-op` call) |
| Re-admission ignored remaining cap: slow transitions (stop grace / health waits / probes) could consume time past the ORIGINAL 180 s deadline and still launch | After readmission "ok", the single-t0 cap gates the LAUNCH: past the original deadline the attempt never starts; whisper (stopped by readmission) is restored under the retry authority first, then an honest `cooldown_expired` variant ("cap exhausted during re-admission transitions") is recorded | `test_slow_transitions_past_cap_do_not_launch_retry` (fake stop consumes +130 s → elapsed 190 s ≥ 180; ZERO vision calls; whisper restored; post_stop sample elapsed ≥ 190 proves t0 never reset) |
| Post-stop conditions checked VRAM only; the service mutation's own swap effect was unmeasured; `sample` accounting had no transition phase | `SwapQuietWatcher.sample_transition("post_stop")`: a fresh raw cumulative reading right after the stop, rate participating when measurable time elapsed (above-threshold = post-mutation drift → restore + reset + resample inside the SAME cap; dt ≤ 0 under a frozen fake clock is recorded with rate None and judged inconclusively — no anchor update, so the next delta spans the whole transition) | `test_post_stop_swap_drift_resets_and_resamples` (20 MiB post-stop delta → 4 MiB/s → drift recorded with reason, stop→restore→stop order, resample elapsed 65–80 s inside the original cap, retry completes) |
| Readmission treated config-mismatch / not-running as plain drift: not-running resampled with the shared service DOWN; a foreign config was resampled on the assumption of a healthy known service; a RAISING gpu probe could escape with whisper left stopped | not-running → restored + health-verified under the granted authority FIRST, then drift-resample (never held down); config mismatch → global fail-closed with the actual state (T6/FR-06 restoration semantics — the consent never authorized the foreign configuration); inspect-None → fail-closed; gpu probe raising → restore (state known) then honest fail-closed | `test_readmission_not_running_restores_then_resamples`, `test_readmission_config_mismatch_is_global_failclosed`, `test_readmission_inspect_none_is_global_failclosed`, `test_gpu_probe_raising_restores_whisper_then_blocks` |

Rationale where wording differed from the PRD's T13 ("config drift →
cooldown_sampling"): the PRD ALSO binds config mismatch to restoration
fail-closed (T6/FR-06) and FR-11's drift examples are volatile conditions
("VRAM no longer free, service state changed" — running/stated). An
identity-level mismatch means a DIFFERENT configuration answers under the
known name: neither stopping it nor "restoring" over it is within the
retry consent, and resampling would assume a healthy known service that
is no longer provably the bound one — so the fail-closed reading was
implemented (also the verifier's explicit directive). Volatile state
(not-running) stays drift per FR-11, but ONLY after restoring the service
under the granted authority, reconciling both rules. Probe budgets: each
transition keeps its own POSITIVE bound (stop grace 30 s; restore-health
deadline 60 s; sampling sleeps min(interval, remaining) > 0 while budget
remains) — no hidden repeated waits; the cap is enforced against the
single original t0 at every gate.

## Objective / problem / why

**Objective.** Convert marginal, plausibly-transient vision `swap_delta`
gate failures into ONE operator-authorized, measured, bounded second chance
per video per invocation (PRD O1–O4), keeping every existing safety
contract intact.

**Problem.** During an authorized real `collection-run` (nine videos,
batches ≤ 5), four completed and five ended partial after the vision
`swap_delta` gate fired on small overshoots (+1.77 to +18.56 MiB over the
512 MiB systemwide budget). The counters are systemwide (cause
unattributable); the contract was deliberately final — a fired gate
closes the GPU window for the remaining IDs of the batch.

**Why.** Operator confirmed a bounded product change: one extra attempt per
video per invocation, only behind a NEW explicit consent, only after swap
activity is MEASURED to stabilize within a bounded read-only cooldown —
restoring the shared Whisper service healthy before any human wait, and
telling the user plainly when the host does not recover. The PRD promises
no cure (N5, A-01); nothing implemented claims efficacy.

## Scope and authorized boundaries

**In scope (implemented).** The bounded retry sub-flow inside the existing
`collection-run` architecture: eligibility from durable typed evidence;
SAME-Whisper restore verification before the prompt; new consent window;
read-only cooldown; named re-admission; vision-only retry; additive
evidence/report fields; offline V1/V2 verification; docs sync.

**Out of scope (restated, honored).** No automatic retries/loops (N1); no
retry for non-`swap_delta` failures (N2); no gate weakening (N3); no
unattended host cleanup (N4); no cure claims (N5); no review/backlog
changes (N6); no SDD artifacts (N7). No new CLI flags (FR-20); the cli.py
change is help-text only. No commits/staging/push/PR this turn.

**Boundaries honored this turn:** local files only — no browser, network,
live GPU, services or production state; every harness uses fakes/temp
fixtures; pre-existing dirty files preserved (git working tree contains
exactly the pre-existing run-consent changes plus this feature's edits).

## Binding decision authority

PRD ledger D-01..D-10 (§2) — not re-opened. **D-08 conservative behavior,
implemented and test-proven:**

- Remaining same-GPU-window IDs stay pending EVEN AFTER RETRY SUCCESS —
  no silent skip, reorder, or automatic retry (OQ-01 default; proven in
  `test_journey_retry_success_keeps_other_window_ids_pending`).
- Subsequent frozen batches reconcile EXACTLY as the pre-existing driver
  does — the retry adds no batch, no window and no model load beyond the
  ONE separately-consented attempt (proven in
  `test_journey_subsequent_frozen_batches_reconcile_unchanged`).
- No invented flags: `collection-run` still has no `--ids`.
- Retry exhaustion is video/window-scoped and reported distinctly from
  the global service-restoration hard stop (`_SwapRetryBlocked` only for
  genuine restoration/lifecycle failures; gate exhaustion never sets
  `hard_stop_reason`).

## Stable task IDs — statuses with observed evidence

### A. Cooldown + gate evidence

- [x] **TI-SQR-01** Typed-gate eligibility + additive ledger evidence.
  `_swap_retry_eligibility` (collection_run.py) decides ONLY from the
  persisted typed gate id `swap_delta` + verified cleanup in
  `meta.json` `resource_gates` (never reason prose); `GateLedger
  .to_jsonable` now persists the raw `detail` (counters/baselines)
  additively; `JobManifest.swap_retry` (new optional dict field,
  old manifests stay readable) holds original/consent/attempt/prior
  provenance — append-only, no second job ledger; the cap is the
  invocation-scoped `offered` set, NEVER a lifetime `attempts >= 2`
  rejection. FR-01/15/16/17 — tests: eligibility matrix (6), ledger
  detail, manifest round-trip, old-manifest readability.
- [x] **TI-SQR-02** Read-only bounded cooldown sampler. New module
  `swap_cooldown.py` (`SwapQuietWatcher`): cumulative `pswpin+pswpout`
  rate per 5 s interval; 60 s admission floor; single 180 s cap anchored
  at t0 and NEVER restarted (`reset_stability` only zeroes the count);
  3 consecutive intervals ≤ 1 MiB/s; decreasing/missing/unreadable
  counters fail closed (`SwapCooldownMeasurementError`, distinct from
  expiry); samples carry phase (`cooldown`/`resample`) for explicit
  transition accounting; constants in `config.py`
  (`SWAP_RETRY_COOLDOWN_*`). FR-07/08/09/12 — tests: 6 watcher units.

### B. Guided lifecycle + consent + retry

- [x] **TI-SQR-03** Restore verification BEFORE the prompt. The batch
  cycle's standing restore already closes the window (verified unload +
  isolated stop + restore); `_verify_whisper_restored_healthy`
  positively re-verifies identity/config (`same_service_config` vs the
  plan binding) and health read-only BEFORE any consent; mismatch →
  `blocked_global` (service-restoration hard-stop category). FR-05/06 —
  tests: T6 config mismatch, T6 unhealthy.
- [x] **TI-SQR-04** New consent + cap + scope binding. Dedicated
  `vision-swap-retry` window naming id, measured overshoot MiB, full
  budget, whisper stop/restore under THIS named authority, second-failure
  effects, exact fingerprints (model/media/sampling); asked via the RAW
  human provider — `RunScopedAuthorization` never covers the kind
  (falls through to "never auto-covered"); EOF/denial →
  `retry_declined_pending`; ONE ask per video per invocation (denial
  included), no re-prompt after a failed second attempt; scope drift
  after consent → `retry_not_executed_pending` with a NEW-authorization
  message (FR-24). FR-02/03/04/17/24 — tests: non-coverage (grant+deny),
  window content, one-ask, scope drift.
- [x] **TI-SQR-05** Re-admission under the named authority.
  `_swap_retry_readmission`: scope fingerprints re-checked read-only
  first; whisper stopped again ONLY under the retry consent;
  `same_service_config` re-checked at that moment; fresh free-VRAM probe
  AFTER the transition (new optional `StageFunctions.gpu_free_mib` seam;
  the stage's own gate stays authoritative); drift → whisper restored,
  stability reset, resampling inside the SAME cap (never a fresh 180 s);
  stop/restore lifecycle failures → honest hard stop; gates unchanged
  (the retry is a normal `run_vision_stage` call with its own fresh
  immediate baseline at start). FR-10/11/12/13 — tests: T13 drift (VRAM
  low → restore → resample → complete; stop/restore/stop call order;
  resample elapsed 65–75 s inside the original cap).

### C. Collection / report / docs

- [x] **TI-SQR-06** Vision-only retry + append-only evidence.
  `_execute_swap_retry` re-runs ONLY vision via the narrated seam;
  fetch/prepare reused (nothing else called); completed
  audio/synthesis/verify never repeated; `_archive_original_vision_attempt`
  copies the whole `vision/` dir to `vision-attempt-N/` and records the
  original stage record + gate evidence in `manifest.swap_retry.original`
  BEFORE the retry can overwrite live paths; the attempt's own record
  (timestamps, duration, outcome, cooldown samples,
  `restore_verified_healthy`) persists on EVERY path where the attempt
  started — success, second failure, interrupt, later restore failure;
  later-invocation retries chain `prior` instead of discarding evidence.
  FR-14/15 — tests: T14 archive + attempt + live-record flip; T15; KI.
- [x] **TI-SQR-07** Terminal branches + D-08 window rule. Distinct states:
  `retry_eligible`, `retry_declined_pending`, `blocked_global`,
  `cooldown_expired`, `measurement_unavailable`, `retry_complete`,
  `retry_exhausted`, `interrupted_unknown`, `retry_not_executed_pending`
  (preexisting isolated server — never adopted; scope drift). Expiry →
  retry NOT executed, video pending/resumable (message verbatim); a
  preexisting isolated server blocks the retry honestly WITHOUT a global
  hard stop; batch driver reconciliation unchanged; no CLI flags
  invented (cli.py help-text only). FR-18/19/20 — tests: T10, T15,
  preexisting-server, journey ×3.
- [x] **TI-SQR-08** Truthful narration + additive report. stderr
  narration: eligibility line, pause line (id, overshoot, budget,
  restored-healthy state), 15 s lines with measured rate + stable count
  + remaining time ("above …"→"NOT recovered"), admission line, retry
  begin/finish (narrated stage wrapper measures real durations), whisper
  stop/restore lines, verbatim terminal messages; final stdout JSON
  gains additive `swap_retries` (state, overshoot, scope, consent,
  samples, resets, attempt) and stays parseable. FR-21/22 — tests: T14
  narration assertions, T10 verbatim, `json.loads(json.dumps(report))`.
- [x] **TI-SQR-09** Safety non-goals enforced. No `swapoff`,
  `drop_caches`, arbitrary container kills, model switching or gate
  relaxation anywhere in the flow; only the approved Whisper lifecycle
  mutations; interrupts keep UNKNOWN semantics (`interrupted_unknown`
  records; stop-interrupt never restores — UNKNOWN; restore-interrupt
  records `restore_unknown_interrupt`); grouped verification never runs
  after a hard stop (existing guard; journey-proven). FR-23/25 — tests:
  T16 ×2, hard-stop journey.
- [x] **TI-SQR-10** Docs sync. README: one bullet after Whisper
  coordination; OPERATIONS: "Bounded vision swap-retry (collection-run
  only)" subsection (what you see in order, terminal states, explicit
  not-claimed); cli.py collection-run description extended (no flags).
  Artifacts English, minimal, consistent with existing style
  (cognitive-doc-design applied).

### D. Offline V1/V2 verification

- [x] **TI-SQR-11** V1 matrix — `tests/test_swap_retry.py`: watcher
  bounds (admission/floor, reset-never-cap, expiry, unreadable,
  decreasing, single-t0); eligibility matrix (swap_delta+cleanup;
  offload/unverified/blocked/deadline/no-detail); ledger detail;
  manifest round-trip; RunScopedAuthorization non-coverage; window
  content; one-ask-per-video; scope drift; stop-failure hard stop.
- [x] **TI-SQR-12** V2 mocked-clock FSM harness — same file, class
  `TestV2FsmHarness` + `TestRunCollectionJourney`: scripted vmstat
  sequences + fake clock/services driving T1→T2/T3/T6(×3)/T9/T10/T11/
  T13/T14/T15/T16(×4 — cooldown, attempt, pre-consent, consent ask) plus
  the correction-wave regressions (readmission stop KI, config-mismatch/
  not-running/inspect-None fail-closed, raising VRAM probe, slow-
  transition launch gate, post-stop swap drift) with verbatim terminal
  messages and durable-record
  assertions (archive, manifest.swap_retry, samples/phases/resets), plus
  three full `run_collection` journeys (retry success keeps same-window
  ids pending; frozen-batch reconciliation unchanged; hard stop skips
  grouped verification). **V3 real-GPU trial EXPLICITLY DEFERRED —
  separate authorization (OQ-05); no efficacy claimed.**

## FR acceptance mapping (25 FRs → tasks; observed proof)

| FR | Tasks | Offline check (observed) |
|---|---|---|
| FR-01 | 01, 11 | V1 eligibility matrix ✔ |
| FR-02 | 04, 11 | one-ask test ✔ |
| FR-03 | 04, 11 | window content + denial ✔ |
| FR-04 | 04, 11 | non-coverage + new-scope ✔ |
| FR-05 | 03, 11 | pre-consent verify + T6 tests ✔ |
| FR-06 | 03, 11 | config-mismatch hard stop ✔ |
| FR-07 | 02, 11 | watcher bounds ✔ |
| FR-08 | 02, 11 | cumulative rate; fail-closed ✔ |
| FR-09 | 02, 11 | reset semantics; overlap ✔ |
| FR-10 | 05, 11 | re-admission order + re-checks ✔ |
| FR-11 | 05, 11 | T13 drift, same cap ✔ |
| FR-12 | 02, 05 | phase-labelled samples; single t0 ✔ |
| FR-13 | 05 | normal stage call, gates untouched (vision.py unmodified) ✔ |
| FR-14 | 06, 11 | T14: only vision called ✔ |
| FR-15 | 01, 06 | archive + original preserved ✔ |
| FR-16 | 01, 11 | typed id; additive detail ✔ |
| FR-17 | 01, 04 | invocation-scoped offered set ✔ |
| FR-18 | 07, 11, 12 | T10 expiry; journey pending ids ✔ |
| FR-19 | 07, 11, 12 | T15 exhaustion, distinct from hard stop ✔ |
| FR-20 | 07, 10 | no flags; reconciliation journey ✔ |
| FR-21 | 08, 10, 12 | 15 s truthful narration ✔ |
| FR-22 | 08, 11 | stderr/stdout split; JSON parseable ✔ |
| FR-23 | 09, 11 | no unattended cleanup (code audit) ✔ |
| FR-24 | 04, 11 | fingerprint drift → new authorization ✔ |
| FR-25 | 09, 11 | interrupt UNKNOWN; hard-stop interplay ✔ |

**All 25 FRs covered by ≥1 implemented task with observed offline proof.
Gaps (honest): V3 deferred (FR rows marked "V3 deferred" in the PRD
matrix are covered by V1+V2 only); FR-23's "no unattended cleanup" is
proven by code inspection (no such action exists in the diff), not by a
dedicated negative test. FR-25/T16 now includes pre-consent and
consent-phase interrupt regressions (correction wave); FR-11/FR-12 now
include the post-stop swap drift, launch-gated cap and transition-phase
accounting regressions.**

## Route

- Delegated direct local implementation (general fallback sub-agent under
  explicit parent instruction; no child delegation, no SDD, RDD OFF).
- Preparation evidence: PRD read in full (587 lines/16 sections); skills
  dotfiles-context + work-unit-commits (+ cognitive-doc-design before
  docs) read and applied; CodeGraph + targeted reads of gates/vision/
  collection_run/guided/whisper_lifecycle/contracts/config; PRD §15
  modules verified on disk; baseline suite run first (781 OK).
- Multi-file trigger met: 10 files touched (8 PRD §15 modules — vision.py
  intentionally untouched — plus new swap_cooldown.py and the new test
  file).
- Write trigger: the user's explicit implement instruction.

## Checks (observed commands and results; repo root cwd)

```bash
# baseline (pre-implementation)
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 \
  -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests \
  -t ai/tiktok-ingest/src
# -> Ran 781 tests / OK

# focused (new feature file, after each implementation wave)
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 \
  -m unittest tiktok_ingest.tests.test_swap_retry
# -> first wave: Ran 37 tests / OK (RED observed first: 10 failing)
# -> correction wave: Ran 47 tests / OK (10 new regressions; RED
#    observed first for the re-indent fixes, then GREEN)

# final full suite (after the validator correction)
# -> Ran 828 tests in ~12s / OK   (781 + 47, zero regressions)
```

- All offline: mocked clock, scripted `/proc/vmstat` fakes, fake
  whisper/Ollama/stage doubles, temp state dirs; no browser/network/GPU/
  services/production mutations.
- **V3 explicitly deferred** — single real-GPU trial requires separate
  operator authorization (OQ-05); no accuracy/recovery percentages
  claimed.

## Review workload forecast vs actual (authored adds)

Planning forecast: **~1000 authored lines (advisory, not a gate).**
Actual measured additions: **~2837 authored lines** — collection_run.py
+1158 (994 + 164 correction), test_swap_retry.py +1466 (new; 47 tests),
swap_cooldown.py +309 (new; +34 correction), OPERATIONS +69, README
+29, config +20, gates +14, contracts +15, guided +7, cli +5 (line-count
deltas vs the pre-session files; the pre-existing uncommitted
run-consent baseline is NOT included). No code or tests were cut to
approach any budget; the overage is recorded honestly (same pattern as
the sibling run-consent task: ~730 forecast → ~2100 actual). `vision.py`
untouched (1135 lines before and after).

## Delivery strategy

`ask-on-risk` — decision PENDING before any future commit. No commits,
staging, push or PR made (none requested; skill auto-commit defaults
explicitly overridden by the user instruction). Honest slice candidates
when delivery is authorized: (1) TI-SQR-01+02 evidence+sampler +
watcher tests; (2) TI-SQR-03+04+05 lifecycle+consent+re-admission;
(3) TI-SQR-06+07+08+09 collection flow; (4) TI-SQR-10 docs+cli;
(5) TI-SQR-11+12 V2 harness + journeys.

## TDD resolution (unchanged by implementation)

Unknown/unconfigured for tiktok-ingest (openspec `strict_tdd: false` is
SDD-scoped and does not list tiktok-ingest; no pytest/pyproject/tox
config; the sibling doc's "TDD: explicit" was that task's own contract).
Practice applied: ordinary tests-first regression checks — focused RED
observed before GREEN in BOTH waves (10 failing in the implementation
wave; the correction wave's control-flow re-indent bugs also observed RED
before GREEN) — WITHOUT claiming strict TDD. Runner: standard unittest
(command above).

## Open product decisions surfaced honestly (not invented)

- **OQ-01** (PRD): same-window ids after a successful retry — implemented
  conservative default (stay pending), proven by test.
- **OQ-03** (PRD): frozen-batch reconciliation — implemented as the
  unchanged pre-existing driver, proven by test; no new hard stop, no
  extra model loads.
- **OQ-06 (new, discovered)**: whether a SUCCESSFULLY retried video's
  audio/synthesis/verify may run later in the SAME invocation. The PRD
  specifies the retry as vision-only (D-07/FR-14) and ends the FSM at
  `retry_complete`; conservative implementation: those stages stay
  pending for a new invocation (the retry window closes after the one
  vision run). Genuinely OPEN — needs an operator decision before any
  "continue the pipeline after retry" change.
- **OQ-04** (PRD): exact additive JSON field names — fixed by this
  implementation (`swap_retries` report key; `manifest.swap_retry`
  {original, consent, attempt, prior}); renames would need a new decision.

## Progress, next step, rollback

- **Progress:** all 12 tasks implemented with observed offline proof;
  ONE bounded validator correction applied (see its section above); final
  suite 828 OK; docs synced; Engram mirror #9656 updated with the FULL
  current document and read back.
- **Next step:** operator decides OQ-06 and the V3 trial (OQ-05);
  delivery decision (commits/PR) awaits explicit authorization.
- **Rollback boundary:** remove exactly this feature's additions — new
  files `swap_cooldown.py`, `tests/test_swap_retry.py`; the
  collection_run.py retry block (+ call site, + signature params, +
  `swap_retries` report key); the additive `detail` key in
  `GateLedger.to_jsonable`; `JobManifest.swap_retry`; the
  `StageFunctions.gpu_free_mib` seam (+ its default wiring); the
  `SWAP_RETRY_*` config constants; the README/OPERATIONS/cli.py doc
  additions. Rollback MUST NOT touch the pre-existing run-consent dirty
  baseline, sibling ODD docs, or the PRD, and never weakens any gate,
  threshold or consent rule.
