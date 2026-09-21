# Evidence Ledger — TikTok Validation v2 (final, 2026-09-18)

**Purpose.** Single verifiable index of every metric, artifact, version, command path, declared UNKNOWN, and incident for the completed experiment (acquisition → language A/B → vision 3-A/3-B(+retry)/4-1/4-2 → restoration). Factual only; interpretation lives in `results/comparison.md`, decisions in `results/recommendation.md`.

## 1. Environment and versions (observed, with source)

| Component | Value | Source |
|---|---|---|
| Host GPU | NVIDIA GeForce RTX 4090, 24,564 MiB total, driver reports CUDA 13.1 era | `results/preflight/session2-baseline.json`, session2 summary |
| Podman | 4.9.3 | `logs/commands.md` |
| FFmpeg/ffprobe (extraction container) | 4.4.2-0ubuntu0.22.04.1, image `localhost/voice-assistant_whisper:latest`, image ID `51b152706d…` | `logs/commands.md` (preflight `ffmpeg -version`) |
| yt-dlp | 2026.08.19, isolated venv, `--no-deps` | `logs/commands.md`, `logs/pip-install.log` |
| Ollama (isolated) | 0.34.1, official archive sha256 `f361dc3992ec07e4ad429f4bb2d10d4663ba2c295f9a9a688c7d52f4ba650034`, server on 127.0.0.1:11435 only | `results/execution-summary.json`, `logs/inference/ollama-0.34.1*.log` |
| Ollama (global) | 0.14.2, untouched; snapshot before/after: 18 files, zero changes | `results/preflight/global-ollama-session2-{before,after}.json` |
| faster-whisper / CTranslate2 | 1.2.1 / 4.8.1 (4.8.1 wheel fetched from PyPI into temp tree — declared deviation), model large-v3, CUDA FP16, beam 3 | `results/lang/language-results.json` |
| Models | qwen2.5vl:7b, qwen3.5:9b (shared, hash-verified against global store), qwen3.8:27b Q4_K_M (five blobs, `sha256` verified, sum exactly 17,741,872,154 B; manifest 950 B — letter deviation, reported) | `results/preflight/shared-model-verification.json`, `results/preflight/qwen38-download-verification.json` |

## 2. Metrics per phase (load, inference, VRAM, RAM, swap, offload, done_reasons)

All values from the preserved `*-metrics.json` / `summary.json` files; gates: VRAM free ≥ 20,480 MiB before load, per-phase VRAM delta limit, swap-I/O delta ≤ 536,870,912 B from the **immediate phase baseline**.

| Phase | Model / frames | Load | Eval median | VRAM delta (limit) | Swap delta | RAM min avail | Offload | done_reasons | Gates |
|---|---|---|---|---|---|---|---|---|---|
| 2 Language | faster-whisper large-v3, CUDA FP16 | 3.44 s | A 8.65 s / B 8.05 s total | peak total 5,944 (Δ 4,406) | 334,725,120 B | 6.67 GB | n/a (Whisper) | n/a | passed |
| 3-A | qwen2.5vl:7b × 14 | 18.9 s | 1.40 s | 6,986 (18,432) | 148,774,912 B | 7.16 GB | 29/29 | 13 stop / 1 length | passed |
| 3-B initial | qwen2.5vl:7b × 16/64 | (same load profile) | 1.02 s | peak total 9,492 | **551,821,312 B → STOP** | 7.29 GB | 29/29 | 16 stop | gate stop, final |
| 3-B retry | qwen2.5vl:7b × 48 | 5.12 s | 1.38 s | 7,005 (18,432) | 144,683,008 B | 5.21 GB | 29/29 | 43 stop / 5 length; 0 empty | all passed |
| 4-1 | qwen3.5:9b × 14 | 17.2 s | 3.52 s | 6,756 (12,288) | 280,145,920 B | 7.29 GB | 34/34 | 11 stop / 3 length | passed |
| 4-2 probe | qwen3.8:27b × 1 ctx2048 | 15.7 s | 2.5 s | 18,138 (20,480) | 361,799,680 B | 9.21 GB | 66/66 | stop | passed |
| 4-2 probe | qwen3.8:27b × 1 ctx4096 | 12.5 s | 2.5 s | 18,278 (20,480) | 183,181,312 B | 10.05 GB | 66/66 | stop | passed |
| 4-2 | qwen3.8:27b × 14 | (3rd 66/66 load) | 8.44 s | 18,465 (20,480), peak total 20,935 | 313,974,784 B | 4.33 GB | 66/66 | 10 stop / 4 length | passed |

Unload discipline: explicit `keep_alive=0` unload + `/api/ps` empty check after every phase; post-phase VRAM returned within +71 MiB of the 2,443 MiB session baseline (tolerance ±512); after the 3-B retry, −26 MiB. Evidence: `results/vision/session2-visual-phases-summary.json` (`unload_and_baseline`), `results/vision/phase-3b-retry-status.json`.

