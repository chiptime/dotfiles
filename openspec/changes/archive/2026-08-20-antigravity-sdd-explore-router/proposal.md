# Proposal: Antigravity SDD Explore Router

## Intent

Reduce SDD exploration cost by using Antigravity whenever available, without changing Gentle AI’s `task(subagent_type="sdd-explore")` contract or weakening canonical artifact guarantees.

## Goals

- Minimize cheap-router token overhead and avoid the native executor when Antigravity is available.
- Preserve exploration quality, artifact isolation, and a provider-neutral seam for a future custom-provider implementation.

## Scope

### In Scope
- A shared user-owned `OPENCODE_CONFIG` override for OpenCode Web that transparently binds `sdd-explore` to a cheap router; repository config provides per-project opt-out.
- Router-first `agy` execution with typed machine output, quota-pool/model selection, canonical-structure validation, and classified metrics.
- Native `sdd-explore-fallback` delegation only for exhausted quota, timeout, or provider outage; force-native kill switch.
- Executor-owned, exactly-once canonical explore persistence and envelope return.

### Out of Scope
- Changing Gentle AI’s task call, orchestrator persistence ownership, `/usage` polling, watcher/cron, or Gentle AI-managed global config.
- Silent fallback for contract, authentication, or captcha failures; these block and surface.
- Phase 2 custom AI SDK provider (approach D), VPS provisioning beyond stated dependencies, and exploration-time codebase modification.

## Capabilities

### New Capabilities
- `antigravity-sdd-explore-routing`: Route SDD exploration through Antigravity with safe native fallback and observability.

### Modified Capabilities
- None.

## Approach

Adopt Phase 1 approach B. The cheap router invokes an artifact-scoped `agy` backend first, consumes a versioned typed result, selects Gemini or 3P pools from the passive quota snapshot, validates canonical output, then persists and returns the canonical envelope. For approved unavailability only, it delegates unchanged native behavior to `sdd-explore-fallback`, which alone persists. Missing/stale quota permits one real Antigravity attempt whose result refreshes routing state. The shared override enables nested fallback (`subagent_depth: 2`; router task permission explicitly allows only fallback; fallback denies task). Keep reusable runner, quota, validator, fixtures, and telemetry provider-neutral for D.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| Shared user-owned OpenCode override | Modified | Router binding, permissions, kill switch, Web launch |
| `antigravity-spike/run-antigravity-task.sh` | Modified | Basis for typed backend contract |
| `.opencode/skills/antigravity-explore/SKILL.md` | Modified | Document routed exploration constraints |
| `tests/*.test.ts` | New | Contract, classification, persistence-boundary tests |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Misclassified failure causes improper fallback | Med | Typed taxonomy; block auth/captcha/contract failures |
| Router savings do not justify complexity | Med | Measure token overhead, success/fallback rate, latency, classifications |
| agy containment or stale quotas | Med | Artifact-only filesystem access; one real attempt on stale/missing snapshot |

## Rollback Plan

Set force-native or project opt-out, restart OpenCode Web to reload the override, and retain `sdd-explore-fallback` as the unchanged native executor.

## Dependencies

- Installed OpenCode 1.18.18 nested-task support and restarted Web processes after override changes.
- `agy`, valid credentials, and compatible passive `~/.config/ai-quotas/gemini.json` (semantic migration may remain backward-compatible).

## Success Criteria

- [ ] Existing Gentle AI calls to `task(subagent_type="sdd-explore")` remain unchanged.
- [ ] Antigravity succeeds without invoking native fallback when available; exactly one path persists the canonical artifact.
- [ ] Only approved unavailability triggers fallback; auth, captcha, and contract failures block visibly.
- [ ] Phase 1 records router token overhead, agy success/fallback rates, latency, and failure classifications to evaluate approach D.
