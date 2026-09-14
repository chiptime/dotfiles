#!/usr/bin/env bash
# Idempotent installer for the projects dashboard subsystem:
#   1. Morning chain systemd timer (boot catch-up + 07:00): regen + daily digest
#   2. systemd user service serving PROJECTS.html on port 47624 (Tailscale-reachable)
# Safe to run multiple times.
set -euo pipefail

DOTFILES="$HOME/.dotfiles"
DIGEST_LINE="30 8 * * * $DOTFILES/ai/projects/cron.sh >> $HOME/.local/state/projects-dashboard/cron.log 2>&1"
REGEN_LINE="0 7 * * * $HOME/bin/projects > /dev/null 2>&1  # projects-dashboard daily regen"

mkdir -p "$HOME/.local/state/projects-dashboard"

# 1. morning chain via systemd timer (boot catch-up + 07:00) — replaces crontab lines
( crontab -l 2>/dev/null | grep -vF "ai/projects/cron.sh" | grep -vF "projects-dashboard daily regen" || true ) | crontab - 2>/dev/null || true
for u in projects-morning.service projects-morning.timer; do
  ln -sfn "$DOTFILES/ai/services/$u" "$HOME/.config/systemd/user/$u"
done
systemctl --user daemon-reload
systemctl --user enable --now projects-morning.timer >/dev/null 2>&1 || true
systemctl --user list-timers projects-morning.timer --no-pager | head -3

# 3. systemd user service (standalone from ai-quotas)
ln -sfn "$DOTFILES/ai/services/projects-dashboard.service" "$HOME/.config/systemd/user/projects-dashboard.service"
systemctl --user daemon-reload
systemctl --user enable --now projects-dashboard.service 2>/dev/null || true
systemctl --user is-active projects-dashboard.service && echo "Service active on http://127.0.0.1:47624/PROJECTS.html"
