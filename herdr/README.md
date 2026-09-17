# Herdr — Cheatsheet

## Modos

Herdr tiene 3 modos de input:

- **Terminal mode** (default): las teclas van al programa del pane
- **Prefix mode**: pulsás `ctrl+b`, soltás, y la siguiente tecla es un comando de herdr
- **Navigate mode**: modo persistente de navegación (entrar con `prefix+[`)

## Keybindings

### Prefix mode: `ctrl+b` → soltar → tecla

```
SPLITS                          PANES (navegación)
─────                           ──────
ctrl+b → v     Split derecha    ctrl+b → h    Ir al pane izquierdo
ctrl+b → -     Split abajo      ctrl+b → j    Ir al pane de abajo
                                ctrl+b → k    Ir al pane de arriba
TABS                            ctrl+b → l    Ir al pane derecho
────                            ctrl+b → `    Último pane (toggle)
ctrl+b → c     Nuevo tab
ctrl+b → n     Tab siguiente    PANES (control)
ctrl+b → p     Tab anterior     ──────
ctrl+b → 1..9  Ir a tab N       ctrl+b → z    Zoom (fullscreen pane)
ctrl+b → ⇧X    Cerrar tab       ctrl+b → r    Modo resize
                                ctrl+b → x    Cerrar pane
WORKSPACES                      ctrl+b → ⇧P   Renombrar pane
──────────
ctrl+b → w     Workspace picker GENERAL
ctrl+b → ⇧N    Nuevo workspace  ───────
ctrl+b → ⇧W    Renombrar ws     ctrl+b → ?    Ver todos los bindings
ctrl+b → ⇧D    Cerrar ws        ctrl+b → q    Detach (todo sigue)
                                ctrl+b → ⇧R   Recargar config
                                ctrl+b → s    Settings
```

### Popups (prefix mode)

```
ctrl+b → g       lazygit    (gestión git visual)
ctrl+b → alt+h   htop       (monitor de procesos)
ctrl+b → f       fzf        (buscar archivos con preview)
```

### Prefix-free (directo, sin ctrl+b)

```
ctrl+alt+1..9              Ir a workspace N directamente
ctrl+alt+← / →             Workspace anterior / siguiente
ctrl+alt+↑ / ↓             Agente anterior / siguiente
alt+shift+← / →            Mover tab izquierda / derecha
ctrl+shift+alt+← ↓ ↑ →    Resize pane directo
```

## Aliases de shell

```
h       herdr                    Launch/attach herdr
ha      herdr agent list         Listar agentes (tabla)
hs      herdr status             Estado del servidor
hw      herdr-workspace          Crear workspace con layout
hn      herdr-ntfy               Push notifications al móvil
hsync   herdr-sync-opencode      Sincronizar proyectos de OpenCode a Herdr
htt     herdr-tts                Neural Voice Feedback Daemon
htr     herdr-tts --toggle-play  Play / Stop lectura del chat actual
htx     herdr-tts --stop         Detener audio inmediatamente
httt    herdr-tts --toggle-auto  Alternar auto-lectura de fondo
htts    herdr-tts --status       Ver estado, lock y voz activa
oca     opencode attach ...      Adjuntarse al servidor opencode central
ocac    opencode attach ... -c   Continuar última sesión en servidor
```

## Workspace Launcher

```bash
hw <directorio> [layout]
```

```
default   →  1 pane solo
dev       →  editor (70%) + shell (30%)
agents    →  main (60%) + 2 agent slots
monitor   →  main (60%) + logs + htop
full      →  4 panes: main + agent + server + logs
opencode  →  editor (40%) + opencode attach al servidor central (60%)
```

## Notificaciones móvil

```
Topic ntfy: bruno-herdr-4VHsmCGC

