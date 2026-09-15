---
name: projects-tracker
description: "Trigger: actualizar estado de proyecto, cerrar sesión de proyecto, projects.yaml, tracker de proyectos, PROJECTS.md, dashboard de proyectos, unclassified, triaje de repos. Keep the global project tracker truthful: update scripts/projects.yaml after meaningful project work, triage newly discovered repos, and regenerate the snapshot."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# Skill: projects-tracker

## Activation Contract

Load when a work session touched a project meaningfully (implementation, bugfix, decision, pause/resume/close) and the session is wrapping up, OR when the user asks about project status, the tracker, or "unclassified" repos.

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

1. Identify the repo the session worked on (basename under `~/Code`).
2. **Time ledger**: if the session did real work, append AI hours — `time-ledger add-ai <D/M/YYYY> <project-basename> <hours> "<short note>" <ses_id>`. Take `ses_id` from the current session (it appears in every mem_save response as `Session: ses_...`); it deep-links the dashboard to this conversation. Approximate hours confidently from session window; Bruno corrects at Excel time. Never log hours for read-only/conversational sessions.
> **Precedencia del hub (2026-09-15):** si el repo ya tiene nota en el hub
>(`hub/proyectos/<slug>.md`), el `status` del YAML es SOLO caché de fallback —
> el dashboard pinta el estado del hub. No lo actualices para repos con nota;
> el estado vive en el hub vía Telegram/clerk.

3. Update only that project's block: `status` if the lifecycle changed, `desc` if the one-liner is stale, `next` with the concrete next action (or drop `next` if none). `next` must be actionable, not a wish.
4. If the sweep would show new repos (a project was cloned/created), add blocks for them or leave them for the "Unclassified" triage section — never leave work repos silently unclassified after a triage round.
5. Rerun `projects` so the snapshot regenerates.
6. If the tracker files themselves changed and the user authorized a commit, commit ONLY `scripts/projects.yaml` and/or `scripts/projects-dashboard.sh` with a conventional message.

## Status Vocabulary

| Status | Meaning |
|---|---|
| `active` | Worked on within the last ~2 weeks or actively maintained |
| `proposal` | Has a pending proposal/decision awaiting Bruno (apply or discard) |
| `paused` | Real project, intentionally on hold — must keep a `next` for resuming |
| `closed` | Finished; no further action expected (work streams, done cycles) |
| `cold` | Legacy, one-off, exercise, or untouched checkout |

## Output Contract

Return: the YAML blocks changed (before → after), the regenerated snapshot path, and the new section counts (active/proposal/paused/closed/cold/unclassified).
