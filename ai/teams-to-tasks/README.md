# Teams → Tareas (teams-to-tasks)

Lee Microsoft Teams web mediante un Chromium con perfil persistente (MCP `playwright_teams`) y convierte mensajes en tareas de la base de datos **Tareas** de Notion personal.

## Componentes

| Artefacto (fuente en este repo) | Destino real (mapeado por symlink) |
| --- | --- |
| `ai/agents/opencode/skills/teams-to-tasks/` | `~/.config/opencode/skills/teams-to-tasks` |
| `ai/agents/opencode/skills/notion-personal-backlog/` | `~/.config/opencode/skills/notion-personal-backlog` |
| `ai/agents/opencode/skills/dotfiles-context/` | `~/.config/opencode/skills/dotfiles-context` |
| `ai/teams-to-tasks/cron.sh` | `~/.config/opencode/scripts/teams-to-tasks-cron.sh` |
| `ai/teams-to-tasks/poller.ts` | sin symlink: cron.sh lo resuelve vía `readlink -f` junto a `src/` y `node_modules/` |
| `ai/agents/opencode/mcp/playwright_teams.fragment.json` | aplicado dentro de `~/.config/opencode/opencode.json` por el instalador |

Datos locales (nunca en el repo): perfil de navegador en `~/.local/share/opencode/playwright-teams-profile`, estado y log en `~/.local/state/teams-to-tasks/`.

## Instalación (máquina nueva o reparación)

```bash
~/.dotfiles/scripts/install-teams-to-tasks.sh
```

Idempotente: symlinks, directorios locales, línea de crontab y fragmento MCP (via `jq`) solo si no existen. Requisitos: `opencode`, `jq`, `flock`, `npx`, `bun`, y el token `NOTION_CLECE` definido en `shell/private-env.sh` (enlazado FUERA del repo — jamás commitear secretos).

El instalador además ejecuta `bun install --frozen-lockfile` y un **smoke check del poller**: resuelve el chromium real de `~/.cache/ms-playwright` (falla si hay drift: `bunx playwright install chromium`) y verifica que con caché vacía el poller termina con rc 2 (fail-closed). Así el drift de Chromium se detecta al instalar, no a las 08:00.

Tras el primer install: reiniciar opencode y hacer login manual una vez en la ventana de Chromium que abre el flujo.

## Uso

- **Programado — sweep**: cron `0 8-18 * * 1-5` → primero el poller (preflight LLM-free); solo si hay novedades arranca el agente en modo autónomo (sin confirmación, directo a 📥 Inbox en la BBDD Tareas).
- **Programado — digest**: cron `30 18 * * 1-5` → resumen estructurado del día (Temas/Decisiones/Dudas/Tareas) añadido a la página `📡 Digest Teams` de Notion. Sin poller: el agente corre directo y nunca toca `last_run`.
- **Bajo demanda**: pedir al agente «revisa Teams y genera tareas» — flujo interactivo con confirmación antes de crear.

## Poller (preflight LLM-free)

`poller.ts` abre Teams con el mismo perfil persistente del MCP `playwright_teams`, busca `que` + Date=Today, compara timestamps contra `~/.local/state/teams-to-tasks/last_run` y sale con un código determinista; `cron.sh` decide en función de él:

| rc | Significado | Acción de cron.sh |
| --- | --- | --- |
| `0` | hay mensajes nuevos (news) | agente 1 vez con la ventana `PREV_LAST_RUN`; tras su ÉXITO, cron escribe el T de inicio de poll en `last_run` |
| `1` | sin novedades | solo log (el propio poller ya avanzó `last_run` a su T de inicio) |
| `2` | fallo (login, perfil bloqueado, DOM cambiado, timeout) | log + notificación crítica, sin agente, `last_run` intacto |

- **Timeout**: deadline in-process de 100 s dentro del poller (cierra el navegador) + backstop `timeout -k 15s 120s` en cron.sh (SIGTERM → cierra y rc 2; `-k` SIGKILL como último recurso).
- **Avance diferido del estado**: en news el poller NO escribe nada; `last_run` avanza solo tras el éxito del agente (un agente fallido no pierde la hora: la ventana `PREV` se repite y el dedupe por huella evita duplicados). Todo camino de fallo deja `last_run` byte-idéntico.
- **E2E manual** (sin CI con credenciales): con el perfil libre de MCP, `cd ai/teams-to-tasks && bun poller.ts; echo $?` — espera `0` (primera vez = bootstrap news) o `1`; revisa `last_run` y repite. Un rc `2` con diagnóstico en stderr indica drift de selectores o sesión caducada.

## Operación

```bash
touch ~/.local/state/teams-to-tasks/PAUSE   # pausar cron
rm ~/.local/state/teams-to-tasks/PAUSE      # reanudar
tail ~/.local/state/teams-to-tasks/cron.log # qué ha hecho
```

- **Login expirado**: el run autónomo aborta con `SESION_EXPIRADA` en el log. Recuperación: pedir al agente el flujo interactivo y loguearse en la ventana de Chromium.
- **Dedupe**: triple — ventana temporal (`last_run`), huella estable `teams:<chat>:<autor>:<fecha-hora>` en la primera línea de Notas, y comprobación de la BBDD antes de crear.
- **Imágenes**: si un mensaje con tarea lleva captura, se fotografía el elemento y se lee con la visión nativa del modelo (fallback: zai-vision); el resumen va en Notas.
- **Notificaciones**: toasts de Windows vía `wsl-notify-send.exe` (stuartleeks/wsl-notify-send). El binario NO está en el repo: el instalador descarga la versión fijada (v0.1.871612270) a `~/.local/bin/`. Ruta sustituible con `TEAMS_NOTIFY_EXE`; fallback a `notify-send` y, si no hay canal, todo queda en el log.
