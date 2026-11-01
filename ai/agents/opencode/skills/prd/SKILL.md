---
name: prd
description: "Trigger: PRD, generar PRD, redactar PRD, product requirements document, documento de requisitos, prd loop, prepare loop, prep the loop, preparar loop, execution handoff, handoff de ejecución, prepare launch, launch prep, plan execution from a PRD, deterministic execution pack. Turn confirmed product decisions into a structured PRD draft (with V1-V3 verification strategy), and optionally prepare a proportional, deterministic execution handoff pack (tasks DAG, literal gates, budgets, launch request)."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "2.0"
---

# Skill: prd

## Activation Contract

Load when asked to create, draft, or write a PRD, or to prepare a deterministic PRD-to-execution loop handoff.
This skill unifies:
1. **PRD Drafting**: Consolidating CONFIRMED product decisions into a durable requirements document with verification strategy.
2. **Loop & Execution Preparation**: Packaging that PRD into a deterministic execution pack (tasks DAG, literal gates, launch request) that a future session can follow without re-deciding scope.

## Hard Rules

- **Entry Gate**: Draft only from confirmed product decisions (grill-me ledger, approved mockup, explicitly confirmed choices). If decisions affecting scope are missing or ambiguous, run interrogation FIRST (one question at a time). Never invent requirements or default values for product-level choices.
- **RFC 2119 & Traceability**: Number functional requirements FR-01..FR-nn using MUST/SHOULD/MAY keywords. Every requirement must trace to a source decision.
- **Evidence-Based Claims**: Technical claims need file:line, command output, or doc citations; otherwise visibly label them ASSUMPTION. Include an explicit Non-Goals section.
- **Technical Verification (V1–V3)**: Technical PRDs (infra, service, install, deployable behavior) MUST include a Verification & Acceptance section built from `assets/verification-and-acceptance.md` (V1 automated unit/infra, V2 clean-room smoke, V3 manual UAT checklist).
- **Execution Boundaries & Stop Gate**:
  1. *Scope containment*: Implementation is strictly limited to the stated Acceptance Criteria / FRs. The downstream executor is strictly forbidden from implementing unrequested optimizations, speculative features, or out-of-scope refactorings.
  2. *Escalation channel*: If the executor uncovers blocking bugs, regression risks, or architectural gaps outside the PRD, it MUST document them under `## Escalated / Out-of-Scope Issues` and STOP without implementing them unilaterally.
  3. *Binary completion stop*: Once verification gates and tests pass, the executor MUST output a 3-line completion summary and terminate execution immediately without looping.
- **Draft Status**: A PRD documents intent; planning never authorizes implementation. Launch is a separate, explicit user action.
- **Language**: Artifacts follow the project/host language convention (English by default unless requested otherwise); identifiers and code terms stay in English. Conversation stays in the user's language.

## Decision Gates

| Situation | Action |
|---|---|
| Open questions affect scope | Interrogate first (grill-me); ledger answers, then draft |
| All key decisions confirmed (Drafting only) | Draft PRD using canonical skeleton + V1-V3 verification |
| User requests execution handoff / loop prep | Prepare full or compact execution pack per `references/loop-contract.md` + `assets/launch-request-template.md` |
| PRD describes a simple, tiny change (1 task) | Recommend plain direct work; avoid unnecessary loop machinery |
| Code change ready for implementation | Hand off via Launch Request (or SDD); never execute without explicit user launch authorization |

## Execution Steps

### Phase 1: PRD Drafting (Default)
1. Verify the entry gate: list confirmed decisions and open questions. If open questions affect scope, STOP and interrogate.
2. Draft with canonical skeleton:
   1. Context & Problem
   2. Objectives & Non-Objectives (explicit Non-Goals)
   3. Architecture & Trust Boundaries
   4. Data Schema & State Machine (FSM when states exist)
   5. Security & Threat Modeling
   6. FR-01..FR-nn requirements (RFC 2119) with per-requirement evidence
   7. Verification & Acceptance (apply `assets/verification-and-acceptance.md` for technical PRDs)
   8. Execution Boundaries & Stop Gate (scope containment + escalation note)
3. Deliver PRD: file path, DRAFT status, FR count, open questions, assumptions.

### Phase 2: Loop & Execution Handoff Prep (When loop/handoff requested)
4. Classify proportionality (direct / compact / full) per `references/loop-contract.md`.
5. Assemble execution plan:
   - Stable task IDs (`T-01..T-nn`) with edit allowlists and test creators.
   - Single authority gate table (`G-01..G-nn`) with literal commands and exact `cwd`.
   - Candidate binding and stop conditions.
6. Prepare Launch Request from `assets/launch-request-template.md`.
7. Finalize and report readiness state (`draft` | `blocked` | `ready_for_bootstrap` | `ready_for_execution`).

## Output Contract

Return:
- Document path(s) and status (`DRAFT` / readiness state).
- FR count, task/gate counts (if loop prepared).
- Open questions / unresolved choices (if any).
- Next action: Recommended launch prompt or next review step.

## References

- `assets/verification-and-acceptance.md` — V1-V3 verification strategy template for technical PRDs.
- `assets/launch-request-template.md` — Explicit launch authorization template for future execution sessions.
- `references/loop-contract.md` — Portable loop requirements, deterministic planning checklist and templates.
