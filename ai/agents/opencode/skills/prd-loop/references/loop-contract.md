# Loop Contract — prd-loop reference

Portable requirements for any handoff pack prepared by prd-loop. Nothing here is
project-specific: no absolute home paths, no fixed doc count, no fixed coverage number.
Read this only while preparing a pack.

## 1. Invariants

- Prep-only: the pack describes a loop; it never executes it. Nothing in the pack flips
  review modes, lenses, permissions, models, or authorizes itself.
- Launch is a separate explicit user action (Section 9). An unreviewed PRD is not a
  launch.
- One actor active at a time. When external parallel work touches the same source,
  record a source-writer ownership guard: the exact files this plan edits, and a stop
  rule if another writer appears in them.
- The future executor is assumed less capable and must never choose transports, tools,
  runners or interpretations at runtime. Every such choice is made here — or explicitly
  blocked.
- If the pack itself changes after evidence was recorded, the affected evidence is void
  and must be re-recorded against the new pack identity.

## 2. Proportionality

| Tier | Signals | Pack shape |
|---|---|---|
| Direct | One task, trivial blast radius | No pack. Recommend plain direct work. |
| Compact | A few tasks, one subsystem | PRD only if useful + ONE self-contained execution doc |
| Full | Multi-milestone or cross-cutting | README-indexed technical plan + tasks + gates; link existing PRDs only when useful |

Rules:

- One canonical execution entrypoint: a single doc linking every required doc by exact
  relative path; everything else links back. No orphans, no duplicated authorities.
- Never copy existing PRD content into the pack; reference path + identity hash
  (Section 5).
- Add a document only when it removes a decision the executor would otherwise make.
  There is no mandatory doc count.
- Docs are born in the repository owning the work. Confirm the canonical root when the
  copy might be a fork; when repos are ambiguous, ask — never default to a dotfiles or
  home location.

Compact pack skeleton (single file): header (root, PRD link + hash, readiness state) →
tasks (Section 7 template) → gate table → launch summary or link.

## 3. PRD composition

- Resolve the existing `prd` skill by name through the runtime skill registry; record
  the resolved path once in the pack header. If resolution fails, stop visibly: name the
  missing dependency and list what was already prepared so nothing is lost.
- The `prd` entry gate still applies inside the loop: confirmed decisions only,
  FR-01..FR-nn with RFC 2119 keywords, evidence or visible ASSUMPTION, explicit
  Non-Goals, and the V1-V3 verification contract for technical PRDs.
- Unresolved scope gaps block the draft. Design exceptions require the user; the planner
  never fills them.
- Product-level template fields (privacy model, provider, currency, pricing, retention)
  stay blank when undecided — blanks are blockers, not defaults.

## 4. Clarification protocol

- One product-level question at a time, then STOP and wait. Non-interactive runs
  (evaluations, child processes) never invoke a question tool; they return the exact
  blocked question in the result payload.
- Routine technical decisions (runner invocation, gate phrasing, file layout inside plan
  scope) are locked by the planner AFTER repo evidence. If feasibility cannot be proven
  from the repo, mark ASSUMPTION with the evidence that would confirm it, or BLOCKED.

## 5. Identity, baselines, evidence

- Candidate binding: content hashes (e.g. SHA-256) plus file modes for the candidate
  bytes — every file the plan will touch, and the tests, config, fixtures, dependency
  manifests and environment pins its gates depend on.
- Baselines diff against the current working tree, not HEAD-only: uncommitted WIP is
  preserved, and neither a commit nor a clean worktree is ever required.
- Logs, generated artifacts and secrets are excluded from bindings and recorded output.
- Commands cited in the pack must be real: verified against the actual repo and
  toolchain, or labeled FUTURE with the task that creates them. An unrun command is
  never reported as passed.
- Every structural claim (counts, ID coverage, link integrity) records the command or
  readback method that verified it; an unverified claim is labeled unverified, never
  PASS.
- A compact pack needs no dedicated script or harness. Define the current runtime
  contract only; when machine checking is unavailable, say so and mark the check's scope
  explicitly as manual or functional.

## 6. Testing and coverage policy

- Resolve the target's testing discipline (runner, TDD or not) from the actual project
  config; never import another project's discipline.
- Coverage metric, denominator, threshold, platform and tool support come from the user
  or the repo. No universal threshold. A lines-only tool never yields branch-coverage
  claims.
- A missing coverage decision that materially affects gates: ask. A declared threshold
  without measurable proof: blocker.
- Integration/E2E gates declare their boundary: simulated provider/network versus
  physical. The pack states which one each gate exercises.
- Manual or physical checks are listed separately from machine gates, never conflated
  with them.

## 7. Task and gate model

Task entry template:

