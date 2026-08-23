# ai-stack router (portable runtime)

Runtime pieces for the sdd-explore router, with NO dependency on the ai-stack
checkout at run time:

| Piece | Lives at | Provided by |
|---|---|---|
| Router binary `agy-explore` | `~/.local/bin/agy-explore` | compiled from the ai-stack repo (see below) |
| Launcher `opencode-web` | `~/.local/bin/opencode-web` | dotbot link → `ai/ai-stack/bin/opencode-web.sh` |
| Router config | `~/.config/ai-stack/opencode-router.json` | dotbot link → `ai/ai-stack/opencode-router.json` |
| Quota records | `~/.local/state/ai-quotas/` | `tools/ai-quotas` (same dotfiles) |

The launcher exports `OPENCODE_CONFIG` AND prepends `~/.local/bin` to PATH —
hub processes inherit a minimal PATH (`zsh -ic` skips `.zprofile`), so without
that export neither `agy-explore` nor `agy` resolve inside the hub and every
routed explore silently degrades to the native fallback.

## New machine bootstrap

1. Dotfiles install (dotbot links config + launcher).
2. Clone the `ai-stack` repo anywhere (build-time dependency only) and compile:
   `cd ai-stack && bun build --compile src/agy/cli.ts --outfile ~/.local/bin/agy-explore`
3. Install the Antigravity CLI (`agy`) into `~/.local/bin` and log in.
4. Ensure `tools/ai-quotas` is serving (writes `~/.local/state/ai-quotas/`).
5. Start the hub via `opencode-web` (wezterm/tmux launchers already do).

## Updating the router

The binary embeds the code at build time: after pulling ai-stack changes,
re-run the compile command from step 2. (`git pull` alone is NOT a deploy in
binary mode — unlike the old repo-shim mode.)

## Rollback

- Hot kill switch (no restart): `touch ~/.config/ai-stack/force-native` —
  routed explores return `force_native` and run the native executor.
- Full native: launch `opencode web` directly instead of `opencode-web`.
