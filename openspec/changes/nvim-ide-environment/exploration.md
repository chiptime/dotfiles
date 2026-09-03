# Neovim IDE Environment — Exploration

**Change:** `nvim-ide-environment`
**Repo:** `/home/bruno/.dotfiles`
**Phase:** Explore (input to proposal)
**Goal:** Replace Antigravity IDE + VSCode with Neovim inside WezTerm, cutting WSL2 memory load. This artifact catalogs the **current** Neovim config state so the proposal can scope the three roadmap phases (vtsls, persistent terminal, AI commit).

All evidence is against `editors/nvim/` (a dotfiles-owned Neovim IDE config), the routemap `doc/antigravity-to-nvim-routemap.md`, and the canonical SDD spec `openspec/specs/antigravity-sdd-explore-routing/spec.md`.

---

## Executive summary — findings that shape the proposal

- **P0 (vtsls) is a one-line reorder, not a rewrite.** `lua/lang/frontend.lua:35` lists `names = { "ts_ls", "tsserver" }`. The `server_choices` resolver (`lang/init.lua:66-74`) picks the **first** name whose availability check passes; on the native LSP path (`lsp.lua:197-199`) that check is `function() return true end`, so putting `vtsls` first is the whole change. Confirmed `vtsls` is a first-class mason-lspconfig server (installed `mason-lspconfig.nvim` `filetype_mappings.lua` lines 112/114/245/247).
- **Mason auto-install is OFF; every server/parser is manual.** `lsp.lua:141-143` and `lsp.lua:188-190` set `automatic_installation=false`, `ensure_installed={}`; `treesitter.lua:37` sets `auto_install=false`. So the proposal must plan an explicit `:MasonInstall vtsls` step and (optionally) verifying the `tsx`/`typescript` parsers exist — nothing installs itself.
- **P1 (persistent terminal) needs a NEW plugin decision.** `config/terminal.lua:24` (and the identical `terminal_legacy.lua:24`) uses `bufhidden = "wipe"`, killing the dev server on close. Neither `snacks.nvim` nor `toggleterm.nvim` is in `lazy-lock.json`. This is the only phase that adds a dependency — an explicit product fork (snacks vs toggleterm), and it collides with the README's documented boundary "keep long-running servers in an external terminal/tmux."
- **P2 (AI commit) has a free keymap and a working binary, but zero existing plumbing.** `opencode` is on PATH at `/home/linuxbrew/.linuxbrew/bin/opencode`. `<leader>gc` is **free** (no collision across the `g` group). No commit-ai/opencode integration exists anywhere in `editors/nvim/`. It also departs from the README boundary that opencode belongs in an external agent/tmux.
- **Legacy/native routing is the single biggest design constraint.** `features.legacy` (`config/features.lua:1-23`) switches terminal, navigation, IDE layout, and LSP setup. Both `terminal.lua` and `terminal_legacy.lua` are functionally identical today (`bufhidden="wipe"`), so P1 must be applied in **both** files or the legacy route silently keeps the broken behavior.
- **Current capability is strong on view/search/git, weak only on the 3 roadmap legs.** Navigation (fzf-lua), diff/blame (gitsigns/diffview), and the IDE-layout sidebar are already wired. The gaps are exactly P0/P1/P2 — no discovery of additional broken workflows.

---

## 1. Current state inventory

### 1.1 File map

```
editors/nvim/
  init.lua                     # entrypoint; loads config.* and routes terminal by features.legacy
  lazy-lock.json              # exact plugin version pins (31 plugins)
  lua/
    config/
      options.lua             # global options (relativenumber, splitright, etc.)
      filetypes.lua           # filetype.add() mapping (bat/ps1/py/cds…)
      keymaps.lua             # global normal-mode maps (w/q/x, Esc→nohlsearch)
      autocmds.lua            # on_yank highlight, BufReadPost last-position
      features.lua            # legacy vs new-route decision (immutable)
      lazy.lua                # lazy.nvim bootstrap + defaults
      terminal.lua            # NEW route: native terminal (bufhidden="wipe")
      terminal_legacy.lua     # LEGACY route: native terminal (identical body)
      ide.lua                 # NEW-route IDE sidebar coordinator (neo-tree/grug-far/neogit/flog/dap)
    lang/
      init.lua                # registry aggregator: filetypes/treesitter/lsp_servers
      data.lua  lua.lua  shell.lua  markdown.lua  git.lua
      frontend.lua            # *** P0 target: ts_ls server_choices ***
      sap.lua  powershell.lua  batch.lua  python.lua
    plugins/
      init.lua                # import order: navigation_legacy, navigation, ide, ui, treesitter, lsp, completion, format, lint, git
      navigation_legacy.lua   # nvim-tree (cond=legacy)
      navigation.lua          # fzf-lua
      ide.lua                 # neo-tree/grug-far/neogit/fugitive/flog/dap/dap-ui (cond=new_route)
      ui.lua                  # which-key + lualine
      treesitter.lua          # nvim-treesitter, auto_install=false
      lsp.lua                 # m a s o n + lspconfig + server setup by registry
      completion.lua          # nvim-cmp
      format.lua              # conform.nvim
      lint.lua                # nvim-lint
      git.lua                 # gitsigns + diffview
```

