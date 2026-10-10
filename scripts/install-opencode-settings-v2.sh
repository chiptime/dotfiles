#!/usr/bin/env bash
# Instalador v2 de ajustes y modelos de opencode (v1 intacto como rollback).
#
# Capas, de menor a mayor prioridad:
#   1. ~/.config/opencode/opencode.json (config viva, la escribe gentle-ai sync)
#   2. ai/agents/opencode/settings/*.fragment.json (mecanismo del repo, igual que v1)
#   3. settings.local.fragment.json (fragmento local de la máquina, igual que v1)
#   4. ai/agents/opencode/models/models.v2.json (fuente única de modelos Y variantes por agente)
#
# Mapa por agente (dos formas soportadas):
#   - par:     {"model": "provider/id", "variant": "low|medium|high|max"}  (variant
#              ausente => default "")
#   - legacy:  "provider/id"                                             (=> default "")
#
# Garantías: para cada agente mapeado solo sobrescribe `model` y `variant` (nunca
# spread de objetos: prompt/permisos de la config viva se conservan), y la
# variante del par/default SIEMPRE gana a la que pongan los fragmentos; solo en
# agentes que YA existen (nunca crea agentes fantasma); valida cada id contra
# `opencode models` y la forma del mapa, y aborta antes de escribir si algo falla.
# Seguro de repetir.
#
# Uso: install-opencode-settings-v2.sh [--dry-run | --check | --help]
set -euo pipefail

usage() {
  cat <<'EOF'
Uso: install-opencode-settings-v2.sh [--dry-run | --check | --help]

  --dry-run  calcula e informa; no escribe nada
  --check    como --dry-run, pero sale con 1 si habría cambios o hay avisos

Variables de entorno (útiles para pruebas en un sandbox):
  OPENCODE_CONFIG_FILE     config a modificar (def.: ~/.config/opencode/opencode.json)
  OPENCODE_LOCAL_FRAGMENT  fragmento local (def.: settings.local.fragment.json junto a la config)
  OPENCODE_MODELS_FILE     mapa a aplicar (def.: ai/agents/opencode/models/models.v2.json del repo)
  OPENCODE_SETTINGS_DIR    dir de fragmentos del repo (def.: ai/agents/opencode/settings)
  OPENCODE_MODELS_LIST     fichero con un id provider/modelo válido por línea
                           (si falta, se ejecuta `opencode models`)
EOF
}

DRY_RUN=0
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --check) CHECK=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Flag desconocida: $arg" >&2; usage >&2; exit 2 ;;
  esac
done
NOWRITE=$((DRY_RUN + CHECK))

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS_DIR="${OPENCODE_SETTINGS_DIR:-$REPO/ai/agents/opencode/settings}"
MODELS_FILE="${OPENCODE_MODELS_FILE:-$REPO/ai/agents/opencode/models/models.v2.json}"
CFG="${OPENCODE_CONFIG_FILE:-$HOME/.config/opencode/opencode.json}"
LOCAL_FRAGMENT="${OPENCODE_LOCAL_FRAGMENT:-$(dirname "$CFG")/settings.local.fragment.json}"

PROBLEMS=0
warn() { echo "  ! $*"; PROBLEMS=$((PROBLEMS + 1)); }
fatal() { echo "FALLO: $*" >&2; exit 1; }

echo "[1/5] Dependencias y prerrequisitos"
for bin in jq sed find sort cmp comm; do
  command -v "$bin" >/dev/null 2>&1 || fatal "falta $bin"
done
[ -f "$CFG" ] || fatal "no existe $CFG"
[ -f "$MODELS_FILE" ] || fatal "no existe $MODELS_FILE"
jq empty "$CFG" 2>/dev/null || fatal "$CFG no es JSON válido; no se toca nada"
# Puerta de forma del mapa: cada agente debe ser "provider/id" (legacy) o un par
# {"model": "provider/id", "variant"?: string}. Cualquier otra forma aborta
# ANTES de tocar la config.
jq -e '
  (.small_model | type == "string" and test("^[^/]+/.+"))
  and (.agents | type == "object")
  and ([.agents[] | ((type == "string" and test("^[^/]+/.+"))
                     or (type == "object"
                         and (.model | type == "string" and test("^[^/]+/.+"))
                         and ((.variant == null) or (.variant | type == "string"))))] | all)
  and (.inherit | type == "array")
  and ([.inherit[] | type == "string"] | all)
