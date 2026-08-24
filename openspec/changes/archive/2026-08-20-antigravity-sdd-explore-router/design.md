# Design: Antigravity SDD Explore Router

## Technical Approach

A thin router agent replaces `sdd-explore` through a shared `OPENCODE_CONFIG` file. Judgment lives in deterministic Bun/TypeScript (`src/agy/`) behind one CLI; the router prompt builds input, runs one command, then persists or delegates to the unchanged native `sdd-explore-fallback`.

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|---|---|---|---|
| 1 | Shared binding | One file `~/.config/ai-stack/opencode-router.json` from a repo template; `scripts/opencode-web.sh` exports `OPENCODE_CONFIG` and execs `opencode web` | Editing Gentle AI `opencode.json`; per-project copies | Merge order global → override → project leaves Gentle AI untouched; one source for every Web directory |
| 2 | Opt-out / kill switch | Project `.opencode/opencode.json` redeclares `agent["sdd-explore"]` (project wins); force-native via `AI_STACK_SDD_EXPLORE=native` or `~/.config/ai-stack/force-native` | Prompt heuristics | Opt-out is config-layer; kill switch is preflight, no restart |
| 3 | Router scope | `opencode/deepseek-v4-flash-free`, ≤60-line prompt, exactly one bash call, no analysis | Reasoning model classifying failures | Minimizes overhead tokens; classification is code |
| 4 | Nesting | `subagent_depth: 2`; router `permission.task` `{"*":"deny","sdd-explore-fallback":"allow"}`; fallback `tools.task:false` + `{"*":"deny"}` | Depth 3; wildcard allow | Verified on 1.18.18; caps recursion at one hop |
| 5 | Language split | TS modules + one CLI; bash only as PATH shim `~/.config/ai-stack/bin/agy-explore` | Monolithic bash router | `bun test`-able; reusable by Phase D |
| 6 | Persistence | CLI owns filesystem writes and emits `receipt.json`; router does at most one `mem_save` when `engramRequired` | Router writing files; CLI calling Engram | One owner per store, exactly-once, assertable |
| 7 | Containment | Workdir `~/.cache/ai-stack/agy-explore/{change}/{run}` only; never `AGY_EXTRA_DIRS`; code context via read-only codegraph MCP; repo `git status --porcelain` hashed pre/post | `--add-dir <repo>` | `--dangerously-skip-permissions` makes any added dir writable; omitting the repo removes that exposure |
| 8 | Quota | Pool chosen by requested model; thresholds, staleness and reset logic in `quota.ts`; result cached to `~/.config/ai-stack/state/router-quota-hint.json` until `reset_time` | `active_model`; `/usage` polling | Passive snapshot only; hint prevents repeat burn |
| 9 | B→D seam | `src/agy/backend.ts` `runExploration()` is provider-neutral | Logic inside CLI | Phase D imports it; only prompt + delegation are throwaway |

## Data Flow

    orchestrator ─task(sdd-explore)→ ROUTER (cheap)
         │ bash: agy-explore --input req.json
         ▼
    CLI → preflight (kill switch, agy present) → quota → spawn agy (workdir only)
        → outcome map → validate → persist → metrics.jsonl → result.json
         ▼
    success ─→ router: mem_save if required → envelope
    quota | transient | timeout ─→ router: task(sdd-explore-fallback) → its envelope (router persists nothing)
    auth_captcha | task_failure | artifact_validation_failure ─→ BLOCK and surface

## File Changes

| File | Action | Description |
|---|---|---|
| `config/opencode-router.template.json` | Create | Override source: agents, depth, permissions |
| `config/prompts/sdd-explore-router.md` | Create | Router prompt |
| `scripts/install-router-config.sh` | Create | Install to home, backup, `--uninstall` |
| `scripts/opencode-web.sh` | Create | Exports `OPENCODE_CONFIG`, execs `opencode web` |
| `src/agy/outcomes.ts` | Create | v1 schemas, exit/log → outcome |
| `src/agy/quota.ts` | Create | Pools, thresholds, staleness, hint cache |
| `src/agy/spawn.ts` | Create | Timeout, daily guard, `run.log` |
| `src/agy/validate.ts` | Create | Section lint, repo-mutation check |
| `src/agy/persist.ts` | Create | Store-aware writes, receipt |
| `src/agy/metrics.ts`, `report.ts` | Create | JSONL sink, savings report |
| `src/agy/backend.ts`, `cli.ts` | Create | Neutral API, CLI entry |
| `tests/agy-router.test.ts` | Create | Contract, classification, persistence, containment |
| `.opencode/skills/antigravity-explore/SKILL.md` | Modify | Document routed usage |
| `antigravity-spike/run-antigravity-task.sh` | Modify | Note: superseded by `src/agy/` |

## Interfaces / Contracts

```ts
type Outcome = "success" | "quota_unavailable" | "transient_unavailable"
  | "auth_captcha" | "timeout" | "task_failure" | "artifact_validation_failure";

interface ExploreRequest { schema: "agy-explore/req@1"; change: string;
  store: "engram"|"openspec"|"hybrid"|"none"; repo: string; brief: string; model: string; }

interface ExploreResult { schema: "agy-explore/res@1"; outcome: Outcome; reason?: string;
  fallbackAllowed: boolean; artifactPath?: string; sha256?: string; elapsedMs: number;
  receipt: { store: string; wroteOpenspec: boolean; engramRequired: boolean }; }
```

`fallbackAllowed` is true only for `quota_unavailable`, `transient_unavailable`, `timeout`. A missing `agy` binary (VPS) maps to `transient_unavailable`, `reason:"agy_absent"`. `metrics.jsonl` records outcome, elapsed, model, router tokens and fallback use; `report.ts` derives success/fallback rate, latency and explore-cost delta versus native runs.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | Pool mapping, staleness, thresholds, exit→outcome, `fallbackAllowed` | Fixture snapshots, table tests |
| Unit | Canonical validator, receipt exactly-once per store | Golden artifacts |
| Integration | CLI with a faked `agy` (success, timeout, auth, empty artifact) | Stub binary via `AGY_BIN` |
| Contract | Override JSON: depth 2, router allows only fallback, fallback denies task | Parse installed template |
| E2E | Routed run and forced-fallback run | Manual smoke during apply |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Documentation-like paths | N/A: no file-type classification or execution | — | — |
| Git repository selection | Applicable: agy subprocess cwd/exposure | Workdir-only; repo never added; porcelain hash compared pre/post; mismatch → `artifact_validation_failure` (block) | Repo mutation blocks and persists nothing |
| Commit state | N/A: no staging or commits | — | — |
| Push state | N/A: no push or refs | — | — |
| PR commands | N/A: no PR automation | — | — |
| Nested task routing (added) | Applicable | Router may call only `sdd-explore-fallback`; fallback denies `task` | Wildcard deny and fallback denial asserted |

## Migration / Rollout

Quota path unchanged (`~/.config/ai-quotas/gemini.json`, `AI_QUOTAS_FILE` override); no migration. Install backs up any existing override; restart OpenCode Web afterwards, since per-directory config is cached. Rollback: force-native file, project opt-out, or `--uninstall` plus restart.

Review budget: over 400 lines expected. Split points — (1) `src/agy/` backend and unit tests, (2) CLI, receipt, metrics and integration tests, (3) template, launcher, install script, docs and smoke.

## Open Questions

- [ ] None blocking. VPS `agy` provisioning stays deferred; that path degrades to native fallback.
