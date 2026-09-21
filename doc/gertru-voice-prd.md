# Gertru Voice Desktop — Product Requirements Document

**Status:** implementation handoff; confirmed product decisions with a proposed technical approach.
**Scope:** Windows desktop client, Linux/WSL speech backend, existing Gertru integration.
**Related roadmap:** [Gertru voice and control-hub roadmap](gertru-voice-roadmap.md).

This document consolidates the discovery conversation so implementation can resume
without repeating it. It is not an SDD artifact, a claim of completed implementation,
or authorization for deployments, credential access, recordings, or external actions.
The phase order is proposed; the product decisions below are confirmed unless marked open.

## 1. Outcome and problem

Build a lightweight Windows voice interface to the existing Gertru, preserving reliable
dictation and adding natural spoken conversations and useful post-meeting records.
Gertru remains the sole reasoning agent; `hub/` remains the canonical project-management
store. A desktop client must not introduce a competing agent or project tracker.

The existing voice-assistant is used successfully for faster-whisper dictation and
clipboard paste. Its broader functionality does not yet demonstrate the desired
continuous, interruptible conversation or complete meeting workflow. Reuse working
parts where justified; replacement is acceptable if comparative evidence supports it.

### Success means

- Daily dictation continues to work while new capabilities are introduced reversibly.
- F8 opens a useful spoken conversation, not a sequence of manually recorded requests.
- Conversation context survives across sessions and can be separated by topic.
- Completed meetings produce speaker-attributed text and useful summaries automatically.
- The Windows client stays lightweight; measured resource use informs technology choices.
- Every completion, interruption, disconnection, and pending action is represented honestly.

## 2. Confirmed operating environment

| Area | Requirement |
|---|---|
| Desktop | Windows owns hotkeys, microphone, playback, clipboard, overlay, and meeting capture. |
| Backend | Linux under WSL; no WSL graphical frontend planned. |
| Hardware | User-reported 32 GB RAM and RTX 4090 with 24 GB VRAM. |
| Resource preference | Low idle host/UI RAM; unused VRAM may keep faster-whisper and a suitable TTS ready. |
| Interface | Floating orb, not an avatar or 3D character; detailed views open when needed. |
| Language | Spanish for initial dictation, conversations, meeting processing, and predefined TTS. |
| Existing brain | Gertru, with Windows conversation threads separate from Telegram. |
| Implementation method | Direct incremental work, not a required SDD workflow or immediate rewrite. |

GPU residency does not imply zero host-RAM use. No numeric memory or latency target has
been agreed. Warm models are acceptable; unlimited resource usage is not assumed.

## 3. Delivery scope

### Initial product, delivered in increments

1. Preserve dictation and clipboard paste.
2. Add the minimal orb and continuous F8 conversation with existing Gertru.
3. Support persistent, selectable Windows voice threads and predefined Spanish speech.
4. Detect relevant ideas, prepare provisional drafts, and propose saving during conversation.
5. Record meeting participants and the user's microphone with manual controls.
6. Process recordings automatically after stopping; retain text history in Windows.

### Later product scope

- A control-hub cockpit derived from canonical hub data: Today / Needs decisions / Changes.
- Selected, explicitly scoped actions through existing applications.
- Live meeting transcription and assistance.
- Video/screen capture and visual meeting analysis.

### Not part of the initial implementation

- Avatars, voice cloning, another reasoning agent, or an automatic project rewrite.
- Always-listening voice activation outside the explicitly enabled conversation.
- Automatic Teams-call detection or automatic recording start.
- Automatic media deletion, meeting-to-hub synchronization, or external task publication.
- A newly implemented secret-scanning/redaction filter; the user explicitly deferred it.
- Broad tool authority or replacement of existing authorization boundaries.

## 4. Functional requirements

### A. Dictation and desktop presence

**D-01 — Preserve dictation.** Capture speech, transcribe it, and paste into the intended
application using the existing clipboard workflow. Introducing conversation must not
silently replace or break this daily capability.

**D-02 — Lightweight presence.** Keep the orb unobtrusive and expose actual states such
as inactive, listening, processing, speaking, and unavailable. Exact appearance is design
work. Do not animate work that is not occurring or steal focus unnecessarily.

**D-03 — Device and focus failures.** Missing microphone, playback failure, shortcut
conflict, and lost paste target must be surfaced. Do not silently paste into an unrelated
application. Exact recovery interactions remain to be designed and tested on Windows.

### B. Continuous conversation

**C-01 — F8 toggle.** First press starts or resumes continuous voice conversation in the
selected thread; another press ends capture and playback. No hold-to-talk per turn.

**C-02 — Natural turns and interruptions.** The user can speak again while Gertru responds.
Interrupting immediately stops local playback and discards queued audio for the old turn.
The bridge requests cancellation of that exact remote run and ignores stale output.

