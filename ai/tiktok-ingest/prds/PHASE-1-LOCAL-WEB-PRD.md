# PRD — Phase 1: Loopback Local Web Application

**Status:** Planning documentation only. This PRD records product requirements;
it authorizes no implementation, execution, service operation, network, browser
or GPU use, commit, or push.
**Owner:** Bruno. **Date:** 2026-09-23.
**Decision authority:** [ROADMAP.md](../ROADMAP.md) — confirmed decisions are
restated here and are not re-opened.
**Context:** [PRD.md](../PRD.md) (original product requirements),
[MVP-PRD.md](../MVP-PRD.md) (implemented MVP scope),
[README.md](../README.md) and [OPERATIONS.md](../OPERATIONS.md)
(implemented contracts), [Phase 0 PRD](PHASE-0-ONE-COMMAND-PRD.md).

## 1. Outcome

A loopback-only, single-user local web interface replaces the terminal as the
operator's primary surface for watching and driving the collection journey:
collections and inventory completeness, run progress, actionable blocks,
evidence and verification review, and the pending backlog queue with
accept/reject — all served from the same Phase 0 engine and the same local
state, with no second engine anywhere.

## 2. Problem and user outcome

Phase 0 removes inter-command ceremony inside a run, but the operator still
needs terminal fluency: consent prompts, status commands to discover what
happened, manual reconstruction of resume instructions, and a CLI review loop
for the backlog. The information already exists — manifests, fingerprints,
verification trails, receipts — but it is not visible without commands.

**User outcome:** the operator sees the whole journey in one local interface:
what a collection contains, where each video stands, what is blocked and how
to resume it, what the evidence and verdicts say, and which entries await
review. Nothing about the engine's guarantees changes; the surface changes.

## 3. Shared definitions

Kept consistent across all phase PRDs:

- **Processing outcome** (`complete`, `partial`, `blocked`, `unsupported`,
  `failed`, `budget_exceeded`, `not_applicable`) is a stage fact;
  **operator status** (`pending`, `accepted`, `rejected`) is a review fact.
  They are different axes and never mix.
- **Accepted is not confirmed.** Acceptance is the operator's review decision;
  `confirmed` is a claim verdict produced only by the verification matrix.
  The UI MUST present these as separate, labelled facts.
- **A candidate URL is not verified evidence.** It is a proposed first-party
  source recorded in a validated synthesis document; it becomes evidence only
  after bounded retrieval and a mechanical verdict.

## 4. Current behavior vs Phase 1 target

| Area | Today (implemented) | Phase 1 target |
|---|---|---|
| Visibility | Terminal commands (`inventory-status`, `inventory-plan`, `status`, `backlog-list`, `backlog-show`) | Read-only web views over the same state |
| Driving a run | One-command terminal run with terminal consent prompts | Run initiation and consent windows surfaced in the local UI, driving the same application command |
| Blocks and resume | The run prints the requirement and the exact resume command | Blocks appear as actionable items with working resume paths |
| Review | Decide → plan → apply through the review CLI | The same decision semantics and safeguards, operated from the pending queue view |

## 5. Goals and non-goals

**Goals**

- G1: One local interface covers the five required views (Section 8).
- G2: The UI is a client of the same application service; the engine, local
  state, manifests, and fingerprints remain the only authority.
- G3: Loopback-only binding, single user, no remote access path.
- G4: One-writer/job-control semantics are preserved and made visible.
- G5: Existing local state contracts are kept; no database migration.
- G6: Review actions through the UI produce the same durable records and
  safeguards as the review CLI.

**Non-goals** (deferred by [ROADMAP.md](../ROADMAP.md))

- Remote hosting, multiple users, authentication for other users.
- External delivery of entries or adapters to Notion/Gertru (Phase 2).
- A new database unless a demonstrated need appears (real concurrent writers,
  multiple users, or query requirements the current stores cannot support);
  any such migration is a separately documented decision, not a default.
- Changing engine semantics, verdict rules, or consent guarantees.
- Unattended or scheduled runs; automatic decisions.

## 6. Entry conditions

- **Dependency on Phase 0:** the Phase 0 acceptance checklist
  ([Phase 0 PRD](PHASE-0-ONE-COMMAND-PRD.md), Section 12) is satisfied on a
  real authorized collection. The UI wraps a proven one-command engine; it
  does not compensate for a missing one.
- Real local state exists from at least one Phase 0 run (inventory, manifests,
  backlog entries) so the views render genuine data from day one.
- Any execution driven from the UI requires the same explicit operator
  authorization as the terminal path; this document grants none.

## 7. User journey (happy path)

1. The operator starts the local interface; it binds to loopback only.
2. **Collections:** the operator sees each known collection with its
   inventory completeness (declared vs observed counts, end-of-list
   evidence, access-blocked status) and the current processing plan.
