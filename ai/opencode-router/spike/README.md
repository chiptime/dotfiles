# Spike: Antigravity CLI como subagente de tareas (PoC)

Fecha: 2026-08-19 / 2026-08-20 · Entorno: PC local de Bruno (WSL2) · Cuenta: `brunosilva.narro@gmail.com` (suscripción existente).

## Verdict: VALIDATED

**Pregunta:** ¿Puede la Antigravity CLI oficial ejecutar tareas NO-INTERACTIVAS de punta a punta (brief entra → resultado sale) con la suscripción existente, de forma scriptable y capturable, SIN patrones que comprometan la cuenta?

**Respuesta:** Sí. El binario oficial `agy` (v1.0.10) tiene un modo `--print`/`--prompt` no-interactivo que ejecuta una tarea completa de punta a punta (lectura del workspace, edición de archivos, escritura del resultado), capturable en stdout/stderr y con sesión persistente que no exige re-login entre tareas.

---

## Evidencia por fase

### S0 — Instalación y login

- **Binario oficial:** `agy` (ELF x86-64, ~172 MB, stripped), **v1.0.10**.
- **Ubicación:** `/home/bruno/.local/bin/agy` (instalado 2026-06-22).
- **Canal de instalación oficial:** `curl -fsSL https://antigravity.google/cli/install.sh | bash` (instala en `~/.local/bin`).
- **Login:** ya realizado previamente. Cuenta activa `brunosilva.narro@gmail.com` (`~/.gemini/google_accounts.json`), credencial OAuth presente (`~/.gemini/oauth_creds.json`, actualizada 2026-08-10).
- **Verificación de sesión viva sin re-login:** `agy models` responde en vivo con el catálogo de suscripción:
  - Gemini 3.6 Flash (High/Medium/Low), Gemini 3.5 Flash (High/Medium/Low), Gemini 3.1 Pro (High/Low), Claude Sonnet 4.6 (Thinking), Claude Opus 4.6 (Thinking), GPT-OSS 120B (Medium).
- Telemetría ya desactivada (`~/.gemini/antigravity-cli/settings.json` → `enableTelemetry: false`).
- Comando de evidencia: `agy --version` → `1.0.10`.

### S1 — Superficie no-interactiva

`agy --help` (v1.0.10) expone:

| Flag | Efecto |
| --- | --- |
| `-p`, `--print` | Ejecuta un prompt único no-interactivo e imprime la respuesta |
| `--prompt` | Alias de `--print` |
| `--dangerously-skip-permissions` | Auto-aprueba todas las solicitudes de permiso de herramientas |
| `--add-dir` (repetible) | Añade un directorio al workspace |
| `--print-timeout` | Timeout del modo print (default `5m0s`) |
| `--model` | Modelo de la sesión |
| `--log-file` | Ruta alternativa del log |
| `--sandbox` | Restricciones de terminal |
| `-i`, `--prompt-interactive` | Prompt inicial interactivo (continúa sesión) |
| `-c`, `--continue` / `--conversation` | Reanudar conversaciones |

Subcomandos: `changelog`, `help`, `install`, `models`, `plugin`/`plugins`, `update`.

**Nota:** NO existe flag `--output-format json`. La salida es texto plano (con enlaces `file://` en markdown).

### S2 — Tarea E2E mínima (corazón del spike)

Comando exacto:

```bash
timeout 300 agy --print "$(cat BRIEF.md)" \
  --add-dir /tmp/agy-spike/task-1 \
  --dangerously-skip-permissions > run.log 2>&1
```

- **Brief:** "Lee input.txt y escribe un resumen de 3 frases en RESUMEN.md. No hagas nada más."
- **Resultado:** `exit=0`, **29 s**, `RESUMEN.md` creado con resumen correcto de 3 frases, `input.txt` intacto, respuesta capturada en `run.log`.

### S3 — Stress

1. **Tarea que debe fallar** (ruta inexistente): `exit=0`, 15 s. El agente detectó el fallo y lo reportó en texto capturable ("No ha sido posible completar la operación porque el archivo no existe…"). NO creó `salida.md`.
2. **Timeout kill** (`timeout 10`): `exit=124`, ~13 s. Muerte limpia, sin estado corrupto en el workspace (cero archivos parciales), `run.log` = `Error: timeout waiting for response`.
3. **Tarea consecutiva tras 60 s:** `exit=0`, 9 s. Sesión viva sin re-login, sin rate-limit visible, `salida.txt` correcto.

### S4 — Wrapper (v1)

`run-antigravity-task.sh <brief.md> <workdir>` con **contrato de éxito explícito**, porque el exit code de agy miente:

| Exit | Significado |
| --- | --- |
| `0` | Terminó **y** todos los artefactos de `AGY_EXPECT` existen y no están vacíos |
| `2` | Argumentos inválidos, brief inexistente o `agy` no encontrado |
| `3` | Guarda diaria alcanzada |
| `4` | El agente terminó limpio pero **la tarea no** (artefacto ausente o vacío) |
| `124` | Timeout |

