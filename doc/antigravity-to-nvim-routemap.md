# Routemap: Antigravity IDE → Neovim (dentro de WezTerm)

Plan de migración de los flujos de trabajo diarios de Antigravity IDE a Neovim, con las disposiciones de pantalla equivalentes. Motivación: eliminar dos GUIs Electron (VSCode + Antigravity) que duplican servidores Node, language servers y watchers dentro de WSL, manteniendo opencode web para acceso desde el móvil vía Tailscale.

## Camino rápido

1. **Fase 0 — arreglar TS**: sustituir `ts_ls` por `vtsls` (el server que usa VSCode, sobre LSP).
2. **Fase 1 — terminal persistente**: reemplazar la terminal de usar-y-tirar por un toggle que no mate el dev server.
3. **Fase 2 — commit con IA**: keymap que genera el mensaje de commit con `opencode run`.
4. **Fase 3 — opcional**: neotest para correr los tests desde nvim.

Verificación global: una semana sin abrir agy/VSCode sin fricción real.

## Mapa de features

| Feature de Antigravity | Cubierta en nvim por | Estado |
|---|---|---|
| Ver y editar código | buffers + neo-tree + LSP (`vtsls`) | ⚠️ fix (server TS) |
| Buscar archivos | `<leader>ff` fzf-lua | ✅ ya está |
| Buscar texto (grep) | `<leader>fg` fzf-lua live_grep | ✅ ya está |
| Ir a definición / refs / hover | `gd` `gr` `K` (LSP) | ✅ ya está |
| Renombrar / code actions | `<leader>rn` `<leader>ca` | ✅ ya está |
| Ver cambios (diff) | `<leader>gd` diffview.nvim | ✅ ya está |
| Histórico de git (timeline) | `<leader>gH` diffview file history | ✅ ya está |
| Blame / hunks inline | gitsigns (`]h` `<leader>gp` `<leader>gb`) | ✅ ya está |
| Mensajes de commit con IA | opencode run → COMMIT_EDITMSG | 🆕 nuevo (Fase 2) |
| Levantar app (pruebas manuales) | terminal persistente + browser | ⚠️ fix (Fase 1) |
| Panel del agente (chat) | opencode TUI en split vertical | 🆕 opcional |
| Tests desde el editor | neotest-vitest | 🆕 opcional (Fase 3) |

Nota honesta: las **pruebas manuales siguen ocurriendo en el browser** — nvim no reemplaza eso. Lo que cambia es dónde vive el dev server (buffer persistente con toggle) y dónde ves sus logs.

## Disposiciones: antes en agy, después en nvim

### 1. Edición diaria: ver, buscar, editar

**Antigravity IDE:**

```
┌────┬─────────────────┬────────────────────────────────┬───────────────┐
│ AB │ EXPLORER        │ tab1.ts │ Button.tsx │ api.ts   │  AGENTE (agy) │
│    │ ▾ src/          │ ────────────────────────────── │               │
│ 📄  │ ▾ components/   │                                │  chat + diffs │
│ 🔍  │   Button.tsx    │  código                        │  propuestos   │
│ ⌥   │   api.ts        │  IntelliSense                  │  por el agente│
├────┴─────────────────┴────────────────────────────────┴───────────────┤
│ TERMINAL / PROBLEMS / OUTPUT          npm run dev ──►                 │
├───────────────────────────────────────────────────────────────────────┤
│ ⎇ main ⚠0  TS ok                        Ln 42 Col 7     UTF-8  TSX    │
└───────────────────────────────────────────────────────────────────────┘
```

**Neovim en WezTerm (después):**

```
                              WezTerm
┌──────────────┬──────────────────────────────────────────────────────┐
│ neo-tree     │  buffer: components/Button.tsx                        │
│ (toggle)     │  ║ signcolumn → hunks de gitsigns                     │
│ ▾ src/       │  K        → hover con tipos (vtsls)                   │
│ ▾ components/│  gd / gr  → definición / referencias                  │
│   Button.tsx │  al escribir → menú de cmp con auto-imports           │
│   api.ts     │  hints de tipos inline (vtsls inlay hints)            │
├──────────────┴──────────────────────────────────────────────────────┤
│ terminal toggle ── npm run dev ── sigue vivo aunque la cierres       │
├──────────────────────────────────────────────────────────────────────┤
│ NORMAL │ main │ editors/nvim/…/Button.tsx │           TSX  42% 42:7  │
└──────────────────────────────────────────────────────────────────────┘
        ↑ <leader>ff / <leader>fg → fzf-lua flotante encima de todo
```

El buscador no es una barra escondida: es una ventana flotante que aparece sobre la disposición actual y desaparece al elegir.

### 2. Git: diff, histórico, blame

**Antigravity IDE (Source Control + timeline del archivo):**

```
┌───────────────┬─────────────────────────────────────────────────────┐
│ SOURCE CTRL   │  Button.tsx (HEAD)      │ Button.tsx (Working Tree)  │
│ M Button.tsx  │  props = {              │  props = {                 │
│ M api.ts      │    variant,             │    variant, size,          │
│               │                          │    onClick,                │
│ TIMELINE      │                          │  }                        │
│ · fix size    │        diff resaltado entre columnas                  │
│ · add onClick │                                                    │
└───────────────┴─────────────────────────────────────────────────────┘
```

**Neovim: `<leader>gd` (diff review) y `<leader>gH` (histórico del archivo):**

