# Nvim TypeScript Parity Specification

## Purpose

Give Neovim VSCode-equivalent TypeScript behavior by making `vtsls` the first-choice server, keeping `ts_ls`/`tsserver` as fallbacks, and configuring tsdk, inlay hints, and auto-imports. Proposal slice P0. Evidence: exploration [14-15, 223-225].

## Requirements

### Requirement: vtsls-First Server Resolution

TypeScript server selection MUST resolve through the ordered `server_choices` list with `vtsls` as the first name and `ts_ls`/`tsserver` retained as subsequent fallback names.

#### Scenario: Native path attaches vtsls

- GIVEN Neovim ≥0.11 (native LSP path) with vtsls installed
- WHEN a TypeScript buffer opens
- THEN `:LspInfo` reports the attached client as `vtsls`

#### Scenario: Legacy fallback retained

- GIVEN a resolution context where `vtsls` is unavailable in the registry
- WHEN a TypeScript buffer opens
- THEN the resolver falls back to `ts_ls`/`tsserver` and a server attaches (no silent drop)

### Requirement: Manual Install Step

Mason auto-install MUST remain off (`automatic_installation=false` unchanged); enabling vtsls MUST require an explicit user-run `:MasonInstall vtsls`.

#### Scenario: Explicit install enables vtsls

- GIVEN an environment without vtsls
- WHEN the user runs `:MasonInstall vtsls` and opens a TS buffer
- THEN vtsls attaches

#### Scenario: No silent auto-install

- GIVEN vtsls is not installed
- WHEN a TypeScript buffer opens
- THEN no background installation starts; fallback resolution applies

### Requirement: TypeScript SDK Configuration

vtsls MUST be configured with a resolvable TypeScript SDK (tsdk) so workspace features work in repos with and without local `node_modules/typescript`. Exact mechanism (`init_options`, vtsls settings, or cmd env) is a design decision.

#### Scenario: Workspace SDK used

- GIVEN a repo containing `node_modules/typescript`
- WHEN vtsls attaches
- THEN TS features resolve against the workspace SDK

#### Scenario: Fallback SDK used

- GIVEN a repo without local TypeScript
- WHEN vtsls attaches
- THEN the configured fallback tsdk is used and features respond

### Requirement: Inlay Hints Default-On

Inlay hints MUST be enabled by default on TypeScript/TSX buffers.

#### Scenario: Hints visible without action

- GIVEN vtsls attached to a TS buffer
- WHEN the buffer finishes loading
- THEN parameter/type inlay hints render with no manual command

### Requirement: Rich Auto-Imports

Completion MUST surface auto-import candidates for unimported workspace symbols; an import path MUST also be available as a code/source action.

#### Scenario: Completion inserts import

- GIVEN an unimported exported symbol
- WHEN completion triggers at an identifier
- THEN the list includes an auto-import item and accepting it inserts the import statement

#### Scenario: Source action adds import

- GIVEN an unresolved identifier under cursor
- WHEN code actions are requested
- THEN an add-import action is offered

### Requirement: Parity Acceptance Battery

Acceptance MUST run in the user's heaviest real TypeScript repository (named at apply time) and MUST verify: server identity, parsers (`typescript`, `tsx` present), and the parity battery below.

#### Scenario: Go-to-definition across files

- GIVEN the acceptance repo open
- WHEN `gd` runs on an imported symbol
- THEN the cursor lands on its definition in another file

#### Scenario: Rename propagates

- GIVEN a symbol referenced across files
- WHEN `<leader>rn` renames it
- THEN all references update (verified via grep or `gr`)

#### Scenario: Hover shows types

- GIVEN a typed symbol under cursor
- WHEN `K` runs
- THEN a float shows its type signature

#### Scenario: Environment verified

- GIVEN the acceptance repo
- WHEN checks run
- THEN `:LspInfo` shows `vtsls` and `:TSInstallInfo` shows `typescript` + `tsx` installed
