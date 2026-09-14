#!/usr/bin/env bash
# Runs every automated suite of the projects subsystem. Zero side effects:
# bash suites use isolated $HOME fixtures; the python suite uses temp files.
# Run from anywhere: bash ~/.dotfiles/scripts/run-smoke-tests.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
rc=0

echo "== test_projects_dashboard =="
bash "$HERE/test_projects_dashboard.sh" || rc=1
echo "== test_time_ledger =="
bash "$HERE/test_time_ledger.sh" || rc=1
echo "== test_projects_html (python) =="
( cd "$HERE/.." && python3 -m unittest scripts.test_projects_html -q 2>&1 | tail -1 ) || rc=1

[ "$rc" -eq 0 ] && echo "ALL SUITES GREEN" || echo "SOME SUITE FAILED"
exit "$rc"
