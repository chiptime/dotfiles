# TikTok Ingest Application Roadmap

Build the smallest operator-facing application that turns one explicitly selected
TikTok collection into verified, classified entries in the local review backlog.
The application should remove operational ceremony without weakening the existing
consent, evidence, resource, or resume guarantees.

## Current foundation

The processing engine already provides:

- collection inventory and current-state planning;
- bounded fetch, CPU preparation, local vision, and local transcription stages;
- content-bound fingerprints and resumable stage state;
- strict ingestion of synthesis documents and bounded source verification;
- atomic local backlog emission and explicit accept/reject tooling;
- consent-gated guided execution with fixture-only coverage.

This foundation remains the single processing engine. The roadmap adds an
application layer around it; it does not reimplement the stages.

## Product principles

1. **One operator action starts the normal path.** Human intervention should be
   limited to login/captcha handling, sensitive consent, and final review.
2. **Media stays local.** Video, audio, and frames never leave the machine.
3. **Only sanitized evidence text may reach a text API.**
4. **Every external effect is explicit and bounded.** A previous authorization
   never authorizes another run, collection, destination, or resource window.
5. **Resume instead of repeat.** Valid completed stages are reused without new
   downloads or audiovisual inference.
6. **The local backlog is the Phase 0 destination.** Every new entry is emitted as
   `pending`; nothing is accepted, rejected, or published automatically.

## Phase 0 — One collection, one command

**Outcome:** one executable command takes a collection URL through browser
inventory, processing, synthesis, verification, and local backlog emission.

Phase requirements: [Phase 0 PRD](prds/PHASE-0-ONE-COMMAND-PRD.md).

```bash
./run-collection.sh "https://www.tiktok.com/@author/collection/name-id"
```

The shell script is only a stable launcher. Product logic belongs in a tested
Python application command:

```text
run-collection.sh
  -> python -m tiktok_ingest collection-run <collection-url>
```

### Operator journey

1. Run the command with one collection URL.
2. Review the preflight: dependencies, local state, exclusive-writer condition,
   storage, GPU capacity, service identity, and planned external destinations.
3. A headed Playwright browser opens with a dedicated run-scoped profile.
4. Complete login and captcha challenges manually, then confirm that the selected
   collection is visible.
5. The application captures collection-scoped evidence, persists the inventory,
   closes Playwright, and removes the run-scoped browser profile.
6. The current processing plan is recomputed and eligible videos are processed in
   batches of at most five.
7. The application coordinates download, CPU preparation, vision, verified model
   release, and transcription behind explicit consent windows.
8. A configured text API receives only sanitized `video.md` and `audio.md`
   evidence and returns a synthesis document that must pass the existing strict
   schema validation.
9. Verification retrieves only the document's validated `candidate_urls` after
   showing the exact destinations to the operator.
10. Results are appended to the local backlog as `pending`, followed by a concise
    batch summary and the existing review commands.

### Work package 0.1 — Launcher and browser inventory

- Add the executable `run-collection.sh` as a thin, fail-fast launcher.
- Add `collection-run` to the Python CLI.
- Launch headed Chromium through Playwright with a dedicated profile; never inspect,
  copy, or automate the operator's default browser profile.
- Wait for explicit operator confirmation after login/captcha handling.
- Reuse the existing collection-scoped DOM and inventory rules so recommendation
  links cannot become collection members.
- Persist the HTML/evidence required for audit and recovery.
- Close the browser context on success, handled failure, or interruption; report
  uncertain cleanup after abrupt process termination.

### Work package 0.2 — Collection coordination

- Recompute the plan from current inventory, processed state, and blocklist.
- Process `new_processable` and explicitly resumable items in batches of at most
  five; skip completed and permanently rejected IDs.
- Preserve one exclusive writer for inventory, run manifests, and backlog state.
- Reuse existing stage functions and fingerprints rather than adding another job
  ledger.
- Coordinate the isolated Ollama lifecycle under explicit consent.
- Add bounded, identity-checked management of the known shared Whisper service so
  the normal path can stop it for vision and restore it for audio. Restoration
  failure blocks progress and remains visible.
- Stop the affected resource window after a fired gate; never retry automatically.
- Produce an aggregate progress report without making it a second state authority.

### Work package 0.3 — Automatic text synthesis

