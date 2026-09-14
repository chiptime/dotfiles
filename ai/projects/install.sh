#!/usr/bin/env bash
# Idempotent installer for the projects dashboard subsystem:
#   1. Weekly ntfy digest cron line (Monday 09:00)
#   2. Daily snapshot+HTML regeneration cron line (08:00, silent, no notify)
#   3. systemd user service serving PROJECTS.html on port 47624 (Tailscale-reachable)
# Safe to run multiple times.
set -euo pipefail

DOTFILES="$HOME/.dotfiles"
DIGEST_LINE="0 9 * * 1 $DOTFILES/ai/projects/cron.sh >> $HOME/.local/state/projects-dashboard/cron.log 2>&1"
REGEN_LINE="0 8 * * * $HOME/bin/projects > /dev/null 2>&1  # projects-dashboard daily regen"

mkdir -p "$HOME/.local/state/projects-dashboard"

# 1. weekly digest (idempotent: replaces any previous entry)
( crontab -l 2>/dev/null | grep -vF "ai/projects/cron.sh" || true; echo "$DIGEST_LINE" ) | crontab -

# 2. daily silent regeneration (idempotent by marker comment)
( crontab -l 2>/dev/null | grep -vF "projects-dashboard daily regen" || true; echo "$REGEN_LINE" ) | crontab -

echo "Installed crontab lines:"
crontab -l | grep -E "ai/projects/cron.sh|projects-dashboard daily regen"

# 3. systemd user service (standalone from ai-quotas)
ln -sfn "$DOTFILES/ai/services/projects-dashboard.service" "$HOME/.config/systemd/user/projects-dashboard.service"
systemctl --user daemon-reload
systemctl --user enable --now projects-dashboard.service 2>/dev/null || true
systemctl --user is-active projects-dashboard.service && echo "Service active on http://127.0.0.1:47624/PROJECTS.html"