```
┌──────────────┬──────────────────────────┬─────────────────────────────┐
│ FILE PANEL   │ Button.tsx               │ Button.tsx                  │
│ (árbol)      │ a/HEAD                   │ b/Working Tree              │
│ M Button.tsx │  props = {               │  props = {                  │
│ M api.ts     │    variant,              │    variant, size,           │
│              │                          │    onClick,                 │
│  [Stage]     │                          │  }                         │
│  [Restore]   │       mismo diff lado a lado, navegando hunks con ]h [h│
└──────────────┴──────────────────────────┴─────────────────────────────┘
```

`<leader>gH` abre el mismo layout pero con la lista de commits del archivo arriba y el diff de cada commit abajo. Blame de línea con `<leader>gb` (flotante) o `<leader>gB` (columna virtual permanente, tipo GitLens).

### 3. Commit con mensaje generado por IA

**Antigravity IDE:** panel Source Control → escribes/pides el mensaje al agente → botón Commit.

**Neovim (después, Fase 2):**

```
     <leader>gc
        │
        ├─► git diff --cached
        │        │
        │        ▼
        │   opencode run "conventional commit message"
        │        │
        │        ▼
        │   mensaje propuesto
        ▼
┌──────────────────────────────────────────┐
│ COMMIT_EDITMSG (buffer normal)           │
│                                          │
│  feat(button): add size and onClick      │
│                                          │
│  # revisas, editas lo que quieras…       │
└──────────────────────────────────────────┘
        │
        ▼
      :wq   ──►   commit hecho
```

Mismo modelo mental que agy (IA propone, tú revisas), pero el mensaje aterriza en un buffer de nvim que editas como cualquier texto antes de confirmar.

### 4. Levantar la app para pruebas manuales

**Antigravity IDE:** panel de terminal abajo con `npm run dev` → abres el browser en Windows → pruebas. Al cerrar el panel o el IDE, el server muere o queda huérfano.

**Neovim (después, Fase 1):**

```
┌─────────────────────────────────────────────────────────────┐
│  buffers de código (lo que estés editando)                  │
├─────────────────────────────────────────────────────────────┤
│  toggle terminal ── npm run dev                             │
│  VITE v6.x  ready in 412 ms                                 │
│  ➜  Local:   http://localhost:5173/                         │
│  hmr update / src/components/Button.tsx                     │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
  browser en Windows ──► http://localhost:5173  (pruebas manuales)
```

Hoy tu terminal (`<leader>tt`) usa `bufhidden = "wipe"`: cierras el split y el dev server muere. Con snacks.terminal/toggleterm el proceso **sobrevive oculto** y `<leader>tt` lo muestra/oculta como un panel. HMR incluido: guardas en nvim y el browser se actualiza solo.

### 5. Agente en el escritorio (opcional)

El chat lateral de agy tiene equivalente natural en opencode TUI como split vertical persistente. En el móvilsigue siendo opencode web por Tailscale — no cambia nada.

```
┌──────────────────────────────┬──────────────────────┐
│  buffers de código           │  opencode (TUI)      │
│                              │                      │
│                              │  > resume la feature │
│                              │  de checkout…        │
│                              │  ◇ editando 3 files  │
│                              │  ◇ corriendo tests   │
└──────────────────────────────┴──────────────────────┘
```

## Fases

| Fase | Cambio | Dónde | Impacto | Esfuerzo |
|---|---|---|---|---|
| 0 | `ts_ls` → `vtsls` (+ inlay hints) | `lua/lang/frontend.lua`, Mason | Alto: la fricción principal | S |
| 1 | Terminal persistente con toggle | `lua/config/terminal.lua` | Alto: habilita el flujo de pruebas | S |
| 2 | `<leader>gc` commit con opencode | nuevo en git.lua | Medio: reemplaza flujo de agy | S |
| 3 | neotest-vitest (tests desde nvim) | nuevo | Medio: opcional, los tests ya corren fuera | M |

Verificación por fase:

- **0**: abrir un `.tsx` del proyecto → `:LspInfo` muestra `vtsls` → completions y `K` responden sin lag perceptible.
- **1**: `<leader>tt` → levantar dev server → cerrar toggle → reabrir → server sigue vivo (mismo PID).
- **2**: stage hunks con `<leader>gs` → `<leader>gc` → mensaje conventional en el buffer → `:wq` → `git log -1` correcto.
- **3**: `neotest` corre suite de vitest y muestra failures en quickfix.

## Carga del sistema: antes vs después

| Proceso | Antes | Después |
|---|---|---|
| GUIs Electron en Windows | 2 (VSCode + agy) | 0 |
| Servidores Node de IDE en WSL | 2 (uno por GUI) | 0 |
| Language servers duplicados | 2 copias de tsserver + extensiones | 1 (vtsls) |
| opencode web (móvil) | 1 | 1 (sin cambios, uso legítimo) |
| Terminal + editor | WezTerm + nvim | WezTerm + nvim (igual) |

## Checklist de migración

- [ ] Fase 0 aplicada y verificada en un proyecto real
- [ ] Fase 1 aplicada: dev server sobrevive al toggle
- [ ] Fase 2 aplicada: commit con IA sin salir de nvim
- [ ] Una semana completa de trabajo sin abrir agy/VSCode
- [ ] Desinstalar o archivar las GUIs (liberar RAM de Windows)

## Siguiente paso

Ejecutar Fases 0–2 en estos dotfiles (tres cambios pequeños, `frontend.lua` + `terminal.lua` + `git.lua`).
