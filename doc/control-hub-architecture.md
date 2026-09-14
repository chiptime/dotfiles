# Control Hub Architecture — Single-Writer Vault with Inbox Capture

Architecture decision record for Bruno's global control hub: one git-versioned vault
that every assistant (Gertru, opencode local/VPS, pi, Obsidian) reads and feeds without
merge risk, without manual git, and without invading the agent's living space.

Decided: 2026-09-13 · Status: design closed, implementation pending formal SDD proposal
Traceability: Engram topic key `architecture/hub-control-ecosistema` (observations #8613–#8629, project `dotfiles`/`ai-stack`/`bruno`)

---

## Problem

Six unregistered entry points hold project state with no single source of truth:
OpenClaw VPS (Gertru + `cerebro/`), Notion personal, Notion work (Clece), opencode VPS,
opencode local, pi local — plus Engram (agent memory) and two local trackers
(`scripts/projects.yaml`, generated `PROJECTS.md`). Result: no global state, no
priorities, noise, and project surveys that only an agent session can reconstruct.

## The decision (one line)

**The vault already exists: `gertru-lab/gertru-workspace`.** Gertru's workspace inside
the OpenClaw container is a git repo (branch `master`, GitHub origin, daily snapshot via
`backup.sh`, living on named volume `openclaw-state`). We do not build a new vault —
we add a delimited, well-structured room (`hub/`) inside it and route all writes
through fixed, validated operations.

Rejected alternatives:
- **New vault / repo** — a seventh entry point; ignores that the embryo exists.
- **Notion as hub** — breaks file-native access for every agent; network-dependent.
- **Bind-mount gymnastics** — unnecessary; the workspace is already a git repo with remote.

## Role map

| System | Role |
|---|---|
| `gertru-lab/gertru-workspace` (`hub/`) | **Single source of truth** for project management state |
| `cerebro/` (rest of workspace) | Gertru's personal second brain — untouched by the hub |
| Engram | Agent operational memory (session state) — never the human dashboard |
| Notion personal | Inbox + mobile push reminders (satellite, not source) |
| Notion work (Clece) | Corporate boundary — untouched |
| `~/.dotfiles/scripts/projects.yaml` + `PROJECTS.md` | Repo-level technical layer (branch, dirty, last commit) — stays as is |
| Morning-brief / weekly-review | Consumers: read the vault, list pending triage |

## Hub layout

```
gertru-workspace/
├── hub/                          ← THE ROOM (ours, created once by us)
│   ├── inbox/                    ← capture door: multi-writer, safe by construction
│   │   ├── opencode/             ←    each writer ONLY creates new files:
│   │   ├── pi/                   ←    <YYYY-MM-DD>-<slug>.md in its own namespace
│   │   ├── bruno/
│   │   └── _triage/              ←    disputed items awaiting human election
│   ├── proyectos/                ← structured zone: clerk writes ONLY here
│   ├── dashboard.md              ← global state, filterable queries
│   └── ecosistema.md             ← apps, integrations, automations map
├── cerebro/                      ← Gertru's brain: intocable
└── (openclaw.json, credentials, identity — outside git entirely)
```

## Write rules (the 10% model)

| Zone | Writers | Rule |
|---|---|---|
| `hub/inbox/<writer>/` | Everyone (agents, Bruno drops) | Create-only, own namespace → content conflicts impossible by construction |
| `hub/proyectos/`, `dashboard.md`, `ecosistema.md` | Clerk (Gertru) only | Fixed validated operations: `set-estado`, `add-hito`, `add-log`, `nuevo-proyecto`, `archivar`, `drain-inbox` |
| `hub/inbox/_triage/` | Nobody applies anything | Items wait for Bruno's explicit election |
| Rest of workspace | Gertru only | Hub never touches it (the 90%) |
| Local clone | Nobody pushes. Ever | Obsidian + obsidian-git configured **pull-only** |

Gertru enters `hub/` only through the service door: her judgment goes into **what**
she writes (mature states, extra info); schema validation guarantees **how**.

## The cycle

```
1. CAPTURE   fast + dirty → inbox/<writer>/<date>-<slug>.md
             (Telegram dictation bypasses inbox: Gertru applies via clerk directly)
2. DRAIN     clerk drains inbox at session end (+ daily safety net):
             clean deterministic item → applied to proyectos/ + dashboard, file removed
             ambiguous/contradictory  → moved to _triage/ + Telegram notification
3. TRIAGE    Bruno elects from his phone ("the second one", "merge them", "drop #1")
             → clerk applies the election (git remembers everything)
4. CONSULT   dashboard (Obsidian, auto-pull) · morning-brief lists pending triage
```

Human interrupted only by a real decision; the machine applies everything mechanical.

## Why merges are impossible

| Layer | Guarantee |
|---|---|
| Inbox content | Writers create disjoint new files only — no shared file, no conflict, ever |
| Structured zone | Single writer (clerk) — one head, nothing to merge |
| Git level (non-fast-forward push) | Resolved by `pull --rebase` before every push — invisible plumbing |
| Rebasing safety | `pull --rebase` **replays** local commits on top of remote (nothing dropped); real overlaps stop loudly and ask — it never silently overwrites |

**Proscribed operations:** `push --force`, `reset --hard`. The plumbing contract is
exactly `pull --rebase` + `push`, nothing else.

## Model strategy

| Work | Model tier |
|---|---|
| Clerk operations, inbox drain, state updates | Small/cheap model (structured ops against a fixed schema — small models excel here) |
| Judgment: reprioritize, weekly-review synthesis, what to archive | Large model |
| Local agents closing a session | Cheap tier (flash class proven on full SDD cycles in agy-bridge) |

Also fixes the pending VPS config bug: default model `glm-5.3-highspeed` is not in
the plan (exit-3 delegation failures) — route/pin a valid cheap tier.

## Implementation phases

| # | Phase | Contents | Status |
|---|---|---|---|
| 1 | Control schema | `hub/` tree, project frontmatter (`estado`, `prioridad`, `ámbito`, `próxima-acción`, `actualizado`), `dashboard.md` queries, `ecosistema.md` | Approved — pending formal proposal |
| 2 | Gertru clerk | Clerk skill (fixed ops + schema validation), `drain-inbox` op, model routing, `pull --rebase` added to `backup.sh`, push-per-session convention | Approved — pending formal proposal |
| 3 | Local mirror | Clone + Obsidian (pull-only sync), local agents drop session-close items to `inbox/<writer>/` and push | Approved — pending formal proposal |
| 4 | Noise zeroing | Archive dead projects (`estado: archivado`), absorb `projects.yaml` conventions where they overlap | Approved — pending formal proposal |

**Migration notes (one-time):** move `cerebro/proyectos/` → `hub/proyectos/`
(same content, cleaner boundary); repoint payloads (morning-brief §4, weekly-review
§6–7 read exact paths); everything lives in ai-stack, which we own.

## Safety nets

| Scenario | Net |
|---|---|
| Bad write to `hub/` (by Gertru, us, or anyone) | Git history = audit + `git revert` |
| Container recreation | Named volume `openclaw-state` + daily backup + GitHub remote |
| Backup/deploy failure | Existing fail-soft semantics (OpenCode backup never breaks) |
| Secrets | `openclaw.json`/credentials live outside the versioned workspace; hub holds none |

## Non-goals

- No new vault, no Notion-hub, no bind mounts, no manual git for Bruno.
- Not touching: `cerebro/` personal content, `openclaw.json`/credentials, Notion work,
  `PROJECTS.md`/`projects.yaml` repo-technical layer.

## Next step

Formal SDD proposal on `ai-stack` (clerk skill + payload repoint + `backup.sh` line +
`hub/` structure definition), preceded by the standard session preflight choices.
Implementation lands in ai-stack; the vault change is content-only inside `hub/`.

## Decision: the hub is the single source of truth for project intent (2026-09-14)

Bruno confirmed the goal: ONE place to talk to and see the global state of everything
running in parallel — that place is the hub. Consequences:

- **The projects dashboard is a sensor + viewer, not a catalog.** It owns facts
  (repos, git state, sessions, hours) and renders the hub's intent beside them.
- **Sensor contract:** the dashboard emits
  `~/.local/share/projects-dashboard/projects.json` (`projects-sensor/v1`) —
  facts only, including projects discovered solely by OpenCode sessions
  (`uncatalogued: true`), which is the autodiscovery feed. Transport to the VPS:
  the 08:00 cron scps the JSON into `hub/inbox/bruno/` (the hub's ingestion
  interface). pc-eyes keeps its real job: OpenClaw working on the PC.
- **Task materialization (one collector):** Gertru pulls BOTH Notions directly
  via her stdio MCP — her token has access to the personal Tareas DB and to
  Mantenimiento SIS (Clece) — and materializes the `## Tareas` section through
  the clerk. No inbox hop and no PC involvement for tasks; the sensor keeps
  machine facts only (repos, sessions, hours, autodiscovery). One-way pull,
  never hand-edited, source ids preserved. Their schema already fits the model:
  `ID` (e.g. OC-11), `App` multi-select maps to streams, `Epic`↔`Tasks` dual
  relation is the milestone→task link, `Status`/`Priority` vocabularies map to
  the hub's closed sets (mapping to be defined in the ai-stack spec change).
- **Ingestion:** unknown/changed projects become hub inbox items → existing
  `drain-inbox` Telegram triage elects estado/prioridad/ambito → clerk writes the
  note. The sensor never writes to `hub/`.
- **Vocabulary mapping (dashboard → hub), approved:** active→`activo`,
  paused→`pausa`, closed+cold→`archivado`; **proposal is not a state** — it becomes
  an inbox item. The hub schema stays at three estados; do not extend it.
- **`scripts/projects.yaml` degrades gracefully:** `status` becomes a local
  fallback/cache until the hub note exists; `group`, `streams`, and descriptions
  remain local presentation metadata.
- **Offline safety:** the dashboard keeps a read-only local mirror of hub
  estado/prioridad; viewing survives VPS downtime, writing always goes through
  the clerk.

## Roadmap context (Sept 2026 survey)

| Wave | Content |
|---|---|
| 1 — Close | agy-bridge 0.2.0 (commit docs, push 6 commits, publish) · teams-to-tasks staged commit · compare-prices docs commit |
| 2 — Unblock | VPS server default-model fix → delegate deploy + live smoke |
| 3 — Build | `ep-storage-lifecycle` apply (entities-portal, planning complete, ~1400-1600 lines, 5 work units) |
| 4 — Backlog | openclaw-notion-mcp apply · pdr-async-docs-phase3 apply · compare-prices critical scrapers |
| Decision | Formal pause/archive: recruiting-backend, recruiting-frontend, voice-assistant, sis, busqueda-vacaciones |

Engram: roadmap saved under topic key `roadmap/activo/global` (observation #8613).
