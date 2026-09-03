# Apply Progress: nvim-ide-environment — Slice P0 (vtsls parity)

**Status: 4/5 tasks done; task 1.5 (manual parity battery) pending — it requires the user's real acceptance repo and interactive nvim.** Everything automatable was verified headlessly against the live config (`~/.config/nvim` → symlink to `editors/nvim`). No branch, no PR: single work-unit commit on `master`.

## Task status

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1.1 | vtsls-first ordering + tsdk settings in `frontend.lua` | ✅ done | `names = { "vtsls", "ts_ls", "tsserver" }`; `config.settings` exactly per design Decision 1; no `cmd`, no `init_options`; `root_markers` kept. Focused test below (12/12 PASS) |
| 1.2 | Inlay hints on `LspAttach`, capability-guarded | ✅ done | `client:supports_method("textDocument/inlayHint")` guard → `vim.lsp.inlay_hint.enable(true, { bufnr = event.buf })`. Runtime: enabled=true on TS buffer; enabled=false + no error on `html` (negative control) |
| 1.3 | Runtime gates: `:MasonInstall vtsls` + parser check | ✅ done | Headless Mason install OK (`vtsls` 0.3.0 at `~/.local/share/nvim/mason/bin/vtsls`); parsers `typescript.so` + `tsx.so` already present; no silent auto-install (Mason packages dir was empty beforehand; `automatic_installation=false` untouched) |
| 1.4 | Headless fallback test (registry without vtsls) | ✅ done | Ephemeral script (not committed): 12/12 checks PASS, exit 0 |
| 1.5 | Manual parity battery in acceptance repo | ⏳ pending | Requires user's interactive nvim + heaviest real TS repo — see checklist below |

## Verification evidence

### Focused check (WU-P0: headless registry-order assert)

Command: `nvim --headless -l /tmp/opencode/sdd-p0-fallback-test.lua` (ephemeral, not committed)

```
PASS names ordered vtsls, ts_ls, tsserver
PASS native registry attaches vtsls
PASS native registry configures no ts_ls/tsserver
PASS legacy registry without vtsls falls back to ts_ls
PASS legacy fallback configures no vtsls
PASS registry with only tsserver resolves tsserver
PASS fallback keeps root_markers
PASS fallback carries settings
PASS settings.vtsls has only autoUseWorkspaceTsdk (no enableServerLocalizedHint)
PASS typescript.updateImportsOnFileMove == always
PASS typescript.suggest.completeFunctionCalls == true
PASS typescript.inlayHints present
RESULT: all checks passed — exit=0
```

Covers spec scenarios "Native path attaches vtsls" (resolver level) and "Legacy fallback retained".

### Runtime harness (real server, real attach)

Command: `nvim --headless /tmp/opencode/ts-scratch/index.ts -c "luafile /tmp/opencode/sdd-p0-attach-gate.lua" +qa`
(scratch project: `package.json` + `tsconfig.json` + `index.ts` under `/tmp/opencode/ts-scratch/`)

```
[attach-gate] filetype=typescript
[attach-gate] attached=true client_count=1
[attach-gate] client=vtsls inlayHint_supported=true
[attach-gate] inlay_hint enabled=true — exit=0
```

Negative control (server without the capability must not error):

```
[attach-gate] filetype=html
[attach-gate] attached=true client_count=1
[attach-gate] client=html inlayHint_supported=false
[attach-gate] inlay_hint enabled=false — exit=0
```

### Runtime gates (task 1.3)

- Config activation discovered: `~/.config/nvim` is a symlink to `/home/bruno/.dotfiles/editors/nvim` — plain `nvim --headless` uses this config; no `NVIM_APPNAME` needed. Neovim 0.12.4 (native LSP path).
- Parser gate: `parser typescript.so=true tsx.so=true` (headless, post lazy-load) — nothing to install.
- vtsls install: headless Mason (`require("mason-registry")` + explicit `pkg:install()`, waited for the install **stream to close**). `vtsls --version` → `0.3.0`. First attempt aborted mid-link because `:qa` raced the npm subprocess — cleaned and re-run waiting on stream close; success. Interactive equivalent if ever needed: `:MasonInstall vtsls`.
- No silent auto-install: Mason had **zero** packages before the explicit install (while `ts_ls` had been the active server), proving nothing auto-installs; `automatic_installation=false` lines untouched.

## Pending manual steps — task 1.5 parity battery (user, interactive)

Acceptance repo: user's heaviest real TypeScript repo (to be named by the user; `~/.config/nvim` already points at this config).

- [ ] `:LspInfo` reports the attached client as `vtsls`
- [ ] `:TSInstallInfo` shows `typescript` + `tsx` installed (headless check already green — confirm visually)
- [ ] `gd` on an imported symbol lands on its definition in another file
- [ ] `<leader>rn` on a cross-file symbol; all references update (verify via `gr` or grep)
- [ ] `K` on a typed symbol shows a hover float with the type signature
- [ ] Inlay hints render on a TS buffer with no manual command (parameter/type hints)
- [ ] Completion at an unimported symbol offers an auto-import item; accepting it inserts the import
- [ ] Code action (`<leader>ca`) on an unresolved identifier offers an add-import source action

Record results in the PR body (or change log) once run.

## Deviations from tasks.md

| Deviation | Reason |
|---|---|
| Headless Mason install via `mason-registry` Lua API instead of `:MasonInstall` command | `:MasonInstall` is async and cannot be awaited headlessly; the Lua API is the same code path the command uses. First attempt hit a `:qa`-vs-npm race (mason terminated the install on exit); resolved by waiting for the install stream `closed` event |
| Extra negative-control server (`html-lsp`) installed into Mason | Task 1.2 requires "no error on non-supporting server"; no installed server lacked the capability, so one real non-supporting server was needed to prove the guard |
| `client:supports_method(...)` (colon) instead of dot-call | Neovim 0.12 deprecates `client.supports_method(m)` dot-call (removal in 0.13, `runtime/lua/vim/lsp/client.lua:256-268`); colon form works on all supported versions. Surfaced by the 1.3 runtime gate, fixed within the slice |
| Ephemeral scripts under `/tmp/opencode/` (not committed) | Per tasks.md convention: scratch scripts stay out of commits |
| `tasks.md` checkboxes 1.1–1.4 updated and committed with the slice | OpenSpec convention: task completion must be visible in the tasks artifact |

## Work-unit evidence (WU-P0)

| Evidence | Value |
|---|---|
| Focused test | `nvim --headless -l /tmp/opencode/sdd-p0-fallback-test.lua` → 12/12 PASS, exit 0 |
| Runtime harness | TS attach gate → `client=vtsls`, hints enabled; html control → no error, hints off |
| Rollback boundary | Revert `editors/nvim/lua/lang/frontend.lua` + `editors/nvim/lua/plugins/lsp.lua` only (this commit); unrelated work untouched |
| Changed lines | 25 insertions + 1 deletion (26 total) — within the ~45 estimate |

## Next

- Slice P1 (tasks 2.1–2.12) or `sdd-verify` for P0 once the user runs the 1.5 battery.
