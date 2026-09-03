# Nvim Terminal Profiles Specification

## Purpose

Replace the wipe-on-close native terminal with toggleterm.nvim: multiple persistent terminals, exactly one visible, repo-versioned profiles started on demand, legacy terminal entry points removed. Proposal slice P1. Evidence: exploration [220-221, 230]; non-goal: legacy route stays frozen.

## Requirements

### Requirement: Single Terminal Dependency

The change MUST introduce toggleterm.nvim as the only new terminal plugin on the non-legacy route, replacing the native `bufhidden="wipe"` terminal behavior. `config/terminal_legacy.lua` MUST remain unchanged (proposal non-goal: legacy route).

#### Scenario: Dependency resolves

- GIVEN the config loads on the non-legacy route
- WHEN plugins resolve
- THEN toggleterm.nvim is present (lazy-loaded) and no other new terminal dependency is added

#### Scenario: Legacy route untouched

- GIVEN `NVIM_IDE_LAYOUT=0` or Neovim <0.11
- WHEN the config loads
- THEN `terminal_legacy.lua` behavior is byte-for-byte unchanged

### Requirement: Multiple Terminals, One Visible

Multiple terminals MUST be openable and closable. At most one terminal window MUST be visible at any time; a cycle control MUST switch the visible window between existing hidden terminals. Hide/show MUST NOT emit notifications or messages.

#### Scenario: Cycling shows one at a time

- GIVEN two hidden terminals exist
- WHEN the cycle control is triggered repeatedly
- THEN the visible terminal switches t1→t2→t1 and no second terminal window ever shows

#### Scenario: Silent hide preserves process

- GIVEN a terminal running a process
- WHEN it is toggled closed
- THEN it hides with no notification and the process survives (same PID on re-show)

### Requirement: Terminals Die With Neovim

Terminal processes MUST run as children of the Neovim process and MUST NOT be resurrected. Restarting lost servers is an accepted cost.

#### Scenario: No resurrection after restart

- GIVEN a running dev-server terminal
- WHEN Neovim quits and relaunches
- THEN the old processes are gone and no terminal auto-opens

### Requirement: Versioned Repo Terminal Profile

A repository MAY declare a terminal profile file versioned with the repo. The schema MUST support multiple named terminals, each with a command. Exact format and location are design decisions.

#### Scenario: Profile starts whole

- GIVEN a repo with a valid profile defining two named terminals with commands
- WHEN `<leader>tp` runs
- THEN both named terminals start, each running its command, honoring the one-visible invariant

#### Scenario: Missing profile never blocks

- GIVEN a repo without a profile
- WHEN `<leader>tp` runs
- THEN a plain shell opens immediately; startup is never blocked
- AND an unparsable profile is treated as absent

### Requirement: No Auto-Start

Opening Neovim or a repo MUST NOT launch any terminal; profile start happens only via `<leader>tp`.

#### Scenario: Clean startup

- GIVEN any repo
- WHEN Neovim finishes loading
- THEN zero terminals are running

### Requirement: Legacy Terminal Entry Points Removed

On the non-legacy route, the `:Terminal` command and `<leader>tt`, `<leader>tT`, `<leader>tc` maps MUST be removed. Replacement toggle/cycle keymaps are a design decision; only `<leader>tp` is fixed here.

#### Scenario: Old entries gone

- GIVEN the non-legacy route
- WHEN the user runs `:Terminal` or presses the old maps
- THEN none are defined; which-key lists no stale terminal bindings

### Requirement: Reserved Agent-Panel Pattern

The terminal design MUST reserve — but not implement — a pattern for a future vertical agent panel, such that adding one later requires no rework of the terminal layout. No agent-panel code ships in this change.

#### Scenario: Pattern documented, unimplemented

- GIVEN the design phase output
- WHEN reviewed
- THEN the reserved vertical-panel pattern is documented and no agent-panel implementation exists in the diff

### Requirement: IDE Sidebar Coexistence

Terminal windows MUST coexist with the IDE sidebar layout: toggling terminals MUST NOT resize or break the fixed-width sidebar or the central editor window.

#### Scenario: Layout invariant under toggling

- GIVEN the IDE sidebar open at its fixed width
- WHEN a terminal toggles open and closed repeatedly
- THEN the sidebar width and central editor window are unchanged
