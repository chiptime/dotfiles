# PRD — Phase 0: One Collection, One Command

**Status:** Planning documentation only. This PRD records product requirements;
it authorizes no implementation, execution, service operation, network, browser
or GPU use, commit, or push.
**Owner:** Bruno. **Date:** 2026-09-23.
**Decision authority:** [ROADMAP.md](../ROADMAP.md) — confirmed decisions are
restated here and are not re-opened.
**Context:** [PRD.md](../PRD.md) (original product requirements),
[MVP-PRD.md](../MVP-PRD.md) (implemented MVP scope),
[README.md](../README.md) and [OPERATIONS.md](../OPERATIONS.md)
(implemented contracts).

## 1. Outcome

One executable command takes a single, explicitly selected TikTok collection
URL through browser inventory, media processing, automatic text synthesis,
bounded verification, and local backlog emission as `pending` entries:

```bash
./run-collection.sh "https://www.tiktok.com/@author/collection/name-id"
```

In the normal path the operator's manual role shrinks to three things:
handling login/captcha in a visible browser, confirming sensitive windows
(collection visibility, consent, exact retrieval destinations), and performing
final backlog review. Stage sequencing, state reuse, batching, resource
coordination, and failure reporting are coordinated by the application behind
the existing consent gates.

## 2. Problem and user outcome

Today a collection journey requires the operator to execute and interleave
many commands (offline inventory merges, plan inspection, consent-gated stage
runs, hand-supplied synthesis documents, backlog review). Each command is
safe and resumable, but the ceremony is the cost: attention goes to process,
not to reviewing entries.

**User outcome:** with one command and bounded human checkpoints, a selected
collection becomes evidence-backed `pending` backlog entries, with failures
visible and resumable, and nothing accepted, rejected, or published
automatically.

## 3. Shared definitions

Kept consistent across all phase PRDs:

- **Processing outcome** (`complete`, `partial`, `blocked`, `unsupported`,
  `failed`, `budget_exceeded`, `not_applicable`) is a stage fact;
  **operator status** (`pending`, `accepted`, `rejected`) is a review fact.
  They are different axes and never mix.
- **Accepted is not confirmed.** Acceptance is the operator's review decision;
  `confirmed` is a claim verdict produced only by the verification matrix.
- **A candidate URL is not verified evidence.** It is a proposed first-party
  source recorded in a validated synthesis document; it becomes evidence only
  after bounded retrieval and a mechanical verdict.

## 4. Current behavior vs Phase 0 target

| Area | Today (implemented) | Phase 0 target |
|---|---|---|
| Collection inventory | Offline merge of operator-captured HTML snapshots; the live browser scan is not implemented | The application drives a headed, run-scoped Playwright browser for inventory; the operator handles login/captcha and confirms visibility |
| Synthesis | Operator-supplied document via `--from-file`; the pipeline makes no model calls | One OpenAI-compatible adapter produces the synthesis document automatically, validated by the same strict contract |
| Shared Whisper service | Operator-managed; runs block with instructions when preconditions (stopped for vision, healthy for audio) are unmet | The application offers bounded stop/restore of the known shared Whisper service inside the GPU window — each mutation only after exact consent and positive identity/config verification, with restored health validated; otherwise it fails closed with instructions |
| Coordination | Guided coordination of one batch behind consent windows | `collection-run` sequences the whole collection journey in batches of at most five, still behind explicit per-window consent |

## 5. Goals and non-goals

**Goals**

- G1: One command covers the normal path from collection URL to `pending`
  backlog entries.
- G2: The browser is used only for inventory, in a run-scoped isolated
  profile, and is closed before media processing.
- G3: Existing stages, fingerprints, and local state remain the single
  authority; the application coordinates, it does not reimplement.
- G4: Eligible candidates are processed in batches of at most five.
- G5: Automatic synthesis through one provider-neutral OpenAI-compatible
  adapter that sends only sanitized evidence text.
- G6: Failures are visible and resumable; classifications are never invented.
- G7: Output is local and `pending` only.

**Non-goals** (deferred by [ROADMAP.md](../ROADMAP.md))

