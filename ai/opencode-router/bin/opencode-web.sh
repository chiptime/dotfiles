#!/usr/bin/env bash
#
# opencode-web.sh — launch OpenCode Web with the sdd-explore router override.
# Portable dotfiles version: the router runs as a compiled binary
# (~/.local/bin/agy-explore), with NO dependency on the ai-stack checkout.
#
# Exports OPENCODE_CONFIG and the PATH the routed agent needs. Hub processes
# inherit a minimal PATH (wezterm spawns `zsh -ic`, which skips .zprofile) —
# without this export, `agy-explore` and `agy` are unresolvable inside the hub
# and every routed explore silently degrades to the native fallback.
#
# Rollback to plain native: launch `opencode web` directly instead of this
# script (or touch ~/.config/ai-stack/force-native for a hot kill switch).
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
# opencode itself may live in linuxbrew (Linux) — include it when present so
# the launcher also works from PATH-minimal contexts (macOS brew is /opt/homebrew).
[ -d /home/linuxbrew/.linuxbrew/bin ] && PATH="/home/linuxbrew/.linuxbrew/bin:$PATH"
CONFIG="${AI_STACK_ROUTER_CONFIG:-$HOME/.config/ai-stack/opencode-router.json}"

if [ ! -f "$CONFIG" ]; then
  echo "router config not installed: $CONFIG" >&2
  echo "run your dotfiles install (links ai/opencode-router/opencode-router.json)" >&2
  exit 2
fi

export OPENCODE_CONFIG="$CONFIG"
exec opencode web "$@"
