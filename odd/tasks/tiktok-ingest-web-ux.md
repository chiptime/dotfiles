# tiktok-ingest — web UX/UI pass

Objective: a strong UX/UI pass on the Phase 1 web interface (`ti web`), guided by the ui-ux-pro-max skill (community skill at `~/Code/personal/antigravity-awesome-skills/skills/ui-ux-pro-max`, data-dense dashboard style). Branch `feat/tiktok-web-ux` (base `e4ccccc`). Delivery: work-unit commits ONLY outside the user's working hours (Mon–Thu 09:00–19:00, Fri 08:00–16:00); no push. Runner: `env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src` (1145 OK, 5 skipped).

## Specs

- S1 (theme): light and dark automatic via `prefers-color-scheme`; neutral data-dashboard palette (dark bg `#0F172A`, light bg `#F8FAFC`, text `#0F172A`/`#F1F5F9`, action blue `#3B82F6`/`#2563EB`), plus status colors for complete / pending / blocked / failed. Contrast ≥ 4.5:1 for text in both themes.
- S2 (typography): system fonts (Inter, Segoe UI, system-ui) and a monospace stack for ids and hashes. No external fonts or any outbound request (CSP `default-src 'self'`, P1-FR-17).
- S3 (structure): fixed sidebar with the sections and inline SVG icons (no emojis); header with engine status; a Home view with summary cards (pending review, blocks, last run, collections); Run becomes its own section instead of a side panel on every view; while launching is disabled it explains why and what is missing.
- S4 (review): list on the left, detail on the right; claims with verdicts as colored badges (verdict text verbatim); Accept / Reject visible, Reject opens the reason field; keyboard shortcuts `j`/`k` move, `a` accept, `r` reject (never while typing in a field; a visible shortcut hint); a counter of recorded-but-unapplied decisions at the top.
- S5 (readable data): collections shown by name (URL slug) with the hash muted; relative dates ("3 weeks ago") with the exact timestamp on hover; ids and URLs never wrap mid-token — truncate with a copy button; saved reports open in a readable view, not raw JSON (raw stays reachable); fix the Runs "Generated at" bug (all reports showed the summary build time).
- S6 (unchanged guarantees): strict CSP, no inline script/style, no HTML injection sinks; status/verdict/outcome strings verbatim from the engine (P1-FR-03); consent modal with only Authorize/Deny, no close path, exact window fields (P1-FR-09); every POST through the one token helper; all existing endpoints unchanged.
- S7 (visual verification): screenshots with headless Chromium at 1440, 1024, 768 and 375 px in light and dark, reviewed before delivery; ui-ux-pro-max pre-delivery checklist (cursor-pointer, focus visible, transitions 150–300 ms, reduced motion, no horizontal scroll at 375 px, labels).
- S8 (L7): "debemos mejorar el lector de .md y de .json" — Markdown and JSON artifacts render formatted (headings, lists, quotes, code, tables; collapsible JSON tree) with a Raw toggle, keeping the text-only, no-outbound-link rendering rule.
- S9 (L8): "una línea explicativa debajo del título de cada sección, y en Runs una leyenda de las 6 etapas".

## Tasks

| ID | Specs | Task | Route | Status | Evidence |
|---|---|---|---|---|---|
| T1 | S5 | backend: `saved_at` from report file name, collection display `name` | inline | done (uncommitted: working hours) | RED 2 tests, GREEN; 1145 OK |
| T2 | S1–S6 | SPA redesign (index.html, app.js, app.css) + structural tests | delegated (context backstop); worker cancelled mid-task, parent finished | done (uncommitted) | node --check OK; structural tests 16 OK; suite 1150 OK |
| T3 | S7 | screenshot matrix + checklist review, fixes | inline | done (uncommitted) | headless shell screenshots light/dark 1440/768/375; fixes in L6 |
| T5 | S8 | evidence reader: Markdown + JSON viewers (`static/reader.js`), Evidence tabs + run Report JSON | inline | done (uncommitted) | RED 19 fail → GREEN 20 OK; suite 1170 OK (5 skipped); screenshots light/dark |
| T6 | S9 | section introductions and six-stage legend | inline | done (uncommitted) | RED 3 errors → GREEN 3 tests; suite 1173 OK (5 skipped); headless browser GET |
| T4 | all | commits after 19:00 (suggested 3rd unit: reader) | inline | pending | |

## Log

- L1 (2026-11-04): "necesitamos mejorarlo enormemente en ux/ui, quiza una pasada base de ux-ui-pro-max skill"; then "dale" accepting kickoff recommendations 1–7.
- L2: ui-ux-pro-max is a community skill (risk: unknown); its scripts only read local CSVs (stdlib, no network) — used for design-system lookup. Accepted: Data-Dense Dashboard style, Inter/system font mood, UX feedback rules (loading, empty states, error recovery). Rejected: landing/CTA patterns and Google Fonts (CSP/P1-FR-17).
- L3: real-state screenshots (before): unstyled HTML, bullet-list nav, hash collection keys, wrapped ids/timestamps, Run panel on every view; Runs "Generated at" identical for all reports because saved reports have no `generated_at` and `summary` falls back to now (CLI output left unchanged; web uses the file-name save time).
- L4: 09:49 Wednesday is inside working hours — commits deferred until 19:00.
- L5: the first T2 worker was cancelled after writing most of the redesign (app.js ~2160 lines, app.css ~1100); the parent verified it (`node --check`, 44 structural/server tests OK) and finished the visual pass inline.
- L6: visual pass on real state (read-only GETs) with Playwright's `chrome-headless-shell` (the full chromium build hung in this environment). Fixes: review `classification` rendered `[object Object]` (now `root / subgroup (confidence N)`, and `el()` renders any object as JSON); Blocks run cards show collection name + relative save time (backend adds `collection_name`, `saved_at` to blocks runs); mobile Menu button styled, engine pill hidden under 480px so the title fits, long hashes wrap; Menu no longer leaks to desktop (`.btn` overrode `display:none`); "Raw JSON" no longer styled as a back link and opens in a new tab. Note: the blocks name/saved_at test assertions were added together with the code (RED not observed).
- L7 (2026-11-04): "debemos mejorar el lector de .md y de .json". Before: every artifact was a plain `<pre>`; run report JSON only opened raw in a new tab. Added S8/T5: `reader.js` (pure Markdown block/inline parser + JSON tree, rendered with createElement/textContent only; links shown as text + inert URL, never anchors; JSON strings holding objects decode on demand), `documentViewer()` in app.js (Formatted|Tree / Raw toggle remembered per session, Expand/Collapse all, line count, Copy raw, invalid JSON falls back to raw with a note). Docs with >=3 `##` sections fold into `<details>` with an outline (video.md: 64 frames). Parser over all 35 real md files: 22 ms total. Tests run reader.js under Node (skipped without node).
- L8 (2026-11-04): "mucho mejor, aunque no entiendo del todo los datos de la ui, en el menu tenemos como muchas secciones para que es cada una?"; user accepted "una línea explicativa debajo del título de cada sección, y en Runs una leyenda de las 6 etapas" with "si porfa". Added intros, stage legend and artifact explanations; rejection warning distinguishes staging a decision from applying it. Full suite passed before the final copy-only clarification; focused tests and JS syntax passed afterward. No commit during working hours.