```markdown
### T-<nn> — <imperative title>
- Intent: <one sentence, traced to FR-xx / milestone>
- Depends: <task ids, or none>
- Edit allowlist: <exact paths this task may create or modify>
- Test creator: <task that creates the tests; existing: <path>>
- Evidence: <what a reviewer runs or reads to see it done>
- Gate: G-<nn>
```

Gate table — the single authority (other docs link here, never copy):

```markdown
| Gate | Command (literal) | cwd | Source policy | Exit meaning |
|---|---|---|---|---|
| G-01 | <exact command, no ellipses> | <path> | <files/state it runs against> | 0 = <pass meaning>; nonzero = <failure meaning> |
```

- Every command is literal and complete: no ellipses, no same-as-above.
- Every gate row carries its own literal cwd — never a same-as or see-G-xx reference.
- One authority per kind: gates, tasks and FRs each have exactly one registry table.
  Any other mention points to it instead of restating counts or ID ranges.
- New scripts or tests are FUTURE until their creator task completes; gates depending on
  them inherit the label.
- A task advances only through its gate; one task active at a time.
- Traceability: every FR maps to at least one task; every task maps to at least one gate
  or explicit manual check; names used in docs match the filesystem, FSM states and
  outputs actually implemented.

## 8. Readiness states

Typed fields, not impressions:

- `draft` — pack incomplete; the missing pieces are listed.
- `blocked` — one named unresolved decision plus its exact question.
- `ready_for_bootstrap` — foundation work (fixtures, verifiers, harness) is defined with
  its own finite budget; nothing asserts runtime behavior yet.
- `ready_for_execution` — every gate command is literally runnable today, all available
  evidence recorded, no FUTURE labels on the critical path.

Bootstrap budgets are finite and self-contained: the foundation may specify implementing
a verifier without claiming it exists now, and never locks or snapshots future state it
does not own (no bootstrap/execution circularity).

Declared counts and ID ranges are part of readiness: they are verified against the
authority tables before any ready_* state is assigned, and a mismatch — or a count that
was never verified — is a gap that blocks readiness and is listed as such.

## 9. Execution contract for the future loop

- Default budget: 2 functional remediation rounds per milestone. Stricter repo or user
  policy wins; never looser without explicit user words. Budget exhaustion on a
  milestone stops and reports — no renames, resets or unbounded retries.
- The budget is a technical proposal until the user actually launches with the launch
  prompt.
- Stop conditions for the future executor: crash, unknown error class, wrong source
  (identity hash mismatch), unavailable gate, any need for native human consent, budget
  exhaustion. On stop, report state losslessly and resume only via Section 10.
- Native authority (prompts, consent, review, retries owned by the host runtime) is
  never guessed portably: the pack defers to the exact provider tokens and native
  contract in force when execution runs. Native budget counters are read from that
  contract, never invented here.
- Final verification reruns every affected gate against the same candidate bytes — one
  binding, no partial rebinds — with manual/physical checks reported separately. A
  self-test that only asserts empty output is not a runtime pass.
- No auto commits/push/PR, no paid or remote credentials. Repo policy stricter than this
  contract wins.

## 10. Resume bookmark

```markdown
## Resume bookmark (maintained by the future executor)
- Pack identity: <entrypoint path + hash>
- Completed: <task ids, gate results, candidate binding id>
- Active task: <id> — budget remaining: <n remediation rounds>
- Next action: <exact next task and gate>
```

Independent contexts resume from this bookmark and re-read current locators. The newest
global memory never overrides it.

## 11. Planning checklist (before declaring any pack ready)

1. Proportionality tier chosen, justified in one line.
2. PRD composed or linked by exact path + identity; zero duplicated PRD content.
3. Every FR traced to task(s); every task to gate(s) or manual check(s).
4. Gate table literal and complete; FUTURE labels where applicable.
5. Coverage/testing policy resolved from repo or user, or a blocker recorded.
6. Candidate binding recorded (bytes, modes, tests, config, fixtures, deps, environment);
   WIP preserved; logs/generated/secrets excluded.
7. Readiness state typed honestly; nothing passed without a run.
8. Unresolved choices listed, each with its exact question.
9. Launch request filled as far as resolved; unresolved placeholders visible.
10. Finalization readback done: pack reread end to end, actual gate/task/FR IDs counted,
    repeated counts and ranges reconciled against the one authority per kind, every
    cross-reference traced.
11. Count verification used deterministic static checks only (counting/parsing pack
    files — never executing tests), and the command or readback method is recorded;
    unchecked claims are not structural PASSes.
12. Report includes artifact paths, root binding, FR/task/gate counts, checks run vs
    pending, unresolved choices, the future launch prompt — and the limitation: the loop
    is a prepared contract, not a background service; nothing runs until the user
    launches it.
