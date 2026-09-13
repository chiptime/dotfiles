#!/usr/bin/env bash
# Instalador idempotente de ajustes top-level de opencode.
# Fusiona todos los fragmentos de ai/agents/opencode/settings/ dentro de
# ~/.config/opencode/opencode.json. El repo siempre gana sobre la config
# viva; el fragmento LOCAL de la máquina (settings.local.fragment.json,
# fuera del repo: valores personales como modelos concretos) gana sobre el
# repo. Así el repo transporta MECANISMO, nunca valores de un proveedor que
# otros usuarios no tienen.
# Safe to re-run. No ejecuta dotbot ni gentle-ai ni opencode.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS_DIR="$REPO/ai/agents/opencode/settings"
LOCAL_FRAGMENT="$HOME/.config/opencode/settings.local.fragment.json"
CFG="$HOME/.config/opencode/opencode.json"

echo "[1/5] Dependencias y prerrequisitos"
for bin in jq sed find sort cmp; do
  command -v "$bin" >/dev/null 2>&1 || { echo "FALTA: $bin"; exit 1; }
done
[ -f "$CFG" ] || { echo "FALTA: $CFG (no existe la configuración de opencode)"; exit 1; }
[ -d "$SETTINGS_DIR" ] || { echo "FALTA: $SETTINGS_DIR (directorio de fragmentos)"; exit 1; }
jq empty "$CFG" 2>/dev/null || { echo "FALLO: $CFG no es JSON válido; no se toca nada"; exit 1; }
echo "config: $CFG"

echo "[2/5] Fragmentos disponibles"
FRAGMENTS=()
while IFS= read -r f; do
  FRAGMENTS+=("$f")
done < <(find "$SETTINGS_DIR" -maxdepth 1 -type f -name '*.fragment.json' | sort)

if [ "${#FRAGMENTS[@]}" -eq 0 ]; then
  echo "sin fragmentos en $SETTINGS_DIR; nada que aplicar"
  exit 0
fi
for f in "${FRAGMENTS[@]}"; do
  echo "  - ${f#"$REPO"/}"
done
if [ -f "$LOCAL_FRAGMENT" ]; then
  echo "  - (local) settings.local.fragment.json — máquina; gana sobre el repo"
fi

# Directorio temporal junto al destino: el mv final es atómico (mismo sistema de ficheros).
WORK="$(mktemp -d "$(dirname "$CFG")/.opencode-settings.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "[3/5] Fusión (repo prioritario sobre config viva; local prioritario sobre repo)"
MERGED="$WORK/merged.json"
cp "$CFG" "$MERGED"
i=0
for f in "${FRAGMENTS[@]}"; do
  i=$((i + 1))
  EXPANDED="$WORK/fragment-$i.json"
  sed "s|\$HOME|$HOME|g" "$f" > "$EXPANDED"
  jq empty "$EXPANDED" 2>/dev/null || { echo "FALLO: fragmento inválido: $f"; exit 1; }
  jq -e 'type == "object"' "$EXPANDED" >/dev/null 2>&1 \
    || { echo "FALLO: el fragmento debe ser un objeto JSON: $f"; exit 1; }
  jq -s '.[0] * .[1]' "$MERGED" "$EXPANDED" > "$WORK/step.json"
  mv "$WORK/step.json" "$MERGED"
done
if [ -f "$LOCAL_FRAGMENT" ]; then
  jq empty "$LOCAL_FRAGMENT" 2>/dev/null || { echo "FALLO: fragmento local inválido: $LOCAL_FRAGMENT"; exit 1; }
  jq -e 'type == "object"' "$LOCAL_FRAGMENT" >/dev/null 2>&1 \
    || { echo "FALLO: el fragmento local debe ser un objeto JSON"; exit 1; }
  cp "$LOCAL_FRAGMENT" "$WORK/fragment-local.json"
  jq -s '.[0] * .[1]' "$MERGED" "$WORK/fragment-local.json" > "$WORK/step.json"
  mv "$WORK/step.json" "$MERGED"
fi

jq empty "$MERGED" 2>/dev/null || { echo "FALLO: el resultado fusionado no es JSON válido; se aborta sin tocar $CFG"; exit 1; }

echo "[4/5] Diferencias por clave"
# Claves top-level cubiertas por los fragmentos, en orden estable.
KEYS=()
while IFS= read -r k; do
  KEYS+=("$k")
done < <(jq -s -r 'reduce .[] as $f ({}; . * $f) | keys_unsorted[]' "$WORK"/fragment-*.json | awk '!seen[$0]++')

for k in "${KEYS[@]}"; do
  AFTER="$(jq -c --arg k "$k" '.[$k]' "$MERGED")"
  if jq -e --arg k "$k" 'has($k)' "$CFG" >/dev/null 2>&1; then
    BEFORE="$(jq -c --arg k "$k" '.[$k]' "$CFG")"
    if [ "$BEFORE" = "$AFTER" ]; then
      echo "  = $k: ya correcto ($AFTER)"
    else
      echo "  ~ $k: cambiado ($BEFORE -> $AFTER)"
    fi
  else
    echo "  + $k: añadido ($AFTER)"
  fi
done

# Comparación canónica completa: detecta cualquier divergencia real, no solo la de las claves listadas.
jq -S . "$CFG"     > "$WORK/before.canon"
jq -S . "$MERGED"  > "$WORK/after.canon"
if cmp -s "$WORK/before.canon" "$WORK/after.canon"; then
  echo "[5/5] Sin cambios: la configuración ya coincide con los fragmentos del repo"
  exit 0
fi

echo "[5/5] Aplicando cambios"
# Prefijo propio: no debe colisionar con backups creados por otras herramientas.
if compgen -G "$CFG.bak-settings-*" >/dev/null; then
  echo "backup de este instalador ya existente; se conserva el original"
else
  BACKUP="$CFG.bak-settings-$(date +%Y%m%d-%H%M%S)"
  cp -p "$CFG" "$BACKUP"
  echo "backup del original: $BACKUP"
fi

chmod --reference="$CFG" "$MERGED" 2>/dev/null || chmod 644 "$MERGED"
mv "$MERGED" "$CFG"
jq empty "$CFG" 2>/dev/null || { echo "FALLO CRÍTICO: $CFG quedó inválido; restaura desde el backup"; exit 1; }
echo "aplicado a $CFG"
echo "Listo. Reinicia opencode para que los ajustes surtan efecto."
