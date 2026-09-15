# Archive Report: notion-task-hitl-resolver

**Archived**: 2026-09-15 (change folder prefixed `2026-09-14`, the date the final work-unit landed, per orchestrator instruction)
**Verdict at close**: COMPLETE — planned, implemented, independently verified (pass_with_warnings, 0 critical), and closed.
**Spec of record**: `openspec/specs/notion-task-hitl-resolver/spec.md` (new capability domain, synced byte-identical from the change delta)

## Final State at Close

| Fact | State | Authority |
|------|-------|-----------|
| Implementation | All 6 work units landed (WU-0, A1, A2, A3, A4, B) | commits `5bf189a`, `1ed71a6`, `1eb6c92`, `e2ec2e5`/`c119683`, `82e9887`, `b29a715`, `449edfc` |
| Tasks | 31/31 complete, 0 pending | apply-progress Engram obs #8732; verify-report #8773; tasks.md checkboxes reconciled at archive (see below) |
| Verification | PASS WITH WARNINGS — 9/9 requirements, 16/16 scenarios, `critical_findings: 0`, `blockers: 0` | verify-report (this folder) + Engram obs #8773 |
| Test suite | 237 pass / 0 fail, 17 files, fully deterministic; `bunx tsc --noEmit` exit 0 | final state after post-verify W-1 fix |
| W-1 (90d boundary flake) | **FIXED post-verify** — commit `d84a0c5` `test(resolver): share one clock between fixture and prune call`, touches only `ai/teams-to-tasks/tests/resolver-run-store.test.ts` (8+/7−); maintainer-approved remediation, attempt settled complete | orchestrator handoff (outranks verify-time snapshot); focused suite 14/0 |
| W-2 (toast also on Failed) | Accepted deviation, non-blocking — conservative addition; outbound-only contract unchanged; no spec requirement violated | verify-report W-2 + handoff acceptance |
| S-1 (R3 has no deterministic regression net) | Accepted follow-up, by design — classification is an attended LLM judgment; optional future `classify.ts` extraction | verify-report S-1 |
| Notion schema | LIVE — 5 properties additively migrated on Tareas `3d675532-da31-802b-b12f-000be53e99ac` (Triage Status ×6 options, Approval State ×3, Resolution Draft, Local Context Ref, Draft ID); attended gate 4.6, human-approved via question tool | apply-progress #8732 (4.6 entry) + handoff |
| Skill | Mapped + indexed: `~/.config/opencode/skills/task-resolver` → repo symlink; `.atl/skill-registry.md` regenerated (24 skills, canonical generator) | apply-progress #8732 (4.2/4.5) + handoff |
| 5.6 walkthrough | 3 real Inbox items classified live read-only, zero mutations: 2 × MANUAL_REQUIRED, 1 × ACTIONABLE_RECOMMENDED; each maps to its R3 scenario | apply-progress #8732 (5.6 entry) |

## Requirement Coverage Closure (R1–R9)

| Req | Requirement | Closed by |
|-----|-------------|-----------|
| R1 | Inbox Detection | `detector.ts` + `resolver-detector.test.ts` (dedupe, forbidden-source refusal) — COMPLIANT |
| R2 | Read-Only Contextualization | `roots.ts`, `evidence-gate.ts`, `read-cli.ts` + gate/run-store tests (private-env denied, realpath-pinned) — COMPLIANT |
| R3 | Resolvability Classification | SKILL.md Decision Gates + attended 5.6 walkthrough — verified per contract (PARTIAL deterministic by design) |
| R4 | Versioned Draft Artifacts | `run-store.ts` (5 hashed artifacts, machine-local, never committed) — COMPLIANT |
| R5 | Human Approval Gate | `draft-hash.ts` + `execute.ts` (hash/session/credential binding, mirror never authorizes, reject/revise rehash) — COMPLIANT |
| R6 | Resolver State Machine | `fsm.ts` + execute integration (illegal transitions, drift void, receipt replay block) — COMPLIANT |
| R7 | Deterministic Post-Approval Executor | `notion-writer.ts` triage action + 429/5xx retry + receipts; ingestion allowlist guard stayed green — COMPLIANT |
| R8 | Untrusted-Data Boundary | evidence-gate deny-lists, injection canaries inert, no shell/ambient sessions — COMPLIANT |
| R9 | Metrics And Ceilings | single `DEFAULT_BUDGETS`, exhaustion ⇒ MANUAL_REQUIRED, run.json metrics; zero-unapproved-mutations audit clean — COMPLIANT |

