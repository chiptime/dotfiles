# TikTok ingest: whole-collection phase-first coordination

## Objective and authorization

Implement the user-approved global order for every frozen selected video:
preparation -> audio -> vision -> synthesis -> grouped verification.
The user explicitly authorized implementation on 2026-10-10. Batches of at
most five are resource/recovery windows, not complete-pipeline execution units.
Do not start synthesis for an early batch before the vision phase has ended
for the entire frozen selection. Preserve valid completed stages on resume.

Local implementation and offline testing only. No live browser, network,
GPU/model requests, service mutations, process closures, WSL changes,
commits, staging, push, or PR creation are authorized by this task.

## Problem and rationale

The existing collection driver completes each batch's preparation, vision,
audio, and synthesis before the next batch. This can wake the shared text
backend before subsequent vision windows and loses the opportunity to keep
audio evidence when vision fails. Audio itself requires prepared media, not
completed vision; the current dependency is imposed by coordination.
Phase-first scheduling improves ordering and resumability, not a guaranteed
solution to systemwide paging or a claim of GPU validation success.

## Scope and constraints

- Freeze eligible IDs and canonical URLs once; never absorb newly eligible IDs.
- Preserve the public `guided-run` standalone order unless a shared safety
  guard is necessary. Change the `collection-run` default without new flags.
- Complete preparation windows before any audio, and audio windows before any
  vision. Process each phase sequentially across the complete frozen selection.
- Synthesis requires complete audio and vision and starts only after the
  collection's vision phase has ended. Preserve grouped exact-URL verification.
- Preserve all stage fingerprints, stores, artifact formats, exclusion rules,
  and valid completed work. Report incomplete IDs honestly and compatibly.
- Preserve per-video vision unload, free VRAM >= 20,480 MiB, zero offload,
  swap I/O <= 512 MiB per attempt, and the 600 s vision stage deadline.
- Keep Whisper lifecycle windows bounded per batch, with verified identity,
  standing restore authority, and healthy restoration before retry consent
  or waiting. Never hold it stopped across the whole collection by default.
- A failed GPU window stays closed and its remaining IDs stay pending. Do not
  reopen that window or implicitly reattempt a failed ID. Later frozen normal
  windows follow the existing approved driver policy; do not invent an
  additional invocation-wide hard stop. Restoration failure or interruption
  blocks all further effects, including synthesis and grouped verification.
- Preserve exactly one extra vision attempt per video per invocation, only
  behind the existing new explicit retry consent and typed eligibility.
- No persistent vision residency between videos, text-service stop/unload API,
  memory-policy tuning, or automatic workload cleanup in this feature.
- Keep stdout JSON parseable and existing report fields compatible; additive
  phase evidence may explain global progress and skipped/pending windows.

## Tasks and route

- [x] **TI-PF-01** Expose bounded coordination phases and protect shared lifecycle
  failure paths. Add deterministic tests first; preserve standalone compatibility.
  Route: delegated direct (reading prepares a write; two non-trivial modules).
- [x] **TI-PF-02** Implement collection-wide phase barriers, preparation-based
  audio eligibility, retry integration, and final compatible reporting. Prove
  cross-batch ordering with more than five IDs and failures/reuse scenarios.
  Route: delegated direct (multi-file behavior and test changes).
- [x] **TI-PF-03** Align operational docs and consent language; verify the full
  offline suite and independently check requirements/lifecycle boundaries.
  Route: delegated direct; parent performs a bounded reported-command spot check.

## Acceptance criteria

1. A shared trace for more than five IDs has all preparation before audio,
   all audio before vision, and all vision windows before any synthesis request.
2. Vision failure cannot discard or repeat already completed audio.
3. Audio failures prevent synthesis for that ID but not unrelated eligible work.
4. Rejected, unsupported, and already completed IDs remain excluded; resume
   does not repeat valid fetch, preparation, audio, synthesis, or verification.
5. Run-plan and stage consent describe the actual phase order and audio work;
   scope drift is not auto-granted and retries still require a separate ask.
6. Whisper stop/restore is balanced per actual cycle. Restore/health failure
   or interrupted unknown state prevents all later API and verification work,
   including previously eligible IDs in mixed batches.
7. Original retry evidence and budgets remain intact. Retry success does not
   reopen its failed original window; existing later-window policy is preserved.
8. Final JSON is backward-compatible/additive and distinguishes completed
   phases from pending videos. No paging efficacy claim is introduced.

## Verification and baseline

