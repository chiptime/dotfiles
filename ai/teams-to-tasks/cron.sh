#!/usr/bin/env bash
# Poller-gated Teams sweep. Only validated functional completion commits state.
# This file is mapped to ~/.config/opencode/scripts/teams-to-tasks-cron.sh.
set -u
umask 077
export PATH="$HOME/.bun/bin:$HOME/.local/bin:/home/linuxbrew/.linuxbrew/bin:$PATH"

STATE_DIR="$HOME/.local/state/teams-to-tasks"
STATE_FILE="$STATE_DIR/last_run"
PAUSE_FILE="$STATE_DIR/PAUSE"
LOG="$STATE_DIR/cron.log"
LOCK="${TEAMS_LOCK_FILE:-/tmp/teams-to-tasks.lock}"
NOTIFY_EXE="${TEAMS_NOTIFY_EXE:-$HOME/.local/bin/wsl-notify-send.exe}"
NOTIFY_EXE_FALLBACK="/mnt/d/Bruno/wsl/tools/wsl-notify-send.exe"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
mkdir -p "$STATE_DIR" || exit 1

notify() {
  local urgency="$1" title="$2" body="$3" msg
  # Only fixed wrapper text or validator-generated counts reach notifications.
  msg="$(printf '%s' "$title - $body" | iconv -f UTF-8 -t ASCII//TRANSLIT 2>/dev/null | tr -cd '\11\12\40-\176')"
  if [ -x "$NOTIFY_EXE" ]; then
    "$NOTIFY_EXE" "$msg" >/dev/null 2>&1 || true
  elif [ -x "$NOTIFY_EXE_FALLBACK" ]; then
    "$NOTIFY_EXE_FALLBACK" "$msg" >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send -a teams-to-tasks -u "$urgency" "$title" "$body" 2>/dev/null || true
  else
    printf '%s [notify-off] %s\n' "$(date '+%F %T')" "$msg" >> "$LOG"
  fi
}

if [ -e "$PAUSE_FILE" ]; then
  printf '%s [paused] PAUSE file present\n' "$(date '+%F %T')" >> "$LOG"
  exit 0
fi

# Keep the existing lock across polling, agent execution, validation and commit.
exec 9>"$LOCK"
if ! flock -n 9; then
  printf '%s [skip] another run holds the lock\n' "$(date '+%F %T')" >> "$LOG"
  exit 0
fi

MODE="${1:-sweep}"
case "$MODE" in sweep|digest) ;; *) exit 1 ;; esac
SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
mkdir -p "$STATE_DIR/runs" || exit 1
RUN_DIR="$(mktemp -d "$STATE_DIR/runs/$(date -u '+%Y%m%dT%H%M%SZ')-$MODE-XXXXXX")" || exit 1
RUN_ID="${RUN_DIR##*/}"
POLL_START_T="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
LOCAL_NOW="$(date '+%Y-%m-%dT%H:%M:%S%:z (%Z)')"
PREV_LAST_RUN="$(date '+%Y-%m-%dT00:00:00%:z')"
HAD_STATE=0
if [ -e "$STATE_FILE" ]; then
  cp "$STATE_FILE" "$RUN_DIR/checkpoint.before" || exit 1
  PREV_LAST_RUN="$(cat "$RUN_DIR/checkpoint.before")"
  HAD_STATE=1
fi
if [ "$MODE" = "digest" ]; then PREV_LAST_RUN="$(date '+%Y-%m-%dT00:00:00%:z')"; fi
export TEAMS_RUN_DIR="$RUN_DIR" TEAMS_RUN_ID="$RUN_ID" TEAMS_MODE="$MODE"
export TEAMS_WINDOW_START="$PREV_LAST_RUN" TEAMS_WINDOW_END="$POLL_START_T"
jq -n --arg run_id "$RUN_ID" --arg mode "$MODE" --arg start "$PREV_LAST_RUN" --arg end "$POLL_START_T" --arg local_now "$LOCAL_NOW" \
  '{run_id:$run_id, mode:$mode, window_start:$start, window_end:$end, local_now:$local_now}' > "$RUN_DIR/window.json" || exit 1

run_poller() {
  if [ -n "${TEAMS_POLL_CMD:-}" ]; then
    bash -c "$TEAMS_POLL_CMD"
  else
    timeout -k 15s 120s bun "$SCRIPT_DIR/poller.ts"
  fi
}

run_agent() {
  if [ -n "${TEAMS_AGENT_CMD:-}" ]; then
    bash -c "$TEAMS_AGENT_CMD"
  else
    # Load only the existing secret environment, not interactive zsh plugins/hooks.
    timeout -k 15s 900s zsh -f -c '
      if [[ -r "$1/../../shell/private-env.sh" ]]; then source "$1/../../shell/private-env.sh"; fi
      exec bun "$1/unattended.ts"
    ' teams-reader "$SCRIPT_DIR"
  fi
}

run_writer() {
  local actions_file="$1"
  if [ -n "${TEAMS_WRITER_CMD:-}" ]; then
    bash -c "$TEAMS_WRITER_CMD"
  else
    if [ -r "$SCRIPT_DIR/../../shell/private-env.sh" ]; then
      # shellcheck source=/dev/null
      . "$SCRIPT_DIR/../../shell/private-env.sh"
    fi
    timeout -k 15s 120s bun "$SCRIPT_DIR/src/notion-writer.ts" "$actions_file"
  fi
}

