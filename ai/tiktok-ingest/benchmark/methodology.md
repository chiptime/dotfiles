# TikTok Validation v2 — Benchmark Methodology

Reproducible protocol for the local-pipeline validation benchmark of the TikTok ingest PRD: one public video, byte-identical re-acquisition, CPU-only extraction, gated Whisper and vision phases, and entity-level concordance against a human-verified reference. This document records HOW the benchmark was run and everything learned while running it. Interpretation of results lives in `results-snapshot/comparison*.md`; the decision posture lives in `results-snapshot/recommendation.md`.

## 1. Scope and outcome

- Source: public TikTok video ID `7684788219510033665` (Spanish narration, ~162.5 s, screencast of developer tools).
- Question answered end to end: can a fully local stack (yt-dlp + FFmpeg + faster-whisper + Ollama VLMs) acquire, extract, transcribe, and read a TikTok video, and how do its outputs compare against a reference?
- Run date: 2026-09-18 (two sessions; the `/tmp` tree was rebuilt that morning after a host reboot cleared it).
- Explicit exclusions throughout: cookies, credentials, authenticated browsing, private App API, challenge/captcha solving, PRD/SDD edits, Git operations.

## 2. Environment and exact versions

| Component | Version | Reference hash where recorded |
|---|---|---|
| Host GPU | NVIDIA GeForce RTX 4090, 24,564 MiB total | n/a |
| Host OS swap context | Ambient background swap activity present on host (motivates gate attribution rule, §8) | n/a |
| Podman | 4.9.3 | n/a |
| FFmpeg / ffprobe (extraction container) | 4.4.2-0ubuntu0.22.04.1, image `voice-assistant_whisper:latest`, ID `51b152706d…` | n/a |
| yt-dlp | 2026.08.19, isolated venv, installed `--no-deps` | n/a |
| Ollama (isolated runtime) | 0.34.1, server bound to 127.0.0.1:11435 only | official archive sha256 `f361dc3992ec07e4ad429f4bb2d10d4663ba2c295f9a9a688c7d52f4ba650034` |
| Ollama (global install on host) | 0.14.2, untouched; 18-file snapshot bit-identical before/after | n/a |
| faster-whisper / CTranslate2 | 1.2.1 / 4.8.1; model `large-v3` (HF snapshot `edaa852ec7e145841d8ffdb056a99866b5f0a478`), CUDA FP16, beam 3 | n/a |
| Vision models | `qwen2.5vl:7b`; `qwen3.5:9b` (shared with global store, hash-verified); `qwen3.8:27b` Q4_K_M (five blobs, sha256-verified, sum exactly 17,741,872,154 B + 950 B manifest) | blob digests in `results-snapshot/ground-truth-comparison.json` provenance and Ollama manifests |
| Whisper ASR note | The isolated Ollama 0.34.1 runtime archive weighs 1,429,323,296 bytes (1.43 GB) — budget for it explicitly | archive sha256 above |

## 3. Phase 1 — Public acquisition and CPU extraction

### 3.1 Acquisition (byte-identical by SHA-256)

1. Create an isolated virtualenv; install `yt-dlp==2026.08.19` with `--no-deps` (no dependency tree).
2. Patch the TikTok extractor at runtime to hard-block App API calls and challenge solving.
3. Execute ONE download (retries disabled everywhere, socket timeout 30 s, outer `timeout 600`):
   - MP4 size cap 25 MiB (26,214,400 B); Spanish WebVTT cap 1 MiB; total downloaded-data cap 100 MiB.
   - No playlist continuation, no overwrite.
4. Verify file type signatures, sizes, SHA-256, and ffprobe metadata; compare against preserved historical evidence. Identity must match exactly before anything downstream runs; on match, the exact historical frame timestamps are reused.

This is what makes the benchmark re-runnable: a fresh download is accepted only if it reproduces the recorded source SHA-256, so every derived artifact binds to the same bytes.

### 3.2 CPU extraction (container, no network, no GPU)

Run inside Podman with `--network none --read-only` (tmpfs `/tmp` only), FFmpeg 4.4.2, 20-minute timeout, 2 GiB output cap:

