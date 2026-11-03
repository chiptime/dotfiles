# tiktok-ingest — Stability S3 (layered orchestration) + S4 (realistic gates, live checks)

Objective: ROADMAP Stability track S3 and S4. Branch `feat/tiktok-stability-s3-s4` (base `ce98dab`). Delivery: work-unit commits, no push. Runner: `env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest discover -s ai/tiktok-ingest/src/tiktok_ingest/tests -t ai/tiktok-ingest/src` (baseline 1024 OK).

## Specs

- S1 (rename): "Rename `pipeline.py` to reflect what it does (URL fetch and validation)." Decision 4: new name `fetch.py`; update every import including tests; no `pipeline.py` compatibility shim. Outside import changes no test assertion changes.
- S2 (engine): "Extract a stage engine (items + phase in, outcomes out) with no TUI and no consent logic. `guided-run` and `collection-run` become thin drivers; consent and the TUI become adapters."
- S3 (collection on engine): "Remove `collection_run`'s dependency on the private `GuidedRun._execute_collection_phase` and keep swap-retry logic in one place."
- S4 (behaviour preservation, all S3 work): every user-visible message, consent window, report field and state stays byte-identical; the existing suite passes without editing assertions (only import paths for S1 and patch targets that move with code may change, each justified in the Log).
- S5 (swap gate): "Make the swap gate tolerate short ambient spikes (sustained rate instead of a raw delta)… Observed false trips exceeded the 512 MiB limit by only 1.77–18.56 MiB." Decision 1: trip only when delta > 512 MiB AND the recent swap rate > 1 MiB/s (the cooldown watcher's threshold); always trip at >= 1024 MiB as a safety net. Assumption: "recent rate" is measured over the swap I/O between consecutive per-frame samples, using elapsed monotonic time.
- S6 (zero items): "Warn when the collection selector returns zero items for a collection that previously had items, so TikTok DOM changes are not silent." Decision 2: stderr warning + note in `cleanup_notes`; scan status and run flow unchanged.
- S7 (live smoke): "Add an opt-in live smoke test (`TIKTOK_INGEST_LIVE=1`) with one short real video: Whisper, Ollama, podman, and the TikTok collection selector. It never runs in the default suite." Decision 3: URLs from `TIKTOK_INGEST_LIVE_URL` (short video) and `TIKTOK_INGEST_LIVE_COLLECTION` (selector); each part skips when its variable is missing; isolated temporary state root; it really stops and restores Whisper.
- S8 (docs): OPERATIONS.md and ROADMAP.md reflect S1–S7; help text unchanged unless a command changes.

## Tasks

| ID | Specs | Task | Route | Status | Evidence |
|---|---|---|---|---|---|
| T1 | S1,S4 | rename pipeline.py -> fetch.py | delegated (user request: use gentle-ai subagents) | done | `3519a2f`; pure rename (no RED: no behaviour); 1024 OK. Worker interrupted mid-way, parent finished inline |
| T2 | S2,S4 | extract engine.py from guided.py; GuidedRun thin driver with consent/narration adapters | delegated, parallel with T3,T4 | done | `d50c947`; RED import error; GREEN 1042 OK; differential 3 scenarios identical; patch targets: tests patch `engine.time` alongside `guided.time` |
| T3 | S5 | swap gate sustained-rate + 1024 MiB ceiling | delegated, parallel | done | `4410474`; RED 1 fail/7 errors; GREEN; parent fix: ceiling no longer caps `--swap-limit-mib` (RED test then GREEN) |
| T4 | S6 | zero-items warning | delegated, parallel | done | `dc8c6e6`; RED rule 1; GREEN; parent wired `inventory-collect` (RED then GREEN) |
| T5 | S3,S4 | collection_run on engine; swap-retry in one place | delegated after T2 | done | `473855c`; RED static test; GREEN 1051 OK; differential 3 incl. swap-retry identical |
| T6 | S7 | opt-in live smoke test | delegated after T3,T4 | done | `070c770`; exception: live parts not runnable here (would stop real Whisper, hit TikTok); verified skipped by default and without URLs |
| T7 | S8 | docs (parent owns OPERATIONS.md/ROADMAP.md) | inline | done | docs commit |
| T8 | all | independent verifier (risk items 4 GPU stop/restore ordering, 2 safety gate) | delegated | done | blocker 1 (negative rate quiet) fixed `b55c466`; blocker 2 (live-smoke doc ambiguity) fixed in docs; recheck all S# pass; 1052 OK |

## Log

- L1 (2026-11-03): "vamos con s3 y s4" / "usa subagentes como gentle-ai-* en lo posible"
- L2 (2026-11-03): user answered "dale" to the kickoff questions -> all recommended defaults accepted (decisions 1–4 in Specs).
- L3: explorer map — `_execute_collection_phase` used at collection_run.py:2673,2768 and test_guided.py:1657-1699; tests patch `guided.time`, `guided.subprocess_runner`; test_bootstrap.py:99-103 reads pipeline module source; zero `mock.patch("tiktok_ingest.pipeline…")`. Swap gate: gates.check_swap_delta (raw delta vs pre-phase baseline), per-frame at vision.py:831; limit config.py:213. Ollama is a Popen child (no own cgroup). Scan state persisted by CollectionScanStore; merge is union-only so a zero scrape is silent.
- L4: shared docs (OPERATIONS.md, ROADMAP.md) owned by the parent (T7) so parallel writers keep disjoint surfaces.
- L5: T3 worker made the 1024 MiB ceiling unconditional, which silently capped an operator `--swap-limit-mib` above 1024 (an explicit option losing its effect). Parent decision: ceiling = max(1024 MiB, override + 1 byte), so an override keeps its old raw-delta meaning; RED test observed first.
- L6: T4 left `inventory-collect` (offline HTML import, same load+merge) unwired; parent wired the stderr warning there (its JSON stdout unchanged).
- L7: T5 audit — only duplication between swap retry and engine was lease construction (now `engine.lease_from_stages`); restore-outcome mappings stay per driver (different pinned messages). `GuidedRun._execute_collection_phase` kept as forwarder: tests still call it.
- L8 (verifier T8): blocker — a swap counter going backwards between samples gave a negative rate treated as quiet (530 MiB delta passed; baseline failed). Fixed `b55c466`: negative rate is unmeasurable, fails closed. Recheck: S1–S7 pass incl. baseline tests on new code (1024 OK after expected import/clock patch rewrites), interrupted-stop per driver, stage-level gate probes, inventory-collect stdout byte-identical.
- L9: second blocker — OPERATIONS live-smoke text implied every part needs a URL; environment probes run with only TIKTOK_INGEST_LIVE=1. Docs corrected.
