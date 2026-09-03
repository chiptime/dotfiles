# Tasks: Neovim IDE Environment

Conventions: paths are repo-rooted (`editors/nvim/...`). "Headless" = `nvim --headless -l <ephemeral-script>` (scratch scripts stay out of commits). Zero-line tasks are verification/runtime steps; record evidence in the PR body.

## Review Workload Forecast

| Slice | PR | Est. changed lines (add+del) | Risk vs 400 |
|---|---|---|---|
| P0 vtsls parity | PR 1 | ~45 (40–60) | Low |
| P1 terminal + bufferline | PR 2 | ~350 (300–400) — terminal.lua rewrite adds ~180 / deletes 73 | Medium |
| P2 AI commit | PR 3 | ~180 (150–220) | Low |
| **Total** | — | **~575 (490–680)** | Under the 800-line budget |

### Suggested Work Units

| Unit | Goal | PR | Focused check | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| WU-P0 | vtsls-first TS LSP + inlay hints | PR 1 | headless registry-order assert | manual parity battery (acceptance repo) | revert `frontend.lua` + `lsp.lua` |
| WU-P1a | toggleterm profiles, one-visible cycling, keymaps | PR 2 | headless invariants batch | PID + profile-fixture checks | revert `plugins/terminal.lua`, `config/terminal.lua`, import, pins |
| WU-P1b | bufferline.nvim top line | PR 2 | headless load + confirm-close | sidebar `winwidth == 34` check | revert `plugins/ui.lua` hunk |
| WU-P2 | `<leader>gc` AI commit flow | PR 3 | headless abort classes | `:wq`/`:q` semantics on scratch repo | revert `config/aicommit.lua` + `init.lua` line |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: Medium

Total exceeds one 400-line PR, so delivery is 3 chained PRs (P0 → P1 → P2), per `auto-chain`. The user still picks the chain strategy (stacked-to-main vs feature-branch-chain) before PR 1 opens; PR 2 is the tight one — if cycling code grows, split WU-P1b into PR 3.

## Phase 1 — Slice P0: vtsls parity (PR 1)

- [x] 1.1 vtsls-first ordering + settings — `editors/nvim/lua/lang/frontend.lua`: `names = { "vtsls", "ts_ls", "tsserver" }`; add `config.settings` per design Decision 1 (`vtsls.autoUseWorkspaceTsdk`, `typescript.inlayHints.*`, `updateImportsOnFileMove = "always"`, `suggest.completeFunctionCalls`); no explicit `cmd`. Deps: none. Verify: headless assert ordered names; spec "Native path attaches vtsls". Est: ~35.
- [x] 1.2 Inlay hints on attach — `editors/nvim/lua/plugins/lsp.lua` existing `LspAttach`: `vim.lsp.inlay_hint.enable(true, { bufnr })` guarded by `supports_method("textDocument/inlayHint")`. Deps: 1.1. Verify: hints render on TS buffer; no error on non-supporting server. Est: ~10.
- [x] 1.3 Runtime steps + parser gate — run `:MasonInstall vtsls`; `:TSInstallInfo` shows `typescript` + `tsx`; confirm no install starts before that. Deps: 1.1. Verify: spec "Explicit install enables vtsls", "No silent auto-install". Est: 0.
- [x] 1.4 Headless fallback test — script builds registry without `vtsls`, asserts `ts_ls`/`tsserver` resolution. Deps: 1.1. Verify: spec "Legacy fallback retained". Est: 0.
- [ ] 1.5 Manual parity battery — acceptance repo (named at apply time): `:LspInfo` = vtsls; `gd` cross-file; `<leader>rn` propagates; `K` hover; hints visible; auto-import completion + add-import source action. Deps: 1.1–1.4. Verify: all "Parity Acceptance Battery" scenarios; record results in PR. Est: 0.

Work-unit commit (WU-P0): `feat(nvim): prefer vtsls with tsdk settings and default inlay hints`.

## Phase 2 — Slice P1: terminal + bufferline (PR 2)

