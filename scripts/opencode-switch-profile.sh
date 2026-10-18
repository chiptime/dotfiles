#!/usr/bin/env bash
# Selector de perfiles de modelos para OpenCode
# Uso: opencode-switch-profile.sh [--fallback | -f] [--no-apply | -n] [--apply | -y] [default | zai | claude | openai | opencode-go | status | edit | apply | menu]
#
# Cada perfil (models.*.json) guarda por agente un par {"model", "variant"}
# propio: el mismo modelo puede usar variantes distintas por agente y por
# perfil, y ningún perfil filtra su variante a otro. Las entradas legacy
# ("provider/model" como string) equivalen a par con variante default ("").

set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"

# Narrow overrides for the sandboxed regression test (test-opencode-switch-edit-variant.sh).
# Unset in normal use: production defaults point inside this repo.
MODELS_DIR="${OPENCODE_SWITCH_MODELS_DIR:-$REPO/ai/agents/opencode/models}"
ACTIVE_MAP="$MODELS_DIR/models.v2.json"
INSTALLER="${OPENCODE_SWITCH_INSTALLER:-$REPO/scripts/install-opencode-settings-v2.sh}"
VARIANT_CHOICES="low medium high max default keep"

show_status() {
  local cfg="$HOME/.config/opencode/opencode.json"
  echo -e "\033[1;37m=== Estado Completo de Modelos en OpenCode ===\033[0m"
  if [ -f "$cfg" ]; then
    node -e '
      const c = require("'"$cfg"'");
      const a = c.agent || {};

      // 2 colores alternados para máxima legibilidad según prefijo de nombre
      const C_CYAN = "\x1b[36m";
      const C_BLUE = "\x1b[34m";
      const C_DIM = "\x1b[2m";
      const C_RESET = "\x1b[0m";
      const C_BOLD = "\x1b[1m";

      if (c.small_model) {
        console.log(`\n${C_BOLD}-- Modelo Ligero / Tareas Globales --${C_RESET}`);
        console.log(`  ${C_CYAN}${"small_model".padEnd(28)}${C_RESET}: ${c.small_model.padEnd(36)} ${C_DIM}[global: modelo ligero/resumen]${C_RESET}`);
      }

      const sections = {
        "1. Orquestación y Flujo ODD": [
          "gentle-orchestrator",
          "gentle-ai-worker",
          "general",
          "gentle-ai-worker-fallback",
          "gentle-ai-verify",
          "gentle-ai-explore",
          "explore"
        ],
        "2. Anillo de Revisión 4R (Code Review & Auditoría)": [
          "review-risk",
          "review-risk-fallback",
          "review-reliability",
          "review-reliability-fallback",
          "review-resilience",
          "review-resilience-fallback",
          "review-readability",
          "review-readability-fallback",
          "review-refuter",
          "review-refuter-fallback",
          "review-validator"
        ],
        "3. Judgment Day & Fixers Adversarios": [
          "jd-judge-a",
          "jd-judge-a-fallback",
          "jd-judge-b",
          "jd-judge-b-fallback",
          "jd-fix-agent",
          "jd-fix-agent-fallback",
          "gentleman-judge",
          "gentleman-judge-fallback",
          "gentleman-fix",
          "gentleman-fix-fallback",
          "judgment-day"
        ],
        "4. Tareas Auxiliares & Automatizaciones": [
          "branch-pr",
          "branch-pr-fallback",
          "issue-creation",
          "issue-creation-fallback",
          "compaction",
          "compaction-fallback",
          "summary",
          "summary-fallback"
        ]
      };

      for (const [title, agents] of Object.entries(sections)) {
        console.log(`\n${C_BOLD}-- ${title} --${C_RESET}`);
        let lastPrefix = "";
        let colorIndex = 0;
        const palette = [C_CYAN, C_BLUE];

        for (const k of agents) {
          if (a[k]) {
            const prefix = k.split("-")[0];
            if (prefix !== lastPrefix) {
              colorIndex = (colorIndex + 1) % palette.length;
              lastPrefix = prefix;
            }
            const color = palette[colorIndex];
            const nameStr = `${color}${k.padEnd(28)}${C_RESET}`;
            const modelStr = (a[k].model || "(hereda/ninguno)").padEnd(36);
            const variantStr = a[k].variant ? `[variant: ${a[k].variant}]` : "[variant: default]";
            console.log(`  ${nameStr}: ${modelStr} ${C_DIM}${variantStr}${C_RESET}`);
          }
        }
      }
    '
  else
    echo "No existe $cfg"
  fi
}

