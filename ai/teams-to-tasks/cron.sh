#!/usr/bin/env bash
# teams-to-tasks — poller-gated hourly sweep (cron) + 18:30 digest
# Sweep mode runs the read-only poller (bun poller.ts) FIRST; the LLM agent
# only runs on news. Poller exit codes gate the run:
#   0 news    → agent sweeps the PREV_LAST_RUN window; after the agent
#               SUCCEEDS, cron advances last_run to the poll-start T
#               (the poller wrote nothing — advancement is deferred).
#   1 no-news → log only (the poller itself advanced last_run to its start).
#   * (2/124/137/…) failure → log + notify, no agent, last_run untouched.
# Digest mode (18:30) is unchanged and never touches last_run.
# Pause:  touch ~/.local/state/teams-to-tasks/PAUSE
# Resume: rm    ~/.local/state/teams-to-tasks/PAUSE
# Log:    ~/.local/state/teams-to-tasks/cron.log
set -u

STATE_DIR="$HOME/.local/state/teams-to-tasks"
STATE_FILE="$STATE_DIR/last_run"
PAUSE_FILE="$STATE_DIR/PAUSE"
LOG="$STATE_DIR/cron.log"
# Test seams consumed by tests/cron.test.ts (defaults are the production values):
#   TEAMS_LOCK_FILE, TEAMS_POLL_CMD, TEAMS_AGENT_CMD, TEAMS_NOTIFY_EXE
LOCK="${TEAMS_LOCK_FILE:-/tmp/teams-to-tasks.lock}"
mkdir -p "$STATE_DIR"

# Desktop notifications: on WSL the reliable channel is wsl-notify-send.exe (Windows toast).
# Installed by scripts/install-teams-to-tasks.sh to ~/.local/bin (pinned release, never in the repo).
# Override via TEAMS_NOTIFY_EXE if it lives elsewhere.
NOTIFY_EXE="${TEAMS_NOTIFY_EXE:-$HOME/.local/bin/wsl-notify-send.exe}"
NOTIFY_EXE_FALLBACK="/mnt/d/Bruno/wsl/tools/wsl-notify-send.exe"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
notify() { # notify <urgency: normal|critical> <title> <body>
  local urgency="$1" title="$2" body="$3"
  if [ -x "$NOTIFY_EXE" ]; then
    "$NOTIFY_EXE" "$title — $body" >/dev/null 2>&1 || true
  elif [ -x "$NOTIFY_EXE_FALLBACK" ]; then
    "$NOTIFY_EXE_FALLBACK" "$title — $body" >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send -a teams-to-tasks -u "$urgency" "$title" "$body" 2>/dev/null || true
  else
    echo "$(date '+%F %T') [notify-off] $title: $body" >> "$LOG"
  fi
}

# Kill switch
if [ -e "$PAUSE_FILE" ]; then
  echo "$(date '+%F %T') [paused] PAUSE file present" >> "$LOG"
  exit 0
fi

# Serialize overlapping runs (cron vs cron; interactive sessions have their own flow).
# The lock spans poller → agent, so the two never hold the browser profile concurrently.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date '+%F %T') [skip] another run holds the lock" >> "$LOG"
  exit 0
fi

cd "$HOME"

MODE="${1:-sweep}"

# cron.sh is symlinked into ~/.config/opencode/scripts; readlink -f lands on the
# repo copy, whose dir already holds poller.ts, src/, and node_modules/ — no
# second symlink needed, symlinks/conf.linux.yaml gains no entry.
SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

run_poller() { # test seam: TEAMS_POLL_CMD replaces the real poller
  if [ -n "${TEAMS_POLL_CMD:-}" ]; then
    bash -c "$TEAMS_POLL_CMD"
  else
    timeout -k 15s 120s bun "$SCRIPT_DIR/poller.ts"
  fi
}

run_agent() { # test seam: TEAMS_AGENT_CMD replaces the real agent runner
  if [ -n "${TEAMS_AGENT_CMD:-}" ]; then
    bash -c "$TEAMS_AGENT_CMD"
  else
    zsh -ic 'opencode run "$TEAMS_PROMPT"'
  fi
}

if [ "$MODE" = "digest" ]; then
  export TEAMS_PROMPT="Ejecuta la skill teams-to-tasks en MODO DIGEST (run programado 18:30, usuario ausente):
