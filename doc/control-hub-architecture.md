# Control Hub Architecture — Single-Writer Vault with Inbox Capture

Architecture decision record for Bruno's global control hub: one git-versioned vault
that every assistant (Gertru, opencode local/VPS, pi, Obsidian) reads and feeds without
merge risk, without manual git, and without invading the agent's living space.

Decided: 2026-09-13 · **Implemented & in production: 2026-09-14** (SDD change `control-hub`, archived) · **Local layer wired: 2026-09-14** (plan phases 1-3 complete)
Traceability: Engram topic keys `architecture/hub-control-ecosistema` + `sdd/control-hub/*`; ai-stack `openspec/specs/{control-hub,gertru-clerk}`; vault `gertru-lab/gertru-workspace`

---

## Problem

Six unregistered entry points hold project state with no single source of truth:
OpenClaw VPS (Gertru + `cerebro/`), Notion personal, Notion work (Clece), opencode VPS,
opencode local, pi local — plus Engram (agent memory) and two local trackers
(`scripts/projects.yaml`, generated `PROJECTS.md`). Result: no global state, no
priorities, noise, and project surveys that only an agent session can reconstruct.

## The decision (one line)

**The vault already existed: `gertru-lab/gertru-workspace`.** Gertru's workspace inside
the OpenClaw container is a git repo (branch `master`, GitHub origin, daily snapshot via
`backup.sh`, living on named volume `openclaw-state`). We did not build a new vault —
we added a delimited, well-structured room (`hub/`) inside it and route all writes
through fixed, validated operations.

