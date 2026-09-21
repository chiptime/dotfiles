# TikTok Ingest — Local Validation Benchmark (v2, 2026-09-18)

Persistent, auditable copy of the validation benchmark that stress-tested the TikTok ingest PRD's local pipeline end to end: public byte-identical video acquisition, CPU-only extraction, gated Whisper transcription, three local vision models under strict VRAM/swap gates, and entity-level concordance against a human-verified reference. Everything under this directory is the source of record; the original run lived in volatile `/tmp` and is expected to disappear.

> **Volatile origin warning.** The original tree `/tmp/opencode/tiktok-validation-v2/` is ephemeral (it was already wiped once by a host reboot on the run day). This directory is the persistent copy. Do not treat `/tmp` paths in the snapshots as live data.

## What question does this benchmark answer?

1. Can the whole local stack acquire and process a public TikTok video with no credentials, no private API, and byte-level identity guarantees (SHA-256 re-acquisition)?
2. Is Whisper's language detection consistent enough to trust autodetect vs pinned language? (A/B: 59/59 byte-identical segments on Spanish input.)
3. Does richer frame sampling (14 exact vs 64 hybrid) change model behavior on identical pixels? (Same pixels + same payload still diverge: median sequence ratio 0.842 — sampling stochasticity is real.)
4. Which local VLM reads on-screen text closest to a strong reference? (`qwen3.5:9b` 66.9% exact concordance at 3.52 s/frame vs `qwen3.8:27b` 65.3% at 8.44 s/frame vs `qwen2.5vl:7b` 39.8% at 1.40 s/frame — CONCORDANCE, not accuracy.)

## Hardware assumed

- NVIDIA RTX 4090, 24 GiB (24,564 MiB) — all models ran fully in VRAM, zero CPU offload.
- Linux host with ambient background swap activity — the reason the swap gate measures delta from an immediate per-phase baseline (see `methodology.md` §6, §8).
- Podman 4.9.3 for the network-isolated CPU extraction container.

## Quick path — re-run end to end from scratch

1. **Acquire**: isolated venv, `yt-dlp==2026.08.19` (`--no-deps`), ONE execution, retries disabled; accept the download only if the MP4/WebVTT SHA-256 match the recorded source hashes. Caps: 25 MiB MP4 / 1 MiB VTT / 100 MiB total.
2. **Extract (CPU, container, no network)**: FFmpeg 4.4.2 in `podman run --network none --read-only`; 16 kHz mono WAV; 24 exact historical frames via `select='eq(pts,N)+…'` (PTS = seconds ÷ time_base); 64 hybrid frames via `scripts/extract_frame_candidates.py` (2 FPS + scene + lower-third text detection, dedup 0.08 s, budget 64).
3. **Language phase (gated)**: faster-whisper 1.2.1 large-v3 FP16 beam 3; A = autodetect, B = sample-detect then pin; pass requires byte-equal segment lists. Gates: swap delta ≤ 512 MiB, VRAM delta limit, immediate baseline.
4. **Vision phases (gated, sequential)**: stop the whisper container COMPLETELY; enforce ≥ 20,480 MiB free VRAM before each Qwen load; one model at a time via `scripts/run_vision.py`; harmonized payload (`temperature=0.1`, `num_predict=1024`, `num_ctx=4096`, `think=false`); unload + verify empty `/api/ps` after every phase; no retries after any gate fires.
5. **Compare**: re-run `scripts/compare_ground_truth.py` against `reference/ground-truth-labels.json` to regenerate entity-level concordance.

Full protocol, gates, versions, and pitfalls: `methodology.md`.

## File map

| Path | What it is |
|---|---|
| `methodology.md` | Complete reproducible protocol: phases, payloads, gates, reference methodology, pitfalls, versions and hashes |
| `reference/ground-truth-labels.json` | Copy of `human-labels.json`: 24-frame reference labels with `human_verification` (bruno) and `provenance_note` intact |
| `results-snapshot/comparison.md` | Raw-output comparison: language A/B, sampling 14 vs 64, three VLMs — operational metrics and what is NOT proven |
| `results-snapshot/comparison-ground-truth.md` | Entity-level concordance of the three VLM configs vs the reference, plus hybrid-pair sampling variance |
| `results-snapshot/evidence.md` | Evidence ledger: environment/versions, per-phase metrics, incidents 1–8, declared unknowns, restoration state |
| `results-snapshot/recommendation.md` | Conditional next-trial design (no architecture selection), blocking pendings, decision rule |
| `results-snapshot/ground-truth-comparison.json` | Full entity-by-entity concordance data (auditable; every entity carries its classification and response sha256) |
| `results-snapshot/language-results.json` | Language A/B raw data: runtime, VAD windows, detections, all 59 segments per configuration |
| `results-snapshot/sampling-manifest.json` | 64 hybrid frames: PTS, timestamps, selection reasons, crop candidates, per-frame sha256/size |
| `scripts/extract_frame_candidates.py` | Hybrid frame sampler (CPU-only, stdlib + ffmpeg) |
| `scripts/lang_experiment.py` | Language A/B runner (faster-whisper) |
| `scripts/run_vision.py` | Gated VLM runner (single model, harmonized payload, VRAM + swap gates) |
| `scripts/compare_ground_truth.py` | Deterministic CPU concordance analyzer (reference vs VLM responses) |

