# Nvim Buffer Line Specification

## Purpose

Show a persistent top buffer line listing open buffers with navigation and closing driven from the line itself. Proposal slice P1. Plugin choice is delegated to the design phase — this spec states the capability, not the implementation.

## Requirements

### Requirement: Visible Top Buffer Line

A buffer line MUST be displayed at the top of the editor window listing open buffers with at least their name and modified state, highlighting the current buffer. The plugin providing it is a design decision.

#### Scenario: Line reflects buffer state

- GIVEN several buffers open
- WHEN the user switches or modifies buffers
- THEN the top line updates to show the open set, modified markers, and the current-buffer highlight

#### Scenario: Only new-route layout affected

- GIVEN the non-legacy IDE route
- WHEN the buffer line renders
- THEN the IDE sidebar width and central window layout are unchanged

### Requirement: Navigation And Closing From The Line

The buffer line MUST support navigating to a listed buffer and closing a listed buffer through direct interaction with the line (e.g. mouse activation). Closing uses standard Neovim buffer-close semantics; the line MUST NOT force-write unsaved buffers.

#### Scenario: Click navigates

- GIVEN the line shows three buffers
- WHEN the user activates a non-current buffer on the line
- THEN that buffer becomes the current window's buffer

#### Scenario: Close from the line

- GIVEN the line shows a closable buffer
- WHEN the user activates its close indicator
- THEN that buffer closes and disappears from the line
- AND if it has unsaved changes, standard Neovim close/confirm behavior applies (no silent force-write)

#### Scenario: Close keeps session alive

- GIVEN the last-but-one buffer closes from the line
- WHEN it closes
- THEN the editor remains usable and the line reflects the remaining buffers
