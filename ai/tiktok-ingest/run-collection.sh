#!/usr/bin/env bash
#
# TikTok Ingest — Phase 0 launcher ("One Collection, One Command").
#
# Thin, fail-fast launcher ONLY: it resolves this script's directory
# robustly, puts the package's src/ on PYTHONPATH and dispatches to
# the tested Python application command:
#
#     python3 -m tiktok_ingest collection-run <collection-url> [options]
#
# ALL product logic lives in the Python command. This script performs
# no installation, no service operations, no network calls and holds
# no secrets. Arguments and the exit code are forwarded unchanged.
#
# Usage:
#     ./run-collection.sh "https://www.tiktok.com/@author/collection/name-id"

set -euo pipefail

# Resolve this script's directory robustly (symlink-safe), regardless
# of the caller's working directory.
SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_SOURCE" ]; do
  SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd -P)"
  SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"
  [[ "$SCRIPT_SOURCE" != /* ]] && SCRIPT_SOURCE="$SCRIPT_DIR/$SCRIPT_SOURCE"
done
SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd -P)"

SRC_DIR="$SCRIPT_DIR/src"

if [ ! -f "$SRC_DIR/tiktok_ingest/cli.py" ]; then
  echo "run-collection.sh: cannot find the tiktok_ingest package under $SRC_DIR" >&2
  exit 2
fi

if [ "$#" -lt 1 ]; then
  echo "usage: run-collection.sh <collection-url> [--dry-run] [--limit N] ..." >&2
  echo "  thin launcher for: python3 -m tiktok_ingest collection-run" >&2
  exit 2
fi

export PYTHONPATH="$SRC_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m tiktok_ingest collection-run "$@"
