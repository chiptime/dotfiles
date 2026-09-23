# PRD — Phase 2: Accepted-Entry Destination Adapters

**Status:** Planning documentation only. This PRD records product requirements;
it authorizes no implementation, execution, service operation, network, browser
or GPU use, commit, or push.
**Owner:** Bruno. **Date:** 2026-09-23.
**Decision authority:** [ROADMAP.md](../ROADMAP.md) — confirmed decisions are
restated here and are not re-opened.
**Context:** [PRD.md](../PRD.md) (original product requirements; manual
handoff position), [MVP-PRD.md](../MVP-PRD.md), [README.md](../README.md) and
[OPERATIONS.md](../OPERATIONS.md) (implemented contracts),
[Phase 0 PRD](PHASE-0-ONE-COMMAND-PRD.md), [Phase 1 PRD](PHASE-1-LOCAL-WEB-PRD.md).

## 1. Outcome

Explicitly accepted local entries can be delivered to optional external
destinations (for example Notion or Gertru) through adapters that carry their
own destination authorization, preview their exact mapping, deliver with
idempotency keys and durable receipts, retry safely after partial failure, and
keep all destination state strictly separate from ingest and review state.

## 2. Problem and user outcome

Today, acceptance is a dead end in the tooling: the operator must copy accepted
entries into the destination system by hand. Manual handoff is unrecorded (no
receipt of what was delivered where), non-idempotent (a repeated manual copy
duplicates content), and unrecoverable (nothing reconciles local and
destination state after a failure). At the same time, delivery must not couple
into the pipeline: the core ingests and the operator reviews; destinations are
a separate concern.

**User outcome:** the operator explicitly selects accepted entries and a
destination, authorizes that destination, previews exactly what will be
created there, and delivers with confidence: retries never duplicate, partial
batches are recoverable, every delivery has a receipt, and nothing at the
destination is ever silently updated or deleted.

## 3. Shared definitions

Kept consistent across all phase PRDs:

- **Processing outcome** (`complete`, `partial`, `blocked`, `unsupported`,
  `failed`, `budget_exceeded`, `not_applicable`) is a stage fact;
  **operator status** (`pending`, `accepted`, `rejected`) is a review fact.
- **Accepted is not confirmed.** Acceptance is the operator's review decision;
  `confirmed` is a claim verdict produced only by the verification matrix.
  A delivered entry carries its recorded verdicts unchanged; delivery never
  re-verifies, upgrades, or reinterprets them.
- **A candidate URL is not verified evidence.** Delivering or rendering a
  link does not create, upgrade, or alter verification evidence or verdicts;
  evidence and verdicts exist only as produced by the bounded verification
  flow, and delivery carries them unchanged.

## 4. Current behavior vs Phase 2 target

| Area | Today (implemented) | Phase 2 target |
|---|---|---|
| Downstream use of accepted entries | Manual, operator-controlled copy; the pipeline never writes to external systems | Optional adapters deliver explicitly selected accepted entries, per-destination authorized, with receipts |
| Delivery record | None (manual handoff is unrecorded) | Durable per-entry receipts with idempotency keys, locally stored |
| Retry/recovery | Not applicable (manual) | Safe retry of failed entries without re-delivering succeeded ones; reconciliation against destination state |
| Destination state | Does not exist in the product | A separate local delivery state, never mixed into backlog/blocklist/manifests |

## 5. Goals and non-goals

**Goals**

- G1: Only explicitly accepted entries are deliverable, only under explicit
  operator selection.
- G2: Each destination is reached through an adapter implementing a minimal
  behavioral contract (eligibility, authorization, mapping preview,
  idempotent delivery, durable receipt, outcome report).
- G3: Every delivery is retry-safe and reconcilable; partial batches are a
  supported, visible outcome.
- G4: Destination state stays separate from ingest and review state;
  destination failures never contaminate the backlog, blocklist, or run
  manifests.