3. **Runs:** starting a collection run opens the same consent windows as the
   terminal path, in the interface. As the run proceeds, per-video stage
   progress appears, derived from manifests.
4. **Blocks:** any blocked video or stage shows the reason and the exact
   resume path; resuming asks for fresh consent as always.
5. **Evidence:** for each video the operator can read `video.md`,
   `audio.md`, the synthesis document, and the verification results with
   their mechanical reasons.
6. **Review:** the pending queue lists entries awaiting decision. The
   operator decides per entry (accept or reject with reason), previews the
   planned changes, and applies them — receiving the same receipts and
   idempotency guarantees as the CLI flow.

## 8. Functional requirements

### Engine and state boundary

- **P1-FR-01** The UI MUST be a client of the same application service that
  the CLI uses. It MUST NOT contain a second processing engine, a second
  state authority, or separate fingerprints; every write MUST go through the
  same application commands.
- **P1-FR-02** All displayed progress, stage outcomes, and statuses MUST be
  derived from the product's manifests and state. The UI MAY cache views for
  rendering; a cache is never authority.
- **P1-FR-03** The UI MUST NOT add, rename, or reinterpret processing
  outcomes, operator statuses, verdicts, or taxonomy values; it renders the
  existing vocabulary with its existing meanings.

### Required views

- **P1-FR-04 Collections view:** MUST show each collection's inventory with
  completeness reported honestly — declared count, observed count,
  end-of-list evidence, access-block markers, and completeness status as
  separate facts, plus the current processing plan classification of items.
  "Observed" MUST never be presented as "processed".
- **P1-FR-05 Runs view:** MUST show current and previous runs with
  per-video stage progress and outcomes, batch membership, and which items
  remain pending for later batches.
- **P1-FR-06 Blocks view:** MUST surface every blocked stage, video, or run
  precondition (for example, Whisper lifecycle preconditions or exclusive-
  writer conflicts) with its reason and an actionable resume path that
  matches what the engine actually supports.
- **P1-FR-07 Evidence view:** MUST present, per video, the visual and audio
  evidence documents, the synthesis document with its provenance, and
  verification results including the mechanical reason trail and verdicts,
  keeping stage completeness separate from claim verdicts.
- **P1-FR-08 Pending review view:** MUST list backlog entries with
  `status: pending` and support explicit per-entry accept/reject with a
  required reason on rejection.

### Run driving and consent

- **P1-FR-09** Consent windows surfaced in the UI MUST carry the same
  content guarantees as the terminal path: exact IDs/URLs, operations,
  limits, and effects before anything runs; denial or abandonment stops
  without assuming consent; an authorization never persists into a later
  action; there is no global yes.
- **P1-FR-10** Login/captcha handling and collection-visibility confirmation
  remain operator actions in the headed browser window driven by the engine,
  under the Phase 0 browser boundary. The UI reports that state; it does not
  automate the operator's browsers or credentials.

### Review semantics

- **P1-FR-11** Accept/reject operated from the UI MUST use the same explicit
  decision semantics as the review CLI: decisions bound to the current entry
  content hash, a previewable plan before anything is written, apply-time
  revalidation, backups, receipts, and idempotent re-application. Rejection
  MUST keep its permanent blocklist semantics.
- **P1-FR-12** The UI MUST NOT offer, imply, or perform automatic
  acceptance, rejection, or publication of entries.

### Job control and concurrency

- **P1-FR-13** One writer at a time: the UI MUST prevent concurrent writers
  it can control (for example, starting a run while a review apply is
  pending, or applying decisions while a run is writing) and MUST surface
  the exclusive-writer requirement for anything outside its control. It MUST
  NOT fabricate a product-wide lock that other writers would ignore.
- **P1-FR-14** Interrupted UI sessions lose nothing: state on disk is truth,
  and the UI reconstructs all views from manifests and state on restart.

### Security boundary

- **P1-FR-15** The interface MUST bind to loopback only and MUST NOT expose
  a remote access path. It is single-user by design; the trust boundary is
  the local single-operator machine.
- **P1-FR-16** The UI MUST NOT display or transmit secrets (synthesis
  provider credentials, destination credentials). Configuration of
  credentials remains outside the UI and outside Git.
- **P1-FR-17** The UI MUST NOT create any new outbound path and MUST NOT
  itself transmit data to external systems. The only outbound effects
  permitted are those of the underlying Phase 0 engine inside its separately
  consented, exact windows: sanitized evidence text to the configured
  synthesis provider, and retrieval of exactly the validated candidate URLs.
  Raw media, frames, browser state, and credentials never leave the machine
  through any path the UI participates in.

### Persistence

