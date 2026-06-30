# dotly
# Paths are now exported in ~/.zshenv
# export DOTFILES_PATH="$HOME/.dotfiles"
# export DOTLY_PATH="$DOTFILES_PATH/modules/dotly"

PATH=$(
  IFS=":"
  echo "${path[*]}"
)
# export PATH
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
# Fig pre block. Keep at the top of this file.
[[ -f "$HOME/.fig/shell/zshrc.pre.zsh" ]] && builtin source "$HOME/.fig/shell/zshrc.pre.zsh"


# ZSH Ops
setopt HIST_IGNORE_ALL_DUPS
setopt HIST_FCNTL_LOCK

# ZSH style
# if [[ -z $TMUX ]]; then
  zstyle ':fzf-tab:complete:cd:*' fzf-preview 'eza -1 --color=always $realpath'
# else
#   zstyle ':fzf-tab:*' fzf-command ftb-tmux-popup
# fi;

# setopt autopushd

# Start zim
source "$ZIM_HOME/init.zsh"

# Async mode for autocompletion
ZSH_AUTOSUGGEST_USE_ASYNC=true
ZSH_HIGHLIGHT_MAXLENGTH=300

source "$DOTFILES_PATH/shell/init.sh"

fpath=("$DOTLY_PATH/shell/zsh/themes" "$DOTLY_PATH/shell/zsh/completions" $fpath)

autoload -Uz promptinit && promptinit

source "$DOTFILES_PATH/shell/zsh/key-bindings.zsh"
source "$DOTLY_PATH/shell/zsh/bindings/reverse_search.zsh"

# bun completions
[ -s "/home/bruno/.bun/_bun" ] && source "/home/bruno/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

PATH=~/.console-ninja/.bin:$PATH

# Fig post block. Keep at the bottom of this file.
[[ -f "$HOME/.fig/shell/zshrc.post.zsh" ]] && builtin source "$HOME/.fig/shell/zshrc.post.zsh"

# opencode
export PATH=/home/bruno/.opencode/bin:$PATH

# Configuraciones para un historial más seguro
HISTSIZE=10000
SAVEHIST=10000
setopt INC_APPEND_HISTORY     # Escribe el comando inmediatamente (evita pérdida por crash)
setopt SHARE_HISTORY          # Comparte historial entre pestañas y sincroniza mejor
setopt HIST_IGNORE_ALL_DUPS   # Evita que el archivo crezca con basura repetida
setopt HIST_REDUCE_BLANKS     # Limpia espacios extra

# Decirle a WezTerm el título de la pestaña (vía hook, no pisa Zim)
_wezterm_tab_title() {
  print -Pn "\e]0;$(basename "$PWD" 2>/dev/null)\a" 2>/dev/null
}
precmd_functions+=(_wezterm_tab_title)

# Widget para pegar imagen de Windows con Alt+V
paste_image_widget() {
  local filename="screenshot_$(date +%Y%m%d_%H%M%S).png"
  # Ejecutamos PowerShell en background, evitamos ensuciar la pantalla
  powershell.exe -Command "Add-Type -AssemblyName System.Windows.Forms; \$img=[System.Windows.Forms.Clipboard]::GetImage(); if (\$img) { \$img.Save(\"$(wslpath -w $(pwd))\\$filename\") } else { exit 1 }" > /dev/null
  
  if [ $? -eq 0 ]; then
    # Inserta el nombre del archivo donde esté el cursor
    LBUFFER="${LBUFFER}${filename} "
    zle -R # Redibuja el prompt
  else
    zle -M "❌ No hay imagen en el portapapeles" # Muestra mensaje sin romper el comando actual
  fi
}
zle -N paste_image_widget
bindkey '^[v' paste_image_widget  # ^[v es Alt + V en zsh

tx () { 
    local script="$HOME/.config/tmux/sessions/${1}.sh";
    if [[ -f "$script" ]]; then
        source "$script";
    else
        echo "No existe la sesión: $1";
        echo "Disponibles:";
        ls ~/.config/tmux/sessions/ 2> /dev/null | sed 's/\.sh$//';
    fi
}

# ==============================================================================
# WEZTERM SHELL INTEGRATION
# ==============================================================================
# El split de WezTerm pasa el CWD vía variable de entorno (evita validación Windows)
if [[ -n "$WEZTERM_CWD" && -d "$WEZTERM_CWD" ]]; then
  cd "$WEZTERM_CWD"
  unset WEZTERM_CWD
