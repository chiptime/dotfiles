# tiktok-ingest run-consent — ODD task artifact

Status: **IMPLEMENTED + VALIDATOR CORRECTION APPLIED (2026-09-30) —
delivery decision still PENDING before any future commit; no commits or
PRs made.** An independent functional validator found one deterministic
flaw (grouped verification after a whisper-restore hard stop) and one
coverage-fidelity gap (substring model matching); both corrected in ONE
bounded pass with regressions, plus two coverage additions (real
ConsoleConsentProvider journey, synthesis narration). Independent
re-verification by the parent is NOT yet done — evidence below is the
writer's own observed runs. Full suite after correction: **781 tests
OK** (was 778 at first implementation, 762 at baseline).

## Objective

Replace `collection-run`'s routine per-stage/per-video/per-batch consent
prompts with the user-approved TWO-decision contract plus ONE grouped
verification authorization, while keeping every existing safety invariant
(gates, identity checks, whisper stop/restore standing-authority semantics,
fail-closed denial/EOF/non-TTY, no global `--yes`, no remembered approval).

## Scope

**In scope**

- `collection-run` authorization flow only (browser grant, readiness
  checkpoint, run-plan grant, frozen batch driver, grouped verify).
- Progress narration on stderr; stdout stays one parseable JSON report.
- Tests reworked to the new contract (existing scenario intent preserved).
- Doc sync (README, OPERATIONS, PRD per-window wording, CLI help) ONLY
  enough to reflect the approved contract.

**Out of scope**

- Standalone `guided-run` consent behavior (must remain unchanged; only an
  additive `await_readiness` seam on the consent provider base).
- Any stage/pipeline/collection logic changes.
- Any commit/push (not authorized this turn).

## Confirmed user contract (authority for this task)

| Decision | Kind | Semantics |
|---|---|---|
| Grant 1 — browser | `browser` | Open headed browser, exact collection, isolated profile, audit HTML, scan/inventory merge, and the later snapshot after readiness — all named in this one grant. |
| Readiness checkpoint | none (new `await_readiness`) | Press-Enter signal after operator login + visible items. Clearly NOT a yes/no authorization; EOF/non-TTY fails closed. |
| Grant 2 — whole current run | `run-plan` (new) | Exact frozen eligible IDs + canonical URLs + stage reuse states, batches of ≤5, lifecycle operations (ollama, whisper stop/restore semantics), synthesis endpoint+model, retry policy (each id/op once), consequences. ONE accept covers the whole current run. |
| Grouped verification | `verify` (asked once, after synthesis) | All exact validated candidate URLs accumulated over the frozen run, displayed together, one decision. |
| Extra scope only | forwarded to human | Genuinely new actions/destinations not covered by the plan, or explicit retry. Unknown kinds never auto-granted. |

Never: per-stage/video/batch routine prompts, global `--yes`, remembered
later-run approval, blanket future-URL approval, absorbing newly eligible
IDs without a new scope decision.

## Stable task IDs

