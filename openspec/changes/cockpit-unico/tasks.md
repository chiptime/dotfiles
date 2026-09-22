# Tasks: cockpit-unico (hub-state v2 + PROJECTS.html cockpit)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1050–1350 total (ai-stack ~750–900, dotfiles ~300–450) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | ai-stack PR1 (U1) → ai-stack PR2 (U2) → dotfiles commit 1 (U3) → dotfiles commit 2 (U4) → dotfiles commit 3 (U5) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary | Est. lines |
|------|------|-----------|----------------------|-----------------|-------------------|-----------|
| U1 | Emit v2 core: parsers + merge + alias + core tests (RED→GREEN) | ai-stack PR1 | `bun test tests/hub-state-emit.test.ts` | Fixture vault in tmpdir, spawn emit (existing pattern) | Revert emit.mjs + alias.json; v1 consumer reads `projects[].slug/estado`, unaffected | ~450–550 (over budget — split tests to U2 or seek size:exception) |
| U2 | Determinism + hardening tests; v1 assertion cleanup | ai-stack PR2 | `bun test tests/hub-state-emit.test.ts` | Same fixture harness | Test-only revert | ~200–260 |
| U3 | Sweep: two-phase trigger + sibling copy (D4/D5) | dotfiles commit 1 | `bash -n scripts/projects-dashboard.sh` | VPS round trip (operator-assisted); unreachable-ssh path testable locally | Revert single hunk in projects-dashboard.sh | ~90–140 |
| U4 | Renderer: v2 index fix `:531-539` + 4 panels + badge (D7) | dotfiles commit 2 | `python3 -m py_compile scripts/projects-html.py` + smoke greps | Local render vs fixture PROJECTS.md ± sibling v2 | Revert single hunk in projects-html.py | ~260–360 |
| U5 | E2E smoke + threat-matrix checks + docs | dotfiles commit 3 | Grep smoke checklist | VPS round trip (operator-assisted) | Docs-only | ~40–80 |

## Phase 1: ai-stack — RED tests (write first, all fail)

- [x] 1.1 Extend `tests/hub-state-emit.test.ts`: v2 schema shape + full note fields (estado, prioridad, ámbito, próxima-acción, actualizado) and hitos `- [ ] (YYYY-MM-DD)` parsed from bodies ← hub-state-v2 R1
- [x] 1.2 Tests: `## Tareas` glyphs `[ ]`/`[/]`/`[x]` + `notion:<id>` → counts {abiertas,en_curso,hecha} + items{estado,origin} ← hub-state-v2 R1
- [x] 1.3 Tests: tolerant `## Log` grammar — 5 dated shapes, undated bullet, `### YYYY-MM-DD` heading sets date context, non-bullet dropped ← hub-state-v2 R1 / D2
- [x] 1.4 Tests: inbox via fs listing (opencode/pi/bruno, `.gitkeep` excluded, `_triage` separate); merge normalization (strip `^\d{3}-`, lowercase `.md`), hub precedence on estado, local fields fill, flags `local_repo`/`hub_note` ← hub-state-v2 R1+R2
- [x] 1.5 Tests: alias 1:N `clece-recruiting` → repos `recruiting-{backend,frontend}`; exit codes (missing `proyectos/` → 2 + zero writes; missing `projects.json` → 0 hub-only); codepoint ordering ← hub-state-v2 R1+R2+R3 / D1

## Phase 2: ai-stack — GREEN implementation

- [x] 2.1 Create `openclaw/hub/hub-state-alias.json` — sealed slug→{unit,repos[]}, seed only clece-recruiting; Products/Planificador stays local-only (resolves open question) ← D1
- [x] 2.2 Rewrite `openclaw/hub/hub-state-emit.mjs`: full-note parser (reuse regen-dashboard FM_RE/HITO_RE patterns), tareas glyph parser, D2 log parser, inbox listing, merge + provenance, canonical ordering, `hub-state/v2` schema, atomic tmp+rename, exit 0/2
- [x] 2.3 Green: bun suite passes; v1 consumer compat intact (added fields only); never reads `dashboard.md`

