---
name: prd
description: "Trigger: PRD, generar PRD, redactar PRD, product requirements document, documento de requisitos. Turn confirmed product decisions into a structured PRD draft; technical PRDs include a V1-V3 verification strategy."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.1"
---

# Skill: prd

## Activation Contract

Load when asked to create, draft, or write a PRD. This skill consolidates CONFIRMED decisions into a durable document. It does not replace interrogation (grill-me) or implementation planning (SDD).

## Hard Rules

- ENTRY GATE: draft only from confirmed product decisions (grill-me ledger, approved mockup, explicitly confirmed choices). If decisions affecting scope are missing or ambiguous, run interrogation FIRST. Never invent requirements to fill sections.
- Number functional requirements FR-01..FR-nn using RFC 2119 keywords (MUST/SHOULD/MAY).
- Technical claims need evidence: file:line, command output, or doc citation. No evidence means label it ASSUMPTION visibly.
- Include an explicit Non-Goals section; out-of-scope is stated, never implied.
- Every requirement traces to a source decision (ledger item, mockup, user confirmation).
- Technical PRDs (installation, infrastructure, service or deployable behavior) MUST include a Verification & Acceptance section built from `assets/verification-and-acceptance.md`: V1-V3 levels with trigger rules, Gherkin acceptance scenarios, and an RF-to-verification matrix. A technical requirement that cannot be verified as a real user would experience it is not done.
- Born in DRAFT status. A PRD documents intent; it never authorizes implementation.
- Write the PRD inside the project it describes (docs/ or docs/prds/), or under `ai/<subsystem>/` in dotfiles for cross-project artifacts. Commit only on explicit request.
- PRD language follows the conversation language; keep identifiers and code terms in English.

## Decision Gates

| Situation | Action |
|---|---|
| Open questions affect scope | Interrogate first (grill-me); ledger answers, then draft |
| All key decisions confirmed | Draft directly from the ledger |
| PRD describes a code change | After user approval, hand off to SDD (sdd-propose) with the PRD as input |
| PRD is technical (install/infra/deployable surface) | Include the V1-V3 verification section from `assets/verification-and-acceptance.md` |
| PRD is the deliverable itself (audit, requirements doc) | Deliver the document; no SDD handoff |

## Execution Steps

1. Verify the entry gate: list confirmed decisions and open questions. If open questions affect scope, STOP and interrogate before writing.
2. Draft with the canonical skeleton:
   1. Context & Problem
   2. A. Objectives & Non-Objectives
   3. B. Architecture & Trust Boundaries (phase the pipeline when flow-like)
   4. C. Data Schema & State Machine (FSM when states/transitions exist)
   5. D. Security & Threat Modeling
   6. E. Success Metrics & Acceptance Criteria
   7. FR-xx requirements (RFC 2119) with per-requirement evidence
3. Mark every unresolved point as OPEN QUESTION or ASSUMPTION in the draft body.
4. For technical PRDs, apply `assets/verification-and-acceptance.md`: define the verification levels and their triggers, write Gherkin acceptance scenarios, and fill the RF-to-verification matrix.
5. Deliver: file path, DRAFT status, FR count, open questions, assumptions, verification summary (when technical).
6. If a code change follows, propose the SDD handoff; never start implementation from the PRD alone.

## Output Contract

Return: PRD path + status, FR count, open questions, assumptions, and the recommended next step (sdd-propose when implementation follows).

## References

- `assets/verification-and-acceptance.md` — V1-V3 verification strategy template for technical PRDs.
- `ai/tiktok-ingest/prds/` — canonical exemplar PRDs following this convention.
