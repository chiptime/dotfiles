#!/usr/bin/env bash
# Smoke test for time-ledger.sh with an isolated $HOME.
# Run: bash scripts/test_time_ledger.sh  (exits non-zero on failure)
set -euo pipefail

REAL="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export HOME="$T"
mkdir -p "$T/.dotfiles/scripts"
cp "$REAL/time-ledger.sh" "$T/.dotfiles/scripts/"
TL="bash $T/.dotfiles/scripts/time-ledger.sh"

fail() { echo "FAIL: $1"; exit 1; }

# add (manual) — single-digit day/month regression (D/M/YYYY parsing bug)
$TL add 2/1/2026 proj-x 1.5 "nota x" >/dev/null || fail "add manual"
grep -q '^2026-01-02,proj-x,1.5,manual,nota x$' "$T/.local/share/time-ledger/entries.csv" \
  || fail "CSV manual: fecha ISO + campos"

# add-ai with session id (6th column)
$TL add-ai 2/1/2026 proj-y 2 "nota y" ses_XYZ >/dev/null || fail "add-ai"
grep -q '^2026-01-02,proj-y,2,ai,nota y,ses_XYZ$' "$T/.local/share/time-ledger/entries.csv" \
  || fail "CSV ai con ses_id"

# day aggregation: per project + total
$TL day 2/1/2026 | grep -q "proj-x" || fail "day: proj-x"
$TL day 2/1/2026 | grep -qE "TOTAL +3\.5 h" || fail "day: total 3.5"

# tsv: project \t hours \t note (+ session column passthrough)
$TL tsv 2/1/2026 | grep -qP '^proj-x\t1.5\tnota x$' || fail "tsv: columnas proj-x"
$TL tsv 2/1/2026 | grep -qP '^proj-y\t2\tnota y\tses_XYZ$' || fail "tsv: ses_id en 4ª col"

# month aggregation over the fixture month
$TL month 2026-01 | grep -qE "TOTAL +3\.5 h" || fail "month: total"

# usage error: missing args must fail (non-zero) without writing
if $TL add 2/1/2026 proj-z >/dev/null 2>&1; then fail "add sin horas debe fallar"; fi
[ "$(wc -l < "$T/.local/share/time-ledger/entries.csv")" -eq 3 ] || fail "CSV sin filas basura"

# week: runs clean on a week with no entries
$TL week | grep -q "no entries" || $TL week >/dev/null || fail "week"

echo "PASS: test_time_ledger"
