#!/usr/bin/env bash
# Instalador idempotente de teams-to-tasks: mapea dotfiles -> mundo real.
# Safe to re-run. No ejecuta dotbot.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENCODE_SKILLS="$HOME/.config/opencode/skills"
OPENCODE_SCRIPTS="$HOME/.config/opencode/scripts"
CFG="$HOME/.config/opencode/opencode.json"

echo "[1/7] Dependencias"
for bin in opencode jq flock npx crontab curl bun; do
  command -v "$bin" >/dev/null 2>&1 || { echo "FALTA: $bin"; exit 1; }
done
[ -f "$CFG" ] || { echo "FALTA: $CFG (crea un opencode.json mínimo antes)"; exit 1; }

echo "[2/7] wsl-notify-send.exe (binario de terceros — descarga versionada, nunca en el repo)"
WNS="$HOME/.local/bin/wsl-notify-send.exe"
if [ -x "$WNS" ]; then
  echo "ya instalado en $WNS"
else
  WNS_URL="https://github.com/stuartleeks/wsl-notify-send/releases/download/v0.1.871612270/wsl-notify-send_windows_amd64.zip"
  TMP="$(mktemp -d)"
  curl -fsSL "$WNS_URL" -o "$TMP/wns.zip"
  mkdir -p "$HOME/.local/bin"
  unzip -o "$TMP/wns.zip" -d "$HOME/.local/bin/" >/dev/null
  rm -rf "$TMP"
  [ -x "$WNS" ] || chmod +x "$WNS"
  echo "instalado en $WNS (v0.1.871612270)"
fi

echo "[3/7] Symlinks (skills + cron.sh)"
ln -sfn "$REPO/ai/agents/opencode/skills/teams-to-tasks" "$OPENCODE_SKILLS/teams-to-tasks"
ln -sfn "$REPO/ai/agents/opencode/skills/notion-personal-backlog" "$OPENCODE_SKILLS/notion-personal-backlog"
ln -sfn "$REPO/ai/agents/opencode/skills/dotfiles-context" "$OPENCODE_SKILLS/dotfiles-context"
mkdir -p "$OPENCODE_SCRIPTS"
ln -sfn "$REPO/ai/teams-to-tasks/cron.sh" "$OPENCODE_SCRIPTS/teams-to-tasks-cron.sh"

echo "[4/7] Directorios locales (datos, no config)"
mkdir -p "$HOME/.local/state/teams-to-tasks"
mkdir -p "$HOME/.local/share/opencode/playwright-teams-profile"

echo "[5/7] Poller teams-to-tasks (bun install + smoke check)"
( cd "$REPO/ai/teams-to-tasks" && bun install --frozen-lockfile ) || { echo "FALLO: bun install"; exit 1; }
# Chromium drift se detecta AQUI, no a las 08:00: reusamos el resolutor real del poller.
CHROME="$( ( cd "$REPO/ai/teams-to-tasks" && bun -e 'const { resolveChromiumExecutablePath } = await import("./poller.ts"); console.log(resolveChromiumExecutablePath(process.env.HOME + "/.cache/ms-playwright") ?? "");' ) || true )"
if [ -z "$CHROME" ] || [ ! -x "$CHROME" ]; then
  echo "FALTA: chromium ejecutable en ~/.cache/ms-playwright (ejecuta: bunx playwright install chromium)"
  exit 1
fi
echo "chromium: $CHROME"
# Smoke determinista del poller: con caché vacía debe fallar cerrado con rc 2
# (sin tocar el perfil real ni la red) — valida módulos, tsconfig y mapeo de exit codes.
SMOKE_CACHE="$(mktemp -d)"
set +e
TEAMS_PLAYWRIGHT_CACHE="$SMOKE_CACHE" timeout -k 5s 30s bun "$REPO/ai/teams-to-tasks/poller.ts" >/dev/null 2>&1
SMOKE_RC=$?
set -e
rm -rf "$SMOKE_CACHE"
if [ "$SMOKE_RC" -ne 2 ]; then
  echo "FALLO: smoke del poller rc=$SMOKE_RC (esperado 2 con caché vacía)"
  exit 1
fi
echo "smoke ok: poller sin chromium → rc 2 (fail-closed verificado)"

echo "[6/7] Crontab (laborable, cada hora 8-18)"
LINE="0 8-18 * * 1-5 $OPENCODE_SCRIPTS/teams-to-tasks-cron.sh"
if crontab -l 2>/dev/null | grep -Fq "teams-to-tasks-cron.sh"; then
  echo "ya instalado"
else
  (crontab -l 2>/dev/null; echo "$LINE") | crontab -
  echo "añadido: $LINE"
fi

echo "[7/7] Fragmento MCP en opencode.json"
if jq -e '.mcp.playwright_teams' "$CFG" >/dev/null 2>&1; then
  echo "ya presente"
else
  TMP="$(mktemp)"
  sed "s|\$HOME|$HOME|g" "$REPO/ai/agents/opencode/mcp/playwright_teams.fragment.json" \
    | jq -s '.[0] * .[1]' "$CFG" - > "$TMP"
  mv "$TMP" "$CFG"
  echo "aplicado playwright_teams a $CFG"
fi

echo "Listo. Reinicia opencode si es la primera vez, y haz login manual una vez en la ventana de Chromium."
