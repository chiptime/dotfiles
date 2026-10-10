# Task: alt-g-baseline-diff — Offline `--diff` baseline comparison for the Alt+G harness

- **ID**: alt-g-baseline-diff
- **Route**: delegated (package-owned implementation writer; parent orchestrator owns review/commit)
- **Trigger**: parent authorization 2026-10-07. User approved `--diff` ONLY — no provider changes, no context collection, no commits/staging/push/remote calls, no live LLM requests.
- **Repo**: `~/.dotfiles/modules/zsh-ai` (standalone nested git repo; `harness/` currently untracked — pre-existing state, preserved)
- **Status**: in_progress
- **Forecast**: ~150–250 authored lines across task doc + tests + implementation + README (advisory, not a cap)

## Objective

Add `./harness/evaluate.sh --diff [case_file.txt]`: a fully OFFLINE comparison mode that
diffs existing `snapshots/<name>.actual.txt` against `snapshots/<name>.expected.txt`,
prints unified diffs plus accurate counts, and never writes to `snapshots/`.

## Background

The harness currently only PRODUCES snapshots (evaluate_one → LiteLLM → `*.actual.txt`).
There is no way to compare an actual snapshot against a reviewed baseline without
network access. `snapshots/.gitignore` ignores `*.actual.txt` only, so
`*.expected.txt` baselines are already trackable — the missing piece is the
comparison command and the documented baseline workflow.

## Scope

### In scope

1. `harness/evaluate.sh`
   - New `--diff [case_file.txt]` mode:
     - No argument → compare ALL snapshot names: the union of `<name>.actual.txt`
       and `<name>.expected.txt` present in `snapshots/` (sorted, deduplicated).
     - With a case file → derive the snapshot name exactly like the evaluation path:
       `basename <file> .txt`, then spaces→hyphens (`${name// /-}`). The case file
       is used for its NAME only; it is never read, so relative paths work from any
       cwd.
   - Exit codes:
     - `0` — all compared snapshots equal.
     - `1` — at least one change or missing side.
     - `2` — operational errors: invalid CLI (unknown option after `--diff`, more
       than one argument), no snapshots at all (empty union, or explicit case with
       zero snapshot files), `diff` missing, `diff` exiting with trouble (`>1`).
   - Missing sides are DISTINGUISHED: "missing actual" (baseline exists, no actual)
     vs "missing baseline" (actual exists, no `*.expected.txt`). Both count as
     Missing and force exit 1, never 2.
   - Unified diffs (`diff -u expected actual`) for changed snapshots; batch mode
     CONTINUES after differences (only operational errors abort).
   - Read-only: `--diff` never creates, modifies, or deletes anything in
     `snapshots/` (or anywhere else).
   - Offline dependency handling: `--diff` requires only `diff` and MUST bypass the
     curl/jq dependency check; curl/jq are never invoked on this path.
   - Accurate summary counts: `Matched`, `Changed`, `Missing` (with actual/baseline
     breakdown when non-zero).
   - Preserve existing evaluation invocation unchanged: `<case_file.txt>` and
     `--all` behave as before (dependency check now runs inside those branches so
     the offline path can bypass it; no-args still prints usage and exits 1).
   - Usage text updated to list `--diff`.
   - Spaces in snapshot/case names handled via quoted arrays; arbitrary cwd handled
     because all paths resolve from `BASH_SOURCE`.
2. `harness/evaluate.test.zsh`
   - Replace live-network and tautological tests with deterministic, isolated
     temp-copy fixture tests under `/tmp/opencode` (copy of the REAL script into a
     fixture tree — no sed patching, no mutation of the real `cases/`/`snapshots/`).
   - Mock external executables (curl, jq, diff) via PATH stub directories — never
     shell functions — because `evaluate.sh` runs as a separate Bash process.
   - Required coverage:
     - actual == baseline → exit 0, `Matched: 1`
     - actual != baseline → exit 1, unified diff body, `Changed: 1`
     - missing baseline (only actual) → exit 1, distinguished message
     - missing actual (only baseline) → exit 1, distinguished message
     - batch with mixed outcomes continues and reports exact counts
     - snapshot names containing spaces (union scan)
     - explicit case-file argument with spaces → space-to-hyphen mapping
     - unknown option (`--diff --promote`) → exit 2
     - too many arguments → exit 2
     - empty snapshots dir → exit 2
     - explicit case with zero snapshots → exit 2
     - `diff` operational failure (PATH stub exiting 2) → exit 2
     - snapshots byte-identical before/after (sha256 of every file)
     - offline: curl/jq never invoked (recording stubs; no marker file)
     - offline: works with curl/jq entirely absent from PATH (bypass proof)
     - arbitrary cwd (run from `/`)
     - evaluation path still works offline end-to-end with mocked curl
       (real jq, local only) and writes its snapshot inside the fixture tree
   - Keep genuinely useful offline tests: script exists/executable, usage on no
     args, case-not-found, missing `=== Buffer ===` (rebuilt on fixtures).
3. `harness/README.md`
   - Document `--diff` usage, exit codes, offline requirements (diff only).
   - Document the baseline workflow: human reviews `*.actual.txt`, then explicitly
     `cp` to `*.expected.txt` to establish the baseline. NO `--promote` flag.
   - Note that `*.actual.txt` stays gitignored and `*.expected.txt` is trackable.
4. `odd/tasks/alt-g-baseline-diff.md` (this document) — created BEFORE source edits.

### Out of scope (explicit)

- No `--promote` or any other new flag/feature.
- No provider changes, no context collection, no live LLM requests.
- No fix for the existing provider API mismatch (chat-shaped `messages[]` payload
  posted to a `/v1/completions` endpoint, parsed as `.choices[0].text`) — reported
  as followup.
