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

# Decirle a WezTerm el título de la pestaña segun la carpeta actual
precmd() {
  print -Pn "\e]0;$(basename $PWD)\a"
}

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