1. Integrity pass: `ffmpeg -v error -i source.mp4 -f null -`.
2. Audio: 16 kHz mono PCM S16LE WAV (`-vn -acodec pcm_s16le -ac 1 -ar 16000`).
3. Historical exact frames: one `select` filter per exact PTS value (`eq(pts,N)`) with `showinfo`, `-vsync vfr`, written as `frame_%02d.png`. Source time base `1/12800`; timestamp seconds = PTS × time_base.
4. Hybrid candidates via `scripts/extract_frame_candidates.py` (stdlib + ffmpeg/ffprobe only):
   - Uniform coverage: 2 FPS scan, 4.0 s minimum gap.
   - Scene-change detection: threshold 0.3.
   - Lower-third text-region detection: threshold 0.15, band = 0.35 of frame height at y-offset 0.65.
   - Conservative merge window 0.08 s; frame budget 64; crop-candidate cap 16.
   - Result recorded in `sampling-manifest.json`: 325 raw 2 FPS candidates → 64 retained (selection reasons 37 uniform / 16 scene / 28 text-region, non-exclusive), 16 crop candidates marked but never inferred.
5. Finalize manifest with per-frame `sha256`, `size_bytes`, computed vs ffmpeg-reported timestamps (0 mismatches).

## 4. Phase 2 — Language A/B (faster-whisper 1.2.1, large-v3, FP16, beam 3)

Both configurations transcribe the SAME untouched full WAV; VAD is used only to choose detection windows (threshold 0.5, min silence 2000 ms, speech pad 400 ms).

| Step | Configuration A | Configuration B |
|---|---|---|
| Detection | autodetect over full audio (`language=null`) | two 30 s VAD-selected windows sampled (0–30 s, 98.27–128.27 s); majority vote, tie broken by mean probability |
| Transcription | full WAV with detected language | full WAV with pinned language |
| Result on this clip | `es` @ 0.998 (uncalibrated) | `es` @ 0.998 / 0.999; 59 segments |

- Acceptance: canonical-JSON comparison of the two 59-segment lists must be byte-equal. It was (verified twice, including during final synthesis).
- The 0.998/0.999 figures are uncalibrated heuristic probabilities — never report them as confidence or accuracy.
- English case: `pending_no_input` by design — the authorized clip contains no English speech; no bilingual claim may be drawn from this run.
- Phase gates apply here too (swap delta ≤ 512 MiB, VRAM delta limit); the whisper container is the ONLY GPU tenant during this phase.

## 5. Phases 3–4 — Vision runs (Ollama 0.34.1, isolated, port 11435)

| Phase | Model | Frames | Notes |
|---|---|---|---|
| 3-A | `qwen2.5vl:7b` | 14 exact historical frames | 14/14 completed; earlier blocked attempts (HTTP 500 before model load) preserved as payload-only records |
| 3-B initial | `qwen2.5vl:7b` | 64 hybrid frames | gate-stopped at 16/64 by the swap gate; outputs preserved, no `summary.json` written |
| 3-B retry | `qwen2.5vl:7b` | remaining 48 (`hybrid_017`–`064`) | user-authorized same-day retry, identical methodology and gates; passed all gates, 0 empty responses |
| 4-1 | `qwen3.5:9b` | same 14 frames | `think=false` |
| 4-2 probes | `qwen3.8:27b` Q4_K_M | 1 frame at ctx 2048 and ctx 4096 | both compatible (66/66 GPU layers); ctx 4096 chosen for the 14-frame run on compatibility, not quality |
| 4-2 | `qwen3.8:27b` Q4_K_M | same 14 frames | `think=false` |

### Harmonized payload (every request, every model)

```json
{
  "prompt": "<fixed OCR/extraction PROMPT constant, see scripts/run_vision.py>",
  "images": ["<single base64 frame>"],
  "stream": false,
  "keep_alive": "15m",
  "think": false,
  "options": {"temperature": 0.1, "num_predict": 1024, "num_ctx": 4096}
}
```

- One image per request; no transcript hints; the full request (with image replaced by its sha256) is persisted as `NNN-<frame>-payload.json` next to the raw response.
- `qwen2.5vl:7b` does not accept `think`; it is omitted there, present as `think=false` for qwen3.5/qwen3.8.
- Known limit: server-derived runtime parameters (KV-cache sizing, mmap/load mode under host memory pressure, image-token budgeting) are NOT forced equal across models. Model comparisons are informative, not controlled.

