# Gertru: from daily dictation to a unified control hub

**Status: living discovery draft, not an approved PRD or implementation plan.**
This roadmap connects the original control-hub vision with a lightweight Windows
voice client and meeting workflows. It records confirmed requirements separately
from proposed delivery order, optional candidates, and unresolved decisions.
It is passive documentation: no implementation, deployment, transfer, or action
authority is granted by this document. No schedule or performance budget is agreed.

## Quick path

**One Gertru brain, one management truth in `hub/`, multiple clients and hands.**

1. Compare reuse against replacement without losing working daily dictation.
2. Add a lightweight Windows orb, then continuous F8 conversation and voice threads.
3. Process completed meetings and keep readable text history in the Windows app.
4. Connect a hub-derived **Today / Needs decisions / Changes** cockpit.
5. Introduce controlled existing-app actions; consider live meetings and video later.

This is the **PROPOSED minimum-to-more sequence**, not approval of every feature.
The cockpit can advance alongside voice once its canonical data contract is ready;
meeting processing is not a prerequisite for a useful control hub.

## How to read this draft

| Label | Meaning |
|---|---|
| **CONFIRMED** | Explicit direction from the conversation; not proof of delivery. |
| **OBSERVED / REPORTED** | Existing evidence, with its verification limits stated. |
| **PROPOSED** | Recommendation awaiting selection and scoped approval. |
| **OPTIONAL / DEFERRED** | Candidate or later scope, not a committed deliverable. |
| **OPEN** | A decision or contract still needed before dependent work. |

## Confirmed requirements and boundaries

| Area | Confirmed direction | Still open / qualification |
|---|---|---|
| Brain and truth | Gertru remains the central brain; `hub/` is the single management truth. Local apps are clients/hands. | Integration contracts, not a second local brain. |
| Desktop split | Windows frontend owns OS-native mic, playback, clipboard, global shortcuts, and capture; Linux/WSL backend. | Frontend technology and process boundary. No WSL graphical frontend. |
| Presentation | Lightweight floating orb; real operational state rather than decorative animation. | Exact states and accessibility behavior. No avatar, 3D, or AIRI. |
| Resources | Low idle UI/host RAM remains important; GPU-resident faster-whisper and suitable TTS are acceptable for readiness. | No measured/agreed RAM or latency thresholds; GPU contention policy open. |
| Dictation | Preserve useful speech-to-clipboard/paste behavior. | Reuse versus replacement of the existing project. |
| Conversation | F8 toggles continuous hands-free conversation until pressed again, including interruptions. No hold-to-talk per turn. | Turn detection, echo handling, cancellation, shortcut conflict behavior. |
| Threads | Resume the active persistent Windows voice thread; create/switch threads like OpenCode. | Exact session/auth API; separate from Telegram threads, same Gertru and hub. |
| Speech output | Predefined natural Spanish TTS initially. | Model/voice choice and delivery mechanism; no initial cloning/custom voice. |
| Meetings | Capture participants and own mic; initially process after the meeting ends. | Windows capture route, speaker attribution, failure recovery. |
| Meeting output | Transcript, summary, agreements, and task drafts visible in the Windows app first. | No automatic hub sync or external task writes. |
| Retention | Text history survives media deletion. Audio/video use a temporary PC folder because of size; manual cleanup initially. | Physical storage, schema, backup, and cleanup UX. No automatic deletion agreed. |
| Notes | Capture ideas and notes is part of the desired scope. | Automatic versus explicit save semantics. |
| Filtering | All secret-filter implementation is explicitly deferred. | Existing operational approvals, secrets handling, and lawful recording obligations remain. |

**Reported hardware:** 32 GB RAM at 6000 MHz and RTX 4090 with 24 GB VRAM
(the earlier 5090 mention was corrected). VRAM is usually free according to the user.
This is not a benchmark: GPU residency does not eliminate host RAM consumption.

Temporary PC media storage is a size choice, **not** a local-only egress rule or a
prohibition on sending media to Gertru. Do not invent per-media approval/filter
requirements. Discovery itself authorizes no actual transfer or remote operation.