### 1.2 Key runtime facts

| Fact | Evidence | Implication |
|------|----------|-------------|
| `mapleader` = space | `init.lua:1` | All `<leader>` maps are space-prefixed |
| Legacy flag | `features.lua:1-23` | `legacy=true` iff Neovim `<0.11` OR `NVIM_IDE_LAYOUT=0`; immutable |
| Lazy default | `lazy.lua:23-26` | Every plugin is `lazy=true` unless it declares its own trigger |
| Mason auto-install OFF | `lsp.lua:141-143,188-190` | Servers must be installed manually |
| Treesitter auto-install OFF | `treesitter.lua:37` | Parsers must be pre-installed |
| Native LSP path used | `lsp.lua:195` (`native_lsp_config_available`) | On 0.11+ → `vim.lsp.config` + `vim.lsp.enable` |

---

## 2. Capability map — the four Antigravity workflows

### 2.1 View / search / edit code

| Capability | Present? | Keymap / trigger | Evidence |
|------------|----------|------------------|----------|
| File tree / explorer | ✅ | `<leader>e` (explorer), `<leader>E` (reveal) | `config/ide.lua:384-385` |
| Open editors (buffers) | ✅ | `<leader>fo` | `config/ide.lua:386` |
| Find files | ✅ | `<leader>ff` | `plugins/navigation.lua:6` |
| Live grep | ✅ | `<leader>fg` | `plugins/navigation.lua:7` |
| Find buffers | ✅ | `<leader>fb` | `plugins/navigation.lua:8` |
| Doc / workspace symbols | ✅ | `<leader>fs`, `<leader>fS` | `plugins/navigation.lua:9-10` |
| IDE Search (grug-far) | ✅ | `<leader>fG` | `config/ide.lua:387` |
| Definitions/references/hover | ✅ | `gd` `gD` `gr` `gi` `K` | `plugins/lsp.lua:224-228` |
| Rename / code action | ✅ | `<leader>rn`, `<leader>ca` | `plugins/lsp.lua:229-230` |
| Diagnostics nav | ✅ | `[d` `]d`, `<leader>cd` | `plugins/lsp.lua:231-233` |
| Completion | ✅ | `InsertEnter` (nvim-cmp) | `plugins/completion.lua:4` |
| **TypeScript LSP quality** | ⚠️ **BROKEN** | `ts_ls` (old tsserver bridge) | `lang/frontend.lua:35` |

**Gap (P0):** `lang/frontend.lua:33-56` only configures `ts_ls`/`tsserver` via `server_choices`. User reports "nvim doesn't work well at all" for TS — root cause is the old `ts_ls` bridge.

### 2.2 Git view + file history

| Capability | Present? | Keymap | Evidence |
|------------|----------|--------|----------|
| Hunks inline (signs) | ✅ | `]h` `[h` | `plugins/git.lua:77-91` |
| Preview hunk | ✅ | `<leader>gp` | `plugins/git.lua:92` |
| Reset hunk | ✅ | `<leader>gr` | `plugins/git.lua:93` |
| Stage hunk | ✅ | `<leader>gs` | `plugins/git.lua:94` |
| Undo stage hunk | ✅ | `<leader>gu` | `plugins/git.lua:95` |
| Blame (float / column) | ✅ | `<leader>gb` / `<leader>gB` | `plugins/git.lua:96-97` |
| Diff this file | ✅ | `<leader>gD` | `plugins/git.lua:98` |
| Hunks → quickfix | ✅ | `<leader>gQ` | `plugins/git.lua:99` |
| Diff review | ✅ | `<leader>gd` | `plugins/git.lua:123-128` |
| File history | ✅ | `<leader>gH` | `plugins/git.lua:130-135` |
| Close diff | ✅ | `<leader>gq` | `plugins/git.lua:136` |
| Source Control panel | ✅ | `<leader>gC` | `config/ide.lua:388` |
| Git graph history (flog) | ✅ | `<leader>gG` | `config/ide.lua:389` |