- G5: Credentials for destinations live outside Git, are scope-limited where
  the destination supports it, and never appear in reports or logs.

**Non-goals** (deferred by [ROADMAP.md](../ROADMAP.md))

- Delivery of `pending` or `rejected` entries under any circumstance.
- Automatic publication, automatic destination selection, or scheduled
  delivery.
- Destination-specific business logic in the core pipeline; adapters are a
  separate layer.
- Silent update or deletion of destination content; deletion semantics
  generally.
- Synchronizing destination state back into backlog status; `accepted`
  remains a review fact, and delivery state is a separate axis.
- Cloud processing of raw media; delivery of media, frames, or raw evidence
  artifacts (entries' reviewed content is delivered as mapped and previewed,
  nothing more).

## 6. Entry conditions

- **Dependency on accepted entries:** locally accepted entries exist,
  produced through the established review flow (decide → plan → apply, CLI or
  Phase 1 UI). Without accepted entries there is nothing eligible to deliver.
- Phase 0 acceptance is complete. Phase 1 is not a dependency: adapters
  consume accepted local entries regardless of whether review happened
  through the CLI or the UI. Phase 1 MAY expose delivery flows later; Phase 2
  remains separately operable and MUST NOT block Phase 1 operation.
- Each destination requires its own explicit authorization from the operator
  before any delivery, and before any adapter is even configured with real
  credentials. This document grants none.

## 7. Operator journey (happy path)

1. The operator opens delivery, selects a destination, and explicitly selects
   a batch of accepted entries for it.
2. The operator grants destination authorization: the consent names the
   destination, the credential scope, and the exact entries in the batch.
3. The operator reviews the mapping preview: for each entry, the exact
   representation that will be created at the destination — fields, verdict
   context, evidence links. Delivery does not start until the preview is
   confirmed.
4. Delivery runs per entry; each entry ends delivered or failed, with a
   durable receipt recorded locally for every attempt and outcome.
5. On partial failure, the operator sees which entries succeeded and which
   failed, and can retry exactly the failed entries without re-delivering the
   succeeded ones.
6. Later, if a delivered entry's acceptance is revoked or its local content
  changes, delivery surfaces the divergence; the operator decides explicitly
  what, if anything, to do. Nothing at the destination changes silently.

## 8. Functional requirements

### Eligibility and selection

- **P2-FR-01** Only entries with operator status `accepted` are eligible for
  delivery. Entries with status `pending` or `rejected` MUST NOT be
  selectable or deliverable, and rejection's permanent blocklist semantics
  are unaffected by any prior delivery.
- **P2-FR-02** Every delivery batch MUST consist of explicitly operator-
  selected entries and an explicitly selected destination. There is no
  deliver-everything default and no automatic destination choice.
- **P2-FR-03** Delivered-at-destination is a delivery-layer fact recorded in
  delivery state. It MUST NOT become an ingest or review status, and it MUST
  NOT modify the backlog entry.

### Adapter contract

- **P2-FR-04** Each destination adapter MUST implement the same behavioral
  contract: check eligibility; obtain destination authorization; produce a
  mapping preview; deliver with an idempotency key; record a durable receipt;
  report per-entry outcomes. The contract is behavioral — adapters MAY differ
  in destination mechanics, not in guarantees.
- **P2-FR-05** The core pipeline MUST NOT gain destination-specific logic.
  Adapters are a separate layer consuming accepted entries and delivery
  state; adding a destination MUST NOT change ingest, review, or verification
  behavior.

### Authorization and secrets

- **P2-FR-06** Destination authorization MUST be granted per destination and
  per batch, naming the destination, the credential scope, and the exact
  entries. An authorization never persists into a later batch and is
  revocable. Denial stops delivery without assuming consent.
- **P2-FR-07** Destination credentials MUST live outside Git, be configured
  at runtime, be scope-limited to what the destination supports for this
  purpose, and be scrubbed from reports, receipts, and exceptions.

### Mapping preview