- Web interface (Phase 1) and destination adapters (Phase 2).
- Unattended or scheduled runs; multi-user or remote hosting.
- Automatic acceptance, rejection, or publication.
- Other platforms, comments, photo-slide ingestion, bilingual processing.
- Cloud processing of raw media.
- A new database before demonstrated need.
- Provider-specific business logic in the core pipeline.

## 6. Entry conditions

- The processing engine, state contracts, guided coordination, and review CLI
  exist as documented in [README.md](../README.md) and
  [OPERATIONS.md](../OPERATIONS.md).
- Any real trial (browser, network, GPU, service operations) requires
  separate explicit operator authorization. This document grants none.

## 7. Operator journey (happy path)

1. The operator runs `./run-collection.sh "<collection-url>"`.
2. The command prints a preflight: dependencies, local state, exclusive-writer
   condition, storage, GPU capacity, service identity, and the planned
   external destinations of this run.
3. A headed Playwright browser opens with a dedicated run-scoped profile.
4. The operator completes login and captcha manually, then explicitly
   confirms that the selected collection is visible.
5. The application captures collection-scoped evidence, persists the
   inventory and scan state, closes the browser context, and removes the
   run-scoped profile.
6. The processing plan is recomputed from current inventory, processed state,
   and blocklist; eligible videos are processed in batches of at most five.
7. Each batch proceeds through explicit consent windows: network download of
   exactly the selected URLs, CPU preparation, the GPU window (isolated
   Ollama lifecycle, gated vision, Whisper stop/restore coordination, audio),
   automatic synthesis, and verification retrieval of exactly the validated
   candidate URLs.
8. Successful results are appended to the local backlog as `pending`. The run
   ends with a concise batch summary and the existing review commands.

## 8. Functional requirements

### Launcher

- **P0-FR-01** `run-collection.sh` MUST be a thin, fail-fast launcher that
  dispatches `python -m tiktok_ingest collection-run <collection-url>`. All
  product logic MUST live in the tested Python application command.
- **P0-FR-02** `collection-run` MUST reject ambiguous input (missing URL,
  non-collection URL) before producing any effect.

### Browser boundary

- **P0-FR-03** The application MUST open a headed browser through Playwright
  with a dedicated run-scoped, isolated profile. It MUST NOT inspect, copy,
  or automate the operator's default browser profile, ambient sessions, or
  cookies.
- **P0-FR-04** Inventory MUST NOT start until the operator has completed
  login/captcha handling and has explicitly confirmed that the selected
  collection is visible.
- **P0-FR-05** Inventory MUST apply the existing collection-scoped DOM and
  inventory rules: recommendation links never become collection members;
  declared count, observed count, end-of-list evidence, access-block markers,
  and completeness status are recorded separately and never inferred from one
  another.
- **P0-FR-06** The application MUST persist the inventory evidence required
  for audit and recovery, close the browser context on success, handled
  failure, or interruption, and remove the run-scoped profile. After abrupt
  process termination, cleanup state MUST be reported as uncertain rather
  than assumed.

### Collection coordination

- **P0-FR-07** The processing plan MUST be recomputed at invocation time from
  current inventory, processed state, and blocklist. Completed and permanently
  rejected IDs MUST be skipped; eligible candidates MUST be processed in
  batches of at most five; remaining items stay pending for later batches.
- **P0-FR-08** The application MUST preserve the exclusive-writer operational
  boundary for inventory, run manifests, and backlog state changes; it MUST
  NOT run concurrently with other writers.
- **P0-FR-09** The application MUST reuse the existing stage functions and
  fingerprints and MUST NOT introduce a second job ledger. Progress reporting
  MUST be a summary derived from the product's manifests, never a state
  authority.
- **P0-FR-10** The isolated Ollama lifecycle MUST remain scoped as today:
  started and stopped by the run that started it, identity-verified, never
  adopting a preexisting server answering on the isolated port, all under
  explicit consent.
