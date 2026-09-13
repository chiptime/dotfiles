#!/usr/bin/env bash
# Weekly projects digest: regenerate the dashboard snapshot and push a short
# summary to the phone via ntfy. Secrets (NTFY_TOPIC) live in private-env.sh,
# which is symlinked OUTSIDE the repo — never inline values here.
set -euo pipefail

DOTFILES="$HOME/.dotfiles"
SNAPSHOT="$HOME/.local/share/projects-dashboard/PROJECTS.md"
STATE="$HOME/.local/state/projects-dashboard"
mkdir -p "$STATE"

[ -f "$DOTFILES/shell/private-env.sh" ] && . "$DOTFILES/shell/private-env.sh" || true

"$DOTFILES/scripts/projects-dashboard.sh" >/dev/null

counts="$(grep -E '^## ' "$SNAPSHOT" | sed -E 's/^## ([^()]*) \(([0-9]+)\)$/\1 \2 ·/' | tr '\n' ' ')"
dirty="$(grep -E '⚠️[0-9]+' "$SNAPSHOT" | awk -F'|' '{gsub(/ /,"",$2); print $2}' | head -5 | tr '\n' ' ')"
msg="📊 ${counts}
⚠️ dirty: ${dirty:-none}"

if [ -n "${NTFY_TOPIC:-}" ]; then
  if ! curl -fsS -m 15 -H "Title: Projects weekly" -H "Tags: bar_chart" -d "$msg" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1; then
    echo "$(date -Is) ntfy send FAILED" >> "$STATE/cron.log"
    exit 0
  fi
else
  echo "$(date -Is) NTFY_TOPIC empty — snapshot regenerated, no push" >> "$STATE/cron.log"
  exit 0
fi

echo "$(date -Is) digest sent" >> "$STATE/cron.log"
