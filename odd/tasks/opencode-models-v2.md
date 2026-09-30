# opencode-models-v2

## Objective
One supported way to assign models to opencode agents, reproducible from this repo, that
survives `gentle-ai sync` and catches stale/renamed model IDs immediately. v1
(`scripts/install-opencode-settings.sh` + `ai/agents/opencode/settings/`) stays untouched as the rollback.

## Problem / why
2026-10-01: `opencode-go/qwen3.7-max`, `deepseek/deepseek-v4-flash`, `deepseek/deepseek-v4-pro-0813`
and `opencode/nemotron-3-super-free` no longer exist. Fixed by hand with `sed`; model choices live in
three unsynchronised places (live `opencode.json`, stale `profiles/`, router json) and
`gentle-ai sync` can silently revert them.

## Scope (authorized)
- New, parallel to v1 (v1 files are NOT modified): models map, installer v2, README v2.
- Model map lives OUTSIDE `ai/agents/opencode/settings/` so v1 never merges it (keeps rollback pure).
- Archive stale `~/.config/opencode/profiles/` and `profile-versions/` (user confirmed unused).
- Router `opencode-router.json`: re-rendered without `model` (already done; committed here as its own unit).

## Constraints / decisions
- Decision (assumed, user said "vale" to the recommendation): model map is tracked in the repo.
- v2 applies `agent.<x>.model` ONLY to agents that already exist in `opencode.json` (no ghost agents);
  warns on orphans (in map, not in config) and on unpinned agents (in config, not in map).
- Every model ID is validated against `opencode models` BEFORE writing; any invalid ID aborts untouched.
- gentle-ai-owned fields (prompt, permission, tools, fallback structure) are never touched.
- TDD: no project configuration found; mode = disabled (source: none). Functional checks only,
  run in a sandbox HOME with a copy of the config. No runner beyond bash/jq.
- Delivery: forecast ~350 authored lines, under the ~400 heuristic -> `single-pr`, feature branch
  `feat/opencode-models-v2`. Repo is on `master` with unrelated dirty work: commit by explicit paths only.
- RDD: off (clone-local) -> no native review; ordinary checks remain.

## Tasks
- [x] T1 Generate `ai/agents/opencode/models/models.v2.json` from the live config (inline, mechanical). Route: inline.
- [x] T2 Write `scripts/install-opencode-settings-v2.sh` + `ai/agents/opencode/models/README.md`. Route: delegated writer was attempted and FAILED (provider 5h usage limit, reset 07:15) -> wrote inline. Route deviation recorded on purpose.
- [x] T3 Functional verification in sandbox (`scripts/test-install-opencode-settings-v2.sh`). Route: inline.
- [x] T4 Real-config check (`--check` => no changes, exit 0) and archive of stale profiles. Route: inline.
- [ ] T5 Commits: (a) router re-render, (b) models v2. Route: inline.

## Progress
- T1-T4 done. Router re-render done before this document (committed in T5a).

## Verification evidence
- Sandbox suite: 26 ok, 0 failures (9 scenarios: apply, idempotent, orphan, unpinned, invalid id outside map,
  invalid id in map aborts untouched, dry-run/check, validation skipped, local fragment shadowed).
- Mutation test: removing the ghost-agent guard makes exactly the 2 orphan tests fail; restored -> 26/26.
- Live `--check` against the real config + real `opencode models` (209 ids): all IDs valid, no changes, exit 0.
- Profiles archived to `~/.local/state/opencode-archive/profiles-20261001.tar.gz`; 103 files extracted and
  `diff -r` identical BEFORE removing `profiles/` and `profile-versions/`.
- Router suite `bun test tests/agy-router.test.ts`: 156 pass, 0 fail (was 3 failing at HEAD).
- shellcheck: only SC2001 (style) on a `sed` pipe; not changed.

## Known limits
- Not verified: how `gentle-ai sync` treats existing `model` keys (only `--dry-run` seen). v2 design does not depend on it
  (re-run installer after sync; `--check` detects drift).

## Next step
T5 commits, then report.
