# ai-stack router config (portable copy)

`opencode-router.json` is the rendered sdd-explore router override for OpenCode
(source of truth for the template: `ai-stack` repo → `config/opencode-router.template.json`).
It is symlinked into `~/.config/ai-stack/opencode-router.json` by dotbot and read by
`ai-stack/scripts/opencode-web.sh` (exports `OPENCODE_CONFIG`).

## New machine bootstrap

1. Clone the `ai-stack` repo (contains the router code, launcher and installer).
2. Run `ai-stack/scripts/install-router-config.sh` — it re-renders this file in place
   (writes through the symlink) and installs the `agy-explore` shim with this
   machine's paths into `~/.config/ai-stack/bin/`.
3. Start the hub via `ai-stack/scripts/opencode-web.sh` (wezterm/tmux already do).

Machine-local by design (never committed): `~/.config/ai-stack/bin/` shim,
`~/.config/ai-stack/state/` metrics, `*.bak-*` backups.

Related: quota records consumed by the router come from `tools/ai-quotas`
(dotfiles, systemd user service; on macOS replace the unit with launchd or run
`ai-quotas serve` manually).
