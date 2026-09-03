# Dotfiles Neovim IDE

This is the dotfiles-owned Neovim IDE configuration. It is intentionally lightweight: Neovim owns editor-local workflows, while tmux remains the primary home for shells, long-running processes, Pi, and opencode.

## Terminal workflow

On the IDE route, terminals are persistent [toggleterm.nvim](https://github.com/akinsho/toggleterm.nvim) panels: processes survive hide and cycle, at most one terminal window is visible, and nothing starts on its own.

| Key | Action |
|-----|--------|
| `<leader>tp` | Start the repo terminal profile (or a plain shell when none exists) |
| `<leader>to` | Toggle the visible terminal |
| `<leader>tn` | New shell terminal |
| `<leader>t]` / `<leader>t[` | Cycle next / previous terminal |
| `<leader>tq` | Close and shut down the current terminal |
| `<leader>ts` | Select a terminal |
| `<Esc><Esc>` | Leave terminal mode |

Boundaries:

- No terminal starts during Neovim startup or repo opening; `<leader>tp` is the only profile trigger.
- Cycling and toggling keep one visible window at most and emit no messages.
- Terminal processes are children of Neovim: they die with it and are never resurrected.
- The bottom panel holds at most 8 terminals. A reserved count-9 pattern for a future vertical agent panel is documented in the change design; no such code ships yet.
- The old `:Terminal` commands and `<leader>tt`/`<leader>tT`/`<leader>tc` maps are gone on this route; tmux keeps hosting Pi, opencode, and anything outside this editor-local panel.

### Repo terminal profile

A repository may version `.nvim/terminals.json`. It is read as data only (JSON, never executed) and supports multiple named terminals:

```json
{ "version": 1, "terminals": [ { "name": "web", "cmd": "pnpm dev" } ] }
```

Unknown keys are ignored. A missing, unparsable, or empty profile is treated as absent (one warning at most), and `<leader>tp` then opens a plain shell immediately. A sibling `.nvim/terminals.lua` is never read.

The legacy route (`NVIM_IDE_LAYOUT=0` or Neovim <0.11) keeps the native `:Terminal`, `:TerminalVertical`, and `:TerminalTab` commands with `<leader>tt`/`<leader>tT`/`<leader>tc` and wipe-on-close behavior.

## Validation commands

Use an isolated XDG config path before applying live links:

```sh
tmp="$(mktemp -d "$PWD/.tmp-nvim-validate.XXXXXX")"
ln -s "$PWD/editors/nvim" "$tmp/nvim"
XDG_CONFIG_HOME="$tmp" \
XDG_DATA_HOME="$tmp/data" \
XDG_STATE_HOME="$tmp/state" \
XDG_CACHE_HOME="$tmp/cache" \
nvim --headless +qa
```

Health and startup evidence:

```sh
XDG_CONFIG_HOME="$tmp" XDG_DATA_HOME="$tmp/data" XDG_STATE_HOME="$tmp/state" XDG_CACHE_HOME="$tmp/cache" nvim --headless '+checkhealth' +qa
XDG_CONFIG_HOME="$tmp" XDG_DATA_HOME="$tmp/data" XDG_STATE_HOME="$tmp/state" XDG_CACHE_HOME="$tmp/cache" nvim --startuptime "$tmp/nvim-startuptime.log" +qa
```

Boundary checks:

```sh
git diff --check
git diff --cached --name-only
git diff --name-only -- editors/vim
# Grep Lua config and lockfile for resident AI/plugin references before accepting the boundary.
```

## Optional external tools

Missing optional tools should degrade capabilities, not block startup:

- Search: `fzf`, `rg`.
- Language servers: `lua-language-server`, `bash-language-server`,
  `marksman`, `vscode-json-language-server`, `yaml-language-server`,
  `taplo`, `typescript-language-server`, `vscode-html-language-server`,
  `vscode-css-language-server`, `cds-lsp`, PowerShell Editor Services via
  `PSES_BUNDLE_PATH`/`POWERSHELL_EDITOR_SERVICES_BUNDLE_PATH`,
  `basedpyright-langserver` or `pyright-langserver`.
- Formatters and linters: `stylua`, `shfmt`, `fish_indent`, project-local
  `prettier`, project-local `biome`, project-local `eslint`, `markdownlint`,
  `yamllint`, `jsonlint`, `taplo`, `shellcheck`, `selene`, `luacheck`, `ruff`.

Treat missing optional tools separately from blocking configuration failures.

## Migration and recovery

Antigravity remains the IDE fallback until this Neovim workflow is accepted through real daily use. Do not remove the fallback while any workflow remains only headless/config-validated.

Rollback symlink target:

```yaml
~/.config/nvim/init.vim: editors/vim/init.vim
```

The forward Neovim metadata target is:

```yaml
~/.config/nvim: editors/nvim
```

The live Neovim link can point at `editors/nvim` after validation. If you need to return to the legacy Vim-backed Neovim entrypoint, use the rollback target above.

## v1 workflow status

Headless validation covers startup, health, command availability, plugin lazy-load boundaries, formatting/linting policy, git/diff commands, and native terminal command execution. Some daily workflows still need real UI acceptance before Neovim should be considered primary: visual tree browsing, fuzzy picker use, live LSP diagnostics/completion with installed tools, visual git signs, and terminal/tmux handoff during actual work.
