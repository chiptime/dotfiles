# cockpit-view Specification

## Purpose

PROJECTS.html (`127.0.0.1:47624`) becomes THE cockpit: four hub sections
(Today / Needs decisions / Changes / Notion Tareas) rendered EXCLUSIVELY from
`hub-state.json` v2, plus Resumen counts, a freshness badge, and the fix that finally
delivers the sibling `hub-state.json` the renderer already tries to read. Read-only
projection — zero new vault writers.

## Requirements

### Requirement: Sections rendered exclusively from v2

Every hub datum in the four sections and the Resumen MUST come from
`hub-state.json` v2, never from `dashboard.md`:

- **Today**: `estado: activo` projects with `proxima_accion` and dated hitos within
  the next 14 days (existing hub convention).
- **Needs decisions**: `inbox` counts (opencode/pi/bruno), `triage` count, and local
  proposals (`status_local: proposal`, labeled provenance "local" — proposal is not
  hub vocabulary).
- **Changes**: per-project recent `logs[]` from v2.
- **Notion Tareas**: per-project `tareas` with origin ids and sealed states
  (`abierta|en_curso|hecha`).
- **Resumen**: activo/pausa/archivado counts computed from v2 `projects[]`.

#### Scenario: Vault edit propagates to the view

- GIVEN a note's `estado` changed in the vault and the emit re-ran
- WHEN the page re-renders
- THEN the section shows the new estado without any dashboard.md involvement

#### Scenario: Resumen from v2

- GIVEN v2 reports 12 activo · 3 pausa · 1 archivado
- WHEN the Resumen renders
- THEN it shows exactly those counts computed from v2

### Requirement: Sibling hub-state.json delivery fix

The sweep MUST copy `~/hub/hub/hub-state.json` to
`~/.local/share/projects-dashboard/hub-state.json` (tmp+rename) after its hub pull —
feeding the sibling read at `projects-html.py:531` that nobody fills today
(`estado_hub`/`prioridad_hub` have always rendered empty). The renderer SHALL
populate hub columns from that copy and MUST NOT fail when it is absent.

#### Scenario: estado_hub finally populated

- GIVEN the sweep copied the v2 file next to PROJECTS.md
- WHEN the renderer runs
- THEN project rows show estado_hub/prioridad_hub from that file

#### Scenario: Missing sibling is graceful

- GIVEN no sibling `hub-state.json` exists
- WHEN the renderer runs
- THEN it completes with empty hub columns (today's fallback preserved)

### Requirement: Freshness badge

The cockpit SHALL display the age of v2's `generated` timestamp. When v2 is missing
or unparsable, the hub sections SHALL render an explicit "sin datos del hub" state
instead of stale numbers presented as current.

#### Scenario: Stale data is visible

- GIVEN v2 `generated` is older than 24 h
- WHEN the page renders
- THEN the badge marks the data stale

### Requirement: Read-only projection invariant

The cockpit MUST NOT add vault writers: static HTML only, no write affordances (no
POST/fetch to hub-mutating endpoints), and no secrets (gateway or Notion tokens)
embedded in the served HTML. Writes continue exclusively through Gertru's validated
contracts (clerk/inbox/Telegram — MAPA_INGESTAS invariantes 46-57).

#### Scenario: No secrets in static HTML

- GIVEN the fully rendered PROJECTS.html
- WHEN scanned for gateway/Notion tokens
- THEN none are present

#### Scenario: No write affordances

- GIVEN the rendered page
- WHEN inspected
- THEN it contains no control that mutates the vault
