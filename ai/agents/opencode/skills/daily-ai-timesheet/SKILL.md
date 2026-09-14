---
name: daily-ai-timesheet
description: "Trigger: excel de ia, rellenar el excel, diario, parte de horas, horas ia, timesheet, log diario, seguimiento ia. Generate the daily pasteable TSV row(s) for the Clece AI-work Excel."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# Daily AI Timesheet (Clece)

## Activation Contract

Load when the user asks for the daily (or weekly) AI-work Excel entry: "rellenar el excel de hoy", "parte de horas", "log diario", "excel de ia". Works from any SIS app workspace.

## Hard Rules

- Rows in Spanish, functional language. NO technical jargon (never: SDD, backoff, worktree, refactor, MCP, JWT, retry policy, API).
- One row per task/day. `Tarea`: one line, start with a verb form ("Implementar", "Análisis y corrección", "Pruebas", "Soporte", "Desarrollo", "Migración").
- Columns, tab-separated: `Fecha | Tarea | Horas Estimadas | Horas Reales con la IA | IA | Modelo usado | Comentarios`.
- `Fecha` as D/M/YYYY. Hours as integer or with `,5` (6,5). Never h:mm.
- `IA` = "Codex"; `Modelo usado` = "GPT-5.5" unless the user overrides that day.
- `Comentarios`: short, casual ("Planificación + ejecución", "Horas extra", "Solo consulta").
- Real hours must cover the fully occupied day: 8h jornada + REUS + extras. Never under-report; ask when unknown.
- `Horas Estimadas` = time WITHOUT AI, typically 2–4x the real time; keep it plausible, conservative.

## Decision Gates

| Situation | Action |
| --- | --- |
| Git/engram show the day's work | Draft rows, then confirm gaps with the user |
| REUS or extra hours unknown | Ask in ONE question round (max 2 questions), then emit |
| Multi-day request ("la semana") | One row per task/day, weekly totals at the end |
| User corrects a row | Apply it and re-emit the full TSV |

## Execution Steps

1. Collect evidence, in priority order: (a) `time-ledger day <D/M/YYYY>` and `time-ledger tsv <D/M/YYYY>` — PRIMARY source when the day has entries; (b) `git log --author=ChipTime --since=<day 00:00> --until=<day+1 00:00>` in `./frontend` and `./backend` if present; (c) `mem_context` for projects `operational-centers`, `associations`, `entities-portal`, `sis`.
2. Translate ledger rows / commits / sessions into user-visible outcomes (a technical commit becomes the business result).
3. Draft rows with estimated vs real hours. Ledger `ai` hours map to `Horas Reales con la IA`; `manual` entries (REUS, reuniones) map to the same column.
4. **Reconciliation (backfill)**: if a day shows real activity (git commits / Engram sessions) but has NO ledger rows, propose the missing rows to Bruno in one round, then insert them with `time-ledger add-ai` — this heals entries from chats that were closed without the session-close convention. Then ask only for remaining data (REUS hours, extras) and emit.

## Output Contract

One fenced ``` block with pasteable TSV (include the header row only if the user asks), then a single line with totals: estimated vs real. Nothing else inside the block.

## References

- `assets/format-example.md` — approved worked example of one full week.
