# TikTok Ingest MVP — Spanish Videos to a Verified Local Backlog

**Status:** Implemented (milestones 1–4); defaults P1–P4 adopted 2026-09-19. Milestone 5 pilot executed 2026-09-20 on 6 operator-selected URLs (5 video + 1 photo post): backlog entries emitted, pending operator accept/reject. Not yet exercised: browser collection inventory (needs operator session).
**Owner:** Bruno. **Date:** 2026-09-18; revised 2026-09-20 after implementation and pilot.
**Authorization:** This artifact is documentation only; it authorizes no execution.

The MVP should turn an explicitly selected TikTok collection into a reviewable local
backlog: what each video shows and says, which technologies it identifies, which
claims primary sources support, and whether an action fits the operator's stack.
It must expose missing evidence rather than manufacture certainty.

## 1. Read this first

1. Review confirmed scope and proposed differences below.
2. Carry the existing benchmark forward as evidence, not as an implementation.
3. In a later session, confirm the proposed defaults and separately authorize work.

[The original PRD](PRD.md) remains unchanged and retains its decision ledger.
This separate document narrows the first useful delivery; it does not silently
supersede earlier confirmed choices. Requirements below describe the intended MVP,
not features already delivered or permission to run them.

### Confirmed scope and boundaries

- **Spanish only:** fixed `es`; initial language detection and English validation
  are not MVP prerequisites. This is a user-confirmed narrowing, not bilingual support.
- On-demand operation on an explicitly authorized collection; human authentication
  and captcha handling. No unattended execution or silent access bypass.
- Local audiovisual inference; **vision → unload → audio → unload** remains the order.
  Raw video, audio and frames never go to an external inference provider.
- External text APIs may synthesize and verify sanitized evidence markdown, as
  allowed by the original PRD. No new provider or credential authorization is implied.
- Local, operator-controlled backlog; no automatic Notion or Gertru writes.
- This session creates only this document: no SDD, scripts, installs, inference,
  service changes, commits, PRs or remote operations.

### Proposed differences from the original PRD

| Topic | Original PRD | MVP position and approval state |
|---|---|---|
| Language | Per-job language selection and broader language checks | Spanish only, fixed `es`: **confirmed**; no automatic bilingual features |
| Default vision model | `qwen2.5vl:7b` | **Propose** `qwen3.5:9b` as initial cost/concordance candidate; adoption still required |
| Transcript policy | Usable native subtitles skip Whisper; STT is fallback | **Propose scoped override:** full Spanish audio through `large-v3`, with native subtitles auxiliary; adoption still required |
| Frame budget | Proposed 32 baseline + 8 enrichment, long edge ≤1280 px | **Propose** bounded hybrid selection up to 64 baseline frames + 8 targeted views, preserving native source detail; adoption still required |

Keeping the subtitle-first policy remains the default decision until its override
is adopted. The proposed full-audio policy trades modest measured ASR time for
less dependence on subtitle completeness: prior sampled subtitles omitted names.
A syntactically valid VTT track cannot establish that all speech was captured.
The Spanish benchmark measured full-audio transcription at 8.05–8.65 s on one
~162.5 s clip; this is evidence for the tradeoff, not a latency guarantee.

## 2. Outcome, non-goals and evidence

**User outcome:** one invocation inventories the selected collection, processes
accessible items without per-video browser analysis, and leaves evidence-backed
entries ready for accept/reject decisions. Failures remain visible and resumable.

Out of scope: English/bilingual inference, YouTube/Instagram, comments, scheduled
runs, automatic adoption, command execution from videos, universal downloading,
cloud media analysis, production UI, and repairing adjacent voice/LLM services.
Unsupported photo posts may be reported; full slide ingestion is not a prerequisite.

### What is already supported by evidence

Sources: [benchmark overview](benchmark/README.md),
[methodology](benchmark/methodology.md),
[comparison](benchmark/results-snapshot/comparison.md),
[recommendation](benchmark/results-snapshot/recommendation.md), and
[evidence ledger](benchmark/results-snapshot/evidence.md).