Baseline hygiene: whisper container fully stopped (SIGTERM→SIGKILL after 10 s timeout, restarted after) before gated loads; free-VRAM gate re-checked per phase (e.g. 21,610 MiB free before the retry load). Evidence: `results/preflight/phase-3b-retry-baseline.json`, `baseline-after-stop.json`, `session2-baseline.json`.

## 3. Hashes, paths, commands

- Tree-wide manifest: `SHA256SUMS` (128 entries, verified 128/128 before inference — `results/preflight/sha256-verification.log`, `logs/checksum-verify.log`).
- Results manifest: `results/RESULTS_SHA256SUMS` (regenerated after this synthesis added files).
- Sampling provenance: `sampling-manifest.json` — 64 frames with per-frame `index`, `filename`, `pts`, `timestamp_seconds_computed` vs `ffmpeg_reported` (0 mismatches), `selection_reasons` (37 uniform / 16 scene / 28 text-region, non-exclusive), `is_crop_candidate` (16 marked, never inferred), `sha256`, `size_bytes`. All 64 hashes re-verified against the files on disk during this synthesis; errors list empty.
- The 14 exact historical frames: verified byte-identical to `SHA256SUMS` (14/14); timestamps and run order recorded in `results/execution-summary.json` (`exact_14_frame_set`).
- Command ledger (verbatim commands, versions, timestamps): `logs/commands.md`.
- Per-frame raw evidence: `results/vision/<run>/NNN-<frame>-payload.json` + `NNN-<frame>-response.json` (payload contains the exact request incl. options; response contains `done_reason`, `load_duration`, `prompt_eval_count`, `eval_duration`, text).
- Pairing dataset produced by this synthesis: `results/analysis-pairs.json` (frame↔hybrid sha matches, per-frame URL/name/doubt extraction, model table).

## 4. Declared UNKNOWNs

