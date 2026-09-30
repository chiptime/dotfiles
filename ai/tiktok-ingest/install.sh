#!/usr/bin/env bash
# Idempotent host setup for TikTok ingest on-demand runs:
#   1. Link and enable llm-hub-chat.service so the synthesis backend comes back
#      after a WSL/host restart. Deliberately NOT podman-restart.service: that
#      one starts every restart:always container on the machine.
#   2. Report whether the synthesis backend answers.
# Prerequisite: the llm-hub-chat container exists
#   (cd ~/Code/personal/llm-hub && podman-compose -f podman-compose.yml up -d llama-chat).
# Synthesis env vars live in shell/exports.sh. Safe to run multiple times.
set -euo pipefail

DOTFILES="$HOME/.dotfiles"
UNIT=llm-hub-chat.service

mkdir -p "$HOME/.config/systemd/user"
ln -sfn "$DOTFILES/ai/services/$UNIT" "$HOME/.config/systemd/user/$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"
systemctl --user is-active "$UNIT"

if curl -sf --max-time 5 "${TIKTOK_INGEST_TEXT_API_BASE_URL:-http://127.0.0.1:8081/v1}/models" >/dev/null; then
  echo "synthesis backend: reachable"
else
  echo "synthesis backend: NOT reachable yet (it may still be loading); check: podman logs llm-hub-chat"
fi
