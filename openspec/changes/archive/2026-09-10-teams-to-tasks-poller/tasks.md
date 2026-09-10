# Tasks: Teams-to-Tasks Poller

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~625 authored, excl. `bun.lock` — **crosses 400: Yes** |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR | Focused test | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Spec, scaffold, core | 1 | `cd ai/teams-to-tasks && bun test tests/core.test.ts` | N/A (pure fns) | New files only |
| 2 | Adapter + poller | 2 ←PR 1 | `cd ai/teams-to-tasks && bun test` | N/A (fake adapter) | Remove poller + src |
| 3 | cron gate, installer, docs | 3 ←PR 2 | `bash -n ai/teams-to-tasks/cron.sh` | Stub rc 0/1/2/124/137; PAUSE/flock | Revert cron/installer/docs |

## Phase 1: Spec & Scaffolding

- [x] 1.1 Amend `openspec/changes/teams-to-tasks-poller/specs/teams-to-tasks-polling/spec.md` (State advancement): news DEFERS last_run to cron.sh post-agent-success (agent failure preserves PREV); no-news advances as before; failure untouched. 731 words; must land under 650. ~20L
- [x] 1.2 Create `ai/teams-to-tasks/package.json` (bun, pinned `playwright-core`) + strict `tsconfig.json`; commit `bun.lock`. AC: `bunx tsc --noEmit` clean. ~35L

## Phase 2: Pure Core (TDD)

- [x] 2.1 RED `ai/teams-to-tasks/tests/core.test.ts`: newer→news, equal/older→no-news, missing state→news, legacy local parse, invalid state→null, relative stamps with injected `now`. (spec: News detection). Fails (no `src/core.ts`). ~110L
- [x] 2.2 GREEN `ai/teams-to-tasks/src/core.ts`: `parseState`, `normalizeTimestamp`, `decide`. AC: core tests green. ~70L

## Phase 3: Poller Lifecycle (TDD)

- [x] 3.1 RED `ai/teams-to-tasks/tests/poller.test.ts` (fake adapter, temp HOME): exit 0/1/2; no-news writes poll-start T; news writes nothing; any error path (throw, invalid state, locked profile, hang) → exit 2, `close()` once, state untouched; SIGTERM identical. (spec: Deterministic exit codes, Browser always closed, Hard timeout; threat rows). Fails (no `poller.ts`). ~115L
- [x] 3.2 GREEN `ai/teams-to-tasks/poller.ts`: `Promise.race` deadline; SIGTERM/SIGINT → `closeOnce()` → exit 2; `EXIT` codes; state write only on no-news. AC: 3.1 green. ~75L

## Phase 4: Integration

- [x] 4.1 RED gating test: `ai/teams-to-tasks/cron.sh` with stub poller (env-overridable): rc 0/1/2/124/137 → agent once, only on 0; PAUSE/lock → nothing. (threat: Subprocess exit routing). ~40L
- [x] 4.2 GREEN rewrite `ai/teams-to-tasks/cron.sh` (sweep only, digest untouched): capture PREV_LAST_RUN pre-poll; `timeout -k 15s 120s bun <dir>/poller.ts`; rc 0 → agent (PREV window); on agent success write poll-start T — drop blind `date > "$STATE_FILE"`; rc 1 → log; rc * → log+`notify`, no agent; PAUSE/flock unchanged. ~45L
- [x] 4.3 Create `ai/teams-to-tasks/src/teams-page.ts`: `TeamsSearchAdapter` on `playwright-core`, executablePath from newest `~/.cache/ms-playwright/chromium-*`, search `que`+Date=Today → raw stamps, idempotent `close`. No bun tests (by design); missing binary → exit 2. ~70L
- [x] 4.4 Update `scripts/install-teams-to-tasks.sh`: `bun install` + smoke check (chromium found, poller rc 0/1/2). Confirm `symlinks/conf.linux.yaml` (read-only) needs no entry. ~25L

## Phase 5: Docs & Verification

- [x] 5.1 Update `ai/teams-to-tasks/README.md`: usage, exit codes, deferred advancement, manual E2E. ~20L
- [x] 5.2 `cd ai/teams-to-tasks && bun test && bunx tsc --noEmit`; `bash -n` cron.sh; recount vs 400 budget.
