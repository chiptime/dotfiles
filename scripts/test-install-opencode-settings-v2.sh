#!/usr/bin/env bash
# Pruebas funcionales de install-opencode-settings-v2.sh en un sandbox hermético.
# Mapa, ajustes (fragmentos del repo), config, catálogo y fragmento local: TODO
# sintético. Nunca toca ~/.config, nunca escribe en el repo ni llama a opencode.
#
# Forma del mapa soportada (por agente):
#   - par:     {"model": "provider/id", "variant": "low|medium|high|max|''"}
#              (variant ausente => default "")
#   - legacy:  "provider/id"           (=> model + variante default "")
# El overlay del mapa solo aplica model+variant: prompt/permisos de la config
# viva nunca se sobrescriben, y la variante del par/default SIEMPRE gana a la
# que pongan los fragmentos para un agente mapeado.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INST="$REPO/scripts/install-opencode-settings-v2.sh"
SBX="$(mktemp -d "${TMPDIR:-/tmp}/ocinst-v2-test.XXXXXX")"
trap 'rm -rf "$SBX"' EXIT
CFG="$SBX/opencode.json"
SETTINGS_SB="$SBX/settings dir" # espacio en el path a propósito
MAP_SB="$SBX/map"
mkdir -p "$SETTINGS_SB" "$MAP_SB"
PASS=0
FAIL=0

check() { # check "descripción" <comando...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); echo "  ok   $desc"
  else FAIL=$((FAIL + 1)); echo "  FAIL $desc"; fi
}
has() { grep -qF -- "$1" <<<"$OUT"; }
cfg() { jq -r "$1" "$CFG"; }
hash_cfg() { sha256sum "$CFG" | cut -d' ' -f1; }
backups() { find "$SBX" -maxdepth 1 -name 'opencode.json.bak-settings-v2-*' | wc -l | tr -d ' '; }

