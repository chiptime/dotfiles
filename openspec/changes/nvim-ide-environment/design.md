# Design: Neovim IDE Environment

## Technical Approach

Three additive slices on the non-legacy route. P0 reorders the `lang/` registry and adds server `settings`; P1 rewrites `config/terminal.lua` in place (so `init.lua` routing is untouched) and adds two lazy plugins; P2 adds one plugin-free `config/` module. `terminal_legacy.lua` stays byte-frozen.

## Architecture Decisions

### Decision: vtsls resolves its own cmd and SDK (P0)

**Choice**: no explicit `cmd`. Settings go in the `server_choices` entry's `config.settings`: `vtsls.autoUseWorkspaceTsdk = true`, `typescript.inlayHints.*`, `updateImportsOnFileMove = "always"`, `suggest.completeFunctionCalls`. Client-side, enable `vim.lsp.inlay_hint.enable(true, { bufnr })` in the existing `LspAttach`, guarded by `supports_method("textDocument/inlayHint")`.
**Rejected**: explicit `cmd` / `init_options.typescript.tsdk` — mason-lspconfig injects neither (no `server_configurations/vtsls`); global `vim.lsp.config("*")` — leaks TS keys to every server.
**Rationale**: `nvim-lspconfig/lsp/vtsls.lua` ships `cmd = { "vtsls", "--stdio" }` and a monorepo-aware `root_dir`, and mason sets `PATH = "prepend"`, so the binary resolves after `:MasonInstall vtsls`. `autoUseWorkspaceTsdk` selects `node_modules/typescript`; absent that, vtsls uses its bundled SDK — both spec scenarios, no hardcoded path. `root_markers` stay: inert for vtsls (runtime `lsp.lua:745`, `root_dir` wins) but needed by the `ts_ls` fallback, which shares the config and ignores `vtsls.*` harmlessly.

### Decision: `.nvim/terminals.json`, data-only (P1)

**Choice**: repo-root `.nvim/terminals.json`, decoded by built-in `vim.json.decode` under `pcall`. Schema `{ "version": 1, "terminals": [ { "name": "web", "cmd": "pnpm dev" } ] }`; unknown keys ignored.
**Rejected**: `.nvim-terminals.lua` — `dofile` executes arbitrary code from any cloned repo; YAML — needs a parser dependency.
**Rationale**: zero dependency, no execution surface. Absent when unreadable, undecodable, or yielding no entry with non-empty `name` and `cmd`; then `<leader>tp` opens a plain shell. Parse failure emits one non-blocking `WARN`.

### Decision: `<leader>t` keymaps (P1)

`:Terminal` and `<leader>tt/tT/tc` are deleted and never rebound, so which-key shows no stale entry. Brackets navigate, matching `]h`/`]d`/`]b`.

| Key | Action |
|---|---|
| `<leader>tp` | Start repo profile (fixed by spec) |
| `<leader>to` | Toggle visible terminal |
| `<leader>tn` | New terminal |
| `<leader>t]` / `<leader>t[` | Cycle next / previous |
| `<leader>tq` | Close and shut down current |
| `<leader>ts` | `:TermSelect` |

`<Esc><Esc>` → `<C-\><C-n>` is preserved. toggleterm ships no cycle command, so cycling is ours: `toggleterm.terminal.get_all(true)`, close the visible one, `:open()` the next — enforcing one-visible.

### Decision: 65536-byte staged-diff threshold (P2)

**Choice**: `#(git diff --cached) >= 65536` → send `git diff --cached --stat` plus every `^@@` header from `git diff --cached --unified=0`, capped at 200 headers with an explicit truncation line.
**Rejected**: line counts (minified files defeat them); no cap (argv limits, cost).
**Rationale**: bytes are deterministic for identical repo state and keep argv far below `ARG_MAX`; `@@` headers carry function context for free.
**Failure classification (R9)** — each notifies and aborts, opening no buffer: not a repo; `git diff --cached --quiet` exits 0 (empty index); `opencode run` exit ≠ 0; 90 s `vim.system` timeout; empty output after ANSI-strip and trim; first non-empty line failing `^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(..\))?!?: .+`. The prompt is a Lua literal in `config/aicommit.lua`.

### Decision: bufferline.nvim (P1)

**Choice**: `akinsho/bufferline.nvim`, `event = "VeryLazy"`, `cond = not features.legacy`, declared in `plugins/ui.lua` beside lualine.
**Rejected**: `mini.tabline` — no close indicator, fails the close-from-line scenario; `barbar.nvim` — owns buffer ordering and state, far beyond the capability.
**Rationale**: single purpose, and its only dependency `nvim-web-devicons` is already a lualine dependency. `close_command`/`right_mouse_command = "confirm bdelete %d"` replaces the default `bdelete!`, so unsaved buffers confirm instead of silently losing edits. `offsets` reuses the existing `tree_filetype` variable so the line never draws over the sidebar.

