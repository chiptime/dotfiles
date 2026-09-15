# Design: Autonomous Task Triage & Resolution Engine (HITL)

## Technical Approach

Two-process model reusing the proven teams-to-tasks split — cognitive proposer, deterministic executor — bridged by an LLM-free cron detector. A Bun `detector.ts` (cron) queries Tareas `3d675532-da31-802b-b12f-000be53e99ac` for `📥 Inbox` and writes a local queue. An attended OpenCode session (new skill `task-resolver`) evaluates queued items with read-only, realpath-pinned local context. Drafts persist as versioned local runs (authoritative; Notion stays satellite per `doc/control-hub-architecture.md`). A deterministic executor extends `src/notion-writer.ts` and executes only hash-bound, session-approved Notion actions, guarded by append-only receipts. Implements spec requirements R1–R9 (traceability below).

## Architecture Decisions

| # | Decision | Choice | Rejected alternative | Rationale |
|---|---|---|---|---|
| D1 Runtime shape | Cron LLM-free detector (hourly M–F, single-instance lockfile, `PAUSE` flag parity) + attended evaluator skill; handoff = `pending-queue.json` `[{page_id, revision, title, detected_at}]` | Full unattended pipeline — its autonomous-mode bypass is anti-HITL; session-only skill — no detection | Cheap detection, zero unattended actuation, mirrors `poller.ts` precedent |
| D2 Run storage | `~/.local/state/task-resolver/`: `runs/<draft-id>/` (`run.json`, `draft.md`, `evidence.json`, `patch.diff`, `actions.json`), `receipts.jsonl`, `seen.json`, `pending-queue.json`, `lock`. Never auto-committed; detector prunes terminal-state runs >90 days; receipts never pruned | In-repo gitignored dir — violates dotfiles-context machine-local rule; untrusted quotes could leak into repo | Plain JSON/MD deterministic layout is git-friendly without committing; receipts must outlive runs for replay protection |
| D3 Budgets & thresholds | Per task: 6 min wall / 80k tokens / $0.50 / 12 reads / 400 KiB evidence. Per session: 30 min / $5 (excess defers to next session). SOLVABLE_LOCAL iff ≥2 independent citations (path+lines from approved roots) or 1 authoritative doc + deterministic check, no unresolved deps, patch parses as unified diff (inert). Else ACTIONABLE_RECOMMENDED (steps justified, verification unresolved) / MANUAL_REQUIRED (ambiguity, missing access, unsafe) | Loose "sufficiency" judgment | Ceilings enforceable deterministically at every `run-store` persist; exhaustion → Manual required with partial evidence retained (R9 scenario) |
| D4 Schema migration | Attended one-shot `schema-migrate.ts`: retrieve data source → PATCH-add the 5 properties (Triage Status select; Resolution Draft / Local Context Ref / Draft ID rich_text; Approval State select), skip-existing, merge options | Ad-hoc manual API calls | Idempotent, additive, reviewable; missing schema degrades mirror only, never analysis |
| D5 Executor extension | New `triage` action in the `NotionAction` union + one handler method; `completion.ts` allowlist unchanged | Separate writer module — duplication risk flagged in exploration | Proposal decision closed; ingestion path provably unchanged via RED test |
| D6 Approval binding | `h = SHA-256(canonical-JSON{page_id, revision, actions[], destination, draft_id})`; chat `approve <h[0:12]>` binds {h, actions, destination, session_id} into `run.json`. Executor re-fetches page revision (drift → approval void → Analyzing, new hash). Receipts keyed by action+destination+h | Notion-side approval property | Mirror never authorizes by construction: executor reads only local `run.json` |

## Data Flow

    cron ──> detector.ts ──> pending-queue.json ──> task-resolver skill (attended)
             seen.json                                │ roots.ts (Proyecto → projects.yaml → roots)
    Tareas (read-only query)                          │ resolver-read (reuses scopedReader)
                                                     ▼
                                          evidence-gate ──> run-store (runs/<id>/)
                                                     │ draft preview + h (chat)
                                                     ▼ approve h12 (session-bound)
                                          execute.ts: drift check → receipt check
                                                     │
                                          NotionWriter(triage/enrich/resolve) → Tareas mirror
                                                     ▼
                                          receipts.jsonl append; run.json → Executed

