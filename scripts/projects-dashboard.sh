#!/usr/bin/env bash
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
  -not -path "*apps-workspaces*" \
  -not -path "*/_archive/*" \
  -not -path "*/.onboard-capture/*" \
  -not -path "*/Clece/sis/.git" \
  -not -path "*__MACOSX*" \
  -not -path "*/.cache/*" 2>/dev/null | sort)

# ---------------------------------------------------------------------------
# 2. Parse human annotations (simple YAML subset: 2-space indented key: value)
# ---------------------------------------------------------------------------
declare -A A_SCOPE A_STATUS A_DESC A_NEXT A_GROUP
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
           group)  A_GROUP["$current"]="$val" ;;
         esac ;;
    esac
  done < "$ANNOTATIONS"
fi

# ---------------------------------------------------------------------------
# 2.5 Ledger hours (current week) from time-ledger CSV, if present
# ---------------------------------------------------------------------------
declare -A L_WEEK
LEDGER_CSV="$HOME/.local/share/time-ledger/entries.csv"
week_total=""
if [ -f "$LEDGER_CSV" ]; then
  if [ "$(date +%u)" -eq 1 ]; then lw_mon="$(date +%F)"; else lw_mon="$(date -d 'last monday' +%F)"; fi
  lw_sun="$(date -d "$lw_mon +6 days" +%F)"
  while read -r p h; do L_WEEK["$p"]="$h"; done < <(
    awk -F, -v from="$lw_mon" -v to="$lw_sun" \
      '$1>=from && $1<=to {sum[$2]+=$3} END {for (p in sum) printf "%s %.1f\n", p, sum[p]}' "$LEDGER_CSV")
  week_total="$(awk -F, -v from="$lw_mon" -v to="$lw_sun" '$1>=from && $1<=to {s+=$3} END {printf "%.1f", s+0}' "$LEDGER_CSV")"
fi

# group members inherit the parent's status
for n in "${!A_GROUP[@]}"; do
  if [ -z "${A_STATUS["$n"]:-}" ]; then
    parent="${A_GROUP["$n"]}"
    [ -n "${A_STATUS["$parent"]:-}" ] && A_STATUS["$n"]="${A_STATUS["$parent"]}"
  fi
done

# ---------------------------------------------------------------------------
# 2b. Hub mirror (Phase 3 — hub-state-mirror): pull the vault clone and load
# hub-state.json. HUB WINS: a repo with a hub note paints the hub's estado
# (activo→active, pausa→paused, archivado→cold); status_local stays the cache
# for repos without notes. Best-effort: any failure keeps the local cache.
declare -A A_HUB_ESTADO A_HUB_PRIO
HUB="$HOME/hub"
if [ -d "$HUB/.git" ]; then
  git -C "$HUB" pull --rebase --quiet 2>/dev/null || true
  if [ -s "$HUB/hub/hub-state.json" ]; then
    while IFS=$'\t' read -r slug estado prio; do
      A_HUB_ESTADO["$slug"]="$estado"
      A_HUB_PRIO["$slug"]="$prio"
    done < <(python3 - "$HUB/hub/hub-state.json" <<'PYEOF'
import json, sys
for p in json.load(open(sys.argv[1])).get("projects", []):
    print(f'{p["slug"]}\t{p["estado"]}\t{p.get("prioridad") or ""}')
PYEOF
    ) 2>/dev/null || true
  fi
fi
hub_section() {  # effective section for a repo (hub wins, local fallback)
  case "${A_HUB_ESTADO[$1]:-}" in
    activo) echo active ;;
    pausa) echo paused ;;
    archivado) echo cold ;;
    *) echo "${A_STATUS[$1]:-}" ;;
  esac
}

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
  local wh="${L_WEEK["$name"]:-}"
  [ -z "$wh" ] && wh="—"
  printf "| %s | \`%s\` | %s%s%s | %s | %s%s |\n" \
    "$name" "${R_BRANCH["$name"]}" "${R_DATE["$name"]}" "$dirty_flag" "$sync" "$wh" "${A_DESC["$name"]:-}" "$next"
}

section() {
  local title="$1" status="$2"
  local names=()
  for name in "${repos[@]}"; do
    [ "$(hub_section "$name")" = "$status" ] && names+=("$name")
  done
  [ "${#names[@]}" -eq 0 ] && return 0
  printf "\n## %s (%d)\n\n| Repo | Branch | Last commit | H(sem) | Notes |\n|---|---|---|---|---|\n" "$title" "${#names[@]}"
  mapfile -t names < <(for n in "${names[@]}"; do echo "${R_DATE["$n"]} $n"; done | sort -r | awk '{print $2}')
  for name in "${names[@]}"; do emit_row "$name"; done
}

