# Bind Engram writes to the OpenCode session

This additive plugin fixes `multiple active runtime sessions match the current project and directory`
without deleting memories, ending other sessions, or replacing the existing Engram adapter.

## Activate

```sh
ln -sfn "$HOME/.dotfiles/ai/agents/opencode/engram-session-binding/plugin.ts" \
  "$HOME/.config/opencode/plugins/engram-session-binding.ts"
```

Quit and restart OpenCode after mapping the plugin. Existing running instances
keep their previously loaded hooks. Resume an existing conversation after restart;
the plugin registers that conversation's actual runtime ID on its first memory write.

## Behavior

- Before a session-aware Engram write, register the hook's `sessionID` using
  the installed binary's supported MCP `mem_session_start` tool.
- Resolve the canonical project with `mem_current_project` in the actual
  workspace, not by guessing a repository name or choosing another active session.
- Wait for successful registration before adding `session_id` to the write.
  Only tools whose schema supports `project` also receive a default project.
- Preserve explicit session IDs and projects; Engram still validates their ownership.
- Deduplicate concurrent registration for one session without conflating sibling sessions.
  A subagent is registered only when it actually writes memory, using its own runtime ID.
- Fail closed after a rejected registration or a five-second timeout. Arguments
  remain unchanged, and a later call can retry registration.
- Do not modify reads or session-end commands. No sessions are closed or deleted.

The registration subprocess inherits the same local environment as the current
`engram mcp --tools=agent` configuration. It explicitly disables cloud autosync.
Keep the binary and data-directory environment aligned if that MCP configuration changes.
Only the first implicit-ID write per session needs a registration subprocess.

## Verify

```sh
bun test ai/agents/opencode/engram-session-binding
```

Tests use temporary repositories and an isolated `ENGRAM_DATA_DIR` under
`/tmp/opencode`. They reproduce an ambiguous unbound save, then verify separate
parent, sibling, and child saves against the real installed Engram binary.
They also check rejection leaves the observation count unchanged and that no
session is ended. Tests never use or repair the live memory database.

## Rollback

Remove only `~/.config/opencode/plugins/engram-session-binding.ts` and restart
OpenCode. The original Engram adapter and all stored memories remain intact.
