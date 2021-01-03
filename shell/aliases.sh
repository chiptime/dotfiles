# Enable aliases to be sudo’ed
alias sudo='sudo '

# 
alias code="/mnt/c/Program\ Files/Microsoft\ VS\ Code/Code.exe"
alias open="explorer.exe"

alias ..="cd .."
alias ...="cd ../.."
alias ll="exa -l"
alias la="exa -la"
alias tt="exa --tree --level=2 --long"

# Jumps
alias ~="cd ~"
# alias code='cd ~/Code'
# TODO symlink to ~/Code
alias dotfiles='cd /mnt/d/Bruno/dotfiles/.dotfiles'
alias cdc='cd /mnt/d/Bruno/Code'
alias cdw="cdc; cd Work"
alias clece="cdw; cd Stratesys/Clece"

# Git
alias gaa="git add -A"
alias gc="$DOTLY_PATH/bin/dot git commit"
alias gcm="git commit -m"
alias gca="git add --all && git commit --amend --no-edit"
alias gco="git checkout"
alias gd="$DOTLY_PATH/bin/dot git pretty-diff"
alias gs="git status -sb"
alias gf="git fetch --all -p"
alias gps="git push"
alias gpsf="git push --force"
alias gpl="git pull --rebase --autostash"
alias gb="git branch"
alias gl="$DOTLY_PATH/bin/dot git pretty-log"

# Utils
alias k='kill -9'
alias i.='(idea $PWD &>/dev/null &)'
alias c.='(code --remote wsl+Ubuntu-20.04 "$(pwd)" &>/dev/null &)'
alias cw.='(code "$(wslpath -w "$(pwd)")" &>/dev/null &)'
alias o.='open .'
alias up='dot package update_all'

