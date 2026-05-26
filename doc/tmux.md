# Tmux Session Templates

Launcher de sesiones de tmux por proyecto. Un comando y se abre todo el entorno.

## Uso

```bash
tx compare-prices    # Grid 2x2: 2 opencode + 2 gemini
tx voice             # Ídem para voice-assistant
tx                   # Lista sesiones disponibles
```

Dentro de tmux: `Ctrl+b` + `d` para detachear (la sesión sigue corriendo).
Volver a entrar: `tx <nombre>` te re-attacha automáticamente.

## Layout

```
┌──────────────────┬──────────────────┐
│                  │                  │
│    opencode      │    opencode      │
│                  │                  │
├──────────────────┼──────────────────┤
│                  │                  │
│     gemini       │     gemini       │
│                  │                  │
└──────────────────┴──────────────────┘
```

## Estructura

```
~/.config/tmux/sessions/          # Plantillas (1 archivo = 1 proyecto)
├── compare-prices.sh
└── voice.sh

~/.dotfiles/shell/functions.sh    # Función tx() — el launcher
```

## Agregar un proyecto nuevo

1. Copiar una plantilla existente:

```bash
cp ~/.config/tmux/sessions/voice.sh ~/.config/tmux/sessions/nuevo-proyecto.sh
```

2. Editar las dos variables:

```bash
SESSION="nuevo-proyecto"
PROJECT_DIR="$HOME/Code/personal/nuevo-proyecto"
```

3. Ajustar ventanas y comandos según necesidad.

4. Lanzar: `tx nuevo-proyecto`

## Atajos esenciales

| Atajo | Acción |
|---|---|
| `Ctrl+b` `←/→/↑/↓` | Moverse entre paneles |
| `Ctrl+b` `o` | Panel siguiente |
| `Ctrl+b` `q` | Mostrar números de panel |
| `Ctrl+b` `z` | Zoom/fullscreen en panel actual |
| `Ctrl+b` `d` | Detach (sale sin cerrar) |

## Cómo funciona

Cada script hace lo mismo:

1. `tmux has-session` — Si la sesión ya existe, re-attacha directo
2. `tmux new-session -d` — Crea sesión en background
3. `tmux split-window -h/-v` — Divide en grid 2x2
4. `tmux select-layout tiled` — Distribuye los paneles equitativamente
5. `tmux send-keys` — Lanza opencode/gemini en cada panel
6. `tmux attach` — Conecta al entorno completo

No hay dependencias extra. Es tmux puro.