- Introduce a small provider interface with one OpenAI-compatible implementation.
- Configure base URL, model, API key, timeouts, and text limits at runtime.
- Keep credentials outside Git and scrub them from reports and exceptions.
- Send only sanitized evidence derived from `video.md` and `audio.md`; never send
  media, frames, local paths, browser state, or credentials.
- Request structured JSON and validate it through the existing
  `SynthesisDocument` contract before persisting anything.
- Disable tool use, arbitrary URL retrieval, and command execution in the provider
  request.
- Treat timeout, refusal, malformed output, or unavailable credentials as a
  resumable synthesis stop. Never manufacture a classification.
- Record provider/model identity and the content-bound synthesis fingerprint.

### Work package 0.4 — Local delivery and acceptance

- Verify only explicit, safe `candidate_urls` from validated synthesis documents.
- Emit every successful result to the local backlog with `status: pending`.
- Leave accept/reject decisions to the existing backlog review workflow.
- Run one bounded real trial on a small explicitly authorized collection.
- Repeat the unchanged command and prove zero unnecessary download or audiovisual
  inference.
- Exercise interruption and provider-failure recovery without duplicate entries.

### Phase 0 acceptance checklist

- [ ] One command reaches `pending` backlog entries for a real small collection.
- [ ] The browser is used only for inventory and is closed before media processing.
- [ ] Recommendation links are excluded and collection completeness is reported
      honestly.
- [ ] The operator performs no normal technical command between stages.
- [ ] Vision and Whisper never overlap in GPU residency.
- [ ] Isolated Ollama cleanup and shared Whisper restoration are positively checked.
- [ ] Only sanitized evidence text reaches the configured text API.
- [ ] Provider failure and process interruption preserve resumable progress.
- [ ] Re-running the same collection performs zero unnecessary audiovisual work.
- [ ] All emitted entries remain local and `pending` until explicit review.

## Stability track

**Outcome:** a run either finishes or stops with an explicit, resumable outcome,
and the shared Whisper service is never left stopped. This track runs alongside
the product phases and comes before any further orchestration feature work.

Review baseline (2026-11-03): 944 tests passing, all with fakes; no live
integration coverage. Recent fixes cluster on Whisper restoration in individual
failure branches and on curses terminal handling.

### S1 — Nothing can hang (done)

- Every podman, `nvidia-smi`, and process-probe subprocess has a finite timeout
  (`runtime.py` constants). A timeout surfaces as the runner's exit 124 and
  fails closed: it is never read as a negative answer such as "container
  missing" or "process gone".
- The Whisper client no longer blocks on a socket with no timeout after sending
  audio. Reads poll in 1 s windows and the stage deadline is checked before
  every read, so a service that stalls with the connection open, or trickles
  bytes of a frame that never completes, ends in a deadline outcome. Sending
  the audio is bounded too, and fragmented messages survive a poll timeout
  between frames.
- SIGTERM and SIGHUP (`kill`, `systemctl stop`, a closed terminal) take the
  same `KeyboardInterrupt` cleanup paths as Ctrl+C. A repeated signal does not
  abort the cleanup that is already running.
- Decision: no run-level wall-clock watchdog. With every external call bounded,
  a global timer would only duplicate the per-stage deadlines.
- Coverage: `tests/test_bounded_calls.py`.

### S2 — One owner for the GPU swap (done)

- `gpu_lease.py` owns every Whisper stop and restore: exactly one start per
  window, no restore after an interruption inside the stop, and one shared
  60 s readiness wait. The guided vision window and the collection swap retry
  both use it; per-branch restoration is gone.
- A persisted marker (`active_gpu_lease.json`) is written before each stop
  and removed after a verified healthy restore.
- Startup reconcile: a marker left by a dead run is reconciled before any new
  GPU work. If Whisper is stopped with the same identity, it is restored
  automatically under the original run's restore grant, including after an
  interruption inside `podman stop`. Anything unproven fails closed.
- Concurrency guard: a marker owned by another live process blocks a new GPU
  window.
- Shape: the lease is an object (`acquire` / `restore_once`) rather than a
  single `with` block, because the stop happens after consent in one phase and
  the restore in a later one. `leased_window` exists for callers that fit in
  one block.
- Not done: free-VRAM verification was not moved into the lease. The
  collection retry's post-stop check reports distinct drift outcomes, so
  moving it would change behaviour. The guided gates stay in the vision stage.
