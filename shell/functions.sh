function cdd() {
	cd "$(ls -d -- */ | fzf)" || echo "Invalid directory"
}

function j() {
	fname=$(declare -f -F _z)

	[ -n "$fname" ] || source "$DOTLY_PATH/modules/z/z.sh"

	_z "$1"
}

function recent_dirs() {
	# This script depends on pushd. It works better with autopush enabled in ZSH
	escaped_home=$(echo $HOME | sed 's/\//\\\//g')
	selected=$(dirs -p | sort -u | fzf)

	cd "$(echo "$selected" | sed "s/\~/$escaped_home/")" || echo "Invalid directory"
}

reverse-search() {
	local selected num
	setopt localoptions noglobsubst noposixbuiltins pipefail HIST_FIND_NO_DUPS 2> /dev/null

	selected=(
		$(fc -rl 1 |
		FZF_DEFAULT_OPTS="--height ${FZF_TMUX_HEIGHT:-40%} $FZF_DEFAULT_OPTS -n2..,.. --tiebreak=index --bind=ctrl-r:toggle-sort $FZF_CTRL_R_OPTS --query=${(qqq)LBUFFER} +m" fzf)
	)
	local ret=$?
	if [ -n "$selected" ]; then
		num=$selected[1]
		if [ -n "$num" ]; then
			zle vi-fetch-history -n $num
		fi
	fi
	zle redisplay
	typeset -f zle-line-init >/dev/null && zle zle-line-init
	return $ret
}

function s() {
	npm >/dev/null 2>/dev/null
	# servor "$1" index.html 8080 --reload
	servor
}

function export_apps() {
	sudo apt list --installed > "$DOTFILES_PATH/os/linux/apt-installed.txt"
	echo "APT apps exported"

	ls -1 /usr/local/lib/node_modules | grep -v npm > "$DOTFILES_PATH/langs/js/global_modules.txt"
	echo "NPM apps exported"
}

function import_apps() {
	sudo dpkg-query -l | awk '{if ($1 == "ii") print $2}' > "$DOTFILES_PATH/os/linux/apt-installed.txt"
	sudo xargs -a "$DOTFILES_PATH/os/linux/apt-installed.txt" apt install
	echo "APT aps imported!"

	xargs -I_ npm install -g "_" < "$DOTFILES_PATH/langs/js/global_modules.txt"
	echo "NPM apps imported!"
}

function clone_git_repo() {
  repo_url=$(curl -s -H "PRIVATE-TOKEN: $GITLAB_STR_TOKEN" "https://gitlab.stratesys.global/api/v4/projects?per_page=200&page=1" | jq --raw-output ".[].http_url_to_repo" | fzf | sed 's/https\?:\/\///')
  git clone "https://token:$GITLAB_STR_TOKEN@$repo_url"
  echo "https://$repo_url"
}

function lazy_nvm {
	unset -f nvm
	unset -f npm
	unset -f node
	unset -f npx

	if [ -d "$HOME/.config/nvm" ]; then
		export NVM_DIR="$HOME/.config/nvm"
		[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" # linux
		# [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion
		export NODE_PATH=$(npm root -g)
		#[ -s "$(brew --prefix nvm)/nvm.sh" ] && source $(brew --prefix nvm)/nvm.sh # osx
	fi

	unset -f pnpm

	if [ -d "${HOME}/.local/share/pnpm" ]; then
		export PNPM_HOME="$HOME/.local/share/pnpm"
		PATH="$PATH:$PNPM_HOME"
		# [ -s "$PNPM_HOME/pnpm" ] && . "$PNPM_HOME/pnpm" # linux
	fi
}

# aliases
function nvm { lazy_nvm; nvm "$@"; }
function npm { lazy_nvm; npm "$@"; }
function node { lazy_nvm; node "$@"; }
function npx { lazy_nvm; npx "$@"; }
function pnpm { lazy_nvm; pnpm "$@"; }



function lazy_jenv {
	unset -f jenv

	eval "$(jenv init -)"
}

function jenv { lazy_jenv; jenv "$@"; }

# nvm
#
# echo printenv

#   echo 'in vscode, nvm not work; use `lazy_nvm`';
if [ "${TERM_PROGRAM}" = "vscode" ] && [ "${VSCODE_TERM_CONTEXT}" = "task" ]; then
    lazy_nvm
fi


###-begin-npm-completion-###
#
# npm command completion script
#
# Installation: npm completion >> ~/.bashrc  (or ~/.zshrc)
# Or, maybe: npm completion > /usr/local/etc/bash_completion.d/npm
#

# echo "compdef"

_npm_completion() {
    local si=$IFS
    compadd -- $(COMP_CWORD=$((CURRENT-1)) \
                    COMP_LINE=$BUFFER \
                    COMP_POINT=0 \
                    npm completion -- "${words[@]}" \
                    2>/dev/null)
    IFS=$si


            cword="$COMP_CWORD"
            words=("${COMP_WORDS[@]}")
}

# execute if is zsh terminal
if [ -n "$ZSH_VERSION" ]; then
	compdef _npm_completion npm
fi
