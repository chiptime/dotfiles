# TikTok ingest — actionable recovery and useful results

Branch: `feat/tiktok-web-ux` (base `e4ccccc`; existing uncommitted UX work must be preserved). Delivery: ask-on-risk; no commits during working hours, no push. Runner: `PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src`.

## Specs

- S1: "en blocks, no se podria añadir algo como reintentar o similar?"; "hacer los bloqueos accionables sin saltar las protecciones". Present a truthful recovery path for blocked videos and collections; retain existing launch hold and consent/lease safeguards.
- S2: "cuando procesas un video realmente que valor aporta?"; "mostrar el resultado útil del vídeo junto con la procedencia real de sus verificaciones". Show useful output and actual provenance without presenting unsupported verdicts as independently verified.
- S3: "y quie recuerde los videos y colecciones son reanuables por si se paran en cualquier paso". Preserve existing durable resume behavior; surface accurate continuation status and limitations without falsely promising that every interruption can resume automatically.

## Tasks

- T1 — S1, S3 — inline: recovery paths and resume behavior, keeping existing protection and state; focused checks passed, uncommitted; Risk: item 2 (execution guards) and item 4 (run coordination).
- T2 — S2 — inline: useful result and provenance; focused checks passed, uncommitted; Risk: item 6 (misleading verdict provenance requires behavioral coverage).
- T3 — S1–S3 — focused checks passed; independent verification partial; pending decision on missing proof.

## Log

- L1 (2026-11-04): "vale, en blocks, no se podria añadir algo como reintentar o similar? y por otra parte, cuando procesas un video realmente que valor aporta?"; accepted proposed improvements with "me parece bien, y quie recuerde los videos y colecciones son reanuables por si se paran en cualquier paso".
- L2: Existing branch and UX edits predate this work. `allow_runs=false` intentionally blocks web launches; do not bypass. `/api/blocks` currently presents status text as a command. Local verification may preserve an operator-supplied verdict without a source fetch.
- L3: Added read-only, identity-validated and shell-quoted terminal resume commands using the active state root for known blocked videos and collections; permanent rejections get no video command. Web launch remains disabled. Added an Evidence result summary preserving verification reasons and explicit no-fetch notices. Focused checks passed, and headless Chromium confirmed the existing real example reports the retained operator verdict and absent source fetch. The running server must be restarted to load Python endpoint changes. Full suite and independent verification remain pending.
- L4: Full suite: 1180 passed, 5 skipped before a final test-only addition; afterward focused suite 16 passed and JavaScript syntax passed. Independent verifier: guard/quoting/rejection and representative interruption/resume tests passed, but neither arbitrary interruption recovery nor synthetic-fixture end-to-end browser provenance was proven. Parent separately observed real-data browser provenance and Blocks render via read-only GET. Restarted our idle server on port 8484, retaining the web-launch hold; no retry was executed. Verification remains partial, not an unconditional guarantee of reusability from every interruption.
