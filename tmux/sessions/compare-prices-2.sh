#!/usr/bin/env bash
# Tmux session: compare-prices-2
# Grid 2x2: opencode con chats 5-8 (índices 4, 5, 6, 7 desde el más reciente)

SESSION="compare-prices-2"
PROJECT_DIR="$HOME/Code/personal/hungry/compare-prices"

# Si ya existe, nos attachamos nomás
tmux has-session -t "$SESSION" 2>/dev/null && exec tmux attach -t "$SESSION"

# Crear sesión detached
tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR"

# Grid 2x2
tmux split-window -h -t "$SESSION:0.0" -c "$PROJECT_DIR"
tmux split-window -v -t "$SESSION:0.0" -c "$PROJECT_DIR"
tmux split-window -v -t "$SESSION:0.2" -c "$PROJECT_DIR"

tmux select-layout -t "$SESSION" tiled

# Esperar a que los shells estén listos
sleep 2

# Obtener sesiones desde el directorio del proyecto
SESSION_IDS=$(cd "$PROJECT_DIR" && opencode session list --format json -n 8 2>/dev/null | jq -r '.[4:8][].id')

# Lanzar opencode en cada panel (chat existente o nuevo)
pane=0
for sid in $SESSION_IDS; do
  tmux send-keys -t "$SESSION:0.${pane}" "opencode -s ${sid}" Enter
  sleep 2
  pane=$((pane + 1))
done

# Paneles sin chat → abrir opencode vacío
while [ $pane -lt 4 ]; do
  tmux send-keys -t "$SESSION:0.${pane}" "opencode" Enter
  sleep 2
  pane=$((pane + 1))
done

tmux select-pane -t "$SESSION:0.0"
tmux attach -t "$SESSION"
