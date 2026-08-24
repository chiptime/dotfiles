#!/usr/bin/env bash
#
# run-antigravity-task.sh — v1 wrapper for the Antigravity CLI (`agy`)
#
# NOTE: superseded for SDD exploration by the typed router backend now
# managed by the dotfiles (`ai/opencode-router`; run
# `agy-explore --input req.json`, binary at `~/.local/bin/agy-explore`).
# This spike remains the reference for the typed backend contract and for
# ad-hoc non-SDD agy tasks.
#
# Runs a single NON-INTERACTIVE task and validates it against an explicit
# success contract. Built for local, human-paced use of a personal
# Antigravity subscription.
#
# Usage:
#   run-antigravity-task.sh <brief.md> <workdir>
#
# Env:
#   AGY_BIN                path to the agy binary (default: agy from PATH)
#   AGY_TIMEOUT_SECONDS    hard timeout for the CLI (default: 300)
#   AGY_MAX_TASKS_PER_DAY  soft daily guard (default: 25) — see note below
#   AGY_EXTRA_DIRS         extra dirs exposed via --add-dir (space-separated).
#                          UNSET BY DEFAULT ON PURPOSE: agy runs with
#                          --dangerously-skip-permissions, so every directory
#                          listed here is WRITABLE by the agent. Only pass a
#                          repo when the task genuinely needs to read code.
#   AGY_EXPECT             space-separated artifact paths that MUST exist and
#                          be non-empty for the run to count as successful.
#
# THE SUCCESS CONTRACT (why this wrapper exists):
#   agy's own exit code is NOT a success signal — the agent returns 0 even when
#   the requested task failed outright. This wrapper exits:
#     0   run finished AND every AGY_EXPECT artifact exists and is non-empty
#     2   bad arguments, missing brief, or agy not found
#     3   daily guard reached
#     4   run finished but an expected artifact is missing or empty
#     124 timed out
#
# DAILY GUARD:
#   Counts agy's OWN conversation databases created today, NOT this script's
#   invocations — so it reflects real usage even when agy is called directly
#   and bypasses this wrapper. The number is a pattern guard, not a published
#   quota: no documented Antigravity limit was used to derive it.

set -u

AGY_BIN="${AGY_BIN:-agy}"
AGY_TIMEOUT_SECONDS="${AGY_TIMEOUT_SECONDS:-300}"
AGY_MAX_TASKS_PER_DAY="${AGY_MAX_TASKS_PER_DAY:-25}"
AGY_CONVERSATIONS_DIR="${AGY_CONVERSATIONS_DIR:-$HOME/.gemini/antigravity-cli/conversations}"

usage() {
  printf 'Usage: %s <brief.md> <workdir>\n' "$0"
}

if [ "$#" -ne 2 ]; then
  printf 'Error: expected 2 arguments, got %d\n' "$#" >&2
  usage >&2
  exit 2
fi

BRIEF_FILE="$1"
WORKDIR="$2"

if [ ! -f "$BRIEF_FILE" ]; then
  printf 'Error: brief file not found: %s\n' "$BRIEF_FILE" >&2
  exit 2
fi

if ! command -v "$AGY_BIN" >/dev/null 2>&1; then
  printf 'Error: agy binary not found: %s\n' "$AGY_BIN" >&2
  exit 2
fi

# --- daily guard, measured from agy's own state --------------------
tasks_today() {
  if [ -d "$AGY_CONVERSATIONS_DIR" ]; then
    find "$AGY_CONVERSATIONS_DIR" -maxdepth 1 -name '*.db' \
      -newermt "$(date +%F) 00:00" 2>/dev/null | wc -l | tr -d '[:space:]'
  else
    printf '0'
  fi
}

COUNT="$(tasks_today)"
if [ "$COUNT" -ge "$AGY_MAX_TASKS_PER_DAY" ]; then
  printf 'Error: daily guard reached (%s/%s real agy runs today).\n' \
    "$COUNT" "$AGY_MAX_TASKS_PER_DAY" >&2
  exit 3
fi

# --- run -----------------------------------------------------------
mkdir -p "$WORKDIR"
if [ "$BRIEF_FILE" != "$WORKDIR/BRIEF.md" ]; then
  cp "$BRIEF_FILE" "$WORKDIR/BRIEF.md"
fi

EXTRA_ARGS=()
if [ -n "${AGY_EXTRA_DIRS:-}" ]; then
  for _d in ${AGY_EXTRA_DIRS}; do
    EXTRA_ARGS+=(--add-dir "$_d")
  done
fi

if [ -z "${AGY_EXPECT:-}" ]; then
  printf 'Warning: AGY_EXPECT not set — success will NOT be validated.\n' >&2
fi

START="$(date +%s)"
timeout "$AGY_TIMEOUT_SECONDS" "$AGY_BIN" \
  --print "$(cat "$WORKDIR/BRIEF.md")" \
  --add-dir "$WORKDIR" \
  "${EXTRA_ARGS[@]}" \
  --dangerously-skip-permissions \
  > "$WORKDIR/run.log" 2>&1
EC=$?
END="$(date +%s)"
ELAPSED=$((END - START))

printf '%s\n' "$EC" > "$WORKDIR/agy-exit.code"
printf 'agy_exit=%d elapsed=%ds runs_today=%s\n' "$EC" "$ELAPSED" "$COUNT"

if [ "$EC" -ne 0 ]; then
  printf 'Error: agy exited %d — see %s\n' "$EC" "$WORKDIR/run.log" >&2
  exit "$EC"
fi

# --- success contract ----------------------------------------------
MISSING=""
if [ -n "${AGY_EXPECT:-}" ]; then
  for _a in ${AGY_EXPECT}; do
    [ -s "$_a" ] || MISSING="$MISSING $_a"
  done
fi

if [ -n "$MISSING" ]; then
  printf 'Error: expected artifact(s) missing or empty:%s\n' "$MISSING" >&2
  printf 'The agent session finished cleanly; the TASK did not.\n' >&2
  exit 4
fi

exit 0
