#!/usr/bin/env bash
# Idempotent installer for the weekly projects digest cron line.
# Safe to run multiple times: replaces any previous ai/projects/cron.sh entry.
set -euo pipefail

LINE="0 9 * * 1 $HOME/.dotfiles/ai/projects/cron.sh >> $HOME/.local/state/projects-dashboard/cron.log 2>&1"

mkdir -p "$HOME/.local/state/projects-dashboard"
( crontab -l 2>/dev/null | grep -vF "ai/projects/cron.sh" || true; echo "$LINE" ) | crontab -

echo "Installed crontab line:"
crontab -l | grep -F "ai/projects/cron.sh"
