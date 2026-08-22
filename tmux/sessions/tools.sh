#!/usr/bin/env bash
# Tmux session: tools
# Grid 2x2: gentle-ai, gemini, opencode web, mobile-proxy

SESSION="tools"

# Si ya existe, nos attachamos nomás
tmux has-session -t "$SESSION" 2>/dev/null && exec tmux attach -t "$SESSION"

# Crear sesión detached
tmux new-session -d -s "$SESSION"

# Grid 2x2
tmux split-window -h -t "$SESSION:0.0" -c "$HOME/Code/personal/hungry/compare-prices"
tmux split-window -v -t "$SESSION:0.0"
tmux split-window -v -t "$SESSION:0.1" -c "$HOME/Code/personal/hungry/compare-prices"

tmux select-layout -t "$SESSION" tiled

# Pane 0 — top-left: gentle-ai
tmux send-keys -t "$SESSION:0.0" "gentle-ai" Enter
sleep 1

# Pane 1 — top-right: gemini
tmux send-keys -t "$SESSION:0.1" "gemini" Enter
sleep 1

# Pane 2 — bottom-left: opencode web (via sdd-explore router launcher)
tmux send-keys -t "$SESSION:0.2" "$HOME/Code/personal/ai-stack/scripts/opencode-web.sh --hostname 0.0.0.0 --port 4096" Enter
sleep 1

# Pane 3 — bottom-right: mobile-proxy
tmux send-keys -t "$SESSION:0.3" "bun run scripts/mobile-proxy.ts" Enter

tmux select-pane -t "$SESSION:0.0"
tmux attach -t "$SESSION"
