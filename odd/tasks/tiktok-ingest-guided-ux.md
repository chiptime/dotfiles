# TikTok ingest: guided-run whisper coordination and terminal UX

## Objective and authorization

Two linked deliverables, explicitly requested by the user on 2026-10-12:
1. `guided-run` must coordinate the shared Whisper service automatically
   (verified stop before vision, standing restore after, under explicit
   consent) instead of requiring manual `podman stop/start`.
2. A polished terminal experience (option A: stdlib only, no third-party
   dependencies): clear consent prompts, per-stage progress, and a readable
   final summary. A full interactive TUI is explicitly deferred.

Local implementation and offline testing only. No live browser, network, GPU,
model, or service operation. No commits unless separately asked.

## Problem and rationale

`collection-run` builds stage functions with whisper lifecycle hooks
(`build_stage_functions`, collection_run.py:336), but the `guided-run` CLI calls
`run_guided` with no stages, so it uses `default_stage_functions()` and cannot
stop/restore Whisper. Vision then fails closed with "stop the whisper service
first (operator-authorized action)". This blocked a real single-video recipe
run, forcing manual `podman stop/start`. The terminal output is also a raw JSON
dump plus plain prompt blocks.

## Scope and constraints

- Reuse the existing whisper lifecycle contract (verified identity, bounded
  stop grace, standing restore authority asked BEFORE the stop, health
  validation, UNKNOWN semantics on interrupt). Do not invent new authority.
- `guided-run` gains the same coordination `collection-run` already has; keep
  it default-on and update the command help/docs that currently say Whisper
  stays operator-managed. Non-interactive/EOF still fails closed.
- No new CLI flags. Preserve stdout JSON parseability; UX narration stays on
  stderr.
- UX (stdlib only): clearer, aligned consent prompts with a one-line intent;
  per-stage progress with elapsed time; a readable end-of-run summary reusing
  `summary.render_table`/`derive_next`; clear denial/error messages.
- Do not change stage semantics, gates, fingerprints, or stores. Preserve the
  phase-first collection behavior and the swap-retry contract.
- Offline tests only; no real services.

## Tasks and route

- [x] **TI-GRX-01** Wire whisper lifecycle hooks into `guided-run` and update
  the affected help/docs. Tests first; preserve standalone selection behavior.
  Route: delegated direct. Done: `guided.whisper_coordinated_stage_functions`
  adds the three `whisper_lifecycle` hooks; `cli.py` guided-run passes them to
  `run_guided` (default-on). Tests:
  `test_cli.GuidedRunDispatchTests.test_guided_run_wires_whisper_lifecycle_stages`
  and `test_guided.TestWhisperCoordinatedStageFunctions`. Standalone default
  (`default_stage_functions`) unchanged.
- [x] **TI-GRX-02** Polish the terminal UX (prompt formatting, stage progress,
  readable final summary) with stdlib only. Tests for formatting and summary
  output. Route: delegated direct. Done: `guided.render_consent_window`
  (aligned one-line intent + sections, clearer denial/EOF/error text),
  `guided.narrate_stages` (start/finish + elapsed, stderr-only),
  `summary.guided_report_summary` reused by `cli.py` to print
  `render_table(...)` on stderr. Stdout JSON shape unchanged.
- [x] **TI-GRX-03** Document the new behavior; verify the full offline suite and
  a real read-only dry-run. Parent spot check. Done: README/OPERATIONS updated;
  full suite 864 tests OK (848 baseline + 16 new); real dry-run produced the
  plan JSON with zero prompts/writes/GPU.

## Acceptance criteria

1. `guided-run` stops the KNOWN Whisper service (verified) before vision and
   restores it after, under a standing consent asked before the stop; EOF or
   non-interactive stdin still fails closed.
2. A single-video `guided-run` completes vision then audio without manual
   `podman` commands.
3. Consent prompts are clearly formatted and aligned; each shows a one-line
   intent plus the existing operations/limits/effects.
4. Long stages show progress with elapsed time on stderr.
5. The run ends with a readable summary (per-stage states + next step); stdout
   JSON stays parseable and unchanged in shape.
6. No new dependencies; no gate/threshold/consent weakening; existing tests and
   JSON contracts unchanged.

## Verification and baseline

TDD unconfigured; apply the deterministic test-first policy (RED then GREEN).

Focused:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_guided tiktok_ingest.tests.test_cli tiktok_ingest.tests.test_summary
```

Full:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src
```

Baseline: 848 tests OK (current). Real GPU/Whisper validation is deferred and
requires separate authorization.

RDD: off (clone-local). Delivery: ask-on-risk; commits only if asked.

## Progress and next step

- Diagnosis complete (guided-run lacked whisper hooks; UX is raw).
- TI-GRX-01..03 implemented and verified (see checkboxes and evidence).
- Verified: focused suite (test_guided/test_cli/test_summary) OK; full offline
  suite 864 OK (848 baseline); real read-only dry-run emits only the plan JSON.
- Not done (deferred, separate authorization): real GPU/Whisper/browser
  validation and the full interactive TUI (option A only was authorized).
- Next: parent spot check of the diff; any live-service validation later.
