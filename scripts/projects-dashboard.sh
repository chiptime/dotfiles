# projects-dashboard — global project tracker
#
# Merges two sources of truth into one Markdown snapshot:
#   1. Git truth: auto-discovered repos under ~/Code (last commit, dirty files, ahead/behind)
#   2. Human layer: scripts/projects.yaml (scope, status, description, next step)
#
# Usage:   projects              (via ~/bin symlink) or scripts/projects-dashboard.sh
# Output:  ~/.local/share/projects-dashboard/PROJECTS.md  (machine-local, never committed)

set -euo pipefail

DOTFILES="${DOTFILES:-$HOME/.dotfiles}"
CODE_ROOT="${CODE_ROOT:-$HOME/Code}"
ANNOTATIONS="$DOTFILES/scripts/projects.yaml"
OUT_DIR="$HOME/.local/share/projects-dashboard"
OUT="$OUT_DIR/PROJECTS.md"

# ---------------------------------------------------------------------------
# 1. Discover git repos
# ---------------------------------------------------------------------------
declare -A R_DIR R_BRANCH R_DATE R_DIRTY R_AHEAD R_BEHIND
repos=()

while IFS= read -r gitdir; do
  repo="$(dirname "$gitdir")"
  name="$(basename "$repo")"
  [ -n "${R_DATE["$name"]:-}" ] && continue  # first match wins (dupes: main repo over worktrees)

  branch="$(git -C "$repo" branch --show-current 2>/dev/null || true)"
  ahead="-"; behind="-"; date="-"; dirty="-"
  # Skip repos with unborn or broken HEAD (e.g. clone without commits, corrupt objects)
  if git -C "$repo" rev-parse -q --verify HEAD >/dev/null 2>&1; then
    date="$(git -C "$repo" log -1 --format=%cs 2>/dev/null || echo -)"
    dirty="$( (git -C "$repo" status --porcelain 2>/dev/null || true) | wc -l | tr -d ' ')"
    if [ -n "$branch" ] && git -C "$repo" rev-parse -q --verify "origin/$branch" >/dev/null 2>&1; then
      counts="$(git -C "$repo" rev-list --left-right --count "origin/$branch...$branch" 2>/dev/null || true)"
      if [ -n "$counts" ]; then
        behind="$(echo "$counts" | awk '{print $1}')"
        ahead="$(echo "$counts" | awk '{print $2}')"
      fi
    fi
  fi

  repos+=("$name")
  R_DIR["$name"]="$repo"
  R_BRANCH["$name"]="${branch:--}"
  R_DATE["$name"]="$date"
  R_DIRTY["$name"]="$dirty"
  R_AHEAD["$name"]="$ahead"
  R_BEHIND["$name"]="$behind"
done < <(find "$CODE_ROOT" -maxdepth 6 -type d -name .git \
  -not -path "*/node_modules/*" \
  -not -path "*sis-app-worktrees*" \
  -not -path "*/.cache/*" 2>/dev/null | sort)

# ---------------------------------------------------------------------------
# 2. Parse human annotations (simple YAML subset: 2-space indented key: value)
# ---------------------------------------------------------------------------
declare -A A_SCOPE A_STATUS A_DESC A_NEXT
annotated=()

if [ -f "$ANNOTATIONS" ]; then
  current=""
  while IFS= read -r line; do
    case "$line" in
      [a-zA-Z0-9_-]*:) current="${line%%:}"; annotated+=("$current") ;;
      "  "*": "*) key="${line%%:*}"; key="${key// /}"; val="${line#*: }"
         case "$key" in
           scope)  A_SCOPE["$current"]="$val" ;;
           status) A_STATUS["$current"]="$val" ;;
           desc)   A_DESC["$current"]="$val" ;;
           next)   A_NEXT["$current"]="$val" ;;
         esac ;;
    esac
  done < "$ANNOTATIONS"
fi

# ---------------------------------------------------------------------------
# 3. Emit Markdown grouped by status
# ---------------------------------------------------------------------------
mkdir -p "$OUT_DIR"
gen="$(date +%Y-%m-%d\ %H:%M)"
unclassified_count=0

emit_row() {
  local name="$1"
  local dirty="${R_DIRTY["$name"]}" dirty_flag=""
  [ "$dirty" != "0" ] && [ "$dirty" != "-" ] && dirty_flag=" ⚠️$dirty"
  local sync=""
  [ "${R_AHEAD["$name"]}" != "-" ] && [ "${R_AHEAD["$name"]}" != "0" ] && sync=" ↑${R_AHEAD["$name"]}"
  [ "${R_BEHIND["$name"]}" != "-" ] && [ "${R_BEHIND["$name"]}" != "0" ] && sync="$sync ↓${R_BEHIND["$name"]}"
  local next="${A_NEXT["$name"]:-}"
  [ -n "$next" ] && next=" · **Next:** $next"
  printf "| %s | \`%s\` | %s%s%s | %s%s |\n" \
    "$name" "${R_BRANCH["$name"]}" "${R_DATE["$name"]}" "$dirty_flag" "$sync" "${A_DESC["$name"]:-}" "$next"
}

section() {
  local title="$1" status="$2"
  local names=()
  for name in "${repos[@]}"; do
    [ "${A_STATUS["$name"]:-}" = "$status" ] && names+=("$name")
  done
  [ "${#names[@]}" -eq 0 ] && return 0
  printf "\n## %s (%d)\n\n| Repo | Branch | Last commit | Notes |\n|---|---|---|---|\n" "$title" "${#names[@]}"
  mapfile -t names < <(for n in "${names[@]}"; do echo "${R_DATE["$n"]} $n"; done | sort -r | awk '{print $2}')
  for name in "${names[@]}"; do emit_row "$name"; done
}

{
  echo "# 🗂️ Global Project Tracker"
  echo
  echo "_Generated $gen · sources: git sweep of \`$CODE_ROOT\` + \`scripts/projects.yaml\`_"
  echo "_Refresh: run \`projects\`_"

  section "🟢 Active" active
  section "📝 Proposal pending decision" proposal
  section "🟡 Paused" paused
  section "✅ Closed (work)" closed
  section "⚪ Cold / legacy" cold

  # unclassified repos found by the sweep
  unclassified=()
  for name in "${repos[@]}"; do
    [ -z "${A_STATUS["$name"]:-}" ] && unclassified+=("$name")
  done
  unclassified_count=${#unclassified[@]}
  if [ "$unclassified_count" -gt 0 ]; then
    printf "\n## ❓ Unclassified (%d)\n\nRepos discovered by the sweep but not yet annotated in projects.yaml:\n\n" "$unclassified_count"
    for name in "${unclassified[@]}"; do
      printf -- "- **%s** — branch \`%s\`, last commit %s, dirty %s\n" "$name" "${R_BRANCH["$name"]}" "${R_DATE["$name"]}" "${R_DIRTY["$name"]}"
    done
  fi

  echo
} > "$OUT"

echo "Dashboard written: $OUT"
echo "Repos scanned: ${#repos[@]} · annotated: ${#annotated[@]} · unclassified: $unclassified_count"
