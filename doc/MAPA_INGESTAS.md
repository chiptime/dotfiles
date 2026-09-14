# Mapa de Ingestas del Hub — estado al 2026-09-14

Fuente viva del diseño: `doc/control-hub-architecture.md` (ADR) · diagrama: `doc/hub-flow.mmd`.
Mental map completo: qué alimenta al sistema, por dónde, quién ingiere y con qué papel.

## Ingestas

| # | Fuente | Tubería | Quién ingiere | Papel | Periodicidad / frescura | Estado |
|---|---|---|---|---|---|---|
| 1 | **Repos / sesiones / horas** (PC) | `projects.json` (`projects-sensor/v1`) → scp → `hub/inbox/bruno/` | drain-inbox 07:30 | Hechos + autodescubrimiento (incluye `uncatalogued` por sesiones) | cadena matinal: **al arrancar el PC (+90 s) o a las 07:00 si ya está encendido** (timer systemd `Persistent=true` — recupera la ejecución si estaba apagado) + on-demand (`projects`) + cierre de cada sesión; scp → inbox: 🔜 se engancha a la misma cadena (spec ai-stack) | ✅ vivo |
| 2 | **Notion personal (Tareas)** | MCP directo de Gertru (stdio) | clerk → sección `## Tareas` | Tareas personales (id, estado, accionable) | a definir en el spec (sugerencia: con el drain diario) | ✅ vivo |
| 3 | **Notion Clece (Mantenimiento SIS)** | MCP directo de Gertru (mismo token, permisos de ambos) | clerk → sección `## Tareas` | Tareas de trabajo (OC-N, App=streams, rama, estado) | ídem #2 | ✅ según token de Gertru — **verificar empíricamente en la 1ª sync** |
| 4 | **Engram** | `engram-sync` chunks vía git → réplica read-only en VPS (`ENGRAM_SYNC_PULL_ONLY=1`) | Gertru consulta | Contexto: memoria de sesiones, logs, resúmenes | ciclo por ejecución del sync (cron de engram-sync, continuo) | ✅ corriendo |
| 5 | **Teams** | `teams-to-tasks` (cron) → Notion personal | vía #2 | Entrada de tareas del equipo | cron en horario laboral | ✅ vivo |
| 6 | **Telegram** (decisiones de Bruno) | triaje del drain-inbox | clerk | Decisiones de estado/prioridad/ámbito | tiempo real (cuando respondes) | ✅ vivo |
| 7 | **Sesiones de otros agentes** (pi, agy) | → sensor (multi-agente) | sensor → inbox | Actividad por proyecto de todos los agentes | heredaría la del sensor | 🔜 planificado — el panel del dashboard ya es multi-agente |
| 8 | **RPi4 homelab** | `engram-sync` como nodo escritor + potencial sensor propio | réplica Engram / inbox | Cuarta máquina aportando memoria y hechos | heredaría engram-sync | 🔜 repo recién creado |
| 9 | GitHub issues/PRs | *sin tubería* | ¿clerk? | Tareas de código | — | 💡 potencial — sin plan, no dibujado |

## Relojes (definitivos, 2026-09-14)

| Reloj | Cuándo | Qué hace | Estado |
|---|---|---|---|
| **Cadena matinal PC** | **primer encendido +90 s** o 07:00 si ya está on (Persistent) | regen md/html/json → digest ntfy diario | ✅ montado (`projects-morning.timer`) |
| Espejo hub→dashboard | dentro de la cadena matinal (tras regen) | baja `hub-state.json` y lo pinta | 🔜 spec ai-stack |
| scp sensor → inbox | dentro de la cadena matinal (tras espejo) | entrega `projects.json` en `hub/inbox/bruno/` | 🔜 spec ai-stack |
| drain-inbox (VPS) | 07:30 | triaje → Telegram | ✅ diseñado en control-hub (jobs.yaml) |
| morning-brief (VPS) | 08:00 | lee hub → timbre Telegram | ✅ diseñado |
| Sync Notion (Gertru) | cada 1 h a :15, laborables 08–20 | pull tareas → `## Tareas` | 🔜 spec ai-stack |
| engram-sync | push :45 cada hora (locales) · pull 07:15 (VPS) | réplica read-only | 🔜 coordinar con sesión engram-sync |
| Teams→Notion | cron laboral | alimenta Notion personal | ✅ vivo |
| Web :47624 | estática entre regens | vista | ✅ viva — refresca la cadena u on-demand |

**Orden anti-pisado**: productores antes que consumidores (espejo→scp→drain→brief); escritores
del hub (drain y sync de tareas) nunca coinciden en minuto (:30 vs :15); engram-sync lleva
flock + rebase propio. **Nota de arranque**: con el PC apagado de madrugada, el drain de las
07:30 lee lo último que dejó la cadena del día anterior — suficiente para triaje matinal; la
cadena del encendido (~08:50) entrega el fresco antes de que empieces a las 9:00.

## Invariantes (lo que mantiene coherente el sistema)

1. **Ninguna ingesta escribe directa en el hub.** Todo pasa por clerk (tareas/decisiones,
   validadas contra schema) o entra como hechos por el inbox (sensor).
2. **Tuberías de una sola dirección**: Notion → hub (pull, ids de origen preservados);
   Engram local → VPS (réplica read-only, never the reverse); hechos PC → inbox.
3. **Estado del proyecto = intención de Bruno** (decisión vía triaje), **no** aritmética
   de tareas. El avance/salud sí se computa (hitos ≤14 días, logs >21 días, tareas abiertas).
4. **El sensor nunca opina**: `projects.json` lleva hechos; `status_local` es solo caché
   de fallback hasta que exista la nota del hub.
5. **Ver (dashboard) y decidir (Telegram/chat)** son canales distintos — el dashboard es
   estático a propósito para no crear un segundo escritor.

## Vocabularios aprobados

- Proyectos (hub, cerrado): `activo` / `pausa` / `archivado` — mapeo desde el dashboard:
  activo→activo, pausado→pausa, cerrado+frío→archivado, **propuesta no es estado → ítem de inbox**.
- Tareas (pendiente de definir en el cambio SDD de ai-stack): colapsar los 13 estados de
  Notion Clece (To Do/Doing/Testing STR/Testing Clece/Complete/…) al conjunto cerrado del hub.

## Piezas relacionadas

- Sensor + dashboard: `scripts/projects-dashboard.sh`, `scripts/projects-html.py`, web `:47624`
- Ledger de horas: `scripts/time-ledger.sh` + `~/bin/time-ledger` (alimenta daily-ai-timesheet → Excel Clece)
- Decisiones ADR: `doc/control-hub-architecture.md` (sección "the hub is the single source of truth")
- Pendiente ejecutar (sesión ai-stack): sección `## Tareas` + job de sync + ingesta de `projects.json`
  + espejo read-only del hub hacia el dashboard + pushear `09141a7` + paso 3.6.