## Phase 3: ai-stack — determinism hardening + deploy

- [x] 3.1 RED then GREEN: byte-stability test (two runs ≡ modulo `generated`) + dashboard.md-deletion test ← hub-state-v2 R3 + acceptance 1–2
- [x] 3.2 RED then GREEN: torn-note re-read test (0 bytes / unterminated FM → re-read after 150 ms) ← D3
- [ ] 3.3 Prune obsolete v1 assertions; deploy emit v2 into VPS container + drain smoke — **operator-assisted** (VPS access) — _deferred: pending-operator-window (working-hours rule; minimal v1-assertion adaptation already landed with U1, full prune rides the deploy commit)_

## Phase 4: dotfiles — sweep two-phase + trigger

- [x] 4.1 `scripts/projects-dashboard.sh`: sibling copy `~/hub/hub/hub-state.json` → `~/.local/share/projects-dashboard/hub-state.json` (tmp+rename) after hub pull ← cockpit-view R2
- [x] 4.2 Two-phase render (D5): render → deliver+trigger → pull → copy → render ← hub-state-v2 R4
- [x] 4.3 ssh trigger: `docker exec … node hub-state-emit.mjs` + pathspec-scoped commit (`add` explicit path, `commit -- hub/hub-state.json`, never `-a`); every failure path logs and continues ← hub-state-v2 R4 scenarios / D4
- [x] 4.4 `bash -n` + local verification of unreachable-VPS path (sweep exit 0)

## Phase 5: dotfiles — renderer cockpit

- [x] 5.1 `scripts/projects-html.py` `:531-539`: index v2 by slug AND repos[] AND unit display name; absent/unparsable sibling → graceful empty hub columns ← cockpit-view R2 both scenarios
- [x] 5.2 Four hub panels (Today / Needs decisions / Changes / Notion Tareas) + Resumen counts computed exclusively from v2 ← cockpit-view R1
- [x] 5.3 Freshness badge (`now − generated` > 24 h ⇒ stale; missing/unparsable ⇒ "sin datos del hub") + one embedded `<script>`, no deps, no fetch ← cockpit-view R3 / D7

## Phase 6: verification

- [x] 6.1 Renderer smoke — decision: **manual-with-readback grep smoke**, not golden-HTML diff. Justification: dotfiles has no python harness; a committed ~500-line golden HTML breaks on any cosmetic change for a best-effort renderer. Run `projects-html.py` on a minimal fixture ± sibling v2 in a temp dir; assert via grep: `estado_hub` populated with sibling, graceful without, stale badge with old `generated`. Ad hoc, not committed.
- [ ] 6.2 Threat matrix (operator-assisted where VPS needed): unrelated dirty file stays uncommitted; no-change ⇒ no commit; push failure ⇒ sweep exit 0; ssh/docker argv fixed literal ← design threat matrix — _deferred: pending-operator-window (VPS round trip; local unreachable-ssh + commit-contention paths verified in U3)_
- [ ] 6.3 E2E smoke: sweep → scp → docker cp → ssh trigger → pull → sibling copy → render shows fresh v2 (VPS round trip, **operator-assisted**) or local harness fallback ← hub-state-v2 R4 / cockpit-view R1 — _deferred: pending-operator-window (local harness fallback executed: fixture hub clone + stubbed ssh in U3, renderer readback smoke in 6.1)_
- [x] 6.4 Grep rendered PROJECTS.html for gateway/Notion tokens and write affordances (fetch/POST) — none present ← cockpit-view R4
- [x] 6.5 Update script header comments + MAPA_INGESTAS ordering note (regen → espejo → scp → trigger → drain) — _script headers + ordering comment landed in U3/U4; MAPA_INGESTAS.md lives VPS-side (not in the local clone) → note rides the operator window with 3.3_
