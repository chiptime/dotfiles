# Recommendation — Conditional Next Trial (No Architecture Selection)

**Headline.** Two evidence layers now exist: (1) raw outputs with no human ground truth (`results/comparison.md`) and (2) entity-level **concordance** against a VLM-assisted reference (`results/comparison-ground-truth.md`, data in `ground-truth/ground-truth-comparison.json`). The reference was transcribed with Gemini Flash 3.8 High and human-verified post-hoc — it is **not blind human ground truth**, so every rate below is agreement with that reference, never accuracy, and carries documented partial circularity. Within those limits, the concordance results justify the **conditional** next-trial design below. Exhaustive blind human labeling remains the gold standard and stays pending. Nothing here selects a final architecture.

## Quick path

1. Read the terminology box — every number is concordance, not accuracy.
2. Read the conditional next-trial design (a)–(d) and its trigger.
3. Check the blocking pendings (d) before scheduling any GPU work.

## Terminology: what "concordance" means here

- **Reference**: `ground-truth/human-labels.json`, method `gemini_flash_3.8_high_assisted_human_verified` (per the JSON provenance block). The human review checked the VLM draft against the frame, but a blind human pass was never run; residual cloud-VLM errors are therefore not corrected. Concordance with this reference is an **upper bound on correctness**, not an unbiased estimate of it.
- **Concordance**: entity-level agreement between a local model's output and the reference (exact / partial / missed / invented). **No percentage in this document is accuracy.**

## Verified inputs (from `ground-truth-comparison.json` + `results/comparison.md` §3.2; every entity auditable)

| Config | Exact (concordance) | Missed | Invented rate | Eval median/frame | VRAM peak delta |
|---|---|---|---|---|---|
| `qwen3.5:9b` (4-1) | 66.9% | 14.8% | 24.0% | 3.52 s | 6,756 MiB |
| `qwen3.8:27b` (4-2) | 65.3% | 15.7% | 24.3% | 8.44 s | 18,465 MiB |
| `qwen2.5vl:7b` (3-A) | 39.8% | 45.8% | 5.9% | 1.40 s | 6,986 MiB |

- `qwen3.5:9b` matches or exceeds `qwen3.8:27b` on exact concordance (66.9% vs 65.3%) with ≈36% less median output volume (1,481 vs 2,325 chars — chars are the measured proxy for output size, not tokens), 2.4× lower eval median (3.52 s vs 8.44 s per frame), and 2.7× lower VRAM peak delta.
- `qwen2.5vl:7b` rarely invents (5.9%) but misses 45.8% of reference entities — a conservative-but-incomplete extractor.
- **The 24% invented rates are upper bounds.** Frames whose reference is a summarized (not exhaustive) labeling of dense system-monitor output (`frame_14`, `frame_17`, `frame_22`) inflate them: e.g. 25 of `qwen3.5`'s 133 invented entities are `frame_17` htop-style process rows that are plausibly real on-screen readings. The **genuinely suspicious cluster is `frame_07`**: both `qwen3.5` (`Google`, `Replit AI`, `Competitive.ai`, among ~50 flagged) and `qwen3.8` (`Google`, `Polar AI`, `Groq`, among ~58 flagged) emit provider names absent from the reference — hallucination candidates that a second pass must adjudicate.
- **Sampling variance**: across the 11 byte-identical baseline-vs-hybrid pairs (sha256-verified in `results/analysis-pairs.json`), the median sequence ratio is 0.842 and the worst is 0.058 (`frame_14`); 5/11 pairs reproduce their baseline at ≥0.9, and entity-set Jaccard ranges 0.385–1.000. Frame-level results do not generalize without averaging.

## Conditional next trial (explicit trigger; not a selection)

**IF** the next trial's criterion is transcriptional fidelity as measured by concordance with the strong cloud reference, **THEN**:

- **(a) Primary comparison**: `qwen3.5:9b` as the main cost/fidelity candidate; `qwen3.8:27b` reserved for high-text-density frames where richer extraction may justify 2.4× latency and 2.7× VRAM delta. `qwen2.5vl:7b` drops out of the fidelity tier (39.8% exact, 45.8% missed) and remains relevant only as a low-cost, invented-conservative option if the miss tolerance is acceptable — itself a hypothesis the trial must test, not a finding.
- **(b) Hallucination filtering is a second pass, not a property of any single model.** Design it as either cross-model checking (entities one model emits that the other does not → flag) or verification against crop re-reads, with human adjudication of flags. The `frame_07` provider names and the `frame_17`/`frame_14` repetition pathologies are the concrete seed cases.
- **(c) Hybrid sampling (14 vs 64) must be aggregated per video, never per frame.** Byte-identical pixels already diverge (median ratio 0.842, worst 0.058); only video-level aggregation over the full ~64-frame set can adjudicate the strategy.
- **(d) Blocking pendings**:
  - (d-i) **Independent human verification of the reference's critical fields (URLs, repo paths)** — the cheapest path to recover ground-truth status for at least the highest-stakes entities, weakening the circularity limitation where it matters most. Full exhaustive blind labeling of all 14 frames (plus the 16 authorized-only crop candidates in `sampling-manifest.json`) remains the gold standard and is still pending.
  - (d-ii) **English-input case** (section below).

Any definitive model or sampling-strategy selection stays out of scope until (d-i) and the divergence-pair adjudication are done.

## Pending trial: English (do not extrapolate)

The Spanish A/B validated detection consistency on one clip. An English-input trial (same A vs B design) is required before any bilingual claim. The existing 0.998/0.999 probabilities are uncalibrated heuristics on Spanish audio and justify nothing about English.

## Trial hygiene requirements (carried forward as preconditions)

1. **Explicit harmonized payloads**: log the full effective request per model (including server-derived values such as effective context, image-token budget, load mode) so model comparisons stop carrying hidden runtime deltas.
2. **Degeneration guard**: detect repetition loops and treat `done_reason: length` outputs as incomplete rather than comparable; 13/108 responses this run hit the 1024-token cap, several on the repetition path.
3. **Gate measurement from immediate baseline only** (already adopted in session 2; it is what disambiguated incidents 5 and 6 in `results/evidence.md`).
4. **Staging-file-aware size monitors** for any download (incident 3).

## Out of scope — explicit non-decisions

- **No model selection** (qwen2.5vl vs qwen3.5 vs qwen3.8) and **no sampling-strategy selection** (14 vs 64 vs crops) is made here. The conditional (a) is a trial design that holds only under its stated criterion (fidelity under concordance with the strong cloud reference); it does not survive a change of criterion and is not a choice of architecture.
- **Audio-driven visual selection is not part of this A/B.** If a future design uses transcript content to choose which frames to inspect, it inverts the PRD's mandated order (mandatory visual analysis first, audio as complementary evidence → vision→audio) into audio→vision, and would need its own explicit authorization and design change against `/home/bruno/.dotfiles/ai/tiktok-ingest/PRD.md`. This document neither proposes nor authorizes that inversion.

## Decision rule

Proceed to a model/sampling decision **only when**: the reference's critical fields (URLs/repos) pass independent human verification, each known divergence pair (`frame_14`, `frame_21`, `frame_22` and hybrids `hybrid_039`, `hybrid_058`, `hybrid_062`) is adjudicated, and the scoring protocol is written down before scores are computed. Until then, every comparative statement should cite `results/comparison.md` §4 ("What is NOT proven") and read concordance numbers from `results/comparison-ground-truth.md`.

## Checklist

- [ ] Every number quoted as concordance with a VLM-assisted reference, never as accuracy
- [ ] Conditional (a)–(d) treated as trial design, not architecture selection
- [ ] Invented-rate figures quoted as upper bounds, with `frame_07` named as the genuine-candidate cluster
- [ ] Hybrid-strategy discussion aggregated per video, never per frame
- [ ] `results/RESULTS_SHA256SUMS` entry updated for this file