## Existing baseline and architecture context

Read [Control Hub Architecture](control-hub-architecture.md) for the existing
single-writer/clerk, capture inbox, Obsidian mirror, and agent-memory boundaries.
Its production statements and historical follow-ups are context, not freshly
verified deployment evidence for this roadmap. This document does not amend it.

- **Reported daily use:** the existing voice-assistant works for dictation through
  faster-whisper to clipboard paste; the desired product is substantially broader.
- **Prior source inspection:** Windows Electron/React → `localhost:8000/transcribe`
  → Python → separate faster-whisper WebSocket service → sidecar intent routing
  → pyperclip and Ctrl+V. Actual deployment and the active path remain unverified.
- **Locally observed state:** the projects sweep now feeds `hub/telemetria/repos.md`
  in canonical `hub/` (2026-09-15 amendment), the local dashboard at :47624 renders
  with hub-precedence, and any other view — including
  [projects-html.py](../scripts/projects-html.py) over
  [projects.yaml](../scripts/projects.yaml) — is a projection of the hub. Existing
  telemetry, cards, and session links remain reuse candidates; the Phase 4
  Windows-client cockpit surface itself is still unbuilt.
- Preserve historical documentation and unrelated dirty changes. No decision has
  been made to rewrite, delete, or replace the voice-assistant repository.

## Target topology: responsibilities, not a selected stack

| Layer | Responsibility | Boundary |
|---|---|---|
| Windows client | Orb, mic/playback, F8, clipboard, participant capture, thread/history views. | Client and OS hands only; framework unselected. |
| Linux/WSL backend | Speech processing and client orchestration toward existing Gertru. | Exact service split/transport open; not a new reasoning authority. |
| Existing Gertru | Conversation, reasoning, and authorized tool coordination. | Windows and Telegram have distinct threads, not distinct brains. |
| Canonical `hub/` | Project management state through existing validated clerk/inbox contracts. | UI projections must not become parallel management writers. |
| Windows-visible history | Persistent conversations and meeting text; references to temporary PC media. | Storage location/schema unresolved; no automatic meeting-to-hub sync. |
| Existing apps | Existing systems of record and task/action surfaces. | Integrations remain selected, scoped capabilities, not blanket access. |

The orb and cockpit are complementary: the orb exposes actual voice/service state;
the cockpit explains work, decisions, and changes. Neither replaces Gertru, Obsidian,
Engram's operational-memory role, or existing corporate boundaries.

## Roadmap: proposed phases and exit evidence

All phases below are **PROPOSED sequencing**. Exit evidence describes what a future
implementation must demonstrate, not tests already passed. Set measurable acceptance
thresholds with the user after baseline measurement; all numeric thresholds are TBD.

### Phase 0 — Establish the baseline and choose reuse deliberately

**Value:** avoid a costly rewrite that merely recreates today's working dictation.

- Verify the currently running dictation path and enumerate reusable client/backend
  pieces, including existing session, tool, telemetry, and hub presentation surfaces.
- Compare keeping/adapting the existing app with a replacement Windows shell using
  the same representative dictation and voice-readiness scenarios.
- Record idle/active host RAM by process, VRAM, cold/warm start time, transcription
  latency and accuracy, packaging complexity, and recovery behavior.
- **Dependencies:** local inspection scope; separate authorization for any later
  remote inspection. Protect the existing voice-assistant's unrelated dirty changes.
- **Exit evidence:** reproducible baseline measurements and a documented reuse/replace
  recommendation with tradeoffs; explicit selection before migration. No assumed savings.

### Phase 1 — Preserve dictation in a lightweight Windows shell

**Value:** a useful daily tool before broader conversation or management features.

- Keep microphone → transcription → clipboard/paste; introduce the minimal orb and
  clear capture/processing/error feedback tied to actual events.
- Minimize idle host/UI RAM while allowing the agreed direction of warm GPU speech
  services; document clipboard focus behavior and visible service failure.
