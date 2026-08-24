# Tasks: Antigravity SDD Explore Router

## Review Workload Forecast

```
Estimated changed lines: 1050–1250
400-line budget risk: High
Chained PRs recommended: Yes
Decision needed before apply: Yes
Delivery strategy: ask-on-risk
Suggested split: PR 1 backend+unit tests → PR 2 CLI/telemetry+integration → PR 3 binding/docs/smoke
Chain strategy: pending
```

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure `src/agy/` modules + unit tests | PR 1 | `bun test tests/agy-router.test.ts -t unit` | N/A — pure logic; no runtime boundary before CLI exists | Revert `src/agy/` + tests; native unchanged |
| 2 | CLI, receipt, metrics, report + integration tests | PR 2 | `bun test tests/agy-router.test.ts -t integration` | `AGY_BIN=tests/helpers/stub-agy.sh bun run src/agy/cli.ts --input req.json` | Revert CLI + telemetry + tests; router not yet bound |
| 3 | Template, prompt, launcher, install, docs + contract/smoke | PR 3 | `bun test tests/agy-router.test.ts -t contract` | `scripts/opencode-web.sh` Web restart; routed + force-native smoke on scratch change | `install --uninstall` + opt-out/force-native + Web restart |

## Phase 1: Backend Foundation (PR 1)

- [x] 1.1 RED: exit/log → Outcome mapping + `fallbackAllowed` (only quota_unavailable/transient_unavailable/timeout) in tests/agy-router.test.ts
- [x] 1.2 GREEN: `src/agy/outcomes.ts` — v1 req/res schemas + classifier
- [x] 1.3 RED: pool-by-requested-model, staleness, thresholds, hint-cache tests
- [x] 1.4 GREEN: `src/agy/quota.ts` — Gemini/3P pools, staleness/reset, hint cache
- [x] 1.5 RED (threat): repo mutation blocks as `artifact_validation_failure`, persists nothing
- [x] 1.6 GREEN: `src/agy/validate.ts` — section lint + porcelain-hash pre/post check
- [x] 1.7 RED: exactly-once receipt per store; `none` persists nothing
- [x] 1.8 GREEN: `src/agy/persist.ts` — store-aware writes, receipt.json
- [x] 1.9 GREEN: `src/agy/spawn.ts` — timeout, daily guard, workdir-only, run.log
- [x] 1.10 GREEN: `src/agy/backend.ts` — provider-neutral `runExploration()` seam for Phase D

## Phase 2: CLI & Telemetry (PR 2)

- [x] 2.1 RED integration: `tests/helpers/stub-agy.sh` via AGY_BIN (success, timeout, auth, empty artifact)
- [x] 2.2 GREEN: `src/agy/cli.ts` — preflight (kill switch, agy present), quota, run, validate, persist, result.json
- [x] 2.3 RED: metrics.jsonl schema + savings-report tests
- [x] 2.4 GREEN: `src/agy/metrics.ts` + `report.ts` — JSONL sink, rates, latency, cost delta

## Phase 3: Binding & Contract (PR 3)

- [x] 3.1 Create `config/opencode-router.template.json` — router/fallback agents, `subagent_depth: 2`, task perms (router: only fallback; fallback: deny)
- [x] 3.2 RED contract: parse template — depth 2, router allows only fallback, fallback denies task
- [x] 3.3 Create `config/prompts/sdd-explore-router.md` — ≤60 lines, cheap model, exactly one bash call
- [x] 3.4 Create `scripts/install-router-config.sh` — install with backup, bin shim, `--uninstall`
- [x] 3.5 Create `scripts/opencode-web.sh` — export `OPENCODE_CONFIG`, exec `opencode web`

## Phase 4: Docs, Smoke & Rollback (PR 3)

- [x] 4.1 Modify `.opencode/skills/antigravity-explore/SKILL.md` — routed usage, containment, kill switch
- [x] 4.2 Modify `antigravity-spike/run-antigravity-task.sh` — superseded-by-`src/agy/` note
- [x] 4.3 Smoke: install config, restart Web, run `task(sdd-explore)` — router success persists exactly once
- [x] 4.4 Smoke: force-native + opt-out + `--uninstall` rollback; depth cap, no nested `<task_result>`
- [x] 4.5 Verify `~/.config/ai-quotas/gemini.json` unchanged; metrics record classifications