13/16 scenarios have passing deterministic tests; the 3 R3 scenarios are closed by the SKILL.md contract plus the attended walkthrough (by-design LLM judgment, accepted follow-up S-1).

## Decisions Log

- **Product decisions closed in tasks phase**: notification = `wsl-notify-send` desktop toast, OUTBOUND-ONLY, fire-and-forget (R5); credential binding = NAME + session_id recorded in `run.json`, secret values never persist, credential name is part of the hash canonical input (R5).
- **Chain strategy**: stacked-to-main, human-decided 2026-09-14 via question tool.
- **Size exceptions**: PR A1 explicit (779 actual vs ~310 est); blanket size:exception for A2 (969), A3, A4, B accepted by maintainer 2026-09-14 — forecasts ran ~3x short due to RED-first test weight; honest per-PR line reporting required at delivery.
- **Resets**: none — no remediation cycles, no re-verify. One post-verify maintainer-approved test-only remediation (W-1 fix, `d84a0c5`).
- **Delivery strategy**: `ask-on-risk`; delivery decision is user-owned and NOT taken in this archive.

## Archive-Time Stale-Checkbox Reconciliation (recorded per contract)

`tasks.md` was archived with all 31 checkboxes marked `[x]`. At archive start, every checkbox was `[ ]` — this was a **known, documented sdd-apply repo policy artifact**, not incomplete work: apply-progress obs #8732 states "tasks.md checkboxes NOT edited (openspec/** hard exclusion; orchestrator owns planning artifacts; this observation is the progress source of truth)". Reconciliation was performed by sdd-archive because:

1. Apply-progress #8732 proves every task 0.1–5.6 complete with per-task evidence, and verify-report (#8773 + this folder's verify-report.md) independently confirms 31/31, 0 incomplete.
2. The orchestrator's launch declared the change completed and ordered closure, citing exactly those two observations as the authoritative trail.
3. The refreshed native dispatcher confirmed post-reconciliation: `taskProgress 31/31 allComplete: true`, `dependencies.archive: ready`, `nextRecommended: archive`, `blockedReasons: []`.

Pre-reconciliation dispatcher read `0/31 / archive: blocked / nextRecommended: apply` — stale checkbox parse, superseded by the above. No other archive gate was affected.

## Contradiction Register

- **verify-report snapshot vs final state**: verify-report (obs #8773, commit `d8c9cee`) recorded W-1 as an open non-blocking warning and a 237-pass run where the flake "did not reproduce". Final state: W-1 fixed in `d84a0c5` (test-only), suite 237/0 deterministic. Final numbers carried from the handoff (higher-ranked source); the snapshot remains valid history of verify time.
- **Date prefix**: folder uses `2026-09-14` (final work-unit date) per explicit orchestrator instruction; archive executed 2026-09-15.
- No unrankable contradictions were found.

## Follow-Ups (non-blocking, carried forward)

1. **W-2**: align design/tasks wording with the toast-also-on-Failed behavior, or leave the accepted deviation as-is (acceptance recorded above).
2. **S-1**: optional pure `classify.ts` extraction to give R3 a deterministic regression net.
3. **Attended steps pending for the user**: (a) crontab install via `scripts/install-task-resolver.sh` — script landed, NOT installed; (b) first live attended resolver session.

## Delivery State

NOT delivered: no push, no branches, no PRs. Commits sit linearly on local `master`: `5bf189a`, `1ed71a6`, `e83bb8d` (+ prior-effort docs commits), `1eb6c92`, `e2ec2e5`, `82e9887`, `b29a715`, `449edfc`, `d8c9cee`, `d84a0c5` (W-1 fix). Blanket size:exception on record for PRs A1/A2/A3/B. The stacked-to-main PR chain is pending the user's delivery decision.

## Engram Trail (observation IDs read by this phase)

- `#8732` — `sdd/notion-task-hitl-resolver/apply-progress` (cumulative, 6 revisions; all work units + 4.6 + 5.6)
- `#8773` — `sdd/notion-task-hitl-resolver/verify-report` (pass_with_warnings, validator-admitted)
- This archive report is also persisted as Engram topic `sdd/notion-task-hitl-resolver/archive-report`.

## Mechanical Integrity

- Delta spec → `openspec/specs/notion-task-hitl-resolver/spec.md`: shell `cp` via mktemp, `diff -r` readback **empty**.
- Change folder → `openspec/changes/archive/2026-09-14-notion-task-hitl-resolver/`: `git mv` after pre-move recursive snapshot, `diff -r` readback **empty**.
- This report is additive-only (created post-move; excluded from readback comparisons by contract).