**Gap:** none blocking. The `g` group is fully occupied with single-letter/unpublished pairs; `<leader>gc` is the only unused `g`-letter, giving clean room for P2.

### 2.3 AI-generated commit message (P2 — NEW)

| Piece | Present? | Evidence |
|-------|----------|----------|
| `opencode` binary on PATH | ✅ | `/home/linuxbrew/.linuxbrew/bin/opencode` |
| A `<leader>gc` commit keymap | ❌ | not defined |
| opencode-to-COMMIT_EDITMSG plumbing | ❌ | not defined |
| Conventional-commit assistant | ❌ | not defined |

`<leader>gc` is **free**: current `g` maps are `p r s u b B D Q t` (gitsigns), `d H q` (diffview), and `C G` (ide). No collision.

### 2.4 Run dev server for manual testing (P1 — NEW)

| Piece | Present? | Evidence |
|-------|----------|----------|
| Native terminal split | ✅ | `:Terminal`, `<leader>tt/tT/tc` | `config/terminal.lua:40-73` |
| **Persistent / toggleable terminal** | ❌ | `bufhidden="wipe"` | `config/terminal.lua:24` |
| snacks.terminal / toggleterm | ❌ not installed | not in `lazy-lock.json` |
| Manual browser testing | ✅ outside nvim (stays there) | routemap line 31 |

> Note: `config/terminal_legacy.lua` is byte-identical to `config/terminal.lua` (both `bufhidden="wipe"`). Both must change in P1, or the legacy route keeps the bug.

---

## 3. Conventions catalog

### 3.1 Leader-key map (collision reference)

**Prefix groups in use** (space leader):

| Prefix | Children | Owner |
|--------|----------|-------|
| `<leader>` + `w q x` | write / quit / close-buffer | `config/keymaps.lua:4-6` |
| `<leader>f` + `f g b s S` | files, grep, buffers, doc/ws symbols | `plugins/navigation.lua:6-10` |
| `<leader>f` + `o G` | open editors, IDE search | `config/ide.lua:386-387` |
| `<leader>g` + `p r s u b B D Q t` | gitsigns hunks/blame | `plugins/git.lua:92-100` |
| `<leader>g` + `d H q` | diffview | `plugins/git.lua:123-136` |
| `<leader>g` + `C G` | source control, git graph | `config/ide.lua:388-389` |
| `<leader>c` + `f F l L` | format, lint | `plugins/format.lua:183-197`, `plugins/lint.lua:227-240` |
| `<leader>t` + `t T c` | terminal | `config/terminal.lua:64-72` |
| `<leader>r n`, `<leader>c a`, `<leader>c d` | rename, code action, diagnostics | `plugins/lsp.lua:229-231` |
| `<leader>b`, `<leader>e`, `<leader>E`, `<leader>m`, `<leader>d d` | sidebar, explorer, reveal, maximize, debug | `config/ide.lua:383-391` |

**Free for P2:** `<leader>gc`.
**For P1:** keep terminal under `<leader>t`; `<leader>tt` is the natural toggle target (already the muscle-memory key).

### 3.2 `features.legacy` flag mechanism

`config/features.lua` returns an **immutable** table (`__newindex` raises):

- `legacy = true` when Neovim `<0.11` **or** `NVIM_IDE_LAYOUT=0`.
- Consumers: `init.lua:15-23` (terminal + ide route), `plugins/ide.lua:1-2` (`new_route = not features.legacy`), `plugins/navigation_legacy.lua:1-2` (`legacy`), `plugins/ui.lua:2` (tree filetype).

Consequence: any change that must reach a legacy user (e.g. P1 terminal persist) has to touch **both** `terminal.lua` and `terminal_legacy.lua`, or gate a non-legacy-only path. The proposal should decide whether legacy is still supported or should be deprecated.

### 3.3 `lang/` registry drives LSP / treesitter / filetypes

`lang/init.lua` aggregates a fixed module list. Each module is a plain table:

| Table key | Purpose | Example |
|-----------|---------|---------|
| `filetypes` | fed to `plugins/format.lua`, `plugins/lint.lua`, `plugins/lsp.lua`, `plugins/treesitter.lua` via `lang.filetypes()` | `git.lua:2`, `frontend.lua:2-11` |
| `parsers` | fed to `lang.treesitter_parsers()` → `treesitter.lua:34` | `frontend.lua:12-20` |
| `servers` | static server configs | `lua.lua:4-34`, `data.lua:4-35` |
| `server_choices` | **ordered** list of `{names={…}, config={…}}`; first available wins | `frontend.lua:33-56`, `python.lua:4-21` |