- **Dependencies:** Phase 0 stack choice, Windows device permissions, backend boundary,
  and candidate lifecycle behavior. Dictation shortcut coexistence remains to be designed.
- **Exit evidence:** repeated dictation into representative apps; cold/warm and idle
  resource measurements; device/backend failure and wrong-focus scenarios exercised;
  a safe return to the existing daily workflow demonstrated before replacement.

### Phase 2 — Add continuous F8 conversation and persistent voice threads

**Value:** speak naturally with the existing Gertru without holding a key each turn.

- F8 starts/resumes the active Windows voice thread; a second press stops continuous
  conversation. Support creating/switching threads and recovering their history.
- Add natural Spanish TTS, interruption handling, and real listening/thinking/speaking
  feedback. Treat those state names as a proposed UX vocabulary.
- Include idea/note capture after save semantics are selected; initially favor
  conversation/dictation/notes over broad action authority as a recommendation only.
- **Dependencies:** Phase 1; verified Gertru session/auth and streaming contracts;
  TTS choice, turn/echo/cancellation design, persistence and note-save decisions.
- **Exit evidence:** multi-turn F8 session with interruptions; stop silences playback
  and ends capture as specified; restart resumes the correct thread; Telegram remains
  separate; disconnect/reconnect and note-save outcomes are explicit and inspectable.

### Phase 3 — Process completed meetings and retain useful text

**Value:** turn a finished meeting into readable, recoverable knowledge in Windows.

- Capture participant audio and own mic, then process after meeting end into transcript,
  summary, agreements, and derived task drafts. Attribution must expose uncertainty.
- Show results in the app with persistent text history independent of temporary media.
  Provide manual media cleanup; do not automatically publish tasks or sync to hub.
- **Dependencies:** Phase 1 capture shell and speech pipeline; selected storage/backup
  contract, lawful recording workflow, and post-processing access to existing Gertru.
  Phase 2 can supply shared history plumbing but is not an absolute capture prerequisite.
- **Exit evidence:** a representative completed meeting includes both audio sources;
  interrupted capture/processing yields a recoverable or explicit failure; results are
  inspectable; deleting test media preserves transcript/summary/history after restart;
  draft tasks cause no external writes. Measure duration, size, and processing time.

### Phase 4 — Deliver the hub-derived control cockpit

**Value:** see what matters today, what needs a decision, and what changed without
reconstructing project state from separate applications or agent conversations.

- Project **Today / Needs decisions / Changes** from canonical `hub/`; proposed
  contents include next actions, pending triage, dated changes, and provenance links.
- Reuse useful cards, repo telemetry, and session links without retaining YAML as a
  competing management truth. Expose stale/unavailable data rather than inventing state.
- Connect orb/thread entry points to relevant context; preserve Obsidian as a valid
  dashboard and existing clerk/inbox authority for any future management changes.
- **Dependencies:** verified hub read/projection contract, freshness/provenance design,
  and reconciliation of current renderer drift. Can run alongside Phases 2–3.
- **Exit evidence:** sampled cockpit entries trace to canonical hub records; technical
  telemetry is distinguishable from management state; stale/offline behavior is visible;
  opening a session preserves context without creating another management database.

### Phase 5 — Introduce controlled actions in selected existing apps

**Value:** move from understanding and drafting to useful execution through current tools.

- Select a small first integration from real daily needs; reuse existing app capabilities
  rather than rebuilding task managers, developer tools, or communication platforms.
- Propose draft/review/execute boundaries and explicit outcome reporting per capability.
  Exact modes, action authority, and which larger integrations ship are **OPEN**.
- Meeting task publication and note-to-hub capture are candidates only after destination,
  save semantics, and write authority are chosen; maintain existing corporate boundaries.
  Reverse-path precedent: Notion→hub task materialization is already sealed via
  `set-tareas` fed from `tareas-fuentes.json` (one-way pull); hub→Notion writes remain
  out of scope.
- **Dependencies:** verified Gertru tool surface, per-integration authorization, failure
  and retry semantics, and canonical write contracts wherever hub state is affected.