- **Day-1 qwen3.8 probe (stopped during load): no metrics JSON exists in this tree.** The temporary tree was rebuilt on 2026-09-18 morning after a reboot cleared `/tmp`; the only surviving record of that event is a preserved session observation (Engram #8936): pull 1,589 s; first ctx2048 probe stopped during model load when the cumulative 512 MiB swap gate fired (537,956,352 B); the swap baseline predated the 26.5-minute pull, so the trigger could not be attributed to the load; observed VRAM peak only 2,817 MiB; no OOM, no confirmed offload; strict no-retry honored. This gap directly motivated the immediate-baseline gate measurement used by every session-2 phase.
- **Random seeds:** no seed was set or recorded anywhere in the pipeline; sampling parameters (Whisper beam 3, temperature 0.0/0.1 as configured) are the only determinism controls. Declared unknown.
- **Runtime-imposed parameters:** per-model server-derived values (KV sizing, mmap disable under memory pressure — visible in `logs/inference/*.log` —, image token budgeting, VRAM-based default context when `num_ctx` is unset) were not forced equal across models. Where the runtime imposed context/payload differences in this day's runs, they are part of the comparison's limits, not hidden.
- **ffmpeg exact binary of the day-1 (pre-reboot) extraction:** the current tree's extraction used FFmpeg 4.4.2-0ubuntu0.22.04.1 (recorded); whether day-1 used a byte-identical binary is not provable post-reboot.
- **`~/.ollama/cache/model-recommendations.json`: not provably intact.** The first isolated 0.34.1 start inherited the real HOME and loaded+persisted that cache file (log lines at 08:59); no pre-run snapshot exists; isolation was corrected afterwards and session-2 before/after snapshots (18 files incl. that cache entry) are identical.
- **`num_ctx` probes for qwen3.8 exist only as 1-frame probes**; ctx 4096 was chosen for the 14-frame run on probe compatibility (66/66 layers each), not on a quality measurement.

## 5. Incident traceability (all reported, none hidden)

| # | Incident | Verified facts | Evidence |
|---|---|---|---|
| 1 | Oversized isolated runtime (day 1 of this tree) | Official Ollama 0.34.1 archive = 1,429,323,296 B (1.43 GB) > authorized 262,144,000 B (250 MiB). It was transferred once, official checksum verified, 28,132,240-B executable retained, oversized archive deleted. Later explicitly re-authorized (≤5 GB), checksum verified before execution, fully extracted (`lib/ollama/llama-server`), which unblocked GPU inference. Prior blocked 3-A attempts preserved (3 metrics JSONs + payload-only dir, HTTP 500 before model load). | `results/execution-summary.json` (`ollama_runtime`, `phase_3a…` failed attempts), `logs/inference/ollama-0.34.1.log` (llama-server-not-found errors), `results/vision/phase-3a-metrics-attempt-{1,2,3}-*.json` |
| 2 | `~/.ollama/cache` mutation via inherited HOME | First isolated server start (08:59) read and rewrote `/home/bruno/.ollama/cache/model-recommendations.json` before isolation was corrected; declared not provably intact; never modified again; session-2 snapshots identical before/after. | Log lines `loaded/persisted model recommendations snapshot path=/home/bruno/.ollama/cache/...`; `results/preflight/global-ollama-session2-{before,after}.json` |
| 3 | Pull monitor killed transient `-partial` staging files | First qwen3.8 pull attempt (09:43) was SIGTERM'd by the size monitor: tree 17,741,872,198 B = authorized blob sum + 44 B from a transient `-partial` staging file → false trigger; original attempt record preserved. Corrected resume monitor excludes staging files; completed pull verified all five blob digests+sizes; blobs sum exactly the authorized 17,741,872,154 B; the 950-B registry manifest is the only letter deviation. | `results/preflight/qwen38-download-verification-attempt-1-transient-partial-size-gate.json`, `results/preflight/qwen38-download-verification.json`, `logs/inference/qwen38-pull*.log` |
| 4 | `podman pause` does not free VRAM | Whisper GPU memory is only released by a full container stop, not a pause: gated phases therefore used full stops (SIGTERM→SIGKILL after 10 s, same container restarted, same ID/image verified healthy). In-tree contrast: 5,664 MiB used with whisper running vs 1,536 MiB after stop. The pause-vs-stop lesson originates in operator session notes; this tree contains no pause experiment artifact (declared). | `results/preflight/baseline-before-stop.json`, `baseline-after-stop.json`, `phase-3b-retry-baseline.json`, `final-restored-state-session2.json` |
| 5 | First run of the day stopped by swap **at load** (qwen3.8, separate lesson) | Day-1 ctx2048 probe stopped during model load by the cumulative 512 MiB swap gate (537,956,352 B) whose baseline predated the 26.5-min pull — ambient contamination made attribution impossible; no retry, later re-run from scratch in session 2 with immediate-baseline gates (probes + 14/14 passed). No metrics JSON survives in this tree (post-reboot rebuild); preserved session observation is the record. | Engram observation #8936 (preserved); contrast: `results/vision/session2-visual-phases-summary.json` |
| 6 | 3-B initial run stopped by swap gate at 16/64 | 551,821,312 > 536,870,912 B from immediate baseline; stop was final per authorization; 16 outputs preserved untouched; user-authorized same-day retry of the remaining 48 with identical methodology passed all gates (swap delta 144,683,008 B ≈ 138 MiB), confirming ambient host swap as the earlier cause. | `results/vision/phase-3b-gate-stop.json`, `phase-3b-metrics.json`, `phase-3b-retry-{metrics,status}.json`, retry summary |
| 7 | Typo found in `results/execution-summary.json` (found, not corrected) | The embedded SHA-256 for `frame_17.png` is 63 hex chars (one `d` omitted). Disk file, `SHA256SUMS`, and `phase-3a…/summary.json` all carry the correct 64-char hash and agree; `hybrid_044.png` is byte-identical to `frame_17.png` per `SHA256SUMS`. Original outputs are never rewritten in this phase, so the typo is documented in place. | `SHA256SUMS` lines 33/84; `results/execution-summary.json` line 82 |
| 8 | Tree rebuilt after reboot | `/tmp` was cleared by a host reboot before 2026-09-18 08:30; the tree was rebuilt with the identical authorized procedure (download 08:36, extraction 08:40, language 08:56). Day-1 artifacts other than the rebuilt ones are not in this tree. | Engram #8942 (08:30 audit: tree absent), tree file mtimes 08:34+ |

## 6. Restoration (end state)

- Whisper container restored: same container ID `7be942cd0fda…`, same image ID `32225d6e788f…`, healthy, health endpoint `ok`.
- Isolated Ollama stopped, port 11435 closed, no auxiliary containers, no ollama processes.
- Global Ollama: bit-identical 18-file snapshot before/after; port 11434 closed as before.
- Final VRAM 2,514 MiB vs 2,443 MiB session baseline (+71 MiB, within ±512).
- No dotfiles/PRD/SDD/Git writes by the experiment; all outputs under `/tmp/opencode/tiktok-validation-v2/`.
- Evidence: `results/preflight/final-restored-state-session2.json`, `voice-assistant-whisper-{before,after}.json`.

## 7. Verification performed by this synthesis phase (CPU-only, textual)

- All 268 JSON files under `results/` + `sampling-manifest.json` parse (`json.load` OK).
- Language A/B: 59 vs 59 segments, canonical-JSON byte-equality re-verified true.
- `sampling-manifest.json`: 64/64 frame hashes re-verified against disk; crop candidates counted = 16; selection reasons tallied.
- 14 historical frames re-hashed: all match `SHA256SUMS`; the `execution-summary.json` embedded-copy typo (incident 7) identified.
- done_reasons and eval medians recomputed from raw responses; they match the recorded summaries.

## 8. Cross-reference (post-synthesis addition)

- Entity-level concordance of the three VLM configs (and the 11 byte-identical 3-B hybrid pairs) against `ground-truth/human-labels.json` → `ground-truth/ground-truth-comparison.json` + `results/comparison-ground-truth.md` (2026-09-18, CPU-only textual analysis via `scripts/compare_ground_truth.py`; reference status documented there: VLM-assisted, human-verified, not blind human ground truth).