# --- JSON helpers: every value travels as argv, never interpolated into JS ---

profile_agent_list() { # file -> "name -> model  [variante: X]" per agent
  node -e '
    const fs = require("fs");
    const d = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    if (typeof d.small_model === "string") {
      console.log(`${"small_model".padEnd(30)} -> ${d.small_model}  [global: modelo ligero/resumen]`);
    }
    for (const [k, v] of Object.entries(d.agents || {})) {
      let desc;
      if (typeof v === "string") {
        desc = `${v}  [variante: default; entrada legacy]`;
      } else if (v != null && typeof v === "object") {
        const variant = typeof v.variant === "string" && v.variant !== "" ? v.variant : "default";
        desc = `${v.model}  [variante: ${variant}]`;
      } else {
        desc = `(entrada inválida: ${String(v)})`;
      }
      console.log(`${k.padEnd(30)} -> ${desc}`);
    }
  ' "$1"
}

profile_has_agent() { # file agent
  node -e '
    const fs = require("fs");
    const d = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    if (process.argv[2] === "small_model") {
      process.exit(typeof d.small_model === "string" ? 0 : 1);
    }
    // Own-property check: `in` would also match inherited keys (constructor,
    // toString, ...) that are not real agents in the profile map.
    const has = d.agents != null && typeof d.agents === "object" &&
      Object.hasOwn(d.agents, process.argv[2]);
    process.exit(has ? 0 : 1);
  ' "$1" "$2"
}

profile_entry_model() { # file agent -> current model of the pair (legacy string = its model)
  node -e '
    const fs = require("fs");
    const d = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    if (process.argv[2] === "small_model") {
      process.stdout.write(typeof d.small_model === "string" ? d.small_model : "");
      process.exit(0);
    }
    const e = (d.agents || {})[process.argv[2]];
    let m = "";
    if (typeof e === "string") m = e;
    else if (e != null && typeof e === "object" && typeof e.model === "string") m = e.model;
    process.stdout.write(m);
  ' "$1" "$2"
}

profile_entry_variant() { # file agent -> current variant ("" for legacy or unset)
  node -e '
    const fs = require("fs");
    const d = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    if (process.argv[2] === "small_model") {
      process.exit(0);
    }
    const e = (d.agents || {})[process.argv[2]];
    let v = "";
    if (e != null && typeof e === "object" && typeof e.variant === "string") v = e.variant;
    process.stdout.write(v);
  ' "$1" "$2"
}

write_small_model() { # file model — writes ONLY small_model
  node -e '
    const fs = require("fs");
    const [path, model] = process.argv.slice(1);
    const data = JSON.parse(fs.readFileSync(path, "utf8"));
    data.small_model = model;
    fs.writeFileSync(path, JSON.stringify(data, null, 2) + "\n", "utf8");
    console.log(`\n✔ small_model guardado en el perfil: ${model}`);
  ' "$1" "$2"
}

write_agent_pair() { # file agent model variant — writes ONLY {model, variant}
  node -e '
    const fs = require("fs");
    const [path, agent, model, variant] = process.argv.slice(1);
    const data = JSON.parse(fs.readFileSync(path, "utf8"));
    data.agents = data.agents || {};
    // Solo el par model+variant: ningún metadato del perfil viaja con la
    // entrada, y el resto de agentes/claves (small_model, inherit, ...)
    // se conservan tal cual.
    data.agents[agent] = { model: model, variant: variant };
    fs.writeFileSync(path, JSON.stringify(data, null, 2) + "\n", "utf8");
    console.log(`\n✔ Par guardado en el perfil: ${agent} -> ${model} (variante: ${variant === "" ? "default" : variant})`);
  ' "$1" "$2" "$3" "$4"
}

