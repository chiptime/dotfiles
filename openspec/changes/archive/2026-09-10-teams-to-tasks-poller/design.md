# Design: Teams-to-Tasks Poller

## Technical Approach

Pure core (`src/core.ts`) + thin Playwright adapter (`src/teams-page.ts`) behind one interface, driven by `poller.ts`, which owns exit codes, deadline, signals, and state. `cron.sh` becomes a gate: PAUSE + `flock` unchanged, poller first, agent only on `0`. Isolating browser I/O lets `bun test` cover every requirement without Chromium.

## Architecture Decisions

| Decision | Choice | Why not the alternative |
|---|---|---|
| Timeout owner | In-process `Promise.race` deadline + `timeout -k 15s 120s` backstop | `timeout` alone yields `124`, cannot close the browser, is untestable in bun; in-process alone dies with a wedged loop |
| Adapter surface | `search()`/`close()` returning **raw timestamp strings** | Returning `Date`s hides locale/relative parsing (`"10:42"`, `"ayer 09:15"`) in the untestable half |
| Layout | `src/` + `tests/`, `bun:test`, strict `tsconfig.json`, local `package.json`, committed `bun.lock` | Mirrors `ai/opencode-router`, the only in-repo TS precedent |
| Poller location | `cron.sh` resolves `$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/poller.ts` | A second symlink splits the module from its `src/` and `node_modules/`; `conf.linux.yaml` needs **no** entry |
| cron gating | `0`→agent, `1`→log, **anything else**→notify, no agent | An explicit `2` case leaves `124`/`137` unhandled. `exec 9>` already spans the script, so the two never share the profile |
| Playwright dep | `playwright-core` + `executablePath` from the newest `~/.cache/ms-playwright/chromium-*` | `playwright` postinstall costs ~170 MB, `bunx` adds network at 08:00, CDP-attach needs the MCP up. Missing binary → exit `2`, smoke-checked at install |
| Browser lifecycle | `try/finally` + idempotent `closeOnce()`; `SIGTERM`/`SIGINT` close then exit `2` | Ad-hoc closes miss a path; closing the persistent context releases the profile |
| State | Write ISO-8601 UTC, also parse legacy local `YYYY-MM-DD HH:MM`, else exit `2` | Forces `cron.sh` to **drop** its post-agent `date > "$STATE_FILE"` and read `PREV_LAST_RUN` **before** polling |

## Data Flow

    cron → PAUSE? → flock(9) → PREV_LAST_RUN
                                  │
        timeout 120s ── bun poller.ts ──→ adapter.search() → raw stamps
                                  │                              │
                                  │          core.normalize/decide ◀┘
                                  ▼
        rc=0 ──→ opencode agent (PREV window, same lock)
        rc=1 ──→ log
        rc=* ──→ log + notify, no agent
                                  │
                  poller writes last_run (rc 0|1)

## File Changes

| File | Action | Description |
|---|---|---|
| `ai/teams-to-tasks/poller.ts` | Create | Deadline, signals, codes, state |
| `ai/teams-to-tasks/src/{core,teams-page}.ts` | Create | Pure logic; adapter |
| `ai/teams-to-tasks/tests/{core,poller}.test.ts` | Create | Units + fake adapter |
| `ai/teams-to-tasks/{package.json,tsconfig.json,bun.lock}` | Create | Pinned `playwright-core` |
| `ai/teams-to-tasks/cron.sh` | Modify | Gate, `PREV_LAST_RUN`, drop state write |
| `ai/teams-to-tasks/README.md`, `.gitignore` | Modify | Docs; ignore `node_modules/` |
| `scripts/install-teams-to-tasks.sh` | Modify | `bun install` + smoke check |

## Interfaces / Contracts

```ts
export interface TeamsSearchAdapter {
  search(): Promise<{ rawTimestamp: string }[]>;
  close(): Promise<void>;                                     // idempotent
}
export const EXIT = { NEWS: 0, NO_NEWS: 1, FAILURE: 2 } as const;
export function parseState(text: string | null): Date | null; // null = bootstrap
export function normalizeTimestamp(raw: string, now: Date): Date | null;
export function decide(lastRun: Date | null, stamps: Date[]): 'news' | 'no-news';
```

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Newer/equal/older boundary, relative stamps, bootstrap, bad state | Pure fns, injected `now` |
| Unit | Exit codes; state advanced on `0`/`1` only, unchanged on failure | Fake adapter, temp `HOME` |
| Integration | Hung/throwing adapters → exit `2`, close once; `cron.sh` gating rc `0/1/2/124` | Injected deadline; stub poller |
| E2E | Real Teams search | Manual — no credentialed CI |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Doc-like paths · git selection · commit · push · PR commands | N/A — no file classification, VCS, or PR operation | — | — |
| Subprocess exit routing | **Applicable** — cron routes on poller status | Only `0`/`1` classify; all else fails closed | rc `0,1,2,124,137` → agent only for `0` |
| Signal/timeout termination | **Applicable** — `timeout` sends SIGTERM | Closes browser, exits `2`; `-k` SIGKILL last | SIGTERM mid-poll → close once, exit `2` |
| Shared profile lock | **Applicable** — MCP and poller share `--user-data-dir` | Locked profile → launch throws | Launch throw → exit `2`, `last_run` unchanged |

## Migration / Rollout

No data migration; `last_run` is absent here, so the first run bootstraps as news. Rollback: revert `cron.sh` and delete the poller files; state, profile, PAUSE, and logs untouched.

## Open Questions

- [ ] **State advancement on news.** Advancing `last_run` on any successful poll means news plus a failing agent silently drops that hour. Notion fingerprint dedupe makes re-processing safe, so deferring advancement to agent success is safer. Implementing the spec literally; confirm before apply.
- [ ] `120s` is an estimate — confirm against a cold-profile run.
