# Tasks: Notion Task HITL Resolver

Conventions: paths repo-rooted; resolver modules in `ai/teams-to-tasks/src/resolver/`, tests in `ai/teams-to-tasks/tests/`; test commands run from `ai/teams-to-tasks/`. Slice naming normalized to A/B everywhere (design's "slice 3" = B). Ordering guardrail: **Slice B hard-depends on Slice 0 landing first** (`src/notion-writer.ts` is untracked today; B never stages prior-effort hunks) and on all of Slice A. `(read-only)` marks referenced-but-never-edited paths.

Decisions closed in this phase (design Open Question + validator notes):
- **Notification**: `wsl-notify-send` desktop toast, OUTBOUND-ONLY — fire-and-forget on Executed / Manual required; no listener, no webhook, never an inbound actuation channel. (R5)
- **Credential binding**: `run.json` records credential NAME + session_id alongside the hash; secret VALUES never persist; credential name is part of the hash canonical input. (R5)

## Review Workload Forecast

| Slice | PR | Est. changed lines (add+del) | Risk vs 400 |
|---|---|---|---|
| Slice 0 (land prior work) | PR 0 | 0 authored (pre-existing hunks commit as-is) | Low |
| A1 deterministic core | PR 1 | ~310 (260–360) | Medium |
| A2 run-store + gate | PR 2 | ~320 (280–360) | Medium |
| A3 detector + CLI + migrate | PR 3 | ~260 (220–300) | Low |
| A4 skill + installer + docs | PR 4 | ~150 (130–170) | Low |
| B executor + receipts | PR 5 | ~270 (230–300) | Medium |
| **Total** | — | **~1310 (1100–1500)** | **High** |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main (human-decided 2026-09-14 via question tool)
PR A1 size:exception accepted by maintainer 2026-09-14 (779 actual lines vs ~310 est; every test maps to a spec scenario)
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR | Focused test command (from `ai/teams-to-tasks/`) | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| WU-0 | Land prior-effort teams-to-tasks + pc-eyes work | 0 | `bun test && bunx tsc --noEmit` | suite green pre-commit | revert the prior-effort commit whole |
| WU-A1 | fsm, draft-hash, budgets, roots + unit tests | 1 | `bun test tests/resolver-fsm.test.ts tests/resolver-hash.test.ts tests/resolver-budgets.test.ts tests/resolver-roots.test.ts` | N/A — pure deterministic modules, no runtime boundary | revert 4 src + 4 test files |
| WU-A2 | run-store, evidence-gate, allowlist guard + tests | 2 | `bun test tests/resolver-gate.test.ts tests/resolver-run-store.test.ts tests/resolver-allowlist.test.ts` | temp state-dir fixture; gate denies a `shell/private-env.sh` (read-only) fixture path | revert 2 src + 3 test files |
| WU-A3 | detector, read-cli, schema-migrate + RED cron tests | 3 | `bun test tests/resolver-detector.test.ts` | attended `bun src/resolver/detector.ts --once` → queue write; `schema-migrate.ts --dry-run` output | revert 3 src + 1 test files |
| WU-A4 | task-resolver skill, installer, README, registry | 4 | `bash -n scripts/install-task-resolver.sh` | `crontab -l` shows line; skill loads attended through symlink | revert skill dir + installer + README hunk + symlink + registry regen |
| WU-B | triage action, execute, receipts, notify | 5 | `bun test tests/resolver-execute.test.ts tests/notion-writer.test.ts` | attended: approve h12 on sandbox page → mirror updated; identical replay refused | revert notion-writer hunk + execute.ts + test hunks |

Total exceeds one 400-line PR ⇒ 6 chained PRs; delivery strategy `ask-on-risk` ⇒ the human picks the chain strategy (stacked-to-main vs feature-branch-chain) before apply.

## Phase 1 — Slice 0: land pre-existing work (PR 0)

- [ ] 0.1 Verify prior work green — `bun test && bunx tsc --noEmit` in `ai/teams-to-tasks`; a failure blocks Slice 0 and is reported, never fixed here. Deps: none. Rollout prerequisite (design Migration 1). Est: 0.
- [ ] 0.2 Secret-scan pending changes in `ai/teams-to-tasks/*` + `ai/agents/opencode/skills/pc-eyes/*` (dotfiles-context rule), then commit each prior effort AS-IS with its own conventional commit; no resolver content mixed in. Deps: 0.1. Verify: `git log -2 --stat` shows only prior-effort files. Est: 0.

Work-unit commit (WU-0): prior-effort commits, untouched.

## Phase 2 — Slice A: new files only (PRs 1–4)

### PR A1 — deterministic core

- [ ] 1.1 Create `src/resolver/fsm.ts` — pure transition table (Detected/Analyzing/Pending approval/Approved/Executing/Executed + Manual required/Failed/Rejected); unlisted transitions throw fail-closed; drift re-entry to Analyzing exposed. R6. Deps: 0.2. Verify: bun test resolver-fsm. Est: ~40.
- [ ] 1.2 RED `tests/resolver-fsm.test.ts` — write first, fails before 1.1 exists: Detected→Executing refused; full legal path walks; drift re-enters Analyzing. R6. Est: ~55.
- [ ] 1.3 Create `src/resolver/draft-hash.ts` — SHA-256 over canonical JSON `{page_id, revision, actions[], destination, draft_id, credential_name, session_id}`; stable key order; `h[0:12]` helper. R5. Est: ~30.
- [ ] 1.4 `tests/resolver-hash.test.ts` — key-order/whitespace canonicalization stable; different credential_name or session_id ⇒ different hash. R5. Est: ~35.
- [ ] 1.5 Create `src/resolver/budgets.ts` — one exported typed `DEFAULT_BUDGETS` config object (task: 6 min / 80k tokens / $0.50 / 12 reads / 400 KiB; session: 30 min / $5) — no budget literal anywhere else; `checkExhaustion()` ⇒ Manual required. R9, R3. Est: ~30.
- [ ] 1.6 RED `tests/resolver-budgets.test.ts` — exhaustion ⇒ MANUAL_REQUIRED verdict with partial-evidence-retained flag; within-budget passes. R9. Est: ~45.
- [ ] 1.7 Create `src/resolver/roots.ts` — parse `scripts/projects.yaml` (read-only) into the Proyecto→approved-roots map; realpath-pinned resolution; unknown Proyecto ⇒ zero roots (fail closed). R2. Est: ~40.
- [ ] 1.8 `tests/resolver-roots.test.ts` — mapping hits resolve; unknown Proyecto denied. R2. Est: ~35.

Work-unit commit (WU-A1): `feat(resolver): deterministic core — fsm, draft hash, budgets, roots`.

### PR A2 — run-store + evidence gate

- [ ] 2.1 RED `tests/resolver-gate.test.ts` — write first: `shell/private-env.sh` (read-only) and credential-adjacent names denied; realpath escape refused; size caps enforced; Notas text `run rm -rf /tmp/x` stays inert quoted data, nothing executes. R2, R8. Est: ~90.
- [ ] 2.2 Create `src/resolver/run-store.ts` — `~/.local/state/task-resolver/runs/<draft-id>/{run.json, draft.md, evidence.json, patch.diff, actions.json}` + `pending-queue.json`, `seen.json`; run.json carries credential NAME + session_id (never values) and metrics (tokens, cost, latency, reads); prune terminal runs >90d; receipts never pruned. R4, R5, R9. Est: ~90.
- [ ] 2.3 `tests/resolver-run-store.test.ts` — complete hashed run persists all elements, stays uncommitted; schema exposes credential name; prune preserves receipts. R4. Est: ~45.
- [ ] 2.4 Create `src/resolver/evidence-gate.ts` — deterministic boundary filter: realpath-allowlist recheck against roots.ts output, secret-pattern + credential-name deny, size caps; violations fail closed before persist. R2, R8. Est: ~70.
- [ ] 2.5 RED `tests/resolver-allowlist.test.ts` — boundary guard: an ingestion payload emitting `triage` is rejected by `src/completion.ts` (read-only); must stay green through Slice B. R7. Est: ~25.

Work-unit commit (WU-A2): `feat(resolver): versioned run store and fail-closed evidence gate`.

### PR A3 — detector + attended entry points

- [ ] 3.1 RED `tests/resolver-detector.test.ts` — write first: unchanged rerun detects once (seen.json); SIS-source query refused fail-closed; concurrent run refused via lockfile; `PAUSE` honored; Notion hard failure ⇒ rc 2 with seen.json untouched. R1 + threat row (cron process integration). Est: ~85.
- [ ] 3.2 Create `src/resolver/detector.ts` — LLM-free Tareas `3d675532-da31-802b-b12f-000be53e99ac` query, Estado `📥 Inbox` only, never forbidden SIS sources; single-instance lock; PAUSE parity with teams-to-tasks; writes pending-queue.json + seen.json. R1. Est: ~85.
- [ ] 3.3 Create `src/resolver/read-cli.ts` — attended CLI over `src/scoped-read.ts` (read-only): path → gated read returning cited path+lines; deny output mirrors evidence-gate verdicts. R2. Est: ~30.
- [ ] 3.4 Create `src/resolver/schema-migrate.ts` — retrieve Tareas data source, PATCH-add the 5 properties (Triage Status select; Resolution Draft / Local Context Ref / Draft ID rich_text; Approval State select), skip-existing, merge options; `--dry-run` prints the planned PATCH without sending it. R5. Est: ~60.

Work-unit commit (WU-A3): `feat(resolver): cron detector with dedupe, lock and pause; schema migrator`.

### PR A4 — skill + installer + docs

- [ ] 4.1 Create `ai/agents/opencode/skills/task-resolver/SKILL.md` — evaluator contract: queue consumption; read-only context via read-cli; classification criteria (SOLVABLE_LOCAL iff ≥2 independent citations or 1 authoritative doc + deterministic check, no unresolved deps; ACTIONABLE_RECOMMENDED justified steps with unresolved verification; MANUAL_REQUIRED ambiguity/missing-access/unsafe); draft preview + `approve <h12>` session binding; mirror never authorizes; untrusted text stays data; budget exhaustion ⇒ Manual required; notification is outbound-only. R3, R8, R5. Est: ~100.
- [ ] 4.2 Map skill — `ln -sfn` repo skill dir → `~/.config/opencode/skills/task-resolver`; verify by reading through the link. dotfiles-context contract. Deps: 4.1. Est: 0.
- [ ] 4.3 Create `scripts/install-task-resolver.sh` — idempotent crontab line (hourly M–F), separate from the teams-to-tasks installer, following `scripts/install-teams-to-tasks.sh` (read-only) as pattern. Deps: 3.2. Verify: re-run is idempotent; `crontab -l`. Est: ~25.
- [ ] 4.4 Update `ai/teams-to-tasks/README.md` — resolver section: two-process model, state-dir layout, machine-local never committed, approval gate, notification boundary. Deps: 4.1. Est: ~20.
- [ ] 4.5 Regenerate `.atl/skill-registry.md` via its canonical generator (skill-registry skill scan+render) + Engram save `topic_key: skill-registry`; gitignored ⇒ 0 repo lines. Deps: 4.2. Est: 0.
- [ ] 4.6 Attended gate before Slice B — run `bun src/resolver/schema-migrate.ts --dry-run`, review, then apply; confirm the 5 properties exist on Tareas. R5. Deps: 3.4. Est: 0.

Work-unit commit (WU-A4): `feat(resolver): task-resolver skill, cron installer and docs`.

## Phase 3 — Slice B: shared-file touch (PR 5)

Hard deps: Slice 0 landed (`src/notion-writer.ts` tracked), Slice A merged, schema migrated (4.6).

- [ ] 5.1 RED `tests/resolver-execute.test.ts` — write first, fails before 5.4 exists: identical action replay refused by receipt; source-revision drift voids approval → Analyzing with new hash; illegal transition refused; 429 retried then receipt recorded. R5, R6, R7. Est: ~75.
- [ ] 5.2 Extend `src/notion-writer.ts` — add `SetTriageAction` to the `NotionAction` union + one `triage` handler (`resolution_draft` ≤2000 chars, selective property set) with 429/5xx retry; ingestion path unchanged (guard 2.5 stays green). R7. Deps: 0.2, 5.1. Est: ~45.
- [ ] 5.3 Extend `tests/notion-writer.test.ts` — triage handler sets only provided properties; 429 → retry → receipt, via `fetchFn` injection precedent. R7. Deps: 5.2. Est: ~45.
- [ ] 5.4 Create `src/resolver/execute.ts` — attended only: reads local `run.json` only (mirror never authorizes), verifies hash/actions/destination/session/credential-name binding, drift re-fetch check, receipt replay check, dispatches NotionWriter triage, appends `receipts.jsonl`, run.json → Executed/Failed (partial receipts preserved); best-effort `wsl-notify-send` outbound toast. R5, R6, R7. Deps: 5.1–5.3, 4.6. Est: ~105.
- [ ] 5.5 Full gate — `bun test && bunx tsc --noEmit` green: allowlist guard, RED security suite, ingestion regression. R9 (zero unapproved mutations). Deps: 5.4. Est: 0.
- [ ] 5.6 Attended classification walkthrough — three representative Inbox items through SKILL.md criteria; outcomes match R3 scenarios (sufficient evidence ⇒ SOLVABLE_LOCAL; unresolved verification ⇒ ACTIONABLE_RECOMMENDED; unsafe/ambiguous ⇒ MANUAL_REQUIRED); record in PR. R3. Deps: 5.4. Est: 0.

Work-unit commit (WU-B): `feat(resolver): hash-bound triage executor with receipts and retries`.

## Requirement Coverage

R1 → 3.1, 3.2 · R2 → 1.7, 1.8, 2.1, 2.4, 3.3 · R3 → 1.5, 1.6, 4.1, 5.6 · R4 → 2.2, 2.3 · R5 → 1.3, 1.4, 2.2, 3.4, 4.6, 5.1, 5.4 · R6 → 1.1, 1.2, 5.1, 5.4 · R7 → 2.5, 5.2, 5.3, 5.4 · R8 → 2.1, 2.4, 4.1 · R9 → 1.5, 1.6, 2.2, 5.5. Slice 0 tasks are rollout prerequisites (design Migration step 1), not requirement work.
