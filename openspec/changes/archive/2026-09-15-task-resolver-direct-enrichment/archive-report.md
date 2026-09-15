# Archive Report: task-resolver-direct-enrichment

**Archived**: 2026-09-15
**Change**: `task-resolver-direct-enrichment` — v2 direct informational enrichment for the Notion task resolver
**Verdict carried in**: verify-report PASS WITH WARNINGS (0 CRITICAL / 2 WARNING / 3 SUGGESTION)
**Status at close**: COMPLETE — 8/8 tasks done, specs synced, slice landed. Delivery (push/PR) is user-owned and NOT done.

## Final State

This report describes the change AT CLOSE. It supersedes any "pending/blocked" claims in intermediate snapshots (`apply-progress`, `verify-report`) written before later work landed.

| Fact | Final state | Source rank |
|------|-------------|-------------|
| Tasks | 8/8 complete (checkboxes reconciled at archive — see below) | Persisted tasks artifact |
| Implementation slice | `66b3531` "feat(resolver): direct informational enrichment replaces approval flow" — 496 changed lines (455+/41−), maintainer-accepted size:exception | Launch handoff (final-state facts) |
| Planning artifacts | Committed `244b772` | Launch handoff |
| Verify report | Committed `cdda890`; `bun test` 249 pass / 0 fail, `bunx tsc --noEmit` exit 0; validator `valid:true` (Engram obs #8832) | Persisted verify-report |
| v1 executor/detector | Dormant, not deleted — `DEPRECATED (v1, dormant)` docblocks in `execute.ts` + `detector.ts` | Slice commit + handoff |
| Cron | NOT installed and NOT wanted (v2 is on-demand) | Launch handoff |
| TEST task | Trashed (not part of the delivered work) | Launch handoff |
| Delivery | NOT pushed, no branches, no PRs (user-owned) | Launch handoff |

Per-task completion evidence: apply-progress obs #8821 records all 8 tasks `[x]` with attempt-2 commit gates (full suite 249 pass, tsc exit 0, secret-scan clean, staged-paths audit via `git show --stat`); verify-report independently confirms 8/8 substantive with all 6 delta paths landed in `66b3531`.

### Intermediate-snapshot claims superseded at close

- verify-report WARNING 2 ("tasks.md checkboxes untoggled") — closed by sanctioned archive-time reconciliation (below); the artifact now shows 8/8.
- verify-report SUGGESTION 1 ("post-archive link-rot in SKILL.md References") — resolved by the sanctioned archive link fix (below); the reference now points at the synced capability spec.
- Remaining accepted follow-ups are listed under Follow-ups; none are CRITICAL.

## Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| v2 direction | Supersede the v1 approval-gate flow with direct informational enrichment: research fills the user's own Inbox task (Triage Status hint, Resolution Draft, cited sources); no approval/hash/receipt, no cron | Live validation + user feedback showed the approval/hash/receipt ceremony and cron were overbuilt; enrichment is the deliverable (proposal) |
| Skip predicate | `Draft ID` OR `Resolution Draft` non-empty ⇒ skip (corrected from the briefed AND); `Local Context Ref` excluded | Under AND the condition is unsatisfiable (v2 never writes `Draft ID`) and every rerun would re-enrich, breaking `Scenario: Rerun skips enriched`; OR also protects legacy v1-enriched tasks. `Local Context Ref` is legitimately empty on ✋ Manual tasks |
| Review budget | 496 lines landed as size:exception | Exceeded the 400-line budget (forecast: Medium); ask-on-risk escalated; maintainer accepted at delivery level (attempt-2 cap 700) |
| Entry point | Thin CLI `src/resolver/enrich.ts` (`runEnrich(deps, io)` + `main(argv)` + `import.meta.main`, injected `fetchFn`/`token`) | Rejected in-session `bun -e`: untestable, unauditable, drifts the moment a second runtime appears. Matches the house pattern (`read-cli.ts`, `schema-migrate.ts`, `execute.ts`); retries stay inside `NotionWriter.request()` |
| Write shape | Single atomic triage PATCH of exactly 3 informational fields; `Estado`/`Notas` never in the body | `notion-writer.ts` unchanged — `triageTask` already accumulates all fields into one PATCH with 429/5xx retries |
| Spec composition | Native `sdd-archive-compose` (exit 0): RENAMED applied before MODIFIED (rename visible to same-change MODIFIED), then REMOVED (FSM, Post-Approval Executor), then ADDED (Enrichment Idempotency, On-Demand Trigger). Unrelated requirements preserved byte-for-byte | Mechanical composition mandate; no model-driven merge |
| Historical annotations | Post-compose notes added to Purpose, Inbox Detection, Versioned Draft Artifacts, Metrics And Ceilings (sections describing dormant v1 code); requirement bodies untouched. `Resolvability Classification` left unannotated (untouched by the delta, not tied to dormant code) | Orchestrator handoff: resulting spec documents v2 semantics and marks v1-dormant sections as historical |

## Sanctioned Archive-Time Actions

1. **tasks.md checkbox reconciliation.** Apply never edits `openspec/**` checkboxes (repo policy), so `tasks.md` still showed `- [ ]` on all 8 tasks even though the work was landed and green. The orchestrator explicitly authorized archive-time reconciliation backed by proof: apply-progress obs #8821 records all 8 tasks complete with commit evidence (`66b3531`), and the persisted verify-report confirms 8/8 substantive, 0 CRITICAL, green gates. All 8 checkboxes were toggled mechanically; the dispatcher was then re-queried and reported `taskProgress 8/8`, `dependencies.archive: ready`, `nextRecommended: archive` before any spec sync or move.
2. **SKILL.md reference re-point (verify SUGGESTION 1).** The References section cited the change-scoped path `openspec/changes/task-resolver-direct-enrichment/specs/notion-task-hitl-resolver/spec.md`, which stopped existing at archive. Re-pointed to the established capability spec `openspec/specs/notion-task-hitl-resolver/spec.md`.

## Archive Verification Evidence

- Composition: `gentle-ai sdd-archive-compose --canonical openspec/specs/notion-task-hitl-resolver/spec.md --delta openspec/changes/task-resolver-direct-enrichment/specs/notion-task-hitl-resolver/spec.md --output .../spec.md.compose-tmp` → exit 0; intermediate moved atomically over the canonical spec.
- Move: pre-move recursive snapshot + collision guard + `git mv` → source absent → `diff -r` snapshot vs. `openspec/changes/archive/2026-09-15-task-resolver-direct-enrichment/` → **no differences** (byte-identical archive; this report is additive-only, written after the readback).

## Follow-ups (non-blocking, accepted)

| ID | Item | Origin |
|----|------|--------|
| W-1 | `No-evidence task` scenario is PARTIAL: no fixture exercises the literal `✋ Manual` status end-to-end (mechanics covered via `ENRICH_HINTS` allowlist + status-guard + verbatim-draft tests) | verify-report WARNING 1 |
| W-2 | SKILL.md v2 went live for future sessions (dotfiles symlink) before archive closed — no gate exists between the slice commit and the live skill | verify-report SUGGESTION 2 |
| S-3 | CLI diagnostic ordering: with no token set, `enrich.ts <bad-uuid>` reports `missing token` before the UUID preflight. Fail-closed either way; reordering would surface the UUID refusal earlier | verify-report SUGGESTION 3 |

## Engram Traceability

Observation IDs read during this archive: **#8821** (apply-progress, final all-8-complete), **#8832** (verify-report). The Engram mirror of this report is saved under topic key `sdd/task-resolver-direct-enrichment/archive-report` (project `dotfiles`).