| ID | Task | Status (observed evidence) |
|---|---|---|
| TI-RC-01 | Readiness checkpoint replaces `browser-confirm`; grant-1 names snapshot/audit writes + readiness semantics (`browser.py`, `guided.py` seam) | ✅ implemented — `await_readiness` seam (base fail-closed + console press-Enter); browser window names checkpoint/writes; `BrowserInventoryReport.confirmed` → `readiness_signaled`; test_browser + test_guided GREEN |
| TI-RC-02 | Run-plan grant over the frozen inventory | ✅ implemented — `RunPlan`, `build_run_plan_window`, `_frozen_inventory`, `_bind_whisper_identity` read-only before the window; TestRunPlan GREEN |
| TI-RC-03 | `RunScopedAuthorization` wrapper + audit + verify deferral | ✅ implemented — subset coverage per kind, `covered_by_run_plan`/`deferred`/`human_*` audit; TestAuthorizationWrapper GREEN |
| TI-RC-04 | Frozen batch driver (fixed slices, once per id, no loop guard) | ✅ implemented — `_batch_progressed` removed; TestFrozenDriver GREEN (each id attempted exactly once across batches) |
| TI-RC-05 | Grouped verification phase | ✅ implemented — `_collect_verify_candidates` + `_run_grouped_verification` (one window, exact URLs, once per id); TestConsent grouped tests GREEN |
| TI-RC-06 | Stage narration wrapper | ✅ implemented — `_narrated_stages` (start/finish, outcome/reused/duration, batch x/y lines); TestProgress GREEN; final report parseable JSON |
| TI-RC-07 | Whisper plan-time binding, drift fail-closed | ✅ implemented — binding via inspect+`verify_known_service` before the plan; scripted-drift test proves human re-ask, zero mutation on denial |
| TI-RC-08 | Full test matrix | ✅ implemented — RED observed first (61 tests: 14F/28E; guided 5E), then GREEN; matrix items covered incl. expired grant, no-reattempt, interrupts, standalone unchanged |
| TI-RC-09 | Docs + CLI sync | ✅ implemented — README journey/consent/whisper/batches sections, OPERATIONS §7/§9 + whisper, PHASE-0 PRD steps 4/6/7 + P0-FR-04 + §9 + comparison row (amended annotations), cli.py description |

Commit evidence: **PENDING** — no commits authorized or made this turn;
checking committed tasks is not applicable.

## Validator correction (bounded, 2026-09-30)

Independent validator evidence `/tmp/opencode/tiktok_contract_check.py`;
ONE correction pass, rest preserved, no live effects:

| Flaw | Correction | Regression (observed) |
|---|---|---|
| Check B: grouped verification ran after a whisper-restore hard stop although "ALL further progress is blocked" | grouped phase now also requires `hard_stop_reason is None` (not just non-interrupted) | `test_restore_failure_after_ready_batch_skips_grouped_verify`: 2 batches, batch 1's five ids synthesized, batch 2 restore fails → zero grouped prompt/verify/backlog; second authorized run resumes with zero re-inference and verifies all 7 |
| Check C: synthesis model coverage used substring match (`glm-5.3` covered `glm-5.3-quantized-evil`) | structured `ConsentWindow.model` attribute (set by `make_auto_synthesizer`), compared with EXACT equality; legacy windows fall back to exact-token parse of the stable `(model <token>)` prose shape; prefix/suffix/case impersonators extract to a different token → extra-scope question | `test_synthesis_model_matches_exactly_never_by_substring`: sneaky/`evil-`/case variants forwarded to the human, exact structured and exact legacy-token windows covered |
| Coverage gap (prompts): journey never exercised with the REAL default provider | new fixture with `ConsoleConsentProvider` + injected `io.StringIO("yes\n\nyes\nyes\n")`, 7 ids / 2 batches / managed whisper | `test_two_batches_managed_whisper_need_exactly_three_yes_and_one_enter`: exactly 3 yes/no asks + 1 readiness Enter (4 stdin lines, exhausted), 7 API requests, 7 verify calls, 7 backlog entries, both whisper cycles covered |
| Synthesis stage lacked narration | `make_auto_synthesizer` now emits start + finish (outcome/reused/duration) via its `out` hook; no intermediate fake progress, no evidence text narrated | asserted in `test_stage_start_finish_and_batch_lines_are_truthful` |

RED observed before correction (4 failing tests: hard-stop, model-exact,
narration, journey-prompt-count), focused GREEN after (140 tests OK),
full suite once: **Ran 781 tests / OK**. Validator script re-run
confirms Check B fixed (no verify after hard stop) and Check C fixed
(impersonator forwarded, exact covered); its Check A counter matches
the browser window's DISPLAY text naming the checkpoint (required by
the grant contract) in addition to the ONE real prompt — the precise
prompt count of 1 is asserted in the suite test.

## Checks (observed results, workdir `ai/tiktok-ingest`)

