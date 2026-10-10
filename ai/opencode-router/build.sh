#!/usr/bin/env bash
#
# build.sh — deploy the router config.
#
# The sdd-explore agy layer (src/agy/, the agy-explore binary, and the
# sdd-explore-dispatch plugin) was retired: both of its consumers (the
# sdd-explore agent and the antigravity-explore skill) are no longer used,
# and the engine lives on solely in the agy-bridge repository. The committed
# opencode-router.json is now a static artifact deployed as-is (step (b) below);
# config/ retains the historical render inputs.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$PWD"
OUT="$ROOT/opencode-router.json"

[ -f "$OUT" ] || { echo "router config missing: $OUT" >&2; exit 2; }

# deploy the router config to the runtime path as a REAL file, never a
# symlink into this repo. History (2026-09-08): this path was a dotbot symlink
# to the tracked template, so an in-place runtime rewrite (ad-hoc model-fix)
# wrote THROUGH the link into the repo (tabs→2-space reformat + injected
# top-level "model"). rm -f replaces any stale symlink without following it;
# the deploy overwrites runtime edits — the repo stays the only source of
# truth, and nothing outside this repo can mutate a tracked file.
RUNTIME="$HOME/.config/ai-stack/opencode-router.json"
rm -f "$RUNTIME"
mkdir -p "$(dirname "$RUNTIME")"
install -m 644 "$OUT" "$RUNTIME"
echo "deployed: $RUNTIME (real copy — never a symlink into the repo)"

echo "done: run 'git diff $RUNTIME' to confirm the deployed config matches the committed file."
