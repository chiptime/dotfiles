# Apply Progress: antigravity-sdd-explore-router

Meta: artifact store `hybrid` | delivery `auto-chain` / `stacked-to-main` | Strict TDD ACTIVE (`bun test`)

## Cumulative state (after Batch 4 — 2026-08-20) — ALL PHASES COMPLETE

**Tasks 1.1–1.10, 2.1–2.4, 3.1–3.5, 4.1–4.5: 24/24 complete.** Batches: 1a (387), 1b (373), 2 (392), 3+4 (348). Ready for sdd-verify.

## Batch 1 — slice PR 1a (tasks 1.1–1.4) — 387/400 lines

| Task | Layer | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-------|-----|-------|-------------|----------|
| 1.1 | Unit | ✅ module-missing 0/1 | ✅ 23/23 | ✅ 12 cases | ✅ |
| 1.2 | Unit | via 1.1 | ✅ outcomes.ts | ✅ 7-outcome table | ✅ |
| 1.3 | Unit | ✅ module-missing 0/1 | ✅ 33/33 | ✅ 10 cases | ✅ density pass |
| 1.4 | Unit | via 1.3 | ✅ quota.ts | ✅ same run | ✅ |

Evidence 1a: full 617/0 (baseline 584/0), tsc 0 slice errors, budget 387/400.

## Batch 2 — slice PR 1b `slice-1b-backend-continuation` (tasks 1.5–1.10) — 373/400 lines

| Task | Layer | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-------|-----|-------|-------------|----------|
| 1.5 threat | Unit | ✅ module-missing | via 1.6 | ✅ 5 cases incl. THREAT blocks | within 1.6 |
| 1.6 | Unit | via 1.5 | ✅ validate.ts 38/38 | ✅ | ✅ |
| 1.7 | Unit | ✅ module-missing | ✅ persist.ts 43/43 | ✅ 4-store + EXACTLY-ONCE | ✅ |
| 1.8 | Unit | via 1.7 | ✅ store matrix | ✅ | ✅ |
| 1.9 | Unit | ✅ module-missing (RED within GREEN task) | ✅ spawn.ts 47/47 | ✅ 4 cases | ✅ |
| 1.10 | Unit | ✅ module-missing (RED within task) | ✅ backend.ts 50/50 | ✅ mutation-block/quota-gate | ✅ |

Evidence 1b: full 634/0, tsc 0 slice errors, budget 373/400. Threat: mutation → block, persist 0 calls, no fallback.

## Batch 3 — slice PR 2: CLI & telemetry (tasks 2.1–2.4) — 392/400 lines

| Task | Layer | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-------|-----|-------|-------------|----------|
| 2.1 | Integration | ✅ module-missing (9 tests + 6-mode stub first) | via 2.2 | ✅ 9 cases incl. THREAT-mutate + force-native | harness |
| 2.2 | Integration | via 2.1 | ✅ cli.ts + depsFor 9/9, 50/50 | ✅ | ✅ caught exit-0-no-artifact ENOENT crash; tsc caught stale defaultDeps |
| 2.3 | Unit+Integration | ✅ module-missing (7 tests) | via 2.4 | ✅ | within 2.4 |
| 2.4 | Unit | via 2.3 | ✅ metrics.ts + report.ts 56/56, 10/10 | ✅ | ✅ appendFileSync fixture fix |

Evidence 2: full 650/0, tsc 0 slice errors, budget 392/400. Runtime harness: stub-agy, no real agy/quota.

## Batch 4 — slice PR 3: binding, contract, docs, smoke & rollback (tasks 3.1–3.5, 4.1–4.5) — 348/400 lines

### TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 3.2 (RED-first) | `tests/agy-router.test.ts` contract describe | Contract | ✅ 56/56 + 10/10 at slice start | ✅ `Cannot find module '../src/agy/render'` + template/prompt/script ENOENT — depth/permission/recursion contract tested BEFORE any binding file existed | via 3.1/3.3/3.4/3.5 | ✅ 6 contract cases: depth===2, router model, router task-perm exact map, fallback tools.task:false + deny-all, renderTemplate inlining, prompt ≤60 lines + exactly 1 bash block + never-nested rule | within GREENs |
| 3.1 | — | Contract | — | via 3.2 | ✅ `config/opencode-router.template.json` | ✅ | ✅ |
| 3.3 | — | Contract | — | via 3.2 | ✅ `config/prompts/sdd-explore-router.md` (24 lines, 1 bash block) | ✅ | ✅ |
| 3.4 | install/uninstall describes | Contract | — | ✅ script ENOENT before creation | ✅ `scripts/install-router-config.sh` (render→install, timestamped backup, executable shim, `--uninstall` restore) | ✅ 3 install cases (render+shim, backup+restore, restore-marked) | ✅ removed dead render block |
| 3.5 | launcher describe | Contract | — | ✅ script ENOENT | ✅ `scripts/opencode-web.sh` (exports OPENCODE_CONFIG, exec web, refuses when uninstalled) | ✅ 2 cases | ✅ |
| 4.1 | — | Docs | — | N/A (docs) | ✅ SKILL.md +14 lines: routed usage, containment, fallback rules, opt-out, kill switch | N/A | N/A |
| 4.2 | — | Docs | — | N/A | ✅ spike header +5 lines: superseded by src/agy/ | N/A | N/A |
| 4.3 | smoke describe | Smoke | ✅ 78 prior tests | ✅ (via contract RED chain — smoke written before any installer existed) | ✅ installed-shim run: success + exactly-once persistence + metrics ['success','success']; isolated Web boot: HTTP 200, log `loading path=.../opencode-router.json` (real opencode 1.18.18, isolated port 30485, active server untouched) | ✅ re-run keeps tampered artifact (exactly-once) | ✅ |
| 4.4 | smoke describe | Smoke | — | same | ✅ force-native through shim (stub marker absent, fallbackAllowed:true); `--uninstall` removes override + launcher then exits 2; depth cap + fallback deny + never-nested prompt rule = contract tests | ✅ | ✅ |
| 4.5 | smoke describe | Smoke | — | same | ✅ quota snapshot hash unchanged (tmp AND real `~/.config/ai-quotas/gemini.json` before/after); metrics classify [['auth_captcha',false],['quota_unavailable',true]] | ✅ | ✅ |

### Work Unit Evidence (Batch 4)

| Evidence | Value |
|---|---|
| Focused contract | `bun test tests/agy-router.test.ts -t contract` → **12 pass / 0 fail / 36 expect()** |
| Focused smoke | `bun test tests/agy-router.test.ts -t smoke` → **4 pass / 0 fail / 12 expect()** |
| Full regression | `bun test` → **665 pass / 0 fail across 8 files** (650 baseline) |
| Runtime harness (Web) | Isolated `opencode web --port 30485` with temp-HOME installed override: **HTTP 200**, config-load log line present; active Web server (port 4096) never touched; process killed after probe. Live LLM router decision adapted to stub runner (no real agy, no quota, no token spend) |
| Type check | `bunx tsc --noEmit` → **0 errors** in test + `src/agy/**` (render.ts cast fixed) |
| Containment | tracked modifications **0**; `openclaw/modo0/` preserved; real quota snapshot hash `c69d2eaeb48e5741…` unchanged (asserted in-test AND verified live) |
| Rollback boundary | `--uninstall` (restore backup) + `touch ~/.config/ai-stack/force-native` (kill switch) + project opt-out (redeclare agent in project `.opencode/opencode.json`) + Web restart. Files: delete template/prompt/render/installer/launcher + batch-4 test blocks; revert SKILL/spike edits |

Budget 4: tests +176 (519→695) + template 29 + prompt 24 + render 12 + installer 71 + launcher 18 + SKILL +14 + spike +5 = **348/400**.

### Batch 4 notes

