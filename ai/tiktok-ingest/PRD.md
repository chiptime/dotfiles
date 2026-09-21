# PRD — TikTok Ingest Pipeline

Status: **Draft, approved for implementation planning; hybrid ingestion revision for discussion.** Not implemented. This update authorizes documentation only, not implementation or SDD.
Owner: Bruno. Last updated: 2026-09-16.

**Design in brief:** use the browser only to inventory/update a collection and handle human authentication. Use cached HTTP metadata and a capability-probed media/subtitle extractor for individual items; analyse downloaded media locally, then send only evidence markdown to the reasoning API. Mandatory visual analysis remains unchanged; additional visual enrichment is optional, not a replacement for that requirement.

## 1. Problem

Technology discovered through TikTok currently has no repeatable path from "saved video" to "verified decision". The manual process used to produce this PRD exposed four concrete failure points:

1. **Captions lie by omission.** A caption and its spoken audio diverge. In one case the repository name existed *only* in the audio; in another, an auto-generated summary turned "90%" into "190%".
2. **Third-party transcription does not scale.** TranscriptMagic's free tier is 3 credits/month against collections of 28+ videos.
3. **Unfiltered volume wastes effort.** Most saved videos are link directories, consumer apps or engagement bait, not adoptable technology.
4. **Claims go unverified.** Without checking a primary source, marketing language ("cut tokens 90%") is indistinguishable from a measured result.

The cost of the gap is not tokens. It is adopting something because a video was persuasive, or ignoring something useful because it was never examined.

## 2. Goals

- Ingest every video in a selected TikTok collection, on demand.
- Extract both **visual** and **spoken** content, locally, without third-party transcription services.
- Classify every video against an evolving taxonomy.
- Verify every technology claim against its primary source.
- Emit entries into a local backlog the operator accepts or rejects before anything reaches an external system.

## 3. Non-goals (v1)

- YouTube and Instagram ingestion (planned phase 2; the source adapter boundary in §5.1 exists to make this cheap, but no YouTube/Instagram code ships in v1).
- Reading or analysing video comments.
- Writing automatically to Notion or to Gertru's second brain.
- Detecting videos removed from a collection after ingestion.
- Unattended/scheduled execution.

## 4. Verified environment

The following environment observations are retained from the original 2026-09-14 investigation; they were not re-measured for this documentation update. Proposed integrations and resource budgets below are not runtime-proven.

| Component | State |
|---|---|
| GPU | NVIDIA RTX 4090, 24564 MiB |
| OS | Ubuntu 24.04.3 LTS on WSL2 (kernel 6.6.87.2-microsoft-standard-WSL2) |
| Container runtime | `podman` present; **`docker` absent** |
| GPU passthrough | `nvidia-container-toolkit` + `nvidia-ctk` present |
| `ollama` | Installed on host, **zero models pulled** |
| `ffmpeg` | Absent on host; present inside existing whisper images |
| `yt-dlp` | Absent everywhere |
| `uv` | Absent |
| Host ports in use | 4000, 8000, 8081, 8082, 8765, 8766, 8767, 11434 |

### 4.1 Reusable infrastructure

`~/Code/personal/voice-assistant/whisper-service` is the STT engine this pipeline reuses. It is a standalone network service with no coupling to its parent application.

- faster-whisper `large-v3`, CUDA 12.2.2 / cuDNN 8, `DEVICE=cuda`, `COMPUTE_TYPE=float16`
- `max_size=None` — no 1 MiB WebSocket frame cap (the `llm-hub` build has this cap and would reject audio longer than roughly 32 s)
- `LANGUAGE` and `VAD_FILTER` are environment-configurable (the `llm-hub` build hardcodes them)

**It is chosen over `llm-hub`'s whisper service, which runs CPU int8 and carries the frame cap.** Run exactly one GPU whisper, never both.

### 4.2 Known blocker in adjacent infrastructure