- [x] 2.1 RED profile-parser fixtures — headless: valid `.nvim/terminals.json` parses as data only (never executed); payload-bearing file stays data or is rejected; sibling `.nvim/terminals.lua` ignored; unparsable == absent + one WARN. Written first; expect failure before 2.3. Deps: none. Verify: threat row "Documentation-like paths". Est: 0.
- [x] 2.2 toggleterm plugin spec — create `editors/nvim/lua/plugins/terminal.lua`: lazy event, `cond = not features.legacy`, opts (botright horizontal split preserved). Deps: none. Verify: headless spec loads; absent on legacy route. Est: ~30.
- [x] 2.3 Rewrite `editors/nvim/lua/config/terminal.lua` — profile parse via `vim.json.decode` under `pcall` (schema `{version, terminals:[{name,cmd}]}`, unknown keys ignored); registry with panel group counts 1–8 (count 9 reserved — NO code, see note); one-visible cycling via `terminal.get_all(true)` close-visible + `:open()` next; keymaps `to/tn/t]/t[/tq/ts/tp`; delete `:Terminal`, `tt/tT/tc` with no rebind; keep `<Esc><Esc>`. Deps: 2.1, 2.2. Verify: 2.1 fixtures green; spec "Cycling shows one at a time", "Old entries gone". Est: ~180 added / ~73 deleted.
- [x] 2.4 PID preservation check — `jobpid()` before hide, after cycle, after re-show; PIDs equal. Deps: 2.3. Verify: spec "Silent hide preserves process". Est: 0.
- [x] 2.5 Headless invariants — cycling t1→t2→t1 never shows two windows; silent hide (no messages); zero terminals after startup; sidebar `winwidth == 34` and central window unchanged before/after toggles. Deps: 2.3. Verify: spec cycling, "Clean startup", "Layout invariant under toggling". Est: 0.
- [x] 2.6 Profile fixtures runtime — absent → plain shell immediately; valid 2-terminal profile → both start honoring one-visible via `tp`; unparsable → treated as absent + WARN. Deps: 2.3. Verify: spec "Profile starts whole", "Missing profile never blocks". Est: 0.
- [x] 2.7 Legacy entries gone — `:Terminal` and `tt/tT/tc` undefined; which-key lists no stale bindings; `git diff` on `config/terminal_legacy.lua` is empty. Deps: 2.3. Verify: spec "Old entries gone", "Legacy route untouched". Est: 0.
- [x] 2.8 bufferline.nvim — `editors/nvim/lua/plugins/ui.lua`: `akinsho/bufferline.nvim`, `event = "VeryLazy"`, `cond = not features.legacy`, `close_command`/`right_mouse_command = "confirm bdelete %d"`, `offsets` reusing the existing `tree_filetype` variable. Deps: none. Verify: headless load; unsaved-buffer close confirms (no force-write). Est: ~50.
- [x] 2.9 Bufferline scenarios — click navigates; close indicator removes entry; last-but-one close keeps editor usable; sidebar width unchanged. Deps: 2.8. Verify: all nvim-buffer-line spec scenarios. Est: 0.
- [x] 2.10 Import — `editors/nvim/lua/plugins/init.lua`: add `{ import = "plugins.terminal" }`. Deps: 2.2. Verify: headless startup resolves both specs. Est: +1.
- [x] 2.11 Pins — `editors/nvim/lazy-lock.json`: pin toggleterm.nvim + bufferline.nvim. Deps: 2.2, 2.8. Verify: `Lazy restore` clean. Est: ~4.
- [x] 2.12 README terminal boundary — `editors/nvim/README.md`: keymap table, profile format, no-auto-start, one-visible. Deps: 2.3. Verify: doc matches behavior. Est: ~10.

Note: reserved agent-panel pattern is documentation-only (design.md "Reserved agent-panel pattern"); task 2.3 must ship no count-9 logic — the reserved design text is the deliverable.
Work-unit commits: WU-P1a = 2.1–2.7, 2.10–2.12 `feat(nvim): toggleterm profiles with one-visible cycling`; WU-P1b = 2.8–2.9 `feat(nvim): bufferline with confirm-close and sidebar offsets`.

## Phase 3 — Slice P2: AI commit (PR 3)

- [ ] 3.1 RED git-root selection — headless: buffer in a subdirectory targets its own repo root via `vim.fs.root(0, { ".git" })` with explicit `cwd` on every git call; non-repo aborts. Written first; expect failure before 3.3. Deps: none. Verify: threat row "Git repository selection". Est: 0.
- [ ] 3.2 RED commit-state checks — empty index aborts; unstaged-only never stages anything; staged + dirty worktree commits index only. Written first; expect failure before 3.3. Deps: none. Verify: threat row "Commit state". Est: 0.
- [ ] 3.3 Create `editors/nvim/lua/config/aicommit.lua` — flow per design Data Flow: repo root → index check (`git diff --cached --quiet` exit 0 → abort) → size gate (`#diff >= 65536` → `git diff --cached --stat` + `^@@` headers from `--unified=0`, capped at 200 + explicit truncation line) → `opencode run` via `vim.system` (90 s timeout, explicit cwd) → six abort classes (non-repo; empty index; exit ≠ 0; timeout; empty output after ANSI-strip + trim; first non-empty line failing the design's Conventional-Commits regex) each notify-and-abort, never opening a buffer; success opens `COMMIT_EDITMSG` buffer with `BufWriteCmd` → `git commit -F <file> --cleanup=strip`; `:q` discards; prompt is a Lua literal in this file; the flow never runs `git add`. Deps: 3.1, 3.2. Verify: all nvim-ai-commit spec scenarios. Est: ~170.
- [ ] 3.4 Wire — `editors/nvim/init.lua`: add `require("config.aicommit")`. Deps: 3.3. Verify: headless startup; `<leader>gc` defined; no existing `<leader>g` map shadowed. Est: +1.
- [ ] 3.5 Abort-class verification — headless per class: notification shown, no `COMMIT_EDITMSG`, no commit, index unchanged. Deps: 3.3. Verify: spec "Empty staging aborts", "Never auto-stages", "Failure aborts cleanly". Est: 0.
- [ ] 3.6 Threshold fixtures — staged diffs just below and at 65536 bytes; assert payload shape (full diff vs stat+headers; ≤200 headers + truncation line). Deps: 3.3. Verify: spec "Small diff sends full diff", "Large diff sends summary". Est: 0.
- [ ] 3.7 Commit semantics — `:wq` commits the edited text; `:q` leaves index untouched. Deps: 3.3. Verify: spec "Edit then commit", "Quit discards". Est: 0.
- [ ] 3.8 Apply-time stdout check — on first real invocation capture raw `opencode run` output; confirm ANSI-strip + validator accept it before trusting the flow. Deps: 3.3. Verify: design Open Question 2 closed with recorded sample. Est: 0.
- [ ] 3.9 README AI-commit boundary — `editors/nvim/README.md` workflow section documents `<leader>gc` without contradicting the out-of-Neovim boundary. Deps: 3.3. Verify: spec "Boundary matches reality". Est: ~10.

Work-unit commit (WU-P2): `feat(nvim): AI commit from staged diff via opencode`.