edit_subagent_in_profile() {
  local target_profile="${1:-default}"
  local target_file=""

  case "$target_profile" in
    default) target_file="$MODELS_DIR/models.default.json" ;;
    zai) target_file="$MODELS_DIR/models.zai.json" ;;
    claude) target_file="$MODELS_DIR/models.claude.json" ;;
    openai) target_file="$MODELS_DIR/models.openai.json" ;;
    opencode-go) target_file="$MODELS_DIR/models.opencode-go.json" ;;
    active|current|"") target_file="$ACTIVE_MAP" ;;
    *)
      if [ -f "$MODELS_DIR/models.${target_profile}.json" ]; then
        target_file="$MODELS_DIR/models.${target_profile}.json"
      else
        echo "Perfil no encontrado: $target_profile" >&2
        exit 1
      fi
      ;;
  esac

  local is_active=0
  if [ "$target_file" = "$ACTIVE_MAP" ] || [ "$(readlink -f "$target_file")" = "$(readlink -f "$ACTIVE_MAP")" ]; then
    is_active=1
  fi

  echo "Editando subagentes en perfil: $(basename "$target_file")"
  echo "Cada agente guarda su par modelo+variante EN ESTE perfil (independiente de los demás)."

  local changes_made=0
  local catalogue_raw=""
  local catalogue_ids=""
  local grouped_model_list=""
  local final_apply=""

  while true; do
    # 1. Elegir subagente con fzf
    local agent_list
    agent_list="$(profile_agent_list "$target_file")"

    local selected_agent=""
    if command -v fzf >/dev/null 2>&1 && [ -t 0 ]; then
      local fzf_prompt="Selecciona subagente a modificar > "
      if [ "$changes_made" -gt 0 ]; then
        fzf_prompt="Selecciona otro subagente a modificar (Esc para salir) > "
      fi
      selected_agent=$(echo "$agent_list" | fzf --height 50% --reverse --prompt="$fzf_prompt" | awk '{print $1}') || true
    else
      echo "$agent_list"
      read -rp "Escribe el nombre exacto del subagente a modificar: " selected_agent || true
    fi

    if [ "$changes_made" -gt 0 ] && [ ! -t 0 ]; then
      if [[ "$selected_agent" =~ ^[sSnNyY]$ ]]; then
        final_apply="$selected_agent"
        break
      fi
    fi

    if [ -z "$selected_agent" ]; then
      if [ "$changes_made" -eq 0 ]; then
        echo "Operación cancelada."
        return 0
      fi
      break
    fi

    # Un agente desconocido nunca debe llegar a una escritura.
    if ! profile_has_agent "$target_file" "$selected_agent"; then
      echo "Subagente no encontrado en el perfil: $selected_agent" >&2
      if [ "$changes_made" -eq 0 ]; then
        return 1
      fi
      break
    fi

    # 3. Estado actual del par en ESTE perfil (una entrada legacy equivale a
    #    {model: <string>, variant: ""}).
    local current_model current_variant
    current_model="$(profile_entry_model "$target_file" "$selected_agent")"
    current_variant="$(profile_entry_variant "$target_file" "$selected_agent")"

    local final_model="$current_model" final_variant="$current_variant"

    if command -v fzf >/dev/null 2>&1 && [ -t 0 ]; then
      # --- MODO INTERACTIVO (FZF) ---
      # Selección unificada: ↑ / ↓ para elegir modelo, ← / → para ciclar variante.
      if [ -z "$catalogue_raw" ]; then
        echo "Cargando catálogo de modelos de OpenCode..."
        catalogue_raw="$(opencode models 2>/dev/null || true)"
        catalogue_ids="$(printf '%s\n' "$catalogue_raw" | grep -E '^[^ /]+/[^ ]+$' || true)"
        grouped_model_list=$(printf '%s\n' "$catalogue_raw" | node -e '
          const raw = require("fs").readFileSync(0, "utf8");
          const lines = raw.split("\n").filter(l => l.includes("/"));
          const byProv = {};
          for (const l of lines) {
            const parts = l.split("/");
            const prov = parts[0];
            if (!byProv[prov]) byProv[prov] = [];
            byProv[prov].push(l);
          }
          for (const [prov, list] of Object.entries(byProv)) {
            console.log(`-- ${prov.toUpperCase()} --`);
            for (const m of list) {
              console.log(`  ${m}`);
            }
          }
        ')
      fi

      if [ -z "$catalogue_ids" ]; then
        echo "No se pudo cargar el catálogo de modelos ('opencode models'). Operación cancelada (no se escribió nada)."
        if [ "$changes_made" -eq 0 ]; then return 0; fi
        break
      fi

      local fzf_model_input="$grouped_model_list"
      if [ -n "$current_model" ] && [ "$current_model" != "(sin modelo asignado)" ]; then
        fzf_model_input="$(printf "* %s (actual)\n%s" "$current_model" "$grouped_model_list")"
      fi

      local picked_line="" selected_model=""

      if [ "$selected_agent" = "small_model" ]; then
        picked_line=$(printf "%s\n" "$fzf_model_input" | fzf --height 60% --reverse --ansi \
          --header="Agente: small_model [global: modelo ligero/resumen] (sin variante)
Modelo actual: ${current_model:-(ninguno)}
[↑/↓] Elegir modelo  [Enter] Confirmar  [Esc] Cancelar" \
          --prompt="Modelo para small_model > ") || true

        if [ -z "$picked_line" ]; then
          echo "Operación cancelada."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi

        selected_model=$(echo "$picked_line" | sed -E 's/^\* +//; s/ +\(actual\).*$//' | awk '{print $1}')
        if [[ "$selected_model" =~ ^-- ]]; then
          echo "Has seleccionado un encabezado de proveedor. Operación cancelada."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi

        if ! printf '%s\n' "$selected_model" | grep -qE '^[^/]+/.+$'; then
          echo "Modelo con forma no válida: '$selected_model' (se espera provider/id). Operación cancelada."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi
        if ! grep -qxF -- "$selected_model" <<<"$catalogue_ids"; then
          echo "Modelo no encontrado en el catálogo de OpenCode: '$selected_model'. Operación cancelada."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi
        final_model="$selected_model"
        final_variant=""
      else
        local state_file cycle_script
        state_file=$(mktemp "${TMPDIR:-/tmp}/oc-var.XXXXXX")
        echo "${current_variant:-default}" > "$state_file"
        echo "0" >> "$state_file"

        cycle_script=$(mktemp "${TMPDIR:-/tmp}/oc-cycle.XXXXXX")
        cat << EOF > "$cycle_script"
#!/usr/bin/env bash
STATE="$state_file"
DIR="\$1"
AGENT="$selected_agent"
CUR_M="${current_model:-ninguno}"

VAR_LIST=("default" "low" "medium" "high" "max")
cur=\$(head -n 1 "\$STATE" 2>/dev/null || echo "default")
[ -z "\$cur" ] && cur="default"

idx=0
for i in "\${!VAR_LIST[@]}"; do
  if [ "\${VAR_LIST[\$i]}" = "\$cur" ]; then idx=\$i; break; fi
done

if [ "\$DIR" = "next" ]; then
  idx=\$(( (idx + 1) % \${#VAR_LIST[@]} ))
  echo "\${VAR_LIST[\$idx]}" > "\$STATE"
  echo "1" >> "\$STATE"
elif [ "\$DIR" = "prev" ]; then
  idx=\$(( (idx - 1 + \${#VAR_LIST[@]}) % \${#VAR_LIST[@]} ))
  echo "\${VAR_LIST[\$idx]}" > "\$STATE"
  echo "1" >> "\$STATE"
fi

new_var=\$(head -n 1 "\$STATE")
out="Agente: \033[1;36m\${AGENT}\033[0m | Modelo actual: \033[1;33m\${CUR_M}\033[0m\nVariante: "
for v in "\${VAR_LIST[@]}"; do
  if [ "\$v" = "\$new_var" ]; then
    out+="\033[1;97;44m \$v \033[0m "
  else
    out+="\033[2m \$v \033[0m "
  fi
done
out+="\n\033[1;33m[↑/↓]\033[0m Modelo  \033[1;33m[←/→]\033[0m Variante  \033[1;33m[Enter]\033[0m Confirmar  \033[1;33m[Esc]\033[0m Cancelar"
printf "\$out"
EOF
        chmod +x "$cycle_script"

        local init_header
        init_header=$("$cycle_script" "init")

        picked_line=$(printf "%s\n" "$fzf_model_input" | fzf --height 60% --reverse --ansi \
          --header="$init_header" \
          --bind "left:transform-header($cycle_script prev)" \
          --bind "right:transform-header($cycle_script next)" \
          --bind "shift-left:transform-header($cycle_script prev)" \
          --bind "shift-right:transform-header($cycle_script next)" \
          --prompt="Modelo para $selected_agent > ") || true

        local chosen_variant variant_touched
        chosen_variant=$(head -n 1 "$state_file" 2>/dev/null || echo "default")
        variant_touched=$(sed -n '2p' "$state_file" 2>/dev/null || echo "0")
        rm -f "$state_file" "$cycle_script"

        if [ -z "$picked_line" ]; then
          echo "Operación cancelada."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi

        selected_model=$(echo "$picked_line" | sed -E 's/^\* +//; s/ +\(actual\).*$//' | awk '{print $1}')
        if [[ "$selected_model" =~ ^-- ]]; then
          echo "Has seleccionado un encabezado de proveedor. Operación cancelada."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi

        if ! printf '%s\n' "$selected_model" | grep -qE '^[^/]+/.+$'; then
          echo "Modelo con forma no válida: '$selected_model' (se espera provider/id). Operación cancelada (no se escribió nada)."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi
        if ! grep -qxF -- "$selected_model" <<<"$catalogue_ids"; then
          echo "Modelo no encontrado en el catálogo de OpenCode: '$selected_model'. Operación cancelada (no se escribió nada)."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi

        final_model="$selected_model"
        if [ "$selected_model" != "$current_model" ]; then
          if [ "$variant_touched" -eq 1 ]; then
            if [ "$chosen_variant" = "default" ]; then final_variant=""; else final_variant="$chosen_variant"; fi
          else
            final_variant=""
            echo "Nota: el modelo cambia; la variante se resetea a default (no se reutiliza la del modelo anterior)."
          fi
        else
          if [ "$variant_touched" -eq 1 ]; then
            if [ "$chosen_variant" = "default" ]; then final_variant=""; else final_variant="$chosen_variant"; fi
          else
            final_variant="$current_variant"
          fi
        fi
      fi
    else
      # --- MODO NO INTERACTIVO / PIPE FALLBACK ---
      # Conserva flujo secuencial (modelo / variante / ambos) para scripts y tests.
      local edit_action=""
      local want_model=0 want_variant=0
      if [ "$selected_agent" = "small_model" ]; then
        edit_action="modelo"
        want_model=1
        want_variant=0
      else
        echo "¿Qué quieres editar para $selected_agent?"
        echo "1) modelo"
        echo "2) variante (propia de este perfil)"
        echo "3) ambos"
        local action_choice=""
        read -rp "Elige acción [1-3]: " action_choice || true
        case "$action_choice" in
          1|modelo|model|m) edit_action="modelo" ;;
          2|variante|variant|v) edit_action="variante" ;;
          3|ambos|both|a|b) edit_action="ambos" ;;
        esac

        case "$edit_action" in
          modelo|variante|ambos) ;;
          *)
            echo "Acción no válida. Operación cancelada (no se escribió nada)."
            if [ "$changes_made" -eq 0 ]; then return 0; fi
            break
            ;;
        esac

        if [ "$edit_action" = "modelo" ] || [ "$edit_action" = "ambos" ]; then want_model=1; fi
        if [ "$edit_action" = "variante" ] || [ "$edit_action" = "ambos" ]; then want_variant=1; fi
      fi

      local selected_model=""
      local model_differs=0
      if [ "$want_model" -eq 1 ]; then
        if [ -z "$catalogue_raw" ]; then
          echo "Cargando catálogo de modelos de OpenCode..."
          catalogue_raw="$(opencode models 2>/dev/null || true)"
          catalogue_ids="$(printf '%s\n' "$catalogue_raw" | grep -E '^[^ /]+/[^ ]+$' || true)"
        fi

        read -rp "Escribe el provider/modelo (ej: anthropic/claude-sonnet-5-5): " selected_model || true

        if [ -z "$selected_model" ]; then
          echo "Operación cancelada."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi

        if ! printf '%s\n' "$selected_model" | grep -qE '^[^/]+/.+$'; then
          echo "Modelo con forma no válida: '$selected_model' (se espera provider/id). Operación cancelada (no se escribió nada)."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi
        if [ -z "$catalogue_ids" ]; then
          echo "No se pudo cargar el catálogo de modelos ('opencode models'); no se puede verificar '$selected_model'. Repite la edición con el catálogo disponible o edita el mapa a mano y valida con el instalador. Operación cancelada (no se escribió nada)."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi
        if ! grep -qxF -- "$selected_model" <<<"$catalogue_ids"; then
          echo "Modelo no encontrado en el catálogo de OpenCode: '$selected_model'. Operación cancelada (no se escribió nada)."
          if [ "$changes_made" -eq 0 ]; then return 0; fi
          break
        fi
        if [ "$selected_model" != "$current_model" ]; then
          model_differs=1
        fi
      fi

      local variant_choice="" variant_value="" variant_keep=0
      if [ "$want_variant" -eq 1 ]; then
        echo "Modelo de $selected_agent en este perfil: ${current_model}"
        echo "Variante actual de $selected_agent: ${current_variant:-(vacía, usa default)}"
        echo "Aviso: la variante vive en ESTE perfil; el resto de perfiles no se ve afectado."
        echo "Aviso: no todos los modelos soportan todos los valores; la compatibilidad no está garantizada."

        read -rp "Variante para $selected_agent (low|medium|high|max|default|keep): " variant_choice || true

        variant_choice="${variant_choice,,}"
        case "$variant_choice" in
          low|medium|high|max) variant_value="$variant_choice" ;;
          default) variant_value="" ;;
          keep) variant_keep=1 ;;
          *)
            echo "Variante no válida: ${variant_choice:-"(vacía)"}. Operación cancelada (no se escribió nada)."
            if [ "$changes_made" -eq 0 ]; then return 0; fi
            break
            ;;
        esac
      fi

      if [ "$model_differs" -eq 1 ]; then
        final_model="$selected_model"
        final_variant=""
        if [ "$want_variant" -eq 0 ]; then
          echo "Nota: el modelo cambia; la variante se resetea a default (no se reutiliza la del modelo anterior)."
        fi
      fi
      if [ "$want_variant" -eq 1 ]; then
        if [ "$variant_keep" -eq 1 ]; then
          if [ "$model_differs" -eq 1 ]; then
            echo "Nota: 'keep' con un modelo distinto no puede conservar la variante del modelo anterior; queda en default."
          else
            echo "Variante sin cambios (keep)."
          fi
        else
          final_variant="$variant_value"
        fi
      fi
    fi

    # 6. No-op si el par resultante es exactamente el actual (misma selección de
    #    modelo + keep, o valores idénticos): nada se escribe ni se aplica.
    if [ "$final_model" = "$current_model" ] && [ "$final_variant" = "$current_variant" ]; then
      echo "Sin cambios: ${selected_agent} ya tiene modelo ${current_model} y variante ${current_variant:-(default)} en este perfil."
      if [ "$changes_made" -eq 0 ]; then
        return 0
      fi
      continue
    else
      # 7. Escribir el cambio (solo llega aquí un edit completo y válido).
      if [ "$selected_agent" = "small_model" ]; then
        write_small_model "$target_file" "$final_model"
      else
        write_agent_pair "$target_file" "$selected_agent" "$final_model" "$final_variant"
      fi
      changes_made=$((changes_made + 1))
    fi
  done

  if [ "$changes_made" -eq 0 ]; then
    return 0
  fi

  if [ "$NO_APPLY" -eq 1 ]; then
    echo "Perfil $(basename "$target_file") actualizado con $changes_made cambio(s) pero NO aplicado (--no-apply activado)."
    return 0
  fi

  if [ "$FORCE_APPLY" -eq 1 ]; then
    final_apply="s"
  elif [ -z "$final_apply" ]; then
    if [ "$is_active" -eq 1 ]; then
      read -rp "¿Quieres aplicar los cambios ahora a tu entorno activo en OpenCode? [s/N]: " final_apply || true
    else
      read -rp "¿Quieres aplicar este perfil ahora a tu entorno activo en OpenCode? [s/N]: " final_apply || true
    fi
  fi

  local should_apply=0
  if [[ "$final_apply" =~ ^[sSyY] ]]; then
    should_apply=1
  elif [ -z "$final_apply" ] && [ ! -t 0 ] && [ "$is_active" -eq 1 ]; then
    # Legacy pipe fallback: si stdin se cerró (EOF) en perfil activo sin respuesta explícita
    should_apply=1
  fi

  if [ "$should_apply" -eq 1 ]; then
    if [ "$is_active" -eq 0 ]; then
      cp "$target_file" "$ACTIVE_MAP"
    fi
    "$INSTALLER"
    echo ""
    show_status
    return 0
  fi

  if [ "$is_active" -eq 1 ]; then
    echo "Perfil activo actualizado con $changes_made cambio(s) pero NO aplicado a OpenCode."
  else
    echo "Perfil $(basename "$target_file") actualizado con $changes_made cambio(s) pero NO aplicado: tu configuración activa no cambia."
  fi
  return 0
}

