#!/usr/bin/env bash
# time-ledger — work-hours ledger per task/project
#
# Feeds daily-ai-timesheet with accumulated real hours instead of estimates.
# Data: ~/.local/share/time-ledger/entries.csv (machine-local, never committed)
# CSV:  date,project,hours,source,note[,session]  (date ISO; source: ai|manual;
#       session = OpenCode ses_... id, optional -> conversation deep-link in the web dashboard)
# Optional web link base for the dashboard: ~/.local/share/time-ledger/webbase
# (one line, e.g. http://100.121.4.15:4097/server/KEY; default http://127.0.0.1:4096)
#
# Capture model:
#   - Agent appends at session close via `add-ai` (projects-tracker convention)
#   - Bruno appends non-AI time (REUS, reuniones, picar a mano) via `add`
#
# Usage:
#   time-ledger add <D/M/YYYY> <project> <hours> [note] [ses_id]   manual entry
#   time-ledger add-ai <D/M/YYYY> <project> <hours> [note] [ses_id]  agent entry (session close)
#   time-ledger day [D/M/YYYY]     per-project totals for that day
#   time-ledger week               per-project + per-day, current week (Mon-Sun)
#   time-ledger month [YYYY-MM]    per-project totals for the month
#   time-ledger tsv <D/M/YYYY>     machine-readable day rows for the timesheet skill

set -euo pipefail

LEDGER_DIR="$HOME/.local/share/time-ledger"
CSV="$LEDGER_DIR/entries.csv"

init() { mkdir -p "$LEDGER_DIR"; [ -f "$CSV" ] || echo "date,project,hours,source,note" > "$CSV"; }

dmy_to_iso() { # D/M/YYYY -> YYYY-MM-DD
  local d="${1%%/*}" rest="${1#*/}" m="${1#*/}" y="${1##*/}"
  m="${rest%/*}"
  printf '%04d-%02d-%02d' "$((10#$y))" "$((10#$m))" "$((10#$d))"
}

add_row() {
  local src="$1" d="$2" proj="$3" hours="$4" note="${5:-}" ses="${6:-}"
  case "$d" in */*) d="$(dmy_to_iso "$d")";; esac
  echo "$d,$proj,$hours,$src,$note${ses:+,}$ses" >> "$CSV"
}

report() { # report <from_iso> <to_iso> <title>
  local from="$1" to="$2" title="$3"
  echo "== $title =="
  local rows total
  rows="$(awk -F, -v from="$from" -v to="$to" \
    '$1>=from && $1<=to {sum[$2]+=$3} END {for (p in sum) print sum[p], p}' "$CSV" | sort -rn)"
  total="$(awk -F, -v from="$from" -v to="$to" '$1>=from && $1<=to {s+=$3} END {print s+0}' "$CSV")"
  if [ "$total" = "0" ]; then echo "  (no entries)"; return 0; fi
  while read -r h p; do printf "  %-28s %5.1f h\n" "$p" "$h"; done <<< "$rows"
  printf "  %-28s %5.1f h\n" "TOTAL" "$total"
}

case "${1:-help}" in
  add|add-ai)
    [ $# -ge 4 ] || { echo "usage: time-ledger $1 <D/M/YYYY> <project> <hours> [note] [ses_id]"; exit 1; }
    init
    add_row "$([ "$1" = add-ai ] && echo ai || echo manual)" "$2" "$3" "$4" "${5:-}" "${6:-}"
    echo "added: $(dmy_to_iso "$2"),$3,$4,$([ "$1" = add-ai ] && echo ai || echo manual),${5:-}${6:+,}${6:-}"
    ;;
  day)
    init; d="${2:-$(date +%d/%m/%Y)}"; iso="$(dmy_to_iso "$d")"; report "$iso" "$iso" "$(date -d "$iso" +%d/%m/%Y)"
    ;;
  week)
    init
    if [ "$(date +%u)" -eq 1 ]; then mon="$(date +%F)"; else mon="$(date -d 'last monday' +%F)"; fi
    sun="$(date -d "$mon +6 days" +%F)"
    report "$mon" "$sun" "Semana $(date -d "$mon" +%d/%m) - $(date -d "$sun" +%d/%m/%Y)"
    echo "-- por día --"
    awk -F, -v from="$mon" -v to="$sun" '$1>=from && $1<=to {sum[$1]+=$3} END {for (d in sum) print d, sum[d]" h"}' "$CSV" | sort
    ;;
  month)
    init; m="${2:-$(date +%Y-%m)}"
    report "$m-01" "$m-31" "Mes $m"
    ;;
  tsv)
    init; [ $# -ge 2 ] || { echo "usage: time-ledger tsv <D/M/YYYY>"; exit 1; }
    iso="$(dmy_to_iso "$2")"
    awk -F, -v iso="$iso" '$1==iso {printf "%s\t%s\t%s\t%s\n", $2, $3, $5, $6}' "$CSV"
    ;;
  help|*)
    sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