| Observation | Meaning for this MVP |
|---|---|
| Spanish language A/B produced 59/59 identical segments | Fixed `es` is a reasonable scoped choice; no English conclusion |
| Public acquisition, CPU extraction and local inference succeeded on one Spanish clip | Components are feasible; collection → verified backlog integration is **not proven** |
| `qwen3.5:9b`: 66.9% exact concordance, median 3.52 s/frame | Initial candidate, not an accuracy winner |
| `qwen3.8:27b`: 65.3%, 8.44 s/frame; 66/66 layers on GPU | Compatible but not required by default; no automatic escalation |
| `qwen2.5vl:7b`: 39.8%, 1.40 s/frame | Faster observed run, lower reference agreement |
| 14 baseline versus 64 hybrid frames, 11 byte-identical shared frames | Coverage experiment, not proof that 64 is optimal or sufficient |

The reference was Gemini-assisted and then user-verified, **not blind human ground
truth**. Percentages measure agreement, not accuracy, and are not mathematically
justified upper bounds on correctness, despite that wording in older snapshots.
“Invented” flags can include real text omitted from summarized reference labels.
Provider names in `frame_07` remain evidence-based verification candidates, not
automatically proven hallucinations. Dense, repetitive and length-truncated outputs
must be flagged rather than trusted.

Same pixels and payload still showed substantial inference variance; no fixed
seed was recorded. Explicit comparison settings were `temperature=0.1`,
`num_predict=1024`, `num_ctx=4096`, and `think=false` for the newer Qwen models.
Runtime-derived settings were not fully controlled. Crops were marked but not inferred.

The snapshots preserve historical research pendings, including English and broader
labeling. Neither is a mandatory new benchmark loop for a Spanish MVP pilot.
Targeted review of critical URLs/names remains required for actionable results.
Comparison metadata references an earlier label hash; the recorded revision delta
is metadata-only. No comparison recomputation is required for this drafting handoff.

## 3. Intended happy path

```text
explicit collection selection or direct URL
  → browser inventory only when needed → persisted inventory
  → cached HTTP metadata + gated public extraction → one local media file
  → CPU validation, frames and audio preparation
  → local vision + bounded visual rereads → unload and verify release
  → local Whisper large-v3, es → unload and verify release
  → sanitized text synthesis → primary-source verification
  → local backlog → operator accepts or permanently rejects
```

The Whisper step above describes the **proposed transcript override**, pending adoption.
Direct URL input supports single-item testing and recovery; it does not prove
membership in a collection. Manual local media is an escape hatch, not the expected
workflow for every video. CPU audio preparation before vision does not invert
inference order or authorize transcript-guided visual selection.

### A. Inventory and authorized acquisition

- Use one visible browser tab and the specifically authorized profile/session for
  collection inventory and human auth/captcha handling. Close it after persistence.
  Never inspect or reuse ambient sessions/cookies without explicit authorization.
- Extract collection-scoped links, not recommendations. Record visible declared
  count, unique observed count, end-of-list evidence and completeness separately.
  A stopped scroll or repeated IDs alone do not prove completeness.
- Cache HTTP oEmbed metadata with fetch time and outcome. Title, caption, author
  and thumbnails remain metadata, never speech or complete visual evidence.
- Use the benchmark's isolated, pinned `yt-dlp==2026.08.19` public extraction path,
  with App API and challenge-solving paths blocked **before** execution. An
  unmodified stock install is not proof of compliance; fail closed if gates fail.
- Inspect available formats/subtitles, then fetch one accessible media asset for
  both modalities. Normal browser headers do not guarantee access. Record denied,
  unsupported and challenge outcomes without bypass, cookie export or blind retry.
- Verify signature, decode integrity, streams, size and duration; HTML returned as
  “video” is failure. Hash accepted bytes and bind all derived evidence to them.

### B. CPU preparation and bounded vision