## Reserved agent-panel pattern (documented, unimplemented)

Terminals carry a group. The bottom **panel group** (`count` 1–8) owns the one-visible invariant and cycling. `count = 9` is reserved for a future vertical agent panel — `Terminal:new{ count = 9, direction = "vertical" }` under `<leader>ta`. Cycling never iterates it, so adding it later changes no existing code path. No agent-panel code ships here.

## Integration invariants

- `init.lua` terminal routing is unchanged; P2 adds one line, `require("config.aicommit")`.
- toggleterm's horizontal split is `botright split` (`ui.lua:228`): full width below everything, so `ide.lua`'s `WIDTH = 34` sidebar keeps its width and the central window keeps its identity (R11).
- Repo-root `README.md` is empty; the boundary lives in `editors/nvim/README.md:3,15` — that is the P2 target.

## Data Flow (P2)

    <leader>gc → repo root → index check → size gate → opencode run
                     │            │            │            │
                   abort ←──── abort ←─────────┴──── abort (exit/timeout/invalid)
                                                          │
                                        COMMIT_EDITMSG ←──┘
                                              │ BufWriteCmd → git commit -F
                                              └ :q → discard

## File Changes

| File | Action | Slice | Description |
|---|---|---|---|
| `lua/lang/frontend.lua` | Modify | P0 | `names = { "vtsls", "ts_ls", "tsserver" }`; add `config.settings` |
| `lua/plugins/lsp.lua` | Modify | P0 | Inlay hints on `LspAttach` when supported |
| `lua/plugins/terminal.lua` | Create | P1 | toggleterm spec: `cond`, `cmd`, `opts` |
| `lua/config/terminal.lua` | Rewrite | P1 | Profile parsing, registry, keymaps |
| `lua/plugins/ui.lua` | Modify | P1 | bufferline entry |
| `lua/plugins/init.lua` | Modify | P1 | `{ import = "plugins.terminal" }` |
| `lazy-lock.json` | Modify | P1 | New pins |
| `lua/config/aicommit.lua` | Create | P2 | `<leader>gc` flow + prompt |
| `init.lua` | Modify | P2 | `require("config.aicommit")` |
| `editors/nvim/README.md` | Modify | P1/P2 | Terminal + AI-commit boundaries |
| `lua/config/terminal_legacy.lua` | Unchanged | — | Frozen |

## Testing Strategy

| Slice | What | Approach |
|---|---|---|
| P0 | Parity battery | Manual in the acceptance repo: `:LspInfo`, `:TSInstallInfo`, `gd`/`gr`/`K`/`<leader>rn`, hints visible, auto-import completion and source action |
| P0 | Fallback retained | Headless: registry without `vtsls` still resolves `ts_ls` |
| P1 | PID preservation | `jobpid()` before hide, cycle, re-show, compare |
| P1 | Invariants | Headless: one-visible, silent hide, no auto-start, sidebar `winwidth == 34` before/after toggling |
| P1 | Profile states | Headless fixtures: absent, valid, unparsable |
| P2 | Abort classification | Headless per class (no repo, empty index, exit ≠ 0, timeout, empty output, non-conventional): notify, no buffer, no commit |
| P2 | Threshold | Fixtures just below and at 65536 bytes; assert payload shape |
| P2 | Commit semantics | `:wq` commits edited text; `:q` leaves the index untouched |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | **Applicable** — a repo-versioned file supplies shell commands | JSON parsed as data; no `dofile`/`luaeval`; runs only on explicit `<leader>tp`, never at startup | Payload-bearing `terminals.json` stays data or is rejected; a sibling `.nvim/terminals.lua` is ignored |
| Git repository selection | **Applicable** (P2) | Every git call takes explicit `cwd` from `vim.fs.root(0, { ".git" })`; no inherited relative paths | Buffer in a subdirectory targets its own repo root; non-repo aborts |
| Commit state | **Applicable** (P2) | Empty index aborts; `git add` forbidden; commit only via `git commit -F <file> --cleanup=strip` | Empty index; unstaged-only; staged plus dirty worktree commits the index only |
| Push state | N/A | The change never pushes | — |
| PR commands | N/A | No PR automation | — |

## Migration / Rollout

No migration. One manual step: `:MasonInstall vtsls` (auto-install stays off). Profiles hold no runtime state.

## Open Questions

- [ ] Acceptance repository is named at apply time (P0).
- [ ] `opencode run` stdout decoration is unverified; ANSI-strip plus Conventional-Commits validation is the mitigation.