**Resolution logic (`lang/init.lua:66-74`):** for each `server_choices` entry, iterate `names`, and stop at the first where `server_available(registry, name)` is true. `server_available` returns `true` when registry is nil, delegates when registry is a function, else looks up `registry[name]`. On the native path the registry is `function() return true end`, so **the first name always wins** → reorder `names` to put `vtsls` first.

### 3.4 Plugin lazy-load conventions

| Trigger type | Used by |
|--------------|---------|
| `cmd = "Mason"` | `plugins/lsp.lua:127` |
| `event = "InsertEnter"` | `plugins/completion.lua:4` |
| `event = "VeryLazy"` | `plugins/ui.lua:7,15` (which-key, lualine) |
| `cmd` + `keys` | `plugins/navigation.lua:3-11` (fzf-lua), `plugins/git.lua:111-121` (diffview) |
| `cmd` + `event` | `plugins/git.lua:42-46` (gitsigns) |
| `cmd` + `ft` + `event` + `keys` | `plugins/format.lua:171-197`, `plugins/lint.lua:219-241` |
| `cond = new_route` / `cond = legacy` | `plugins/ide.lua:6`, `plugins/navigation_legacy.lua:7` |

### 3.5 Mason + server setup path

- `mason.nvim` (`lsp.lua:126-133`): `cmd="Mason"`, rounded border.
- `mason-lspconfig.nvim` (`lsp.lua:134-144`): `ft=lang.filetypes()`, `ensure_installed={}`, `automatic_installation=false`.
- `nvim-lspconfig` (`lsp.lua:145`+): `cmd` + `ft`; config registers `vim.lsp.config`+`vim.lsp.enable` on native path, else falls back to `lspconfig[server].setup()`.
- A server setup only happens if `server_enabled(config)` is true (`lsp.lua:15-27,238`); the `condition`-key (used by `powershell.lua`) is stripped before handoff (`lsp.lua:30`).

### 3.6 Terminal selection

`init.lua:15-22`:
```
if features.legacy then require("config.terminal_legacy")
else
  require("config.terminal")
  if filereadable(ide.lua) then require("config.ide") end
end
```
Both terminal modules are currently identical. The `ide.lua` coordinator is **non-legacy only**.

---

## 4. Risks & gaps

| # | Risk / gap | Evidence | Impact on roadmap |
|---|------------|----------|-------------------|
| R1 | `bufhidden="wipe"` kills the process on close (terminal split) | `config/terminal.lua:24`, `terminal_legacy.lua:24` | **P1 core bug.** Must change to `hide` + add a real toggle, or processes die. Both files must change (legacy route). |
| R2 | Neither snacks.nvim nor toggleterm.nvim is installed | `lazy-lock.json` (absent) | **P1 introduces the only new dependency.** Product fork: snacks (bigger suite) vs toggleterm (leaner). |
| R3 | Mason auto-install OFF → vtsls won't appear until manually installed | `lsp.lua:141-143,188-190` | **P0.** Add explicit `:MasonInstall vtsls` step + verification (`:LspInfo` shows `vtsls`). |
| R4 | vtsls TypeScript SDK resolution (mason-lspconfig `typescript.lua` resolves `tsdk`) | installed `mason-lspconfig.nvim/lua/mason-lspconfig/typescript.lua` | **P0.** May need `init_options` with `typescript.tsdk` for full feature parity (inlay hints, workspace). Verify `cmd` auto-resolves on the native path; add explicit `cmd` if not. |
| R5 | Legacy LSP path would silently drop TS if `vtsls` becomes the only name and the installed `lspconfig` lacks a `vtsls` key | `lsp.lua:64-74` (`setup_legacy_lsp`) | **P0.** Keep `ts_ls`/`tsserver` after `vtsls` in `names` so pre-0.11 still resolves. |
| R6 | Treesitter `tsx`/`typescript` parsers need pre-install (`auto_install=false`) | `treesitter.lua:37` | P0 acceptance could be masked by missing parser. Verify `:TSInstallInfo` shows `tsx` + `typescript`. |
| R7 | README documents an explicit boundary: long-running servers/agents stay OUTSIDE nvim | `README.md:3,15` | **P1/P2 are intentional departures.** Update README (docs-only) or the boundary becomes contradictory. |
| R8 | `<leader>gs` stages only the current hunk (gitsigns); AI commit may need full-stage | `plugins/git.lua:94` | **P2 UX gap.** Consider a stage-all step (`git add -A`/`:Git add`) before `opencode run`, or document "stage first". |
| R9 | `opencode run` non-interactive behavior unverified from nvim; WSL network for Tailscale/opencode backend | `opencode` at `/home/linuxbrew/.linuxbrew/bin/opencode` | **P2.** Validate `opencode run` produces a conventional commit from a staged diff and returns cleanly to nvim. |
| R10 | `ide.lua` maps `K`→hover globally (`ide.lua:392`) and `lsp.lua` maps `K`→hover buffer-local (`lsp.lua:228`) | both | Minor: buffer-local wins; both do the same thing. Not a blocker, note for P0 review. |
| R11 | `ide.lua` hard-codes a `WIDTH = 34` and assumes a "central" normal editor window (`ide.lua:2,27-30`) | `config/ide.lua` | P1 terminal toggle should integrate with (not fight) the IDE layout's window management. |
| R12 | `server_choices` first-wins means ordering is the ONLY switch; no per-project `server` selection | `lang/init.lua:66-74` | P0 note: users can't pick vtsls vs ts_ls per project; comes as a config reorder, not a runtime choice. |