if [ "$MODE" = "sweep" ]; then
  # Poller may advance its own no-news state; give it a disposable copy, NEVER
  # the committed checkpoint. Discovery alone cannot certify a complete sweep.
  if [ "$HAD_STATE" = 1 ]; then cp "$RUN_DIR/checkpoint.before" "$RUN_DIR/poll-state" || exit 1; fi
  export TEAMS_STATE_FILE="$RUN_DIR/poll-state"
  run_poller > "$RUN_DIR/poller.log" 2>&1
  POLL_RC=$?
  printf '%s\n' "$POLL_RC" > "$RUN_DIR/poller.exit"
  case "$POLL_RC" in
    0) ;;
    1)
      printf '%s [quiet] poller: no news in que+Today; checkpoint unchanged; run=%s\n' "$(date '+%F %T')" "$RUN_ID" >> "$LOG"
      exit 0 ;;
    *)
      printf '%s [poll-fail] poller rc=%s; agent skipped, state untouched; run=%s\n' "$(date '+%F %T')" "$POLL_RC" "$RUN_ID" >> "$LOG"
      notify critical "[WARN] Teams sweep" "Poller failed (rc $POLL_RC); checkpoint unchanged; run=$RUN_ID"
      exit 1 ;;
  esac
fi

export TEAMS_PROMPT="Run the supplied teams-to-tasks skill in unattended autonomous $MODE mode.
Run ID: $RUN_ID
Current local date/time and timezone: $LOCAL_NOW
UTC window_end: $POLL_START_T
Exact window_start: $PREV_LAST_RUN
For sweep: discover using que + Today and review messages after window_start up to window_end.
Identify requests, deadlines, blockers and follow-ups directed to Bruno. Discard resolved requests.
Preserve fingerprint dedupe: existing tasks are enriched only with new context, or marked done only on explicit resolution of that exact task. New tasks go directly to Inbox with source attribution and the existing routing rules. Inspect relevant source images; if blocked, fail rather than skip.
For digest: review today's discovered messages up to window_end, summarize topics/decisions/open questions/already captured tasks, and append today's heading under the existing authorized Digest Teams parent only if absent.
Do not change schema, routing, permissions or browser locks. Close the browser as the last tool call.
Return only the final JSON completion contract from your instructions, using these exact run/window values."

run_agent > "$RUN_DIR/events.jsonl" 2> "$RUN_DIR/agent.stderr"
AGENT_RC=$?
printf '%s\n' "$AGENT_RC" > "$RUN_DIR/agent.exit"
# Preserve full diagnostics on EVERY path; no raw output is sent to a toast.
ACTIONS_FILE="$RUN_DIR/actions.json"
if [ "$AGENT_RC" = 0 ] && bun "$SCRIPT_DIR/src/completion.ts" "$RUN_DIR/events.jsonl" "$RUN_DIR/agent.stderr" \
  "$RUN_ID" "$MODE" "$PREV_LAST_RUN" "$POLL_START_T" "$ACTIONS_FILE" > "$RUN_DIR/summary.txt" 2> "$RUN_DIR/validation.log"; then

  WRITER_RC=0
  if [ -f "$ACTIONS_FILE" ] && [ "$(jq 'length' "$ACTIONS_FILE" 2>/dev/null || echo 0)" -gt 0 ]; then
    export TEAMS_ACTIONS_FILE="$ACTIONS_FILE"
    run_writer "$ACTIONS_FILE" > "$RUN_DIR/writer.log" 2>&1
    WRITER_RC=$?
    printf '%s\n' "$WRITER_RC" > "$RUN_DIR/writer.exit"
    if [ "$WRITER_RC" -ne 0 ]; then
      printf '%s\n' "Notion writer failed (rc $WRITER_RC)" >> "$RUN_DIR/validation.log"
    fi
  fi

  if [ "$WRITER_RC" = 0 ]; then
    if [ "$MODE" = "sweep" ]; then
      # Do not overwrite a checkpoint changed outside our lock during the run.
      if { [ "$HAD_STATE" = 1 ] && ! cmp -s "$STATE_FILE" "$RUN_DIR/checkpoint.before"; } || \
         { [ "$HAD_STATE" = 0 ] && [ -e "$STATE_FILE" ]; }; then
        printf '%s\n' 'Checkpoint changed concurrently; commit rejected' > "$RUN_DIR/validation.log"
      elif printf '%s' "$POLL_START_T" > "$RUN_DIR/checkpoint.next" && mv "$RUN_DIR/checkpoint.next" "$STATE_FILE"; then
        printf '%s\n' committed > "$RUN_DIR/checkpoint.status"
      else
        printf '%s\n' 'Checkpoint commit failed' > "$RUN_DIR/validation.log"
      fi
    fi
    if [ "$MODE" = "digest" ] || [ -f "$RUN_DIR/checkpoint.status" ]; then
      SUMMARY="$(cat "$RUN_DIR/summary.txt")"
      printf '%s [ok] %s completed: %s; run=%s\n' "$(date '+%F %T')" "$MODE" "$SUMMARY" "$RUN_ID" >> "$LOG"
      notify normal "[OK] Teams $MODE" "$SUMMARY"
      exit 0
    fi
  fi
fi

printf '%s [fail] %s incomplete (agent rc=%s); state not advanced; run=%s\n' "$(date '+%F %T')" "$MODE" "$AGENT_RC" "$RUN_ID" >> "$LOG"
notify critical "[FAIL] Teams $MODE" "Incomplete run; checkpoint unchanged; inspect runs/$RUN_ID"
exit 1