fi
# Habilita "Semantic Zones" para poder saltar de prompt en prompt y 
# detectar el directorio actual (CWD) nativamente en WezTerm.
# ZSH no define $HOSTNAME por defecto, y wezterm.sh lo necesita
# para armar la URL del OSC 7 (CWD tracking).
export HOSTNAME="${HOST:-$(hostname 2>/dev/null || echo localhost)}"
source "$DOTFILES_PATH/shell/zsh/plugins/wezterm/wezterm.sh"

# ==============================================================================
# ZSH-AI GHOST TEXT: Ctrl+G (directo LiteLLM, 0 overhead)
# ==============================================================================
export ZSH_AI_KEY="${LITELLM_MASTER_KEY:-sk-test-key-12345}"
export ZSH_AI_URL="http://127.0.0.1:4000/v1/chat/completions"
export ZSH_AI_MODEL="qwen-3.6-27b"

# Contexto cacheado 120s (directorio + git status para mejor precisión)
_ai_quick_context() {
  local cache_file="/tmp/_ai_ctx_$$"
  local now=$(date +%s)
  if [[ -f "$cache_file" ]]; then
    local cached_at=$(head -1 "$cache_file")
    (( now - cached_at < 120 )) && { tail -n +2 "$cache_file"; return; }
  fi
  local ctx="OS: $(uname -s) | Dir: ${PWD##*/}"
  # Contenido del directorio (primeros 15 archivos)
  local files=$(ls -1A 2>/dev/null | head -15 | tr '\n' ' ')
  [[ -n "$files" ]] && ctx+=" | Files: $files"
  # Git
  if git rev-parse --is-inside-work-tree &>/dev/null 2>&1; then
    local branch=$(git branch --show-current 2>/dev/null)
    local status=$(git status --short 2>/dev/null | head -8 | tr '\n' ' ')
    ctx+=" | Git: $branch"
    [[ -n "$status" ]] && ctx+=" | Changes: $status"
  fi
  # Proyecto
  [[ -f package.json ]] && ctx+=" | Project: node/bun"
  [[ -f Cargo.toml ]] && ctx+=" | Project: rust"
  [[ -f pyproject.toml || -f requirements.txt ]] && ctx+=" | Project: python"
  [[ -f go.mod ]] && ctx+=" | Project: go"
  echo "$now" >| "$cache_file" && echo "$ctx" >> "$cache_file" && echo "$ctx"
}

# Llamada directa curl (con prompt mejorado de zsh-ai)
_ai_call_litellm() {
  local q="${1//\"/\\\"}"
  local ctx=$(_ai_quick_context)
  local sys="You are a zsh command generator. Output ONLY the raw command. No markdown, no backticks, no explanation. Use single quotes for spaces. Double quotes only for variable expansion. Prefer modern alternatives: rg over grep, fd over find, bat over cat, jq for JSON, eza over ls."
  curl -s --max-time 10 "$ZSH_AI_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ZSH_AI_KEY" \
    -d "{\"model\":\"$ZSH_AI_MODEL\",\"max_tokens\":120,\"temperature\":0.1,\"messages\":[{\"role\":\"system\",\"content\":\"$sys\"},{\"role\":\"user\",\"content\":\"Context: $ctx\\n\\nTask: $q\"}]}" 2>/dev/null \
    | jq -r '.choices[0].message.content // empty' 2>/dev/null
}

# Detector de comandos peligrosos
_ai_is_dangerous() {
  local cmd="$1"
  [[ "$cmd" =~ (rm[[:space:]]+-r[^[:space:]]*f|[[:space:]]mkfs|dd[[:space:]]+if=.*of=/dev/sd|chmod[[:space:]]+777|sudo[[:space:]]+rm|git[[:space:]]+push[[:space:]]+--force|curl.*\|[[:space:]]*bash) ]]
}

# Widget: Ctrl+G → ghost text
_ai_ghost_suggest() {
  local query="$BUFFER"
  [[ -z "$query" ]] && return
  POSTDISPLAY=$'\n'"  ..."
  zle redisplay
  local cmd=$(_ai_call_litellm "$query")
  if [[ -n "$cmd" && "$cmd" != *"error"* ]]; then
    _AI_GHOST_CMD="$cmd"
    # Escapar % para que no interfiera con prompt sequences
    local safe="${cmd//\%/%%}"
    if _ai_is_dangerous "$cmd"; then
      POSTDISPLAY=$'\n'"  ⚠ $safe"
    else
      POSTDISPLAY=$'\n'"  $safe"
    fi
  else
    POSTDISPLAY=$'\n'"  ❌ falló"
  fi
  zle redisplay
}

