# Apply Progress: nvim-ide-environment

Direct work-unit commits on `master` (no branch, no PR, per orchestrator). Verified headlessly against the live config (`~/.config/nvim` → symlink to `editors/nvim`); scratch scripts live under `/tmp/opencode/` and are never committed.

## Slice status

| Slice | Status | Commit |
|---|---|---|
| P0 vtsls parity | 4/5 done — task 1.5 (manual parity battery) pending on the user's acceptance repo | `25697a9` |
| P1 terminal + bufferline | 12/12 done (P1a `0c6f697`, P1b `PENDING_COMMIT`) | work-unit commits on master |

---

## Slice P0 — vtsls parity (tasks 1.1–1.5)

**Status: 4/5 done; task 1.5 (manual parity battery) pending — it requires the user's real acceptance repo and interactive nvim.**

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1.1 | vtsls-first ordering + tsdk settings in `frontend.lua` | ✅ done | `names = { "vtsls", "ts_ls", "tsserver" }`; `config.settings` per design Decision 1; no `cmd`, no `init_options`; `root_markers` kept |
| 1.2 | Inlay hints on `LspAttach`, capability-guarded | ✅ done | `client:supports_method("textDocument/inlayHint")` guard → `vim.lsp.inlay_hint.enable(true, { bufnr = event.buf })` |
| 1.3 | Runtime gates: vtsls install + parser check | ✅ done | `vtsls` 0.3.0 via headless Mason Lua API; parsers `typescript` + `tsx` already present; no silent auto-install |
| 1.4 | Headless fallback test (registry without vtsls) | ✅ done | 12/12 checks PASS, exit 0 (`/tmp/opencode/sdd-p0-fallback-test.lua`) |
| 1.5 | Manual parity battery in acceptance repo | ⏳ pending | Requires user's interactive nvim; checklist below |

### P0 pending manual battery (task 1.5, user, interactive)

- [ ] `:LspInfo` reports `vtsls`; `:TSInstallInfo` shows `typescript` + `tsx`
- [ ] `gd` cross-file; `<leader>rn` propagates; `K` hover
- [ ] Inlay hints render without manual command
- [ ] Completion offers auto-import; `<leader>ca` offers add-import

### P0 deviations (kept from the P0 batch)

| Deviation | Reason |
|---|---|
| Headless Mason install via `mason-registry` Lua API instead of `:MasonInstall` | command is async; waited on install stream `closed` (`:qa` races npm mid-link) |
| `client:supports_method(...)` colon call | Neovim 0.12 deprecates dot-call (removal 0.13) |

---

## Slice P1 — terminal + bufferline (tasks 2.1–2.12)