systemctl --user start herdr-ntfy     # iniciar
systemctl --user stop herdr-ntfy      # parar
journalctl --user -u herdr-ntfy -f    # ver logs
```

## Feedback por Voz (Neural TTS)

Vocalización bajo demanda y automática de agentes con **Microsoft Edge Neural TTS + miniaudio + PulseAudio WSLg**. Diseñado para trabajar con múltiples chats en paralelo sin colisiones de voz.
Desarrollado como plugin independiente en [`~/Code/personal/herdr-tts`](file:///home/bruno/Code/personal/herdr-tts) (instalable vía `herdr plugin install chiptime/herdr-tts` o `herdr plugin link`).

* **Consumo de recursos**: 0 VRAM, ~2 MB RAM host.
* **Seguridad en chats paralelos**: Mutex exclusivo (nunca se pisan las voces). Modo `scope focused` por defecto (solo lee automáticamente el chat que estás mirando).
* **Atajos de teclado en Herdr**:
  * **`prefix + r`** (`ctrl+b` → `r`) → **Play / Stop** toggle del chat en el que estás enfocado.
  * **`prefix + s`** (`ctrl+b` → `s`) → **Stop** inmediato (corta el audio en seco).
  * **`prefix + v`** (`ctrl+b` → `v`) → Alternar auto-lectura de fondo (incluso silenciada, `prefix+r` siempre funciona bajo demanda).
* **Voces**: `elvira` (default), `alvaro`, `ximena`, `dalia`, `jorge`.
* **Servicio**: `systemctl --user status herdr-tts`

```bash
herdr-tts --toggle-play        # Play / Stop en el pane enfocado (htr)
herdr-tts --stop               # Parar audio inmediatamente (htx)
herdr-tts --toggle-auto        # Alternar lectura automática de fondo (httt)
herdr-tts --scope focused|all  # 'focused' (solo chat activo) | 'all' (cualquiera sin solapar)
herdr-tts --status             # Ver configuración, lock y estado (htts)
herdr-tts --voice alvaro       # Cambiar a voz masculina
herdr-tts --rate +25%          # Cambiar velocidad
herdr-tts --speak "Hola"       # Probar síntesis directa
```

## Comandos útiles

```bash
herdr                                  # launch / reattach
herdr server reload-config             # recargar config.toml
herdr agent list                       # ver agentes
herdr agent wait <nombre>              # esperar a que termine
herdr agent prompt <nombre> "texto"    # enviar prompt
herdr agent read <nombre> --source recent-unwrapped --lines 50
herdr agent focus <nombre>             # ir al pane del agente
herdr workspace list                   # listar workspaces
herdr server stop                      # parar TODO
```

## Collie (PWA Móvil con Tailscale)

Collie es una interfaz web PWA diseñada para ver y responder a tus agentes de Herdr desde el móvil sobre tu red privada Tailscale.

```bash
# Estado del servicio
collie status
collie doctor

# Iniciar / Parar / Reiniciar
collie start
collie stop
collie restart
collie logs

# Emparejar tu teléfono (escanear QR o ingresar código de un solo uso)
collie qr
collie pair

# Enlace Tailscale
# URL: https://desktop-q02omrn.tail2640fd.ts.net
```

> **Habilitar HTTPS en Tailscale:**
> Para que el certificado TLS de Tailscale funcione sin advertencias:
> 1. Abrir: https://login.tailscale.com/f/serve?node=nXpmapuxEv11CNTRL
> 2. Activar "Serve" y "HTTPS".
> 3. Ejecutar `collie serve` para activar el enlace HTTPS.

## OpenCode — Integración y Sincronización

Herdr incluye sincronización automática con tus proyectos y sesiones de OpenCode Web:

```bash
# Sincronizar todos tus proyectos a Herdr en modo LIGERO (por defecto, 0 MB de RAM extra)
herdr-sync-opencode
# (o el alias rápido: hsync)

# Sincronizar conectando attach automáticamente a todos (si tienes RAM de sobra)
herdr-sync-opencode --attach

# Conectar manualmente en cualquier pane cuando decidas trabajar en él
oca     # opencode attach http://localhost:4096 (en el directorio del workspace)
ocac    # opencode attach http://localhost:4096 -c (continúa la última sesión)
```

### Proyectos sincronizados de OpenCode Web:
1. `bruno` (`/home/bruno`)
2. `associations` (`.../sis/apps-workspaces/associations`)
3. `entities-portal` (`.../sis/apps-workspaces/entities-portal`)
4. `operational-centers` (`.../sis/apps-workspaces/operational-centers`)
5. `compare-prices` (`~/Code/personal/hungry/compare-prices`)
6. `voice-assistant` (`~/Code/personal/voice-assistant`)
7. `odata-batch` (`~/Code/personal/odata-batch`)
8. `ai-stack` (`~/Code/personal/ai-stack`)
9. `llm-hub` (`~/Code/personal/llm-hub`)
10. `vps` (`~/Code/personal/vps`)
11. `.dotfiles` (`~/.dotfiles`)

### 📊 Comparativa de consumo de RAM

| Aspecto | OpenCode Web (Navegador) | Herdr con `opencode attach` | Múltiples instancias `opencode` completas |
|---|---|---|---|
| **Quién paga la RAM visual** | El navegador del cliente (Chrome en PC/Pixel) | Proceso Node/Bun por cada pane en tu PC | Proceso Node/Bun completo por cada pane |
| **Consumo por proyecto en reposo** | **0 MB** (solo datos en SQLite) | **~300 - 350 MB** (proceso TUI en el pane) | **~1.5 - 3 GB** (duplica modelos, MCPs, runtime) |
| **11 proyectos simultáneos** | ~1.6 GB (solo el servidor daemon) | ~5.1 GB (servidor daemon + 11 clientes TUI) | ~20+ GB (inviable en la mayoría de PCs) |
| **Sincronización con Collie / Móvil** | No vinculada con Herdr | **100% en tiempo real con Collie y ntfy** | 100% en tiempo real |

> **Recomendación de uso:**
> - Si tienes RAM suficiente (16GB - 32GB+), el modo `--all` es el más cómodo porque todas tus terminales están vivas y el cambio en Collie es instantáneo.
> - Si quieres optimizar RAM al máximo, usa el modo On-Demand (`--no-attach` o cerrando panes con `Ctrl+b → x` al terminar): los workspaces persisten en Herdr con 0 MB de coste, y ejecutas `oca` solo en los 2 o 3 proyectos en los que estés trabajando en ese momento.