- **P0-FR-11** The application MUST offer bounded management of the known
  shared Whisper service — stop for vision, restore for audio — so the normal
  one-command path requires no operator service commands. Each mutation
  remains conditional: a stop or restore is performed only after an explicit
  bounded consent naming the service and operation, and only after the
  target's identity and configuration are positively verified against the
  known shared service; after restore, health MUST be positively validated
  and the same service and configuration returned, after every affected
  batch including failure paths. Failure handling distinguishes phases. If
  consent is denied, or identity or configuration verification fails, the
  application MUST block BEFORE any mutation: no mutation occurs. If
  restoration or restored-health validation fails after an authorized stop,
  the application MUST block all further progress, report the actual partial
  service state with recovery instructions, make no claim of restoration,
  and perform no further service or pipeline mutation except bounded cleanup
  or recovery already covered by the standing authorization; no rollback is
  guaranteed or implied. No arbitrary or unidentified workload is ever
  stopped, and no implicit service authority exists: capability is
  mandatory, each mutation is conditional.
- **P0-FR-12** A fired resource gate or any non-complete vision outcome MUST
  end the GPU window for the remaining IDs of the batch. Gates are never
  retried automatically.

### Automatic synthesis

- **P0-FR-13** The application MUST provide exactly one provider-neutral
  synthesis adapter speaking an OpenAI-compatible protocol. Base URL, model,
  API key, timeouts, and text limits MUST be configurable at runtime;
  credentials MUST live outside Git and MUST be scrubbed from reports,
  exceptions, and logs.
- **P0-FR-14** The adapter MUST send only sanitized evidence text derived
  from `video.md` and `audio.md` (including their labelled metadata
  sections). It MUST NOT send video, audio, frames, local filesystem paths,
  browser state, or credentials.
- **P0-FR-15** The provider request MUST disable tool use, arbitrary
  provider-side URL retrieval, and command execution.
- **P0-FR-16** The adapter MUST request structured JSON output, and the
  result MUST pass the existing strict synthesis-document validation (the
  `SynthesisDocument` contract) before anything is persisted. Provider and
  model identity, and a content-bound synthesis fingerprint, MUST be
  recorded.
- **P0-FR-17** Timeout, provider refusal, malformed output, and unavailable
  credentials MUST produce a resumable synthesis stop. The application MUST
  never manufacture, complete, or auto-fill a classification; failure is
  never recorded as an empty success.

### Verification and local delivery

- **P0-FR-18** Verification MUST retrieve only the explicit, safe
  `candidate_urls` recorded in validated synthesis documents, and only after
  the operator has consented to the exact destinations. Existing safe-URL
  rules (public HTTP(S) only, per-hop redirect validation, bounded retrieval)
  remain unchanged.
- **P0-FR-19** Every successful result MUST be appended to the local backlog
  with `status: pending`. The application MUST NOT accept, reject, publish,
  or route entries to any external system. Accept/reject remains the
  operator's explicit review decision through the existing flow, and
  rejection keeps its permanent blocklist semantics.

## 9. Consent, security, and privacy

- Every external effect — network download, container preparation, GPU
  window, Whisper stop/restore, synthesis API call, verification retrieval —
  MUST sit behind an explicit consent window showing the exact IDs/URLs,
  operations, limits, and effects before anything runs. Denial, EOF,
  cancellation, or a non-interactive environment MUST stop without assuming
  consent. There is no global yes; an authorization never persists into a
  later invocation or window.
- Browser credentials stay inside the browser boundary: run-scoped profile
  only, removed at the end, never mounted, exported, or read by the
  extractor.
- Media stays local: video, audio, and frames never leave the machine.
- Outbound traffic is limited to sanitized evidence text for synthesis and to
  exact candidate-URL retrieval for verification.
- Service lifecycle authority is bounded to the named known services
  (isolated Ollama; the known shared Whisper service after identity check).
  Stopping arbitrary workloads is forbidden.
- Secrets never enter Git and never appear in reports or errors.

## 10. State, idempotency, recovery, failure policy

- The current local state tree, manifests, and stage fingerprints remain the
  only authority; the application adds no parallel state.
- Re-running the unchanged command on an already-processed collection MUST
  perform zero unnecessary downloads and zero audiovisual re-inference;
  backlog emission stays duplicate-guarded by stable video ID.