interactive_menu() {
  local options=(
    "default          - Opción A Primaria (GLM-5.3-flash, GLM-5.3 max, Sol verify, Gemini explore)"
    "claude           - Prioridad Anthropic Directa (Sonnet 5.5 worker, Opus 5.5 riesgo)"
    "openai           - Prioridad ChatGPT Plus (GPT-6.1 Sol worker/verify/reviewers)"
    "opencode-go      - Prioridad OpenCode Go / Zen (Qwen 3.8 Max worker/verify)"
    "edit             - Seleccionar y cambiar modelo/variante de subagentes en un perfil"
    "apply            - Aplicar perfil activo actual a OpenCode e instalar"
    "status           - Mostrar tabla detallada del estado de todos los subagentes"
  )

  local choice=""
  local key=""
  if command -v fzf >/dev/null 2>&1 && [ -t 0 ]; then
    local fzf_out
    fzf_out=$(printf "%s\n" "${options[@]}" | fzf --height 45% --reverse \
      --header="[Enter]: seleccionar perfil | [e]: editar subagente del perfil seleccionado" \
      --expect=e,ctrl-e \
      --prompt="Selecciona acción OpenCode > ") || true

    key=$(echo "$fzf_out" | head -n 1)
    choice=$(echo "$fzf_out" | sed -n '2p' | awk '{print $1}')
  else
    echo "=== Selector de Perfiles de OpenCode ==="
    echo "1) default     (Opción A Primaria)"
    echo "2) claude      (Prioridad Anthropic Directa)"
    echo "3) openai      (Prioridad ChatGPT Plus)"
    echo "4) opencode-go (Prioridad OpenCode Go / Zen)"
    echo "e) edit        (Editar modelo/variante de subagentes en un perfil)"
    echo "a) apply       (Aplicar perfil activo a OpenCode)"
    echo "s) status      (Ver estado actual)"
    read -rp "Elige una opción [1-4, e, a, s]: " opt
    case "$opt" in
      1) choice="default" ;;
      2) choice="claude" ;;
      3) choice="openai" ;;
      4) choice="opencode-go" ;;
      e|E|5) choice="edit" ;;
      a|A) choice="apply" ;;
      s|S|6) choice="status" ;;
      *) echo "Cancelado." ; exit 0 ;;
    esac
  fi

  # Si pulsó 'e' o 'ctrl-e' sobre cualquier perfil en la lista de fzf
  if [ "$key" = "e" ] || [ "$key" = "ctrl-e" ]; then
    local target_p="${choice:-active}"
    if [ "$target_p" = "edit" ] || [ "$target_p" = "status" ] || [ "$target_p" = "apply" ]; then
      target_p="active"
    fi
    edit_subagent_in_profile "$target_p"
    exit 0
  fi

  if [ -n "$choice" ]; then
    case "$choice" in
      edit)
        local prof_choice="active"
        if command -v fzf >/dev/null 2>&1 && [ -t 0 ]; then
          prof_choice=$(printf "%s\n" "active (perfil actualmente en uso)" "default" "claude" "openai" "opencode-go" "zai" | fzf --height 40% --reverse --prompt="¿En qué perfil quieres modificar el subagente? > " | awk '{print $1}')
        fi
        edit_subagent_in_profile "${prof_choice:-active}"
        ;;
      apply)
        "$SCRIPT_PATH" apply
        ;;
      *)
        "$SCRIPT_PATH" "$choice"
        ;;
    esac
  fi
  exit 0
}

