# Ecosystem portal — phase 2: total and thoughtful integration

Objective: Unified project cards joining service access/health (catalogue, port 47625) with lifecycle status/next/sessions/hours (tracker, port 47624), preserving data ownership, freshness transparency, local-only safety, and seamless mobile/desktop UX.
Branch: master (pre-existing dirty working-tree preserved; no commits or publishing authorized). Delivery: ask-on-risk.
Runners: Node built-in tests (`tools/launchpad/tests/portal.test.cjs`), Python unittests (`scripts/test-projects-portal.py`, `scripts/test_projects_html.py`, `tools/launchpad/tests/test_discover.py`), Cargo (`tools/launchpad`), isolated state outside workspace.

## Pre-change baseline (verified 2026-10-19)
- Services active and responding: port 47624 (Python PROJECTS.html, HTTP 200), port 47625 (Rust launchpad, HTTP 200).
- Test suites:
  - `node --test tools/launchpad/tests/portal.test.cjs`: 18/18 passed.
  - `python3 scripts/test-projects-portal.py`: 7/7 passed.
  - `python3 scripts/test_projects_html.py`: 37/37 passed.
  - `cargo test --manifest-path tools/launchpad/Cargo.toml`: 21/21 passed.
  - `python3 -m unittest tools/launchpad/tests/test_discover.py`: 26/26 passed.
- Baseline inventory:
  - Tiles in `tiles.json`: 75 total (9 web, 12 script, 54 project).
  - Tracker entities: 54 in `projects.yaml`, 48 in `PROJECTS.md` tables.
  - Join coverage analysis:
    - 39 project tiles directly match tracker entries by ID or basename of location.
    - 15 project tiles without immediate match:
      - 3 stale non-git parent directory tiles (`amore`, `hungry`, `lucia`).
      - 1 timestamped exercise directory tile (`ai_workshop_dist_20251124-161644`).
      - 6 personal repos not yet tracked in projects.yaml (`evaluaciones`, `hermes-gertru-backups`, `opencode-infra`, `organize-ideas`, `portfolio`, `tanny`, `whisper-notes`).
      - 5 work sub-repos/streams (`associations`, `entities-portal`, `operational-centers`, `oc--test`, `apps-workspaces-operational-centers`) belonging to the `sis` group.
    - 2 web tiles match tracker projects (`opencode`, `herdr-brain`).
    - 1 web tile matches aliased tracker project (`compara-precios` ↔ `compare-prices`).
    - 15 tracker entities without direct project-tile match (mostly historical exercises like `01-analyze_logs...`, groups like `planificador`, `sis`, and repos represented as web services).

## Non-negotiable constraints (already locked)
1. Single lifecycle source of truth: `scripts/projects.yaml` + hub-state remains the lifecycle owner. The catalogue provides discovery + live health. No second manually maintained project registry.
2. Deterministic mapping: join key based on location basename / tile id ↔ tracker repo basename / key, with explicit disambiguation table for groups and renames (e.g. `Planificador-*` → `planificador`, `sis/*` → `sis`, `compara-precios` → `compare-prices`).
3. Freshness transparency: every data piece labeled with its source and freshness (tracker snapshot age, hub-state age, live health latency). Never show page generation time as data freshness.
4. Local-only & non-breaking: No ntfy, no hub-writes, no remote calls. Preserve `#compare-prices` anchor, session links, `#hub-fresh` badge. No iframes, frameworks, external CDNs.

## Specs
- S1 [Resolved - D1]: Mantener las 4 tiles (amore, hungry, lucia, ai_workshop_dist_20251124-161644) tal como están en `tiles.json`. Se tratan como tiles normales en el sistema de join.
- S2 [Resolved - D2]: Tercera vista 'Proyectos' en la cabecera compartida (`Catálogo` | `Proyectos` | `Seguimiento`). Catálogo (47625, `/`) y Seguimiento (47624, `/PROJECTS.html`) se mantienen intactos en su función especializada; la nueva vista se servirá en 47624 como `/PROYECTOS.html`.
- S3 [Resolved - D3]: Unión completa (full join): cada ficha muestra honestamente lo disponible. Si falta tile, se muestran los datos de tracking (estado, horas, next, sesiones) sin widget de servicio/salud; si falta tracking en projects.yaml, muestra sus datos de catálogo/ruta con badge de estado 'Sin clasificar' y sin inventar horas.
- S4 [Resolved - D4]: Taxonomía Trabajo vs Personal unificada con badge distintivo en cada ficha (`Personal` / `Trabajo`) y botones/chips de filtro rápido (`Todos` · `Personal` · `Trabajo`) en la barra de herramientas de la vista, combinable con búsqueda y filtros por estado.
- S5 [Resolved - D5]: Buscador específico e independiente en la vista unificada 'Proyectos', buscando en tiempo real en nombre, descripción, estado, accionable 'next', ubicación y tags sin acoplamiento entre servicios ni persistencia compleja.
- S6: Contrato de ficha unificada & DOM:
  - Cabecera: avatar/emoji, nombre canónico, badge de ámbito (`Personal` | `Trabajo`), badge de estado (`activo` | `propuesta` | `pausado` | `cerrado` | `frío` | `sin clasificar`).
  - Acceso catálogo: si servicio web (`url`), botón/enlace de apertura + dot de salud en vivo + latencia; si proyecto/script con `location`, botón de copia de ruta local con feedback; si script con `command`, copia de comando.
  - Seguimiento: descripción (`desc`), accionable pendiente (`next: 🎯 ...`), resumen de tiempo (`⏱️ X.X h total · Y.Y h semana`), sesiones OpenCode (`💬 N sesiones` con enlaces profundos seguros `ses_...`), estado git/dirty.
  - Metadatos de frescura etiquetados: snapshot age, hub age, live health polling cada 5s a `/api/tiles` (puerto 47625) con degradación elegante si no responde.
  - Anclas: cada tarjeta lleva `id="<slug>"` (ej. `#compare-prices`), preservando saltos directos.
