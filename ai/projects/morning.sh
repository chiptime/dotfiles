#!/usr/bin/env bash
# Morning chain: regenerate snapshot (md/html/json) then send the daily digest.
# Anchored to PC boot (projects-morning.timer, Persistent=true) so it fires when
# Bruno actually starts the day; also fires at 07:00 if the PC is already on.
set -euo pipefail

LOG_DIR="$HOME/.local/state/projects-dashboard"
mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/cron.log" 2>&1
echo "$(date -Is) morning chain: start"
"$HOME/bin/projects" >/dev/null
"$HOME/.dotfiles/ai/projects/cron.sh"
echo "$(date -Is) morning chain: done"