- Contract-before-binding honored: depth/permission/recursion tests failed RED against a repo with NO binding files, then went green as each file landed.
- Installer renders via pure `src/agy/render.ts` (`@prompt-file` marker → prompt text); no jq dependency; timestamped backups never silently overwrite.
- Launcher only STARTS a new isolated process; it never restarts or signals an existing Web server.
- 4.3 adaptation (documented): full `task(sdd-explore)` through a live cheap-router LLM would consume real model tokens/quota — outside apply constraints. Evidence chain instead: config loads in real isolated Web boot + the router's exact bash call (`agy-explore --input req.json`) proven through the INSTALLED shim with stub-agy, exactly-once persistence asserted.
- Web boot log showed one unrelated pre-existing error (user's engram.ts plugin `name.replace` bug) — not caused by this change; reported, untouched.

### Threat matrix status (final)

- Git repository selection: unit + integration + contract covered (porcelain pre/post; stub mutation blocks).
- Filesystem containment: workdir-only spawn, repo never `--add-dir`'d, installer touches only `~/.config/ai-stack/` (temp HOME in tests).
- Nested task routing: contract-tested (router task-perm exact `{*:"deny","sdd-explore-fallback":"allow"}`; fallback `tools.task:false` + deny-all; `subagent_depth:2`).
- Live config / secrets: never modified; quota snapshot hash-asserted immutable.

### Files changed (cumulative, all batches)

`src/agy/{outcomes,quota,validate,persist,spawn,backend,cli,metrics,report,render}.ts` · `tests/agy-router.test.ts` (81 tests: 56 unit / 10 integration / 12 contract / 4 smoke — counts overlap filters) · `tests/helpers/stub-agy.sh` · `config/opencode-router.template.json` · `config/prompts/sdd-explore-router.md` · `scripts/install-router-config.sh` · `scripts/opencode-web.sh` · `.opencode/skills/antigravity-explore/SKILL.md` (modified) · `antigravity-spike/run-antigravity-task.sh` (modified) · tasks.md 24/24 checked · apply-progress.md (this artifact).

### Remaining tasks

**None.** 24/24 complete. Change ready for `sdd-verify`.

### Issues found (not caused by this change)

- Pre-existing: 3 TS18046 in `tests/openclaw-compose.test.ts`.
- Pre-existing: user plugin `~/.config/opencode/plugins/engram.ts` throws `name.replace is not a function` at Web boot (observed in isolated smoke log).
- Engram testing-capabilities cache stale.

### Evidence revision (after Batch 4 — FINAL)

- `config/opencode-router.template.json` `1ab8763cd8e59ef630fda6ece0bad6133f7179f294d252d9bd76b79cc3ccb79b`
- `config/prompts/sdd-explore-router.md` `bf88ea25afcf3d475dec6bc0feabe9bb1e3cd2aaee8e38a37c7acb199a9bea73`
- `src/agy/render.ts` `a5d22211c154d977466c3628a970d5740d0df860cf27d29f75677582d62b1e26`
- `scripts/install-router-config.sh` `394bc5075e5378f5932111fb3fb8dbc2418fb1ba69444125b407f76ab0575765`
- `scripts/opencode-web.sh` `cc1bc1767a2bf441b717f4655aaa4757445512b1ab5a4386b1fdaabdb60e9762`
- `tests/agy-router.test.ts` `ebfeda890c6c702050222eb61482271e26b17798d61ed4be54a3cd317ed45b94`
- `.opencode/skills/antigravity-explore/SKILL.md` `1bc93b6ce46acfc241b1656c6acd74deabf4dfce51f2a06444398331ed2a9eb5`
- (Earlier batches unchanged: stub `81e643ca…`, outcomes `95bca1d0…`, quota `617e409b…`, persist `1e6a1a3f…`, spawn `63a73d8f…`, validate `aee6768f…`, backend `fa2c20e4…`, cli `fa5dd7e8…`, metrics `904b4a12…`, report `94bd85ef…`)
- **Combined content hash (all change files): `ead1feb3a9b61045082c6dbe71aa4325d7f2cf22b7c2d0238a4c65a11e252e2d`**
- GREEN proof: 12/0 contract · 4/0 smoke · 665/0 full suite · 0 slice tsc errors