### Task status (WU-P1a: 2.1–2.7, 2.10–2.12)

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 2.1 | RED profile-parser fixtures | ✅ done | RED run before 2.3: `FAIL module exposes parse_profile`, exit 1; GREEN after 2.3: **21/21 PASS**, exit 0 |
| 2.2 | toggleterm plugin spec | ✅ done | `plugins/terminal.lua`: `cond = not features.legacy`, `cmd = { "ToggleTerm", "TermSelect" }`, `size = 12`, `direction = "horizontal"` |
| 2.3 | Rewrite `config/terminal.lua` | ✅ done | `vim.json.decode` under `pcall`; panel counts 1–8; one-visible cycling via `get_all(true)` close-visible + open next; keymaps `tp/to/tn/t]/t[/tq/ts`; `:Terminal`+`tt/tT/tc` deleted; `<Esc><Esc>` kept |
| 2.4 | PID preservation | ✅ done | core harness: `PID preserved across hide/cycle/re-show` PASS (jobpid equal for t1 and t2) |
| 2.5 | Headless invariants | ✅ done | 34/34 PASS: one-visible cycling t1→t2→t1, silent hide (0 notifications, 0 new `:messages`), zero terminals after startup, sidebar `winwidth == 34` + central identity before/after toggling, terminal window spans full width (botright) |
| 2.6 | Profile fixtures runtime | ✅ done | absent → 1 running shell immediately, silent; valid 2-terminal → both jobs running (`sleep 543`/`sleep 544`), one visible; unparsable → absent + exactly 1 WARN |
| 2.7 | Legacy entries gone | ✅ done | `:Terminal`/`:TerminalVertical`/`:TerminalTab` undefined; `tt/tT/tc` maparg empty; new maps + `<Esc><Esc>` defined; `git diff` on `terminal_legacy.lua` empty (0 lines) |
| 2.8 | bufferline spec | ✅ done | `plugins/ui.lua`: `akinsho/bufferline.nvim`, `event = "VeryLazy"`, `cond = not features.legacy`, `close_command`/`right_mouse_command = "confirm bdelete %d"`, `offsets` reusing `tree_filetype` |
| 2.9 | Bufferline scenarios | ✅ done | harness **19/19 PASS**: click/close handlers wired, current-buffer highlight, modified marker, clean close removes entry, modified close keeps content (no silent discard), session alive, sidebar 34 |
| 2.10 | Import `plugins.terminal` | ✅ done | one line in `plugins/init.lua`; headless startup resolves both specs |
| 2.11 | Pins toggleterm + bufferline | ✅ done | `lazy-lock.json`: `toggleterm.nvim` `9a88eae8`, `bufferline.nvim` `655133c3` via headless `require("lazy").install({ wait = true })`; `Lazy restore` exit 0 |
| 2.12 | README terminal boundary | ✅ done | keymap table, profile format + data-only rule, no-auto-start, one-visible, count-9 reservation, legacy-route note |

### WU-P1a verification evidence

**Focused check (task 2.1 fixtures — RED→GREEN):**

Command: `nvim --headless -l /tmp/opencode/sdd-p1-profile-fixtures.lua` (ephemeral)

```
# RED (before 2.3):
FAIL module exposes parse_profile
RESULT: 1 check(s) failed — exit 1

# GREEN (after 2.3):
PASS module exposes parse_profile
PASS valid profile returns two entries
PASS entries carry exact name/cmd data
PASS parse is silent on valid profile
PASS parse never executes commands (marker absent)
PASS payload-bearing file stays inert data (string, not executed)
PASS payload never executed (marker absent)
PASS lua-source profile rejected (nil)
PASS lua-source rejection warns exactly once
PASS lua-source payload never executed
PASS sibling terminals.lua ignored (json wins)
PASS sibling terminals.lua never executed
PASS lua-only fixture treated as absent (nil)
PASS absent profile is silent
PASS lua-only sibling never executed
PASS unparsable profile treated as absent (nil)
PASS unparsable profile warns exactly once
PASS absent profile returns nil
PASS absent profile is silent
PASS entries without non-empty name+cmd are dropped
PASS empty-field profile is silent (absent, not parse failure)
RESULT: 0 check(s) failed — exit 0
```

**Runtime harness (tasks 2.4–2.7):**

Command: `nvim --headless /tmp/opencode/sdd-p1-runtime/repo/scratch.md -c "luafile /tmp/opencode/sdd-p1-runtime-core.lua" +qa!`

→ 34/34 PASS. Highlights: `:Terminal`-family commands and `tt/tT/tc` maps undefined; `tp/to/tn/t]/t[/tq/ts` + `<Esc><Esc>` defined; zero terminals after startup; sidebar `winwidth == 34` and central-window identity stable across create/cycle/hide/re-show/shutdown; terminal window width == `&columns` (botright full width); `jobpid()` identical before hide / after cycle / after re-show; zero `vim.notify` calls and zero new `:messages` lines across all hide/show/cycle ops.

Command (task 2.6, per case): `SDD_P1_CASE={absent|valid|unparsable} nvim --headless <fixture-repo>/scratch.md -c "luafile /tmp/opencode/sdd-p1-runtime-profile.lua" +qa!`

→ 3 cases × all checks PASS (absent: 5/5; valid: 7/7; unparsable: 5/5). Fixture repos are throwaway `git init` dirs under `/tmp/opencode/sdd-p1-profile-repos/`.

### WU-P1a work-unit evidence

