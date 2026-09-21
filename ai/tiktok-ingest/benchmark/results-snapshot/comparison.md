# Comparison — Language A/B, Sampling 14 vs 64, and Three VL Models

**Headline.** (1) The two Spanish language configurations produced byte-identical transcriptions (59/59 segments), with no English input available to test. (2) On the 11 sampling frames that are byte-identical between the 14-exact and 64-hybrid sets, the same model with the same payload never disagreed on extracted URLs or tool names, but produced visibly different prose in 9/11 pairs, including three degenerate-repetition or unverifiable-expansion cases — the strongest argument for human labeling before any sampling decision. (3) All three VL models fit fully in GPU with zero offload; qwen3.8:27b was ~6x slower per frame than qwen2.5vl:7b at the same explicit generation options. **No accuracy ranking is possible: there is no human ground truth.**

## Scope and method

- Vision comparisons use only the models' own frame responses. No transcription entities were used as hints, and no response is called "better" because it contains more text.
- Per-frame provenance: every claim below is traceable to a numbered payload/response pair under `results/vision/<run-dir>/` plus the frame SHA-256 in `SHA256SUMS` / `sampling-manifest.json`. The machine-readable pairing dataset is `results/analysis-pairs.json`.
- No `% accuracy` appears anywhere: no labeled ground truth exists for any frame.

## 1. Language A/B (faster-whisper large-v3, Spanish audio)

| Item | Configuration A (autodetect) | Configuration B (sample-detect, then pin) |
|---|---|---|
| Detection | `es`, probability 0.998046875 | Window 0–30 s: `es` 0.998046875; window 98.27–128.27 s: `es` 0.9990234375 (third sample not needed, first two agreed) |
| Transcription | full original WAV, `language=null` | full original WAV, `language=es` |
| Segments | 59 | 59 |
| Result | **Identical**: canonical-JSON comparison of both segment lists is byte-equal (re-verified during synthesis) | same |

- The 0.998/0.999 figures are **uncalibrated heuristic probabilities**, not confidence intervals and not accuracy.
- **English status: `pending_no_input`.** The authorized clip contains no English speech; no bilingual validation was performed and none may be inferred from these numbers.
- Runtime: faster-whisper 1.2.1, CTranslate2 4.8.1, CUDA FP16, beam 3; A 8.65 s vs B 8.05 s transcription time (not a quality measure).

## 2. Sampling: 14 exact vs 64 hybrid (same model `qwen2.5vl:7b`, same payload)

Both runs used the identical `PROMPT` constant, `temperature=0.1`, `num_predict=1024`, `num_ctx=4096`, `think=false`, individual images, no transcript hints.

### 2.1 Pairing by pixels

Cross-referencing `SHA256SUMS` shows **11 of the 14 exact frames exist byte-identically in the 64-hybrid set** (e.g. `frame_01`≡`hybrid_001`, `frame_17`≡`hybrid_044`). Three frames (`frame_05` 17.4 s, `frame_07` 41.4 s, `frame_08` 53.4 s) have **no byte-identical hybrid counterpart** — the hybrid selector's dedup/merge retained neighboring instants instead (16.0 s, 40.0 s, 52.56 s), which are different pixels. Content comparisons for those three frames are therefore impossible at pixel level and are not attempted.

### 2.2 Same pixels, same model, same payload: 11 exact pairs

