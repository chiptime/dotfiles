#!/usr/bin/env bash
# Pruebas funcionales de install-opencode-settings-v2.sh en un sandbox.
# Usa una config sintética derivada del mapa: nunca toca ~/.config ni llama a opencode.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INST="$REPO/scripts/install-opencode-settings-v2.sh"
MAP="$REPO/ai/agents/opencode/models/models.v2.json"
SBX="$(mktemp -d)"
trap 'rm -rf "$SBX"' EXIT
CFG="$SBX/opencode.json"
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

# Fixture: todos los agentes del mapa con un modelo viejo y un prompt que NO debe tocarse.
fresh() {
  rm -f "$SBX"/opencode.json* "$SBX/none.json"
  jq '{small_model: "zz/stale",
       agent: ((.agents | with_entries(.value = {prompt: "keep-me", model: "zz/stale"}))
               + (.inherit | map({(.): {prompt: "keep-me"}}) | add // {}))}' "$MAP" > "$CFG"
  jq -r '.small_model, (.agents[])' "$MAP" | sort -u > "$SBX/valid.txt"
}
run() {
  OUT="$(OPENCODE_CONFIG_FILE="$CFG" OPENCODE_LOCAL_FRAGMENT="${LOCAL:-$SBX/none.json}" \
    OPENCODE_MODELS_LIST="${LIST:-$SBX/valid.txt}" "$INST" "$@" 2>&1)"
  RC=$?
}
MODEL_OF() { jq -r --arg a "$1" '.agents[$a]' "$MAP"; }

echo "1. aplica el mapa sin tocar nada más"
fresh; run
check "sale con 0" test "$RC" -eq 0
check "informa el cambio" has "~ agent.branch-pr.model: zz/stale ->"
check "branch-pr = mapa" test "$(cfg '.agent["branch-pr"].model')" = "$(MODEL_OF branch-pr)"
check "small_model = mapa" test "$(cfg '.small_model')" = "$(jq -r .small_model "$MAP")"
check "prompts intactos" test "$(cfg '[.agent[].prompt] | unique | join(",")')" = "keep-me"
check "inherit sin modelo" test "$(cfg '.agent["review-validator"].model')" = "null"
check "hay backup v2" test "$(backups)" -eq 1

echo "2. idempotente"
run
check "sin cambios" has "Sin cambios"
check "no crea otro backup" test "$(backups)" -eq 1

echo "3. huérfano: agente del mapa ausente en la config"
fresh; jq 'del(.agent["branch-pr"])' "$CFG" > "$CFG.t" && mv "$CFG.t" "$CFG"; run
check "avisa huérfano" has "huérfano: 'branch-pr'"
check "no crea agente fantasma" test "$(cfg '.agent | has("branch-pr")')" = "false"

echo "4. agente nuevo sin fijar"
fresh; jq '.agent["new-thing"] = {prompt: "x"}' "$CFG" > "$CFG.t" && mv "$CFG.t" "$CFG"; run
check "avisa sin fijar" has "sin fijar: 'new-thing'"
check "no le asigna modelo" test "$(cfg '.agent["new-thing"].model')" = "null"

echo "5. id inválido fuera del mapa"
fresh; jq '.agent["new-thing"] = {prompt: "x", model: "zz/bad"}' "$CFG" > "$CFG.t" && mv "$CFG.t" "$CFG"; run
check "avisa id inválido" has "id inválido fuera del mapa: agent.new-thing.model = zz/bad"
check "no es fatal" test "$RC" -eq 0

echo "6. id inválido en el mapa aborta sin escribir"
fresh; grep -vxF "$(MODEL_OF branch-pr)" "$SBX/valid.txt" > "$SBX/valid2.txt"; before="$(hash_cfg)"
LIST="$SBX/valid2.txt" run
check "sale con 1" test "$RC" -eq 1
check "no escribe" test "$(hash_cfg)" = "$before"
check "no crea backup" test "$(backups)" -eq 0

echo "7. --dry-run y --check"
fresh; before="$(hash_cfg)"; run --dry-run
check "dry-run sale con 0" test "$RC" -eq 0
check "dry-run no escribe" test "$(hash_cfg)" = "$before"
run --check
check "check detecta deriva (1)" test "$RC" -eq 1
fresh; run >/dev/null; run --check
check "check limpio sale con 0" test "$RC" -eq 0

echo "8. sin lista de modelos: valida omitida, no bloquea"
fresh; LIST="$SBX/no-existe.txt" run
check "avisa validación omitida" has "validación omitida"
check "aplica igualmente" test "$(cfg '.agent["branch-pr"].model')" = "$(MODEL_OF branch-pr)"

echo "9. fragmento local con otro modelo: gana el mapa"
fresh; echo '{"agent":{"branch-pr":{"model":"zz/other"}}}' > "$SBX/local.json"; LOCAL="$SBX/local.json" run
check "informa 'gana el mapa'" has "gana el mapa"
check "valor final = mapa" test "$(cfg '.agent["branch-pr"].model')" = "$(MODEL_OF branch-pr)"

echo
echo "resultado: $PASS ok, $FAIL fallos"
[ "$FAIL" -eq 0 ]