# Fixture de mapa: pares con/sin variante + entrada legacy.
fresh_map() {
  rm -f "$SETTINGS_SB"/*.fragment.json
  cat > "$MAP_SB/models.json" <<'EOF'
{
  "small_model": "fake/small",
  "agents": {
    "worker": {"model": "fake/model-a", "variant": "high"},
    "helper": {"model": "fake/model-b"},
    "legacy": "fake/model-a",
    "resetter": {"model": "fake/model-c", "variant": "max"}
  },
  "inherit": ["inherited"]
}
EOF
}

map_set() { # jq-filter  (muta el mapa del sandbox)
  jq "$1" "$MAP_SB/models.json" > "$MAP_SB/t.json" && mv "$MAP_SB/t.json" "$MAP_SB/models.json"
}

# Fixture de config: modelos viejos, variante previa "max" (estado tras aplicar
# un perfil A), prompt y permisos que NO deben tocarse.
fresh() {
  rm -f "$SBX"/opencode.json* "$SBX/none.json" "$SBX"/local.json
  jq -n '{small_model: "zz/stale",
       agent: {
         worker:    {prompt: "keep-me", permission: {task: "deny"}, model: "zz/stale", variant: "max"},
         helper:    {prompt: "keep-me", permission: {task: "deny"}, model: "zz/stale", variant: "max"},
         legacy:    {prompt: "keep-me", permission: {task: "deny"}, model: "zz/stale", variant: "max"},
         resetter:  {prompt: "keep-me", permission: {task: "deny"}, model: "zz/stale"},
         inherited: {prompt: "keep-me"}
       }}' > "$CFG"
}

printf 'fake/model-a\nfake/model-b\nfake/model-c\nfake/small\n' | sort -u > "$SBX/valid.txt"

run() {
  OUT="$(OPENCODE_CONFIG_FILE="$CFG" \
         OPENCODE_LOCAL_FRAGMENT="${LOCAL:-$SBX/none.json}" \
         OPENCODE_MODELS_LIST="${LIST:-$SBX/valid.txt}" \
         OPENCODE_MODELS_FILE="$MAP_SB/models.json" \
         OPENCODE_SETTINGS_DIR="$SETTINGS_SB" \
         "$INST" "$@" 2>&1)"
  RC=$?
}

echo "1. aplica pares {model, variant} sin tocar prompts ni permisos"
fresh_map; fresh; run
check "sale con 0" test "$RC" -eq 0
check "worker.model = mapa" test "$(cfg '.agent.worker.model')" = "fake/model-a"
check "worker.variant = mapa (cambia)" test "$(cfg '.agent.worker.variant')" = "high"
check "helper sin variant en el mapa -> default explícito (limpia el max previo)" test "$(cfg '.agent.helper.variant')" = ""
check "legacy string -> variante default (limpia el max previo)" test "$(cfg '.agent.legacy.variant')" = ""
check "legacy string -> modelo aplicado" test "$(cfg '.agent.legacy.model')" = "fake/model-a"
check "resetter.variant = mapa" test "$(cfg '.agent.resetter.variant')" = "max"
check "prompts intactos" test "$(cfg '[.agent[].prompt] | unique | join(",")')" = "keep-me"
check "permisos intactos" test "$(cfg '.agent.worker.permission.task')" = "deny"
check "inherit sin modelo ni variante" test "$(cfg '.agent.inherited.model') $(cfg '.agent.inherited.variant')" = "null null"
check "small_model = mapa" test "$(cfg '.small_model')" = "fake/small"
check "informa cambio de modelo" has "~ agent.worker.model: zz/stale -> fake/model-a"
check "informa cambio de variante" has "~ agent.worker.variant: max -> high"
check "informa variante a default" has "~ agent.helper.variant: max -> (default)"
check "hay backup v2" test "$(backups)" -eq 1

echo "2. idempotente"
run
check "sin cambios" has "Sin cambios"
check "no crea otro backup" test "$(backups)" -eq 1

echo "3. perfiles independientes: cambiar de mapa limpia/aplica SU variante"
map_set '.agents.worker = {model: "fake/model-b", variant: "low"}'
run
check "perfil B aplica SU variante (A high -> B low)" test "$(cfg '.agent.worker.variant')" = "low"
map_set '.agents.worker = "fake/model-b"'
run
check "perfil legacy (string) limpia la variante heredada de A" test "$(cfg '.agent.worker.variant')" = ""
map_set '.agents.worker = {model: "fake/model-b", "variant": ""}'
run
check "perfil con variante vacía explícita también limpia" test "$(cfg '.agent.worker.variant')" = ""

echo "4. fragmento de ajustes: el par mapeado manda en model+variant; el resto se conserva"
fresh_map; fresh
echo '{"agent": {"helper": {"prompt": "fragment-prompt", "variant": "high"}, "unmapped": {"variant": "low"}}}' > "$SETTINGS_SB/test.fragment.json"
jq '.agent.unmapped = {prompt: "keep-me"}' "$CFG" > "$CFG.t" && mv "$CFG.t" "$CFG"
run
check "variante del fragmento pierde contra el par/default del mapa" test "$(cfg '.agent.helper.variant')" = ""
check "prompt del fragmento se conserva (capa sobre config)" test "$(cfg '.agent.helper.prompt')" = "fragment-prompt"
check "permiso de la config viva se conserva" test "$(cfg '.agent.helper.permission.task')" = "deny"
check "agente NO mapeado conserva la variante del fragmento" test "$(cfg '.agent.unmapped.variant')" = "low"
check "avisa que el mapa gana la variante del fragmento" has "agent.helper.variant: high -> (default)"

echo "5. mapa legacy completo (solo strings)"
fresh_map
map_set '.agents = (.agents | to_entries | map({(.key): (if (.value | type) == "string" then .value else .value.model end)}) | add)'
fresh; run
check "legacy total: worker.variant default" test "$(cfg '.agent.worker.variant')" = ""
check "legacy total: resetter.variant default" test "$(cfg '.agent.resetter.variant')" = ""
check "legacy total: model sigue aplicando" test "$(cfg '.agent.worker.model')" = "fake/model-a"

echo "6. mapa inválido aborta antes de escribir"
malformed() { # desc jq-filter
  fresh_map; map_set "$2"; fresh; local before; before="$(hash_cfg)"
  run
  check "$1: sale con 1" test "$RC" -eq 1
  check "$1: no escribe" test "$(hash_cfg)" = "$before"
  check "$1: no crea backup" test "$(backups)" -eq 0
}
malformed "variante numérica" '.agents.worker.variant = 3'
malformed "variante booleana" '.agents.helper.variant = true'
malformed "model sin proveedor" '.agents.worker.model = "noSlash"'
malformed "model numérico" '.agents.worker.model = 7'
malformed "entrada como array" '.agents.worker = ["fake/model-a"]'
malformed "agents como array" '.agents = []'
malformed "small_model inválido" '.small_model = "nope"'

fresh_map; map_set '.agents.worker.model = "fake/model-z"'; fresh; before="$(hash_cfg)"
run
check "id inválido en el mapa: sale con 1" test "$RC" -eq 1
check "id inválido en el mapa: no escribe" test "$(hash_cfg)" = "$before"
check "id inválido en el mapa: no crea backup" test "$(backups)" -eq 0
check "informa el id inválido" has "no existe en opencode models"

echo "7. huérfano: agente del mapa ausente en la config"
fresh_map; fresh; jq 'del(.agent.worker)' "$CFG" > "$CFG.t" && mv "$CFG.t" "$CFG"; run
check "avisa huérfano" has "huérfano: 'worker'"
check "no crea agente fantasma" test "$(cfg '.agent | has("worker")')" = "false"

echo "8. agente nuevo sin fijar"
fresh_map; fresh; jq '.agent["new-thing"] = {prompt: "x"}' "$CFG" > "$CFG.t" && mv "$CFG.t" "$CFG"; run
check "avisa sin fijar" has "sin fijar: 'new-thing'"
check "no le asigna modelo" test "$(cfg '.agent["new-thing"].model')" = "null"
check "no le asigna variante" test "$(cfg '.agent["new-thing"].variant')" = "null"

echo "9. id inválido fuera del mapa"
fresh_map; fresh; jq '.agent["new-thing"] = {prompt: "x", model: "zz/bad"}' "$CFG" > "$CFG.t" && mv "$CFG.t" "$CFG"; run
check "avisa id inválido" has "id inválido fuera del mapa: agent.new-thing.model = zz/bad"
check "no es fatal" test "$RC" -eq 0

echo "10. --dry-run y --check"
fresh_map; fresh; before="$(hash_cfg)"; run --dry-run
check "dry-run sale con 0" test "$RC" -eq 0
check "dry-run no escribe" test "$(hash_cfg)" = "$before"
run --check
check "check detecta deriva (1)" test "$RC" -eq 1
fresh_map; fresh; run >/dev/null; run --check
check "check limpio sale con 0" test "$RC" -eq 0

echo "11. sin lista de modelos: validación omitida, no bloquea"
fresh_map; fresh; LIST="$SBX/no-existe.txt" run
check "avisa validación omitida" has "validación omitida"
check "aplica igualmente" test "$(cfg '.agent.worker.model')" = "fake/model-a"
check "aplica variante igualmente" test "$(cfg '.agent.worker.variant')" = "high"

echo "12. fragmento local con otros valores: gana el mapa"
fresh_map; fresh
echo '{"agent":{"worker":{"model":"zz/other","variant":"low"}}}' > "$SBX/local.json"
LOCAL="$SBX/local.json" run
check "informa 'gana el mapa'" has "gana el mapa"
check "model final = mapa" test "$(cfg '.agent.worker.model')" = "fake/model-a"
check "variant final = mapa" test "$(cfg '.agent.worker.variant')" = "high"

echo
echo "resultado: $PASS ok, $FAIL fallos"
[ "$FAIL" -eq 0 ]
