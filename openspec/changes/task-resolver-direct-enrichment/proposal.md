# Proposal: Direct Task Enrichment

## Intent
Live validation and user feedback showed approval/hash/receipts and cron were overbuilt. Users triage in Notion; enrichment is the deliverable.

## Scope
### In Scope
- Inbox research: proposed answer or missing decisions, with sources.
- Direct in-session enrichment; skip already-enriched tasks.
- Retire approval/executor/hash/receipts from the path; document dormant code.

### Out of Scope
- Cron installation, Teams replies, code pushes, task closures, v1 code deletion, `modules/dotly`.

## Capabilities
### New Capabilities
None.

### Modified Capabilities
- `notion-task-hitl-resolver`: supersede R5 with no-gate-needed enrichment; reconcile draft/executor requirements and simplify new-run FSM.

## Approach
On demand, read Inbox; preserve gated read-cli roots/allowlist, secrets denial, budgets, untrusted-text-as-data. Classifications (🤖 Auto / 💡 Acción / ✋ Manual) are hints. Existing validated, retrying notion-writer `triage` writes only Triage Status, Resolution Draft, Local Context Ref into the user's task. Never touch Estado. Design decides any thin enrichment entry point.

## Affected Areas
| Path | Impact |
|---|---|
| `ai/agents/opencode/skills/task-resolver/SKILL.md` | Modify evaluator contract |
| `openspec/specs/notion-task-hitl-resolver/spec.md` | One capability delta |
| `ai/teams-to-tasks/README.md` | Document direct path and dormant v1 |

## Risks
- Partial writes/idempotency: define enrichment completeness and retry behavior.
- Scope growth: respect 400-line review budget; ask before exceeding.

## Rollback Plan
Revert v2 contract/docs and optional entry point; stop enrichment. Preserve task information; no cron.

## Dependencies
Existing triage schema, notion-writer, gated reader.

## Success Criteria
- [ ] Enrichment publishes without approval/hash/receipts; reruns skip enriched tasks.
- [ ] Sources retained; read boundaries and Estado preserved.

Ready for spec: Yes
