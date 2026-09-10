# Notion Personal Backlog — Schema

Data source ID: `PENDING` — filled in by whichever session runs the Bootstrap step in `SKILL.md` and saved to the engram profile (`scope: personal`, `topic_key: tooling/notion-personal-backlog`). Do not hardcode a value here manually; treat the engram profile as the source of truth once it exists.

## Properties

| Property | Type | Options |
|---|---|---|
| Título | title | — |
| Proyecto | select | dotfiles, ai-stack, vps-hungry, recruiting-frontend, recruiting-backend, sis, odata-batch, compare-prices, voice-assistant, busqueda-vacaciones, otro/vida |
| Prioridad | select | 🔥 Alta, 🟡 Media, 🟢 Baja |
| Estado | select | 📥 Inbox, 🔨 En curso, ✅ Hecho, 🗑️ Descartado |
| Notas | rich_text | free text, optional short context |
| Creado | created_time | automatic |

## Explicitly excluded from Proyecto

Do not add `operational-centers`, `associations`, `entities-portal`, or `entities-portal-ep-comments` as Proyecto options. Those are Clece SIS work and belong in Mantenimiento SIS via `/notion-backlog` — mixing them here recreates the exact scope contamination that decision `architecture/notion-scope-boundary` (engram #7493) ruled out.

## Adding a new Proyecto option

Ask the user to confirm before adding one. Update this table and the live Notion select options together so they never drift apart.

## Default view suggestion

Board view grouped by `Estado`, secondary grouping by `Proyecto`, sorted by `Creado` descending. This is the fast visual read the personal database exists to provide — Gertru's internal memory has no equivalent view.