FALLBACK_MODE=0
NO_APPLY=0
FORCE_APPLY=0
ARGS=()

for arg in "$@"; do
  case "$arg" in
    --fallback|-f)
      FALLBACK_MODE=1
      ;;
    --no-apply|-n)
      NO_APPLY=1
      ;;
    --apply|-y)
      FORCE_APPLY=1
      ;;
    *)
      ARGS+=("$arg")
      ;;
  esac
done

PROFILE="${ARGS[0]:-}"

# Soporte para sintaxis tipo: opencode-switch default --fallback / opencode-switch -f default
if [ "$PROFILE" = "--fallback" ] || [ "$PROFILE" = "-f" ]; then
  FALLBACK_MODE=1
  PROFILE="${ARGS[1]:-}"
fi

# Si se ejecuta sin argumentos o con "select" / "menu", abrir menú interactivo con fzf
if [ -z "$PROFILE" ] && [ "$FALLBACK_MODE" -eq 0 ]; then
  if [ -t 0 ]; then
    interactive_menu
  else
    show_status
    exit 0
  fi
fi

case "$PROFILE" in
  edit|subagent)
    edit_subagent_in_profile "${ARGS[1]:-active}"
    ;;
  apply)
    echo "Aplicando configuración activa a OpenCode..."
    "$INSTALLER"
    echo ""
    show_status
    ;;
  select|menu)
    interactive_menu
    ;;
  default|"")
    if [ "$FALLBACK_MODE" -eq 1 ]; then
      echo "Cambiando a perfil: DEFAULT (con fallback) (Orquestador en Sol, Worker en Sonnet 5.5, Verify en Sol)..."
      # Si se pide flag -f en default, carga directamente Sol/Sonnet como contingencia
      cp "$MODELS_DIR/models.openai.json" "$ACTIVE_MAP"
    else
      if command -v ai-quotas >/dev/null 2>&1; then
        echo "Verificando cuota de Z.ai..."
        ai-quotas check || true
      fi
      echo "Cambiando a perfil: DEFAULT (Opción A Primaria — Orquestador GLM-5.3-flash, Worker GLM-5.3, Verify Sol)..."
      cp "$MODELS_DIR/models.default.json" "$ACTIVE_MAP"
    fi
    "$INSTALLER"
    echo ""
    show_status
    ;;
  zai)
    if command -v ai-quotas >/dev/null 2>&1; then
      echo "Verificando cuota de Z.ai..."
      ai-quotas check || true
    fi
    echo "Cambiando a perfil: ZAI (Prioridad Z.ai Max — Worker en GLM-5.3, Orquestador GLM-5.3-flash)..."
    cp "$MODELS_DIR/models.zai.json" "$ACTIVE_MAP"
    "$INSTALLER"
    echo ""
    show_status
    ;;
  claude|subs)
    echo "Cambiando a perfil: CLAUDE (Prioridad Anthropic Directa — Worker/Verify en Sonnet 5.5, Riesgo en Opus 5.5)..."
    cp "$MODELS_DIR/models.claude.json" "$ACTIVE_MAP"
    "$INSTALLER"
    echo ""
    show_status
    ;;
  openai|sol|subs-sol|subs-openai)
    echo "Cambiando a perfil: OPENAI (Prioridad ChatGPT Plus — Worker/Verify/Review en GPT-6.1 Sol)..."
    cp "$MODELS_DIR/models.openai.json" "$ACTIVE_MAP"
    "$INSTALLER"
    echo ""
    show_status
    ;;
  opencode-go|zen|go)
    echo "Cambiando a perfil: OPENCODE-GO (Prioridad OpenCode Go / Zen — Worker/Verify en Qwen 3.8 Max)..."
    cp "$MODELS_DIR/models.opencode-go.json" "$ACTIVE_MAP"
    "$INSTALLER"
    echo ""
    show_status
    ;;
  status)
    show_status
    echo ""
    echo "Uso: opencode-switch [--fallback | -f] [--no-apply | -n] [--apply | -y] [default | zai | claude | openai | opencode-go | edit | apply | status | menu]"
    ;;
  *)
    echo "Perfil desconocido: $PROFILE" >&2
    echo "Uso: opencode-switch [--fallback | -f] [--no-apply | -n] [--apply | -y] [default | zai | claude | openai | opencode-go | edit | apply | status | menu]" >&2
    exit 1
    ;;
esac