- No fix for `--all` aborting on the first failing case (`call_litellm`/`save_snapshot`
  use `exit 1`, killing the batch loop) — reported as followup.
- No commits, staging, push, remote calls (dotfiles skill: commit only on explicit
  request).

## Design notes

- Mode dispatch happens before `check_dependencies` so `--diff` never requires
  curl/jq; `check_diff_dependencies` requires only `diff`.
- Union scan uses `nullglob` over both suffixes into an associative array, then
  `sort -z` for deterministic order (names may contain spaces).
- Per name: both files present → `diff -u expected actual` (0=matched, 1=changed
  and printed, ≥2=operational error → exit 2 immediately); one side missing →
  counted+reported, batch continues.
- Summary printed to stdout; warnings/errors to stderr (existing log_* helpers).

## Checklist (exact checks)

- [x] Task doc created before source edits (this file)
- [x] RED: rewritten `evaluate.test.zsh` run against unchanged `evaluate.sh` —
      16 new `--diff`/usage tests observed failing (7 offline tests already green)
- [x] GREEN: `bash -n harness/evaluate.sh` passes
- [x] GREEN: `zsh -n harness/evaluate.test.zsh` passes
- [x] GREEN: `zsh harness/evaluate.test.zsh` — 23 passed, 0 failed, exit 0
- [x] GREEN: `zsh run-tests.zsh harness` — file discovered, 23 passed, 0 failed
- [x] `--diff` never mutates fixtures (sha256 before/after equal)
- [x] curl/jq never invoked in `--diff` (marker-based proof)
- [x] `--diff` succeeds with curl/jq absent from PATH
- [x] Exit codes 0/1/2 as specified, missing actual vs baseline distinguished
- [x] Existing `<case_file>` / `--all` invocations preserved (offline mocked test)
- [x] README documents `--diff`, exit codes, explicit-cp baseline workflow
- [x] Real `harness/cases/` and `harness/snapshots/` untouched (fixtures in /tmp/opencode)
- [x] Files read back after writing
- [x] Recovery mirror saved to Engram under the correctly detected project (failure
      to save is not a blocker; no invented session id)

## Status: COMPLETED 2026-10-07 — pending parent review

## Correction round (independent verifier findings, same day)

Scoped corrections only; same allowed surfaces; no commits/network; no provider
or `--all` changes.

### Findings and fixes

1. **Unchecked `sort` in process substitution** (evaluate.sh union listing):
   `<(printf '%s\0' ... | sort -z)` failure was invisible to `set -e`/`pipefail`
   — a failing sort (or failure after partial output) could silently yield an
   empty/partial `names` batch and exit 0.
   Fix: explicit-case mode needs no listing tools; union mode now pre-checks
   `sort`/`mktemp` (exit 2 "Missing: ..."), writes the NUL name list to a
   `mktemp` file, runs status-checked `sort -z -o <tmp> <tmp>` (exit 2 on any
   failure, including partial output), reads names back from the file, removes
   the temp file, and trips an exit-2 guard if zero names survived.
2. **Sequential suffix removal misnamed dotted pairs**: `${b%.actual.txt}` then
   `${b%.expected.txt}` collapsed `release.expected.txt.actual.txt` and
   `release.expected.txt.expected.txt` both to `release`.
   Fix: the two glob patterns are now processed separately, each stripping ONLY
   the suffix it matched — "release.expected.txt" stays one complete name.
3. **Test cleanup removed the shared namespace**: `cleanup_fixtures` rm'd
   `/tmp/opencode/zsh-ai-diff-tests` itself, so concurrent invocations could
   delete each other's fixtures.
   Fix: `FIXTURE_BASE` is now invocation-owned (`run.$$.$RANDOM`), and cleanup
   removes it only behind an ownership guard (`${NAMESPACE}/run.*` pattern) —
   never the shared namespace or other invocations' directories.

### Correction checklist (exact checks)

- [x] RED: 5 deterministic failing tests added first; run observed
      `Results: 23 passed, 5 failed` — exactly the 5 correction tests failing
- [x] GREEN: `zsh harness/evaluate.test.zsh` — 28 passed, 0 failed
- [x] Regression coverage: sort failing; sort failing after partial output;
      sort binary missing; dotted-suffix pair name preserved; sentinel fixture
      ownership (own run dir removed, sibling sentinel survives)
- [x] Minimal-PATH offline test updated for the union listing's real tool set
      (`sort`, `mktemp`, `rm` added to symlinks; explicit-case path unchanged)
- [x] README offline requirements updated (sort + mktemp for all-snapshots mode)
- [x] No provider/`--all` changes; no commits; real snapshots/cases untouched

### Verification (correction round — exact four prior commands)

Re-run after corrections; results reported in the work-unit handoff.

## Verification (parent-authorized, run once, foreground)

```
bash -n harness/evaluate.sh
zsh -n harness/evaluate.test.zsh
zsh harness/evaluate.test.zsh
zsh run-tests.zsh harness
```

## Followups (reported, not fixed here)

1. Provider API mismatch: `build_payload` emits chat-shaped `messages[]` but the
   default `ZSH_AI_LITELLM_URL` is `/v1/completions` and `save_snapshot` parses
   `.choices[0].text`. Either switch to `/v1/chat/completions` + `.message.content`,
   or emit `prompt` for completions.
2. `--all` abort: `call_litellm`/`save_snapshot`/`parse_test_case` call `exit 1`, so
   the first failing case terminates the whole batch; the PASSED/FAILED counters in
   `evaluate_all` are unreachable on failure. They should `return` non-zero instead.
3. Baseline establishment is manual (`cp`) by design; a future `--promote` was
   explicitly rejected for now.