TDD configuration is unconfigured for TikTok ingest; apply the default
deterministic test-first policy: observe RED for the new ordering/safety
scenarios, implement GREEN, and refactor while checks stay green.

Repository-root focused command:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_collection_run tiktok_ingest.tests.test_guided tiktok_ingest.tests.test_swap_retry
```

Repository-root full command:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src
```

Baseline observed by the exploration worker: 828 tests, 15.726 s, OK; no
failing test names. Argparse output and a non-failing HTTPError cleanup
ResourceWarning were observed. Live GPU validation is deferred and requires
separate authorization; no runtime inference is part of these offline checks.

RDD: off, decided by clone-local preference, verified by the parent. Do not
start native review or change the preference. Independent requirements
verification remains proportional ordinary functional checking.

## Delivery and rollback

Forecast: approximately 900-1300 authored additions/deletions including tests
and docs, advisory only. Delivery strategy: ask-on-risk; delivery is not
requested, so commits/PR slicing are pending and no commit is authorized.
Never omit tests or compress code to meet a line-count forecast.

Rollback removes only this feature's coordination, tests, and doc changes,
restoring batch-first collection execution without weakening safety gates.
Preserve unrelated dirty files and the existing swap-retry implementation.

## Progress and next step

- Exploration complete; current subsystem baseline is clean on `master`.
- TI-PF-01 complete: private bounded phases and shared restore/cleanup safety guard implemented; standalone order retained.
- Observed RED: root focused command above ran 169 tests in 9.365 s with four expected new regression failures (global order, audio-first failure isolation, collection mixed-ready restore failure, standalone mixed-ready restore failure).
- TI-PF-01 GREEN: `env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_guided` ran 80 tests in 1.805 s, OK. Includes prepared-audio eligibility, fresh stop-cycle runners, and recovery after an unexpected server-start failure. One intermediate fixture error (assigning a frozen StageFunctions field) was corrected with dataclasses.replace.
- Engram mirror topic: `odd/tiktok-ingest-phase-first/tasks`; mirror #10374 verified against this document before source edits.
- Tracking document itself is the only new file so far.
- TI-PF-02 complete (source verified, no rewrite this turn): `run_collection`
  now owns the global barriers in `collection_run.py:2348-2449` (phase order
  preparation -> audio -> vision -> synthesis; one fresh `GuidedRun` per
  phase/window at `collection_run.py:2375`); preparation-based audio eligibility
  in `guided.py:_collection_audio_phase` (`guided.py:1334-1363`);
  vision-only windows (`guided.py:_gpu_phase(..., vision_only=True)`,
  `guided.py:1365-1572`); grouped verification deferred past every phase
  (`collection_run.py:2451-2471`). Evidence tests: `test_global_order_across_seven_ids_and_two_resource_windows`
  (7 ids, 2 windows, global order, 2 stop/2 restore),
  `test_audio_failure_precedes_vision_and_only_blocks_own_synthesis`,
  `test_restore_failure_blocks_previously_ready_mixed_batch`,
  `TestHardStopBlocksGroupedVerification`, and the swap-retry journeys
  `test_journey_retry_success_keeps_other_window_ids_pending` /
  `test_journey_subsequent_frozen_batches_reconcile_unchanged`.
- TI-PF-02 verification: focused `test_collection_run` 45 tests OK,
  `test_guided` 80 tests OK, `test_swap_retry` 47 tests OK (172 total, 8.299 s).
- TI-PF-03 complete: README collection-run section and OPERATIONS section 9 +
  whisper/swap-retry corrected to phase-first global ordering with bounded
  resource windows, preparation-based audio eligibility, and pending semantics;
  honest no-efficacy wording retained.
- TI-PF-03 verification (independent, read-only): full suite
  `env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src`
  ran 835 tests in 13.230 s, OK, exit 0.
- Whitespace check: `git diff --check -- ai/tiktok-ingest` is clean (exit 0)
  and the tracking document has no trailing whitespace. The command scoped to
  the whole repo (`... .`) reports PRE-EXISTING trailing whitespace only in the
  unrelated dirty file `os/windows/.wezterm.lua` (lines 119, 169-171), which
  this task preserves untouched.
- Acceptance-criteria audit (8/8 met; code+tests, no source gaps found) is
  recorded in the parent session report; no acceptance gap required a fix.
- Honest pending: live GPU V3 validation remains deferred and needs separate
  authorization; the cooldown stays a bounded measurement, never an efficacy
  claim.
