# Apply Progress: nvim-ide-environment

Direct work-unit commits on `master` (no branch, no PR, per orchestrator). Verified headlessly against the live config (`~/.config/nvim` → symlink to `editors/nvim`); scratch scripts live under `/tmp/opencode/` and are never committed.

## Slice status

| Slice | Status | Commit |
|---|---|---|
| P0 vtsls parity | 4/5 done — task 1.5 (manual parity battery) pending on the user's acceptance repo | `25697a9` |
| P1 terminal + bufferline | 12/12 done (P1a `0c6f697`, P1b `40de1fb`) | work-unit commits on master |
| P2 AI commit | 9/9 done (WU-P2 commit on master) | see git log after this batch |

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

---

## Slice P2 — AI commit (tasks 3.1–3.9)

### Task status (WU-P2)

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 3.1 | RED git-root selection | ✅ done | RED before 3.3: `FAIL module config.aicommit loads` + 3 cases, exit 1; GREEN after 3.3: **4/4 PASS**, exit 0 (`/tmp/opencode/sdd-p2-root-selection.lua`) |
| 3.2 | RED commit-state checks | ✅ done | RED before 3.3: module + 8 cases FAIL, exit 1; GREEN after 3.3: **9/9 PASS**, exit 0 (`/tmp/opencode/sdd-p2-commit-state.lua`) |
| 3.3 | `config/aicommit.lua` created | ✅ done | 189 lines; full design Data Flow; six abort classes notify-and-abort; COMMIT_EDITMSG buffer with `BufWriteCmd` → `git commit -F --cleanup=strip`; `:q` discards; prompt is a Lua literal; never runs `git add`; `<leader>gc` set |
| 3.4 | Wire `init.lua` | ✅ done | +2 lines (`require("config.aicommit")`); headless startup exit 0 (isolated XDG too); **19/19 PASS** wiring checks: `<leader>gc` ours (desc), gitsigns buffer-local `gp/gr/gs/gu/gb/gB/gD/gQ` + diffview `gd/gH/gq` + ide `gC/gG` all still mapped after attach |
| 3.5 | Abort-class verification | ✅ done | **24/24 PASS** — six classes × {notify shown, no COMMIT_EDITMSG, no commit, index unchanged} (`/tmp/opencode/sdd-p2-abort-classes.lua`) |
| 3.6 | Threshold fixtures | ✅ done | **13/13 PASS** — staged diff exactly 65535 B → payload byte-identical full diff; exactly 65536 B → stat + @@ header, body excluded; 205-hunk fixture → exactly 200 headers + `(hunk headers truncated: 200 of 205 shown)` |
| 3.7 | Commit semantics | ✅ done | **17/17 PASS** — `:wq` commits the EDITED text; committed content is the index version (dirty worktree excluded, still dirty after); index drained by commit; `:q!`/`:q` → no commit, staged area untouched, buffer gone |
| 3.8 | Apply-time stdout check | ✅ done | REAL `opencode run` (v1.18.21): raw stdout is byte-clean — hexdump `7465 7374 3a20 ...` = `test: initialize counter with a fixed seed value\n`, zero ANSI (decoration on stderr only), exit 0, ~19 s. Module-level real run: 6 s, buffer opened with `chore: set counter initial value to 42`, validator accepts. **Design Open Question 2 CLOSED** |
| 3.9 | README AI-commit boundary | ✅ done | `editors/nvim/README.md` "AI commit workflow" section: keymap, staging requirement, six abort classes, 65536 B summary, `:wq`/`:q` semantics, one-shot-call reconciliation with the tmux/agents boundary |

### WU-P2 verification evidence

**Focused check (tasks 3.1/3.2 — RED→GREEN):**

Command: `nvim --headless <script>.lua -c "luafile <script>.lua" +qa!` (ephemeral scripts under `/tmp/opencode/`)

```
# RED (before 3.3), 3.1:                        # RED (before 3.3), 3.2:
FAIL module config.aicommit loads               FAIL module config.aicommit loads
FAIL subdir buffer resolves to its own repo...  FAIL empty index aborts with a staging...
FAIL git calls run against the resolved root    FAIL empty index: no opencode call
FAIL non-repo buffer resolves to nil            FAIL (6 further module-dependent cases)
RESULT: 4 check(s) failed - exit 1              RESULT: 9 check(s) failed - exit 1

# GREEN (after 3.3): 3.1 → RESULT: 0 failed - exit 0 (4/4)
#                    3.2 → RESULT: 0 failed - exit 0 (9/9)
```

