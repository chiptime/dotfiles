# TikTok ingest: single upfront consent for guided-run

## Objective and authorization

Make `guided-run` ask ONE upfront authorization covering the whole tanda,
exactly like `collection-run`'s run plan, instead of prompting per phase.
User request 2026-10-12: "me hace demasiadas preguntas, solo quiero darle el
ok una vez y que haga lo que le toque hacer".

This is a consent-UX change, NOT a safety bypass: the single window must name
every id, destination, operation, service and budget honestly; EOF or
non-interactive stdin still fails closed; genuinely new scope still asks.

Local implementation and offline testing only. No live browser, network, GPU,
model or service operation. No commits unless asked.

## Problem and rationale

`collection-run` freezes a run plan, asks ONE whole-run window, and discharges
the covered per-phase windows through `RunScopedAuthorization`. `guided-run`
kept the older per-window prompts (tanda, prepare, gpu, whisper-stop,
whisper-restore), which is noisy for a single operator doing one tanda.

## Scope and constraints

- `guided-run` builds a run plan from its selection: frozen ids, canonical
  URLs, the full stage sequence, the bound Whisper service identity and its
  stop/restore, the isolated Ollama lifecycle, and (when synthesis is
  involved) the text API endpoint and model.
- Ask exactly ONE window naming all of the above plus the budget/limits.
- On grant, auto-discharge only the covered kinds (tanda, prepare, fetch, gpu,
  whisper-stop, whisper-restore). Verification keeps its own grouped decision.
  Any window whose ids/destinations/service fall outside the plan is asked as
  genuinely new scope, never auto-granted.
- EOF, denial or non-interactive stdin still fails closed.
- Reuse the existing run-plan/coverage machinery; if it must move into
  `guided.py` to avoid a circular import, that is allowed — keep behaviour
  identical for `collection-run`.
- Do not weaken gates, thresholds, identity checks, or the swap-retry contract.
- Offline tests only.

## Tasks and route

- [x] **TI-GSC-01** Build the single run-plan consent for guided-run and the
  coverage provider; wire the CLI. Tests first. Route: delegated direct.
  Evidence: `guided.py` builds a `RunPlan` from the selection, asks ONE
  `build_run_plan_window` window, then wraps consent in
  `RunScopedAuthorization` behind `_GuidedRunScopedConsent` (verify stays a
  separate human decision); `cli.py` guided-run help/description updated.
  RED (stashed guided.py): 7 new tests → 5 failures + 2 errors
  (`expected 'run-plan', got 'tanda'`); GREEN: 98/98 test_guided.
- [x] **TI-GSC-02** Update help/docs; verify the full offline suite and a real
  read-only dry-run. Parent spot check.
  Evidence: README "Guided operation" and OPERATIONS section 8 rewritten for
  ONE run-plan window; focused 167 OK, full 871 OK; real dry-run
  `guided-run --ids 7689460059104890130 --dry-run` → exit 0, zero
  prompts/writes/GPU, empty stderr; `git diff --check` clean.

## Acceptance criteria

1. A `guided-run` invocation asks exactly ONE authorization window covering the
   whole tanda, then runs prepare/vision/audio without further prompts.
   Evidence: `TestGuidedRunSingleConsent.test_one_window_then_covered_windows_discharged`
   asserts `consent.asked_kinds == ["run-plan"]` while covered windows run.
2. The window names every id, canonical URL, service identity, stop/restore and
   the text API endpoint/model when relevant.
   Evidence: `test_run_plan_window_names_ids_urls_service_and_limits`,
   `test_run_plan_window_names_text_api_endpoint_and_model`.
3. Denial/EOF/non-interactive still stops before any effect.
   Evidence: `test_run_plan_denial_runs_nothing`,
   `test_run_plan_eof_fails_closed`, updated CLI non-interactive test.
4. Scope drift (ids, URLs, service identity, endpoint/model) is asked as new
   scope, never auto-granted.
   Evidence: `test_out_of_plan_window_is_asked_as_new_scope`.
5. `collection-run` behaviour and its JSON contract are unchanged.
   Evidence: `collection_run.py` untouched (byte-identical); focused
   `test_collection_run` 69 OK.
6. No new dependencies; existing tests and gates unchanged.
   Evidence: stdlib only; coverage/permission/gate code untouched; existing
   per-window coordination tests preserved (now at the GuidedRun engine seam).

## Verification and baseline

Focused (ran after change, 167 OK):

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_guided tiktok_ingest.tests.test_cli tiktok_ingest.tests.test_collection_run
```

Full (ran after change, 871 OK; baseline was 864 + 7 new):

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src
```

Baseline: 864 tests OK. Live GPU/Whisper validation deferred.

RDD: off (clone-local). Delivery: ask-on-risk; commits only if asked.

## Progress and next step

- Diagnosis complete: guided-run lacked the run-plan single-consent path.
- TI-GSC-01/02 done: guided-run asks ONE run-plan window and auto-discharges
  the covered kinds, verification stays its own grouped decision.
- Next: none required; live GPU/Whisper validation remains deferred to a
  separate authorization.