- **P2-FR-08** Before any delivery, the operator MUST see, per entry, the
  exact destination representation — which fields are delivered, how
  classification, entities, claims with verdicts, fit, actionable text, and
  evidence links map — and MUST explicitly confirm it. Hidden or implicit
  field mapping is prohibited. A change in mapping constitutes a new mapping
  version requiring fresh confirmation.

### Idempotency, receipts, retry

- **P2-FR-09** Each delivery attempt MUST carry a stable idempotency key
  derived from entry identity, destination, and mapping version. Re-delivery
  with the same key MUST NOT create duplicate destination content. That
  guarantee MUST hold through one of: the destination's own idempotent-create
  support, or a recoverable create protocol that identifies an unreceipted
  prior create before any retry — a deterministic destination-side identifier
  derived from the key plus an authorized post-create read-back that commits
  the receipt. A receipt pre-check alone is inadmissible (a crash between
  creation and receipt commit would duplicate on retry); a destination
  offering neither mechanism is refused.
- **P2-FR-10** Every delivery attempt MUST produce a durable receipt locally:
  entry identity, destination, mapping version, idempotency key, outcome,
  timestamp, and the destination-side identifier when the destination
  provides one. Receipts live outside Git and survive restarts.
- **P2-FR-11** Partial batches MUST be supported: outcomes are per-entry;
  failed entries are retryable without re-delivering succeeded ones; a crash
  mid-batch leaves receipts that state exactly what was delivered.
- **P2-FR-12** Reconciliation MUST be available: through authorized reads,
  compare destination-side identifiers against local receipts and report
  divergence. Reconciliation is read-only; fixing divergence is an explicit
  operator action.

### Divergence and revocation semantics

- **P2-FR-13** If a delivered entry's acceptance is later revoked or its
  local content changes after delivery, the adapter MUST surface the
  divergence. It MUST NOT silently update, re-deliver over, or delete
  destination content; resolution is always an explicit operator decision,
  recorded as such.
- **P2-FR-14** No adapter operation may delete or overwrite destination
  content without a separately explicit, per-item operator instruction naming
  the destination item. Silent deletion or update is prohibited in all
  paths, including reconciliation and retry.

### Failure isolation

- **P2-FR-15** Destination failures (unavailable, unauthorized, rate-limited,
  partial) MUST NOT alter ingest state, backlog entries or statuses, the
  blocklist, run manifests, or review records. Delivery state is recorded in
  its own store and never written into the ingest/review state tree.
- **P2-FR-16** Delivery state MUST NOT gate ingestion or review: runs,
  review, and the Phase 1 UI continue operating normally while a destination
  is failed or unconfigured.

## 9. Consent, security, and privacy

- What leaves the machine is exactly the previewed, mapped content of the
  selected accepted entries — no media, no frames, no raw evidence
  artifacts, no local paths, no browser state, no credentials.
- Destination credentials follow the same secret rules as synthesis
  credentials: outside Git, runtime-configured, scrubbed from all durable
  output.
- Destination authorization is bounded and non-persistent, consistent with
  the product principle that a previous authorization never authorizes
  another batch, destination, or credential use.

## 10. State, idempotency, recovery, failure policy

- Delivery state (receipts, idempotency keys, divergence reports) is a
  separate local store with atomic writes; it is never merged into backlog,
  blocklist, inventory, or run manifests.
- Recovery position: after any interruption, receipts are the statement of
  what happened; retry decisions start from receipts, never from memory or
  assumption. An unreceipted prior create is identified through the recoverable
  create protocol's read-back before any retry, never assumed absent.
- Repeated delivery attempts of an unchanged (entry, destination, mapping)
  triple converge to a single destination representation — duplicates are a
  defect, not an accepted risk.
- Destination outage is a visible, retryable condition; it never blocks or
  contaminates ingest/review operation.

## 11. UX and operational behavior

- Delivery is always a deliberate flow: select → authorize → preview →
  confirm → deliver; each step names its exact scope.
