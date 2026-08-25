# opencode-router (sdd-explore router)

Complete source of truth for the sdd-explore router — the LOCAL opencode-ecosystem
override that routes SDD exploration through Antigravity (`agy`) with a cheap
deterministic CLI deciding the route. This dotfiles directory owns everything;
the `ai-stack` repo (VPS infra) is NOT needed for anything router-related.

## What lives here

| Piece | Lives at | Notes |
|---|---|---|
| Router source | `src/agy/*.ts` | typed outcomes, quota pools, containment, persistence |
| Dispatcher plugin source | `src/plugin/sdd-explore-dispatch.ts` + `src/agy/dispatch-core.ts` | 0-LLM before-hook interceptor (pure core + thin adapter) |
| Dispatcher plugin artifact | `~/.config/opencode/plugins/sdd-explore-dispatch.ts` | bundled by `build.sh`; auto-loads globally in opencode |
| Config template + prompt | `config/` | `opencode-router.template.json` + `prompts/sdd-explore-router.md` |
| Rendered router config | `opencode-router.json` | committed output of `build.sh`; dotbot-links to `~/.config/ai-stack/opencode-router.json` |
| Launcher | `bin/opencode-web.sh` | dotbot-links to `~/.local/bin/opencode-web` |
| Router binary | `~/.local/bin/agy-explore` | compiled by `build.sh` from `src/agy/cli.ts` |
| Tests | `tests/agy-router.test.ts`, `tests/dispatch.test.ts` | unit + integration + contract + smoke, hermetic (stub agy) |
| Quota records | `~/.local/state/ai-quotas/` | provided by `tools/ai-quotas` (same dotfiles) |
| Request flow diagram | `FLOW.md` | Mermaid diagram of the full dispatch flow with per-path costs |

## New machine bootstrap

1. Dotfiles install (dotbot links the config + launcher).
2. `ai/opencode-router/build.sh` — renders the config and compiles `agy-explore`.
3. Install the Antigravity CLI (`agy`) into `~/.local/bin` and log in.
4. Ensure `tools/ai-quotas` is serving (writes `~/.local/state/ai-quotas/`).
5. Start the hub via `~/.local/bin/opencode-web` (wezterm/tmux launchers already do).

No `ai-stack` checkout is required at any point — that repo is VPS infrastructure.

## Updating the router

Edit `src/` or `config/`, then from this directory:

```bash
bun test               # must be green (router + dispatcher suites)
./build.sh             # re-render config + recompile binary + rebundle plugin
```

The binary and the plugin artifact embed the code at build time — pulling
changes without running `build.sh` is NOT a deploy. The rendered
`opencode-router.json` is committed; the integrity test fails if it ever
drifts from `config/` template + prompt.

## Rollback

- Hot kill switch (no restart): `touch ~/.config/ai-stack/force-native` — routed
  explores return `force_native` and run the native executor. The dispatcher
  plugin honors the SAME file (plus `AI_STACK_SDD_EXPLORE=native`), falling
  through to the native router/executor before anything runs. Remove the file
  to re-enable routing.
- Delete `~/.config/opencode/plugins/sdd-explore-dispatch.ts` and restart
  opencode to remove the interceptor entirely (rebuild via `build.sh`).
- Full native: launch `opencode web` directly instead of `opencode-web`.