- S7: Motor de join determinista (`scripts/projects_unified.py` / `projects-html.py`):
  - Reglas de mapeo ordenadas: 1) tabla de desambiguación explícita (grupos `planificador`, `sis`, alias como `compara-precios` ↔ `compare-prices`, `comparaprecio` ↔ `compare-prices`), 2) `tile.id == tracker_key`, 3) `basename(tile.location) == tracker_key`.
  - Cobertura medida y validada sin dependencias externas ni modificación de archivos del tracker.
- S8: Navegación compartida actualizada con 3 enlaces:
  - `<script id="portal-nav">` idéntico byte a byte en `tools/launchpad/static/index.html`, `scripts/projects-html.py` y `PROYECTOS.html`.
  - Enlaces dinámicos que resuelven el hostname actual (soporte LAN y loopback).
  - Accesibilidad completa: `aria-current="page"`, `aria-label`, foco `:focus-visible`, sin dependencias remotas (fuentes, CDN, iframes).
- S9: Suite de regresión y verificación en vivo:
  - Tests aislados en Python y Node (sin red, sin servicios vivos, sin modificar git ni emitir ntfy).
  - Verificación DOM en vivo: escritorio y móvil (375px) sin desbordamiento horizontal.

## Tasks
- T0 [done] Pre-change verification: servicios 47624/47625 activos; 5 suites verdes; inventario y cobertura de join medida.
- T1 [done] Decisiones de producto D1-D5 acordadas y registradas en el feature doc.
- T2 [done] Módulo de join determinista (`scripts/projects_unified.py`) y tests unitarios aislados (RED → GREEN: 10 tests en `scripts/test_projects_unified.py`).
- T3 [done] Actualización de la cabecera de navegación (3 vistas: Catálogo, Proyectos, Seguimiento) con `NAV_JS` compartido byte a byte; suites sincronizadas (`portal.test.cjs` 18/18, `test-projects-portal.py` 7/7).
- T4 [done] Generación de la vista unificada `PROYECTOS.html` en `scripts/projects-html.py` servida en puerto 47624 con 84 fichas unificadas, filtros por ámbito/estado, buscador en cliente y polling de salud en vivo a 47625.
- T5 [done] Suite de tests automatizados (`test_projects_unified.py`: 10 tests de estructura, escape XSS, enlaces, anclas, accesibilidad, y equivalencia de script nav).
- T6 [done] Despliegue local y verificación en vivo:
  - `./tools/launchpad/scripts/deploy.sh back` ejecutado OK; servicio activo y saludable en puerto 47625.
  - `python3 scripts/projects-html.py` generó `PROJECTS.html` y `PROYECTOS.html` en `~/.local/share/projects-dashboard/` (servidos por puerto 47624 con HTTP 200).
  - Verificación DOM real con headless Chrome:
    - Escritorio: 84 fichas renderizadas, anclas funcionales (incluyendo `#compare-prices`), búsqueda y filtros responsivos.
    - Móvil 375px: 1 columna de 343px, `windowWidth: 375`, `scrollWidth: 375`, `hasHorizontalOverflow: false`.
    - Navegación LAN-compatible con hostname dinámico.

## Log
- L1 (2026-10-19): Phase 2 iniciada. Servicios 47624 y 47625 activos y verificados. Las 5 suites pasan al 100%. Mediciones de join sobre datos reales completadas.
- L2 (2026-10-19): Decisiones de producto D1 a D5 resueltas secuencialmente con el usuario:
  - D1: Mantener las 4 tiles en `tiles.json`.
  - D2: Tercera vista 'Proyectos' en la cabecera compartida.
  - D3: Unión completa (full join) honesta para elementos asimétricos.
  - D4: Distinción Trabajo vs Personal con badges por tarjeta y filtros rápidos en cabecera.
  - D5: Buscador específico e independiente en la vista Proyectos.
- L3 (2026-10-19): Implementado motor de join determinista en `scripts/projects_unified.py` con mapeo de desambiguación para grupos (`sis`, `planificador`) y alias (`compara-precios`/`comparaprecio`). 10 tests unitarios pasan.
- L4 (2026-10-19): Integración de 3 vistas en `tools/launchpad/static/index.html` y `scripts/projects-html.py`. Despliegue local de backend launchpad y re-renderizado de `PROJECTS.html` y `PROYECTOS.html`.
- L5 (2026-10-19): Verificación en vivo completada en puertos 47624 y 47625. Las 6 suites automáticas pasan al 100% (119 tests en total). Verificación CDP en viewport 375px confirma 0 overflow horizontal y comportamiento interactivo de filtros.
