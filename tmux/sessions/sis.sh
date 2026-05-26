#!/usr/bin/env bash
# Tmux session: sis
# Grid 2x2: opencode en los 4 paneles

SESSION="sis"
PROJECT_DIR="$HOME/Code/Work/Stratesys/Clece/sis"

# Si ya existe, nos attachamos nomás
tmux has-session -t "$SESSION" 2>/dev/null && exec tmux attach -t "$SESSION"

# Crear sesión detached
tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR"

# Grid 2x2
tmux split-window -h -t "$SESSION:0.0" -c "$PROJECT_DIR"
tmux split-window -v -t "$SESSION:0.0" -c "$PROJECT_DIR"
tmux split-window -v -t "$SESSION:0.2" -c "$PROJECT_DIR"

tmux select-layout -t "$SESSION" tiled

# Lanzar opencode con delay entre cada uno para evitar conflicto con pragma
for pane in 0 1 2 3; do
  tmux send-keys -t "$SESSION:0.${pane}" "opencode" Enter
  sleep 1
done

tmux select-pane -t "$SESSION:0.0"
tmux attach -t "$SESSION"