' "$MODELS_FILE" >/dev/null 2>&1 \
  || fatal "$MODELS_FILE tiene una forma inválida (small_model, agents{} con pares {model, variant} o strings provider/modelo, inherit[])"
echo "config: $CFG"
echo "mapa:   ${MODELS_FILE#"$REPO"/}"

WORK="$(mktemp -d "$(dirname "$CFG")/.opencode-settings-v2.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "[2/5] Fusión de capas (fragmentos del repo y local)"
LAYERS="$WORK/layers.json"
echo '{}' > "$LAYERS"
i=0
while IFS= read -r f; do
  i=$((i + 1))
  EXPANDED="$WORK/fragment-$i.json"
  sed "s|\$HOME|$HOME|g" "$f" > "$EXPANDED"
  jq -e 'type == "object"' "$EXPANDED" >/dev/null 2>&1 || fatal "fragmento inválido: $f"
  jq -s '.[0] * .[1]' "$LAYERS" "$EXPANDED" > "$WORK/step.json" && mv "$WORK/step.json" "$LAYERS"
  echo "  - ${f#"$REPO"/}"
done < <(find "$SETTINGS_DIR" -maxdepth 1 -type f -name '*.fragment.json' 2>/dev/null | sort)
if [ -f "$LOCAL_FRAGMENT" ]; then
  jq -e 'type == "object"' "$LOCAL_FRAGMENT" >/dev/null 2>&1 || fatal "fragmento local inválido: $LOCAL_FRAGMENT"
  jq -s '.[0] * .[1]' "$LAYERS" "$LOCAL_FRAGMENT" > "$WORK/step.json" && mv "$WORK/step.json" "$LAYERS"
  echo "  - (local) $(basename "$LOCAL_FRAGMENT")"
fi
MERGED="$WORK/merged.json"
jq -s '.[0] * .[1]' "$CFG" "$LAYERS" > "$MERGED"

echo "[3/5] Validación de ids de modelo"
VALID="$WORK/valid.txt"
: > "$VALID"
if [ -n "${OPENCODE_MODELS_LIST:-}" ]; then
  [ -f "$OPENCODE_MODELS_LIST" ] && sort -u "$OPENCODE_MODELS_LIST" > "$VALID" || true
elif command -v opencode >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  { timeout 90 opencode models 2>/dev/null || true; } | grep -E '^[^ /]+/[^ ]+$' | sort -u > "$VALID" || true
fi
HAVE_LIST=0
[ -s "$VALID" ] && HAVE_LIST=1
if [ "$HAVE_LIST" -eq 0 ]; then
  warn "no se pudo obtener la lista de modelos: validación omitida"
