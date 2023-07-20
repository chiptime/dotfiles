alias sudo='sudo '

# WSL
# alias code="/mnt/c/Program\ Files/Microsoft\ VS\ Code/Code.exe"
# alias code="/mnt/c/Program\ Files/Microsoft\ VS\ Code/bin/code"
alias open="explorer.exe"
alias docker="docker.exe"
alias docker-compose="docker-compose "

notify-send() {
    title="${@}"

    params=""

    count=0

    while [ $# -gt 0 ]; do

        if [[ $1 == "--"* ]]; then

            _param=$(echo $1 | sed "s/--app-name/--appId/g")

            params="$params $_param"

            shift

        elif [[ $count == 0 ]]; then

            v=$(echo "$1" | sed 's/"/\\"/g')

            params="$params-c \"$v\""

            shift
        else
            v=$(echo $1 | tr '\n' ' ')

            params="$params \"$v\""

            shift
        fi

        count=count+1
    done

    # echo "$params"
    # echo "\n\n"
    # echo "wsl-notify-send.exe $params"
    # echo "\n\n"

    # all=$(echo $title | sed "s/--app-name/--appId/g")

    command=(wsl-notify-send.exe $params)

    eval "${command[@]}"
}
zsh-notify() { wsl-notify-send.exe --appId=$appId --category $WSL_DISTRO_NAME "${@}"; }

alias ..="cd .."
alias ...="cd ../.."
alias ll="exa -l"
alias la="exa -la"
alias tt="exa --tree --level=2 --long"

# Jumps
alias ~="cd ~"
alias cdcw='cd /mnt/d/Bruno/Code'
alias cdc='cd ~/Code'
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
alias gcl="clone_git_repo"

# Utils
alias k='kill -9'
alias i.='(idea $PWD &>/dev/null &)'
alias c.='(code --remote wsl+Ubuntu-20.04 "$(pwd)" &>/dev/null &)'
alias cw.='(code "$(wslpath -w "$(pwd)")" &>/dev/null &)'
alias o.='open .'
alias wt='cmd.exe /c start wt.exe -d .'
# alias up='dot package update_all'
