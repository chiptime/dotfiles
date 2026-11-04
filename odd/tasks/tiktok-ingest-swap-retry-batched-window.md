# Feature: Batched swap-retry window (vision model residency)

Objective: one swap-retry window covers every eligible video of a batch, so
the isolated-Ollama model loads once per batch instead of once per video.
Branch: `feat/tiktok-web-ux` (rides on the current working tree; commit
slices kept separate). Delivery strategy: ask-on-risk. Test runner:
`PYTHONPATH=src python3 -m unittest …` from `ai/tiktok-ingest/`.

## Specs

- **S1** When one batch leaves MORE THAN ONE id eligible for the bounded
  swap-retry, the driver offers ONE consent window covering ALL eligible ids.
  With exactly one eligible id the window keeps today's single-id semantics.
- **S2** The batched window runs ONE read-only cooldown, ONE whisper stop
  (re-admission), ONE isolated Ollama start (model load), then the vision
  re-run for each covered id sequentially, then ONE Ollama stop and ONE
  whisper restore under the same lease.
- **S3** Each covered id keeps at most ONE retry attempt per invocation;
  per-id attempt records, terminal states and report shapes are unchanged.
- **S4** If a vision call inside the window fails the swap gate again, the
  window closes at that id (whisper restored, ollama stopped); remaining
  covered ids stay pending/resumable; no second window this invocation.
- **S5** The consent window names every covered id and states the shared
  lifecycle explicitly (one isolated-Ollama lifecycle and one model load for
  all listed ids).
- **S6** Unchanged: eligibility rules, cooldown admission and drift resets,
  the cooldown cap semantics, the never-adopt rule for preexisting servers,
  and the exactly-one-attempt-per-id bound.
- **S7** `prds/PHASE-0-SWAP-QUIET-RETRY-PRD.md`: decision D-11 added; FR-03
  amended to name every covered id and the shared Ollama lifecycle; new
  FR-26 (D-11) specifies the batched window and the mid-window close rule.
  FR-02 (one attempt per id per invocation) is unchanged and still holds.
- **S8** `OPERATIONS.md` swap-retry section and the report-field notes
  describe the batched window.

Evidence base (2026-11-04 live run, `series-7455…`): median 2 s per frame,
image encode 13–53 ms, GPU ~idle; isolated llama-server started ~20 times in
one afternoon; a 47-frame video took 2 h wall for ~90 s inference. Root
cause: per-video retry cycles pay whisper stop/start + model reload per id.

## Tasks

| ID | Spec | Route | Status | Commit/evidence |
|---|---|---|---|---|
| T1 | S7 | inline | done | PRD: D-11 + FR-03 amended + FR-26 (count 26) |
| T2 | S1, S5 | inline | done | `_build_swap_retry_window([eligs])` multi-id; collect-then-ask in `_run_swap_retry_flow` |
| T3 | S2, S3, S4 | inline | done | `_execute_swap_retry(items)` one lifecycle for N ids; mid-window close `_is_swap_gate_failure`; readmission drops drifted ids (4-tuple) |
| T4 | S8 | inline | done | OPERATIONS swap-retry section + ROADMAP throughput (DONE note + exclusive-window practice) |
| T5 | all | inline | done | full suite 1197 OK (5 live skipped); test_swap_retry 51 incl. 4 new batched tests |

## Log

- L1 (2026-11-04): "solo aplicaria el punto 1, y quiza deberiamos dejar una
  ventana de tiempo exclusiva para esto" — apply model residency only; an
  exclusive time window for ingest runs is noted as operational practice
  (ROADMAP throughput track); scheduled runs stay deferred.
- L2 (2026-11-04): live-run diagnosis recorded (see S-evidence); feature
  document created before the first source write.