- `AGY_EXPECT` — artefactos que deben existir para considerar éxito. Si no se define, avisa por stderr de que **no se validó**.
- `AGY_EXTRA_DIRS` — dirs extra para `--add-dir`. **Vacío por defecto a propósito** (ver contención).
- La guarda diaria cuenta las **conversaciones reales de agy** (sus `.db`), no las invocaciones del wrapper, así que también cuenta si se llama a `agy` directamente.

Contrato verificado con 5 casos, sustituyendo el binario por `true` (sin gastar cuota): artefacto ausente → `4`, válido → `0`, vacío → `4`, brief inexistente → `2`, sin contrato → warning + `0`.

### S5 — MCP: CodeGraph y Engram dentro de agy

Hallazgo posterior, y el que más cambia la conclusión: **`agy` acepta servidores MCP**, vía `~/.gemini/config/mcp_config.json` (`mcpServers`, stdio).

- **CodeGraph** ya estaba cableado (`codegraph serve --mcp`) + tool-def en `~/.gemini/antigravity-cli/mcp/codegraph/`.
- **Engram** añadido con `engram setup antigravity-cli`.
- `ai-stack` indexado con `codegraph init` (28 archivos, 424 nodos, 1 844 edges).

**Verificación funcional** — una tarea pidió a agy usar ambas herramientas:
- `codegraph_explore` → 48 símbolos reales, cruzados contra `codegraph query` con líneas exactas (`proxyServerError:1371`, `MOBILE_CSS:787`). No alucinado.
- `mem_search` → devolvió la observación real `#7298` del proyecto `ai-stack`.

**Consecuencia:** con CodeGraph + Engram + web fresca + modelo de suscripción, agy deja de ser inferior al subagente nativo para exploración.

**Decisión de alcance (privacidad):** Engram quedó **de solo lectura** a propósito — `mem_search`, `mem_context`, `mem_get_observation`, `mem_current_project`. El perfil `agent` exponía 18 tools incluyendo `mem_save`/`mem_update`, lo que habría permitido a un agente alojado en Google **escribir** en la memoria persistente de todos los proyectos.

---

## Qué funcionó

- Ejecución no-interactiva E2E completa (lectura → razonamiento → escritura de archivos) con la suscripción.
- Salida y errores 100 % capturables (stdout/stderr a archivo), sin TTY requerido.
- Sesión persistente: 8 tareas sin re-login ni flujo OAuth repetido.
- Flags estables y documentados; telemetría ya apagada.
- Muerte limpia ante timeout externo (exit 124), sin corrupción de workspace.

## Qué falló o sorprendió

- **El exit code NO refleja éxito/fallo de la tarea.** El CLI devuelve `0` aunque la tarea subyacente falle (el "éxito" es que la sesión del agente terminó). Resuelto en el wrapper v1 con `AGY_EXPECT`.
- **Sin salida JSON nativa.** Hay que parsear texto o usar el SDK (fuera de alcance de este spike).
- **Contención: agy escribe fuera del workdir.** Con `--dangerously-skip-permissions` y el repo en `--add-dir`, dejó un `informe.md` huérfano en la raíz de `ai-stack` (conservado como `logs/05-MCP-verify-informe-STRAY-repo-root.md`). Todo directorio que pases es **escribible**, no solo legible.
- **El contador diario original era teatro.** Solo contaba invocaciones del wrapper: el 19-ago hubo 6 ejecuciones reales y registró 1. Corregido para medir las conversaciones reales de agy.

## Recomendación: ship (acotado)

VALIDATED para el rol de **investigación técnica bajo demanda**, que es donde aporta lo que las herramientas nativas no dan: web fresca con citas verificables + modelo de suscripción.

1. Usarlo como herramienta manual, invocada por ti, con workspace contenido.
2. Pasar el repo por `AGY_EXTRA_DIRS` **solo** cuando la tarea deba leer código.
3. Declarar siempre `AGY_EXPECT`: sin eso no hay señal de éxito.
4. NUNCA exponer la suscripción como API/servicio (territorio de baneo); sin proxies 2api.
5. Si aparece warning/captcha/re-auth inesperado → parar y documentar.

**Lo que NO recomiendo todavía:** montar la cola local→VPS. La superficie operativa (seguridad de cuenta, contención de escritura, alcance de la memoria) es real y el caudal es bajo. Es mucha maquinaria para poco rendimiento; revisar cuando el uso manual justifique automatizarlo.

### Sobre el techo de tareas/día

El "~10 tareas/día" del spike **no salió de ningún límite documentado**: fue una cota conservadora fijada a mano para la fase experimental. En 8 ejecuciones no apareció ni un rate-limit, warning, captcha ni re-auth. El wrapper v1 usa 25 por defecto y es configurable (`AGY_MAX_TASKS_PER_DAY`), pero sigue siendo una **guarda de patrón, no una cuota**. Las señales reales a vigilar son: cuota de la suscripción, aparición de warnings/captcha/re-auth, y forma del uso (ráfagas humanas vs. cadencia de máquina).

---

## Contenido del entregable

- `README.md` — este documento (veredicto + evidencias).
- `run-antigravity-task.sh` — wrapper v1 (contrato de éxito + guarda real).
- `logs/` — evidencias crudas por fase (`00-*`, `01-*`, `02-*`, `03-*`, `04-*`, `05-*`).
- `explorations/` — exploraciones producidas con agy (`health-msk-assistant-prompting.md` + su brief).