1. Navega a https://teams.microsoft.com con el MCP playwright_teams. Si aparece login, ABORTA con 'SESION_EXPIRADA: login manual necesario'.
2. Lee TODOS los mensajes de HOY (búsqueda 'que' + filtro Date=Today, sin ventana temporal).
3. Produce un digest estructurado: Temas tratados / Decisiones / Dudas abiertas / Tareas ya capturadas (por título).
4. En Notion: busca la página '📡 Digest Teams' bajo Personal (1) y añade un heading_2 con la fecha de hoy seguido del digest en bullets (créala si no existe; no dupliques el heading si la fecha ya está).
5. CIERRA el navegador y devuelve 1 línea de resumen."
else
  # Watermark captured BEFORE polling: on news the poller writes nothing, so
  # this is both the window the agent sweeps and the value state keeps if the
  # agent fails.
  PREV_LAST_RUN=$(cat "$STATE_FILE" 2>/dev/null || date '+%Y-%m-%d 00:00')
  # Poll-start T — written to state only after the agent succeeds. An earlier
  # anchor is safe (fingerprint dedupe re-reads); a later one could skip.
  POLL_START_T=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

  run_poller
  POLL_RC=$?

  case "$POLL_RC" in
    0) : ;; # news — fall through to the agent below
    1)
      echo "$(date '+%F %T') [quiet] poller: no news (last_run advanced by the poller)" >> "$LOG"
      exit 0
      ;;
    *)
      echo "$(date '+%F %T') [poll-fail] poller rc=$POLL_RC — agent skipped, state untouched" >> "$LOG"
      notify "critical" "⚠️ Teams sweep" "Poller fallido (rc $POLL_RC) — agente no ejecutado, ver cron.log"
      exit 0
      ;;
  esac

  export TEAMS_PROMPT="Ejecuta la skill teams-to-tasks en MODO AUTÓNOMO (run programado por cron, usuario ausente):
1. Navega a https://teams.microsoft.com con el MCP playwright_teams. Si aparece el login de Microsoft, ABORTA inmediatamente con el mensaje 'SESION_EXPIRADA: login manual necesario' y no crees nada.
2. Descubrimiento: búsqueda 'que' + filtro Date=Today; considera solo mensajes de HOY posteriores a: ${PREV_LAST_RUN}.
3. Candidatas: peticiones explícitas, deadlines, blockers y follow-ups dirigidos a mí (Bruno). Descarta lo que quedó resuelto en el propio hilo. Si el mensaje lleva imagen relevante, captúrala con un screenshot del elemento, léela con visión nativa y resume 1-2 líneas para Notas.
4. DEDUPE por huella: cada candidata lleva la ID estable 'teams:<chat>:<autor>:<YYYY-MM-DD-HH:MM>'. Consulta la data source 'Tareas' de Notion (Notas contiene la ID) y no crees ninguna cuya ID ya exista.
5. Crea las nuevas directamente como 📥 Inbox, con Proyecto y Prioridad estimados y Notas empezando por la ID de huella seguida de la cita fuente (chat, autor, fecha/hora). No pidas confirmación.
6. CIERRA el navegador con playwright_teams_browser_close.
7. Devuelve un resumen de 1-3 líneas: títulos creados o 'sin novedades'."
fi

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

if run_agent > "$OUT" 2>&1; then
  if [ "$MODE" = "sweep" ]; then
    # Deferred advancement: the agent succeeded, so the watermark moves from
    # PREV_LAST_RUN to this run's poll start (never a blind wall-clock date).
    printf '%s' "$POLL_START_T" > "$STATE_FILE"
  fi
  SUMMARY=$(grep -v '^[[:space:]]*$' "$OUT" | tail -3 | tr '\n' ' ' | cut -c1-200)
  echo "$(date '+%F %T') [ok] $MODE completed: $SUMMARY" >> "$LOG"
  notify "normal" "✅ Teams $MODE" "${SUMMARY:-sin novedades}"
else
  echo "$(date '+%F %T') [fail] opencode run exited non-zero (state not advanced)" >> "$LOG"
  if grep -q "SESION_EXPIRADA" "$OUT"; then
    notify "critical" "⚠️ Teams sweep" "Sesión expirada: login manual necesario (pide el flujo interactivo)"
  else
    notify "critical" "❌ Teams sweep" "Run fallido — ver cron.log"
  fi
fi
