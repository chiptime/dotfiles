# Launchpad — custom app/service launcher

Objective: a personal launchpad web service listing local applications and services as health-checked tiles. Branch: current (uncommitted work in dotfiles). Delivery: ask-on-risk. Runner: `cargo test` in `tools/launchpad`.

## Specs

- S1 (from "también quisiera un launchpad de aplicaciones y servicios montados custom"): a custom-built launchpad (Rust binary + embedded front, pattern of `tools/ai-quotas`) at `tools/launchpad/`, serving a tile grid at `/`.
- S2 (user decision, 2026-10-18): "Servicio propio (Rust + systemd)" — systemd user service, own port 47625 (47624 is taken by python), `LAUNCHPAD_PORT`/`LAUNCHPAD_HOST` env overrides.
- S3: Tiles are config-driven from `~/.config/launchpad/tiles.json` (repo master at `tools/launchpad/config/tiles.json`, symlinked per dotfiles-context skill; `LAUNCHPAD_TILES` env override). Tile: `id`, `name`, `icon` (emoji), `url`, optional `description`, optional `health_url` (default: `url`), optional `health_interval_secs` (default 15).
- S4: Endpoints: `/` (embedded HTML, `Cache-Control: no-store`), `/api/tiles` (tiles + server-side health states), `/api/health` (own health JSON).
- S5: Server-side health: GET `health_url` (ureq, 2s timeout), result cached per tile for its interval; state `up|down|unknown` with latency ms; front polls `/api/tiles` every 5s.
- S6: Front: dark grid (ai-quotas palette), tile = icon/avatar, name, host:port, status dot + latency, click opens `url` in a new tab.
- S7: Seed tiles from real running services (verified 2026-10-18): ai-quotas 47623, OpenCode 4096, Collie 8787, herdr-brain 8741, PI Web 8504, Projects Dashboard 47624, ComparaPrecio 4200.
- S8: `scripts/deploy.sh back` builds release, installs binary + symlinks unit/config, restarts service, health-checks; `systemd/launchpad.service` mirrors ai-quotas unit. README documents config + deploy.
- S9 (from "quisiera no solo tener ahi las apps o servicios web, sino tambien servicio script o similar como un discovery de mi ecosistema custom, servicios como opencode-switch, tiktok ingest, etc"): tile `kind` = `web` (default, current behavior) | `script`. Script tiles: `command` (copy-to-clipboard), `docs`, `location`, no url requirement, no health probe.
- S10 (user decision, 2026-10-18): "Catálogo + copy + autodiscovery" — catalog + copyable command + auto-discovery; NO remote execution (service binds 0.0.0.0; execution would be LAN-reachable RCE).
- S11: Front renders two sections (Servicios / Scripts & herramientas); script tiles show command in code style, click copies to clipboard with "copiado" feedback (clipboard API + execCommand fallback for insecure LAN origins).
- S12: `scripts/discover.py` (stdlib only) discovers the custom ecosystem: `scripts/*.sh` (skipping test files, description from header, command resolved via aliases.sh when aliased), `ai/*/` subsystems with README, and listening HTTP ports not already covered. `--merge` adds only new ids to tiles.json (writes through the ~/.config symlink into the repo master); never removes or overwrites manual edits.
- S13 (from "te pedire que la web no corte los titulos, no deben tener elipsis sino permitir el render completo del titutlo"): tiles render full titles — `.name`, `.host` and `.cmd` wrap (no ellipsis); `.desc` keeps one-line ellipsis (long descriptions).
- S14 (from "mira en otras rutas que a lo mejor tengo features como voice-assistant, llm-hub... que no aparecen"): discovery covers personal repos under `~/Code/personal/` (`LAUNCHPAD_PROJECT_ROOTS` override, `*-worktrees` skipped), README first body line as description (badge lines `[![` filtered). llm-hub and agent-tts descriptions enriched manually (manual edits preserved by merge).

## Tasks