| Observation | Count / evidence |
|---|---|
| URL sets identical between the two runs | 11/11 pairs (only `frame_15`≡`hybrid_041` yields a URL — the same one in both runs) |
| Tool/name sets identical | 11/11 pairs at the extracted-name level (e.g. `frame_22`≡`hybrid_062`: both report README, "Mi1 license", docling, mammoth) |
| Responses literally identical | 2/11 (`frame_09`≡`hybrid_026`, `frame_11`≡`hybrid_030`) |
| Wording differs, same content skeleton | 6/11 (similarity 0.65–0.97) |
| **Degenerate repetition (both runs affected differently)** | 2 pairs: `frame_14`≡`hybrid_039` (one run loops `cat /etc/issue*`, the other loops `ls -l /dev/pts/N`; both hit the 1024-token cap) and `frame_22`≡`hybrid_062` (3-B run repeats one "clearly visible" sentence ~24x; 3-A run instead transcribes numeric columns it itself flags as truncated) |
| **Unverifiable expansion** | 1 pair: `frame_21`≡`hybrid_058` — 3-A reports "no specific names or URLs" and an empty code block; 3-B adds headings and full Java class bodies. At least one run's extra code is very likely fabricated, but **without ground truth it cannot be adjudicated which** |
| Doubt markers (`[illegible]`/`[uncertain]`) | identical counts per pair (frame_01: 4 vs 4; frame_11: 2 vs 2) — self-reported uncertainty was stable even when prose was not |

Interpretation limits: entity-level agreement on identical pixels is evidence of **consistency**, not correctness. The three divergence classes above are the concrete reason the next step is human labeling, not a sampling verdict.

### 2.3 Coverage of the 64-hybrid set beyond the 14 (53 hybrid-only frames)

- The 50 hybrid frames with no counterpart in the 14-set (53 records including 3 nearest-instinct duplicates) and their extracted URL/name lists are catalogued per frame in `results/analysis-pairs.json` (`hybrid_only_coverage`), each tied to `hybrid_NNN.png`, index, timestamp, and originating run (`phase-3b-qwen2.5vl-64` for 001–016, `phase-3b-retry-qwen2.5vl-48` for 017–064).
- These are coverage observations only. More frames ⇒ more extraction opportunities is arithmetic, not a quality finding, and no winner is declared on text volume.
- Documented coverage gap (from `sampling-manifest.json`): uniform sampling is 2 FPS (~500 ms native gap); on-screen text/URL/code visible for under ~0.5 s can fall entirely between samples. Scene-change (16 frames) and lower-third text-region-change (28 frames) candidates partially mitigate this; the manifest records this as a heuristic, not a guarantee (e.g. centered or full-frame terminal text).
- **The 16 crop views were never inferred.** `sampling-manifest.json` marks 16 `is_crop_candidate` frames (e.g. hybrid_002, 004, 005, 007, 011, 017, 019, 020, 022, 026, 028, 030, 031, 032, 038, 039); the crops exist on disk under `frames/crops/` and were verified against `SHA256SUMS`, but no model has seen them. Any crop contribution to a future decision is unmeasured.

### 2.4 3-B initial stop and authorized retry (provenance)

- Initial run: 16/64 frames, stopped by the 512 MiB swap-I/O gate measured from the immediate phase baseline (551,821,312 > 536,870,912 bytes); outputs preserved untouched in `phase-3b-qwen2.5vl-64/`, no `summary.json` by design (`phase-3b-gate-stop.json`).
- Retry (user-authorized, same day): only the remaining 48 frames (`hybrid_017`–`064`), identical PROMPT/options/gates imported from `scripts/run_vision.py`, same gate measured from a fresh immediate baseline. Swap delta 144,683,008 bytes (138.0 MiB binary / 144.7 MB decimal — the "138–145" range spans the two unit readings) versus 551.8 MB in the morning run, with identical methodology: this confirms the morning stop was **ambient host swap pressure** (~1.2 GB of background `pswpout` growth between morning baseline and session, per `session2-visual-phases-summary.json`), not the workload. All retry gates passed; zero empty responses; unload returned VRAM to baseline −26 MiB.
- The 16 completed pairs were reused, never repeated or overwritten; the 16 crop views remained unauthorized and were not executed.

## 3. Models: qwen2.5vl:7b vs qwen3.5:9b vs qwen3.8:27b (same 14 frames)

Explicit generation options were harmonized (`temperature=0.1`, `num_predict=1024`, `num_ctx=4096`, `think=false` in every request).

### 3.1 CRITICAL causality note — read before quoting any number

