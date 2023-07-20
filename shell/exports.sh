# time
export TIMEFMT=$'%J %U user %S system %P cpu %*E total'

export AUTO_NOTIFY_THRESHOLD=10

# Brew
export HOMEBREW_PREFIX="/home/linuxbrew/.linuxbrew"
export HOMEBREW_CELLAR="/home/linuxbrew/.linuxbrew/Cellar"
export HOMEBREW_REPOSITORY="/home/linuxbrew/.linuxbrew/Homebrew"
export PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin${PATH+:$PATH}"
export MANPATH="/home/linuxbrew/.linuxbrew/share/man${MANPATH+:$MANPATH}:"
export INFOPATH="/home/linuxbrew/.linuxbrew/share/info:${INFOPATH:-}"

# export JAVA_HOME='/Library/Java/JavaVirtualMachines/amazon-corretto-15.jdk/Contents/Home'
export GEM_HOME="$HOME/.gem"
export GOPATH="$HOME/.go"

# export PYTHON="/usr/local/opt/python/libexec/bin"

export FZF_DEFAULT_OPTS='
  --color=pointer:#ebdbb2,bg+:#3c3836,fg:#ebdbb2,fg+:#fbf1c7,hl:#8ec07c,info:#928374,header:#fb4934
  --reverse
'
export XDG_CONFIG_HOME="$HOME/.config"

# export _JAVA_OPTIONS=-Xmx512M
export JAVA_HOME="/usr/lib/jvm/java-8-openjdk-amd64"
export JENV="$HOME/.jenv/bin"

# variables for WSL2 UI Apps - VcXsrv
export DISPLAY="$(grep nameserver /etc/resolv.conf | sed 's/nameserver //'):0"
export LIBGL_ALWAYS_INDIRECT=1
#echo xfce4-session >~/.xsession

export GDK_SCALE=0.5
export GDK_DPI_SCALE=2
# export GDK_SCALE=3
# export GDK_DPI_SCALE=2
export XCURSOR_SIZE=64
# split path to array and concatenate for compatibility with WSL
# dont work with path with spaces
# originalPath=$(echo "$PATH" | sed 's! !\\ !g')

# oldPath=($(echo $PATH | tr ':' ' '))

newpath=(
  "$HOME/bin"
  "$JAVA_HOME"
  "$JENV"
  "$DOTLY_PATH/bin"
  "$DOTFILES_PATH/bin"
  "$JAVA_HOME/bin"
  "$GEM_HOME/bin"
  "$GOPATH/bin"
  "$HOME/.cargo/bin"
  "$HOMEBREW_PREFIX"
  "$HOMEBREW_CELLAR"
  "$HOMEBREW_REPOSITORY"
  "$PATH"
  "$MANPATH"
  "$INFOPATH"
  "/usr/local/opt/ruby/bin"
  "/usr/local/opt/python/libexec/bin"
  "/usr/local/bin"
  "/usr/local/sbin"
  "/bin"
  "/usr/bin"
  "/usr/sbin"
  "/sbin"
)

# export path=("${newpath[@]}" "${oldPath[@]}")

printf -v sNewPath "%s:" "${newpath[@]}"

export PATH="$sNewPath $PATH"
