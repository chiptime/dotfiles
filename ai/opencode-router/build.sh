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
echo "done: run 'git diff $OUT' to confirm the rendered config is unchanged before committing."