{
  echo "# 🗂️ Global Project Tracker"
  echo
  echo "_Generated $gen · sources: git sweep of \`$CODE_ROOT\` + \`scripts/projects.yaml\`_"
  echo "_Refresh: run \`projects\` (now prints this view)_"
  [ -n "$week_total" ] && [ "$week_total" != "0.0" ] && echo "_⏱️ Ledger semana actual: **${week_total}h**_"

  section "🟢 Active" active
  section "📝 Proposal pending decision" proposal
  section "🟡 Paused" paused
  section "✅ Closed (work)" closed
  section "⚪ Cold / legacy" cold

  # unclassified repos found by the sweep
  unclassified=()
  for name in "${repos[@]}"; do
    [ -z "${A_STATUS["$name"]:-}" ] && [ -z "${A_HUB_ESTADO["$name"]:-}" ] && unclassified+=("$name")
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

# Web dashboard (best-effort; standalone from ai-quotas, served on its own port)
python3 "$DOTFILES/scripts/projects-html.py" "$OUT" >/dev/null 2>&1 || true

# --- Control Hub telemetry (fail-soft) ---------------------------------------
# The hub vault (~/hub, clone of gertru-lab/gertru-workspace) is the single
# source of truth. Repo machine-state flows INTO it here: hub/telemetria/ is
# a machine-writable zone with ONE writer (this sweep); nobody else edits it.
# Contract: pull --rebase → pathspec-scoped write → push. Never force.
HUB="$HOME/hub"
if [ -d "$HUB/.git" ]; then
  if git -C "$HUB" pull --rebase --quiet 2>/dev/null; then
    TDIR="$HUB/hub/telemetria"; mkdir -p "$TDIR"; touch "$TDIR/.gitkeep"
    {
      echo "# Telemetría de repos"
      echo
      echo "> Zona de escritura MECÁNICA: único escritor = el sweep (projects-dashboard.sh)."
      echo "> Nadie más edita aquí — ni Gertru ni Bruno. Versionada como todo el vault."
      echo
      echo "_Generado: $(date '+%Y-%m-%d %H:%M') por el sweep local · solo repos vivos (active/proposal/paused)_"
      echo
      echo "| Repo | Estado | Rama | Último commit | Dirty |"
      echo "|------|--------|------|---------------|-------|"
      for name in "${repos[@]}"; do
        st="${A_STATUS["$name"]:-}"
        case "$st" in active|proposal|paused) ;; *) continue ;; esac
        printf '| %s | %s | `%s` | %s | %s |\n' \
          "$name" "$st" "${R_BRANCH["$name"]:-?}" "${R_DATE["$name"]:-?}" "${R_DIRTY["$name"]:-0}"
      done
    } > "$TDIR/repos.md"
    if git -C "$HUB" status --porcelain -- hub/telemetria | grep -q .; then
      git -C "$HUB" add hub/telemetria/repos.md hub/telemetria/.gitkeep 2>/dev/null
      git -C "$HUB" commit -q -m "telemetria(sweep): repos.md $(date '+%Y-%m-%d %H:%M')" -- hub/telemetria 2>/dev/null \
        && git -C "$HUB" push --quiet 2>/dev/null \
        && echo "hub: telemetria pushed" || echo "hub: telemetria commit/push falló (no crítico)"
    fi
  else
    echo "hub: pull --rebase falló — telemetría omitida (no crítico)"
  fi
fi

echo
cat "$OUT"
echo "Repos scanned: ${#repos[@]} · annotated: ${#annotated[@]} · unclassified: $unclassified_count"

# ---------------------------------------------------------------------------
# 6. Hub delivery (Phase 2 — sensor-inbox-triage): atomic best-effort scp of
# projects.json into hub/inbox/bruno/. Never fatal: the morning chain (regen,
# digest) must complete even when the VPS is unreachable. Ordering per
# doc/MAPA_INGESTAS.md: regen -> espejo -> scp -> drain 07:30.
JSON_OUT="$OUT_DIR/projects.json"
if [ -s "$JSON_OUT" ]; then
  SSH_OPTS=(-F /dev/null -i "$HOME/.ssh/id_contabo_VPS_1"
    -o IdentitiesOnly=yes -o IdentityAgent=none -o ControlPath=none
    -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10)
  if scp -q "${SSH_OPTS[@]}" "$JSON_OUT" \
      "root@100.74.160.4:/tmp/bruno-projects.incoming" 2>/dev/null \
     && ssh "${SSH_OPTS[@]}" root@100.74.160.4 \
      'docker cp /tmp/bruno-projects.incoming aistack-all-o9aphm-openclaw-1:/home/node/.openclaw/workspace/hub/inbox/bruno/projects.json && rm /tmp/bruno-projects.incoming' 2>/dev/null; then
    echo "hub: projects.json entregado al inbox"
  else
    echo "hub: entrega de projects.json falló (no crítico)"
  fi
fi