- The comparison is **informative, not controlled**. Server-derived runtime parameters (KV-cache sizing per model, mmap/load-mode decisions under host memory pressure, image-token budgeting, the VRAM-based default-context path in Ollama 0.34.1) were not forced equal and differ per model family; the day's probes existed precisely because the runtime imposed context/payload constraints where it did.
- **There is no human ground truth for any frame.** Any ranking derived from the table below is preliminary and demands human labeling first. Nothing here selects an architecture.

### 3.2 Operational metrics (RTX 4090 24 GiB, isolated Ollama 0.34.1, all gates passed, zero CPU offload)

| Metric | qwen2.5vl:7b (3-A) | qwen3.5:9b (4-1) | qwen3.8:27b (4-2) |
|---|---|---|---|
| GPU layers offloaded | 29/29 | 34/34 | 66/66 (3 loads, all 66/66) |
| Load time (first frame) | 18.9 s | 17.2 s | 15.7 s (ctx2048 probe) / 12.5 s (ctx4096 probe) |
| Eval median / frame | 1.40 s | 3.52 s | 8.44 s |
| VRAM peak delta | 6,986 MiB (limit 18,432) | 6,756 MiB (limit 12,288) | 18,465 MiB (limit 20,480; peak total 20,935 of 24,564) |
| Swap I/O delta | 148.8 MB | 280.1 MB | 314.0 MB (gate 536.9 MB) |
| done_reasons (14 frames) | 13 stop / 1 length | 11 stop / 3 length | 10 stop / 4 length |
| qwen3.8 model weight | — | — | 27B Q4_K_M, five blobs summing exactly 17,741,872,154 B + 950 B manifest (letter deviation, reported) |

The `length` cases are outputs truncated at `num_predict=1024`; for content purposes they are incomplete, and several coincide with the repetition pathologies in §2.2 (e.g. `frame_14`, `hybrid_039`, `hybrid_062`).

### 3.3 Content observations on the same 14 frames (no ground truth)

- **URL agreement:** all three models reported the identical URL on the one URL-bearing frame (`frame_15`: `https://omarchy.org/install`; qwen3.8 lists it twice in its output). No model reported a URL the others denied on the same pixels.
- **Self-reported doubt:** qwen3.5 and qwen3.8 emit more `[illegible]`/`[uncertain]` markers than qwen2.5vl on most frames (e.g. frame_17: 5 vs 0 vs 0; frame_21: 6 vs 0 vs 0; frame_05: 1/6 markers vs 0). More caution flags is not accuracy evidence in either direction.
- **Extraction richness (example, `frame_04`):** all three agree on the core names (`archify.zip`, `Archify`, MIT license, README, Contributing, Security, trending badge). qwen3.5 additionally reports supported tools visible in the hero text (Cursor, Claude Code, Codex CLI, OpenCode) and file formats (JSON IR, HTML/SVG); qwen3.8 additionally transcribes the architecture-diagram node labels (Auth Service, OAuth/OIDC, Kafka, PostgreSQL, CI/CD, …). These are omissions relative to each other on the same pixels — which extraction is *correct* is exactly what human labeling must decide.
- **Where to verify:** per-frame payloads/responses live in `results/vision/phase-3a-qwen2.5vl-14/`, `phase-4-1-qwen3.5-14/`, `phase-4-2-qwen3.8-14/` with matching `NNN-frame_XX-{payload,response}.json` numbering.

## 4. What is NOT proven by this experiment

- That either sampling strategy (14 exact or 64 hybrid) is sufficient for reliable extraction — including for the three exact frames with no byte-identical hybrid counterpart.
- That any model is more accurate than another, in either language.
- That crop views add or remove value — they were never run.
- Anything about English audio or bilingual behavior.

## Checklist for the reader

- [x] Every number above traces to a preserved JSON/log in this tree (see `results/evidence.md`).
- [x] No transcription-derived entity was used to judge any vision output.
- [x] No accuracy percentage or model/sampling winner is claimed.
- [x] Crop views: marked only, never inferred.
- [ ] Human labels for the 14 baseline frames (+ marked crops) — **still missing; required before deciding anything**.