**Runtime harness:** 3.5 abort classes 24/24 (non-repo, empty index, exit≠0 via fake exit 3, timeout via `exec sleep` fake killed at 1 s → `code=124/signal=15`, ANSI-only empty output, non-conventional first line — each: notify + no buffer + no commit + index unchanged); 3.6 thresholds 13/13; 3.7 semantics 17/17; 3.4 wiring 19/19; isolated-XDG `nvim --headless +qa` exit 0.

### 3.8 raw opencode sample (Open Question 2 closed)

```
$ opencode run "<prompt+diff>"   # scratch repo /tmp/opencode/sdd-p2-real, trivial staged diff
exit=0, ~19 s (module-level rerun: 6 s)
stdout hexdump 00000000: 7465 7374 3a20 696e 6974 6961 6c69 7a65  test: initialize
raw stdout (cat -A): "test: initialize counter with a fixed seed value$"   ← no ANSI, no CR
stderr: session/model banner with ANSI codes (discarded; never fed to the validator)
verdict: stdout is clean; ANSI-strip is a no-op safety net; validator accepted both samples
```

### WU-P2 work-unit evidence

| Evidence | Value |
|---|---|
| Focused test | 3.1/3.2 fixtures: RED exit 1 → GREEN 4/4 + 9/9 exit 0 (after the validator fix, re-run: all suites 0 failed) |
| Runtime harness | abort classes 24/24; thresholds 13/13; commit semantics 17/17; wiring 19/19; real `opencode run` end-to-end → buffer + validator accept |
| Rollback boundary | Revert `config/aicommit.lua` + the 2 `init.lua` lines + README section — no other behavior touches; `terminal_legacy.lua` byte-frozen (0-line diff re-verified this batch) |
| Changed lines | aicommit.lua 189 + init 2 + README 12 + tasks/progress docs — code well under the ~180-line estimate, total under the 450 budget |

### P2 deviations from tasks.md/design

| Deviation | Reason |
|---|---|
| Scope pattern `[%w%-%_.]+` instead of the design's `\(..\)` (exactly two chars) | Spec mandates `type(scope)?: subject`; a 2-char-only scope would reject this repo's own `feat(nvim): ...` style (the WU-P2 commit message itself). Any non-empty word-like scope passes |
| Conventional-Commits check = structural match + type-set lookup, not one alternation regex | Lua patterns have NO alternation (`|`) — the design's single regex is unimplementable as one Lua pattern. Two patterns (scoped/plain) + `TYPES` set cover the same grammar |
| Timeout class detected as `code == 124 and signal == 15` | `vim.system` reports timeouts this way (probed: no `timeout` field on the result); async callback variant verified identical |
| Defensive second guard `payload == ""` after the quiet-check | Unreachable in practice (`--quiet` governs); message mirrors the empty-index class so no unclassifiable abort exists |

### P2 implementation gotchas (for verify)

- `vim.system` completes only when the process exits AND stdio pipes close: a fake `sh` that SIGTERMs while its child holds the pipe delays the callback. Real `opencode` is a direct binary — unaffected. Test fakes must `exec sleep`.
- `vim.fn.maparg` returns a string unless called with the 4th arg `true` (dict with `desc`).
- gitsigns `g`-maps are buffer-local and appear only after attach in a git repo — wiring checks must open a repo buffer and wait for attach.
- Notify capture for the async flow must stay installed until the flow settles (restore-after-`run()` misses the callback notify).

## Pending manual steps (user, interactive)

- Task 1.5 parity battery (P0, checklist above).
- Interactive feel of `<leader>tp/to/tn/t]/t[/tq/ts` in a real session (headless covers semantics; which-key shows the new `t` group automatically).
- Bufferline: real mouse click navigation + close indicator in a live UI (headless verified handlers and confirm semantics).
- `<leader>gc` against real daily commits (headless + one-shot real `opencode run` verified; interactive review-edit-commit loop is the remaining feel check).

## Next

- All three slices applied (P0 4/5 — only the user's manual parity battery remains; P1 12/12; P2 9/9). `sdd-verify` for the whole change.
