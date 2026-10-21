#!/usr/bin/env bash
# launchpad deploy script.
#
# Usage: ./deploy.sh [back]
#   back : build + install binary + map unit/config + restart the service,
#          then (once healthy) install the discovery timer
#
# Idempotent: symlinks are re-pointed, the unit is reloaded, the service
# restarted. Requires cargo and a systemd user session.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${LAUNCHPAD_PORT:-47625}"

deploy_back() {
  echo "==> building launchpad (release)"
  (cd "$APP" && cargo build --release)

  echo "==> installing binary"
  mkdir -p "$HOME/.local/bin"
  # Stop first: the running service holds the binary open ("Text file busy").
  systemctl --user stop launchpad.service
  cp "$APP/target/release/launchpad" "$HOME/.local/bin/launchpad"

  echo "==> mapping unit + tiles config"
  mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/launchpad"
  ln -sfn "$APP/systemd/launchpad.service" "$HOME/.config/systemd/user/launchpad.service"
  # Never overwrite a live tiles.json: seed it only on first install.
  if [ ! -e "$HOME/.config/launchpad/tiles.json" ]; then
    ln -sfn "$APP/config/tiles.json" "$HOME/.config/launchpad/tiles.json"
  fi
  systemctl --user daemon-reload
  # Enable: without the default.target.wants link the unit never starts on login.
  systemctl --user enable launchpad.service

  echo "==> starting service"
  systemctl --user start launchpad.service
  sleep 1
  curl -sf -m 6 "http://127.0.0.1:${PORT}/api/health" >/dev/null \
    || { echo "ERROR: launchpad not healthy after restart" >&2; exit 1; }
  echo "==> launchpad OK (http://127.0.0.1:${PORT})"

  # Only after the app is healthy: periodic local discovery (idempotent).
  echo "==> installing discovery timer"
  bash "$APP/scripts/install-discovery.sh"
}

case "${1:-back}" in
  back) deploy_back ;;
  *) echo "usage: $0 [back]" >&2; exit 2 ;;
esac
