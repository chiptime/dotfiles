# Autonomous Task Triage & Resolution Engine with Human-in-the-Loop (HITL)

## 1. Context and Problem

Exploration #8709 documents ingestion without downstream triage. **Golden rule: external mutations require explicit human approval; proposals remain Draft/Pending approval.**

## A. Objectives and Non-Objectives

- Reduce friction; improve consistency and local-context enrichment.
- Exclude unilateral execution, review bypass, production mutations, remote/mobile approval, and webhooks (unnecessary attack surface).
- Teams replies and Git patches remain drafts in v1; existing ingestion stays unchanged.

### Capabilities

#### New Capabilities
- `notion-task-hitl-resolver`: Inbox detection, enrichment, classification, drafts, approval, controlled Notion execution.

#### Modified Capabilities
None; `teams-to-tasks-polling` remains unchanged.

## B. Pipeline Architecture and Sandbox (Trust Boundaries)

1. **Ingestion/watcher:** recommend an LLM-free Bun poller plus attended session: cheap detection without unattended actuation. Query only Tareas `3d675532-da31-802b-b12f-000be53e99ac`, Estado `📥 Inbox`; deduplicate page/revision locally.
2. **Enrichment:** map Proyecto through `scripts/projects.yaml` to approved `~/.dotfiles`/`~/projects/` roots. Bounded semantic/grep/AST searches cover scripts, docs, repositories, and approved knowledge sources; record paths/revisions, never credentials.
3. **Classification:** SOLVABLE_LOCAL requires sufficient cited evidence; ACTIONABLE_RECOMMENDED has justified next steps but unresolved execution/verification; MANUAL_REQUIRED covers ambiguity, missing access, or unsafe requests.
4. **Proposals:** trusted host persists versioned local runs: reply draft, inert patch, checklist, evidence, classification rationale, action targets, hash, budgets. Runs are authoritative, auditable, git-friendly—not automatically committed. Notion is a satellite per `doc/control-hub-architecture.md`.
5. **Approval:** chat preview supports approve/reject/revise; bind confirmation to exact hash, actions, destination and credential/session. Extend deterministic `ai/teams-to-tasks/src/notion-writer.ts` with validated mirror/update actions and retries. Mirror publication also requires approval; Notion edits never authorize execution.

## C. Notion Data Schema and States

Add Triage Status/select, Resolution Draft/rich_text, Local Context Ref/rich_text, Approval State/select, Draft ID/rich_text. Preserve Estado and Notas fingerprints.

Local FSM: Detected→Analyzing→Pending approval→Approved→Executing→Executed; analysis→Manual required/Failed; pending→Rejected; failures/revisions→new draft. All unlisted transitions fail closed. Source drift invalidates approval; receipts prevent replay. Mirror failures never imply success.

## D. Security and Threat Modeling

Analysis has read-only disk/APIs; only the trusted host writes run artifacts. Treat Teams→Notas→context→drafts as untrusted data, never instructions. Pin realpaths; deny secrets, `shell/private-env.sh`, credential-adjacent files, modules, caches, profiles, and SIS sources. No arbitrary shell or ambient sessions.

Risks: injection/leakage (High), replay/drift (Medium); mitigate through isolation, redaction, version checks and idempotency.

Rollback: disable resolver; revoke pending approvals; restore writer changes; retain receipts. Compensating external writes require fresh approval.

## E. Success Metrics and Acceptance Criteria

- [ ] Zero unapproved mutations; injection/replay/illegal-transition tests pass.
- [ ] Measure approvals/(approvals+rejections), edit rate, p95 draft latency, tokens/cost per task.
- [ ] Enforce configured time/token/cost ceilings; exhaustion yields manual review.

Design: settle run location/retention, classifier thresholds, numeric budgets, schema migration, retry reconciliation. Dependencies: Bun, scoped reader, writer, approved Notion schema. Impact: `ai/teams-to-tasks/`; mapping read-only. Delivery: ask-on-risk above 400 changed lines; recommend chained PRs.

Ready for spec: Yes.