### Observed phase metrics (RTX 4090 24 GiB, zero CPU offload, all gates enforced)

| Phase | Load | Eval median/frame | VRAM peak delta (limit) | Swap delta (limit 512 MiB) | done_reasons |
|---|---|---|---|---|---|
| 2 Language | 3.44 s | A 8.65 s / B 8.05 s total | 4,406 MiB | 334.7 MB | n/a |
| 3-A qwen2.5vl ×14 | 18.9 s | 1.40 s | 6,986 MiB (18,432) | 148.8 MB | 13 stop / 1 length |
| 3-B initial ×16 | — | 1.02 s | 9,492 MiB peak | 551.8 MB → STOP | 16 stop |
| 3-B retry ×48 | 5.12 s | 1.38 s | 7,005 MiB (18,432) | 144.7 MB | 43 stop / 5 length |
| 4-1 qwen3.5 ×14 | 17.2 s | 3.52 s | 6,756 MiB (12,288) | 280.1 MB | 11 stop / 3 length |
| 4-2 probe ctx2048/4096 | 15.7 / 12.5 s | 2.5 s | 18,138 / 18,278 MiB (20,480) | 361.8 / 183.2 MB | stop |
| 4-2 qwen3.8 ×14 | (3rd 66/66 load) | 8.44 s | 18,465 MiB (20,480) | 314.0 MB | 10 stop / 4 length |

Unload discipline after every phase: explicit `keep_alive=0` unload plus empty `/api/ps` check; post-phase VRAM within +71 MiB of the 2,443 MiB session baseline (tolerance ±512 MiB).

## 6. Safety gates (non-negotiable, enforced in `scripts/run_vision.py`)

1. **Free-VRAM gate**: ≥ 20,480 MiB free before EACH Qwen load (`nvidia-smi`), on top of a fully stopped whisper container.
2. **Per-model VRAM delta limits**: 18,432 MiB (qwen2.5vl:7b), 12,288 MiB (qwen3.5:9b), 20,480 MiB (qwen3.8:27b), measured from the immediate phase baseline.
3. **Zero CPU offload**: all layers must be GPU-resident (29/29, 34/34, 66/66 observed). An offload is a failed run, not a slow run.
4. **Swap gate**: cumulative swap I/O delta (`pswpin + pswpout` from `/proc/vmstat` × page size) ≤ 536,870,912 B (512 MiB) measured from the IMMEDIATE phase baseline — never from an older baseline (§8, pitfall 4).
5. **No retries after a gate fires.** A gate stop is final for that authorization window; resuming requires explicit new user authorization (this happened once: 3-B retry of the remaining 48 frames, identical methodology).
6. **Never Whisper + vision simultaneously**: the whisper container is fully stopped (SIGTERM→SIGKILL after 10 s, restarted afterwards) before any gated VLM load.
7. Baseline hygiene: snapshots of GPU state and global Ollama state before/after every phase; global Ollama (0.14.2) must remain bit-identical (18-file snapshot verified).

## 7. Reference methodology (what the labels are and are not)

- The reference (`reference/ground-truth-labels.json`) is a **VLM-assisted transcription verified by a human** (`labeling_method: gemini_flash_3.8_high_assisted_human_verified`, labeler bruno, `human_verification.status: human_verified_reference`). It is **NOT independent blind human ground truth**.
- Consequence: every metric computed against it is **concordance/agreement**, never accuracy. A local model can score high by mirroring the cloud VLM's reading and still be wrong (partial circularity; see `results-snapshot/comparison-ground-truth.md`).
- Comparison (`scripts/compare_ground_truth.py`, CPU-only, difflib-based, deterministic):
  - Entity types: `name`, `repo_or_path`, `url_domain`, `command`; text regions compared via token coverage + difflib ratio.
  - Per-entity classes: `exact` (normalized containment), `partial` (substring/prefix/token ≥4 chars), `missed`, `invented` (output entity matching no reference entity), `unknown` (invented candidate inside a reference-illegible zone).
  - Markers honored: reference `[cut]` caps classification at partial (partial-max); reference `[illegible]` suppresses extraction and downgrades output-only entities from invented to unknown.
  - `divergence_score = missed_rate + invented_rate + (1 − mean token coverage)`.
  - Scope: 14 baseline frames × 3 configs (42 comparisons) + 11 byte-identical hybrid pairs re-scored for qwen2.5vl. Non-identical 3-B frames are never compared against frames they do not depict.
