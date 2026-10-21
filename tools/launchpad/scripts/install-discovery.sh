#!/usr/bin/env bash
# Install the launchpad discovery timer (systemd user, local only).
#
# Usage: ./install-discovery.sh
#
# Idempotent: re-points the unit/timer symlinks, reloads systemd and enables
# the TIMER only (the oneshot service is started by the timer, never enabled).
# Requires a systemd user session. Also run by deploy.sh after the app is healthy.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"

echo "==> mapping discovery service + timer"
mkdir -p "$UNIT_DIR"
for unit in launchpad-discover.service launchpad-discover.timer; do
  ln -sfn "$APP/systemd/$unit" "$UNIT_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now launchpad-discover.timer
echo "==> launchpad-discover.timer enabled (status: systemctl --user list-timers launchpad-discover.timer)"
