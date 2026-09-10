```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:8f54f27486619a2922f03f8fc981803bff78aac08e5287b93f246a872555a12a
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 8/8
scenarios: 14/14
test_command: cd ai/teams-to-tasks && bun test
test_exit_code: 0
test_output_hash: sha256:46e718586858e7bdd37362846b19fce96dddc85fb09792baea314239c74cd6f8
build_command: cd ai/teams-to-tasks && bunx tsc --noEmit
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

## Verification Report

**Change**: teams-to-tasks-poller
**Version**: N/A (delta spec, no version field)
**Mode**: Standard (strict_tdd: false)

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 12 |
| Tasks complete | 11 |
| Tasks incomplete | 1 (5.2 — the verification task, satisfied by this run) |

Task 5.2 is the verification step itself (`bun test && bunx tsc --noEmit`; `bash -n cron.sh`; budget recount). It was intentionally left `[ ]` for this phase; this run completes it.

### Build & Tests Execution

**Build (type-check)**: ✅ Passed
```text
$ cd ai/teams-to-tasks && bunx tsc --noEmit
(no output — clean)  exit 0
```

**Tests**: ✅ 37 passed / ❌ 0 failed / ⚠️ 0 skipped (108 expect() calls)
```text
$ cd ai/teams-to-tasks && bun test
37 pass / 0 fail / 108 expect() calls  (exit 0)
```

**Syntax gates**: ✅ `bash -n ai/teams-to-tasks/cron.sh` OK · ✅ `bash -n scripts/install-teams-to-tasks.sh` OK

**Coverage**: ➖ Not available (no `--coverage` configured for this project; threshold 0)

### Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| News detection | Newer message is news | `tests/core.test.ts > decide > "newer message is news"` | ✅ COMPLIANT |
| News detection | Equal or older is no-news | `tests/core.test.ts > decide > "equal watermark is no-news (strictly newer required)"`, `"older messages are no-news"` | ✅ COMPLIANT |
| News detection | Missing state bootstraps as news | `tests/core.test.ts > decide > "missing state bootstraps as news when messages exist"` | ✅ COMPLIANT |
| State advancement | No-news advances at poll | `tests/poller.test.ts > "no-news exits 1 and advances state to poll start T"` | ✅ COMPLIANT |
| State advancement | News defers advancement to agent success | `tests/poller.test.ts > "news exits 0 and writes nothing"`; `tests/cron.test.ts > "poller rc 0 → agent … state advances to poll-start T after agent success"`, `"poller rc 0 but agent fails → state stays byte-identical"` | ✅ COMPLIANT |
| State advancement | Failure preserves state | `tests/poller.test.ts > "adapter throws … no state file"`, `"invalid state fails closed … byte-identical"`, `"unrecognized timestamp … state untouched"` | ✅ COMPLIANT |
| Deterministic exit codes | Failure code is uniform | `tests/poller.test.ts` failure-path suite (throw / hang / invalid state / DOM change) all assert `EXIT.FAILURE` (2) | ✅ COMPLIANT |
| Browser always closed | Close on error | `tests/poller.test.ts > "adapter throws … close once"`, `"hung search hits the deadline … close once"` | ✅ COMPLIANT |
| Hard timeout | Hung search is terminated | `tests/poller.test.ts > "hung search hits the deadline: exit 2, close once"` | ✅ COMPLIANT |
| PAUSE and flock preserved | PAUSE blocks everything | `tests/cron.test.ts > "PAUSE file present → neither poller nor agent runs"` | ✅ COMPLIANT |
| PAUSE and flock preserved | Lock contention skips run | `tests/cron.test.ts > "lock held elsewhere → neither poller nor agent runs"` | ✅ COMPLIANT |
| Agent gating | News launches one agent | `tests/cron.test.ts > "poller rc 0 → agent exactly once with the PREV window"` | ✅ COMPLIANT |
| Agent gating | No-news and failure launch nothing | `tests/cron.test.ts > "poller rc 1 → no agent …"`, `"poller rc 2 / 124 / 137 → no agent …"` | ✅ COMPLIANT |
| Read-only guarantee | No mutation calls | `tests/readonly.test.ts > "adapter issues only read-only Teams actions"` | ✅ COMPLIANT |

**Compliance summary**: 14/14 scenarios compliant.

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| News detection | ✅ Implemented | `decide()` uses `newest > lastRun` (strictly newer); empty results → no-news; null watermark → news |
| State advancement | ✅ Implemented | `runPoll` writes `formatState(pollStart)` only on no-news; news writes nothing; cron writes `POLL_START_T` only after agent success; failures never write |
| Deterministic exit codes | ✅ Implemented | `EXIT = {NEWS:0, NO_NEWS:1, FAILURE:2}`; every error path (throw, invalid state, unrecognized timestamp, deadline, signals) → 2 with `console.error` diagnostic |
| Browser always closed | ✅ Implemented | `try/finally` → `closeOnce`; `createSignalExit` closes once then exits 2 on SIGTERM/SIGINT |
| Hard timeout | ✅ Implemented | in-process `withDeadline` (`Promise.race`, timer cleared) + cron `timeout -k 15s 120s` backstop |
| PAUSE and flock preserved | ✅ Implemented | `PAUSE` check and `flock -n 9` unchanged; lock spans poller→agent |
| Agent gating | ✅ Implemented | `case 0` fall-through → agent; `1` → log+exit; `*` → log+notify, no agent |
| Read-only guarantee | ✅ Implemented | `TeamsSearchAdapter` surface is `search()`/`close()` only; `teams-page.ts` issues navigate/search/filter/read interactions only (no post/react/reply/request) |

### Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| Timeout owner: in-process deadline + `timeout` backstop | ✅ Yes | `withDeadline` + `timeout -k 15s 120s bun …` |
| Adapter returns raw timestamp strings | ✅ Yes | `search(): Promise<{ rawTimestamp: string }[]>` |
| Layout mirrors `ai/opencode-router` (src/ + tests/, bun:test, strict tsconfig, local package.json, committed bun.lock) | ✅ Yes | all present; `node_modules/` gitignored |
| Poller resolved via `readlink -f` dirname (no second symlink) | ✅ Yes | `SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"`; no `conf.linux.yaml` entry needed |
| cron gating 0→agent / 1→log / else→notify | ✅ Yes | `case` matches exactly; `exec 9>` spans poller→agent |
| `playwright-core` + `executablePath` from newest `chromium-*` | ✅ Yes | `resolveChromiumExecutablePath`; pinned `playwright-core@1.63.0-alpha-2026-08-31` |
| `try/finally` + idempotent `closeOnce`; signals close then exit 2 | ✅ Yes | implemented and tested |
| State ISO-8601 UTC + legacy local parse | ✅ Yes | `formatState`/`parseState`; legacy `YYYY-MM-DD HH:MM` accepted |