- **P1-FR-18** The UI MUST NOT introduce a database migration. Local state
  contracts remain as documented; if a demonstrated need for different
  storage arises, that is a separate documented decision with its own
  migration plan, not part of this phase.

### Accessibility and responsiveness

- **P1-FR-19** Where concretely applicable, queue actions and consent
  actions MUST be operable by keyboard, text MUST remain readable at common
  desktop sizes, and controls MUST carry functional labels. No broader
  compliance claim is made or required by this phase.

## 9. State, idempotency, recovery, failure policy

- All engine state, idempotency, and recovery guarantees are inherited
  unchanged from Phase 0 and the existing contracts; the UI adds no
  authoritative state.
- Review operations through the UI are idempotent in exactly the CLI sense:
  re-applying confirmed decisions is a no-op with a receipt; a decision whose
  bound entry hash no longer matches aborts with zero writes.
- If the UI process dies mid-run, the underlying run follows the engine's
  interruption semantics (Phase 0 PRD, Section 10); the UI restarts into an
  honest reconstruction, never a cached promise.

## 10. UX and operational behavior

- The five views are the primary navigation; each links to the underlying
  evidence rather than paraphrasing it (an operator can always reach the
  actual `video.md`/`audio.md`/verification content).
- Blocks are first-class citizens: a block without a working resume path is a
  defect, not a state.
- Destructive or consequential actions (apply decisions, consent windows)
  require an explicit confirmation step and report their receipts.
- The UI MUST present processing outcomes and operator status as separate,
  labelled facts, and MUST NOT render verdicts as review states or vice
  versa.

## 11. Acceptance criteria

Demonstrated on real local state from at least one authorized Phase 0 run; no
accuracy or satisfaction percentages are claimed.

- [ ] All five required views render genuine local state: collections with
      honest completeness, runs with per-video stage progress, blocks with
      working resume paths, evidence with synthesis and verification trails,
      and the pending queue.
- [ ] A collection run started from the UI reaches `pending` backlog entries
      with consent windows carrying the same content as the terminal path.
- [ ] Accept/reject through the UI produces the same decision records,
      backups, and receipts as the CLI flow; re-applying confirmed decisions
      is a no-op; a changed entry between preview and apply aborts with zero
      writes.
- [ ] Job control: an attempt to run concurrent writers under UI control is
      refused with an explanation; exclusive-writer conflicts with external
      writers are surfaced, not silently ignored.
- [ ] No second authority: for a sampled video and entry, the UI's view
      matches the CLI status and backlog reports exactly.
- [ ] The service is unreachable from non-loopback interfaces (verified by a
      concrete binding check on the target machine).
- [ ] The UI itself transmits nothing and creates no new outbound path: the
      only outbound effects during a UI-driven run are the engine's existing,
      separately consented synthesis and exact-URL verification windows; raw
      media, frames, and browser state never leave the machine. No secret is
      ever displayed by the interface.
- [ ] An interrupted UI session restarts into state reconstructed from disk,
      with no lost or fabricated progress.

## 12. Risks and unresolved decisions

Confirmed decisions are not re-opened; these are genuine open points.

| Risk / open point | Notes |
|---|---|
| Consent parity on a graphical surface | Terminal consent guarantees (explicitness, non-persistence, exact contents) must hold in the UI. The interaction design is open; the content guarantees are not. |
| Long-running progress refresh | Stage progress updates during a run need a refresh approach. Minimal is preferred; the design decision is open and MUST NOT introduce a second progress authority. |
| Residual local-machine trust | Loopback binding excludes the network, not other local processes or people at the machine. This is the accepted single-operator boundary of this phase, to be documented, not solved with authentication here. |
| Storage evolution pressure | If real concurrent writers, multiple users, or unsupported query needs appear, a storage change becomes its own documented decision with migration guarantees. Premature migration stays out. |
| Accessibility scope | Only the concrete items in P1-FR-19 are required. Broader accessibility work is deferred, not silently assumed done. |

## 13. Handoff to Phase 2

Phase 1 exits when the acceptance checklist above is satisfied. Phase 2
(destination adapters) depends on explicitly accepted local entries produced
by the established review flow — from either the CLI or the UI — and MUST NOT
block Phase 1 operation (see [Phase 2 PRD](PHASE-2-DESTINATIONS-PRD.md)).

## 14. References

- [ROADMAP.md](../ROADMAP.md) — phase decisions and deferral list.
- [Phase 0 PRD](PHASE-0-ONE-COMMAND-PRD.md) — engine this UI wraps.
- [PRD.md](../PRD.md) — verdict matrix, taxonomy, state contracts.
- [OPERATIONS.md](../OPERATIONS.md) — review semantics, concurrency
  guarantees and limits the UI must preserve.
- [Phase 2 PRD](PHASE-2-DESTINATIONS-PRD.md).
