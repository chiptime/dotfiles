# Proposal: Neovim IDE Environment

## Intent

Replace Antigravity and VSCode with Neovim in WezTerm while preserving editing/search, Git history, AI commits, and dev-server testing. Two Electron GUIs duplicate Node IDE servers, TypeScript servers, and WSL2 watchers; the target runs one `vtsls`. opencode web remains through Tailscale [routemap:3,190-198].

## Delivery Scope

| PR | Deliverable | Acceptance |
|---|---|---|
| P0 | In `lua/lang/frontend.lua`, prefer `vtsls`, retaining fallbacks; configure `tsdk`, auto-imports, and inlay hints. | After explicit `:MasonInstall vtsls` and parser verification, `:LspInfo` reports `vtsls`; full VSCode parity passes in the user's heaviest TypeScript repository, named at apply time. Auto-install stays off [exploration:14-15,223-225]. |
| P1 | Add `toggleterm.nvim` for multiple closable terminals: one visible, cyclable, hidden silently. `<leader>tp` starts all terminals in a versioned repository profile; absence opens a non-blocking shell. No auto-start. Remove `:Terminal` and `<leader>tt/tT/tc`. Add a top buffer line; choose its plugin in design. | Terminals preserve PIDs when hidden, die with Neovim, and coexist with the IDE sidebar [exploration:220-221,230]. Reserve—but do not implement—a vertical agent-panel pattern. |
| P2 | Add `<leader>gc`; require staged changes; use full diff or, above a design-defined threshold, `--stat` plus hunk summary; call `opencode` with an English strict-conventional prompt stored in Lua; open `COMMIT_EDITMSG`. Update README boundaries. | Empty staging or opencode failure notifies and aborts without execution or an empty buffer; success remains editable and commits only on `:wq` [exploration:113-124,227-228]. |

Use `auto-chain`: one PR per slice, P0 → P1 → P2, within 800 lines.

## Capabilities

### New Capabilities
- `nvim-typescript-parity`: VSCode-equivalent TypeScript behavior.
- `nvim-terminal-profiles`: Persistent profiles and reserved panel pattern.
- `nvim-ai-commit`: Safe staged-diff generation.
- `nvim-buffer-line`: Buffer navigation.

### Modified Capabilities
None.

## Approach and Dependencies

Follow lazy-loading, `lang/`, and keymap conventions. Dependencies: `vtsls`, `toggleterm.nvim`, a selected bufferline plugin, and `opencode`. Files and `README.md` change; `terminal_legacy.lua` stays frozen.

## Non-goals

Neotest; DAP acceptance; agent panel; GUI uninstall; legacy route; startup budget; browser integration; tmux; other languages. Manual tests stay in-browser.

## Risks and Mitigations

| Risks | Mitigation |
|---|---|
| R1/R2/R11: lifecycle, plugins, or terminals disrupt layout | Lazy-load narrowly; verify invariants. |
| R3-R6/R12: install, SDK, or fallback hides TS defects | Verify server, parsers, `tsdk`, hints, imports, and fallbacks in-repository. |
| R7-R9: docs drift, staging ambiguity, opencode/large-diff failure | Update README; never auto-stage; enforce abort and bounded input. |

## Rollback Plan

Revert each PR independently: restore TypeScript ordering; remove terminal/bufferline plugins and maps; remove AI integration and restore README. Profiles hold no runtime state.

## Success Criteria

- [ ] Four workflows work without Antigravity or VSCode.
- [ ] P0, P1, and P2 pass acceptance independently.
- [ ] opencode web and manual browser testing remain unchanged.