`llm-hub` **cannot start in its current state**. `LLAMA_ARG_MODEL` points at `Qwen3-30B-A3B-Instruct-2507-IQ4_XS.gguf`, but `models/` contains only `nomic-embed-text-v1.5.Q8_0.gguf`. Because `litellm` declares `depends_on: service_healthy` against `llama-chat`, LiteLLM does not start either.

**Consequence for this PRD:** the pipeline takes **no functional dependency on `llm-hub`**. It requires only that the external podman network `llm-hub-network` exists, because the reused whisper service attaches to it. Restoring `llm-hub` is out of scope.

## 5. Architecture

Two boundaries, each justified by a constraint rather than preference.

**Host** — Playwright with a persistent Chromium profile, and pipeline orchestration.
The operator must solve TikTok captchas personally in a visible browser using an explicitly authorized profile/session. Keep that profile on the host, never mount or export it to the extractor container. The prior investigation reported working headed Playwright; no session was accessed for this revision. Close the browser after inventory capture; do not keep a tab or perform browser analysis for every video.

**Container (podman + CDI GPU)** — media fetch, vision inference, speech-to-text.
This is where containerisation pays for itself: CUDA and Python dependency isolation.

### 5.1 Stage flow

Stages run **sequentially, with at most one heavy model resident at a time.** This is the core design constraint (see §7).

```
[0] discover    host       browser inventory/update → stable IDs + collection evidence
[1] fetch       host/HTTP  cached oEmbed metadata; container extractor capability probe
                           → available timed subtitles + one cached media download
[2] vision      container  scene-detected frames → local VLM  → video.md
                           (VLM unloaded before stage 3 begins)
[3] audio       container  usable timed subtitles, else local audio → whisper WS → audio.md
[4] synthesis   host/API   reasoning model reads ONLY video.md + audio.md
                           → classification, claims, actionables
[5] verify      host/API   each technology claim → primary source → verdict
[6] emit        host       append entry to backlog.jsonl
```

**Stage 4 never receives video, audio or frames** — only `video.md` and `audio.md`, including a provenance-labelled metadata section. The expensive reasoning model reads compact text; the heavy local models do the bulk work. This is deliberate, and it is the same principle the Spotify `shunt` case demonstrated. Discovery and metadata adapters remain distinct from media/transcript extraction so future source adapters do not change these evidence boundaries.

#### 5.1.1 What “API” means here

| Interface | Useful capability | Boundary |
|---|---|---|
| TikTok oEmbed [S1] | HTTP GET `https://www.tiktok.com/oembed?url=<video_url>` returns title, author, thumbnail and embed markup | Metadata only: not a transcript, media-download API or collection inventory. Do not render the embed to analyse each item. |
| TikTok Data Portability [S2–S3] | Full Archive → Likes and Favourites → **Favourite Videos** documents `Date` and `Video landing page link` | Not an Activity-only guarantee; collection membership, saved-video media downloads and transcripts are not promised. Approved application/Login Kit, user consent and EEA/UK user eligibility are prerequisites. Optional future import, not an MVP dependency. |
| TikTok Display API [S4] | Authorized user's public posts | Not the operator's saved third-party videos; not a collection-discovery substitute. |
| Proposed yt-dlp adapter [S5] | Inspect available media formats and subtitle tracks, then fetch accessible assets | An unofficial extractor, not an official TikTok API. Upstream support is not proof that any particular URL works on this machine. |

An operator-supplied export or URL list can seed inventory, but must retain its origin and cannot establish collection membership without evidence. No app registration or new third-party transcript service is required for the MVP.

### 5.2 Model assignments

| Stage | Model | Location | Rationale |
|---|---|---|---|
| Vision | Qwen2.5-VL 7B via ollama (~6 GB) | Local GPU | Fits comfortably when whisper is not resident; no quality downgrade |
| STT | faster-whisper large-v3, float16 | Local GPU, reused container | Already built and GPU-configured |
| Synthesis + verification | GLM-5.3-flash by default; Opus 5 on explicit request | API | Reads only markdown; cost is negligible |

Model identifiers are configuration, not architecture. Changing the VLM must not require changing stage boundaries.

## 6. Stage detail

### 6.1 Discovery (host)

