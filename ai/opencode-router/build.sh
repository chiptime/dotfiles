#!/usr/bin/env bash
#
# build.sh — render the router config and compile the agy-explore binary.
#
# This directory is the complete source of truth for the sdd-explore router:
# (a) re-render opencode-router.json from config/ (template + prompt), then
# (b) compile the CLI to ~/.local/bin/agy-explore. The only write inside the
# dotfiles tree is opencode-router.json (committed); the binary lands in
# ~/.local/bin. Requires: bun.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$PWD"
TEMPLATE="$ROOT/config/opencode-router.template.json"
PROMPT_FILE="$ROOT/config/prompts/sdd-explore-router.md"
OUT="$ROOT/opencode-router.json"
BIN="$HOME/.local/bin/agy-explore"

[ -f "$TEMPLATE" ] || { echo "template missing: $TEMPLATE" >&2; exit 2; }
[ -f "$PROMPT_FILE" ] || { echo "prompt missing: $PROMPT_FILE" >&2; exit 2; }

# Render via the pure module (authoritative path, same as the render step the
# ai-stack installer used before the router moved to dotfiles).
RENDER="$ROOT/src/agy/render.ts" TEMPLATE="$TEMPLATE" PROMPT_FILE="$PROMPT_FILE" OUT="$OUT" \
  bun -e '
    const { readFileSync, writeFileSync } = require("node:fs");
    const { renderTemplate } = require(process.env.RENDER);
    const t = JSON.parse(readFileSync(process.env.TEMPLATE, "utf8"));
    const p = readFileSync(process.env.PROMPT_FILE, "utf8");
    writeFileSync(process.env.OUT, JSON.stringify(renderTemplate(t, p), null, "\t") + "\n");
  '
echo "rendered: $OUT (from config/ template + prompt)"

mkdir -p "$HOME/.local/bin"
bun build --compile src/agy/cli.ts --outfile "$BIN"
echo "compiled: $BIN"

# (c) bundle the sdd-explore dispatcher plugin into the global plugins dir.
# Design option (b): the versioned source of truth is src/plugin/
# sdd-explore-dispatch.ts + src/agy/dispatch-core.ts in THIS repo; build.sh
# renders a single self-contained artifact into ~/.config/opencode/plugins
# (unversioned, auto-loaded at startup). No runtime cross-repo imports, so
# the plugin survives reboots/upgrades with zero path magic. The .ts
# extension is deliberate: the auto-loader demonstrably picks up .ts files
# there today, and the bundle payload is plain ESM JS (valid TS input).
PLUGIN_SRC="$ROOT/src/plugin/sdd-explore-dispatch.ts"
PLUGIN_OUT="$HOME/.config/opencode/plugins/sdd-explore-dispatch.ts"
[ -f "$PLUGIN_SRC" ] || { echo "plugin source missing: $PLUGIN_SRC" >&2; exit 2; }
mkdir -p "$HOME/.config/opencode/plugins"
bun build --target=bun --format=esm "$PLUGIN_SRC" --outfile "$PLUGIN_OUT"
echo "bundled: $PLUGIN_OUT"

echo "done: run 'git diff $OUT' to confirm the rendered config is unchanged before committing."
