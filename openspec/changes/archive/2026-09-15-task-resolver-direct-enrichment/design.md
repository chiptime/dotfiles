# Design: Direct Task Enrichment

## Technical Approach

One thin CLI (`src/resolver/enrich.ts`) performs read → skip-check → single atomic triage PATCH. The evaluator session researches and calls it. No FSM, no hash, no receipts on this path.

## Decision 1 — Enrichment entry point

**Choice**: thin CLI `bun src/resolver/enrich.ts <page-id> --status <s> --draft-file <f> [--refs <r>]`, built as `runEnrich(deps, io)` + `main(argv)` + `import.meta.main`, `fetchFn`/`token` injected.

**Rejected**: in-session `bun -e`. It forces every runtime to re-invent the idempotency read and hand-shape the action JSON — untestable, unauditable, and it drifts the moment a second runtime (Pi) appears.

**Rationale**: matches the house pattern (`read-cli.ts`, `schema-migrate.ts`, `execute.ts`), makes the skip predicate unit-testable with an injected `fetchFn`, and reduces the skill contract to one command line. Retries stay where they already are, inside `NotionWriter.request()`.

## Decision 2 — Retry predicate (Enrichment Idempotency)

**Skip iff** `Draft ID` non-empty **OR** `Resolution Draft` non-empty. **Re-enrich iff both are empty.**

**Correction to the briefed `AND`**: v2 never writes `Draft ID` (a v1 hash artifact; writing it resurrects hash semantics). Under `AND`, the skip condition is unsatisfiable and *every* rerun re-enriches, breaking `Scenario: Rerun skips enriched`. `OR` is also what the delta spec states, and it additionally protects legacy v1-enriched tasks.

`Local Context Ref` is **excluded** from the predicate: it is legitimately empty on a ✋ Manual no-evidence task, which would otherwise loop forever.

**Atomicity — no writer change needed.** `triageTask` (notion-writer.ts:369) accumulates every provided field into one `properties` object and issues a single `PATCH /pages/{id}`. All three fields land or none do; partial state is impossible from the writer. The only way to reach a re-enrich state is the user clearing the fields — the documented retry path.

Enforced pre-write: `resolution_draft` ≤ `MAX_RESOLUTION_DRAFT_CHARS` (2000). `Estado` and `Notas` are never in the body.

## Decision 3 — Dormant v1 documentation

| Where | Note |
|---|---|
| `README.md` §Task Resolver | Rewrite "Gate de aprobación" → direct enrichment; one line marking detector/executor/receipts dormant |
| `execute.ts` docblock | One line: `DEPRECATED (v1, dormant): superseded by enrich.ts` |
| `detector.ts` docblock | Same one-line marker (the other executable v1 entry point) |

No deletions, no other files.

## Operational flow (FSM-free, per task)

    on-demand ──→ GET page ──→ skip? ──yes──→ report skipped
                                 │no
                                 ▼
                    read-cli research ──→ 1× triage PATCH ──→ report

## File Changes

| File | Action | Description |
|---|---|---|
| `ai/teams-to-tasks/src/resolver/enrich.ts` | Create | Read + skip predicate + single triage write |
| `ai/teams-to-tasks/tests/resolver-enrich.test.ts` | Create | Predicate and write-shape tests |
| `ai/agents/opencode/skills/task-resolver/SKILL.md` | Modify | v2 evaluator contract; drop approval/h12/queue authority |
| `ai/teams-to-tasks/README.md` | Modify | Direct path + dormant v1 |
| `src/resolver/execute.ts`, `detector.ts` | Modify | One-line DEPRECATED marker each |
| `ai/teams-to-tasks/src/notion-writer.ts` | None | Already atomic and selective |

## Requirement traceability

| Delta requirement | Design element |
|---|---|
| Direct Informational Enrichment | `enrich.ts` → `triageTask`, three fields only |
| Estado untouched | Field allowlist; `Estado`/`Notas` never in the PATCH body |
| Enrichment Idempotency | Skip predicate (OR) + single-PATCH atomicity |
| On-Demand Trigger | CLI has no scheduler; SKILL.md invokes it on user request |
| Removed FSM / executor | v1 entry points marked dormant, not invoked |

## Testing Strategy

| Layer | What | How |
|---|---|---|
| Unit | Skip predicate; body allowlist; 2000-char refusal | `bun test`, stub `fetchFn` |
| Integration | Enrich → rerun skips; cleared fields re-enrich | Fake Notion page fixture |

## Threat Matrix

New spawned CLI ⇒ process-integration boundary only.

| Boundary | Applicability | Response | RED test |
|---|---|---|---|
| Documentation-like paths | N/A — no file classification or execution |—|—|
| Git repository selection | N/A — no VCS operation |—|—|
| Commit / Push state | N/A — never commits or pushes |—|—|
| PR commands | N/A — no PR automation |—|—|
| Process integration (added) | Applicable | `page_id` validated as UUID before any request; untrusted Notion/Teams text is argv/stdin data, never interpolated into a shell; token from env only, never argv | Malformed `page_id` refuses pre-flight; draft containing shell metacharacters writes verbatim, spawns nothing |

## Migration / Rollout

No migration. Schema already carries the five resolver properties. Rollback: stop calling `enrich.ts`.

## Open Questions

None.
