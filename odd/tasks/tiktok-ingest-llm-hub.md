# tiktok-ingest on llm-hub — ODD feature document

Objective: every model call of tiktok-ingest (vision/OCR, transcription, synthesis) goes to llm-hub resident services; tiktok-ingest stops managing Ollama, Whisper containers, GPU leases and swap retries.
Branch: dotfiles `feat/tiktok-ingest-llm-hub` (from `feat/tiktok-web-ux` `9cf650d`); llm-hub `feat/tiktok-ingest-vision-whisper-gpu`. Delivery: ask-on-risk, local commits only, no push. Test runner: `cd ai/tiktok-ingest && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s src/tiktok_ingest/tests -t src` (baseline 1197 OK, 5 skipped).

## Specs

- S1 — User request (verbatim): "en tiktok-ingest, necesito migrar a usar en todo llm-hub".
- S2 — Vision (kickoff default 1, accepted with "dale"): vision/OCR uses llm-hub `llama-chat` (`http://127.0.0.1:8081/v1/chat/completions`, OpenAI-compatible, `image_url` data URLs, model `qwen-3.8-27b` = same resident model as synthesis, mmproj F16). No `qwen3.5:9b`, no isolated Ollama.
- S3 — Transcription (default 2): `llm-hub-whisper` on GPU (float16, large-v3, es, VAD off), WS `ws://127.0.0.1:8765/ws/transcribe`, health `http://127.0.0.1:8766/health`. `voice-assistant-whisper` is not used nor started/stopped by tiktok-ingest.
- S4 — Remove dead code (default 3): delete `ollama_runtime.py`, `whisper_lifecycle.py`, `gpu_lease.py`, Ollama-specific gates, swap cooldown / swap-retry, and their tests, CLI subcommands/flags and docs.
- S5 — Routing (default 4): direct llm-hub ports, no LiteLLM.
- S6 — Commits (default 5): local commits on feature branches in both repos, including the pending llm-hub Qwen3.8 compose edits; no push.
- S7 — Assumption: historical run artifacts (`meta.json` with `swap_retry`, old fingerprints) still load; old fingerprints simply stop matching (re-run recomputes).
- S8 — Assumption: FFmpeg runs from llm-hub's whisper image (`localhost/llm-hub_whisper-service:latest`, same ffmpeg 4.4.2-0ubuntu0.22.04.1) so no voice-assistant artifact is needed.

## Tasks

| ID | Specs | Route | Status | Commit / evidence |
|----|-------|-------|--------|-------------------|
| T1 | S2, S3, S6 | inline — llm-hub: mmproj, whisper GPU + health port + server.py ported from voice-assistant | done | llm-hub `2970d82`, `8362f3d` (OOM fix); OCR probe 1.2 s exact text; real 48 s clip transcript identical to stored audio.md; VRAM 22.4/24 GiB both awake |
| T2 | S2, S3, S8 | inline — config + vision.py protocol to OpenAI chat; whisper endpoints; ffmpeg image; systemd unit for llm-hub-whisper | done | `c02a907`; RED ImportError parse_chat_response; GREEN 1167 OK; real 58-frame clip complete in 534 s on scratch state |
| T3 | S4, S7 | delegated writer (context backstop: engine 1.5k, collection_run 3.1k, guided 1k lines + 7 test files) — remove lease/lifecycle/swap/Ollama code, tests and docs | done | `8a7c038`; RED podman ps called twice in a guided run; GREEN 915 OK (252 dead tests deleted); real CLI audio on llm-hub-whisper complete, no container touched |
| T4 | all | independent verifier (Risk items 3, 5) + parent differential | done | verifier: no blocker, S7 unproven (empty probe); parent differential on real state copy: `status`, `backlog-list`, `summary`, `summary --json`, `summary --collection <2 keys> --report latest` IDENTICAL base 9cf650d vs HEAD; only diff = removed commands in usage text; test_web_review flake not reproduced (3/3 OK) |

## Log

- L1 (2026-11-04, user, verbatim): "en tiktok-ingest, necesito migrar a usar en todo llm-hub"
- L2 (2026-11-04, user, verbatim): "dale" — all five kickoff defaults accepted.
- L3: llm-hub-whisper on GPU + llama-chat awake + voice-assistant-whisper on GPU would exceed 24 GiB, so voice-assistant-whisper was stopped (it is not used by herdr-brain; tiktok-ingest used to stop it anyway).
- L4: llm-hub's whisper server.py was an old copy (1 MiB WS message cap, auto language, VAD on) → closed the connection on a 48 s clip; ported voice-assistant `fad180a` server.py.
- L5: another session has an uncommitted edit in llm-hub `config/litellm-config.yaml` (qwen-3.8 alias); left untouched.
- L6: first real vision run died at frame 7: llama-chat cgroup OOM-kill (mem_limit 2g; llama.cpp host prompt cache defaults to 8 GiB). Fixed in llm-hub `8362f3d` (cache-ram 1024, mem 4g); rerun complete, RSS 3.0/4.3 GB, VRAM 23.0/24 GiB.
- L7: vision on the 27B averages ~9 s/frame → `vision_stage_deadline_seconds` 600 → 900 (decision taken: 64 frames + 8 rereads must fit).
- L8: the other llm-hub session moved whisper health to 127.0.0.1:8766 and made llm-hub-whisper the only host whisper (voice-assistant consumes it); tiktok-ingest follows (health :8766).
- L9: commit `c02a907` alone still has engine/guided stopping whisper before vision; T3 removes that path.
- L10: another session committed `bb23f1b` on this branch and added `ai/services/llm-hub.service` (starts every llm-hub-* container); it overlaps harmlessly with llm-hub-chat/llm-hub-whisper units.
- L11: closed. Next: user decides push/merge for both branches; optionally drop the per-container units in favor of llm-hub.service.
