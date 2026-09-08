#!/usr/bin/env bash
# ai-quotas deploy script.
#
# Usage:
#   ./deploy.sh back    build + install + restart the WSL backend service
#   ./deploy.sh tray    cross-compile + deploy the Windows tray + relaunch
#   ./deploy.sh watchdog (re)create the Windows scheduled task (5 min)
#   ./deploy.sh all     back + tray + watchdog (default)
#
# Requirements:
#   back : cargo, systemctl (user session), the service unit symlinked in
#          ~/.config/systemd/user/ai-quotas.service
#   tray : rustup target x86_64-pc-windows-gnu + mingw-w64 linker
#          (x86_64-w64-mingw32-gcc); WSL distro name = Ubuntu; tray lives in
#          C:\Users\<user>\ai-quotas-tray\
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRAY="$APP/tray-windows"
WIN_USER="$(ls /mnt/c/Users | grep -vx -E 'Public|Default|Default User|All Users|desktop.ini' | head -1)"
TRAY_DIR="/mnt/c/Users/$WIN_USER/ai-quotas-tray"
DISTRO="Ubuntu"

deploy_back() {
  echo "==> building backend (release)"
  (cd "$APP" && cargo build --release)
  echo "==> installing + restarting service"
  systemctl --user stop ai-quotas.service
  cp "$APP/target/release/ai-quotas" "$HOME/.local/bin/ai-quotas"
  systemctl --user start ai-quotas.service
  sleep 2
  curl -sf -m 6 http://127.0.0.1:47623/api/health >/dev/null \
    || { echo "ERROR: backend not healthy after restart" >&2; exit 1; }
  echo "==> back OK (http://127.0.0.1:47623)"
}

deploy_tray() {
  echo "==> building tray (windows-gnu)"
  (cd "$TRAY" && cargo build --release --target x86_64-pc-windows-gnu)
  echo "==> deploying to $TRAY_DIR"
  taskkill.exe /IM ai-quotas-tray.exe /F >/dev/null 2>&1 || true
  cp "$TRAY/target/x86_64-pc-windows-gnu/release/ai-quotas-tray.exe" "$TRAY_DIR/"
  (cd "$TRAY_DIR" && cmd.exe /c "start ai-quotas-tray.exe" >/dev/null 2>&1 &)
  echo "==> waiting for tray process (interop spawn can be slow)"
  tray_ok=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 3
    if tasklist.exe 2>/dev/null | grep -i ai-quotas-tray >/dev/null; then tray_ok=1; break; fi
  done
  [ -n "$tray_ok" ] || { echo "ERROR: tray not running after launch" >&2; exit 1; }
  echo "==> tray OK"
}

deploy_watchdog() {
  echo "==> (re)creating watchdog scheduled task (every 5 min, hidden via vbs)"
  schtasks.exe /create /tn "ai-quotas-watchdog" \
    /tr "wscript.exe C:\\Users\\$WIN_USER\\ai-quotas-tray\\watchdog.vbs" \
    /sc minute /mo 5 /f >/dev/null
  echo "==> watchdog OK"
}

case "${1:-all}" in
  back)     deploy_back ;;
  tray)     deploy_tray ;;
  watchdog) deploy_watchdog ;;
  all)      deploy_back; deploy_tray; deploy_watchdog ;;
  *) echo "usage: $0 [back|tray|watchdog|all]" >&2; exit 2 ;;
esac
