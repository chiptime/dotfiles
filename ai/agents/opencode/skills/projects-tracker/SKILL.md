---
name: projects-tracker
description: "Trigger: archiva y cierra, cierra sesión, done por hoy, listo, cerrar el día, actualizar estado de proyecto, projects.yaml, tracker de proyectos, PROJECTS.md, dashboard de proyectos, unclassified, triaje de repos. Session close and tracker update are ONE flow: after meaningful project work, capture AI hours and update scripts/projects.yaml — never close a session without it."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# Skill: projects-tracker

## Activation Contract

Load when a work session touched a project meaningfully (implementation, bugfix, decision, pause/resume/close) and the session is wrapping up, OR when the user asks about project status, the tracker, or "unclassified" repos.

**Session-close phrases are a HARD trigger**: "archiva y cierra", "cierra", "cierra sesión", "done", "listo por hoy", "that's it" (or equivalents) MUST load this skill when the session did real project work — even though the user only asked to close. Updating the tracker, the time ledger and the snapshot is part of closing, not an extra step. Skipping it because "the user only said close" is a session-close bug.

The tracker lives in `~/.dotfiles`:
- `scripts/projects.yaml` — human layer: one block per repo basename (scope, status, desc, next)
- `scripts/projects-dashboard.sh` — git sweep + merge; run as `projects` (symlink in `~/bin`)
- Snapshot: `~/.local/share/projects-dashboard/PROJECTS.md` (machine-local, never committed)

## Hard Rules

- Statuses are exactly: `active | proposal | paused | closed | cold`. Never invent new ones.
- One block per repo **basename** (the script matches by basename, first discovery wins).
- YAML is a simple 2-space-indented subset (`key: value`); the parser is not a YAML engine. No anchors, no lists, no multiline.
- Never put secrets in `projects.yaml`.
- The snapshot output directory is machine-local state; never commit it.
- Do not touch unrelated dirty blocks in `~/.dotfiles` when committing (Bruno often has parallel staged work).

## Execution Steps

Use the `session-close` CLI (`scripts/session-close.py`, symlinked in `~/bin`). It does the mechanical part; the agent only writes `desc`/`next`.

1. From the session's working directory, run `session-close` with no arguments. It prints the detected OpenCode session id, active hours (message gaps over 30 min are idle), the repos with commits since the session started, and the current tracker block of this project. Never log hours for read-only/conversational sessions: skip the close entirely for those.
2. Pick the tracker key: the repo **basename** the work landed in (a worktree `<repo>-worktrees/<name>` belongs to `<repo>`).
3. Run `session-close <project> --next "<concrete next action>"`, adding `--desc "<one-liner>"` only if it is stale and `--status <s>` only if the lifecycle changed. `next` must be actionable, not a wish. Use `--hours` only to correct the estimate, `--note` for a better ledger note than the session title. Several projects touched: run it once per project with `--hours` split between them.
> **Precedencia del hub (2026-09-15):** si el repo ya tiene nota en el hub
>(`hub/proyectos/<slug>.md`), el `status` del YAML es SOLO caché de fallback —
> el dashboard pinta el estado del hub. No pases `--status` para repos con nota;
> el estado vive en el hub vía Telegram/clerk.
4. The CLI then appends `time-ledger add-ai` with the session id, regenerates the snapshot (`projects`), prints the section counts and the `improve-system --status` line, and commits ONLY `scripts/projects.yaml` when the current time is outside working hours (Mon–Thu 09:00–19:00, Fri 08:00–16:00; weekends are outside). Inside working hours it skips the commit and says so. It never pushes. Use `--no-commit` to skip it always, `--dry-run` to preview.
5. If the cadence line reports `CADENCIA VENCIDA`, append a gentle one-liner at the very end of your response:
   > 💡 *Han pasado N días desde la última auditoría de `/improve-system`. Cuando tengas un hueco, ejecútalo para revisar el feedback loop y gotchas de la semana.*
6. If the sweep shows new repos (a project was cloned/created), create their blocks with `session-close <repo> --desc "..." [--scope work]` or leave them for the "Unclassified" triage section — never leave work repos silently unclassified after a triage round.
7. Changes to `scripts/projects-dashboard.sh` itself are never committed by the CLI; commit them only when the user asks.

## Status Vocabulary

| Status | Meaning |
|---|---|
| `active` | Worked on within the last ~2 weeks or actively maintained |
| `proposal` | Has a pending proposal/decision awaiting Bruno (apply or discard) |
| `paused` | Real project, intentionally on hold — must keep a `next` for resuming |
| `closed` | Finished; no further action expected (work streams, done cycles) |
| `cold` | Legacy, one-off, exercise, or untouched checkout |

## Output Contract

Return what `session-close` printed: the YAML blocks changed (before → after), the ledger entry, the regenerated snapshot path, the section counts (active/proposal/paused/closed/cold/unclassified), and whether the tracker was committed or why not.