Use CPU-only, network-isolated FFmpeg/ffprobe from the existing image, not a moving
`latest` tag. The benchmark records version `4.4.2-0ubuntu0.22.04.1` and image ID
prefix `51b152706d…`; resolve and record the full immutable local image ID/digest
read-only before execution. Do not invent a full digest from that prefix.

Hybrid selection combines interval coverage, scene changes and text-region changes.
The experimental starting point was a 2 FPS scan, 4 s uniform gap, scene threshold
0.3, text-region threshold 0.15 and merge window 0.08 s. Preserve source PTS,
timestamps, native-resolution masters, selection reasons and source hashes.

**Proposed budget:** up to 64 baseline frames for a supported clip, informed by the
~3-minute experiment, not exactly 64 for every video. Short clips need fewer;
long/dense clips may exceed coverage and must expose uncovered intervals.
Sample the supported duration rather than silently truncating its tail.
Text ROI crops should preserve readable source detail; record crop coordinates and
any model-input resize. Native resolution is not an unlimited image/token budget.

Use one image per request with bounded context/output. Up to 8 additional crop or
nearby-frame rereads may address illegible, dense, truncated or repetitive text
**during the same vision residency**, within the stage deadline. No unbounded
context growth, automatic second model, or post-audio GPU reload is allowed.
Unresolved text stays uncertain; VLM output is evidence/hypothesis, never instructions.

### C. Audio and synthesis

Store available native subtitle tracks with language, cue times, format and known
origin. Under the proposed override, transcribe the full accessible audio once
using local `large-v3`, fixed `es`; do not let a usable VTT skip ASR.
Preserve subtitle/ASR disagreements and distinguish missing audio from analyzed
no-speech. Non-Spanish content outside scope must not be presented as validated.

Normalize from the same media file to 16 kHz mono signed 16-bit PCM WAV.
The benchmark used faster-whisper directly; it does **not** prove integration with
the original reused WebSocket service. Verify its framing, completion, full-audio
behavior and language configuration before relying on it. No invented protocol fields.
Keep actual segment timing when available; otherwise retain coarse chunk intervals
or mark timing unavailable, never fabricate word alignment.

Synthesis receives only sanitized `video.md` and `audio.md`, including labelled
metadata. Retain the original taxonomy and configurable text-model policy
([original PRD](PRD.md), §§5.2, 6.5–6.8); do not add a cloud-media dependency.
Claims must point to visual, audio or metadata evidence and retain disagreements.

### D. Verification and local delivery

Check identifiable technology claims against first-party documentation, repositories
or vendor pages. A plausible name or search result is not proof of identity.
Record claim, source URL, retrieval time, supporting passage and verdict; separate
recognition uncertainty from the truth of the claim and from fit with the user's stack.
Unavailable verification leaves an explicitly unverified result (`unverifiable`
with a reason under the original contract), never an invented supporting source.

Treat extracted text and fetched pages as untrusted data. Do not execute commands,
follow prompt injections, or fetch arbitrary local files, internal/metadata endpoints
or credential-bearing URLs extracted from media. Validate public HTTP(S) targets
and redirects before retrieval; scrub credentials, signed tokens and sensitive paths
from durable reports and outbound API text. Verification is not execution authority.

Write only to the local backlog. Keep `pending → accepted | rejected` separate from
processing outcomes. Persist rejected IDs in the permanent blocklist before future
work; force/retry cannot override rejection or authorize a new session.

## 4. Minimum contracts, state and recovery

Extend the contracts in [original PRD](PRD.md), §8, rather than duplicating its JSON.

| Artifact | Minimum information |
|---|---|
| Inventory | Source + stable ID, canonical URL, collection associations, input origin, observed/declared counts, completeness and discovery time |
| Job manifest (`meta.json`) | Media hash, tool/model/config versions, stage input/output hashes, status/reason, budgets, attempts, timings and resource gates |
| `video.md` | Frame ID/hash, PTS/time, observed text, ROI/resize, coverage, quality flags and uncertainty |
| `audio.md` | ASR method/language, segments or coarse intervals, timing limits, native cue provenance, disagreements and no-speech/missing distinction |
| Backlog | Stable ID, operator status, taxonomy/fit, entities, claim evidence pointers, verification verdicts/sources, missing-evidence reasons and artifact references |