### Issues Found

**CRITICAL**: None. (One gap was found and remediated during verification — see below.)

**WARNING**:
1. **Review-budget overrun** — 477 authored changed lines vs the 400-line PR budget / 250-line sdd-attempt cap (reported by apply). Root cause: `cron.sh`/`README`/installer were pre-existing untracked files whose full content counted as new lines. Already surfaced in apply-progress; orchestrator decision (size:exception vs split) remains open.
2. **Teams DOM selectors unverified against a live session** — `SEARCH_BOX`/`TIMESTAMP_NODE`/date-filter roles are locale-tolerant guesses, proven only by the manual E2E gate (no credentialed CI). Mitigated by fail-closed: unrecognized timestamp or missing pane → exit 2 → notify. Real risk remains until a manual E2E run.

**SUGGESTION**:
1. `design.md` data-flow diagram line "poller writes last_run (rc 0|1)" is stale — actual behavior is poller writes on rc 1 only, cron writes on rc 0 after agent success (the State decision row and amended spec describe this correctly).
2. `chromium-1208` vs `playwright-core@1.63.0-alpha` runtime mismatch is untested; the installer smoke only catches a *missing* binary, not a binary/protocol mismatch.

**Verification remediation note**: The Read-only guarantee scenario had no runtime covering test (adapter `teams-page.ts` was untested by design). Within the ≤150-line remediation budget I added `tests/readonly.test.ts`, which drives the real `TeamsSearchPage` against a recording `playwright-core` fake and asserts the complete interaction sequence is read-only (navigate → fill "que" → press Enter → date/today filter → read timestamps; no composer/send/reply/react/comment/request). Suite re-run: 37 pass / 0 fail. No production source was modified.

### Verdict

**PASS WITH WARNINGS** — All 8 requirements and 14 scenarios are runtime-verified, tests and type-check are green, cron/installer syntax clean, no secret leakage, and design is coherent. Residual warnings are the review-budget overrun and the unverified-live-Teams-selector risk (both mitigated, non-blocking).