Playwright with the explicitly authorized persistent profile enumerates the target collection and captures additions on subsequent on-demand updates. Private collections require the operator's logged-in session — this is why the flow is operator-triggered. Use one collection tab, bounded scrolling, and collection-scoped item links rather than recommendation links; no browser-per-video loop. Persist the inventory before closing the browser. Do not infer completeness merely because previously seen IDs appear; stop on verified end-of-list or a budget limit and report incomplete enumeration.

Output per video: `id`, canonical `url`, nullable `author`, `caption`, `is_photo_post`, observed `collection`, `discovered_at`, and discovery provenance. Deduplicate by `(source, stable video ID)`, retaining all observed collection associations. Record declared count when visible, unique observed count, and completeness separately; never invent a count.

**Captions are captured but never treated as authoritative content.** They are metadata; §1 establishes why.

### 6.2 Metadata (host/HTTP) and fetch (container)

1. Check blocklist and completed-stage cache before remote per-item work. Fetch oEmbed via HTTP only on a metadata cache miss/expiry; cache payload, canonical URL, fetch time and outcome. Missing metadata does not prevent processing accessible media.
2. A pinned, capability-probed yt-dlp adapter inspects actual formats and subtitle tracks without downloading video first [S5]. Subtitle extraction must be enabled explicitly; an empty result is not proof the platform has no subtitles. Record extractor version, language/format, probe outcome and access failures. Probe only an approved extraction path: see the challenge-handling gate below.
3. Download one suitable accessible media asset, retaining its hash. Reuse that local asset for both frame extraction and speech recognition; valid subtitles avoid STT but do not remove the mandatory visual pass. Check that the chosen format has the video's audio, not merely an associated music track. No second media download for audio.
4. Use local FFmpeg [S6] for frames and, only when STT is needed, audio normalization to the existing service's required format (§6.4). Missing streams, unsupported codecs and photo posts are explicit capabilities/outcomes, not successful empty videos.

**Extractor safety gate:** upstream TikTok code currently includes app-API paths and automatic JS challenge solving [S5]. Therefore installing stock yt-dlp is not sufficient to prove policy compliance. Before execution, verify the pinned adapter can stop before challenge-solving or unauthorized endpoint/session paths. If that cannot be guaranteed, disable remote extraction and accept an operator-provided local file. Do not build private-endpoint reverse engineering, signature emulation or captcha bypasses, and do not use ambient browser cookies or cookie export. Access barriers pause for human action or yield a blocked result; they do not authorize transferring browser credentials to the downloader.

Frame strategy: **FFmpeg scene detection plus interval samples**, so low-motion screencasts still yield frames. Preserve original timestamps and deduplicate near-identical frames. Sending the whole video to a VLM is not in scope.

### 6.3 Vision (container)

The VLM receives the extracted frames and produces `video.md`: what is shown on screen, step by step — UI, terminal commands, code, product names visible in the recording.

This stage is required for **every** video, subject to explicit unavailable/failed outcomes, preserving the original requirement. Rationale: the most valuable detail in a technical TikTok is frequently on screen and absent from both caption and speech. “Possible video processing” does not silently change this to transcript-only ingestion.

Use the local VLM for visual description and OCR-style reading of programming URLs, tool/repository names, terminal commands and code. A separate OCR engine is an optional future enrichment, not a new mandatory dependency. `video.md` records frame timestamp/path, observed text, uncertainty and any crop used; burned-in subtitles are visual evidence, not an extracted native subtitle track. Never complete blurry repository URLs or missing code from model knowledge. Record `[illegible]` and verify only identifiable candidates against primary sources. Do not execute extracted commands or follow frame instructions as agent instructions.

Optional enrichment means a bounded crop or closer sample around uncertain text, not skipping the baseline pass or uploading the full video to an API. Photo posts require actual slide assets with slide-index provenance; a cover thumbnail alone does not establish complete visual coverage. Unsupported slide retrieval produces a partial result or accepts manually supplied slides.

### 6.4 Audio (container)

Layered, cheapest first:

