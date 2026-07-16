# Dotfiles Neovim IDE

This is the dotfiles-owned Neovim IDE configuration. It is intentionally lightweight: Neovim owns editor-local workflows, while tmux remains the primary home for shells, long-running processes, Pi, and opencode.

## Terminal workflow

Native terminal commands are available for short editor-local commands only:

- `:Terminal [cmd]` opens a horizontal terminal split.
- `:TerminalVertical [cmd]` opens a vertical terminal split.
- `:TerminalTab [cmd]` opens a terminal tab.
- `<leader>tt`, `<leader>tT`, and `<leader>tc` open terminal workflows from normal mode.
- `<Esc><Esc>` leaves terminal mode.

No terminal shell starts during Neovim startup. Start long-running servers, agents, package watchers, Pi, and opencode in an external terminal or tmux session instead of inside Neovim.

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
