# Ecosistema portal — phase 1 UI

The two local pages now read as one product: a shared **Ecosistema** header with
**Catálogo** (Launchpad, port 47625, `/`) and **Seguimiento** (project tracker,
port 47624, `/PROJECTS.html`). Each page keeps its own backend and data; phase 1
only unifies navigation, design language and the catalogue filters.

## Quick path

1. Open either page; the header link for the current page carries `aria-current="page"`.
2. Use **Catálogo** to find and open services or copy a script command / project path.
3. Use **Seguimiento** to see project state, hours, sessions and the hub cockpit.

## Navigation

| Aspect | Decision |
|--------|----------|
| Links | Built at load time from `window.location` (`protocol` + `hostname` + fixed port), so the portal works over the LAN and not only on loopback. |
| No JavaScript | The static `href` is a fallback only (`/` or `/PROJECTS.html` for the current page, `http://localhost:<port>/…` for the other one). |
| `file:` or empty host | Falls back to `http://localhost`. |
| Sharing | The `<script id="portal-nav">` block is duplicated byte for byte in `static/index.html` and `scripts/projects-html.py` (`NAV_JS`); the Python test fails if they drift. |
| Isolation | No iframe, proxy, framework, external font/script/stylesheet or remote call. |

## Catálogo: filters

Type and topic are separate filters and combine with the search text (AND).

| Filter | Values | Notes |
|--------|--------|-------|
| Tipo | Todos · Servicios · Scripts · Proyectos personales · Proyectos de trabajo | Personal/work are project types, so they never appear as topics. A project with neither tag only shows under Todos. |
| Tema | Todos los temas · every other tag (IA, SAP, Teams, Voz, custom…) | Built from the data; an active topic stays listed (count 0) so it can be cleared. |
| Texto | name, description, command, host, location, docs, tags | Trimmed and case-insensitive. |

- Chips are buttons with `aria-pressed`; pressing the active one clears it. Counts show how many cards each chip would leave given the other filters and the text.
- **Limpiar filtros** (toolbar and no-results state) resets type, topic and text and focuses the search box.
- States: loading, empty catalogue, no results, and a load-error alert that keeps the last good data visible while polling retries.

## Catálogo: actions and polling

| Card | Action | Feedback |
|------|--------|----------|
| Service | Opens `url` in a new tab (`noopener`). | Health dot, latency/`down` text and error text. |
| Script with `command` | Copies the command. Never executes it. | `Copiado ✓` / `No se pudo copiar` on the card and in a polite live region. |
| Project with `location` | Copies the path. | Same. |
| Script without `command`, project without `location` | None: plain card, no role, no tab stop, no hint. | — |

The page polls `/api/tiles` every 5 seconds. Markup is only rewritten when it changes, and the
focused chip or card is re-focused by `data-key`, so polling does not reset the query, filters,
focus or the copy feedback. Insecure (LAN) origins use the `execCommand` clipboard fallback.

## Proyectos (fase 2)

La nueva vista unificada (`/PROYECTOS.html` servida en el puerto 47624 por `projects-dashboard`)
presenta fichas unificadas que combinan:
- **Acceso y salud del servicio** (Catálogo): enlace de apertura y dot de salud en vivo (polleado cada 5s a `http://<host>:47625/api/tiles`), o copia de ruta/comando al portapapeles.
- **Ciclo de vida y tracking** (Seguimiento): estado (`activo`, `propuesta`, `pausado`, `frío`, `cerrado`, `sin clasificar`), siguiente acción accionable (`next: 🎯 ...`), horas acumuladas y de la semana (`⏱️`), y enlaces a sesiones de agentes (`💬`).
- **Mapeo determinista:** basado en `scripts/projects_unified.py` sin crear registros duplicados. Reglas: desambiguación explícita (grupos como `planificador`, `sis`, alias como `compara-precios` / `comparaprecio`), coincidencia de id exacto, o coincidencia por nombre base de la ruta (`basename(location)`).
- **Frescura etiquetada:** cada ficha muestra la edad del snapshot, del hub y de la salud en vivo sin falsear procedencias.
- **Filtros y búsqueda:** filtros rápidos por ámbito (Personal / Trabajo), estado del proyecto y buscador en tiempo real en cliente.

## Seguimiento

La vista de seguimiento detallada (`/PROJECTS.html` en puerto 47624) se mantiene intacta en su
función original: reporte tabular de git, cockpit de hub y sesiones multi-agente. Se actualizó su
cabecera de navegación para incluir el enlace a **Proyectos**.

## Verification

```bash
node --test tools/launchpad/tests/portal.test.cjs
python3 scripts/test-projects-portal.py
python3 scripts/test_projects_html.py
python3 -m unittest scripts/test_projects_unified.py
cargo test --manifest-path tools/launchpad/Cargo.toml
python3 -m unittest tools/launchpad/tests/test_discover.py
```