1. **Actual timed subtitle tracks** when the approved extractor can retrieve and parse them: prefer usable SRT/WebVTT, preserve cue times, language and source URL locally, and label creator/automatic origin only when known. The post description and oEmbed title are not speech transcripts. Upstream TikTok extraction includes subtitle parsing paths [S5], not guaranteed availability for every video.
2. **Bounded quality gate:** reject empty/malformed cues, invalid/out-of-duration timing or a clearly mismatched language. Flag suspicious repetition, sparse coverage and garbled technical terms; gaps alone do not prove missing speech. Use at most one local STT fallback pass when the track is absent, unusable or suspect. Preserve the original and disagreement rather than silently replacing evidence. Exact coverage/quality thresholds require the proof of concept.
3. **Whisper fallback** uses the reused local service and audio from the same media file, after vision has unloaded. Format normalization targets 16 kHz mono signed 16-bit PCM WAV as recorded below, not an invented new endpoint or wire protocol. Verify framing, chunk boundaries, completion and normalization against the actual service before implementation. Whisper can hallucinate, especially on poor audio; no-speech/VAD findings and uncertainty must remain visible [S7].

Whisper contract (WebSocket, **not** HTTP):

```
ws://127.0.0.1:8767/ws/transcribe        # host-published port
ws://voice-assistant-whisper:8765/...    # from inside llm-hub-network

→ (optional) text frame {"type":"initial_prompt","text":"..."}
← {"type":"ack",...}
→ binary WAV frames (16 kHz mono s16le)
← {"type":"partial","text":"<chunk>","accumulated":"<session total>"}

health: GET http://127.0.0.1:8766/health
```

One connection per job — the recorded service contract keeps accumulated text per connection, so do not multiplex jobs. `LANGUAGE` defaults to `es` and is environment-configurable in that deployment; this does **not** establish a per-message language control. Select language explicitly for each job through a verified supported mechanism (potentially service configuration/restart between language groups). Per-job selection remains a proof-of-concept gate, not a new WebSocket field.

Output: `audio.md`, with extraction method, language, quality flags and timestamps when provided. The recorded `partial` response exposes text but no segment timestamps: preserve known input chunk intervals as coarse provenance, or mark timing unavailable. Do not fabricate word-level alignment or assume upstream Whisper features exist in the reused WS service. Subtitle cues retain their native timing. A missing stream, blocked download or failed service is not “no speech”; the latter requires actual analysed audio evidence.

### 6.5 Synthesis (API)

Input: `video.md` + `audio.md`, with caption/oEmbed metadata embedded as labelled text. Nothing else. Raw media and frames stay local; any future third-party media upload requires separate explicit consent.

Output:
- **Classification** against the taxonomy (§6.7)
- **Extracted claims** — every factual assertion, marked with its source: `caption`, `audio`, or `visual`
- **Named entities** — tools, repositories, models, products
- **Proposed actionables**

Where audio, visual evidence and caption disagree, all are recorded. Disagreement is signal, not noise. Claims reference the evidence artifact and cue/frame/chunk interval when available; uncertain recognition never becomes a confirmed entity through synthesis alone.

### 6.6 Verification (API + web)

Every video classified as `tecnología` has its claims checked against a primary source — repository, documentation, vendor pricing page.

Each claim receives a verdict:

| Verdict | Meaning |
|---|---|
| `confirmed` | Primary source supports the claim |
| `overstated` | Real, but weaker than presented |
| `unverifiable` | Named entity could not be located |
| `contradicted` | Primary source disagrees |
| `unnamed` | The video never identifies the technology |

`unnamed` is a first-class outcome, not an error. A video that withholds the tool's name to farm engagement produces an entry stating exactly that, and no adoption recommendation.

Verification also records **fit with the operator's actual stack** — a technically excellent library with no current use case is a low-priority entry, not a recommendation.

### 6.7 Taxonomy — fixed roots, dynamic subgroups

Five roots are fixed and act as the anchor:

