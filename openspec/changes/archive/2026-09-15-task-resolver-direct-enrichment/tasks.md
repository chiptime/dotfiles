# Tasks: Direct Task Enrichment

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~370 (range 340–420) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR (one slice) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Lines (est.) | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------|----------------------|-----------------|-------------------|
| 1 | RED suite: skip predicate, write shape, pre-flight guards (1.1–1.3) | ~130 | PR 1 | `bun test tests/resolver-enrich.test.ts` | N/A — Notion I/O mocked via `fetchFn` seam; no live boundary | Delete `tests/resolver-enrich.test.ts` |
| 2 | `enrich.ts` thin CLI, GREEN (2.1–2.2) | ~115 | PR 1 | `bun test tests/resolver-enrich.test.ts` | Attended: `bun src/resolver/enrich.ts <uuid> --status "💡 Acción" --draft-file f` vs test page | Revert `src/resolver/enrich.ts` |
| 3 | v2 contract docs (3.1–3.2) | ~125 | PR 1 | `bun test && bunx tsc --noEmit` | N/A — docs only, no runtime boundary | Revert the 4 doc files |
| 4 | Full gate (4.1) | 0 | PR 1 | `bun test && bunx tsc --noEmit` | Same command as gate | N/A |

## Phase 1: RED — failing tests first

- [x] 1.1 Create `ai/teams-to-tasks/tests/resolver-enrich.test.ts` (bun:test, mocked page GET via injected `fetchFn`): enriched-skip (non-empty `Resolution Draft` ⇒ no PATCH), empty-enrich (both fields empty ⇒ one PATCH), cleared-fields-retry (cleared ⇒ re-enrich). Spec: Enrichment Idempotency. Verify: `bun test tests/resolver-enrich.test.ts` fails for the right reason.
- [x] 1.2 Add write-shape RED tests: PATCH body carries exactly Triage Status + Resolution Draft + Local Context Ref; `Estado`/`Notas` never present; draft > 2000 chars refused pre-write. Spec: Direct Informational Enrichment. Verify: fails in same file.
- [x] 1.3 Add threat-matrix RED tests (process integration): malformed `page_id` (non-UUID) refuses pre-flight with zero requests; draft with shell metacharacters written verbatim, spawns nothing; token from env only, never argv. Verify: fails in same file.

## Phase 2: GREEN — implementation

- [x] 2.1 Create `ai/teams-to-tasks/src/resolver/enrich.ts` — `runEnrich(deps, io)` + `main(argv)` + `import.meta.main` (read-cli.ts/schema-migrate.ts pattern; `fetchFn`/`token` injected); UUID-validate `page_id`; skip predicate = `Draft ID` OR `Resolution Draft` non-empty ⇒ skip. Spec: Enrichment Idempotency. Verify: 1.1 green.
- [x] 2.2 Extend `enrich.ts` — read task (idempotency check) → compose triage action (hint + draft + context ref; NEVER `Estado`) → dispatch via NotionWriter `triage` (single atomic PATCH, retries intact; `ai/teams-to-tasks/src/notion-writer.ts` (read-only)); enforce 2000-char limit; report enriched/skipped/refused. Spec: Direct Informational Enrichment. Verify: 1.2 + 1.3 green.

## Phase 3: Contract & documentation

- [x] 3.1 Rewrite `ai/agents/opencode/skills/task-resolver/SKILL.md` to v2: drop queue-as-authority and `approve <h12>` with zero contradictory v1 remnants. New contract: on-demand "triaje el inbox" → read Inbox (live read-only) → research via `read-cli` (unchanged rules: allowlist, secrets deny, untrusted-as-data, SIS forbidden) → classify as hint → `bun src/resolver/enrich.ts` per task → report. Spec: On-Demand Trigger + RENAMED migration. Verify: `grep -iE "approve|cron|h12" SKILL.md` empty.
- [x] 3.2 Update `ai/teams-to-tasks/README.md` resolver section (direct path; v1 detector/executor/receipts marked dormant) + one-line `DEPRECATED (v1, dormant)` docblocks in `src/resolver/execute.ts` and `src/resolver/detector.ts`. Spec: REMOVED/RENAMED migration docs. Verify: `bunx tsc --noEmit` clean.

## Phase 4: Full gate

- [x] 4.1 From `ai/teams-to-tasks/`: `bun test && bunx tsc --noEmit` green (prior 237 tests stay green); confirm `git diff --stat` under 400 changed lines before PR. Verify: exit 0.