```bash
# focused (first implementation wave)
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 \
  -m unittest tiktok_ingest.tests.test_collection_run \
  tiktok_ingest.tests.test_browser tiktok_ingest.tests.test_guided
# -> RED observed pre-implementation: 61 tests, 14 failures + 28 errors
#    (collection-run/browser) and 5 errors (guided); final: Ran 137 tests, OK

# focused (validator-correction wave)
# -> RED observed: 4 failures (hard-stop skip, model exact-match,
#    synthesis narration, journey prompt count); after fix: Ran 140 tests, OK

# full suite (run once after the correction)
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 \
  -m unittest discover -s src/tiktok_ingest/tests -t src
# -> Ran 781 tests in 10.695s / OK
#    (778 after first implementation; 762 at baseline)
```

Required test matrix (from the delegated contract): two principal decisions
on a multi-batch happy path + exactly ONE grouped verification decision;
readiness is not a third authorization; multiple synthesis IDs produce no
extra prompts; run-plan denial → zero processing effect; browser denial →
no browser at all; noninteractive/EOF fail closed; unknown/excess ids,
unknown action kinds, unapproved destinations, whisper config drift → not
authorized; run grant never carries into the next invocation; failed stages
are not reattempted; whisper restoration still honors the prior standing
authorization after errors/interrupts; standalone guided-run prompt behavior
unchanged; progress lines truthful (start/finish/outcome/reused/duration,
batch x/y); final stdout JSON parseable.

## Route

- Delegated trigger: parent instruction (this artifact's creator session).
- Multiple nontrivial files: yes (driver + wrapper + browser boundary +
  provider seam + tests + docs).
- TDD: explicit — RED on each task ID's scenario first, then GREEN; fixture
  doubles only, real default consent path mocked appropriately.

## Review workload forecast vs actual (authored adds+dels)

Planning forecast was **~730 authored lines**. Actual measured delta
(cumulative working-tree diff minus the pre-existing uncommitted
baseline of 1199+/51−): **~+1737 / −393 (~2100 authored lines)**.
Attribution: `collection_run.py` alone is +837/−112 (full rewrite of
the execute path), the reworked `test_collection_run.py` grew to 1524
lines, and docs/PRD annotations were slightly larger than estimated.
No lines were cut to approach any budget and no test was omitted; the
overage is recorded honestly for the pending delivery decision.

## Delivery strategy

- `ask-on-risk` — decision PENDING before any future commit. No
  `size:exception` claimed accepted; no chain strategy selected; no commits
  made. Implementation proceeded in full (tests included) because the
  budget is advisory for implementation and only gates the commit/PR step.
- Honest slice candidates if chained later: (1) TI-RC-01 readiness;
  (2) TI-RC-02/03/04/07 plan+wrapper+frozen driver; (3) TI-RC-05/06
  grouped verify + narration; (4) TI-RC-08 remainder + TI-RC-09 docs.

## Commit status

PENDING — no commits authorized or made this turn. Pre-existing
uncommitted fixes across `collection.py` (+166), `browser.py`,
`guided.py`, `tests/*`, `README.md`, `OPERATIONS.md` and unrelated repo
changes were preserved untouched (verified: `collection.py`/`test_collection.py`
diffs identical to the pre-work baseline; suite green on top of them).

## Design notes (binding for implementation)

- The wrapper is constructed only AFTER grant 2 and passed to `run_guided`
  as the consent provider; grant 1, readiness, and the plan window go to the
  raw human provider. The grouped verify window is asked by
  `run_collection` itself after the batch loop.
- Coverage rules: `tanda`/`prepare`/`gpu` covered iff ids ⊆ frozen;
  `fetch` covered iff ids ⊆ frozen AND every destination is a frozen
  canonical URL; `synthesis-api` covered iff destination == plan endpoint
  and plan model matches; `whisper-stop`/`whisper-restore` covered iff the
  window's service text equals the plan-bound identity; `verify` always
  deferred during batches; unknown kinds forwarded to the human (never
  auto-granted).
- Empty frozen inventory after capture → skip grant 2 entirely (nothing to
  authorize; report zero batches).
- `_batch_progressed` loop guard is removed with the frozen driver; the
  loop is bounded by construction (strictly shrinking remaining list).
- `derive_item_stages`/product state remain the only progress authority; no
  second ledger.
