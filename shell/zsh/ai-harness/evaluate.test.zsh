#!/usr/bin/env zsh

# =============================================================================
# Harness Tests — shell/zsh AI widget Evaluation Harness
#
# Deterministic and fully OFFLINE:
#   - Fixtures are temp copies of the real evaluate.sh under /tmp/opencode
#     (the real harness cases/ and snapshots/ are never mutated).
#   - External tools (curl, jq, diff) are mocked via PATH stub directories,
#     never shell functions, because evaluate.sh runs as a separate Bash process.
# =============================================================================

source "${0:A:h}/test_helper.zsh"

readonly HARNESS_DIR="${0:A:h}"
readonly HARNESS_SCRIPT="${HARNESS_DIR}/evaluate.sh"
readonly NAMESPACE="/tmp/opencode/zsh-ai-diff-tests"
# Invocation-owned fixture root: never the shared namespace itself, so
# concurrent invocations cannot delete each other's fixtures.
readonly FIXTURE_BASE="${NAMESPACE}/run.$$.$RANDOM"

mkdir -p "$FIXTURE_BASE"

typeset -a FIXTURE_ROOTS=()

# ── Helpers ────────────────────────────────────────────────────────────────

run_harness() {
  bash "$HARNESS_SCRIPT" "$@" 2>&1
}

# Create an isolated fixture tree with a copy of the real script.
# Sets REPLY to the fixture root.
make_fixture() {
  local root
  root="${FIXTURE_BASE}/fx.$$.$RANDOM"
  mkdir -p "$root/harness/snapshots" "$root/harness/cases" "$root/stubs"
  cp "$HARNESS_SCRIPT" "$root/harness/evaluate.sh"
  chmod +x "$root/harness/evaluate.sh"
  FIXTURE_ROOTS+=("$root")
  REPLY="$root"
}

# Write a snapshot file: write_snapshot <root> <name> <actual|expected> <content>
write_snapshot() {
  local root="$1" name="$2" side="$3" content="$4"
  printf '%s\n' "$content" > "$root/harness/snapshots/$name.$side.txt"
}

# Write a case file: write_case <root> <filename> <content>
write_case() {
  local root="$1" filename="$2" content="$3"
  printf '%s\n' "$content" > "$root/harness/cases/$filename"
}

# Run the fixture script from an arbitrary cwd (/) with the default PATH.
# Sets LAST_OUT and LAST_CODE.
run_fixture() {
  local root="$1"; shift
  local out code
  out=$( (cd / && "$root/harness/evaluate.sh" "$@") 2>&1 )
  code=$?
  LAST_OUT="$out"
  LAST_CODE="$code"
}

# Run the fixture script with an overridden PATH.
run_fixture_with_path() {
  local path_val="$1" root="$2"; shift 2
  local out code
  out=$( (cd / && PATH="$path_val" "$root/harness/evaluate.sh" "$@") 2>&1 )
  code=$?
  LAST_OUT="$out"
  LAST_CODE="$code"
}

# Executable stub: make_stub <dir> <name> <body...>
make_stub() {
  local dir="$1" name="$2"; shift 2
  {
    echo '#!/bin/bash'
    printf '%s\n' "$@"
  } > "$dir/$name"
  chmod +x "$dir/$name"
}

# Minimal tool dir containing only symlinks to the named real binaries.
make_minimal_dir() {
  local dir="$1"; shift
  mkdir -p "$dir"
  local t real
  for t in "$@"; do
    real=$(command -v "$t")
    [[ -n "$real" ]] && ln -sfn "$real" "$dir/$t"
  done
}

