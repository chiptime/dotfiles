# tiktok-ingest — Phase 1 local web application

Objective: ROADMAP "Phase 1 — Local web application" per `ai/tiktok-ingest/prds/PHASE-1-LOCAL-WEB-PRD.md` (P1-FR-01..19 are the authoritative requirements; this document records the decisions). Branch `feat/tiktok-web-phase1` (base `57d9620`). Delivery: work-unit commits, no push without the user. Runner: `env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src` (baseline 1052 OK, 5 skipped).

## Specs

- S1 (entry condition): PRD §6 "the Phase 0 acceptance checklist … is satisfied on a real authorized collection. The UI wraps a proven one-command engine; it does not compensate for a missing one." Decision 1: audit the checklist read-only against real state; if items lack evidence, build read-only views first and hold run launching from the web until the user decides.
- S2 (stack): stdlib only (`http.server` threaded), HTML/CSS/vanilla JS with no build step, no new dependencies.
- S3 (launcher): new subcommand `ti web`, binds only `127.0.0.1:8484`, opens the browser. (P1-FR-15)
- S4 (browser-origin protection): random token per start, required on every mutating request, plus `Host` and `Origin` checks. Not a login (PRD §12 residual local trust stays documented, not solved). Static/evidence paths reject traversal.
- S5 (progress): Server-Sent Events carrying the same narration lines the CLI prints; reload rebuilds everything from disk (P1-FR-02, P1-FR-14).
- S6 (consent): a modal that cannot be closed without deciding, showing exactly the CLI consent window fields, Authorize / Deny; browser login readiness is a "Ready" button. Non-persistent, no global yes (P1-FR-09, P1-FR-10).
- S7 (v1 scope): views Collections, Runs, Blocks, Evidence, Pending review (P1-FR-04..08); launch `ingest` (collection or video URL); backlog review record/plan/apply (P1-FR-11, 12). No low-level diagnostic tools.
- S8 (single writer): one active run/write at a time; conflicts reported (P1-FR-13); GPU lease guard stays authoritative.
- S9 (docs/help): `ti web` help text, OPERATIONS.md section, ROADMAP Phase 1 status.

## Tasks

| ID | Specs | Task | Route | Status | Evidence |
|---|---|---|---|---|---|
| T0 | S1 | Phase 0 checklist evidence audit (read-only, real state) | delegated explorer | done | 8/10 evidenced; 7, 8 unit-test only (see L3) |
| T1 | S2,S3,S4 | web package: loopback server, token/Host/Origin, static + safe paths, `ti web` | delegated writer | done | `7ab9484`; RED import; GREEN 1077 OK; parent: `ti` launcher pass-through |
| T2 | S7 | read-only JSON views (collections, runs, blocks, evidence, pending) | delegated, parallel with T4 | done | `062e9a6`; RED import; GREEN; read-only hash proof; real-state smoke counts match T0 |
| T3 | S7,S8 | backlog review endpoints (record/plan/apply) | delegated after T2 | done | `c890ff0` + `426a110`; RED import; GREEN; CLI parity on twin states |
| T4 | S5,S6,S8 | run runner: worker thread, web consent provider, readiness, SSE, single-writer lock, cooperative cancel | delegated, parallel with T2; launch gated by T0 | done | `32f9447`; RED import; GREEN; parent wired SSE streaming in server.py and fixed route precedence (literal beats param, RED first) |
| T5 | S6,S7 | frontend SPA (5 views, consent modal, run launch) | delegated after T2–T4 | done | `a9f233e`; RED 5 structural tests; GREEN; `node --check` OK |
| T6 | S9 | docs | inline | done | docs commit |
| T7 | all | independent verifier (risk: security item 2, concurrency item 4) | delegated | pending | |

## Log

- L1 (2026-11-04): "2" (start Phase 1), then "dale" accepting every kickoff recommendation (decisions 1–7 in Specs).
- L2: explorer map — no web code exists; StageEngine exposes `approve`/`notify`; `install_termination_handlers` is main-thread only, so web runs need cooperative cancellation; reports under `<state>/reports/<collection_key>/<ts>.json`; PRD §12 leaves consent UI design and progress refresh open. ROADMAP Phase 0 checklist: all 10 items unchecked; real state exists in ~/.local/state/tiktok-ingest.
- L3 (T0 audit): Phase 0 checklist on collection c99f2ad2160a8254 (`salud`, reports 2026-10-12): items 1-6, 9, 10 EVIDENCED from saved reports + backlog (2 pending entries); 7 (only sanitized text reaches the API) and 8 (provider failure keeps resumable progress) PARTIAL — proven by unit tests only, no live artifact. Per S1: build views and review first; web run launching held for a user decision at the end.
- L4 (T1): `bin/ti`/`bin/tiktok-ingest` always dispatched to `ingest`; parent added a `web` pass-through so `ti web` works.
- L5: route collision — views' `/api/runs/{key}/{report}` captured `/api/runs/active/events`; parent changed the router so the most specific route wins (literal segment beats parameter), RED test first.
- L6: parent committed T3 once with 3 failing `RunWebTests` (bare Mock server lacks a real app); fixed in `426a110`.
- L7: review decisions use the product vocabulary `accepted`/`rejected` (P1-FR-03); apply is bound by `plan_sha256` of the previewed plan because the CLI has no confirmation token.