- Interruption: valid completed stages are reused on resume; an abrupt kill
  is recovered from the evidence of real state (status output, fingerprints,
  recorded state files), never from promises; re-invocation requires new
  authorization for pending operations.
- Provider failure: a synthesis stop is resumable at the synthesis stage
  without redoing vision or audio.
- Whisper restoration failure MUST block further progress visibly with the
  affected state reported; it never silently degrades into a bypass.
- Stage failures keep the implemented outcome vocabulary, each with a reason.

## 11. UX and operational behavior

- Within a run, the operator performs no normal technical command between
  stages; intervention is limited to login/captcha, confirmations, and
  consents.
- The preflight and every consent window MUST name exactly what will happen,
  in a readable terminal presentation.
- The run summary MUST report per-video stage outcomes, honest inventory
  completeness, blocks with their reasons, and the next review commands.
- The exclusive-writer requirement MUST be stated at preflight: no other
  writers may run concurrently.

## 12. Acceptance criteria

All items require one separately authorized real trial on a small, explicitly
authorized collection. No accuracy percentages are claimed or implied.

- [ ] One command produces `pending` backlog entries for a real small
      collection.
- [ ] The browser is used only for inventory and is closed before media
      processing; the run-scoped profile is removed.
- [ ] Recommendation links are excluded and inventory completeness is
      reported honestly (declared vs observed vs end-of-list evidence).
- [ ] The operator performs no normal technical command between stages.
- [ ] Vision and Whisper never overlap in GPU residency; isolated Ollama
      cleanup and shared Whisper restoration are positively checked (empty
      residency plus observed GPU release).
- [ ] Only sanitized evidence text reaches the configured text API —
      confirmed by inspecting the outbound payload in a controlled check:
      no media, frames, local paths, or browser state.
- [ ] A provider failure (timeout or malformed output) leaves resumable
      state, produces no backlog entry for the affected ID until resolved,
      and never an invented classification; no duplicate entries appear
      after recovery.
- [ ] A process interruption mid-run resumes without duplicate entries and
      without redoing completed stages.
- [ ] Re-running the same collection performs zero unnecessary downloads and
      zero audiovisual inference.
- [ ] All emitted entries remain local and `pending` until explicit operator
      review.

## 13. Risks and unresolved decisions

Confirmed decisions are not re-opened; these are genuine open points.

| Risk / open point | Notes |
|---|---|
| TikTok DOM volatility | Collection-scoped selectors remain the highest-maintenance component. A changed DOM must fail explicitly, never yield a fake empty inventory. |
| Login friction of the run-scoped profile | A run-scoped profile may require login on every run. Whether a longer-lived dedicated (still isolated) profile is acceptable is a separate future decision; the default stays run-scoped. |
| Whisper stop/restore escalation | Historical stops required escalating termination signals. Graceful termination, bounded escalation, and restoration-failure reporting must be defined and tested when this capability is implemented. |
| Provider JSON discipline | OpenAI-compatible endpoints vary in structured-output reliability. Failures remain resumable stops; prompt and configuration tuning is operational work, never a reason to weaken validation. |
| Enumeration accuracy | Observed counts below declared counts are honest outcomes. Acceptance requires visible completeness reporting, not full enumeration. |

## 14. Handoff to Phase 1

Phase 0 exits when the acceptance checklist above is satisfied on a real
authorized collection. Phase 1 (local web application) begins only after that
acceptance, per [ROADMAP.md](../ROADMAP.md). The handoff carries: a stable
application command surface over the engine, unchanged local state contracts,
and proven resume behavior.

## 15. References

- [ROADMAP.md](../ROADMAP.md) — phase decisions and deferral list.
- [PRD.md](../PRD.md) — original requirements: evidence boundaries, verdict
  matrix, taxonomy, state contracts, failure policy.
- [MVP-PRD.md](../MVP-PRD.md) — implemented MVP scope, resource gates,
  bounded operating limits.
- [README.md](../README.md) / [OPERATIONS.md](../OPERATIONS.md) — current
  implemented commands and operational procedures.
- [Phase 1 PRD](PHASE-1-LOCAL-WEB-PRD.md) / [Phase 2 PRD](PHASE-2-DESTINATIONS-PRD.md).
