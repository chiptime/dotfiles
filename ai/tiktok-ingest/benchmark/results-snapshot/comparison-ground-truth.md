# VLM outputs vs. reference labels — entity-level concordance

**Reference status: VLM-assisted, human-verified, not blind human ground truth.** The reference (`ground-truth/human-labels.json`) was transcribed with Gemini Flash 3.8 High and reviewed by the human labeler. Every rate below is **concordance with that reference**, never accuracy. Because the reference itself is anchored on a strong cloud VLM, the ranking carries a circularity limitation (see below).

This report compares the textual outputs of the three local VLM configs (qwen2.5vl:7b phase 3-A, qwen3.5:9b phase 4-1, qwen3.8:27b phase 4-2) against the reference labels over the 14 baseline frames, plus the 11 byte-identical 3-B hybrid pairs for qwen2.5vl (sampling analysis). Full entity-by-entity data with provenance: `ground-truth/ground-truth-comparison.json`. Existing `results/comparison.md` is untouched; this file is additive.

## Quick path

1. Read the ranking table below for the headline concordance numbers.
2. Check per-frame divergences before drawing conclusions — medians hide outliers.
3. Read the sampling section if you care about 64-frame hybrid stability.
4. Any number can be audited in the JSON (every entity carries its classification).

## Per-model concordance with reference (14 frames each)

| Model | Phase | Exact | Strict-case exact | Partial | Missed | Invented rate | Mean token coverage | Median chars |
|---|---|---|---|---|---|---|---|---|
| `qwen3.5:9b` | 4-1 | 66.9% | 65.3% | 18.2% | 14.8% | 24.0% | 67.6% | 1481 |
| `qwen3.8:27b` | 4-2 | 65.3% | 62.7% | 19.1% | 15.7% | 24.3% | 71.6% | 2325 |
| `qwen2.5vl:7b` | 3-A | 39.8% | 32.2% | 14.4% | 45.8% | 5.9% | 43.9% | 685 |

Counts: `qwen2.5vl:7b` exact/partial/missed = 94/34/108 of 236 reference entities; invented 13 (+6 unknown) of 221 output entities. · `qwen3.5:9b` exact/partial/missed = 158/43/35 of 236 reference entities; invented 133 (+32 unknown) of 555 output entities. · `qwen3.8:27b` exact/partial/missed = 154/45/37 of 236 reference entities; invented 155 (+64 unknown) of 637 output entities.

**Ranking by composite concordance** (exact + 0.5·partial − missed): `qwen3.5:9b` > `qwen3.8:27b` > `qwen2.5vl:7b`

## Per-frame divergences (top 3 per model by divergence score)

### `qwen2.5vl:7b` (3-A)

| Frame | Exact | Missed | Invented | Coverage | Divergence | What went wrong |
|---|---|---|---|---|---|---|
| frame_22 | 12% | 81% | 12% | 14% | 1.80 | missed: name 'MIT'; name 'Claude Sonnet'; name 'LLM' — invented: 'docling 4/14 >13.6 24 27 60 70 21 21' |
| frame_21 | 0% | 83% | 0% | 5% | 1.78 | missed: name 'Replace'; name 'Show'; name 'Translate' |
| frame_05 | 30% | 58% | 0% | 20% | 1.38 | missed: name 'Click a'; name 'LIVE'; name 'API' |

### `qwen3.5:9b` (4-1)

| Frame | Exact | Missed | Invented | Coverage | Divergence | What went wrong |
|---|---|---|---|---|---|---|
| frame_17 | 53% | 33% | 61% | 31% | 1.64 | missed: name 'GHz'; name 'Ryzen AI'; name 'MiB' — invented: 'dncrypt_write/25'; 'kworker/u97'; 'kworker/14' |
| frame_07 | 63% | 20% | 55% | 80% | 0.95 | missed: name 'COMPRESSION'; name 'RTX'; name 'STACKED' — invented: 'Competitive.ai'; 'Google'; 'Replit AI' |
| frame_21 | 17% | 50% | 0% | 58% | 0.93 | missed: name 'Markdown'; name 'Replace'; name 'Show' |

### `qwen3.8:27b` (4-2)