## SHA-256 of copied files (traceability)

Copied verbatim from `/tmp/opencode/tiktok-validation-v2/` on 2026-09-18.

| File in this repo | SHA-256 |
|---|---|
| `reference/ground-truth-labels.json` | `215d82a8b847dfed34d6f9a934c857d79e8c4d03633004065bf6d543acba7113` |
| `results-snapshot/comparison.md` | `5469fc45c788d58c608dd7cf572619362a87ddb169967b5b927d14dbda4e7061` |
| `results-snapshot/comparison-ground-truth.md` | `cb867786b1b85cd9c5d84045976f16397d861d5f8687efa9bf14fce8e06e55ac` |
| `results-snapshot/evidence.md` | `1196db996ab3f0bc606f43ef3a562d6c8db365f58af7ea3d05d332ea82929e6e` |
| `results-snapshot/recommendation.md` | `39b095a469890b22930957fdbab28c0e1a8e098808b329808afc7624ce16952f` |
| `results-snapshot/ground-truth-comparison.json` | `db3b26d0eaf40c3df68b96ed7c9f3c05d40ce2ba1a244d9cab70818a06d61f66` |
| `results-snapshot/language-results.json` | `62f3716d4a71a791580c2fb6e2427b6e83b79860fd91890e5b68faf02597381d` |
| `results-snapshot/sampling-manifest.json` | `292ad618fb37a6226c77ec5fe4abf3c871b5e6fef2e5d180533609147bcd35e7` |
| `scripts/extract_frame_candidates.py` | `b32210c6a718d88fd93ed91c0050ce158f073aa501b51e04fb6e711230b37738` |
| `scripts/lang_experiment.py` | `b3ac3d2b1a73543f6f784d2e54c92f2336bbff1a6622b6fd9d72c3a8a3500bbe` |
| `scripts/run_vision.py` | `6831f63629b6822ad753b769633e2ec2bbefefe31a619aa624266ab9e86d0f40` |
| `scripts/compare_ground_truth.py` | `872c816960e40249c5284e54b5a0decc7b037b4520f015df265a5343ef404a76` |

## Known limitations

1. **Reference is not blind ground truth.** The labels are a strong-cloud-VLM (Gemini Flash 3.8 High) transcription reviewed post-hoc by the human labeler. All rates are concordance with that reference — an upper bound on correctness, never accuracy — with a documented partial circularity.
2. **English case pending.** The clip contained no English speech; language validation covers Spanish only (`pending_no_input`). No bilingual claim is supported.
3. **Sampling variance is material.** Byte-identical pixels produced different outputs under the same payload (median baseline-vs-hybrid sequence ratio 0.842; worst 0.058). Frame-level conclusions do not generalize; aggregate per video.
4. **Metrics measure agreement, not correctness.** Invented rates are upper bounds (dense system-monitor frames carry summarized reference labels); `done_reason: length` outputs are truncated and not fully comparable.
5. **Snapshot revision caveat.** `results-snapshot/ground-truth-comparison.json` embeds `reference_sha256 77b0cb6b…`, which matches an earlier revision of the labels file; the copied `reference/ground-truth-labels.json` (hash above) includes the final `human_verification` block added afterwards. Re-run `scripts/compare_ground_truth.py` against the copied labels to regenerate concordance against the current revision.
6. **No seed control.** No random seed was set or recorded anywhere in the pipeline; determinism controls are limited to decoding parameters (beam 3, temperature).

## Next step

Read `results-snapshot/recommendation.md` for the conditional next-trial design and its blocking pendings (independent human verification of critical reference fields; English-input trial). No model or sampling-strategy selection is made by this benchmark.
