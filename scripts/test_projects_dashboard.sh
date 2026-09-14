#!/usr/bin/env bash
# Smoke test for projects-dashboard.sh with an isolated $HOME (real git repos in tmp).
# Run: bash scripts/test_projects_dashboard.sh  (from anywhere; exits non-zero on failure)
set -euo pipefail

REAL="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export HOME="$T"

# fixture: dotfiles copy + yaml + two repos + ledger
mkdir -p "$T/.dotfiles/scripts" "$T/.local/share/time-ledger"
cp "$REAL/projects-dashboard.sh" "$T/.dotfiles/scripts/"
cat > "$T/.dotfiles/scripts/projects.yaml" <<'YAML'
proj-a:
  scope: personal
  status: active
  desc: fixture project A
proj-b:
  scope: personal
  status: cold
  desc: fixture project B
YAML
mkdir -p "$T/Code/personal/proj-a" "$T/Code/personal/proj-b"

# proj-a: repo with one commit + one dirty file
git -C "$T/Code/personal/proj-a" init -q
git -C "$T/Code/personal/proj-a" -c user.email=t@t -c user.name=t commit -q --allow-empty -m x
echo dirty > "$T/Code/personal/proj-a/f.txt"
# proj-b: repo without commits (unborn HEAD path)
git -C "$T/Code/personal/proj-b" init -q

# ledger: 1.5h for proj-a today
echo "date,project,hours,source,note" > "$T/.local/share/time-ledger/entries.csv"
echo "$(date +%F),proj-a,1.5,manual,fixture" >> "$T/.local/share/time-ledger/entries.csv"

bash "$T/.dotfiles/scripts/projects-dashboard.sh" >/dev/null
OUT="$T/.local/share/projects-dashboard/PROJECTS.md"
[ -f "$OUT" ] || { echo "FAIL: no PROJECTS.md"; exit 1; }

fail() { echo "FAIL: $1"; exit 1; }
grep -q "## 🟢 Active (1)" "$OUT" || fail "sección Active con 1 elemento"
grep -qE '^\| proj-a \|.*\| 1\.5 \|' "$OUT" || fail "fila proj-a con H(sem)=1.5"
grep -q '⚠️1' "$OUT" || fail "marcador dirty en proj-a"
grep -qE '^\| proj-b \|.*\| — \|' "$OUT" || fail "proj-b (sin commits) con horas vacías"
grep -q "fixture project A" "$OUT" || fail "desc del yaml presente"
grep -q "⏱️ Ledger semana actual: \*\*1,5h\*\*\|⏱️ Ledger semana actual: \*\*1.5h\*\*" "$OUT" || fail "total semanal en cabecera"

echo "PASS: test_projects_dashboard"