- **`tecnología`** — adoptable: library/SDK, CLI/agent tooling, model/AI service, SaaS product
- **`recurso`** — directories, catalogues, asset banks
- **`educativo`** — concepts and techniques, learning platforms
- **`consumo`** — personal apps with no stack relevance
- **`ruido`** — motivational, trend, engagement bait with no identifiable technology

**Subgroups are generated per video from its content**, then normalised against an accumulating `taxonomy.json`. Normalisation is mandatory: without it `cli-agentes`, `agentes-cli` and `agent-tools` become three labels for one concept, and the taxonomy stops being useful at exactly the point it grows.

### 6.8 Emission (host)

Appends to `backlog.jsonl`. **The pipeline never writes to Notion or Gertru.** Gertru is not CLI-invocable; handoff is manual and operator-controlled.

## 7. VRAM budget

24 GB total, and the constraint is real: evidence of a prior OOM exists in the adjacent project (`llm-hub`'s whisper was moved to CPU "para liberar VRAM para Qwen", its GGUF is missing, and an empty file named `oom` sits at that repo root).

Therefore:

- **Stage 2 and stage 3 never hold their models simultaneously.** The VLM is unloaded before whisper loads.
- The pipeline **does not** assume `llm-hub`'s 30B chat model is resident; if the operator restores it later, re-check free VRAM and pause or unload competing workloads before proceeding.
- Stage 4 uses an API model, consuming zero VRAM.

Sequential staging is what makes "don't downgrade the model" achievable on a single GPU.

### 7.1 Proposed bounded defaults (to validate, not measured guarantees)

| Resource | Initial policy |
|---|---|
| Browser | One tab; close after inventory; stop after 5 minutes or 3 scroll cycles with no new IDs, reporting completeness as unknown unless end/count is verified |
| HTTP | At most 2 metadata requests concurrently; one media job at a time; maximum 2 retries for transient failures with backoff and `Retry-After`; stop on authentication/challenge failures |
| Cache | Metadata TTL 24 hours; failed probes retry only after backoff/explicit retry; stable ID plus media hash and stage/tool/model configuration identify reusable outputs |
| Media | Maximum 10 minutes or 250 MiB per item, 5-minute download deadline; limit exceeded becomes `budget_exceeded`, never silent truncation |
| Visual input | Scene threshold starting at 0.3 plus a sample every 5 seconds; at most 32 baseline frames and 8 enrichment crops/samples, long edge at most 1280 px; record uncovered intervals when capped |
| Local compute/storage | One GPU model at a time, 10-minute deadline per inference stage, 10 GiB media cache cap; evict only unreferenced completed-job media, never backlog/evidence or active input |

Measure browser lifetime/RSS, request counts, bytes, frame count, stage latency and peak VRAM in the proof of concept. Check other resident GPU workloads before starting; explicitly unload the VLM before starting GPU Whisper and release Whisper before the next vision job. If the independently managed service cannot release VRAM safely, pause for the operator rather than claiming sequential invocation alone guarantees memory release.

## 8. Data contracts

All state is machine-local under `~/.local/state/tiktok-ingest/` and is never committed.

```
~/.local/state/tiktok-ingest/
├── backlog.jsonl          # operator-edited queue
├── blocklist.json         # permanently rejected video IDs
├── taxonomy.json          # accumulated normalised subgroups
├── processed.json         # stable ID → hash, stage/config versions and completion
├── inventory.json         # collection observations, unique IDs and completeness
├── cache/                 # bounded metadata, subtitle tracks and shared media by ID/hash
└── runs/<run-id>/<video-id>/
    ├── video.md
    ├── audio.md
    └── meta.json
```

`meta.json` records discovery provenance, extraction/tool versions, media hash, subtitle provenance, selected frame times, quality/coverage flags, per-stage status/reason and retry history. Strip credentials and signed access tokens from durable reports/API text; cache sensitive retrieval references only locally as necessary. Do not store browser sessions in this state tree or commit any runtime data.

Keep extraction state separate from operator backlog `status`: stages use `complete`, `partial`, `blocked`, `unsupported`, `failed`, `budget_exceeded` or `not_applicable`, with a reason. Partial evidence may produce a clearly labelled backlog entry with `classification: null` and a reason when insufficient; unknown classification is not a sixth taxonomy root. It must not be cached as fully processed. Resume failed stages using valid cached inputs. `--force` invalidates selected derived outputs but never overrides the permanent blocklist or authorizes a new session.

### 8.1 `backlog.jsonl`

One JSON object per line. The operator edits `status` by hand.

```json
{
  "id": "7683273567433248022",
  "url": "https://www.tiktok.com/@mralekai/video/7683273567433248022",
  "author": "mralekai",
  "ingested_at": "2026-09-14T10:00:00Z",
  "status": "pending",
  "classification": {
    "root": "educativo",
    "subgroup": "concepto-tecnica",
    "confidence": 0.86
  },
  "entities": ["Claude Code", "Gemini 2.5 Flash", "shunt"],
  "claims": [
    {
      "claim": "90% token reduction",
      "source": "audio",
      "verdict": "overstated",
      "evidence": "https://portal.spotify.com/blog/...",
      "note": "Applies to bulk-read context, not total billed cost"
    }
  ],
  "fit": "adjacent",
  "actionable": "Evaluate opt-in bulk reader for large unindexed material",
  "artifacts": "runs/2026-09-14T10-00Z/7683273567433248022/"
}
```

`status`: `pending` → `accepted` | `rejected`.
On `rejected`, the ID is appended to `blocklist.json` and is never ingested again.

## 9. Failure policy

The operator is present by design, so failures surface rather than degrade silently.

| Failure | Policy |
|---|---|
| Captcha / session expired | **Pause and surface.** Operator resolves in the visible browser; resume with `--retry`. Never bypass. |
| Video unavailable / access denied | Record observed response and `blocked`/`unsupported` reason, continue the batch; assert deletion or region blocking only with supporting evidence |
| No speech (music-only) | `audio.md` records "no speech detected" — an empty transcript is a finding, not an error |
| Subtitle tracks absent, inaccessible or unusable | Fall back to local Whisper only if audio is accessible; preserve probe outcome and quality reason |
| Media download blocked / unsupported / budget exceeded | Retain available metadata/subtitles as partial evidence; offer operator-supplied local media associated with the stable ID, never bypass restrictions |
| Collection says “This collection isn’t available” plus “Log in” | Report inaccessible in the observed context; do not infer deletion, privacy state, membership or current count |
| Photo post / missing stream / illegible frames | Record missing modality or coverage explicitly; accept local slides/media, never claim a thumbnail is full visual analysis |
| Whisper service unreachable | Fail the stage explicitly; **never** silently fall back to caption-only |
| VLM OOM | Fail the run with the VRAM state reported; do not retry blindly |
| Already processed | Skip unless `--force` |

**Observed access friction:** the original research reported two captchas, including one on first page load. That does not establish TikTok's detection rules or predict frequency. The latest supplied public collection observation was “This collection isn’t available” plus “Log in”; no new authenticated collection check was performed for this revision. Reducing browser navigation is a resource decision, not a claim to avoid detection.

## 10. Proposed dependencies (implementation only)

Container image (new, built by this subsystem):

- `yt-dlp` — reported absent in the original environment snapshot; pinned adapter requires the §6.2 policy gate before use
- `ffmpeg` — present in existing whisper images, required here too

Host:

- ollama model pull for the chosen VLM (ollama itself is installed; no models exist)

Reused, not rebuilt:

- `voice-assistant/whisper-service` — started independently by the operator
- podman network `llm-hub-network` must exist

Per dotfiles doctrine, third-party binaries are **not committed**: the installer pins version and URL and downloads to `~/.local/bin` or into the built image.

## 11. Repository layout

```
~/.dotfiles/ai/tiktok-ingest/
├── PRD.md              # this document
├── README.md           # operational guide (written at implementation time)
├── Containerfile       # yt-dlp + ffmpeg + client deps
├── podman-compose.yml  # joins llm-hub-network, CDI GPU, ports outside §4 set
├── install.sh          # idempotent: image build, model pull, state dirs
└── src/                # orchestrator, stage implementations, contracts
```

Invocation: an OpenCode skill (`/tiktok-ingest`) wrapping the script engine, per decision 16 of the grilling ledger.

No symlink into `~/.config` is required for the PRD itself. The skill wrapper, when built, follows the standard mapping: repo home `ai/agents/opencode/skills/tiktok-ingest/` → `ln -sfn` → `~/.config/opencode/skills/tiktok-ingest`.

## 12. Open risks

1. **Container ports** must avoid 4000, 8000, 8081, 8082, 8765, 8766, 8767, 11434. Container name must avoid `whisper`, `whisper-service`, `litellm`, `llm-hub-*`, `voice-brain`.
2. **`llm-hub` remains broken.** Not a dependency, but if the operator restores the 30B model, the VRAM budget in §7 must be re-checked before running vision stages concurrently with anything else.
3. **TikTok DOM volatility.** Discovery depends on page structure; selectors will break. Treat discovery as the highest-maintenance component.
4. **Collection enumeration accuracy.** The original research reported 22 reliably extracted links against a declared 28, with recommendation contamination. These are historical observations, not current collection counts. Enumeration must validate a currently visible declared count when available and otherwise report unknown completeness.
5. **VLM quality on dense screencasts.** Unvalidated. First run should be assessed against videos whose on-screen content is already known.
6. **Extractor policy and compatibility.** Subtitle/media code paths exist upstream, but the pinned version must prove no automatic challenge solving, unauthorized private-endpoint path or ambient cookie access. If not enforceable, remote extraction stays disabled; manual files remain usable.
7. **Whisper lifecycle and contract.** Verify actual WAV framing/completion, long-audio behavior, language selection, available timing and safe GPU release without changing the shared service or inventing protocol fields.

### 12.1 Unresolved proof-of-concept checks

No extraction, model inference or integration test was run for this PRD revision. A separately authorized proof of concept must test: a video with retrievable timed subtitles; one without tracks; malformed/mismatched subtitles; silence/music; mixed-language technical speech; a dense low-motion screencast; a photo post; and a blocked/unsupported URL with manual-file recovery. Compare recognized names/URLs/code with human-labelled evidence, and tune the proposed budgets/quality gates without weakening mandatory visual coverage reporting. Verify cache reuse, request limits, frame timestamps, model unloading and failure resumption empirically. Inspect the actual WS implementation before relying on its historical contract sketch.

## 13. Success criteria

The pipeline is working when, for an authorized representative collection (historical scale: ~28 videos):

- Every discovered video is classified when evidence permits, or explicitly reported partial/unknown; subgroups are normalised without synonym duplicates, and incomplete collection enumeration is visible.
- Every identifiable `tecnología` claim carries a verdict and primary-source evidence when available. `unverifiable` records the failed search; `unnamed` cites the extraction evidence and carries no invented primary-source URL or adoption recommendation.
- No entry reaches the backlog without both `video.md` and `audio.md`, or an explicit recorded reason for a missing one.
- Rejected IDs are never re-ingested on a subsequent run.
- Normal supported-path interventions are limited to authorization/captchas and setting `status`; blocked/unsupported items may additionally require manual input or explicit retry, never silent bypass.
- One collection browser tab suffices for inventory/update, with no per-video browser analysis. Cached HTTP metadata and unchanged media/stage results are reused; one downloaded media asset serves both modalities.
- Valid timed subtitles skip STT, but never skip required vision. Unusable tracks invoke at most one STT fallback; each output preserves method, uncertainty and real timestamps or explicit timing limits.
- Visual-only URLs/tool names/code retain frame provenance; blurry text is not completed or promoted to a verified entity. Capped sampling and missing modalities remain visible in output.
- Observed request/retry/frame/storage limits match §7.1, VLM and Whisper never overlap in GPU residency, and synthesis receives only the two markdown artifacts.
- Blocked/unsupported cases preserve partial progress and accept local media without accessing cookies, bypassing challenges, uploading media to third parties or depending on `llm-hub` services.

## 14. Evidence and scope of verification

Official/upstream documentation consulted on 2026-09-16. These sources establish documented capabilities, not runtime success on the target collection. Context7 was used first for yt-dlp, FFmpeg and Whisper; direct upstream source inspection resolved TikTok-specific details. The Data Types page itself overrides a search summary that incorrectly omitted Favourite Videos.

| Ref | Source | Supports |
|---|---|---|
| S1 | [TikTok Embed Videos](https://developers.tiktok.com/docs/en/embed-videos) | oEmbed metadata and embed response, not transcript/media retrieval |
| S2 | [TikTok Data Types](https://developers.tiktok.com/docs/en/data-portability-data-types), updated August 28, 2026 | Full Archive / Likes and Favourites / Favourite Videos: Date + landing-page link; no promised saved collection mapping/transcripts |
| S3 | [Data Portability Get Started](https://developers.tiktok.com/doc/data-portability-api-get-started) and [Data Portability API](https://developers.tiktok.com/products/data-portability-api/) | Application/Login Kit approval, consent and regional eligibility; not MVP infrastructure |
| S4 | [Display API overview](https://developers.tiktok.com/doc/display-api-overview/) | Authorized user's public video display, not saved third-party inventory |
| S5 | [yt-dlp README](https://github.com/yt-dlp/yt-dlp#readme), [supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), [TikTok extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py) | Metadata probing, subtitle/media parsing, compatibility caveat and challenge/app-API safety gate; moving upstream source, not an approved pinned version |
| S6 | [FFmpeg documentation](https://ffmpeg.org/ffmpeg-all.html) | Scene/interval frame selection, timestamps and audio resampling; not evidence of local WS compatibility |
| S7 | [Whisper model card](https://github.com/openai/whisper/blob/main/model-card.md) and [Whisper transcription source](https://github.com/openai/whisper/blob/main/whisper/transcribe.py) | ASR capabilities and hallucination/language limitations; does not define the reused faster-whisper WebSocket contract |

## Appendix — decision ledger

Decisions confirmed with the operator before this PRD was written.

| # | Decision |
|---|---|
| 1 | On-demand trigger; operator resolves captchas. No cron. |
| 2 | Input: private TikTok collections from the operator's account. TikTok only in v1. |
| 3 | Layered transcription: native subtitles first, local STT fallback. |
| 4 | Content inference stays local on GPU; external model APIs only for synthesis and verification. HTTP source metadata/media retrieval is not remote inference. |
| 5 | All videos processed and classified automatically, without prompting. |
| 6 | All `tecnología` classifications verified against primary sources. |
| 7 | Gertru handoff is manual; not CLI-invocable. |
| 8 | Output to hand-edited `backlog.jsonl`; rejection by ID is permanent. |
| 9 | Idempotent state in `~/.local/state/tiktok-ingest/`; `--force` to reprocess. |
| 10 | Fixed taxonomy roots, dynamic subgroups normalised in `taxonomy.json`. |
| 11 | Split architecture: host browser + orchestration, container GPU work. |
| 12 | Reuse `voice-assistant/whisper-service` over WebSocket; build nothing new. |
| 13 | Sequential stages: vision → `video.md`, audio → `audio.md`, then reasoning over markdown only. |
| 14 | No functional dependency on `llm-hub`; only its network must exist. |
| 15 | Add `yt-dlp`; `ffmpeg` already available in images. |
| 16 | Skill wrapper over a script engine. |
| 17 | Non-goals as listed in §3. |
| 18 | Mandatory local visual analysis for every video, with explicit failure/missing-evidence reporting (retained from original §6.3). |
| A | VLM: Qwen2.5-VL 7B via ollama. |
| B | Synthesis: GLM-5.3-flash default, Opus 5 on request. |
| C | Frames: ffmpeg scene detection with interval fallback. |

The hybrid browser/HTTP split, safety gates and bounded defaults in this revision are proposed refinements, not newly confirmed implementation decisions. The existing sequential local vision → audio → markdown-only API flow and local operator-controlled backlog remain requirements.