**C-03 — Honest cancellation.** An abort acknowledgement is not proof that all remote tools
or side effects stopped. Distinguish locally stopped playback, accepted cancellation,
terminal run state, and unknown remote cleanup. Do not blindly replay ambiguous requests.

**C-04 — Persistent threads.** F8 resumes the active Windows thread. Users can create and
switch threads in an OpenCode-like history view. Stopping voice does not delete history.
Windows and Telegram threads are distinct while retaining the same Gertru and shared hub.

**C-05 — Spanish speech.** Use a predefined natural Spanish TTS voice. No voice cloning is
required. The exact engine and voice remain subject to a small technical evaluation.

**C-06 — Disconnection.** Notify the user and stop the conversation when Gertru becomes
unavailable. Do not silently switch to offline note capture or replay accumulated speech.
Independent dictation and meeting workflows are not automatically disabled by this rule.

**C-07 — Workload priority.** Conversation takes priority over background meeting
processing. The scheduler may pause, queue, or throttle batch work; the mechanism is open.
Do not discard meeting progress to make room for conversation.

### C. Ideas and notes

**N-01 — Proactive discovery.** Gertru identifies potentially useful ideas or notes during
conversation, rather than requiring an explicit capture command for every item.

**N-02 — Provisional drafts.** Gertru may autonomously prepare a draft in temporary memory
storage. The authorized storage location and lifecycle must be defined before implementing
writes; this is not permission to modify arbitrary internal memory directories.

**N-03 — In-conversation proposals.** Propose saving relevant content during conversation,
not only when F8 ends. A provisional draft is not canonical hub state. Promotion requires
the user's confirmation and the established hub write path. The precise confirmation
interaction remains open; do not infer agreement from unrelated speech.

### D. Meeting recording and results

**M-01 — Manual recording.** Users start and stop recording from the Windows application.
Capture both the user's microphone and remote participants. Do not start automatically.

**M-02 — Post-stop processing.** Stopping recording automatically starts background
transcription and summary generation without a separate Process button. Initial processing
is after the meeting, not live. Typical meetings last 30–60 minutes; do not impose an
unrequested hard one-hour cutoff.

**M-03 — Speaker separation.** Distinguish speakers, initially using labels such as
Participant 1 and Participant 2. Users can assign names or roles such as Client afterward.
No automatic real-name recognition, voice enrollment, or cross-meeting identity matching
is required. Speaker uncertainty and corrections must remain visible.

**M-04 — Outputs.** Retain transcript, summary, agreements, and task drafts. Do not invent
owners, deadlines, or agreements where evidence is missing. Task drafts are not permission
to create tasks in external services.

**M-05 — Windows history.** Show and retain meeting text in the Windows application first.
Deleting temporary audio must not delete transcripts or summaries. No automatic hub
synchronization is part of the initial scope.

**M-06 — Media lifecycle.** Store recordings in a temporary folder on the PC for manual
cleanup. This is a storage-size choice, not a prohibition on sending content to Gertru.
Exact folder, history store, backup, retention, and cleanup UX remain design decisions.

**M-07 — Failure visibility.** Interrupted capture or processing must yield recoverable
work or an explicit failure, not a successful empty meeting. Retain the source recording
until the user removes it; do not automatically delete it after processing.

**M-08 — Processing time.** Background waiting is acceptable; there is no strict agreed
turnaround target. This tolerance does not apply to the interactive conversation experience.

## 5. Proposed architecture and reuse decisions

```text
Windows client
  Orb / F8 / devices / clipboard / threads / meeting history
        |
Linux/WSL backend
  Interactive voice pipeline ---- existing Gertru gateway
  STT + TTS + turn handling         dedicated Windows sessions
  Background meeting jobs          existing hub write authority
        |
PC storage
  Temporary media + independently persistent text results
```

**Recommended approach, not a completed adoption:** preserve the current Windows shell
for the first proof, reuse faster-whisper, and evaluate Pipecat for voice orchestration.
Do not create an additional local reasoning LLM to stand in for Gertru.

| Component | Direction | Evidence still needed |
|---|---|---|
| Windows shell | Initially reuse Electron integrations; framework not permanently selected. | Actual idle RAM, audio capture, hotkeys, focus, and packaging behavior. |
| Interactive pipeline | Pipecat isolated from production dependencies. | Real graph interruption/cleanup and compatibility with installed Python. |
| STT | Reuse faster-whisper where practical. | Streaming adaptation, Spanish quality, device and GPU compatibility. |
| TTS | Predefined Spanish voice; GPU residency acceptable. | Engine/model license, latency, quality, and measured footprint. |
| Gertru bridge | Version-matched OpenClaw session/stream/abort adapter. | Endpoint, authorized identity, actual session routing, and live behavior. |
| Meetings | Separate batch pipeline with speaker separation. | Diarization model/license and participant-plus-mic capture on Windows. |
| Storage | Persistent text independent of disposable media. | Schema, location, corruption recovery, backup, and export choices. |