- Headline concordance (upper bounds, not accuracy): `qwen3.5:9b` 66.9% exact / 14.8% missed / 24.0% invented; `qwen3.8:27b` 65.3% / 15.7% / 24.3%; `qwen2.5vl:7b` 39.8% / 45.8% / 5.9%.

## 8. Pitfalls learned (each one cost a real incident)

1. **`podman pause` does not free VRAM.** Only a full container stop releases GPU memory (observed 5,664 MiB running vs 1,536 MiB after stop). Gated phases use full stop + restart with same ID/image verified healthy.
2. **Isolate HOME, not only `OLLAMA_MODELS`.** The first isolated Ollama start inherited the real HOME and rewrote `~/.ollama/cache/model-recommendations.json`. Set `HOME` to an isolated directory for the isolated server; snapshot global state before/after.
3. **A pull monitor must not kill `-partial` staging blobs.** The first qwen3.8 pull was SIGTERM'd by a size monitor that counted a transient `-partial` file (+44 B over budget) — a false trigger. Corrected monitors exclude staging files; resume verified all five blob digests.
4. **The swap gate can fire from ambient host swap, not your workload.** The 3-B initial stop (551.8 MB delta) was later shown to be ~1.2 GB of background `pswpout` growth. Always measure the delta from an IMMEDIATE pre-phase baseline, and attribute swap growth per process before blaming the model. Day-1's qwen3.8 probe died to exactly this mis-attribution.
5. **`fps=N` in ffmpeg rewrites PTS.** To hit exact historical timestamps, extract with an explicit `select='eq(pts,N)+…'` filter using PTS = seconds ÷ time_base, with `-vsync vfr` — never with `fps=N`.
6. **`detect_language` in faster-whisper requires a decoded array.** Pass a float32 numpy slice (`decode_audio` + slice), not a file path; record the call parameters.
7. **The isolated Ollama runtime is heavy: 0.34.1 weighs 1.43 GB.** Budget the runtime download explicitly; transfer once, verify the official checksum, delete the archive, keep only what executes.
8. **Download budgets must anticipate real artifact sizes.** The original 250 MiB runtime budget was exceeded by the official archive; the fix was an explicit re-authorization (≤5 GB) — not a silent retry. Oversized-transfer incidents are reported, never hidden.

## 9. Recorded hashes (provenance anchors)

- Reference labels (current file, this repo): `215d82a8b847dfed34d6f9a934c857d79e8c4d03633004065bf6d543acba7113` — see README for the full copied-file manifest and the revision caveat about `ground-truth-comparison.json`'s embedded `reference_sha256`.
- Ollama 0.34.1 archive: `f361dc3992ec07e4ad429f4bb2d10d4663ba2c295f9a9a688c7d52f4ba650034`.
- Per-frame sha256: `sampling-manifest.json` (64 hybrids) and `reference/ground-truth-labels.json` (24 historical frames); `frame_17.png` ≡ `hybrid_044.png` byte-identical.
- Source identity: public MP4/WebVTT re-acquisition is accepted only on exact SHA-256 match with the recorded source hashes (§3.1).

## 10. Re-run checklist

- [ ] Fresh `/tmp` workspace; isolated venv; `yt-dlp==2026.08.19 --no-deps`; one download, retries off
- [ ] Source SHA-256 matches recorded hashes before extraction
- [ ] CPU extraction in `--network none` container; exact-PTS select filter; hybrid sampler with the §3.2 parameters
- [ ] Whisper phase alone; swap/VRAM gates from immediate baseline
- [ ] Whisper container fully stopped; free-VRAM gate ≥ 20,480 MiB; one model at a time; harmonized payload; unload + `/api/ps` empty after each phase
- [ ] Global Ollama snapshot bit-identical before/after; environment restored at the end
- [ ] If reference or outputs change: re-run `scripts/compare_ground_truth.py` to regenerate concordance data