- Batch results are per-entry and honest: delivered, failed with reason, or
  skipped with reason; receipts are inspectable by the operator.
- Divergence reports state what changed locally versus what exists at the
  destination, and offer explicit resolution actions — never automatic ones.
- Whether and how delivery state becomes visible in the Phase 1 UI is a
  later integration decision; it MUST NOT couple the engines or turn
  delivery facts into review statuses.

## 12. Acceptance criteria

Demonstrated with a small, explicitly authorized batch on one real
destination; no accuracy or satisfaction percentages are claimed.

- [ ] Only accepted entries are selectable; pending and rejected entries are
      refused with an explanation.
- [ ] A delivered batch produces a durable local receipt per entry, including
      idempotency key and destination-side identifier where provided.
- [ ] Re-running the same batch (same entries, destination, mapping version)
      creates zero duplicate destination content.
- [ ] An injected partial failure (one entry fails) leaves succeeded entries
      undisturbed; retrying the failed entry succeeds without re-delivering
      the succeeded ones; receipts reflect both attempts accurately.
- [ ] A destination outage or authorization denial leaves backlog, blocklist,
      inventory, and run manifests byte-identical to before the attempt.
- [ ] Revoking an entry's acceptance after delivery (or editing its local
      content) surfaces a divergence report; no destination content is
      silently updated or deleted.
- [ ] Mapping preview precedes every delivery, and a mapping change requires
      fresh confirmation before delivering again.
- [ ] A secret scan of the repository and of receipts/reports finds no
      destination credentials.

## 13. Risks and unresolved decisions

Confirmed decisions are not re-opened; these are genuine open points.

| Risk / open point | Notes |
|---|---|
| Destination capability variance | Idempotent create support, rate limits, read-back fidelity, and identifier stability differ per destination. Where a destination offers no idempotency primitive, a recoverable create protocol (deterministic identifier plus post-create read-back receipt commit) carries the guarantee; a receipt pre-check alone does not, because an unreceipted create after a crash would duplicate on retry. |
| Adapter feasibility order | Notion and Gertru are examples, not commitments. Gertru has historically not been CLI-invocable; whether a Gertru adapter is feasible at all is an open decision. Which destination ships first is undecided. |
| Mapping fidelity and drift | Backlog entry shape will evolve; mapping versions must make delivered content auditable and prevent stale mappings from silently re-delivering changed content. |
| Reconciliation cost | Authorized reads for reconciliation may be rate-limited or paginated; reconciliation scope is per explicit operator request, never background sweeps. |
| Explicit update paths | This phase defines no update/delete semantics beyond "explicit, per-item operator instruction". Whether richer revision flows are ever needed is deferred. |
| Phase 1 visibility of deliveries | Showing delivery state in the Phase 1 UI is desirable later, but the integration point is an open decision that must not couple engines or mix delivery facts into review statuses. |

## 14. Handoff and exit

Phase 2 exits when the acceptance criteria above hold for at least one real
destination adapter. Everything deferred by [ROADMAP.md](../ROADMAP.md)
(unattended runs, multi-user hosting, automatic decisions or publication,
other platforms, and the rest) remains deferred; no Phase 2 capability
reopens those decisions. If further destinations are added later, each is a
new adapter under this same contract with its own authorization and proofs.

## 15. References

- [ROADMAP.md](../ROADMAP.md) — phase decisions and deferral list.
- [PRD.md](../PRD.md) — original manual-handoff position this phase replaces
  for accepted entries, and the verdict/taxonomy contracts delivered content
  must preserve.
- [OPERATIONS.md](../OPERATIONS.md) — review semantics producing the accepted
  entries adapters consume; concurrency limits.
- [Phase 0 PRD](PHASE-0-ONE-COMMAND-PRD.md) — engine and state authority.
- [Phase 1 PRD](PHASE-1-LOCAL-WEB-PRD.md) — review UI that MAY surface
  delivery later; not a dependency of this phase, and Phase 2 must not block
  its operation.
