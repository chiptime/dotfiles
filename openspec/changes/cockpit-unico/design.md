# Design: cockpit-unico

## Technical Approach

Extend `hub-state-emit.mjs` into a v2 merge emitter on the VPS (vault notes + inbox
`projects.json`), reusing `regen-dashboard.mjs` proven parsers (FM_RE, HITO_RE, codepoint
sort). Local stays a pure consumer: sweep pulls the clone, copies the sibling, renders.

**Root cause found (bigger than the brief):** hub slugs are numbered (`006-ai-stack`),
local keys are not (`ai-stack`, unit `Clece SIS`). So hub-precedence has NEVER fired —
neither `projects-dashboard.sh:130` nor `projects-html.py:536`. The `:531` copy gap is the
second half of the bug; fixing only the copy fixes nothing.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| D1 | Merge key = strip `^\d{3}-`, lowercase; 1:N aliases in sealed `hub-state-alias.json` | frontmatter `repos:` (clerk schema change, vault writes); fuzzy match | `tareas-fuentes.json` precedent; `clece-recruiting`→`recruiting-{backend,frontend}` is unresolvable by normalization alone |
| D2 | Tolerant `## Log` parser: `- [(fecha)] texto`; non-bullets dropped; `### YYYY-MM-DD` heading sets date context | strict `YYYY-MM-DD` only | Real notes use `(2021)`, `(2020-06)`, `(2026-08-18 17:23)`, `(2026-07-14/16)`, and undated bullets; the template uses `###` headings. Strict = silent data loss |
| D3 | Torn-read guard: on anomaly (0 bytes, or unterminated frontmatter) re-read once after 150 ms | flock (touches Gertru's sealed write path); accept | `clerk-render-tareas.mjs:189` is a non-atomic `writeFileSync` at `:15` hourly. Emit-local, no contract change; anomaly drops a whole project today |
| D4 | The ssh trigger commits (`pull --rebase` → add → `commit -- <pathspec>` → push); emit stays git-free | emit self-commits; leave uncommitted | Mirrors `projects-dashboard.sh:230-235`; keeps emit's exit 0/2 charter testable. Machine commits are precedent (`telemetria(sweep)`, `snapshot`) |
| D5 | Sweep renders twice: render → deliver+trigger → pull → copy → render | single pass | `projects.json` only exists after render; single-pass lands v2 one cycle late, killing the on-demand trigger's purpose |
| D6 | Tail-5 logs in document order | sort by parsed date | Dates are optional/ambiguous (D2); `## Log` is append-only |
| D7 | One embedded `<script>`, no deps, no fetch | none | Page has 0 JS today |

## Data Flow

    VAULT notes ──┐
                  ├─→ emit v2 (VPS) ─→ hub-state.json ─→ commit/push
    projects.json ┘         ▲                                  │
         ▲ scp+docker cp    │ ssh docker exec                  ▼
    sweep (local) ──────────┴─────────────────────── pull → sibling copy → render

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `openclaw/hub/hub-state-emit.mjs` | Modify | v2 schema, note/registry merge, D1–D3, D6 |
| `openclaw/hub/hub-state-alias.json` | Create | Sealed slug→{unit,repos[]} map |
| `tests/hub-state-emit.test.ts` | Modify | v2 suite (below) |
| `scripts/projects-dashboard.sh` | Modify | D4 trigger, D5 two-phase, sibling copy (tmp+rename) |
| `scripts/projects-html.py` | Modify | Index v2 by slug+repos+unit (`:531-539`); 4 panels; badge |

## Interfaces / Contracts

```jsonc
{ "schema": "hub-state/v2", "generated": "<ISO>",
  "projects": [{ "slug": "ai-stack", "nota": "006-ai-stack", "estado": "activo",
    "prioridad": 3, "ambito": "personal", "proxima_accion": "", "actualizado": "2026-09-15",
    "repos": ["ai-stack"], "provenance": "merged", "local_repo": true, "hub_note": true,
    "hitos": [{"fecha":"2026-10-01","texto":"…"}],
    "tareas": {"abiertas":1,"en_curso":2,"hecha":0,"items":[{"id","titulo","estado","origin":"notion"}]},
    "logs": [{"fecha":"2026-09-15","texto":"…"}],
    "local": {"branch","dirty","status_local","last_commit"} }],
  "inbox": {"opencode":0,"pi":0,"bruno":1}, "triage": 0 }
```

Ordering: estado(activo→pausa→archivado→unknown) → prioridad(absent last) → slug, codepoints.
Panels: Today←`estado:activo`+`proxima_accion`+hitos ≤14d · Needs decisions←`inbox`/`triage`
+`status_local:proposal` (labeled "local") · Changes←`logs` · Tareas←`tareas`. Badge: `now −
generated` > 24 h ⇒ stale; missing/unparsable ⇒ "sin datos del hub".

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | Log grammar (5 shapes + undated + `###` + non-bullet), hitos, tareas glyphs, slug/alias merge, hub-precedence, `local_repo`/`hub_note` flags | Fixture vault, Bun |
| Unit | Byte stability (2 runs ≡ modulo `generated`); `dashboard.md` deleted ≡ | Spec acceptance 1–2 |
| Unit | Missing `projects.json` ⇒ exit 0; missing `proyectos/` ⇒ exit 2, zero writes; torn note ⇒ re-read | Spawn emit |
| Integration | Sibling absent ⇒ renderer completes, empty hub columns; present ⇒ `estado_hub` populated | Run `projects-html.py` |
| Manual | No token/secret in HTML; no write affordance | grep rendered output |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — no file classification or execution of repo content | — | — |
| Git repository selection | Applicable — `git -C "$HUB"` local + container vault path | Absolute paths only, never `cd`; both literals, no interpolation of scanned names | Wrong/missing repo dir ⇒ skip, non-fatal |
| Commit state | Applicable — commit inside the sweep | `add` explicit path + `commit -- hub/hub-state.json`; never `-a`; empty index ⇒ skip, exit 0 | Dirty unrelated file stays uncommitted; no-change ⇒ no commit |
| Push state | Applicable — pushes to `origin/master` | Tracking branch only, no refspec, never force/reset; `index.lock` contention ⇒ non-fatal | Push failure ⇒ sweep exit 0 |
| PR commands | N/A — no PR/branch automation | — | — |

ssh/docker argv is a fixed literal (no user/scan-derived interpolation); failure is
best-effort and non-fatal, mirroring the existing scp delivery.

## Migration / Rollout

No data migration. Ship ai-stack first (v2 + tests; the v1 consumer reads
`projects[].slug/estado` and is unaffected by added fields), then dotfiles. Commits:
(1) alias map + merge/parsers + tests, (2) emit trigger + sibling copy + two-phase sweep,
(3) renderer panels + badge. Each well under 400 lines.

## Open Questions

- [ ] Alias map seed beyond `clece-recruiting`: `Products/Planificador`→`frontend`/`backend` has no hub note — confirm it stays local-only.
- [ ] `prioridad` is `3` for all 17 notes today, so ordering collapses to estado→slug (cosmetic, not blocking).
