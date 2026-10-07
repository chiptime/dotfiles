#!/bin/bash
set -Eeuo pipefail

# =============================================================================
# Alt+G Evaluation Harness — dotfiles shell/zsh AI widgets
# Mirrors the real widget contract in shell/zsh/.zshrc (_ai_call_litellm):
# chat/completions payload, Bearer auth, .choices[0].message.content parsing.
# Run: ./evaluate.sh cases/sample.txt
# Run all: ./evaluate.sh --all
# Offline baseline comparison: ./evaluate.sh --diff [case_file.txt]
# =============================================================================

readonly HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly CASES_DIR="${HARNESS_DIR}/cases"
readonly SNAPSHOTS_DIR="${HARNESS_DIR}/snapshots"
readonly LITELLM_URL="${ZSH_AI_URL:-http://127.0.0.1:4000/v1/chat/completions}"
readonly LITELLM_MODEL="${ZSH_AI_MODEL:-qwen-3.6-27b}"
readonly LITELLM_KEY="${ZSH_AI_KEY:-}"
readonly REQUEST_TIMEOUT="${ZSH_AI_TIMEOUT:-10}"

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m'

# Track results for --all mode
PASSED=0
FAILED=0
TOTAL=0

log_info()  { echo -e "${GREEN}✓${NC} $*"; }
log_warn()  { echo -e "${YELLOW}⚠${NC} $*" >&2; }
log_error() { echo -e "${RED}✗${NC} $*" >&2; }

# =============================================================================
# Dependency Check
# =============================================================================