| Frame | Exact | Missed | Invented | Coverage | Divergence | What went wrong |
|---|---|---|---|---|---|---|
| frame_21 | 0% | 100% | 0% | 9% | 1.91 | missed: name 'Markdown'; name 'Replace'; name 'Show' |
| frame_17 | 60% | 20% | 51% | 40% | 1.31 | missed: name 'MiB'; name 'Total'; name 'Kibp' — invented: 'kworker/u9'; 'Process List'; 'TUI' |
| frame_07 | 54% | 29% | 57% | 85% | 1.01 | missed: name 'Antigravity'; name 'Codex'; name 'Copilot' — invented: 'Google'; 'Polar AI'; 'Groq' |

Note: `frame_01` is a blank reference frame (no reference entities), so exact/missed/coverage are n/a there; only invented candidates apply. On dense system-monitor frames (`frame_14`, `frame_17`, `frame_22`) the reference is a summarized labeling, so output-only fragments that are plausibly real on-screen readings (htop process rows, benchmark cells) remain flagged as invented — treat the invented rate as an upper bound there.

## Sampling findings: 11 byte-identical hybrid pairs (3-B, qwen2.5vl)

These 11 hybrids are byte-identical re-samplings of baseline frames (sha256-verified in `results/analysis-pairs.json`), so any output difference is pure sampling stochasticity under temperature, not content difference. They are scored against the same reference frames as their baselines (`comparison_type: hybrid-vs-baseline`); non-identical 3-B frames were **not** compared against reference frames they do not depict.

| Frame | Hybrid | Baseline vs hybrid seq. ratio | Entity Jaccard | Hybrid exact vs ref | Baseline exact vs ref | Char delta |
|---|---|---|---|---|---|---|
| frame_01 | hybrid_001 | 0.906 | 0.929 | n/a | n/a | +43 |
| frame_04 | hybrid_005 | 0.928 | 1.000 | 29% | 29% | -81 |
| frame_09 | hybrid_026 | 1.000 | 1.000 | 100% | 100% | +0 |
| frame_11 | hybrid_030 | 1.000 | 1.000 | 100% | 100% | +0 |
| frame_14 | hybrid_039 | 0.058 | 0.556 | 50% | 50% | -781 |
| frame_19 | hybrid_048 | 0.655 | 0.913 | 0% | 0% | +235 |
| frame_22 | hybrid_062 | 0.151 | 0.875 | 19% | 12% | +2799 |
| frame_10 | hybrid_028 | 0.842 | 0.923 | 56% | 44% | +52 |
| frame_15 | hybrid_041 | 0.824 | 0.750 | 46% | 77% | -32 |
| frame_17 | hybrid_044 | 0.977 | 0.833 | 73% | 73% | -9 |
| frame_21 | hybrid_058 | 0.355 | 0.385 | 0% | 0% | +1145 |

- Median baseline-vs-hybrid sequence ratio: 0.842 (min 0.058, max 1.000).
- Median entity-set Jaccard: 0.913 (min 0.385, max 1.000).
- 5/11 pairs reproduce their baseline output at ≥0.9 sequence ratio.

## Reference status and circularity limitation

- **Status**: the reference is a strong-model (Gemini Flash 3.8 High) transcription reviewed post-hoc by the human labeler, who reports no significant divergence from on-screen reality. It is **not** an independent blind human ground truth.
- **Consequence**: all metrics here are *agreement with the reference*, not accuracy. A local model can score high by mirroring the cloud VLM's reading habits and still be wrong.
- **Circularity**: the human verification step checked the VLM draft against the frame, so residual cloud-VLM errors that a human would not independently reproduce are not corrected; concordance with the reference is therefore an upper bound on correctness, not an unbiased estimate of it.
- **Markers honored**: reference `[cut]` entities are capped at partial (partial-max); reference `[illegible]` zones suppress entity extraction and downgrade output-only entities there from invented to unknown.
- **Method availability**: deterministic CPU pipeline in `scripts/compare_ground_truth.py`; every entity decision is stored in `ground-truth/ground-truth-comparison.json` with the sha256 of the reference and of each response file.

## Checklist

- [ ] Ranking read as concordance, not accuracy
- [ ] Top divergent frames inspected before recommending a model
- [ ] Hybrid-pair variability considered when generalizing 14-frame results to 64 frames
- [ ] `ground-truth/ground-truth-comparison.json` parsed and audited spot checks

## Next step

Cross-check this concordance view against the resource/latency trade-off analysis in `results/comparison.md` and the decision in `results/recommendation.md` before finalizing the model choice.
