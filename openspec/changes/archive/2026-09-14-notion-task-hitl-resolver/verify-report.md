```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:f445e22da6558b8e5289737558335b78435e8aa431e1f111225c43a402120b14
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 9/9
scenarios: 16/16
test_command: cd ai/teams-to-tasks && bun test
test_exit_code: 0
test_output_hash: sha256:c0f20ee46e17baba1829d8cb712dde5cb9326129d184a5364f98800fbeee065c
build_command: cd ai/teams-to-tasks && bunx tsc --noEmit
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

## Verification Report

**Change**: notion-task-hitl-resolver
**Version**: N/A (delta spec; no version header)
**Mode**: Standard (`strict_tdd: false` in `openspec/config.yaml`)

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 31 |
| Tasks complete | 31 |
| Tasks incomplete | 0 |

Every task 0.1–5.6 is checked complete in the cumulative apply-progress evidence (Engram observation #8732, topic `sdd/notion-task-hitl-resolver/apply-progress`). WU-0→WU-B all landed: commits `5bf189a`, `1ed71a6` (Slice 0), `1eb6c92` (A1), `e2ec2e5`/`c119683` (A2), `82e9887` (A3), `b29a715` (A4), `449edfc` (B). 4.6 (attended schema-migrate gate) was completed attended by the orchestrator with the human; no orchestrator-owned task remains.

### Build & Tests Execution

**Build (typecheck)**: ✅ Passed
```text
$ cd ai/teams-to-tasks && bunx tsc --noEmit
(no output) — exit 0
```

**Tests**: ✅ 237 passed / ❌ 0 failed / ⚠️ 0 skipped (17 files, 711 expect calls, ~77s)
```text
$ cd ai/teams-to-tasks && bun test
237 pass / 0 fail / 711 expect() calls / Ran 237 tests across 17 files. exit 0
```

The documented exactly-90d boundary flake in `tests/resolver-run-store.test.ts:212` did **not** reproduce on this run (full suite green). Isolated rerun is stable: `bun test tests/resolver-run-store.test.ts` → 14 pass / 0 fail / 41 expect in 22ms. The `error:`/`invalid last_run state` lines in the full log are intentional error-path fixtures in `tests/poller.test.ts` (they assert, not crash).

**Coverage**: ➖ Not required (config `verify.coverage_threshold: 0`; no coverage gate for this change).

Focused guards (all green):
- `bun test tests/resolver-allowlist.test.ts` → 3 pass / 0 fail (ingestion unchanged guard, R7/2.5)
- `env -u NOTION_TOKEN -u NOTION_CLECE bun src/resolver/schema-migrate.ts --dry-run` → exit 0, prints the offline 5-property PATCH plan, makes zero network calls

### Spec Compliance Matrix

| Requirement | Scenario | Covering test | Result |
|-------------|----------|---------------|--------|
| R1 Inbox Detection | Detected once per revision | `resolver-detector.test.ts` > "second unchanged run adds nothing" | ✅ COMPLIANT |
| R1 Inbox Detection | Forbidden source refused | `resolver-detector.test.ts` > "forbidden source configured…refused" / "response referencing a forbidden source is refused" | ✅ COMPLIANT |
| R2 Read-Only Contextualization | Secret file denied | `resolver-gate.test.ts` > "private-env.sh is denied…" + `resolver-run-store.test.ts` > "denied evidence throws and leaves zero bytes" | ✅ COMPLIANT |
| R3 Resolvability Classification | Sufficient evidence | SKILL.md Decision Gates + 5.6 walkthrough (attended LLM judgment — no deterministic test by design) | ⚠️ PARTIAL |
| R3 Resolvability Classification | Unresolved verification | SKILL.md Decision Gates + 5.6 walkthrough (item 3 → ACTIONABLE_RECOMMENDED) | ⚠️ PARTIAL |
| R3 Resolvability Classification | Unsafe or ambiguous | SKILL.md Decision Gates + 5.6 walkthrough (items 1–2 → MANUAL_REQUIRED) | ⚠️ PARTIAL |
| R4 Versioned Draft Artifacts | Complete hashed run | `resolver-run-store.test.ts` > "all five artifacts land under runs/<draft-id>" | ✅ COMPLIANT |
| R5 Human Approval Gate | Hash-bound approval | `resolver-execute.test.ts` > "hash mismatch…refuses" + `resolver-hash.test.ts` > "different credential_name or session_id ⇒ different hash" | ✅ COMPLIANT |
| R5 Human Approval Gate | Mirror never authorizes | `resolver-execute.test.ts` > "illegal transition refused: …mirror approval ignored" | ✅ COMPLIANT |
| R5 Human Approval Gate | Reject or revise | `resolver-fsm.test.ts` > "Pending approval → Rejected ends the run" + "drift from Pending approval voids" + `resolver-hash.test.ts` > "revision drift rehashes" | ✅ COMPLIANT |
| R6 Resolver State Machine | Illegal transition refused | `resolver-fsm.test.ts` > "Detected → Executing directly is refused" + `resolver-execute.test.ts` > "illegal transition refused" | ✅ COMPLIANT |
| R6 Resolver State Machine | Drift voids approval | `resolver-fsm.test.ts` > "drift from Approved voids" + `resolver-execute.test.ts` > "source-revision drift voids approval" | ✅ COMPLIANT |
| R6 Resolver State Machine | Receipt blocks replay | `resolver-execute.test.ts` > "identical action replay is refused by its receipt" | ✅ COMPLIANT |
| R7 Deterministic Post-Approval Executor | Validated action retried | `notion-writer.test.ts` > "retries on HTTP 429" + `resolver-execute.test.ts` > "429 is retried and the receipt is still recorded" | ✅ COMPLIANT |
| R8 Untrusted-Data Boundary | Injection attempt inert | `resolver-gate.test.ts` > "shell-injection string…neither trips the gate nor executes" + `resolver-run-store.test.ts` > "untrusted Notas text persists as JSON-quoted data" | ✅ COMPLIANT |
| R9 Metrics And Ceilings | Budget exhausted | `resolver-budgets.test.ts` > "task token ceiling exceeded" / "wall clock exceeded…retains nothing partial" / "session ceilings exhausted" | ✅ COMPLIANT |

**Compliance summary**: 16/16 scenarios verified. 13 have passing deterministic tests; 3 (R3 classification) are covered by the SKILL.md Decision Gates contract plus the 5.6 attended walkthrough (3 real items classified, each matching its R3 scenario) — the classification is by design an attended LLM judgment, not a deterministic function.

### Correctness (Static Evidence)

| Requirement | Status | Notes (file:line) |
|------------|--------|-------|
| R1 Inbox Detection | ✅ Implemented | `detector.ts:40` Tareas-only default; `:48-51` forbidden SIS IDs; `:85-89` assertAllowedSource throws; `:124-137` parseInboxPages fail-closed on forbidden source in raw text + wrong parent; `:261-262` seen.json dedupe by page/revision; `:203-225` single-instance lock; `:237` PAUSE; `:57,163-176` exit codes |
| R2 Read-Only Contextualization | ✅ Implemented | `roots.ts:40-59,103-126` parse projects.yaml → realpath-pinned approvedRoots, unknown Proyecto ⇒ `[]`; `evidence-gate.ts:69-75` deny `private-env` by name; `:80-93` deny segments (modules/caches/profiles); `read-cli.ts:79-98` reuses the exact gate + scoped reader |
| R3 Resolvability Classification | ✅ Implemented (contract) | `SKILL.md:27-32` Decision Gates (SOLVABLE_LOCAL / ACTIONABLE_RECOMMENDED / MANUAL_REQUIRED); `budgets.ts:71-96` checkExhaustion ⇒ MANUAL_REQUIRED; walked-through 5.6 |
| R4 Versioned Draft Artifacts | ✅ Implemented | `run-store.ts:102-161` persistRun writes run.json/draft.md/evidence.json/patch.diff/actions.json, run.json last; machine-local `~/.local/state/task-resolver`, never committed |
| R5 Human Approval Gate | ✅ Implemented | `draft-hash.ts:15-46` SHA-256 over canonical {page_id, revision, actions, destination, draft_id, credential_name, session_id}; `execute.ts:266-281` hash + session + credential-name binding; `:301-317` drift voids → Analyzing new hash; `notion-writer.ts:384-402` triage never touches Estado/Notas |
| R6 Resolver State Machine | ✅ Implemented | `fsm.ts:40-50` transition table; `:64-68` unlisted throws IllegalTransitionError; `execute.ts:285,311,336,368,376` uses transition() |
| R7 Deterministic Post-Approval Executor | ✅ Implemented | `notion-writer.ts:60-73` SetTriageAction; `:369-417` triageTask (≤2000 chars, selective property set); `:182-192` 429/5xx retry; `completion.ts:61` allowlist excludes `triage` (ingestion unchanged) |
| R8 Untrusted-Data Boundary | ✅ Implemented | `evidence-gate.ts:96-108,146-149` secret-value shapes denied, injection canary inert; `scoped-read.ts` no shell; `SKILL.md:20` untrusted text is data |
| R9 Metrics And Ceilings | ✅ Implemented | `budgets.ts:29-41` single DEFAULT_BUDGETS (6min/80k/$0.50/12reads/400KiB; 30min/$5); `run-store.ts:47,136` run.json metrics; zero unapproved mutations (audit below) |

### Zero-Unapproved-Mutations Audit (R9 golden rule)

Static classification of every `fetch`/`spawn`/`exec`/write primitive in `src/resolver/`:

| File | Primitive | Classification |
|------|-----------|----------------|
| `detector.ts:298` | `globalThis.fetch` | Read-only Notion **query** (POST `/data_sources/{id}/query`) — no write |
| `detector.ts:28,211,223` | `rmSync` | Local lock-file release only |
| `execute.ts:148` | `appendFileSync` | Local `receipts.jsonl` (machine-local) |
| `execute.ts:153` | `writeFileSync` | Local `run.json` state rewrite |
| `execute.ts:213` | `Bun.spawn(["wsl-notify-send", …])` | Outbound-only toast; fixed literal args (not data-driven), no inbound, no shell interpretation |
| `execute.ts:419` | `globalThis.fetch` | Injected: read-only drift GET + triage PATCH **via NotionWriter** (the sanctioned hash-bound writer) |
| `roots.ts:49,54` | `.exec()` | **RegExp.exec**, not shell — false positive |
| `run-store.ts:143-157,202,256-257` | `writeFileSync`/`renameSync`/`rmSync` | Local state-dir persistence/prune only |
| `schema-migrate.ts:211` | `globalThis.fetch` | Attended one-shot schema PATCH (additive properties, fail-closed on conflict, unreachable from cron/executor) |

Verdict: **no unapproved mutation surface.** `execute.ts` reads authority only from local `run.json`/`actions.json`; the drift re-fetch consumes only `last_edited_time` and ignores all mirror properties (`execute.ts:229-246`). The only network writes are the hash-bound `NotionWriter.triageTask` (from `execute.ts`) and the attended `schema-migrate.ts` (out of the resolver execution path). `roots.ts` `.exec()` is regex matching, not process execution.

### Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| D1 Runtime shape (cron detector + attended evaluator) | ✅ Yes | `detector.ts` + `SKILL.md` + `install-task-resolver.sh` |
| D2 Run storage (~/.local/state/task-resolver, 90d prune, receipts never pruned) | ✅ Yes | `run-store.ts:171,181-210` |
| D3 Budgets & thresholds (single DEFAULT_BUDGETS; exhaustion ⇒ Manual) | ✅ Yes | `budgets.ts` |
| D4 Schema migration (attended, additive, skip-existing, merge options) | ✅ Yes | `schema-migrate.ts:78-111` |
| D5 Executor extension (triage action; ingestion allowlist unchanged) | ✅ Yes | `notion-writer.ts` + `completion.ts:61` |
| D6 Approval binding (h12, drift re-fetch, receipts) | ✅ Yes | `draft-hash.ts` + `execute.ts` |
| Closed: notification = `wsl-notify-send` outbound-only | ⚠️ Deviation | Executor also fires a toast on **Failed** (design enumerated "Executed / Manual required") — see W-2 |
| Closed: credential binding = name + session, never values | ✅ Yes | `run-store.ts:59-60`; secret values never persist |

### Issues Found

**CRITICAL**: None

**WARNING**:
- **W-1 — exactly-90d boundary flake in `tests/resolver-run-store.test.ts:212` (follow-up, non-blocking).** Root cause: the `mk()` fixture anchors each run's `updated_at` to `Date.now()` captured at fixture-write time (`:198`), but `pruneTerminalRuns(stateDir, Date.now())` (`:211`) compares against a *later* `Date.now()`. The "edge-terminal" run (exactly `RUN_RETENTION_MS` old) therefore appears `90d + ε` old at prune time, where ε = (prune clock − mk clock). The production comparison `now - updated > retentionMs` is **correct** (strict `>` keeps a run exactly at the boundary); the defect is purely a test-clock skew: the test passes only when fixture-write and prune land in the same millisecond, and fails under full-suite load when ε ≥ 1ms. Fail-safe direction confirmed (spurious red, never green-over-bug). **Proposed minimal fix**: capture a single `const now = Date.now()` at the top of the test, thread it into `mk` as the fixture anchor (`record.updated_at = new Date(now - ageDays * DAY_MS).toISOString()`), and pass it to `pruneTerminalRuns(stateDir, now)` — a 1–2 line, test-only change. Did not reproduce this run; isolated rerun is stable.
- **W-2 — toast fires on `Failed` in addition to the design's "Executed / Manual required" (`execute.ts:371`).** Deviation from the tasks.md notification decision. The outbound-only contract is unchanged, the addition is conservative (a partial-receipt failure needs the same human ping), and no spec requirement is violated. Non-blocking; recommend either accepting the deviation explicitly or aligning the design/tasks wording.

**SUGGESTION**:
- **S-1 — R3 classification lacks a deterministic unit-test path.** The three R3 scenarios are satisfied by the SKILL.md Decision Gates plus the attended 5.6 walkthrough, not by machine-executable tests (by design — classification is an LLM judgment). If future iterations want R3 red/green coverage, extract the criteria into a small pure `classify.ts` (`evidence → SOLVABLE_LOCAL | ACTIONABLE_RECOMMENDED | MANUAL_REQUIRED`) and test it against the same fixtures. Not required for this change.

### Verdict

**PASS WITH WARNINGS** — all 9 requirements and 16 scenarios verified; full suite (237) and typecheck green; zero-unapproved-mutations audit clean. Two non-blocking warnings (test-only flake; conservative toast deviation) and one suggestion. Nothing blocks `sdd-archive`.