- Details: `OPERATIONS.md` "GPU lease marker and startup reconcile";
  feature document `odd/tasks/tiktok-ingest-gpu-lease.md`.

### S3 — Layered orchestration (done)

- Done: `engine.py` holds `StageEngine`, which runs the stages and the GPU
  lease with injected `approve` (consent) and `notify` (narration) adapters
  and imports no TUI or consent provider. `GuidedRun` is a thin driver;
  `collection-run` drives phases through the public `GuidedRun.engine`.
- Done: the GPU lease is built in one place (`engine.lease_from_stages`) for
  the primary swap and the swap retry. Restore outcome mapping stays per
  driver because each emits its own pinned messages.
- Done: `pipeline.py` renamed to `fetch.py`.
- Original plan:
- Extract a stage engine (items + phase in, outcomes out) with no TUI and no
  consent logic. `guided-run` and `collection-run` become thin drivers;
  consent and the TUI become adapters.
- Remove `collection_run`'s dependency on the private
  `GuidedRun._execute_collection_phase` and keep swap-retry logic in one place.
- Rename `pipeline.py` to reflect what it does (URL fetch and validation).

### S4 — Realistic gates and live checks (done)

- Done: swap gate trips over 512 MiB only while swap I/O exceeds 1 MiB/s;
  1024 MiB always trips. The cgroup option was dropped: the isolated Ollama
  is a direct child process with no cgroup of its own.
- Done: opt-in live smoke test `tests/test_live_smoke.py`
  (`TIKTOK_INGEST_LIVE`, `TIKTOK_INGEST_LIVE_URL`,
  `TIKTOK_INGEST_LIVE_COLLECTION`).
- Done: zero-items warning in browser inventory and `inventory-collect`.
- Details: `OPERATIONS.md`; feature document
  `odd/tasks/tiktok-ingest-stability-s3-s4.md`.
- Original plan:

- Make the swap gate tolerate short ambient spikes (sustained rate instead of
  a raw delta), or measure the Ollama cgroup instead of host-wide
  `/proc/vmstat`. Observed false trips exceeded the 512 MiB limit by only
  1.77–18.56 MiB.
- Add an opt-in live smoke test (`TIKTOK_INGEST_LIVE=1`) with one short real
  video: Whisper, Ollama, podman, and the TikTok collection selector. It never
  runs in the default suite.
- Warn when the collection selector returns zero items for a collection that
  previously had items, so TikTok DOM changes are not silent.

## Phase 1 — Local web application

Status (2026-11-04): `ti web` serves every minimum view below and backlog
review; launching runs from the web is built but disabled until the Phase 0
acceptance checklist is closed (items 7 and 8 lack live evidence). See
`OPERATIONS.md` section 10 and `odd/tasks/tiktok-ingest-web-phase1.md`.

**Outcome:** replace terminal ceremony with a loopback-only, single-user interface
without replacing the Phase 0 engine.

Phase requirements: [Phase 1 PRD](prds/PHASE-1-LOCAL-WEB-PRD.md).

Minimum views:

- collections and inventory completeness;
- current and previous runs with per-video stage progress;
- actionable blocks and resume instructions;
- visual/audio evidence and synthesis review;
- verification results;
- pending backlog accept/reject queue.

Keep the existing local state contracts initially. A new database is justified only
if the product later introduces real concurrent writers, multiple users, or query
requirements the current stores cannot support.

## Phase 2 — Accepted-result destinations

**Outcome:** deliver explicitly accepted entries through optional adapters.

Phase requirements: [Phase 2 PRD](prds/PHASE-2-DESTINATIONS-PRD.md).

- Add export adapters for destinations such as Notion or Gertru.
- Send only accepted entries and require destination-specific authorization.
- Record idempotency keys, delivery receipts, and retry-safe outcomes.
- Keep destination failures separate from ingest and operator-review state.

## Explicitly deferred

- unattended or scheduled runs;
- multiple users or remote hosting;
- automatic acceptance or rejection;
- automatic publication of `pending` entries;
- YouTube, Instagram, comments, photo-slide ingestion, or bilingual processing;
- cloud processing of raw media;
- a new database before demonstrated need;
- provider-specific business logic in the core pipeline.

## Next step

Design and implement Phase 0 only. Do not begin the web interface or destination
adapters until the one-command real-collection acceptance checklist is satisfied.