FSM (pure table in `fsm.ts`, unlisted transitions throw): Detected→Analyzing→Pending approval→Approved→Executing→Executed; Analyzing→Manual required/Failed; Pending approval→Rejected; source revision change → back to Analyzing.

## File Changes

| File | Action |
|---|---|
| `ai/teams-to-tasks/src/resolver/{detector,roots,run-store,evidence-gate,fsm,draft-hash,budgets,execute,schema-migrate,read-cli}.ts` | Create — all deterministic modules |
| `ai/teams-to-tasks/src/notion-writer.ts` | Modify — add `SetTriageAction` + handler (only shared-file touchpoint) |
| `ai/agents/opencode/skills/task-resolver/SKILL.md` | Create — evaluator contract; symlink to `~/.config/opencode/skills/` per dotfiles-context |
| `scripts/install-task-resolver.sh` | Create — idempotent crontab line (separate from teams-to-tasks installer) |
| `tests/resolver-*.test.ts` | Create; extend `tests/notion-writer.test.ts` |

## Interfaces / Contracts

```ts
type TriageStatus = "⏳ Pendiente" | "🤖 Auto" | "💡 Acción" | "✋ Manual" | "✔️ Hecho" | "❌ Rechazado";
interface SetTriageAction { action: "triage"; page_id: string;
  triage_status?: TriageStatus; resolution_draft?: string;   // ≤2000 chars
  local_context_ref?: string; approval_state?: string; draft_id?: string; }
interface QueueItem  { page_id: string; revision: string; title: string; detected_at: string; }
interface Receipt    { action_hash: string; page_id: string; destination: string; executed_at: string; ok: boolean; }
```

`evidence-gate.ts`: deterministic boundary filter — realpath-allowlist re-check against roots resolved by `roots.ts`, credential-name/secret-pattern deny, size caps; violations fail closed before persist. Untrusted text (Teams, Notas, drafts) stays quoted data everywhere; no shell, no ambient sessions.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | FSM transitions, hash canonicalization, gate deny-lists, budget ceilings, roots mapping | Pure `bun test` |
| Integration | Detector dedupe + SIS-source refusal, triage handler, receipt replay block, drift void, 429 retry | `fetchFn` injection (notion-writer.test.ts precedent) |
| RED security suite | Injection-in-Notas inert; illegal transition refused; replay blocked; drift voids; budget exhaustion → Manual; ingestion emitting `triage` rejected by completion.ts | Spec scenarios, one test each |

## Threat Matrix

Documentation-like paths / Git repo selection / Commit state / Push state / PR commands: **N/A** — no git, commit, push, or PR automation in any component; patches stay inert drafts; nothing classifies executables. **Applicable — process integration (cron)**: concurrent detector runs → lockfile refusal; `PAUSE` honored; Notion hard failure → rc 2 with `seen.json` untouched (no re-detection). RED: lock-contention test.

## Failure / Degraded Behavior

Detector down → session re-queries Tareas at start (queue is a hint, not source of truth). Notion 429/5xx → NotionWriter retries; hard exhaustion → run Failed, succeeded actions receipted, mirror failure never implies success; same-hash retry allowed while drift-free. Session crash mid-approval → run stays Pending approval; approvals never survive across sessions (attended executor only).

## Migration / Rollout

1. Land the pre-existing uncommitted teams-to-tasks work first (owned by a prior effort; resolver tasks never stage or modify those hunks — `src/notion-writer.ts` is currently untracked and is a hard dependency of slice 3).
2. Slice A: detector + state dir + store/gate/fsm/budgets + skill + installer (new files only).
3. Slice B: `triage` action + executor + receipts + completion regression (touches `notion-writer.ts`).
Chained PRs, each ≤400 changed lines; ask-on-risk per delivery policy. Schema migration run attended before slice B.

## Requirement Traceability

R1 detector · R2 roots + read-cli + evidence-gate · R3 skill criteria + budgets · R4 run-store · R5 draft-hash + execute · R6 fsm · R7 notion-writer extension + receipts · R8 skill untrusted-data rule + gate · R9 budgets + `run.json` metrics (precision inputs, p95 latency, tokens/cost per task).

## Open Questions

- None blocking. New-Inbox notification channel (`wsl-notify-send` vs ntfy) delegated to the tasks phase.