| Evidence | Value |
|---|---|
| Focused test | profile fixtures: RED exit 1 → GREEN 21/21 exit 0 |
| Runtime harness | core invariants 34/34; profile cases absent/valid/unparsable all PASS; `Lazy restore` exit 0 |
| Rollback boundary | Revert `plugins/terminal.lua`, `config/terminal.lua`, the `plugins/init.lua` import line, and the two `lazy-lock.json` pins (plus README section) — no unrelated work touched; `terminal_legacy.lua` byte-frozen (0-line diff verified) |
| Changed lines | this commit: ~310 add / ~71 del (terminal rewrite 278 lines incl. doc comments) |

### P1 deviations from tasks.md/design

| Deviation | Reason |
|---|---|
| `Terminal:new` + immediate `term:spawn()` at allocation (no open/close flash) | toggleterm's `get_all()` only sees spawned terminals; creating without spawning let two profile terminals collide on count 1 and toggleterm silently re-id'd one (caught by task 2.6 fixtures: `sleep 543`/`sleep 544` came back swapped). Spawning at allocation keeps counts 1–8 unique and satisfies "both terminals start" without any window flash |
| `M.new_terminal()` returns the created terminal | headless verification needs the term handle; harmless for keymap use |
| `:TerminalVertical`/`:TerminalTab` also deleted (whole-file rewrite) | tasks pin `:Terminal` removal; the rewrite replaces the native-terminal file entirely — spec "Old entries gone" satisfied; legacy route keeps all three commands |
| `M.select()` forces plugin load then runs `:TermSelect` | the command exists only after toggleterm loads; design table says `<leader>ts` → `:TermSelect`, kept verbatim |
| WARN emitted only on JSON decode failure | design: "Parse failure emits one non-blocking WARN"; unreadable/absent/empty stays silent (fixture-asserted 0 warns) |

---

### Task status (WU-P1b: 2.8–2.9)

See rows 2.8–2.9 above. Verification evidence:

Command: `nvim --headless -c "luafile /tmp/opencode/sdd-p1-bufferline.lua" +qa!`

```
PASS bufferline loads
PASS close_command uses confirm bdelete
PASS right_mouse_command uses confirm bdelete
PASS offset reuses tree_filetype (neo-tree)
PASS bufferline owns the tabline
PASS line lists open buffers by name
PASS line wires buffer click for navigation
PASS line wires close indicator click
PASS line highlights the current buffer
PASS line marks modified buffer
PASS close indicator removes entry (clean buffer)
PASS closed buffer disappears from the line
PASS modified close keeps content (no silent discard)
PASS modified buffer closed via confirm
PASS closed modified buffer disappears from the line
PASS editor remains usable after closing last-but-one buffer
PASS line reflects remaining buffers
PASS sidebar still 34 wide with bufferline shown
PASS central window unchanged
RESULT: 0 check(s) failed
```

Headless caveat: `confirm` auto-answers "yes" without a UI, so the modified-buffer close SAVES (content preserved — the assertion). Interactively the standard confirm prompt appears; the config never uses `bdelete!`, so nothing is silently discarded. Real mouse click-through remains an interactive check (handlers verified wired above).

### WU-P1b work-unit evidence

| Evidence | Value |
|---|---|
| Focused test | bufferline scenario harness 19/19 PASS, exit 0 |
| Runtime harness | same harness (loads bufferline + sidebar; `winwidth == 34` + central identity with the line shown) |
| Rollback boundary | Revert the `plugins/ui.lua` hunk only; the orphan `bufferline.nvim` lockfile pin from P1a is ignored by Lazy until the spec returns |

## Pending manual steps (user, interactive)

- Task 1.5 parity battery (P0, checklist above).
- Interactive feel of `<leader>tp/to/tn/t]/t[/tq/ts` in a real session (headless covers semantics; which-key shows the new `t` group automatically).
- Bufferline: real mouse click navigation + close indicator in a live UI (headless verified handlers and confirm semantics).

## Next

- Slice P2 (AI commit, tasks 3.1–3.9) or `sdd-verify` for P0+P1.
