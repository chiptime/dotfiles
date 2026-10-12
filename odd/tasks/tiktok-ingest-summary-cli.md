# TikTok ingest: human-readable `summary` command

## Objective and authorization

Two linked deliverables. First, persist each `collection-run` report JSON to a
browsable machine-local folder named by collection key plus timestamp. Second,
add a read-only `summary` subcommand that renders the durable per-video stage
state as a compact human table with a derived next step, keeping a `--json`
mode for scripting. The user explicitly asked to improve the raw `collection-run`
JSON dump into "un mini cli o algo" and to save the JSON under "id de coleccion
+ fecha"; implementation authorized on 2026-10-12.

Local implementation and offline testing only. No live browser, network, GPU,
model, service, or state-mutating operation. No commits unless separately asked.

## Problem and rationale

After a real collection run the operator had to read a large JSON document to
learn something simple: audio completed for every video, vision failed on the
first one. A focused, read-only view makes the real outcome obvious in a line
while preserving the machine-parseable JSON contract.

## Scope and constraints

- New `summary` command: optional `--collection URL_OR_KEY`, optional `--json`,
  plus the shared `--state-root`.
- Persist the `collection-run` report JSON under
  `<state_root>/reports/<collection_key>/<UTC-timestamp>.json` using the existing
  atomic writer, and narrate the saved path to stderr. Never write reports into
  the repository. Keep the existing stdout JSON unchanged.
- Human mode prints a table plus one derived "next" line; JSON mode returns a
  structured object. Do not change `collection-run` stdout JSON or `status`.
- `summary` reads live durable state by default and can read a saved report via
  `--report latest|PATH` for a collection.
- Reuse the durable product stores/manifests and existing stage derivation; no
  second state and no writes in `summary` itself. Read-only.
- Cover: all items (no collection) and one collection's plan items; per-stage
  states; backlog status; honest pending reasons; author handle when derivable.
- Offline tests only, with fixture state; no real services.

## Tasks and route

- [x] **TI-SUM-01** Persist the collection-run report JSON to the dated
  per-collection folder and narrate the path. Add offline tests. Route:
  delegated direct. Evidence: `_persist_report` in
  `collection_run.py` writes
  `<state_root>/reports/<collection_key>/<UTC-timestamp>.json` via
  `atomic_write_json` at every execute-mode return (dry-run writes
  nothing) and narrates to the `out` channel (stderr). Tests:
  `TestReportPersistence` (3 tests: path shape + atomic content + stderr
  narration; never under `/.dotfiles/`; dry-run writes no report).
- [x] **TI-SUM-02** Implement the summary builder and renderer, wire the CLI
  subcommand (`--collection`, `--json`, `--report`), and add offline tests.
  Route: delegated direct. Evidence: new `summary.py`
  (`build_summary`/`render_table`/`derive_next`/`author_from_url`/
  `resolve_report_path`), CLI `summary` subparser + dispatch; 10 tests in
  `test_summary.py` (human table, `--json`, no-collection all items,
  `--collection` filtering, `--report latest`/`PATH`, read-only,
  unknown-collection failure).
- [x] **TI-SUM-03** Document the command and report folder in README/OPERATIONS
  and verify the full offline suite plus a real read-only run against the
  current state. Route: delegated direct; parent spot check. Evidence:
  README "Reading a run later (`summary`)" + report-persistence bullet;
  OPERATIONS command-table row + "Saved run reports" + "9.6". Full suite:
  848 tests, 1 failure ONLY in the out-of-surface
  `test_cli.test_subcommands_are_declared` (see gap). Real read-only run
  against `c99f2ad2160a8254` (human + `--json`) worked and mutated nothing
  (no `reports/` dir was created by `summary`).

## Acceptance criteria

1. A finished `collection-run` writes its report JSON under
   `<state_root>/reports/<collection_key>/<UTC-timestamp>.json` and prints the
   path to stderr; no report is written inside the repository.
2. `summary` prints a legible table with one row per item and per-stage states.
3. `--json` emits a stable structured object; default stdout stays human.
4. No collection: summarizes all processed items. With `--collection`: limits
   to that collection's plan items and shows its counts.
5. `--report latest|PATH` reads a saved report instead of live state.
6. A derived "next" line reflects the real pending reason (vision pending with
   audio saved, synthesis pending, all complete, etc.).
7. Read-only in `summary`: no state, manifest, or backlog writes; no
   network/GPU/service use. Existing commands and JSON contracts unchanged.

## Verification and baseline

TDD unconfigured for TikTok ingest; apply the default deterministic test-first
policy (observe RED for the new behavior, then GREEN).

Focused:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_summary
```

Full:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src
```

Baseline: 835 tests OK observed on 2026-10-12 before this change (doc
said 836; reconcile to 835). After: 848 tests, 1 out-of-surface failure
(`test_cli.test_subcommands_are_declared`), see gap below.

RDD: off (clone-local). No native review. Delivery: ask-on-risk; commits only
if separately requested.

## Progress and next step

- TI-SUM-01/02/03 implemented and offline-verified (see evidence above).
- RED then GREEN: focused run observed 13 tests / 1 failure + 11 errors
  before implementation, then OK after.
- GAP (out of allowed surface): `ai/tiktok-ingest/src/tiktok_ingest/tests/test_cli.py`
  `ParserTests.test_subcommands_are_declared` asserts the exact subcommand
  set and now fails because `summary` was added. Derived candidate:
  add `"summary",` to the expected set (after `"collection-run",`).
- No commits/staging/push performed; unrelated dirty files preserved.
