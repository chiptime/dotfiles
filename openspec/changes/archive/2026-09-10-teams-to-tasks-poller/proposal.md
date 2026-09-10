# Proposal: Teams-to-Tasks Poller

## Intent

Replace about 55 costly weekly agent discovery runs with an LLM-free preflight. Most find no new Teams messages. The poller makes execution event-driven without changing task creation.

## Scope

### In Scope
- Add a read-only Node.js/Playwright poller using the shared Teams profile and proven search (`que`, `Date=Today`).
- Compare message timestamps with `~/.local/state/teams-to-tasks/last_run`; return deterministic news, no-news, and failure codes.
- Gate sweep execution in `cron.sh`; preserve `PAUSE`, `flock`, logging, notifications, and state semantics.
- Enforce a hard poller timeout and always close its browser before releasing the shared profile.
- Test decision/state logic with `bun test` behind an injectable page abstraction.
- Map repo-owned artifacts into `~/.config/opencode/scripts/`; commit no secrets or runtime state.

### Out of Scope
- The 18:30 digest flow or its scheduling.
- Changes to extraction, Notion writes, deduplication, or interactive Teams usage.
- Posting, reacting, or otherwise mutating Teams content.

## Capabilities

### New Capabilities
- `teams-to-tasks-polling`: Read-only discovery, timestamp/state decisions, deterministic outcomes, cleanup, and safe agent gating.

### Modified Capabilities
None.

## Approach

Build pure decision/state modules behind an injected Playwright adapter. The poller searches Teams, normalizes timestamps, compares `last_run`, always closes the browser, and exits deterministically. `cron.sh` holds the lock across poller→agent, applies an external timeout, and launches the agent only for news; failures do not advance state. Add package metadata and tests first.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `ai/teams-to-tasks/` | New/Modified | Poller, tests, package metadata, cron gate, docs |
| `scripts/install-teams-to-tasks.sh` | Modified | Dependencies and poller symlink installation |
| `symlinks/conf.linux.yaml` | Modified | Poller mapping |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Teams DOM changes | Med | Isolate browser adapter; fail closed without advancing state |
| Profile contention/leaked Chromium | Med | `flock`, sequential ownership, `finally` cleanup, hard timeout |
| Timestamp/locale errors | Med | Explicit normalization and boundary tests |
| Review budget exceeded | Med | Estimate 330–430 lines; ask before apply above 400 and split core from integration |

## Rollback Plan

Revert the cron gate and mappings; remove poller files. Preserve `last_run`, profile, `PAUSE`, and logs.

## Dependencies

- Node.js/Bun, Playwright, authenticated Teams profile, `timeout`, and `flock`.

## Success Criteria

- [ ] No-news sweeps exit without launching OpenCode; news launches exactly one agent run.
- [ ] Poller never mutates Teams, always closes Chromium, and cannot exceed its timeout.
- [ ] `bun test` covers timestamp boundaries, exit codes, invalid state, and failure-safe handling.
- [ ] Poller and agent never hold the browser profile concurrently; failures never advance `last_run`.
