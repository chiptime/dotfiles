#!/usr/bin/env bash
#
# build.sh — bundle the quota-balancer plugin for OpenCode
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$PWD"

QB_SRC="$ROOT/src/quota-balancer.ts"
QB_OUT="$HOME/.config/opencode/plugins/quota-balancer.ts"

[ -f "$QB_SRC" ] || { echo "plugin source missing: $QB_SRC" >&2; exit 2; }

mkdir -p "$(dirname "$QB_OUT")"
bun build --target=bun --format=esm "$QB_SRC" --outfile "$QB_OUT"
echo "bundled: $QB_OUT"