---

## 5. Recommendations for the proposal

### 5.1 Scope slices (suggested, matching routemap phases)

- **Slice 1 — vtsls (P0, S);** files: `lang/frontend.lua` (+ README optional-tool list).
  - Reorder `frontend.lua:35` `names` to `{ "vtsls", "ts_ls", "tsserver" }` (keeps legacy fallback).
  - Optionally add `init_options`/tsdk handling; verify `:LspInfo` shows `vtsls` and `K`/completions respond.
  - Include a runtime doc step: `:MasonInstall vtsls` (after Mason opts allow it) — note `automatic_installation` stays OFF.
- **Slice 2 — persistent terminal (P1, S);** files: `config/terminal.lua` + `config/terminal_legacy.lua` (+ a new terminal plugin entry in `plugins/`).
  - Introduce snacks.terminal (or toggleterm) as a lazy `cmd`/`keys` plugin.
  - Replace `bufhidden="wipe"` with a hide-on-close/toggle model; bind the existing `<leader>tt` to toggle.
  - GUI-side: rely on WezTerm hosting a single nvim (no tmux panes) per user preference.
- **Slice 3 — AI commit (P2, S);** files: new module (e.g. `config/git.lua` or added keys in `plugins/git.lua`).
  - `<leader>gc`: `git diff --cached` (or full-staged diff) → `opencode run` (conventional-commit prompt) → open `COMMIT_EDITMSG` buffer for review → `:wq` commits.
  - Keep gitsigns for hunks; the AI commit only needs to read the staged diff.
- **Slice 4 — (optional, deferred) neotest-vitest (P3, M):** explicitly non-goal for this change per routemap affordance; tests already run outside nvim.

### 5.2 Non-goals (recommended)

- Not re-adding Antigravity/VSCode GUI; opencode **web** (Tailscale from phone) stays — it's not the ram culprit.
- Not replacing manual browser testing (stays in the browser; nvim only owns the persistent terminal).
- Not adding a tmux layer — user prefers everything as nvim buffers in WezTerm.
- Not auto-installing Mason servers (config keeps `automatic_installation=false`; verification is a doc/manual step).
- Not implementing a full agent-panel split (opencode TUI) or tests-from-editor in this cycle.

### 5.3 Open product questions for the user

1. **Terminal plugin fork (P1):** snacks.nvim (richer, bigger dep tree) vs toggleterm.nvim (leaner, single-use)? This is the only new dependency and the biggest scope decision.
2. **Legacy route (P0/P1):** is `NVIM_IDE_LAYOUT=0` / pre-0.11 still supported? If not, P0/P1 can target only `terminal.lua` + `lang/frontend.lua` non-legacy path and simplify; if yes, both terminal variants + a guarded pick must change.
3. **AI commit trigger (P2):** should `<leader>gc` require a pre-staged area, or stage-all behind the scenes first? (Affects the "AI writes the message but you review" contract.)
4. **vtsls parity bar (P0):** does "works well" mean full workspace/inlay-hint parity with VSCode (tsdk config) or just reliable completion/navigation? Sets R4 effort.
5. **Commit prompt style (P2):** strict conventional-commits, or match the repo's existing commit history style?

---

## Next step

Feed this to `sdd-propose` to produce the change proposal (slices, acceptance criteria, and answers to the open questions above).

## Skills loaded

- `cognitive-doc-design` (this artifact's structure).
