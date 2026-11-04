#!/usr/bin/env bash
# Idempotent host setup for TikTok ingest on-demand runs:
#   1. Link and enable llm-hub-chat.service (vision + synthesis) and
#      llm-hub-whisper.service (transcription) so llm-hub comes back after a
#      WSL/host restart. Deliberately NOT podman-restart.service: that one
#      starts every restart:always container on the machine.
#   2. Report whether both backends answer.
# Prerequisite: the llm-hub containers exist
#   (cd ~/Code/personal/llm-hub && podman-compose -f podman-compose.yml up -d --no-deps llama-chat whisper-service).
# Synthesis env vars live in shell/exports.sh. Safe to run multiple times.
set -euo pipefail

DOTFILES="$HOME/.dotfiles"
UNITS=(llm-hub-chat.service llm-hub-whisper.service)

mkdir -p "$HOME/.config/systemd/user"
for unit in "${UNITS[@]}"; do
  ln -sfn "$DOTFILES/ai/services/$unit" "$HOME/.config/systemd/user/$unit"
done
systemctl --user daemon-reload
for unit in "${UNITS[@]}"; do
  systemctl --user enable --now "$unit"
  systemctl --user is-active "$unit"
done

if curl -sf --max-time 5 "${TIKTOK_INGEST_TEXT_API_BASE_URL:-http://127.0.0.1:8081/v1}/models" >/dev/null; then
  echo "llm-hub chat (vision + synthesis): reachable"
else
  echo "llm-hub chat: NOT reachable yet (it may still be loading); check: podman logs llm-hub-chat"
fi
if curl -sf --max-time 5 "http://127.0.0.1:8766/health" >/dev/null; then
  echo "llm-hub whisper (transcription): reachable"
else
  echo "llm-hub whisper: NOT reachable yet (it may still be loading); check: podman logs llm-hub-whisper"
fi
