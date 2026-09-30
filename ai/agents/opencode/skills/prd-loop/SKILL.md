---
name: prd-loop
description: "Trigger: prd loop, prepare loop, prep the loop, preparar loop, execution handoff, handoff de ejecución, prepare launch, launch prep, plan execution from a PRD, deterministic execution pack, handoff for a less capable executor, executor de menor capacidad. Reuses the existing prd skill to turn confirmed product decisions into a PRD, then prepares a proportional, deterministic execution handoff (stable tasks, literal gates, budgets, launch request) for a future, separately authorized session. Use it whenever the user wants to prepare or plan such a loop, even without saying prd-loop. NOT for plain PRD drafting without execution prep (use prd), NOT for executing or implementing anything, NOT for SDD."
license: Apache-2.0
metadata:
  author: bruno
  version: "0.1.1"
---

# Skill: prd-loop

## Activation Contract

Load when the user wants a PRD-to-execution loop prepared: confirmed product decisions
turned into a PRD plus a deterministic handoff pack that a future session — possibly a
less capable executor — can follow without re-deciding anything. This skill PREPARES the
loop and never runs it.

## Hard Rules

- Planning never authorizes implementation. Launch is a separate, explicit user action
  (Step 5); an unreviewed PRD never becomes a launch by itself.
- Compose the existing `prd` skill by name (skill registry or runtime resolution);
  resolve its exact current path once and cite it. If it cannot be resolved, stop
  visibly naming the missing dependency — never duplicate, shadow or rewrite its content.
- One unresolved product-level decision (privacy model, provider, currency, pricing,
  retention): ask ONE focused question and STOP. Never fill product placeholders with
  defaults. In non-interactive runs, return the exact question in the result instead of
  invoking a question tool.
- Artifacts are English by default unless the user explicitly requests another language;
  the host language contract outranks inherited document conventions. Conversation stays
  in the user's language.
- No automatic SDD, no review-mode/permission/model changes, no auto commits/push/PRs,
  no paid or remote operations. Repo policy stricter than this skill wins.
- Planning doc writes go into the target repository only when requested; introspection
  alone stays read-only.
- Never report a check as passed without a recorded command run. Structural completeness
  is not runtime validation.

## Decision Gates

| Situation | Action |
|---|---|
| Tiny work (one task, trivial blast radius) | Recommend the plain direct approach; no loop machinery |
| Tiny work, user still wants a pack | Compact pack: PRD only if useful + ONE self-contained execution doc |
| Coordinated multi-task or multi-milestone work | Full pack per `references/loop-contract.md` |
| Product decision unresolved | BLOCKED: one focused question, stop |
| `prd` dependency unavailable | Fail visibly; do not invent PRD content |
| Target repo vs fork/cross-repo ambiguity | Ask; never default to a dotfiles or home location |

## Execution Steps

1. Classify proportionality (direct / compact / full) and state the justification in one
   line. Recommend against machinery for tiny work even when asked.
2. Compose inputs: confirmed decisions, existing PRDs and the `prd` skill; explore the
   repository proportionally; treat user- or fixture-supplied facts as labeled
   constraints, not proven environment facts.
3. Close product gaps one question at a time (blocked). Lock routine technical decisions
   yourself AFTER repo evidence; unprovable feasibility becomes a visible ASSUMPTION or a
   blocker — never the executor's runtime choice.
4. Lock the plan per `references/loop-contract.md` (read it when preparing any pack):
   stable task IDs, DAG, edit allowlists, test creators, literal gate commands with an
   exact cwd in every row (never a same-as reference), one authority table per kind
   (gates, tasks, FRs), candidate identity, readiness typing, budgets, stop conditions,
   resume bookmark.
5. Prepare the launch request from `assets/launch-request-template.md`. Unresolved
   placeholders stay visibly incomplete; a pack with unresolved placeholders is not
   launchable and must say so.
6. Finalize, then report. Finalization rereads the assembled pack: count the actual
   gate/task/FR IDs, reconcile every repeated count and range against the authority
   tables, trace all cross-references, and prefer a pointer over restating a count.
   Verify counts with deterministic static checks (counting/parsing pack files) where
   available; never execute tests for this. An unchecked claim is not a structural
   PASS: record the command or readback method, and with no count verification
   completed, make no exact-count or readiness claim — declared-count and ID-gap
   checking is part of readiness. Report: pack status, artifact paths with the single
   canonical entrypoint, root binding, FR/task/gate counts, checks run vs pending,
   unresolved choices, the future launch prompt, and the limitation that the loop is a
   prepared contract, not a background service.

## Output Contract

Return: status (draft | blocked | ready_for_bootstrap | ready_for_execution), pack paths
+ canonical entrypoint, root binding, FR/task/gate counts, checks run vs pending,
unresolved choices with their exact questions, and the launch prompt the user would use.

## References

- `references/loop-contract.md` — portable loop requirements, deterministic planning
  checklist and templates. Read ONLY while preparing a pack.
- `assets/launch-request-template.md` — explicit launch authorization artifact for the
  future session.