Rejected alternatives:
- **New vault / repo** — a seventh entry point; ignores that the embryo exists.
- **Notion as hub** — breaks file-native access for every agent; network-dependent.
- **Bind-mount gymnastics on the clone** — replaced in production by the stable
  `/opt/ai-stack/openclaw-hub` mirror (Dokploy's re-clone races container creation).

## Role map

| System | Role |
|---|---|
| `gertru-lab/gertru-workspace` (`hub/`) | **Single source of truth** for project management state |
| **Obsidian (local clone, pull-only)** | **Human dashboard — first-class product layer**: `hub/dashboard.md`, project notes, Canvas ecosystem map. Read-only sync via obsidian-git (never pushes) |
| `cerebro/` (rest of workspace) | Gertru's personal second brain — untouched by the hub |
| Engram | Agent operational memory (session state) — never the human dashboard |
| Notion personal | Direct task source (2026-09-15, `hub-task-sync`): personal Tareas DB pulled by Gertru's `sync-tasks` job — one-way, reads only |
| Notion work (Clece) | Direct task source (2026-09-15, `hub-task-sync`): Mantenimiento SIS board pulled by the same job — task ingestion only, still no hub→Notion writes |
| `~/.dotfiles/scripts/projects.yaml` + `PROJECTS.md` | Repo-level technical layer (branch, dirty, last commit) — stays as is |
| `/opt/ai-stack/openclaw-hub` (ro-mounted at `/etc/openclaw/hub/`) | The RULES: `CLERK.md`, `schema.json`, `clerk-validate.mjs`, `MANUAL.md`, `templates/` — stable mirror, never workspace-resident (Gertru cannot edit her own write rules) |
| **`~/hub` (local clone + `hub-session-close` skill)** | **Local agent layer**: every meaningful opencode session drops a session-close summary into `~/hub/hub/inbox/opencode/` and pushes (create-only, pathspec-scoped, pull --rebase first). First live item: `aaf4081` (2026-09-14) |
| Morning-brief / weekly-review / drain-inbox | Consumers: read the vault, list pending triage, apply inbox items |

**Task ingestion amendment (2026-09-15, SDD change `hub-task-sync`, archived at ai-stack
`openspec/changes/archive/2026-09-15-hub-task-sync/`):** Gertru pulls BOTH Notion sources
directly through her stdio MCP (personal Tareas DB + Clece Mantenimiento SIS, data source
`2345b76b-8c24-409e-a728-6074d38acefd`), configured in `openclaw/hub/tareas-fuentes.json`;
the hourly `sync-tasks` job (:15, workdays 08–20) materializes the hub `## Tareas` section
through the clerk op `set-tareas`. Hub task vocabulary is sealed to
`abierta|en_curso|hecha`; the 13 Notion states map onto it (mapping sealed in
`doc/MAPA_INGESTAS.md`). Tasks take no inbox hop; the PC sensor keeps machine facts only
(repos, sessions, hours, autodiscovery).

## Data vs. Rules — separation of powers

| | `workspace/hub/` (the vault — DATA) | `/etc/openclaw/hub/` (ro-mount — RULES) |
|---|---|---|
| Contents | `proyectos/`, `inbox/`, `dashboard.md`, `ecosistema.md` | `CLERK.md`, `schema.json`, `clerk-validate.mjs`, `MANUAL.md`, `templates/` |
| Who writes | Gertru, only via validated ops | Nobody in-container (physically read-only) |
| Source | git → GitHub vault → Obsidian mirror | ai-stack repo (with its 141+ tests), rsynced to the stable `/opt` mirror |
| Lifecycle | Evolves with every drain/log | Evolves with every deploy |

Rationale: Gertru must keep write access to her workspace (it is her house). If the
validator lived inside it, the fox would guard the henhouse. The ro-mount is the only
hard in-container boundary.

## Hub layout

```
gertru-workspace/
├── hub/                          ← THE ROOM (ours)
│   ├── inbox/                    ← capture door: multi-writer, safe by construction
│   │   ├── opencode/             ←    each writer ONLY creates new files:
│   │   ├── pi/                   ←    <YYYY-MM-DD>-<slug>.md in its own namespace
│   │   ├── bruno/
│   │   └── _triage/              ←    disputed items awaiting human election
│   ├── proyectos/                ← structured zone: clerk writes ONLY here
│   ├── dashboard.md              ← UNIFIED view: summary + dated milestones +
│   │                               projects + repo telemetry (regen-dashboard)
│   ├── telemetria/repos.md       ← machine zone: ONLY writer = the projects sweep
│   │                               (2026-09-15 amendment — repo state flows INTO
│   │                               the hub; any other view is a projection)
│   └── ecosistema.md             ← apps, integrations, automations map
├── cerebro/                      ← Gertru's brain: untouched
└── (openclaw.json, credentials, identity — outside git entirely)
```

## Write rules (the 10% model)

| Zone | Writers | Rule |
|---|---|---|
| `hub/inbox/<writer>/` | Everyone (agents, Bruno drops) | Create-only, own namespace → content conflicts impossible by construction |
| `hub/proyectos/`, `dashboard.md`, `ecosistema.md` | Clerk (Gertru) only | Eight fixed validated ops: `set-estado`, `add-hito`, `add-log`, `nuevo-proyecto`, `archivar`, `drain-inbox`, `regen-dashboard`, `set-tareas` — schema validation before any write, exit 1 = zero writes (spec `gertru-clerk`: 9 requirements / 14 scenarios) |
| `hub/inbox/_triage/` | Nobody applies anything | Items wait for Bruno's explicit election |
| Rest of workspace | Gertru only | Hub never touches it (the 90%) |
| Local clone | Nobody pushes. Ever | Obsidian + obsidian-git configured **pull-only** |

Vocabularies (closed, Spanish): `estado ∈ {activo, pausa, archivado}` ·
`prioridad` integer ≥ 1 (1 = max) · `ámbito ∈ {personal, trabajo}` ·
`próxima-acción` ≤ 140 chars · `actualizado` ISO date, mandatory on every write.

## The cycle (PROVEN in production 2026-09-14)

```
1. CAPTURE   fast + dirty → inbox/<writer>/<date>-<slug>.md
             (Telegram dictation bypasses inbox: Gertru applies via clerk directly)
2. DRAIN     drain-inbox job 07:30 Europe/Madrid (jobs.yaml, Telegram delivery):
             clean deterministic item → applied to proyectos/ + dashboard, file removed
             ambiguous/contradictory  → moved to _triage/ + Telegram notification
3. TRIAGE    Bruno elects from his phone; clerk applies the election (re-validated)
4. CONSULT   Obsidian dashboard (auto-pull) · morning-brief §4 lists pending triage 08:00
```

Production evidence (2026-09-14): drain run ok (236s, delivered), fail-closed with
absent validator, 2-writer zero-collision inbox, human election applied
(vault commits `0dbe89f` → `214bd2a` → `7130e99`, all pushed).

## Why merges are impossible

| Layer | Guarantee |
|---|---|
| Inbox content | Writers create disjoint new files only — no shared file, no conflict, ever |
| Structured zone | Single writer (clerk) — one head, nothing to merge |
| Git level (non-fast-forward push) | `pull --rebase [--autostash]` + push only; force-push and hard reset are PROSCRIBED |
| Rebasing safety | Rebase REPLAYS local commits (nothing dropped); overlaps stop loudly and ask |

## Model strategy

| Work | Model tier |
|---|---|
| Clerk ops, drain, state updates | Cheap tier (`zai/glm-4.5-flash` alias, cost 0, plan-covered) |
| Judgment: reprioritize, weekly-review synthesis | Large model (`zai/glm-5.3` default, denylist-corrected) |
| Local agents closing a session | Cheap tier |

## Obsidian — the human layer (WIRED 2026-09-14)

The local mirror lives at `~/hub` (clone via the `github.com-chiptime` SSH alias) and is
**pre-wired**: the Obsidian Git plugin is downloaded at
`~/hub/.obsidian/plugins/obsidian-git/` with `data.json` locked to pull-only
(`disablePush: true`, `autoPullInterval: 5`, `autoSaveInterval: 0`), enabled via
`community-plugins.json`, and `.obsidian/` is excluded through `.git/info/exclude`
(never travels to the vault). Remaining human steps: open `~/hub` as an Obsidian vault
and turn on community plugins.

Agent side: the `hub-session-close` skill (dotfiles
`ai/agents/opencode/skills/hub-session-close/`, symlinked + registered) wires the
session-close convention — create-only items under `hub/inbox/opencode/`, `pull
--rebase` → pathspec add → commit → push, never force, stop on conflict. What you get:
`hub/dashboard.md` as the global state table, every project note readable/searchable,
`hub/ecosistema.md` + Canvas for the ecosystem map. Edits made here NEVER travel — if
you want a change, tell Gertru or drop an inbox item.

## From-zero reproducibility (verified live)

Deploy the compose app and the hub self-assembles: clerk surface mounted from the
stable `/opt` mirror, entrypoint scaffold (idempotent, fail-soft, NEVER `migrate`),
drain job via jobs.yaml + drift gate. Human steps only for pre-existing content:
`migrate` + derived-values report review before the vault push. Full story:
`DEPLOY.md §9.7`; operator usage: `openclaw/hub/MANUAL.md`; agent contract:
`openclaw/hub/CLERK.md`.

## Production incidents & hardenings (2026-09-14, all fixed with RED-GREEN tests)

1. **Gateway death on transient fetch failure** — upstream OpenClaw 2026.7.1-2 bug:
   unhandled TLSSocket `'error'` in the SSRF guard; its custom lookup dials api.z.ai
   AAAA records. Three-layer mitigation: `NODE_OPTIONS=--dns-result-order=ipv4first` +
   `net.ipv6.conf.all.disable_ipv6=1` + `/etc/hosts` pin (`extra_hosts`) — the hosts
   file is the only lookup layer nothing can bypass. Deployed image has since moved to
   `2026.9.4` (digest-pinned); upstream report + unpin still pending.
2. **Dokploy re-clone inode race** — direct `./openclaw/hub` bind captured an empty
   pre-clone inode; the drain ran with "validador ausente" and correctly fail-closed
   everything to `_triage`. Fix: stable `/opt/ai-stack/openclaw-hub` mirror (the same
   proven pattern as `/opt/ai-stack/automations`), synced by the existing path unit.
3. **Crash-orphaned writes** — a killed run left uncommitted writes in the working
   tree; the drain refused to touch them (fail-closed), the human elected, the operator
   re-validated, then they were committed. Lesson: "zero partial writes" is guaranteed
   at COMMIT granularity; write-then-crash leaves working-tree orphans by design.
4. **Empty directories are invisible to git** — scaffold created `inbox/<ns>/` with
   `mkdir`, but git does not version empty dirs: every fresh clone (Obsidian mirror,
   local agents) was born WITHOUT an inbox; the container's own working tree masked
   the flaw. Fix: `.gitkeep` in all four namespaces (vault `1e3cec2`) + scaffold seeds
   them (ai-stack `a65d23a`). Lesson: a distributed system's real test is a NEW node
   joining, not the existing ones working.

## Plan status (convergence plan v2.3)

| Phase | Status |
|---|---|
| 1 — Control schema (tree, frontmatter, vocabularies, dashboard) | ✅ production |
| 2 — Gertru clerk (ops, validator, drain 07:30, model routing, rebase plumbing) | ✅ production |
| 3 — Local layer (mirror `~/hub`, Obsidian pull-only pre-wired, agent session-close skill) | ✅ wired (open Obsidian to finish) |
| 4 — Noise zeroing (2026-09-14: `012-time-tracker` archived by election `b76c1e3`; recruiting ×2 proven ACTIVE — the dormant-survey was stale Engram data; `projects.yaml` absorption resolved by layer consistency, no merge needed) | ✅ closed |

## Follow-ups (open, non-blocking)

- **Dashboard regeneration**: CLOSED — the clerk `regen-dashboard` op shipped (ai-stack
  `7b9b197`) and the drain runs it post-apply (`automations/payloads/drain-inbox.md`),
  so migrated notes now reach `dashboard.md`. Caveat: the rebuild is LLM-mediated;
  there is no deterministic regeneration script yet.
- **Upstream report** to OpenClaw for the SSRF-guard unhandled error; unpin
  `extra_hosts` when fixed.
- Plan phase 4: formal pause/archive of dormant projects + absorb `projects.yaml`.

## Non-goals

- No new vault, no Notion-hub, no manual git for Bruno, no local pushes.
- Not touching: `cerebro/` personal content, `openclaw.json`/credentials, Notion work
  (beyond read-only task ingestion), `PROJECTS.md`/`projects.yaml` repo-technical layer.

## Documents

`MANUAL.md` (daily use, Spanish) · `CLERK.md` (agent contract, Spanish) ·
`DEPLOY.md §9.7` (from-zero) · this ADR (why) · specs in `openspec/specs/{control-hub,gertru-clerk}`.
