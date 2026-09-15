# Delta for notion-task-hitl-resolver

v2 supersedes the v1 approval-gate flow with direct informational enrichment: a read-only researcher that fills each Inbox task with what the user needs to resolve it — the proposed reply (or what is missing to decide) plus sources. The only write is that information into the user's own task.

## MODIFIED Requirements

### Requirement: Direct Informational Enrichment
(Previously: Human Approval Gate — enrichment payloads required hash-bound approval before any Notion write.)

The evaluator MUST write directly into the user's Inbox task via the deterministic notion-writer triage action (validated property shape, 429/5xx retries), and only these fields: Triage Status (hint: 🤖 Auto / 💡 Acción / ✋ Manual), Resolution Draft (proposed reply OR what is missing to decide), and Local Context Ref (cited sources). No approval, hash, or receipt MAY gate enrichment. Estado MUST never change.

#### Scenario: Enriched task
- GIVEN a researched Inbox task
- WHEN enrichment writes
- THEN Triage Status, Resolution Draft, and Local Context Ref with citations are set; Estado unchanged

#### Scenario: No-evidence task
- GIVEN insufficient evidence to decide
- WHEN enrichment writes
- THEN Triage Status is ✋ Manual and the draft states exactly what is missing; nothing fabricated

## ADDED Requirements

### Requirement: Enrichment Idempotency
A task already carrying a Draft ID or non-empty Resolution Draft MUST be skipped. A task whose fields were cleared after a partial failure MUST become eligible again (the retry path).

#### Scenario: Rerun skips enriched
- GIVEN a task with a non-empty Resolution Draft
- WHEN enrichment reruns
- THEN the task is skipped, untouched

#### Scenario: Cleared fields re-enrich
- GIVEN a partial write left Resolution Draft empty
- WHEN enrichment reruns
- THEN the task is re-enriched

### Requirement: On-Demand Trigger
Enrichment MUST run only when the user requests it in session (e.g. "triaje el inbox"). No cron or unattended scheduling. (Supersedes poller-driven scheduling for new runs; the v1 detector remains optional and unused.)

#### Scenario: Session-triggered only
- GIVEN no user request this session
- WHEN the session idles
- THEN no enrichment runs

## REMOVED Requirements

### Requirement: Resolver State Machine
(Reason: v2 enrichment is stateless per task; the multi-state run lifecycle, drift-voids-approval, and receipt replay protection apply only to the dormant v1 path.)
(Migration: None for new runs; v1 execute.ts/receipts code stays dormant and documented.)

### Requirement: Deterministic Post-Approval Executor
(Reason: "post-approval" semantics are retired; the validated triage write with 429/5xx retries folds into Direct Informational Enrichment.)
(Migration: Existing notion-writer triage action reused, approval-free; ingestion unchanged.)

## RENAMED Requirements

### Requirement: Human Approval Gate → Direct Informational Enrichment
(Reason: v2 is informational-only enrichment; no approval ceremony. Requirement body in MODIFIED above.)
(Migration: update approval/hash/receipt wording in SKILL.md and ai/teams-to-tasks/README.md to direct enrichment.)

## UNCHANGED (restated for clarity)

- Read-only gated research: approved roots/allowlist, secrets denied, untrusted text as data.
- SIS sources forbidden.
- Estado untouched by any enrichment write.
- Teams replies, code pushes, task closures: out of scope by nature.