# Widget: Alt+E → explicar el comando actual
_ai_explain() {
  local query="$BUFFER"
  [[ -z "$query" ]] && return
  local sys="You are a Zsh CLI expert. Briefly explain in SPANISH what the following command does, in 1-2 lines. Be direct and informative."
  local q="${query//\"/\\\"}"
  local explanation=$(curl -s --max-time 10 "$ZSH_AI_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ZSH_AI_KEY" \
    -d "{\"model\":\"$ZSH_AI_MODEL\",\"max_tokens\":100,\"temperature\":0.3,\"messages\":[{\"role\":\"system\",\"content\":\"$sys\"},{\"role\":\"user\",\"content\":\"$q\"}]}" 2>/dev/null \
    | jq -r '.choices[0].message.content // empty' 2>/dev/null)
  if [[ -n "$explanation" && "$explanation" != *"error"* ]]; then
    zle -M "💡 ${explanation}"
  else
    zle -M "❌ No se pudo explicar"
  fi
}

# → (Right Arrow): aceptar ghost text
_ai_accept_ghost() {
  if [[ -n "$_AI_GHOST_CMD" ]]; then
    BUFFER="$_AI_GHOST_CMD"
    CURSOR=${#BUFFER}
    POSTDISPLAY=""
    unset _AI_GHOST_CMD
    zle redisplay
  else
    zle forward-char
  fi
}

# Limpiar ghost text al ejecutar (vía precmd)
_ai_clear_ghost() {
  if [[ -n "$_AI_GHOST_CMD" ]]; then
    POSTDISPLAY=""
    unset _AI_GHOST_CMD
  fi
}
precmd_functions+=(_ai_clear_ghost)

zle -N _ai_ghost_suggest
zle -N _ai_accept_ghost
zle -N _ai_explain
bindkey '^G'    _ai_ghost_suggest
bindkey $'\e[C' _ai_accept_ghost   # Right Arrow
bindkey $'\eOC' _ai_accept_ghost   # Right Arrow (app mode)
bindkey $'\ee'  _ai_explain        # Alt+E → explicar

# ==============================================================================
# COMMAND HEALING: Alt+F (AI-powered error correction, async via FIFO)
# ==============================================================================

# -- Phase 1: Error tracking via preexec/precmd hooks --
# Temp file /tmp/_ai_last_cmd_$$ stores: line1=command, line2=exit_status

# preexec: runs BEFORE command execution. Captures what's about to run.
_ai_preexec_track() {
  _AI_PENDING_CMD="$1"
}

# precmd: runs AFTER command execution. Captures exit status (first thing).
_ai_precmd_track() {
  local _exit=$?
  if [[ -n "${_AI_PENDING_CMD:-}" ]]; then
    _AI_LAST_CMD="$_AI_PENDING_CMD"
    _AI_LAST_EXIT="$_exit"
    _AI_PENDING_CMD=""
    printf '%s\n%s\n' "$_AI_LAST_CMD" "$_AI_LAST_EXIT" >| "/tmp/_ai_last_cmd_$$"
  fi
}

preexec_functions+=(_ai_preexec_track)
# Insert at beginning to capture $? before other precmd functions modify it
precmd_functions=(_ai_precmd_track "${precmd_functions[@]}")

# -- Phase 2: Async ZLE widget + FIFO callback --

_ai_heal_widget() {
  # Guard: no failed command available
  if [[ -z "${_AI_LAST_CMD:-}" || "${_AI_LAST_EXIT:-0}" -eq 0 ]]; then
    POSTDISPLAY=$'\n'"  ℹ️ No failed command to heal"
    zle redisplay
    return 0
  fi

  # Loading indicator
  local safe_cmd="${_AI_LAST_CMD//\%/%%}"
  POSTDISPLAY=$'\n'"  🔄 Healing: $safe_cmd (exit $_AI_LAST_EXIT)..."
  zle -R -c

  # Clean up any previous async session
  if [[ -n "${_AI_HEAL_FD:-}" ]]; then
    exec {_AI_HEAL_FD}>&- 2>/dev/null
    zle -F "$_AI_HEAL_FD" 2>/dev/null
  fi

  # Create FIFO for async communication
  local fifo="/tmp/_ai_heal_fifo_$$"
  rm -f "$fifo"
  mkfifo "$fifo" || {
    POSTDISPLAY=$'\n'"  ❌ Failed to create async pipe"
    zle redisplay
    return 1
  }

  # Snapshot variables for background job (subshell inherits copies)
  local _h_cmd="$_AI_LAST_CMD"
  local _h_exit="$_AI_LAST_EXIT"
  local _h_key="$ZSH_AI_KEY"
  local _h_url="$ZSH_AI_URL"
  local _h_model="$ZSH_AI_MODEL"

  # Background job: call LLM healing prompt and write result to FIFO
  # Does NOT block ZLE — runs in a subshell via &!
  (
    # Phase 3: Healing-specific prompt (brevity + CLI correctness)
    local sys="You are a zsh command debugger. The user ran a command that FAILED. Output ONLY the corrected command. No markdown, no backticks, no explanation. If the command is fundamentally wrong, suggest the closest correct alternative. Prefer modern CLI tools: rg, fd, bat, jq, eza."
    local escaped="${_h_cmd//\"/\\\"}"
    local result
    result=$(curl -s --max-time 15 "$_h_url" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $_h_key" \
      -d "{\"model\":\"$_h_model\",\"max_tokens\":120,\"temperature\":0.1,\"messages\":[{\"role\":\"system\",\"content\":\"$sys\"},{\"role\":\"user\",\"content\":\"Failed command (exit $_h_exit): $escaped\\n\\nSuggest the corrected command:\"}]}" 2>/dev/null \
      | jq -r '.choices[0].message.content // empty' 2>/dev/null)
    # Always write to FIFO (empty string signals failure to callback)
    printf '%s\n' "${result:-}" > "$fifo"
  ) &!

  # Open FIFO read+write: <> avoids blocking since we act as both reader and writer
  exec {_AI_HEAL_FD}<>"$fifo"

  # Register ZLE handler: fires when data is readable on the FD
  zle -F "$_AI_HEAL_FD" _ai_heal_callback
}

# Callback: invoked by ZLE when the FIFO has data ready
_ai_heal_callback() {
  local result
  IFS= read -r result <&$_AI_HEAL_FD || true

  # Clean up FD and FIFO
  exec {_AI_HEAL_FD}>&- 2>/dev/null
  zle -F "$_AI_HEAL_FD" 2>/dev/null
  rm -f "/tmp/_ai_heal_fifo_$$"
  unset _AI_HEAL_FD

  # Process result: insert into buffer or show error
  if [[ -n "$result" && "$result" != *"error"* ]]; then
    if _ai_is_dangerous "$result"; then
      local safe="${result//\%/%%}"
      POSTDISPLAY=$'\n'"  ⚠ Dangerous suggestion (review manually): $safe"
    else
      BUFFER="$result"
      CURSOR=${#BUFFER}
      POSTDISPLAY=""
    fi
  else
    POSTDISPLAY=$'\n'"  ❌ Could not generate a fix"
  fi
  zle redisplay
}

# -- Phase 3: Binding --

zle -N _ai_heal_widget
bindkey '^[f' _ai_heal_widget  # Alt+F → Command Healing

# Cleanup temp files on shell exit
_ai_heal_cleanup() {
  rm -f "/tmp/_ai_last_cmd_$$"
  rm -f "/tmp/_ai_heal_fifo_$$"
}
zshexit_functions+=(_ai_heal_cleanup)

# ── WezTerm shell integration fix ──────────────────────────────
# Las funciones __wezterm_* son inyectadas por WezTerm después de
# que .zshrc termina. Las pisamos en el primer precmd.
__override_wezterm_shell_integration() {
  functions[__wezterm_set_user_var]='() { return 0 }'
  functions[__wezterm_user_vars_precmd]='() { return 0 }'
  # Elimina este hook después de la primera ejecución
  precmd_functions=("${(@)precmd_functions:#__override_wezterm_shell_integration}")
}
precmd_functions+=(__override_wezterm_shell_integration)


# Added by Antigravity CLI installer
export PATH="/home/bruno/.local/bin:$PATH"