- T1 [inline, done] S1/S3: scaffold cargo project + `config.rs` parse/validate — 6 config tests green
- T2 [inline, done] S4/S5: `health.rs` probe+cache, `server.rs` routes — 3 health + 2 server tests green
- T3 [inline, done] S6: `static/index.html` grid front — `node --check` OK
- T4 [inline, done] S2/S8: systemd unit + deploy.sh (fixed Text-file-busy: stop→cp→start) + README + config symlink
- T5 [inline, done] S7: seed 7 tiles + deployed + live verify: `/api/tiles` 200, 7/7 up
- T6 [inline, done] S9: `kind` field (web default | script) + validation — RED compile → GREEN
- T7 [inline, done] S9: `tiles_body` emits command/docs/location for scripts, health only for web
- T8 [inline, done] S11: front sections (Servicios / Scripts & herramientas) + clipboard copy con execCommand fallback
- T9 [inline, done] S12: `scripts/discover.py` (scripts/*.sh + ai/*/ + puertos) + merge semilla (14 added, 21 total, idempotente) + deploy + verify live
- T10 [inline, done] S13: CSS wrap (name/host/cmd sin elipsis) — verificado en DOM live (textOverflow=clip, whiteSpace=normal, título real de 2 líneas)
- T11 [inline, done] S14: discover_projects() ~/Code/personal (29 added → 50 tiles: 9 web / 41 script), badge-filter, llm-hub/agent-tts enriquecidas a mano, README actualizado, idempotencia 0 added

## Log

- L1 (2026-10-18): "también quisiera un launchpad de aplicaciones y servicios montados custom" — after confirming ai-quotas deployment on 47623.
- L2 (2026-10-18): user picked "Servicio propio (Rust + systemd)" (recommended) over static page or extending ai-quotas.
- L3 (2026-10-18): discovery — 47624 busy (python projects-dashboard), launchpad gets 47625; services verified by curl titles (4096 OpenCode, 8741 herdr-brain, 8504 PI Web, 8787 Collie, 4200 ComparaPrecio, 47624 dir listing).
- L4 (2026-10-18): T1-T5 done inline. Evidence: `cargo test` 11 passed, 0 build warnings after removing dead `State::Unknown`; deploy.sh back OK; `launchpad.service` active; `/api/tiles` 7/7 up; installed binary byte-identical to repo build. Fix during deploy: cp while service runs fails with Text file busy — deploy now stops before cp, starts after. No commit made (not requested); work sits uncommitted in dotfiles.
- L5 (2026-10-18): "esta genial, aunque quisiera no solo tener ahi las apps o servicios web, sino tambien servicio script o similar como un discovery de mi ecosistema custom, servicios como opencode-switch, tiktok ingest, etc"
- L6 (2026-10-18): user picked "Catálogo + copy + autodiscovery" (recommended) over remote execution or manual sections only.
- L7 (2026-10-18): T6-T9 done. Evidence: `cargo test` 16 passed; discovery stdout valid JSON; `--merge` 14 added (9 script de scripts/, 4 ai/*, port-4097, port-47625) → 21 tiles (9 web / 12 script), segunda pasada 0 added (idempotente); deploy back OK; `/api/tiles` 9/9 web up; playwright: secciones renderizadas, alias resuelto (`$ opencode-switch`), click copia → "copiado ✓". Known cosmetic: tiktok-ingest description empieza a mitad de frase (README parser toma la primera línea de cuerpo); favicon 404. Sin commit (no pedido).
- L8 (2026-10-18): "te pedire que la web no corte los titulos, no deben tener elipsis sino permitir el render completo del titutlo. por el momento me parece todo bien, si puedes mira en otras rutas que a lo mejor tengo features como voice-assistant, llm-hub (con sus funcionalidades principales como whisper->voz, embedings o pensamiento de llm pequeño) o similar que no aparecen"
- L9 (2026-10-18): T10-T11 done. Recon: proyectos personales en ~/Code/personal (30 dirs no-worktree; llm-hub sirve API :4000 con llama-chat GPU + nomic-embed; voice-assistant usa faster-whisper RTX 4090; agent-tts hostea herdr-brain). Merge 29 added → 50 tiles; idempotencia 0 added; README documenta kind/autodiscovery. Cosmetics previos se mantienen.