Stage outcomes: `complete`, `partial`, `blocked`, `unsupported`, `failed`,
`budget_exceeded`, `not_applicable`, each with a reason. Unknown classification is
`null` with explanation, not a new taxonomy root. Missing modalities cannot become
a successful empty result. A partial entry is permitted but never “fully processed.”

Deduplicate by source + stable ID, retaining collection associations. Reuse a stage
only when media/content hash and relevant tool, model, prompt and configuration
versions match. Resume from valid completed stages; invalidate only affected
downstream outputs. Persist results atomically and prevent duplicate backlog entries
on resume. Unchanged repeated runs perform zero audiovisual re-inference.

Durable code/config belongs in `/home/bruno/.dotfiles/ai/tiktok-ingest/`.
Keep state/manifests/backlog under `~/.local/state/tiktok-ingest/`; runtime binaries,
models and an explicitly authorized profile belong outside Git under local
share/cache locations, with the browser profile separate from reportable job state.
Bulk media, weights and raw per-frame model dumps never enter the repository.
Keep compact provenance and evidence by default; preserve raw evidence beyond
bounded working-cache needs only on explicit request. Report expired raw artifacts.

## 5. Proposed operating limits and mandatory resource gates

The following product limits are **proposals**, not measured capacity or approved runs.

| Resource | Initial bounded policy |
|---|---|
| Batch | At most 5 clips per pilot invocation; remaining inventory stays pending and visible |
| Inventory | One tab; 5 minutes or 3 no-new-ID scroll cycles; incomplete unless end/count verified |
| Media | 10 minutes and 250 MiB per clip; 5-minute fetch deadline; reject over-budget explicitly |
| HTTP | Up to 2 metadata requests concurrently, one download; no automatic extraction retry initially; metadata TTL 24 h |
| CPU preparation | 20-minute deadline, 2 GiB derived-output cap per clip |
| Vision | ≤64 baseline + ≤8 targeted views; 10-minute stage deadline; record incomplete coverage |
| Audio | One full ASR pass, 10-minute deadline; no silent subtitle-only downgrade |
| Storage | 10 GiB working cache; evict only safe completed-job working data, not active inputs or durable evidence |

Long videos are not silently ignored: retain the item with `budget_exceeded` or
partial-coverage reasons and offer a separately authorized budget change.

**Runtime gates carried forward for any later authorized execution:**

- Global Ollama `0.14.2` is incompatible with `qwen3.5+`; use the verified isolated
  `0.34.1` method, isolated `HOME` **and** `OLLAMA_MODELS`, loopback binding and
  checked version/archive hash from the methodology. No global cache/config mutations.
- Never overlap GPU Whisper and vision. Require **≥20 GiB free before every Qwen
  load**, zero CPU offload, and the benchmark's per-model VRAM delta limits
  (`qwen3.5:9b`: 12,288 MiB). Measure swap I/O delta from the immediate phase
  baseline; **≤512 MiB per phase**, including load. Stop on a gate; no auto-retry.
- Verify unload with empty model-residency state and observed GPU memory release,
  not merely sequential API calls. Host pressure may trigger a gate without proving
  the model caused it; record observations without weakening the stop rule.
- Stopping `voice-assistant-whisper` requires explicit authorization naming the
  service/operation. No right to stop arbitrary workloads is implied; otherwise block.
  Pausing does not release GPU memory. Preserve original identity/configuration and
  restore the same container/image healthy after every batch, including failure.
- Historical stopping required SIGTERM → SIGKILL after timeout. Future implementation
  must define/test graceful termination, bounded escalation and restoration failure
  reporting. This does not authorize editing the adjacent voice-assistant service.