# Digest of every snapshot file (names + bytes) to prove --diff is read-only.
snapshot_digest() {
  local dir="$1"
  local files=("$dir"/*(N))
  if (( ${#files[@]} == 0 )); then
    echo "(no files)"
    return
  fi
  sha256sum -- "${files[@]}"
}

cleanup_fixtures() {
  FIXTURE_ROOTS=()
  # Ownership guard: remove ONLY this invocation's run-scoped directory —
  # never the shared namespace or anything another invocation owns.
  if [[ -n "$FIXTURE_BASE" && "$FIXTURE_BASE" == ${NAMESPACE}/run.* ]]; then
    rm -rf -- "$FIXTURE_BASE"
  fi
}

# ── Kept offline tests (real script, no network reached) ───────────────────

test_script_exists_and_executable() {
  [[ -x "$HARNESS_SCRIPT" ]]
}

test_usage_when_no_args() {
  local out code
  out=$(run_harness 2>&1)
  code=$?
  [[ "$out" == *"Usage"* ]] && [[ $code -eq 1 ]]
}

test_usage_lists_diff_mode() {
  local out
  out=$(run_harness 2>&1)
  [[ "$out" == *"--diff"* ]]
}

test_fails_when_case_not_found() {
  local out code
  out=$(run_harness "cases/no-existe.txt" 2>&1)
  code=$?
  [[ $code -ne 0 ]] && [[ "$out" == *"Not found"* ]]
}

test_fails_when_missing_buffer_section() {
  local root
  make_fixture; root="$REPLY"
  write_case "$root" "no-buffer.txt" '=== Context ===
CWD: /tmp

=== Expectations ===
Should fail'
  run_fixture "$root" "$root/harness/cases/no-buffer.txt"
  [[ $LAST_CODE -ne 0 ]] && [[ "$LAST_OUT" == *"Missing"* && "$LAST_OUT" == *"Buffer"* ]]
}

test_all_mode_no_cases() {
  local root
  make_fixture; root="$REPLY"
  run_fixture "$root" --all
  [[ $LAST_CODE -eq 0 ]] && [[ "$LAST_OUT" == *"No .txt cases"* ]]
}

# Evaluation path still works end-to-end offline: real jq (local binary),
# curl mocked via PATH stub returning a fixed chat-completions-shaped response
# (the shape the real LiteLLM endpoint and .zshrc widget parsing expect).
test_evaluation_offline_with_mocked_curl() {
  local root marker snap
  make_fixture; root="$REPLY"
  marker="$root/stubs/curl-invocations"
  write_case "$root" "sample.txt" '=== Buffer ===
echo hi'
  make_stub "$root/stubs" "curl" \
    "printf '%s\n' '{\"choices\":[{\"message\":{\"content\":\"mocked snapshot content\"}}]}'" \
    "echo invoked >> '$marker'" \
    "exit 0"
  run_fixture_with_path "$root/stubs:$PATH" "$root" "$root/harness/cases/sample.txt"
  snap="$root/harness/snapshots/sample.actual.txt"
  [[ $LAST_CODE -eq 0 ]] \
    && [[ "$LAST_OUT" == *"Snapshot"* ]] \
    && [[ -f "$snap" ]] \
    && [[ "$(cat "$snap")" == "mocked snapshot content" ]] \
    && [[ "$(cat "$marker")" == "invoked" ]]
}

# ── --diff: outcomes and counts ────────────────────────────────────────────

test_diff_equal_snapshots_exit_0() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "same line"
  write_snapshot "$root" "alpha" actual "same line"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 0 ]] \
    && [[ "$LAST_OUT" == *"Matched: 1"* ]] \
    && [[ "$LAST_OUT" == *"Missing: 0"* ]]
}

test_diff_different_snapshots_exit_1_with_unified_diff() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "beta" expected "shared line
baseline only line"
  write_snapshot "$root" "beta" actual "shared line
actual only line"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 1 ]] \
    && [[ "$LAST_OUT" == *"Changed: 1"* ]] \
    && [[ "$LAST_OUT" == *"+actual only line"* ]] \
    && [[ "$LAST_OUT" == *"-baseline only line"* ]]
}

test_diff_missing_baseline_distinguished() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "gamma" actual "some output"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 1 ]] \
    && [[ "$LAST_OUT" == *"Missing baseline: gamma"* ]] \
    && [[ "$LAST_OUT" == *"Missing: 1 (actual: 0, baseline: 1)"* ]]
}

test_diff_missing_actual_distinguished() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "delta" expected "reviewed baseline"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 1 ]] \
    && [[ "$LAST_OUT" == *"Missing actual: delta"* ]] \
    && [[ "$LAST_OUT" == *"Missing: 1 (actual: 1, baseline: 0)"* ]]
}

test_diff_batch_continues_with_exact_counts() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "same"
  write_snapshot "$root" "alpha" actual "same"
  write_snapshot "$root" "beta" expected "keep
drop me"
  write_snapshot "$root" "beta" actual "keep
add me"
  write_snapshot "$root" "gamma" expected "only baseline"
  write_snapshot "$root" "delta" actual "only actual"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 1 ]] \
    && [[ "$LAST_OUT" == *"Matched: 1"* ]] \
    && [[ "$LAST_OUT" == *"Changed: 1"* ]] \
    && [[ "$LAST_OUT" == *"Missing: 2 (actual: 1, baseline: 1)"* ]] \
    && [[ "$LAST_OUT" == *"+add me"* ]] \
    && [[ "$LAST_OUT" == *"-drop me"* ]] \
    && [[ "$LAST_OUT" == *"Missing actual: gamma"* ]] \
    && [[ "$LAST_OUT" == *"Missing baseline: delta"* ]]
}

test_diff_scans_names_with_spaces() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "name with spaces" expected "identical"
  write_snapshot "$root" "name with spaces" actual "identical"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 0 ]] && [[ "$LAST_OUT" == *"Matched: 1"* ]]
}

test_diff_case_arg_maps_spaces_to_hyphens_from_any_cwd() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "my-case" expected "mapped"
  write_snapshot "$root" "my-case" actual "mapped"
  # Relative arg from cwd=/ proves the case file is used for its name only.
  run_fixture "$root" --diff "cases/my case.txt"
  [[ $LAST_CODE -eq 0 ]] && [[ "$LAST_OUT" == *"Matched: 1"* ]]
}

# ── --diff: operational errors (exit 2) ────────────────────────────────────

test_diff_rejects_unknown_option() {
  local root
  make_fixture; root="$REPLY"
  run_fixture "$root" --diff --promote
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"Unknown option"* ]]
}

test_diff_rejects_extra_args() {
  local root
  make_fixture; root="$REPLY"
  run_fixture "$root" --diff a.txt b.txt
  [[ $LAST_CODE -eq 2 ]]
}

test_diff_no_snapshots_exit_2() {
  local root
  make_fixture; root="$REPLY"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"No snapshots"* ]]
}

test_diff_explicit_case_no_snapshots_exit_2() {
  local root
  make_fixture; root="$REPLY"
  run_fixture "$root" --diff "$root/harness/cases/ghost.txt"
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"No snapshots"* ]]
}

test_diff_operational_failure_exit_2() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "beta" expected "a"
  write_snapshot "$root" "beta" actual "b"
  make_stub "$root/stubs" "diff" "exit 2"
  run_fixture_with_path "$root/stubs:$PATH" "$root" --diff
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"diff"* ]]
}

test_diff_missing_diff_binary_exit_2() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "x"
  write_snapshot "$root" "alpha" actual "x"
  # No diff, curl, or jq resolvable in PATH.
  make_minimal_dir "$root/stubs" bash dirname basename sort
  run_fixture_with_path "$root/stubs" "$root" --diff
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"Missing: diff"* ]]
}

# ── --diff: offline guarantees ──────────────────────────────────────────────

test_diff_never_invokes_curl_or_jq() {
  local root marker
  make_fixture; root="$REPLY"
  marker="$root/stubs/invocations"
  write_snapshot "$root" "alpha" expected "same"
  write_snapshot "$root" "alpha" actual "same"
  make_stub "$root/stubs" "curl" "echo curl >> '$marker'" "exit 99"
  make_stub "$root/stubs" "jq" "echo jq >> '$marker'" "exit 99"
  run_fixture_with_path "$root/stubs:$PATH" "$root" --diff
  [[ $LAST_CODE -eq 0 ]] && [[ ! -e "$marker" ]]
}

test_diff_works_without_curl_and_jq_in_path() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "same"
  write_snapshot "$root" "alpha" actual "same"
  make_minimal_dir "$root/stubs" bash dirname basename sort diff mktemp rm
  run_fixture_with_path "$root/stubs" "$root" --diff
  [[ $LAST_CODE -eq 0 ]] && [[ "$LAST_OUT" == *"Matched: 1"* ]]
}

test_diff_leaves_snapshots_unchanged() {
  local root before after
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "same"
  write_snapshot "$root" "alpha" actual "same"
  write_snapshot "$root" "beta" expected "old"
  write_snapshot "$root" "beta" actual "new"
  write_snapshot "$root" "gamma" expected "only baseline"
  write_snapshot "$root" "delta" actual "only actual"
  before=$(snapshot_digest "$root/harness/snapshots")
  run_fixture "$root" --diff
  after=$(snapshot_digest "$root/harness/snapshots")
  [[ $LAST_CODE -eq 1 ]] && [[ "$before" == "$after" ]]
}

# ── Correction round: verifier findings regression coverage ────────────────

# Sort failure during the union listing must be an operational error (2),
# never a silent empty batch that exits 0.
test_diff_sort_failure_exit_2() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "same"
  write_snapshot "$root" "alpha" actual "same"
  make_stub "$root/stubs" "sort" "exit 1"
  run_fixture_with_path "$root/stubs:$PATH" "$root" --diff
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"sort"* ]]
}

# Sort that emits partial output and THEN fails must still exit 2 —
# partial results must never be reported as a complete comparison.
test_diff_sort_partial_output_failure_exit_2() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "same"
  write_snapshot "$root" "alpha" actual "same"
  write_snapshot "$root" "beta" expected "same"
  write_snapshot "$root" "beta" actual "same"
  make_stub "$root/stubs" "sort" "printf 'alpha\\0'" "exit 1"
  run_fixture_with_path "$root/stubs:$PATH" "$root" --diff
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"sort"* ]]
}

# Missing sort binary must exit 2, not silently produce an empty batch.
test_diff_missing_sort_binary_exit_2() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "alpha" expected "same"
  write_snapshot "$root" "alpha" actual "same"
  make_minimal_dir "$root/stubs" bash dirname basename diff
  run_fixture_with_path "$root/stubs" "$root" --diff
  [[ $LAST_CODE -eq 2 ]] && [[ "$LAST_OUT" == *"Missing: sort"* ]]
}

# A case whose NAME legitimately contains ".expected.txt" must keep that name:
# release.expected.txt.actual.txt / release.expected.txt.expected.txt are one
# pair named "release.expected.txt" — not two files collapsing to "release".
test_diff_dotted_suffix_pair_name_preserved() {
  local root
  make_fixture; root="$REPLY"
  write_snapshot "$root" "release.expected.txt" expected "dotted name"
  write_snapshot "$root" "release.expected.txt" actual "dotted name"
  run_fixture "$root" --diff
  [[ $LAST_CODE -eq 0 ]] && [[ "$LAST_OUT" == *"Matched: 1"* ]]
}

# Cleanup must remove ONLY this invocation's fixture root: a sentinel dir
# belonging to another (concurrent) invocation under the shared namespace
# must survive, and our own run-scoped base must be removed.
test_cleanup_removes_only_own_fixture_root() {
  local sentinel own_gone sentinel_alive
  sentinel="$NAMESPACE/sentinel.$$.$RANDOM"
  mkdir -p "$sentinel"
  echo keep > "$sentinel/keep"
  make_fixture
  cleanup_fixtures
  [[ ! -e "$FIXTURE_BASE" ]] && own_gone=yes
  [[ -f "$sentinel/keep" ]] && sentinel_alive=yes
  rm -rf -- "$sentinel"
  [[ "$own_gone" == yes && "$sentinel_alive" == yes ]]
}

# ── Runner ─────────────────────────────────────────────────────────────────

echo "Running harness tests..."

local passed=0; local failed=0

run_test() {
  local name="$1"; shift
  if "$@"; then
    echo "  ✓ $name"
    ((passed++))
  else
    echo "  ✗ $name"
    ((failed++))
  fi
}

run_test "script exists and is executable" test_script_exists_and_executable
run_test "shows usage when no args" test_usage_when_no_args
run_test "usage lists --diff mode" test_usage_lists_diff_mode
run_test "fails when case file not found" test_fails_when_case_not_found
run_test "fails when Buffer section missing" test_fails_when_missing_buffer_section
run_test "--all with no cases exits cleanly" test_all_mode_no_cases
run_test "evaluation completes offline with mocked curl" test_evaluation_offline_with_mocked_curl
run_test "--diff equal snapshots exits 0" test_diff_equal_snapshots_exit_0
run_test "--diff different snapshots exits 1 with unified diff" test_diff_different_snapshots_exit_1_with_unified_diff
run_test "--diff missing baseline is distinguished" test_diff_missing_baseline_distinguished
run_test "--diff missing actual is distinguished" test_diff_missing_actual_distinguished
run_test "--diff batch continues with exact counts" test_diff_batch_continues_with_exact_counts
run_test "--diff scans snapshot names with spaces" test_diff_scans_names_with_spaces
run_test "--diff maps case-name spaces to hyphens from any cwd" test_diff_case_arg_maps_spaces_to_hyphens_from_any_cwd
run_test "--diff rejects unknown option" test_diff_rejects_unknown_option
run_test "--diff rejects extra args" test_diff_rejects_extra_args
run_test "--diff with no snapshots exits 2" test_diff_no_snapshots_exit_2
run_test "--diff explicit case with no snapshots exits 2" test_diff_explicit_case_no_snapshots_exit_2
run_test "--diff operational diff failure exits 2" test_diff_operational_failure_exit_2
run_test "--diff with missing diff binary exits 2" test_diff_missing_diff_binary_exit_2
run_test "--diff never invokes curl or jq" test_diff_never_invokes_curl_or_jq
run_test "--diff works without curl and jq in PATH" test_diff_works_without_curl_and_jq_in_path
run_test "--diff leaves snapshots byte-identical" test_diff_leaves_snapshots_unchanged
run_test "--diff sort failure exits 2" test_diff_sort_failure_exit_2
run_test "--diff sort partial-output failure exits 2" test_diff_sort_partial_output_failure_exit_2
run_test "--diff with missing sort binary exits 2" test_diff_missing_sort_binary_exit_2
run_test "--diff preserves names containing .expected.txt" test_diff_dotted_suffix_pair_name_preserved
run_test "cleanup removes only own fixture root" test_cleanup_removes_only_own_fixture_root

cleanup_fixtures

echo ""
echo "Results: ${passed} passed, ${failed} failed"
[[ $failed -eq 0 ]] || exit 1
