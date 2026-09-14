---
name: hub-session-close
description: Trigger: cerrar sesión, cierre de sesión, cierra el ciclo, session close, hub sync, archive la sesión, end of session summary. Drop the session summary into the control-hub vault inbox (~/hub) and push — create-only, own namespace, never touches the structured zone.
---

# hub-session-close — feed the Control Hub when a session ends

The control hub (`~/hub`, a clone of `gertru-lab/gertru-workspace`) is Bruno's global
project state. Local agent sessions feed it ONE way: a session-close summary item in
the inbox namespace `pi/`. Gertru's 07:30 drain picks it up, applies the
deterministic parts, and routes anything ambiguous to human triage.

## When to run

- At the end of a session that produced meaningful work on a project (state change,
  milestone, decision, bugfix worth tracking), or
- When the user asks to close/archive the session ("cierra", "cierre de sesión",
  "cierra el ciclo", "hub sync").

Do NOT run for trivial sessions (read-only questions, quick lookups, chat).

## Preconditions

1. `~/hub` must exist and be a git repo (the vault clone). If missing, SKIP with a
   one-line note — do not clone it yourself.
2. The session must have something real to say: a project slug, what changed, what is
   next. If not, skip silently.

## The write contract (non-negotiable)

- **Create-only, own namespace:** the ONLY path you may write is
  `~/hub/hub/inbox/pi/<YYYY-MM-DD>-<short-slug>.md` — a NEW file that does not exist.
- **Never** touch `hub/proyectos/`, `hub/dashboard.md`, `hub/ecosistema.md`,
  `hub/inbox/_triage/`, `cerebro/`, or any other vault content.
- **Git ops:** `pull --rebase` → pathspec-scoped `add` of YOUR file only → conventional
  commit → `push`. If the rebase stops on a conflict: STOP, leave the tree as is, and
  report — never force, never reset --hard, never push --force.

## Steps

1. `git -C ~/hub pull --rebase --quiet` (if it conflicts → stop and report).
2. Write `~/hub/hub/inbox/pi/<YYYY-MM-DD>-<short-slug>.md` with this shape:

   ```markdown
   # <project-slug> — <one-line what happened>

   - **Cambio:** <state change / milestone / decision, 1-2 lines>
   - **Siguiente:** <next concrete action, or "—" if none>
   - **Evidencia:** <commit hash / file / test result>
   - De: sesión pi local · <YYYY-MM-DD>
   ```

   Keep it under 15 lines. Facts only — the drain (Gertru) decides what to apply;
   if the item is ambiguous it goes to human triage, so write it unambiguous.
3. `git -C ~/hub add hub/inbox/pi/<file>` (pathspec-scoped — ONLY your file).
4. `git -C ~/hub commit -m "inbox(pi): <project> — <one-line>" -- hub/inbox/pi/<file>`
   (conventional message, no AI attribution).
5. `git -C ~/hub push --quiet`.
6. Confirm in one line: item path + push result.

## Failure modes

| Situation | Action |
|---|---|
| `~/hub` missing | Skip + note (maintainer clones it) |
| Rebase conflict | Stop, report, leave tree intact |
| Push rejected (non-fast-forward) | One retry after another `pull --rebase`; then stop and report |
| File already exists | Never overwrite — pick a different slug suffix |

## Why this is safe

Two writers never collide because everyone only creates NEW files in their own
namespace. The structured zone has a single writer (Gertru via validated ops). Bruno's
Obsidian mirror is pull-only. Git history is the audit trail.