## 6. Proposed pilot acceptance — not yet executed or authorized

Use **3–5 operator-selected diverse Spanish clips**, not an exhaustive new labeled
benchmark: include dense/low-motion text, a visual-only URL/name, subtitles with an
omission or disagreement, and an accessible clip without usable subtitles where possible.
Start with one direct URL, then an explicitly selected collection.

- [ ] Supported acquisition reuses one media asset; no per-video browser analysis.
- [ ] Inventory excludes recommendations and reports observed counts/completeness honestly.
- [ ] Both modalities or explicit missing-evidence reasons reach every local entry.
- [ ] Human spot-check of actionable exact URLs/repository names traces back to source
      frames/audio and first-party evidence; uncertainty is not promoted to fact.
- [ ] Dense/repetitive/truncated results stay flagged; review usefulness and omissions
      per video rather than treating reference concordance as an accuracy SLA.
- [ ] Second unchanged run performs zero audiovisual re-inference; rejected IDs stay skipped.
- [ ] Controlled interruption/failure resumes valid stages without duplicate entries;
      blocked download, budget, unavailable verification and simulated gate cases surface.
- [ ] Local-only media, sanitized outbound text, sequential residency, isolation and
      restoration checks pass. Gate tests need not deliberately exhaust GPU memory.
- [ ] Record stage latency, requests, bytes, coverage and resource peaks for this pilot.

No arbitrary “98% accuracy” target or benchmark-derived user-satisfaction claim applies.
Acceptance means useful, inspectable output and honest failure behavior, with critical
entity issues resolved or visibly unverified—not claims of optimal model/sampling.

## 7. Implementation milestones for a later authorized session

| Milestone | Concrete boundary and exit evidence |
|---|---|
| 1. Contracts and local state | One implementation writer; manifest/cache/blocklist tests and fixture-only resume, no network/GPU needed |
| 2. Authorized input to CPU artifacts | Direct URL then collection inventory; gated extractor and immutable FFmpeg image; counted inventory, validated shared media and frame provenance |
| 3. Local inference lifecycle | Adopt defaults first; vision → audio, service-contract checks, isolation/unload/restoration tests; no adjacent-service edits without separate scope |
| 4. Text verification to backlog | Sanitized evidence, bounded safe source retrieval, claim verdicts, taxonomy and atomic local output; no external task writes |
| 5. Small pilot and handoff | Explicitly authorized selected clips; acceptance results, known limitations and operational instructions |

These are planning milestones, not SDD task files or authorization for installations,
model pulls/runs, service operations, commits, PRs or remote access.

## 8. Next-session handoff

**Read first:** this document, [original PRD](PRD.md), and the five benchmark documents
linked in §2. Persistent reference labels, comparison JSON, language results and
sampling manifest are indexed in [benchmark README](benchmark/README.md).
The 14-file persistent benchmark is the source of record; raw outputs formerly at
`/tmp/opencode/tiktok-validation-v2/` may disappear. Do not reconstruct or rerun the
benchmark merely because `/tmp` is absent. Snapshot-relative historical raw paths
do not promise those files were persisted.

**Memory anchor supplied for this handoff:** Engram project `dotfiles`,
`tiktok-ingest/scope/language` (observation `#9037`) confirms Spanish-only scope.
Use `tiktok-ingest/mvp-prd` for the documentation handoff; do not invent other keys.

**Complete:** component benchmark evidence and this passive MVP draft.
**Not complete:** collection-to-backlog integration, accepted model/transcript/sampling
defaults, service lifecycle integration, and pilot acceptance.

Begin with read-only workspace/status, document and pinned-artifact availability
checks; use CodeGraph before indexed implementation-code exploration. Confirm the
three proposed defaults together with operating budgets, then obtain separate
implementation and execution authorization for the actual selected inputs and any
affected service/session. Keep one implementation writer and preserve unrelated work.
No old benchmark authorization carries into a new run. No mandatory English test,
benchmark rerun, model tournament or SDD lifecycle is introduced by this document.