check_dependencies() {
  local missing=()
  command -v curl &>/dev/null || missing+=("curl")
  command -v jq   &>/dev/null || missing+=("jq")
  if [ ${#missing[@]} -gt 0 ]; then
    log_error "Missing: ${missing[*]}"
    exit 1
  fi
}

# Offline path (--diff) needs neither curl nor jq — only diff.
check_diff_dependencies() {
  command -v diff &>/dev/null || { log_error "Missing: diff"; exit 2; }
}

# =============================================================================
# Test Case Parsing
# =============================================================================

parse_test_case() {
  local case_file="$1"
  [ -f "$case_file" ] || { log_error "Not found: $case_file"; exit 1; }

  local content; content=$(cat "$case_file")

  local context=""; local buffer=""; local expectations=""

  if echo "$content" | grep -q "=== Context ==="; then
    context=$(echo "$content" | sed -n '/=== Context ===/,/=== [A-Za-z]* ===/p' | tail -n +2 | head -n -1)
  fi

  if echo "$content" | grep -q "=== Buffer ==="; then
    buffer=$(echo "$content" | sed -n '/=== Buffer ===/,/=== [A-Za-z]* ===/p' | tail -n +2 | head -n -1)
  else
    log_error "Missing '=== Buffer ===' in $case_file"
    exit 1
  fi

  if echo "$content" | grep -q "=== Expectations ==="; then
    expectations=$(echo "$content" | sed -n '/=== Expectations ===/,$ p' | tail -n +2)
  fi

  export ALT_G_CONTEXT="$context"
  export ALT_G_BUFFER="$buffer"
  export ALT_G_EXPECTATIONS="$expectations"
}

# =============================================================================
# Payload Building
# =============================================================================

build_payload() {
  local ctx="${ALT_G_CONTEXT:-}"
  local buf="${ALT_G_BUFFER:-}"

  # Same system prompt and message shape as _ai_call_litellm in .zshrc.
  local sys="You are a zsh command generator. Output ONLY the raw command. No markdown, no backticks, no explanation. Use single quotes for spaces. Double quotes only for variable expansion. Prefer modern alternatives: rg over grep, fd over find, bat over cat, jq for JSON, eza over ls."

  local usr="Task: $buf"
  [ -n "$ctx" ] && usr="Context: $ctx

$usr"

  jq -n \
    --arg model "$LITELLM_MODEL" \
    --arg system "$sys" \
    --arg user "$usr" \
    '{
      model: $model,
      messages: [
        {role: "system", content: $system},
        {role: "user", content: $user}
      ],
      max_tokens: 120,
      temperature: 0.1
    }'
}

# =============================================================================
# LiteLLM API Call
# =============================================================================

call_litellm() {
  local payload="$1"
  local -a auth=()
  [ -n "$LITELLM_KEY" ] && auth=(-H "Authorization: Bearer $LITELLM_KEY")
  local response
  response=$(curl -s -X POST "$LITELLM_URL" \
    -H "Content-Type: application/json" \
    "${auth[@]}" \
    -d "$payload" \
    --max-time "$REQUEST_TIMEOUT" \
    --fail 2>&1) || {
    log_error "LiteLLM unreachable at $LITELLM_URL"
    exit 1
  }

  if echo "$response" | jq -e '.error' &>/dev/null; then
    log_error "LiteLLM error: $(echo "$response" | jq -r '.error')"
    exit 1
  fi
  echo "$response"
}

# =============================================================================
# Snapshot Saving
# =============================================================================

save_snapshot() {
  local response="$1"; local case_name="$2"
  mkdir -p "$SNAPSHOTS_DIR"

  local text; text=$(echo "$response" | jq -r '.choices[0].message.content // empty')
  [ -n "$text" ] || { log_error "Empty response text"; exit 1; }

  local filename="${case_name// /-}.actual.txt"
  echo "$text" > "$SNAPSHOTS_DIR/$filename"
  log_info "Snapshot → $filename"
}

# =============================================================================
# Single case runner
# =============================================================================

evaluate_one() {
  local case_file="$1"
  local case_name; case_name=$(basename "$case_file" .txt)

  parse_test_case "$case_file"
  local payload; payload=$(build_payload)
  local response; response=$(call_litellm "$payload")
  save_snapshot "$response" "$case_name"
}

# =============================================================================
# --all mode
# =============================================================================

evaluate_all() {
  shopt -s nullglob
  local cases=("$CASES_DIR"/*.txt)
  shopt -u nullglob

  if [ ${#cases[@]} -eq 0 ]; then
    log_warn "No .txt cases found in $CASES_DIR"
    exit 0
  fi

  echo "Evaluating ${#cases[@]} case(s)..."
  echo ""

  for case_file in "${cases[@]}"; do
    local name; name=$(basename "$case_file")
    TOTAL=$((TOTAL + 1))
    if evaluate_one "$case_file"; then
      PASSED=$((PASSED + 1))
    else
      FAILED=$((FAILED + 1))
      log_error "FAILED: $name"
    fi
    echo ""
  done

  echo "================================"
  echo -e "${GREEN}Passed: $PASSED${NC}  ${RED}Failed: $FAILED${NC}  Total: $TOTAL"
  echo "Snapshots in: $SNAPSHOTS_DIR"
  echo -e "Run: ${YELLOW}git diff $SNAPSHOTS_DIR${NC} to review changes"
}

# =============================================================================
# --diff mode: OFFLINE snapshot vs baseline comparison (read-only)
#   0 = all equal, 1 = changes/missing, 2 = operational error
# =============================================================================

DIFF_MATCHED=0
DIFF_CHANGED=0
DIFF_MISSING_ACTUAL=0
DIFF_MISSING_BASELINE=0

usage() {
  echo "Usage: $0 <case_file.txt> | --all | --diff [case_file.txt]"
  echo "  $0 cases/sample.txt     # evaluate one case (requires LiteLLM)"
  echo "  $0 --all                # evaluate all cases (requires LiteLLM)"
  echo "  $0 --diff [case.txt]    # offline: diff *.actual.txt vs *.expected.txt"
  echo "  --diff exit codes: 0 all equal | 1 changes/missing | 2 operational error"
}

diff_summary_and_exit() {
  local total_missing=$((DIFF_MISSING_ACTUAL + DIFF_MISSING_BASELINE))
  local missing_label="Missing: $total_missing"
  if [ "$total_missing" -gt 0 ]; then
    missing_label="Missing: $total_missing (actual: $DIFF_MISSING_ACTUAL, baseline: $DIFF_MISSING_BASELINE)"
  fi
  echo "================================"
  echo -e "${GREEN}Matched: $DIFF_MATCHED${NC}  ${RED}Changed: $DIFF_CHANGED${NC}  ${YELLOW}${missing_label}${NC}"
  echo "Snapshots in: $SNAPSHOTS_DIR (unmodified by --diff)"
  if [ "$DIFF_CHANGED" -gt 0 ] || [ "$total_missing" -gt 0 ]; then
    exit 1
  fi
  exit 0
}

run_diff_mode() {
  if [ $# -gt 1 ]; then
    log_error "Invalid CLI: --diff takes at most one case file argument"
    usage
    exit 2
  fi

  local -a names=()
  if [ $# -eq 1 ]; then
    case "$1" in
      -*) log_error "Unknown option: $1"; usage; exit 2 ;;
    esac
    # Same mapping as the evaluation path: basename minus .txt, spaces -> hyphens.
    # The case file itself is never read — only its name matters, so any cwd works.
    local case_name
    case_name=$(basename "$1" .txt)
    case_name="${case_name// /-}"
    if [ ! -f "$SNAPSHOTS_DIR/$case_name.actual.txt" ] && [ ! -f "$SNAPSHOTS_DIR/$case_name.expected.txt" ]; then
      log_error "No snapshots found for case '$case_name' in $SNAPSHOTS_DIR"
      exit 2
    fi
    names=("$case_name")
  else
    # Union of all snapshot names (deduplicated), sorted, space-safe.
    # Each glob strips ONLY the suffix it matched, so names that legitimately
    # contain ".actual.txt"/".expected.txt" (e.g. case "release.expected.txt")
    # keep their full name instead of collapsing to a shorter one.
    local -A seen=()
    local f b
    shopt -s nullglob
    for f in "$SNAPSHOTS_DIR"/*.actual.txt; do
      b=$(basename "$f")
      seen["${b%.actual.txt}"]=1
    done
    for f in "$SNAPSHOTS_DIR"/*.expected.txt; do
      b=$(basename "$f")
      seen["${b%.expected.txt}"]=1
    done
    shopt -u nullglob
    if [ ${#seen[@]} -eq 0 ]; then
      log_error "No snapshots found in $SNAPSHOTS_DIR"
      exit 2
    fi
    # Checked ordering: a missing or failing sort (including failure after
    # partial output) is an operational error — never a silent empty/partial
    # batch. Explicit-case mode above needs none of these tools.
    local listing_missing=()
    command -v sort &>/dev/null || listing_missing+=("sort")
    command -v mktemp &>/dev/null || listing_missing+=("mktemp")
    if [ ${#listing_missing[@]} -gt 0 ]; then
      log_error "Missing: ${listing_missing[*]}"
      exit 2
    fi
    local tmp_list
    tmp_list=$(mktemp) || { log_error "mktemp failed while listing snapshots"; exit 2; }
    if ! printf '%s\0' "${!seen[@]}" > "$tmp_list" || ! sort -z -o "$tmp_list" "$tmp_list"; then
      rm -f "$tmp_list"
      log_error "sort failed while ordering snapshot names"
      exit 2
    fi
    while IFS= read -r -d '' f; do
      names+=("$f")
    done < "$tmp_list"
    rm -f "$tmp_list"
    if [ ${#names[@]} -eq 0 ]; then
      log_error "No snapshot names survived ordering"
      exit 2
    fi
  fi

  local name actual expected dstat diff_out
  for name in "${names[@]}"; do
    actual="$SNAPSHOTS_DIR/$name.actual.txt"
    expected="$SNAPSHOTS_DIR/$name.expected.txt"
    if [ ! -f "$expected" ]; then
      DIFF_MISSING_BASELINE=$((DIFF_MISSING_BASELINE + 1))
      log_warn "Missing baseline: $name (no ${name}.expected.txt — cp after human review)"
      continue
    fi
    if [ ! -f "$actual" ]; then
      DIFF_MISSING_ACTUAL=$((DIFF_MISSING_ACTUAL + 1))
      log_warn "Missing actual: $name (no ${name}.actual.txt — run an evaluation first)"
      continue
    fi
    dstat=0
    diff_out=$(diff -u "$expected" "$actual") || dstat=$?
    if [ "$dstat" -eq 0 ]; then
      DIFF_MATCHED=$((DIFF_MATCHED + 1))
      log_info "Matched: $name"
    elif [ "$dstat" -eq 1 ]; then
      DIFF_CHANGED=$((DIFF_CHANGED + 1))
      echo "Changed: $name"
      printf '%s\n' "$diff_out"
      echo ""
    else
      log_error "diff failed with exit code $dstat while comparing '$name'"
      exit 2
    fi
  done

  diff_summary_and_exit
}

# =============================================================================
# Main
# =============================================================================

main() {
  if [ $# -eq 0 ]; then
    usage
    exit 1
  fi

  # Offline branch first: never requires curl/jq, never touches the network.
  if [ "$1" = "--diff" ]; then
    shift
    check_diff_dependencies
    run_diff_mode "$@"
  elif [ "$1" = "--all" ]; then
    check_dependencies
    evaluate_all
  else
    check_dependencies
    evaluate_one "$1"
  fi
}

main "$@"