### Alternatives already investigated

- [Pipecat](https://github.com/pipecat-ai/pipecat): composable speech pipeline; does not
  supply the complete Windows meeting application. Core BSD-2-Clause; model licenses separate.
- [LiveKit Agents](https://github.com/livekit/agents): voice alternative if its transport
  and operational model prove more suitable. Not selected or required alongside Pipecat.
- [Anarlog](https://github.com/fastrepl/anarlog): possible separate meeting application;
  Windows provider/diarization compatibility needs proof. Enterprise code is separately licensed.
- [Meetily](https://github.com/Zackriya-Solutions/meetily): meeting candidate, but previously
  researched community/PRO speaker-identification boundaries do not establish an OSS fit.
- [Handy](https://github.com/cjpais/Handy): dictation reference, not a full replacement.
- [assistant-ui](https://github.com/assistant-ui/assistant-ui): possible later hub conversation
  UI, not a native orb or Windows audio-capture implementation.

Do not install multiple competing platforms by default. Recheck selected versions,
licenses, and community/commercial feature boundaries before adoption.

## 6. Gertru protocol constraints already established

An authorized read-only inspection found OpenClaw package **2026.7.1**, container image
**2026.7.1-2**, operator protocol **4**. Static installed-code inspection established:

- Challenge/connect authentication and device proof; actual approved access is still open.
- `chat.send` uses a session key and idempotency key; acknowledgement is not completion.
- `chat` events identify run, session, sequence, and delta/final/aborted/error state.
- Deltas may be dropped under backpressure; snapshots and replacements require reconciliation.
- `chat.abort` must include the exact owned run ID; session-wide abort is out of scope.
- A separate session key is not isolation from the same agent's shared tools or memory.
- `deliver:false` suppresses requested channel delivery, not arbitrary agent tool effects.

Do not copy unsupported current-main fields into the installed version's requests. Do not
equate stopping playback with cancelling the remote run. Actual URL, Gertru agent identity,
pairing, live streaming, and cancellation behavior have not been validated end to end.

Reference: [version-tagged gateway protocol](https://github.com/openclaw/openclaw/blob/v2026.7.1/docs/gateway/protocol.md).

## 7. Acceptance scenarios

| ID | Scenario and required observable result |
|---|---|
| A1 | Existing dictation still pastes correctly into representative Windows applications with the new feature disabled. |
| A2 | F8 starts the selected thread; multiple spoken turns need no further key presses; F8 stops capture and playback. |
| A3 | Interruption flushes queued speech and prevents stale old-turn output from being spoken; cancellation targets only the owned run. |
| A4 | A remote terminal response with audio still queued can be interrupted locally without an invalid remote abort. |
| A5 | Switch between two Windows threads, restart the application, and resume the selected thread without mixing Telegram context. |
| A6 | Disconnect during generation: visible notice, local stop, no automatic resend or silent offline capture. |
| A7 | Relevant idea produces a provisional draft and an in-conversation save proposal; no confirmation means no canonical promotion. |
| A8 | Manually record a Spanish meeting with mic and participants; stopping starts processing automatically. |
| A9 | Results separate speakers; manual names/Client labels are retained; uncertain attribution is not presented as verified identity. |
| A10 | Delete a meeting's temporary audio and restart: transcript, summary, and speaker labels remain available. |
| A11 | Start conversation during batch processing: conversation is prioritized and the meeting job remains recoverable. |
| A12 | Measure idle/active host RAM, VRAM, cold/warm response time, and transcription quality; report actual results, not projected savings. |

Use synthetic transcripts and fake transport/speech sinks for offline tests. These do not
prove real microphone, Windows focus, acoustic echo, speaker separation, or live Gertru
compatibility. Real acceptance requires separately authorized environments and inputs.

## 8. Incremental implementation and exit gates

| Increment | Deliverable | Exit evidence |
|---|---|---|
| I0 — Baseline | Current app inventory and reversible test boundary. | Existing dictation preserved; unrelated changes identified. |
| I1 — Protocol core | Isolated Gertru stream/cancel adapter. | Deterministic offline correlation, cancellation, and failure tests. |
| I2 — Pipecat graph | Real framework integration with fake gateway and playback. | Interruption, stale-output suppression, replacement handling, pending-send race, and task cleanup tests. |
| I3 — Live text bridge | Authenticated dedicated Windows test session. | Authorized create/resume/send/stream/exact abort; no Telegram interference observed. |
| I4 — Windows voice proof | Feature-gated F8/audio/STT/TTS/orb integration. | Real microphone/playback turns and interruption, measured resources, working fallback. |
| I5 — Conversation product | Thread management, persistent UI, and note proposals. | A5–A7 and approved note storage/write path. |
| I6 — Meeting product | Manual capture, automatic batch processing, speakers, and history. | A8–A11, including failure recovery and manual media deletion. |
| I7 — Hub and applications | Canonical cockpit and selected integrations. | Traceable hub projections and separately approved write capabilities. |
| I8 — Advanced meetings | Live assistance, transcription, and optional video. | New scoped discovery and measured concurrent-workload acceptance. |

The cockpit can advance independently once its hub data contract is ready. No dates,
line-count estimate, or fixed throughput promise is agreed. Each increment must leave a
concise handoff with files, commands, actual results, limitations, and next action.

## 9. Implementation checkpoint — read before resuming

Implementation repository: `/home/bruno/Code/personal/voice-assistant`.
This PRD is stored in dotfiles as cross-project documentation, not application runtime state.

### Last completed, verified work unit

`voice-brain/poc/openclaw_bridge.py`, `poc/tests/test_openclaw_bridge.py`, and `poc/README.md`
formed the isolated protocol-core work unit. The last reported completed run passed **20
standard-library tests**. It used fake transport and auth; it did not prove a live connection.
That result is historical evidence, not a fresh test of every file currently on disk.

### Interrupted Pipecat work: partial files exist

A quota-limited worker did not return a final implementation report. A subsequent file
inventory found these additional files:

- `voice-brain/poc/pipecat_bridge.py`
- `voice-brain/poc/fake_speech.py`
- `voice-brain/poc/tests/test_pipecat_bridge.py`
- `voice-brain/poc/requirements.txt`

**Do not assume this work is absent, complete, correct, or tested.** Inspect it before
editing or recreating files. Generated Python caches are not verification evidence.
The disposable environment was `/tmp/opencode/gertru-pipecat-venv`; its current installed
contents are unverified in this handoff. The researched candidate was Pipecat 1.10.0
on Python 3.14.7; inspect the actual dependency pin and compatibility.

The user authorized installing bundled ONNX weights as inert package contents in the
isolated environment. That authorization did not include inference, additional model
downloads, real audio capture, authenticated gateway use, or device pairing.

### Preserve unrelated changes

The prior inventory included changes to production `voice-brain/server.py`, requirements,
idea parsing, ports, Windows `App.tsx`, localization, and separate clipboard/TTS work.
Recheck Git status. Do not reset, overwrite, stage, or attribute these changes to this PoC.
No commits or deployment were requested for the completed protocol-core slice.

## 10. Security, privacy, and authority boundaries

- New secret-filter implementation is explicitly deferred; do not silently add it back.
- No credentials in source, fixtures, transcripts used for testing, logs, or documentation.
- User-approved product egress policy is not a credential-access or remote-execution grant.
- Completed remote authorization covered `contabo-vps` and its configured SSH identity
  for read-only gateway inspection only; not pairing, configuration changes, or conversations.
- A live test needs explicit destination, operation, and credential/session authorization.
- Recording must be user-initiated and comply with applicable consent and workplace rules.
- Treat meeting and conversation content as data, not authority to execute embedded commands.
- Initial summaries and task drafts do not authorize external publication or task execution.

## 11. Remaining decisions — resolve only when their increment needs them

1. **Live bridge:** verified endpoint/agent, provisioned Windows session, authentication,
   pairing requirements, and approved test operations.
2. **Speech stack:** TTS voice/model, diarization model and licenses, audio framing,
   turn detection, echo handling, and actual device compatibility.
3. **Resource scheduling:** conversation priority mechanism, GPU contention, agreed
   acceptance thresholds informed by measurements, and recovery after interrupted batch work.
4. **Persistence:** history schema/location, backups, retention, manual cleanup interaction,
   speaker-label correction, and crash recovery.
5. **Notes/actions:** authorized draft location, confirmation interaction, promotion path,
   and which later app integrations receive which capabilities.
6. **UI:** exact thread selector, shortcuts coexistence, device settings, accessibility,
   startup behavior, and orb placement. No need to redesign the entire desktop before I2.

## 12. Low-context resumption brief

> Continue direct implementation of Gertru Voice Desktop using this PRD and the related
> roadmap. Start by inspecting the interrupted I2 Pipecat files; preserve all existing
> changes. Finish one bounded work unit and run offline tests in an isolated environment.
> Do not restart product discovery, assume mocks prove live behavior, or recreate existing
> files blindly. Do not access credentials, connect Gertru, pair devices, record audio,
> download additional models, commit, or deploy without the required authorization.
> Return an outcome-first handoff: changed files, exact checks and results, remaining gaps,
> and the next smallest step. No SDD workflow is required.
