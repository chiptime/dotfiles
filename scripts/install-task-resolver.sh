#!/usr/bin/env bash
# Instalador idempotente del task-resolver (detector HITL): skill symlink + estado + crontab.
# Safe to re-run. Separado a propósito de install-teams-to-tasks.sh: subsistemas independientes.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENCODE_SKILLS="$HOME/.config/opencode/skills"
STATE_DIR="$HOME/.local/state/task-resolver"
DETECTOR="$REPO/ai/teams-to-tasks/src/resolver/detector.ts"
PRIV_ENV="$REPO/shell/private-env.sh"

echo "[1/4] Dependencias"
for bin in crontab bun; do
  command -v "$bin" >/dev/null 2>&1 || { echo "FALTA: $bin"; exit 1; }
done
[ -f "$DETECTOR" ] || { echo "FALTA: $DETECTOR"; exit 1; }
[ -r "$PRIV_ENV" ] || { echo "FALTA: $PRIV_ENV (define NOTION_CLECE fuera del repo)"; exit 1; }

echo "[2/4] Skill symlink"
ln -sfn "$REPO/ai/agents/opencode/skills/task-resolver" "$OPENCODE_SKILLS/task-resolver"

echo "[3/4] Directorio de estado local (datos, nunca en el repo)"
mkdir -p "$STATE_DIR"

echo "[4/4] Crontab (laborable, cada hora 8-18; minuto 10 para no chocar con teams-to-tasks)"
BUN="$(command -v bun)"
# cron usa un PATH mínimo: rutas absolutas y source del entorno de secretos que vive fuera del repo.
# El detector es auto-contenido por invocación: exit 0 news / 1 no-news / 2 fallo (fail-closed, seen.json intacto).
LINE="10 8-18 * * 1-5 bash -c '. $PRIV_ENV; cd $REPO/ai/teams-to-tasks && exec $BUN src/resolver/detector.ts' >> $STATE_DIR/cron.log 2>&1"
if crontab -l 2>/dev/null | grep -Fq "src/resolver/detector.ts"; then
  echo "ya instalado"
else
  (crontab -l 2>/dev/null; echo "$LINE") | crontab -
  echo "añadido: $LINE"
fi

echo "Listo. El detector alimenta $STATE_DIR/pending-queue.json; la sesión atendida (skill task-resolver) consume la cola."
