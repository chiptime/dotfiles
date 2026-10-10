# hub-state-v2 Specification

## Purpose

`hub-state.json` v2 is the single deterministic machine index of the cockpit: a merge
of (a) the vault project notes `hub/proyectos/*.md` and (b) the local repo registry
`projects.json` delivered at `hub/inbox/bruno/`. Produced on the VPS by the extended
`hub-state-emit.mjs` with ZERO LLM in the data path. `dashboard.md` is never an input.

## Requirements

### Requirement: Deterministic v2 emit from vault files

The emit MUST parse vault notes directly — frontmatter (`estado`, `prioridad`,
`ámbito`, `próxima-acción`, `actualizado`), hitos (`- [ ] (YYYY-MM-DD)` in note
bodies), `## Tareas` glyphs (`[ ]`/`[/]`/`[x]` + `notion:<id>`), `## Log` entries —
and the inbox by filesystem listing (`hub/inbox/*` excluding `.gitkeep`; `_triage`
counted separately). It MUST NOT read `dashboard.md`.

The output schema MUST be `hub-state/v2`: top-level `generated` (ISO-8601),
`projects[]` (per-project: `slug`, `estado`, `prioridad`, `ambito`,
`proxima_accion`, `actualizado`, `hitos[{fecha,texto}]`,
`tareas{abiertas,en_curso,hecha,items[{id,titulo,estado,origin}]}`, `logs[]`),
`inbox{opencode,pi,bruno}`, `triage`. The emit SHALL write atomically (tmp+rename)
and remain the ONLY writer of `hub-state.json`. Exit 0 = written; exit 2 = unreadable
vault (`proyectos/` missing or unreadable); zero partial writes.

#### Scenario: Emit never depends on dashboard.md

- GIVEN a vault with `proyectos/*.md` and a stale `dashboard.md`
- WHEN the emit runs
- THEN `hub-state.json` reflects only parsed notes + registry
- AND deleting `dashboard.md` and re-running yields identical output (modulo `generated`)

#### Scenario: Missing projects.json is not fatal

- GIVEN `hub/inbox/bruno/projects.json` is absent
- WHEN the emit runs
- THEN v2 lists hub-only entries (flagged `local_repo:false`) and exits 0

### Requirement: Merge rules with hub precedence

The emit MUST merge notes and registry on normalized slug (trim, lowercase, `.md`
stripped). Where both sources exist, the hub note's `estado` SHALL win
(hub-precedence); local-only fields (branch, dirty, `status_local`) fill the merged
entry. Provenance MUST be explicit: hub-only projects flagged `local_repo:false`;
local repos without a hub note flagged `hub_note:false`.

#### Scenario: Hub precedence on shared slug

- GIVEN `projects.json` says `llm-hub` is paused and the hub note says `estado: activo`
- WHEN the merge runs
- THEN the merged entry carries `estado: activo` with provenance `merged`

#### Scenario: Hub-native project without local repo

- GIVEN `piso-familiar.md` has no matching `projects.json` entry
- WHEN the merge runs
- THEN v2 lists `piso-familiar` flagged `local_repo:false`

### Requirement: Canonical ordering and byte stability

Projects SHALL be ordered estado (activo→pausa→archivado, unknown last) → prioridad
(absent last) → slug, comparing by codepoints (no localeCompare). The same vault
input MUST produce byte-identical output except the `generated` timestamp.

#### Scenario: Byte stability

- GIVEN an unchanged vault
- WHEN the emit runs twice
- THEN the two outputs differ only in `generated`

### Requirement: Emit trigger cadence — drain + sweep on-demand

The emit SHALL run (a) at the VPS drain (07:30 Europe/Madrid, after regen-dashboard —
existing step) and (b) on-demand: after the local sweep delivers `projects.json`
(scp + docker cp), it triggers the remote emit via one
`ssh <vps> docker exec openclaw node hub-state-emit.mjs <hub-dir>` call.
Chosen over a VPS path-unit: it reuses the proven ssh/docker-cp delivery path, adds
zero VPS scheduling outside `jobs.yaml`, and shares the delivery step's best-effort
failure domain.

#### Scenario: Sweep-triggered refresh

- GIVEN the sweep delivered a fresh `projects.json` and triggered the remote emit
- WHEN the emit completes on the VPS
- THEN the pushed v2 reaches the `~/hub` clone at its next pull

#### Scenario: Remote trigger failure is non-fatal

- GIVEN the VPS is unreachable during the sweep
- WHEN the trigger ssh fails
- THEN the sweep logs and continues; the 07:30 drain still regenerates v2

## Determinism acceptance

1. **dashboard.md-deletion test**: with a fixed vault, delete `hub/dashboard.md`, run
   the emit, restore — v2 MUST be byte-identical (modulo `generated`).
2. **Byte-stability test**: two consecutive emits over the same vault MUST differ only
   in `generated`. Both tests belong in the emit test suite (ai-stack).
