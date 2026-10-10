#!/usr/bin/env bash
# ==============================================================================
# OpenCode Smart Launcher & Attach Proxy
# ==============================================================================
# Intercepts standalone/resume launches (such as Herdr's `opencode --session <id>`
# or direct CLI calls) and transparently connects them to the central OpenCode
# Web server (`http://localhost:4096`) via `attach`.
#
# Passthrough commands (web, serve, acp, mcp, run, export, import, etc.) or when
# OPENCODE_STANDALONE=1 is set will execute the real binary directly.
# ==============================================================================
set -euo pipefail

REAL_OPENCODE="${OPENCODE_REAL_BIN:-$HOME/.local/opt/opencode/bin/opencode}"
if [[ ! -x "$REAL_OPENCODE" ]]; then
  if [[ -x "$HOME/.local/bin/opencode-bin" ]]; then
    REAL_OPENCODE="$HOME/.local/bin/opencode-bin"
  elif [[ -x "/home/linuxbrew/.linuxbrew/bin/opencode" ]]; then
    REAL_OPENCODE="/home/linuxbrew/.linuxbrew/bin/opencode"
  elif [[ -x "/opt/homebrew/bin/opencode" ]]; then
    REAL_OPENCODE="/opt/homebrew/bin/opencode"
  elif [[ -x "$HOME/.opencode/bin/opencode" ]]; then
    REAL_OPENCODE="$HOME/.opencode/bin/opencode"
  else
    echo "Error: Real opencode binary not found at $REAL_OPENCODE" >&2
    exit 1
  fi
fi

# 1. Escape hatch: allow running standalone / raw directly
if [[ "${OPENCODE_STANDALONE:-0}" == "1" || "${OPENCODE_RAW:-0}" == "1" ]]; then
  exec "$REAL_OPENCODE" "$@"
fi

# 2. Known subcommands that must NOT be converted to attach
# Note: 'session' is a subcommand ('opencode session list'), but '--session' is a flag!
case "${1:-}" in
  acp|agent|attach|auth|completion|db|debug|export|github|import|mcp|models|plug|plugin|pr|providers|run|serve|session|stats|uninstall|upgrade|web|-h|--help|-v|--version)
    exec "$REAL_OPENCODE" "$@"
    ;;
esac

# 3. If we are here, opencode was invoked to start an interactive TUI session:
# E.g.:
#   - `opencode --session <id>` (Herdr agent resume)
#   - `opencode -s <id>`
#   - `opencode` (interactive in current dir)
#   - `opencode /path/to/project`
#
SERVER_URL="${OPENCODE_SERVER_URL:-http://localhost:4096}"

# Check/Wait for OpenCode Web server:
# If port is not responding, wait up to 15s (covers cold-boot and service restarts)
if ! curl -sf "$SERVER_URL" >/dev/null 2>&1; then
  if [ -t 2 ]; then
    printf "\e[33m⏳ Esperando a que OpenCode Web (%s) esté listo...\e[0m\n" "$SERVER_URL" >&2
  fi
  attempts=0
  while ! curl -sf "$SERVER_URL" >/dev/null 2>&1; do
    sleep 0.5
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      break
    fi
  done
fi

# If server is up, attach to it!
if curl -sf "$SERVER_URL" >/dev/null 2>&1; then
  dir="$PWD"
  args=()
  has_dir=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir)
        has_dir=true
        args+=("$1" "$2")
        shift 2
        ;;
      --dir=*)
        has_dir=true
        args+=("$1")
        shift
        ;;
      -*)
        args+=("$1")
        shift
        ;;
      *)
        if [[ -d "$1" ]]; then
          dir="$(cd "$1" && pwd)"
        else
          args+=("$1")
        fi
        shift
        ;;
    esac
  done

  if [[ "$has_dir" == false ]]; then
    args+=("--dir" "$dir")
  fi

  exec "$REAL_OPENCODE" attach "$SERVER_URL" "${args[@]}"
fi

# Fallback: if server is completely offline after 15s, run standalone binary
if [ -t 2 ]; then
  printf "\e[31m⚠️ OpenCode Web (%s) no respondió. Ejecutando instancia local...\e[0m\n" "$SERVER_URL" >&2
fi
exec "$REAL_OPENCODE" "$@"
