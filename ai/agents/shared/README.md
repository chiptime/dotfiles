# Shared agent instructions

Instruction blocks owned by these dotfiles and injected into every harness's
global instructions file, between `<!-- dotfiles:<name> begin/end -->` markers.

| File | Purpose |
|---|---|
| `mission-contract.md` | Ask all questions once at kickoff, then execute until done; only 4 hard stops |

## Apply

```bash
~/.dotfiles/scripts/install-mission-contract.sh
```

Idempotent. Targets: `~/.config/opencode/AGENTS.md`, `~/.claude/CLAUDE.md`,
`~/.codex/AGENTS.md`, `~/.pi/agent/AGENTS.md`, `~/.gemini/GEMINI.md`
(Antigravity/agy and Gemini CLI) (skipped when missing). It also
links the `/mission` command into OpenCode and Claude Code.

## Re-apply automatically

The `gentle-ai` shell wrapper in `shell/functions.sh` re-runs the installer
after `gentle-ai sync|install|upgrade|restore|uninstall`. gentle-ai 4.3.1 keeps
content outside its own markers, so this is a safety net for future versions.

Edit the contract here, never in the target files: the installer overwrites the
block on every run.
