# TikTok ingest: curses terminal UI (option B)

## Objective and authorization

Add an interactive curses-based terminal UI (stdlib only, no third-party
dependency) that shows live run progress and renders consent windows as
modals, for `guided-run` and `collection-run`. The user chose `curses`
explicitly on 2026-10-12.

This is a presentation layer only. It MUST NOT weaken the consent contract
(non-TTY still fails closed, no `--yes`), MUST NOT change stage semantics,
gates, fingerprints or stores, and MUST keep stdout JSON parseable.

## Problem and rationale

The current UX is line-based: consent blocks and narration scroll. A live
dashboard makes a long run legible (per-id stage matrix, current activity,
elapsed) and lets the operator answer a window in place.

## Scope and constraints

- New `tui.py`: a curses app with a small state model (per-id stage states,
  phase, current activity, elapsed) and a renderer.
- It implements the existing `ConsentProvider` contract (`ask`,
  `await_readiness`) and an `out` sink, so it drops in where the console
  provider and narration are used today.
- Consent semantics are preserved exactly: only `y`/`yes` grants; EOF, a
  non-interactive stdin, or a read error denies; the full window text (title,
  ids, destinations, operations, limits, effects) is shown.
- Automatic fallback to the existing line-based UI when curses cannot start or
  stdin/stdout is not a TTY (CI, pipes, dumb terminals). The fallback path must
  behave exactly as today.
- stdout stays reserved for the JSON report; the TUI renders on the terminal
  (stderr/screen), never corrupting stdout.
- No new dependencies. No gate/threshold/identity/swap-retry changes.
- Offline tests only; a fake screen/window for rendering tests.

## Tasks and route

- [x] **TI-TUI-01** State model + renderer + `ConsentProvider`/`out`
  implementation with a safe fallback. Tests first. Route: delegated direct.
  Evidence: `src/tiktok_ingest/tui.py` (`TuiState`, `apply_line`,
  `render_lines`, `compose_modal_lines`, `CursesConsentProvider`,
  `FallbackSession`, `ui_supported`, `terminal_ui`); RED then GREEN in
  `tests/test_tui.py` (34 tests OK).
- [x] **TI-TUI-02** Wire into the `guided-run` and `collection-run` CLI entry
  points (auto-detect TTY); keep `--dry-run` and non-TTY paths unchanged.
  Evidence: `cli.py` guided-run/collection-run now run inside
  `tui.terminal_ui()`; fallback passes `consent=None`/`out=None` so the
  existing defaults run byte-identically. `tests/test_cli.py` TUI wiring
  tests (4) OK.
- [x] **TI-TUI-03** Tests + docs; verify the full offline suite and a
  non-TTY smoke run. Evidence: full discover suite `Ran 909 tests ... OK`
  (baseline 871 + 38 new); non-TTY smoke below; README + OPERATIONS updated.

## Acceptance criteria

1. On a TTY, a run shows a live dashboard: per-id stage states, current
   activity and elapsed time, updating as stages progress.
2. Consent windows render in the TUI and preserve exact semantics: only
   `y`/`yes` grants; EOF/non-TTY/read error denies; the whole window text is
   visible.
3. Non-TTY, `--dry-run`, or curses-unavailable falls back to the current
   line-based behavior unchanged.
4. stdout remains the parseable JSON report; the TUI never writes to stdout.
5. No new dependencies; gates, identity checks, and the swap-retry contract are
   untouched; the offline suite passes.

## Acceptance evidence

1. Live dashboard on TTY: `render_lines` + `CursesConsentProvider.out` fold
   the real narration into per-id states / phase / activity / elapsed;
   renderer exercised through a fake window.
2. Consent semantics: `test_tui.ConsentSemanticsTests` — only `y`/`yes`
   grants; EOF, read error and non-TTY deny; `compose_modal_lines` equals
   `render_consent_window` (whole text).
3. Fallback: `terminal_ui` yields `FallbackSession` (consent/out None) off a
   TTY or when curses cannot start; `--dry-run` never starts the TUI.
4. stdout JSON: report printed only after the curses session; smoke run
   below produced valid JSON on stdout with exit 0.
5. No new dependencies (stdlib `curses`); full offline suite passes.

Non-TTY smoke (write-integrity verified, zero writes):

```
$ cd ai/tiktok-ingest && env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
    python3 -m tiktok_ingest guided-run --ids 7689460059104890130 --dry-run
exit=0; stdout valid JSON (mode=dry-run, selected=1); stderr empty

$ env ... python3 -m tiktok_ingest guided-run --ids 7689460059104890130
exit=0; stdout valid JSON (mode=execute, run_plan.granted=False);
stderr contains "refused: non-interactive environment"; md5 of
processed.json/inventory.json/backlog.jsonl unchanged (NO WRITES)
```

## Verification and baseline

Focused:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_tui tiktok_ingest.tests.test_guided tiktok_ingest.tests.test_cli
# Ran 157 tests ... OK
```

Full:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src
# Ran 909 tests ... OK
```

Baseline: 871 tests OK (+38 new: 34 `test_tui` + 4 `test_cli` wiring). Real
interactive validation is manual (TTY) and requires operator authorization.

RDD: off (clone-local). Delivery: ask-on-risk; commits only if asked.

## Progress and next step

- Decision fixed: curses, stdlib only, with a mandatory line-based fallback.
- TI-TUI-01..03 complete. Next: operator TTY validation (manual, needs
  authorization); no further code change required for acceptance.