else
  BAD=0
  while IFS=$'\t' read -r who id; do
    if ! grep -qxF -- "$id" "$VALID"; then
      echo "  x $who = $id (no existe en opencode models)" >&2
      BAD=$((BAD + 1))
    fi
  done < <(jq -r '("small_model\t" + .small_model),
                (.agents | to_entries[] | "\(.key)\t\(.value | if type == "string" then . else .model end)")' "$MODELS_FILE")
  [ "$BAD" -eq 0 ] || fatal "$BAD id(s) inválidos en el mapa; se aborta sin escribir. Corrige el mapa"
  echo "  mapa: todos los ids existen ($(wc -l < "$VALID" | tr -d ' ') modelos conocidos)"
fi

echo "[4/5] Aplicando el mapa de pares (solo model+variant, solo agentes existentes)"
jq --slurpfile m "$MODELS_FILE" '
  $m[0] as $m | (.agent // {}) as $ag
  | . * {small_model: $m.small_model,
         agent: ($m.agents | with_entries(select($ag[.key] != null)
           | .value = (if (.value | type) == "object"
                       then {model: .value.model, variant: (.value.variant // "")}
                       else {model: .value, variant: ""} end)))}
' "$MERGED" > "$WORK/patched.json" && mv "$WORK/patched.json" "$MERGED"

while IFS= read -r line; do
  [ -n "$line" ] && echo "  = capa local/repo define un valor distinto, gana el mapa: $line"
done < <(jq -r --slurpfile m "$MODELS_FILE" '
  def mm($k): $m[0].agents[$k] | if type == "string" then . else .model end;
  def mv($k): if ($m[0].agents[$k] | type) == "object" then ($m[0].agents[$k].variant // "") else "" end;
  (if .small_model != null and .small_model != $m[0].small_model
   then "small_model: \(.small_model) -> \($m[0].small_model)" else empty end),
  ((.agent // {}) | to_entries[]
   | select(.value | type == "object")
   | select($m[0].agents[.key] != null)
   | (if .value.model != null and .value.model != mm(.key)
      then "agent.\(.key).model: \(.value.model) -> \(mm(.key))" else empty end),
     (if .value.variant != null and .value.variant != mv(.key)
      then "agent.\(.key).variant: \(.value.variant) -> \(if mv(.key) == "" then "(default)" else mv(.key) end)" else empty end))
' "$LAYERS")

CHANGES="$(jq -r -n --slurpfile a "$CFG" --slurpfile b "$MERGED" '
  def disp($v): if $v == null or $v == "" then "(default)" else $v end;
  ($a[0].agent // {}) as $A | ($b[0].agent // {}) as $B
  | ($B | to_entries[] | select(.value | type == "object") | . as $e
     | ($A[$e.key].model // null) as $old
     | select($e.value.model != null and $e.value.model != $old)
     | (if $old == null then "+" else "~" end)
       + " agent.\($e.key).model: \($old // "(ninguno)") -> \($e.value.model)"),
    ($B | to_entries[] | select(.value | type == "object") | . as $e
     | ($A[$e.key].variant // null) as $oldv
     | select($e.value.variant != null and $e.value.variant != $oldv)
     | (if $oldv == null then "+" else "~" end)
       + " agent.\($e.key).variant: \(if $oldv == null then "(ninguna)" else disp($oldv) end) -> \(disp($e.value.variant))"),
    (if ($a[0].small_model // null) != ($b[0].small_model // null)
     then "~ small_model: \($a[0].small_model // "(ninguno)") -> \($b[0].small_model)" else empty end)
')"
if [ -n "$CHANGES" ]; then
  echo "$CHANGES" | sed 's/^/  /'
else
  echo "  = todos los modelos y variantes ya estaban correctos"
fi

while IFS= read -r a; do
  [ -n "$a" ] && warn "huérfano: '$a' está en el mapa pero no existe en la config (se omite)"
done < <(jq -r '(.agent // {}) as $ag | input | .agents | keys[] | select($ag[.] == null)' "$MERGED" "$MODELS_FILE")
while IFS= read -r a; do
  [ -n "$a" ] && warn "sin fijar: '$a' no está en el mapa ni en inherit (conserva el modelo que le dé gentle-ai)"
done < <(jq -r --slurpfile m "$MODELS_FILE" '
  $m[0] as $m | (.agent // {}) | keys[] | . as $k
  | select($m.agents[$k] == null and ([$m.inherit[] | select(. == $k)] | length) == 0)
' "$MERGED")
if [ "$HAVE_LIST" -eq 1 ]; then
  while IFS=$'\t' read -r path id; do
    [ -n "$path" ] || continue
    grep -qxF -- "$id" "$VALID" || warn "id inválido fuera del mapa: $path = $id"
  done < <(jq -r 'paths(type == "string") as $p
    | select($p | last | tostring | test("^(model|small_model)$"))
    | "\($p | map(tostring) | join("."))\t\(getpath($p))"' "$MERGED")
fi

echo "[5/5] Comparación y escritura"
jq -S . "$CFG" > "$WORK/before.canon"
jq -S . "$MERGED" > "$WORK/after.canon"
if cmp -s "$WORK/before.canon" "$WORK/after.canon"; then
  echo "Sin cambios: la config ya coincide con las capas y el mapa"
  [ "$CHECK" -eq 1 ] && [ "$PROBLEMS" -gt 0 ] && exit 1
  exit 0
fi
if [ "$NOWRITE" -eq 1 ]; then
  echo "dry-run: no se escribió nada (la config cambiaría)"
  [ "$CHECK" -eq 1 ] && exit 1
  exit 0
fi
BACKUP="$CFG.bak-settings-v2-$(date +%Y%m%d-%H%M%S)"
cp -p "$CFG" "$BACKUP"
echo "backup: $BACKUP"
chmod --reference="$CFG" "$MERGED" 2>/dev/null || chmod 644 "$MERGED"
mv "$MERGED" "$CFG"
jq empty "$CFG" 2>/dev/null || fatal "$CFG quedó inválido; restaura desde $BACKUP"
echo "aplicado a $CFG"
echo "Listo. Reinicia opencode. Tras cada 'gentle-ai sync', vuelve a ejecutar este script."