- **Exit evidence:** one approved end-to-end action with observable result and audit
  context; denied/cancelled/failed/retried cases behave as specified without duplicate
  writes. No claim of unrestricted tool access or automatic publication.

### Phase 6 — Consider live meetings, screen/video, and richer assistance

**Value:** optional real-time help once completed-meeting workflows are dependable.

- **DEFERRED:** live transcription and assistance, video/screen capture, and richer
  cross-app context. Each needs separate discovery and scope selection.
- **Dependencies:** demonstrated Phase 3 reliability; capture permissions and lawful use;
  shared-GPU contention, storage growth, interruption, and live transport decisions.
- **Exit evidence:** scoped live scenarios show truthful state and recoverable failures;
  measured host RAM/VRAM/latency under concurrent workloads meets later-agreed thresholds;
  video cleanup still preserves text. These are future gates, not approval to build.

## Reuse candidates: evidence, not a stack selection

Prior source-backed research below does **not** prove runtime integration, lower RAM,
or percentage savings. Links identify upstream projects; licenses must be rechecked
against the versions selected for implementation. No new broad research is required now.

| Candidate | Potential fit | Limits / current disposition |
|---|---|---|
| Existing voice-assistant | Preserve daily dictation and reuse working speech/client pieces. | Deployment unverified; reuse versus full replacement remains open. |
| [assistant-ui](https://github.com/assistant-ui/assistant-ui) | MIT composable React chat with external state; potential hub conversation surface. | Does not itself supply a lightweight native orb or OS audio capture. |
| [LiveKit starter](https://github.com/livekit-examples/agent-starter-react) + [components](https://github.com/livekit/components-js) | MIT starter / Apache-2.0 components; voice states and transport. | Infrastructure burden; optional, not selected. |
| [Pipecat](https://github.com/pipecat-ai/pipecat) | BSD core voice-pipeline alternative. | Candidate only; integration and operational fit unproven. |
| [Handy](https://github.com/cjpais/Handy) | Local dictation reference or reusable candidate. | Not a whole-product replacement for Gertru, cockpit, and meetings. |
| WhisperWriter / Voxtype | Dictation comparison candidates from prior research. | Python/Qt dictation is partial scope; Linux-focused Voxtype mismatches Windows frontend. |
| AIRI / Open WebUI | Prior comparison context only. | AIRI excluded with avatars; Open WebUI has custom-branding license concerns and platform duplication risk. |

## Open decisions, in priority order

1. **Baseline and shell:** what is actually running; reuse or replace; frontend choice;
   packaging, process split, and measured acceptance thresholds.
2. **Gertru connection:** session/auth API, streaming/cancellation, persistent Windows
   thread identity, reconnect semantics, and access boundary.
3. **Voice lifecycle:** TTS model/voice, interruption and echo strategy, device/shortcut
   conflicts, GPU sharing and service residency under competing workloads.
4. **Data lifecycle:** text/media storage and schema, backup, retention and manual cleanup
   behavior; note auto-save versus explicit save; meeting attribution/recording workflow.
5. **Cockpit and actions:** hub projection/freshness contract, approved integration order,
   exact modes and action authority, and any later explicit publication destinations.

## Exclusions and how to resume

- No new brain, management vault, tracker, WSL GUI, avatar/3D, or initial voice cloning.
- No initial live meeting/video scope, secret-filter implementation, automatic media
  deletion, automatic meeting-to-hub sync, or automatic external task publication.
- No blanket tool authority, forced rewrite, approved framework, invented dates, or
  assumed resource advantage. This is neither a formal SDD artifact nor a closed PRD.

**Resume at Phase 0:** confirm the running baseline and reuse comparison locally,
then resolve the first blocking decision before implementation. Record new evidence
with its source and verification date, promote proposals only after explicit selection,
and revise the sequence as utility and dependencies become clearer. Remote inspection
or transfer requires separate explicit destination/operation/session authorization.
Keep this document passive; do not create a competing project-state source to track it.
