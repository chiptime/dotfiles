# Nvim AI Commit Specification

## Purpose

Add `<leader>gc`: generate a strict conventional-commits message in English from the staged diff via `opencode`, land it in an editable `COMMIT_EDITMSG` buffer, and commit only on `:wq`. Proposal slice P2. Evidence: exploration [113-124, 227-228].

## Requirements

### Requirement: AI Commit Trigger

A `<leader>gc` normal-mode keymap MUST start the AI-commit flow. The key is currently free across the `<leader>g` group; no existing map may be shadowed.

#### Scenario: Flow starts

- GIVEN a repo with staged changes
- WHEN `<leader>gc` is pressed
- THEN the AI-commit flow begins

### Requirement: Staged Area Required

The flow MUST require a non-empty staging area. With an empty index it MUST notify and abort. The flow MUST never stage anything itself (`git add` is forbidden inside the flow).

#### Scenario: Empty staging aborts

- GIVEN nothing staged
- WHEN the flow starts
- THEN a notification explains staging is required and the flow aborts: no opencode call, no buffer opened

#### Scenario: Never auto-stages

- GIVEN unstaged changes only and an empty index
- WHEN the flow aborts
- THEN the index is unchanged

### Requirement: Opencode Failure Aborts

The flow MUST call the `opencode` CLI with the staged-diff input and the commit prompt. Any opencode failure (non-zero exit, empty/invalid output) MUST notify and abort; the flow MUST NEVER fall back to opening an empty or placeholder commit buffer.

#### Scenario: Failure aborts cleanly

- GIVEN opencode errors or returns unusable output
- WHEN the flow reaches that point
- THEN the user is notified and no `COMMIT_EDITMSG` buffer opens and no commit runs

### Requirement: Deterministic Oversized-Diff Handling

The flow MUST compare the staged diff against a deterministic, config-defined threshold. Below it, opencode receives the full staged diff; at or above it, opencode receives `git diff --cached --stat` plus a hunk summary instead. The threshold value and summary shape are design decisions; the choice MUST be reproducible for identical repo state.

#### Scenario: Small diff sends full diff

- GIVEN a staged diff under the threshold
- WHEN the flow runs
- THEN opencode receives the full staged diff

#### Scenario: Large diff sends summary

- GIVEN a staged diff at or above the threshold
- WHEN the flow runs
- THEN opencode receives the `--stat` plus hunk summary, never the full diff, and the flow proceeds normally

### Requirement: Strict Conventional Commits In English

The prompt MUST demand a strict Conventional Commits message (`type(scope)?: subject`) written in English. The prompt template MUST live as Lua source inside the Neovim config, versioned with the dotfiles.

#### Scenario: Message conforms

- GIVEN any staged diff
- WHEN a message is generated
- THEN it matches Conventional Commits form and is in English

#### Scenario: Template is config source

- GIVEN the dotfiles config
- WHEN inspected
- THEN the prompt template is a literal in the Lua config, not generated ad hoc

### Requirement: Editable COMMIT_EDITMSG, Commit On Write-Quit

The generated message MUST be placed in a `COMMIT_EDITMSG` buffer opened for editing. The git commit MUST execute only when the user writes and quits the buffer (`:wq`); any other exit discards without committing.

#### Scenario: Edit then commit

- GIVEN the generated message in `COMMIT_EDITMSG`
- WHEN the user edits it and runs `:wq`
- THEN git commits with the edited message and the buffer closes

#### Scenario: Quit discards

- GIVEN the `COMMIT_EDITMSG` buffer open
- WHEN the user quits without writing
- THEN no commit is created and the staged area is untouched

### Requirement: README Boundary Updated

`README.md` MUST be updated so the documented "long-running servers/agents stay outside Neovim" boundary no longer contradicts the in-Nvim AI-commit capability.

#### Scenario: Boundary matches reality

- GIVEN `README.md` after this change
- WHEN the workflow section is read
- THEN it documents `<leader>gc` and the AI-commit boundary without contradiction
