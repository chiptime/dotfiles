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

selected=( $(fc -rl 1 |
	FZF_DEFAULT_OPTS="--height ${FZF_TMUX_HEIGHT:-40%} $FZF_DEFAULT_OPTS -n2..,.. --tiebreak=index --bind=ctrl-r:toggle-sort $FZF_CTRL_R_OPTS --query=${(qqq)LBUFFER} +m" fzf) )
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
	servor $1 index.html 1000 --reload
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
  repo_url=$(curl -s -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/user/repos?per_page=200" | jq --raw-output ".[].ssh_url" | fzf)
  git clone "$repo_url"
  echo "$repo_url"
}

function lazy_nvm {
	unset -f nvm
	unset -f npm
	unset -f node
	unset -f npx

	if [ -d "${HOME}/.nvm" ]; then
		export NVM_DIR="$HOME/.nvm"
		[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" # linux
		#[ -s "$(brew --prefix nvm)/nvm.sh" ] && source $(brew --prefix nvm)/nvm.sh # osx
	fi
}

# aliases
function nvm { lazy_nvm; nvm "$@"; }
function npm { lazy_nvm; npm "$@"; }
function node { lazy_nvm; node "$@"; }
function npx { lazy_nvm; npx "$@"; }
