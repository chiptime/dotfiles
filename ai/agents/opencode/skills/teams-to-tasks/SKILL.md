---
name: teams-to-tasks
description: "Trigger: teams, mensajes de teams, canal de teams, genera tareas desde teams, teams to tasks. Read Microsoft Teams web via the persistent-profile playwright_teams MCP and convert messages into Notion tasks."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# Skill: teams-to-tasks

## Activation Contract

Load when the user asks to read Microsoft Teams messages, chats, or channels and derive tasks from them (e.g. "genera tareas de Teams", "revisa el canal X y apunta lo pendiente").

## Hard Rules

- Use ONLY the `playwright_teams_*` MCP tools (persistent profile). NEVER the isolated `playwright_*` server: it holds no Teams session.
- READ-ONLY in Teams: never send, reply, edit, delete, or react to any message.
- Every created task's Notas MUST start with its stable source fingerprint (`teams:<chat>:<author>:<YYYY-MM-DD-HH:MM>`); never create a task whose fingerprint already exists in the Tareas data source.
- Never create Notion items before showing the proposed task list and getting explicit user confirmation — EXCEPT in autonomous mode (see Decision Gates).
- One task per actionable item. Every task cites its source: channel/chat, author, date, short quote.
- Do not persist full Teams message contents in memory or Notion beyond the short source quote.

## Decision Gates

| State | Action |
|---|---|
| Login / 2FA screen after navigating | STOP — ask the user to log in inside the opened browser window; poll `playwright_teams_browser_snapshot` until the Teams home appears, then continue |
| Target channel/chat not identified | Ask ONE question naming the channel or chat before navigating |
| No actionable items after extraction | Report that no tasks were extracted; do NOT touch Notion |
| Extracted item is Clece SIS work | Route it per the notion-personal-backlog redirect rules (`/notion-backlog`), never the personal backlog |
| Run is scheduled/unattended (cron) and the prompt marks MODO AUTÓNOMO | Skip the confirmation gate: create directly as 📥 Inbox. Dedupe by fingerprint (Hard Rules). If the login gate triggers, ABORT immediately with `SESION_EXPIRADA: login manual necesario` (cannot re-auth unattended). Close the browser and return a 1-3 line run summary |
| User asks to pause, resume or check the scheduled reader | Pause: `touch ~/.local/state/teams-to-tasks/PAUSE` · Resume: `rm` that file · Status: `cat ~/.local/state/teams-to-tasks/last_run` + `tail -20 ~/.local/state/teams-to-tasks/cron.log` |

## Execution Steps

1. `playwright_teams_browser_navigate` to `https://teams.microsoft.com`, then `playwright_teams_browser_snapshot`.
2. Apply the login gate if needed; otherwise continue.
3. Discovery (efficient first pass): click the search combobox, type a high-frequency term (e.g. `que`), press Enter; on the results page open `Date filter` and pick `Today`. The results index which chats had activity today — open only those. Caveat: search indexes message text only, never meeting events or messages missing the term; combine with the chat-list sweep for full coverage.
4. For each relevant chat: navigate to it, scroll the message pane until the requested time range is visible, then snapshot.
5. Extract messages (author, timestamp, text) and consolidate consecutive or related messages into threads. If a message carrying a candidate has an attached image that matters, screenshot the image element (`playwright_teams_browser_take_screenshot`, element target), read the saved PNG with the model's native vision (read tool; fallback: `zai-vision` MCP) and prepare a 1-2 line description for Notas.
6. Derive candidate tasks: explicit asks, deadlines, blockers, follow-ups. Build each candidate's stable fingerprint `teams:<chat>:<author>:<YYYY-MM-DD-HH:MM>` (normalize relative timestamps against today). Query the Tareas data source for that fingerprint in Notas and drop duplicates. Then present the proposed list and WAIT for user confirmation (or proceed per the autonomous gate).
7. Create one row per task in the Tareas data source (data_source_id in the `notion-personal-backlog` engram profile): Notas = fingerprint on the first line, then the source quote (chat, author, date) and the image description if any. If the message implies a deadline (hoy, mañana, explicit date), set the `Vence` date property.
8. Close the browser with `playwright_teams_browser_close` to free RAM — the browser is on-demand and must not stay resident.
9. Return the created page URLs with source attribution.

## Digest Mode

When invoked in MODO DIGEST (scheduled 18:30 run or explicit request):

1. Sweep ALL of today's messages (search `que` + Date=Today — no time window), applying the login gate.
2. Produce a structured digest: **Temas tratados**, **Decisiones**, **Dudas abiertas**, **Tareas** (already-captured ones referenced by title).
3. Notion: locate the page `📡 Digest Teams` under Personal (1) (create it if missing) and append a `heading_2` with today's date followed by the digest as bullets. Never duplicate a heading for a date that already exists.
4. Close the browser and return a 1-line summary.

## Output Contract

Return: tasks created (title + Notion URL + source: channel, author, date), or the confirmation-gated extraction list if the user did not confirm. On login timeout, return exactly what the user must do.

## References

- `/home/bruno/.config/opencode/skills/notion-personal-backlog/SKILL.md` — task creation flow and Notion schema.
- `/home/bruno/.config/opencode/skills/mcp-trigger-protocol/SKILL.md` — MCP selection matrix.
- `/home/bruno/.config/opencode/opencode.json` — `playwright_teams` server definition (persistent profile).
