#!/usr/bin/env bash
# Regression tests for `opencode-switch edit`: profile-local model+variant pairs.
#
# Scope (V3): each agent stores {"model", "variant"} INSIDE each profile map;
# the old shared gentle-variants.fragment.json is gone. The same model may use
# different variants per agent and per profile, and profiles never leak
# variants into each other.
#
# Hermetic sandbox:
#   - OPENCODE_SWITCH_MODELS_DIR points at sandbox maps (paths with a space).
#   - OPENCODE_SWITCH_INSTALLER is a fake that only logs invocations; the real
#     installer never runs.
#   - `opencode` on PATH is a fake (working catalogue, or deliberately broken to
#     prove variant-only edits never call it).
#   - HOME is redirected so status rendering cannot read the real user config.
#
# Section 0 checks the REAL repo migration data (read-only): all eight maps
# must hold pairs with the pre-migration model values and the migrated
# variants, and the shared fragment must be gone.
#
# The production script is always executed as a subprocess (never sourced) and
# stdin is a pipe, so every interactive prompt takes the non-fzf `read` path.
#
# Optional env:
#   OC_SWITCH_SCRIPT  script under test (default: sibling production script).
#                     RED baseline: copy the PRE-change script into a stub repo
#                     layout so its (now removed) shared-fragment default
#                     resolves safely inside the sandbox:
#
#                       mkdir -p /tmp/opencode/red-stub/scripts \
#                                /tmp/opencode/red-stub/ai/agents/opencode/settings
#                       cp scripts/opencode-switch-profile.sh /tmp/opencode/red-stub/scripts/
#                       OC_SWITCH_SCRIPT=/tmp/opencode/red-stub/scripts/opencode-switch-profile.sh \
#                         TMPDIR=/tmp/opencode bash scripts/test-opencode-switch-edit-variant.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SWITCH_UNDER_TEST="${OC_SWITCH_SCRIPT:-$SCRIPT_DIR/opencode-switch-profile.sh}"
MODELS_REPO="$REPO_DIR/ai/agents/opencode/models"
FRAGMENT_REPO_PATH="$REPO_DIR/ai/agents/opencode/settings/gentle-variants.fragment.json"

for bin in jq node mktemp; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing dependency: $bin" >&2; exit 2; }
done

PASS=0
FAIL=0
SB=""

ok() { PASS=$((PASS + 1)); echo "  ok   $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL $1"; }
assert_eq() { # label expected actual
  if [ "$2" = "$3" ]; then ok "$1"; else fail "$1: expected [$2] got [$3]"; fi
}
assert_not_zero_rc() {
  if [ "$RUN_RC" -ne 0 ]; then ok "$1"; else fail "$1: expected non-zero exit, got 0"; fi
}
assert_same_bytes() { # label file backup
  if cmp -s "$2" "$3"; then ok "$1"; else fail "$1: $2 changed"; fi
}
assert_contains() { # label file text
  if grep -qF -- "$3" "$2" >/dev/null 2>&1; then ok "$1"; else fail "$1: [$3] not found in $2"; fi
}

# ---------------------------------------------------------------------------
# Section 0: repo migration data (read-only checks against the real maps)
# ---------------------------------------------------------------------------

# Pre-migration snapshot (agent -> model, plus small_model/inherit/$comment)
# captured before the pair migration. The migration must keep every model
# value and all unrelated metadata byte-for-byte equivalent in meaning.
write_migration_expectations() { # dir
  cat > "$1/models.v2.json" <<'EOF'
{"comment":"Perfil Z.ai Max: Prioridad GLM-5.3 para writers, AGY para explore, balance con Claude/OpenCode-Go para review. models.zai.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"opencode/ling-3.1-flash-free","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"zai-coding-plan/glm-5.3-flash","general":"zai-coding-plan/glm-5.3","gentle-ai-explore":"zai-coding-plan/glm-5.3-flash","gentle-ai-verify":"openai/gpt-6.1-sol","gentle-ai-worker":"zai-coding-plan/glm-5.3","gentle-ai-worker-fallback":"anthropic/claude-sonnet-5-5","gentle-orchestrator":"zai-coding-plan/glm-5.3-flash","gentleman-fix":"opencode/ling-3.1-flash-free","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"opencode/ling-3.1-flash-free","jd-fix-agent":"zai-coding-plan/glm-5.3","jd-fix-agent-fallback":"anthropic/claude-sonnet-5-5","jd-judge-a":"anthropic/claude-opus-5-5","jd-judge-a-fallback":"openai/gpt-6.1-sol","jd-judge-b":"openai/gpt-6.1-sol","jd-judge-b-fallback":"opencode-go/qwen3.8-max","judgment-day":"opencode/minimax-m2.5","review-readability":"zai-coding-plan/glm-5.3-flash","review-readability-fallback":"opencode-go/glm-5.3","review-refuter":"openai/gpt-6.1-sol","review-refuter-fallback":"opencode-go/qwen3.8-max","review-reliability":"openai/gpt-6.1-sol","review-reliability-fallback":"opencode-go/qwen3.8-max","review-resilience":"opencode-go/glm-5.3","review-resilience-fallback":"opencode-go/qwen3.8-max","review-risk":"anthropic/claude-opus-5-5","review-risk-fallback":"openai/gpt-6.1-sol","summary":"opencode/muse-spark-1.3-contributor-free","branch-pr-fallback":"zai-coding-plan/glm-4.7","issue-creation-fallback":"zai-coding-plan/glm-4.7","gentleman-judge-fallback":"opencode-go/gpt-6-luna","gentleman-fix-fallback":"zai-coding-plan/glm-4.7","compaction-fallback":"zai-coding-plan/glm-4.7","summary-fallback":"zai-coding-plan/glm-4.7"}}
EOF
  cat > "$1/models.default.json" <<'EOF'
{"comment":"Perfil Z.ai Max: Prioridad GLM-5.3 para writers, AGY para explore, balance con Claude/OpenCode-Go para review. models.zai.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"opencode/ling-3.1-flash-free","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"zai-coding-plan/glm-5.3-flash","general":"zai-coding-plan/glm-5.3","gentle-ai-explore":"zai-coding-plan/glm-5.3-flash","gentle-ai-verify":"openai/gpt-6.1-sol","gentle-ai-worker":"zai-coding-plan/glm-5.3","gentle-ai-worker-fallback":"anthropic/claude-sonnet-5-5","gentle-orchestrator":"zai-coding-plan/glm-5.3-flash","gentleman-fix":"opencode/ling-3.1-flash-free","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"opencode/ling-3.1-flash-free","jd-fix-agent":"zai-coding-plan/glm-5.3","jd-fix-agent-fallback":"anthropic/claude-sonnet-5-5","jd-judge-a":"anthropic/claude-opus-5-5","jd-judge-a-fallback":"openai/gpt-6.1-sol","jd-judge-b":"openai/gpt-6.1-sol","jd-judge-b-fallback":"opencode-go/qwen3.8-max","judgment-day":"opencode/minimax-m2.5","review-readability":"zai-coding-plan/glm-5.3-flash","review-readability-fallback":"opencode-go/glm-5.3","review-refuter":"openai/gpt-6.1-sol","review-refuter-fallback":"opencode-go/qwen3.8-max","review-reliability":"openai/gpt-6.1-sol","review-reliability-fallback":"opencode-go/qwen3.8-max","review-resilience":"opencode-go/glm-5.3","review-resilience-fallback":"opencode-go/qwen3.8-max","review-risk":"anthropic/claude-opus-5-5","review-risk-fallback":"openai/gpt-6.1-sol","summary":"opencode/muse-spark-1.3-contributor-free","branch-pr-fallback":"zai-coding-plan/glm-4.7","issue-creation-fallback":"zai-coding-plan/glm-4.7","gentleman-judge-fallback":"opencode-go/gpt-6-luna","gentleman-fix-fallback":"zai-coding-plan/glm-4.7","compaction-fallback":"zai-coding-plan/glm-4.7","summary-fallback":"zai-coding-plan/glm-4.7"}}
EOF
  cat > "$1/models.zai.json" <<'EOF'
{"comment":"Perfil Z.ai Max: Prioridad GLM-5.3 para writers, AGY para explore, balance con Claude/OpenCode-Go para review. models.zai.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"opencode/ling-3.1-flash-free","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"agy/gemini-3.8-flash","general":"zai-coding-plan/glm-5.3","gentle-ai-explore":"agy/gemini-3.8-flash","gentle-ai-verify":"openai/gpt-6.1-sol","gentle-ai-worker":"zai-coding-plan/glm-5.3","gentle-ai-worker-fallback":"anthropic/claude-sonnet-5-5","gentle-orchestrator":"zai-coding-plan/glm-5.3-flash","gentleman-fix":"opencode/ling-3.1-flash-free","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"opencode/ling-3.1-flash-free","jd-fix-agent":"zai-coding-plan/glm-5.3","jd-fix-agent-fallback":"anthropic/claude-sonnet-5-5","jd-judge-a":"anthropic/claude-opus-5-5","jd-judge-a-fallback":"openai/gpt-6.1-sol","jd-judge-b":"openai/gpt-6.1-sol","jd-judge-b-fallback":"opencode-go/qwen3.8-max","judgment-day":"opencode/minimax-m2.5","review-readability":"agy/gemini-3.8-flash","review-readability-fallback":"opencode-go/glm-5.3","review-refuter":"openai/gpt-6.1-sol","review-refuter-fallback":"opencode-go/qwen3.8-max","review-reliability":"openai/gpt-6.1-sol","review-reliability-fallback":"opencode-go/qwen3.8-max","review-resilience":"opencode-go/glm-5.3","review-resilience-fallback":"opencode-go/qwen3.8-max","review-risk":"anthropic/claude-opus-5-5","review-risk-fallback":"openai/gpt-6.1-sol","summary":"opencode/muse-spark-1.3-contributor-free","branch-pr-fallback":"zai-coding-plan/glm-4.7","issue-creation-fallback":"zai-coding-plan/glm-4.7","gentleman-judge-fallback":"opencode-go/gpt-6-luna","gentleman-fix-fallback":"zai-coding-plan/glm-4.7","compaction-fallback":"zai-coding-plan/glm-4.7","summary-fallback":"zai-coding-plan/glm-4.7"}}
EOF
  cat > "$1/models.claude.json" <<'EOF'
{"comment":"Perfil Claude: Prioridad Anthropic Directa para Worker, Verify y Riesgo. models.claude.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"opencode/ling-3.1-flash-free","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"agy/gemini-3.8-flash","general":"anthropic/claude-sonnet-5-5","gentle-ai-explore":"agy/gemini-3.8-flash","gentle-ai-verify":"anthropic/claude-sonnet-5-5","gentle-ai-worker":"anthropic/claude-sonnet-5-5","gentle-ai-worker-fallback":"openai/gpt-6.1-sol","gentle-orchestrator":"openai/gpt-6.1-sol","gentleman-fix":"opencode/ling-3.1-flash-free","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"opencode/ling-3.1-flash-free","jd-fix-agent":"anthropic/claude-sonnet-5-5","jd-fix-agent-fallback":"openai/gpt-6.1-sol","jd-judge-a":"anthropic/claude-opus-5-5","jd-judge-a-fallback":"openai/gpt-6.1-sol","jd-judge-b":"openai/gpt-6.1-sol","jd-judge-b-fallback":"opencode-go/qwen3.8-max","judgment-day":"opencode/minimax-m2.5","review-readability":"opencode-go/qwen3.8-max","review-readability-fallback":"opencode-go/hy4-preview","review-refuter":"openai/gpt-6.1-sol","review-refuter-fallback":"opencode-go/qwen3.8-max","review-reliability":"openai/gpt-6.1-sol","review-reliability-fallback":"opencode-go/qwen3.8-max","review-resilience":"opencode-go/qwen3.8-max","review-resilience-fallback":"openai/gpt-6.1-sol","review-risk":"anthropic/claude-opus-5-5","review-risk-fallback":"openai/gpt-6.1-sol","summary":"opencode/muse-spark-1.3-contributor-free","branch-pr-fallback":"zai-coding-plan/glm-4.7","issue-creation-fallback":"zai-coding-plan/glm-4.7","gentleman-judge-fallback":"opencode-go/gpt-6-luna","gentleman-fix-fallback":"zai-coding-plan/glm-4.7","compaction-fallback":"zai-coding-plan/glm-4.7","summary-fallback":"zai-coding-plan/glm-4.7"}}
EOF
  cat > "$1/models.openai.json" <<'EOF'
{"comment":"Perfil OpenAI: Prioridad ChatGPT Plus (Sol) para Orquestador, Worker, Verify y Reviewers. models.openai.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"opencode/ling-3.1-flash-free","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"agy/gemini-3.8-flash","general":"openai/gpt-6.1-sol","gentle-ai-explore":"agy/gemini-3.8-flash","gentle-ai-verify":"openai/gpt-6.1-sol","gentle-ai-worker":"openai/gpt-6.1-sol","gentle-ai-worker-fallback":"anthropic/claude-sonnet-5-5","gentle-orchestrator":"openai/gpt-6.1-sol","gentleman-fix":"opencode/ling-3.1-flash-free","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"opencode/ling-3.1-flash-free","jd-fix-agent":"openai/gpt-6.1-sol","jd-fix-agent-fallback":"anthropic/claude-sonnet-5-5","jd-judge-a":"openai/gpt-6.1-sol","jd-judge-a-fallback":"anthropic/claude-opus-5-5","jd-judge-b":"openai/gpt-6.1-sol","jd-judge-b-fallback":"opencode-go/qwen3.8-max","judgment-day":"opencode/minimax-m2.5","review-readability":"openai/gpt-6.1-sol","review-readability-fallback":"opencode-go/qwen3.8-max","review-refuter":"openai/gpt-6.1-sol","review-refuter-fallback":"opencode-go/qwen3.8-max","review-reliability":"openai/gpt-6.1-sol","review-reliability-fallback":"opencode-go/qwen3.8-max","review-resilience":"openai/gpt-6.1-sol","review-resilience-fallback":"opencode-go/qwen3.8-max","review-risk":"openai/gpt-6.1-sol","review-risk-fallback":"anthropic/claude-opus-5-5","summary":"opencode/muse-spark-1.3-contributor-free","branch-pr-fallback":"zai-coding-plan/glm-4.7","issue-creation-fallback":"zai-coding-plan/glm-4.7","gentleman-judge-fallback":"opencode-go/gpt-6-luna","gentleman-fix-fallback":"zai-coding-plan/glm-4.7","compaction-fallback":"zai-coding-plan/glm-4.7","summary-fallback":"zai-coding-plan/glm-4.7"}}
EOF
  cat > "$1/models.opencode-go.json" <<'EOF'
{"comment":"Perfil OpenCode Go: Prioridad Qwen 3.8 Max de OpenCode Go / Zen. models.opencode-go.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"opencode/ling-3.1-flash-free","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"agy/gemini-3.8-flash","general":"opencode-go/qwen3.8-max","gentle-ai-explore":"agy/gemini-3.8-flash","gentle-ai-verify":"opencode-go/qwen3.8-max","gentle-ai-worker":"opencode-go/qwen3.8-max","gentle-ai-worker-fallback":"openai/gpt-6.1-sol","gentle-orchestrator":"openai/gpt-6.1-sol","gentleman-fix":"opencode/ling-3.1-flash-free","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"opencode/ling-3.1-flash-free","jd-fix-agent":"opencode-go/qwen3.8-max","jd-fix-agent-fallback":"openai/gpt-6.1-sol","jd-judge-a":"opencode-go/deepseek-v4-pro","jd-judge-a-fallback":"openai/gpt-6.1-sol","jd-judge-b":"opencode-go/qwen3.8-max","jd-judge-b-fallback":"openai/gpt-6.1-sol","judgment-day":"opencode/minimax-m2.5","review-readability":"opencode-go/qwen3.8-max","review-readability-fallback":"opencode-go/hy4-preview","review-refuter":"opencode-go/qwen3.8-max","review-refuter-fallback":"openai/gpt-6.1-sol","review-reliability":"opencode-go/qwen3.8-max","review-reliability-fallback":"openai/gpt-6.1-sol","review-resilience":"opencode-go/qwen3.8-max","review-resilience-fallback":"openai/gpt-6.1-sol","review-risk":"opencode-go/deepseek-v4-pro","review-risk-fallback":"openai/gpt-6.1-sol","summary":"opencode/muse-spark-1.3-contributor-free","branch-pr-fallback":"zai-coding-plan/glm-4.7","issue-creation-fallback":"zai-coding-plan/glm-4.7","gentleman-judge-fallback":"opencode-go/gpt-6-luna","gentleman-fix-fallback":"zai-coding-plan/glm-4.7","compaction-fallback":"zai-coding-plan/glm-4.7","summary-fallback":"zai-coding-plan/glm-4.7"}}
EOF
  cat > "$1/models.subs.json" <<'EOF'
{"comment":"Perfil Suscripciones Activas: Anthropic Directa + ChatGPT Plus + AGY + OpenCode-Go. models.subs.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"deepseek/deepseek-flash","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"agy/gemini-3.8-flash","general":"anthropic/claude-sonnet-5-5","gentle-ai-explore":"agy/gemini-3.8-flash","gentle-ai-verify":"openai/gpt-6.1-sol","gentle-ai-worker":"anthropic/claude-sonnet-5-5","gentle-ai-worker-fallback":"openai/gpt-6.1-sol","gentle-orchestrator":"openai/gpt-6.1-sol","gentleman-fix":"deepseek/deepseek-flash","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"deepseek/deepseek-flash","jd-fix-agent":"anthropic/claude-sonnet-5-5","jd-fix-agent-fallback":"openai/gpt-6.1-sol","jd-judge-a":"anthropic/claude-opus-5-5","jd-judge-a-fallback":"openai/gpt-6.1-sol","jd-judge-b":"openai/gpt-6.1-sol","jd-judge-b-fallback":"opencode-go/qwen3.8-max","judgment-day":"opencode/minimax-m2.5","review-readability":"opencode-go/qwen3.8-max","review-readability-fallback":"opencode-go/hy4-preview","review-refuter":"openai/gpt-6.1-sol","review-refuter-fallback":"opencode-go/qwen3.8-max","review-reliability":"openai/gpt-6.1-sol","review-reliability-fallback":"opencode-go/qwen3.8-max","review-resilience":"opencode-go/qwen3.8-max","review-resilience-fallback":"openai/gpt-6.1-sol","review-risk":"anthropic/claude-opus-5-5","review-risk-fallback":"openai/gpt-6.1-sol","summary":"opencode/muse-spark-1.3-contributor-free"}}
EOF
  cat > "$1/models.subs-openai.json" <<'EOF'
{"comment":"Perfil Suscripciones Activas sin Claude (Fallback OpenAI Sol): ChatGPT Plus + AGY + OpenCode-Go. models.subs-openai.json","small_model":"opencode/muse-spark-1.3-contributor-free","inherit":["review-validator"],"models":{"branch-pr":"deepseek/deepseek-flash","compaction":"opencode/muse-spark-1.3-contributor-free","explore":"agy/gemini-3.8-flash","general":"openai/gpt-6.1-sol","gentle-ai-explore":"agy/gemini-3.8-flash","gentle-ai-verify":"openai/gpt-6.1-sol","gentle-ai-worker":"openai/gpt-6.1-sol","gentle-ai-worker-fallback":"anthropic/claude-sonnet-5-5","gentle-orchestrator":"openai/gpt-6.1-sol","gentleman-fix":"deepseek/deepseek-flash","gentleman-judge":"opencode/nemotron-3-ultra-free","issue-creation":"deepseek/deepseek-flash","jd-fix-agent":"anthropic/claude-sonnet-5-5","jd-fix-agent-fallback":"openai/gpt-6.1-sol","jd-judge-a":"anthropic/claude-opus-5-5","jd-judge-a-fallback":"openai/gpt-6.1-sol","jd-judge-b":"openai/gpt-6.1-sol","jd-judge-b-fallback":"opencode-go/qwen3.8-max","judgment-day":"opencode/minimax-m2.5","review-readability":"opencode-go/qwen3.8-max","review-readability-fallback":"opencode-go/hy4-preview","review-refuter":"openai/gpt-6.1-sol","review-refuter-fallback":"opencode-go/qwen3.8-max","review-reliability":"openai/gpt-6.1-sol","review-reliability-fallback":"opencode-go/qwen3.8-max","review-resilience":"opencode-go/qwen3.8-max","review-resilience-fallback":"openai/gpt-6.1-sol","review-risk":"anthropic/claude-opus-5-5","review-risk-fallback":"openai/gpt-6.1-sol","summary":"opencode/muse-spark-1.3-contributor-free"}}
EOF
}

test_migration_data() {
  echo "migration: 8 maps hold profile-local pairs; models intact; shared fragment removed"
  local mig
  mig="$(mktemp -d "${TMPDIR:-/tmp}/ocswitch-mig.XXXXXX")"
  write_migration_expectations "$mig"
  # Variants carried from the removed shared gentle-variants.fragment.json;
  # every other agent starts at the explicit default "".
  local frag_variants='{"general":"max","gentle-orchestrator":"","gentle-ai-explore":"high","gentle-ai-worker":"max","gentle-ai-worker-fallback":"high","gentle-ai-verify":"max","jd-fix-agent":"max","jd-fix-agent-fallback":"high"}'
  local map file exp shape
  for map in v2 default zai claude openai opencode-go subs subs-openai; do
    file="$MODELS_REPO/models.$map.json"
    exp="$mig/models.$map.json"
    if [ ! -f "$file" ]; then fail "models.$map.json exists"; continue; fi
    shape="$(jq -r '[.agents | to_entries[] |
      ((.value | type == "object")
       and (.value.model | type == "string" and test("^[^/]+/.+"))
       and (.value.variant | type == "string"))] | all' "$file")"
    assert_eq "models.$map.json: every entry is a {model, variant} pair (no legacy strings left)" "true" "$shape"
    if jq -e --slurpfile e "$exp" '
        ."$comment" == $e[0].comment
        and .small_model == $e[0].small_model
        and .inherit == $e[0].inherit
        and ((.agents | to_entries | map({(.key): .value.model}) | add) == $e[0].models)
      ' "$file" >/dev/null 2>&1; then
      ok "models.$map.json: models, small_model, inherit and metadata intact"
    else
      fail "models.$map.json: model values or metadata drifted from the pre-migration snapshot"
    fi
    if jq -e --argjson frag "$frag_variants" '
        [.agents | to_entries[] | .value.variant == ($frag[.key] // "")] | all
      ' "$file" >/dev/null 2>&1; then
      ok "models.$map.json: migrated variants (shared-fragment agents kept theirs, others default)"
    else
      fail "models.$map.json: variants do not match the migration expectation"
    fi
  done
  if [ ! -e "$FRAGMENT_REPO_PATH" ]; then
    ok "shared gentle-variants.fragment.json removed after migration"
  else
    fail "gentle-variants.fragment.json still exists (should have been removed)"
  fi
  case "$mig" in
    "${TMPDIR:-/tmp}"/ocswitch-mig.*) rm -rf "$mig" ;;
  esac
}

# ---------------------------------------------------------------------------
# Section 1+: editor behavior in a hermetic sandbox
# ---------------------------------------------------------------------------

make_sandbox() {
  SB="$(mktemp -d "${TMPDIR:-/tmp}/ocswitch-edit-test.XXXXXX")"
  MODELS_SB="$SB/models dir"
  mkdir -p "$MODELS_SB" "$SB/home/.config/opencode" "$SB/bin-ok" "$SB/bin-broken"

  # Active map: worker/helper share the same model with different variants;
  # legacy is a legacy string entry (variant defaults to "").
  cat > "$MODELS_SB/models.v2.json" <<'EOF'
{
  "small_model": "fake/small",
  "agents": {
    "worker": {"model": "fake/model-a", "variant": "high"},
    "helper": {"model": "fake/model-a", "variant": ""},
    "legacy": "fake/model-b"
  },
  "inherit": ["ghost-inherit"]
}
EOF
  # Inactive profile: independent variants for the same agents.
  cat > "$MODELS_SB/models.profx.json" <<'EOF'
{
  "small_model": "fake/small",
  "agents": {
    "worker": {"model": "fake/model-a", "variant": "max"},
    "helper": {"model": "fake/model-b", "variant": "low"},
    "legacy": "fake/model-b"
  },
  "inherit": []
}
EOF
  cat > "$SB/home/.config/opencode/opencode.json" <<'EOF'
{"agent": {"worker": {"model": "fake/model-a"}}}
EOF

  cat > "$SB/installer-fake.sh" <<'EOF'
#!/usr/bin/env bash
echo run >> "$OC_INSTALLER_LOG"
EOF
  chmod +x "$SB/installer-fake.sh"

  cat > "$SB/bin-ok/opencode" <<'EOF'
#!/usr/bin/env bash
echo call >> "$OC_MARK"
printf 'fake/model-a\nfake/model-b\n'
EOF
  cat > "$SB/bin-broken/opencode" <<'EOF'
#!/usr/bin/env bash
echo call >> "$OC_MARK"
echo "opencode: simulated catalogue failure" >&2
exit 1
EOF
  chmod +x "$SB/bin-ok/opencode" "$SB/bin-broken/opencode"

  : > "$SB/opencode.marker"
  : > "$SB/installer.log"

  cp "$MODELS_SB/models.v2.json" "$SB/active.bak"
  cp "$MODELS_SB/models.profx.json" "$SB/profx.bak"
}

cleanup_sandbox() {
  case "$SB" in
    "${TMPDIR:-/tmp}"/ocswitch-edit-test.*) rm -rf "$SB" ;;
  esac
}

run_switch() { # profile bindir input(with \n escapes)
  RUN_RC=0
  printf '%b' "$3" | env \
    HOME="$SB/home" \
    PATH="$SB/$2:$PATH" \
    OC_MARK="$SB/opencode.marker" \
    OC_INSTALLER_LOG="$SB/installer.log" \
    OPENCODE_SWITCH_MODELS_DIR="$MODELS_SB" \
    OPENCODE_SWITCH_INSTALLER="$SB/installer-fake.sh" \
    bash "$SWITCH_UNDER_TEST" edit "$1" > "$SB/out.log" 2> "$SB/err.log" || RUN_RC=$?
}

marker_calls() { wc -l < "$SB/opencode.marker" | tr -d ' '; }
installer_runs() { wc -l < "$SB/installer.log" | tr -d ' '; }

agent_kind() { # agent file -> pair|legacy|missing
  jq -r --arg a "$1" 'if (.agents[$a] | type) == "object" then "pair"
                      elif (.agents[$a] | type) == "string" then "legacy"
                      else "missing" end' "$MODELS_SB/$2"
}
agent_model() { # agent file
  jq -r --arg a "$1" 'if (.agents[$a] | type) == "object" then .agents[$a].model
                      else .agents[$a] end' "$MODELS_SB/$2"
}
agent_variant() { # agent file (LEGACY for string entries, NULL for missing variant key)
  jq -r --arg a "$1" 'if (.agents[$a] | type) == "object" then (.agents[$a].variant // "NULL")
                      else "LEGACY" end' "$MODELS_SB/$2"
}

assert_no_writes() {
  assert_same_bytes "active map untouched" "$MODELS_SB/models.v2.json" "$SB/active.bak"
  assert_same_bytes "profx profile untouched" "$MODELS_SB/models.profx.json" "$SB/profx.bak"
}
assert_installer_never() {
  assert_eq "installer never invoked" 0 "$(installer_runs)"
}

test_agent_list_shows_model_and_variant() {
  echo "agent list renders model AND variant for pairs and legacy entries"
  make_sandbox
  run_switch active bin-broken '\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_contains "list shows pair model" "$SB/out.log" "fake/model-a"
  assert_contains "list shows pair variant" "$SB/out.log" "high"
  assert_contains "list marks legacy entries" "$SB/out.log" "legacy"
  assert_no_writes
  assert_installer_never
  cleanup_sandbox
}

test_variant_only_active() {
  echo "variant-only on active profile: writes the profile pair, applies once, never queries models"
  make_sandbox
  run_switch active bin-broken 'worker\n2\nmax\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "worker stays a pair" "pair" "$(agent_kind worker models.v2.json)"
  assert_eq "worker model unchanged" "fake/model-a" "$(agent_model worker models.v2.json)"
  assert_eq "worker variant updated in profile" "max" "$(agent_variant worker models.v2.json)"
  assert_same_bytes "profx profile untouched" "$MODELS_SB/models.profx.json" "$SB/profx.bak"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  assert_contains "variant prompt shows selected model" "$SB/out.log" "fake/model-a"
  cleanup_sandbox
}

test_variant_only_inactive_declined() {
  echo "variant-only on inactive profile + decline: profile saved, no installer, active untouched"
  make_sandbox
  run_switch profx bin-broken 'worker\n2\nmedium\nn\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "profx worker variant" "medium" "$(agent_variant worker models.profx.json)"
  assert_same_bytes "active map untouched (declined)" "$MODELS_SB/models.v2.json" "$SB/active.bak"
  assert_eq "installer never invoked" 0 "$(installer_runs)"
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  cleanup_sandbox
}

test_variant_only_inactive_accepted() {
  echo "variant-only on inactive profile + accept: whole profile applied once"
  make_sandbox
  run_switch profx bin-broken 'worker\n2\nmedium\ns\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "profx worker variant" "medium" "$(agent_variant worker models.profx.json)"
  assert_eq "active gets profx worker pair (variant medium)" "medium" "$(agent_variant worker models.v2.json)"
  assert_eq "active gets profx helper variant (profile B independent)" "low" "$(agent_variant helper models.v2.json)"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  cleanup_sandbox
}

test_variant_default_clears() {
  echo "variant 'default' stores an empty string in the profile pair"
  make_sandbox
  run_switch active bin-broken 'worker\n2\ndefault\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "worker variant cleared to empty string" "" "$(agent_variant worker models.v2.json)"
  assert_eq "worker model preserved" "fake/model-a" "$(agent_model worker models.v2.json)"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  cleanup_sandbox
}

test_variant_keep_is_noop() {
  echo "variant 'keep': no writes, no installer"
  make_sandbox
  run_switch active bin-broken 'worker\n2\nkeep\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  cleanup_sandbox
}

test_independent_same_model_different_variants() {
  echo "same model, different variants per agent stay independent"
  make_sandbox
  run_switch active bin-broken 'helper\n2\nlow\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "helper variant updated" "low" "$(agent_variant helper models.v2.json)"
  assert_eq "helper model unchanged" "fake/model-a" "$(agent_model helper models.v2.json)"
  assert_eq "worker (same model) keeps its own variant" "high" "$(agent_variant worker models.v2.json)"
  cleanup_sandbox
}

test_model_only_active_resets_variant() {
  echo "model-only change on active profile resets the variant to default"
  make_sandbox
  run_switch active bin-ok 'worker\n1\nfake/model-b\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "worker model updated" "fake/model-b" "$(agent_model worker models.v2.json)"
  assert_eq "worker variant reset to empty" "" "$(agent_variant worker models.v2.json)"
  assert_contains "reset is communicated" "$SB/out.log" "resetea"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  assert_eq "opencode models called once" 1 "$(marker_calls)"
  cleanup_sandbox
}

test_model_same_is_noop() {
  echo "model-only with the same model: no-op, variant preserved"
  make_sandbox
  run_switch active bin-ok 'worker\n1\nfake/model-a\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_eq "worker variant still high (preserved)" "high" "$(agent_variant worker models.v2.json)"
  cleanup_sandbox
}

test_invalid_model_shape_rejected() {
  echo "typed model without provider/id shape is rejected before any write"
  make_sandbox
  run_switch active bin-ok 'worker\n1\nnoSlash\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_contains "shape rejection is communicated" "$SB/out.log" "forma no válida"
  assert_eq "catalogue consulted exactly once" 1 "$(marker_calls)"
  cleanup_sandbox
}

test_unknown_catalogue_model_rejected() {
  echo "well-shaped model absent from the catalogue is rejected before any write"
  make_sandbox
  run_switch active bin-ok 'worker\n1\nfake/model-z\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_contains "catalogue rejection is communicated" "$SB/out.log" "no encontrado en el catálogo"
  assert_eq "catalogue consulted exactly once" 1 "$(marker_calls)"
  cleanup_sandbox
}

test_combined_invalid_model_keeps_variant() {
  echo "combined edit with invalid model: nothing written, variant also unchanged"
  make_sandbox
  run_switch active bin-ok 'worker\n3\nnoSlash\nlow\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_eq "worker variant still high (untouched)" "high" "$(agent_variant worker models.v2.json)"
  assert_eq "worker model still fake/model-a" "fake/model-a" "$(agent_model worker models.v2.json)"
  cleanup_sandbox
}

test_inactive_invalid_model_no_apply() {
  echo "inactive profile + invalid model: rejected before write/apply; active never copied"
  make_sandbox
  run_switch profx bin-ok 'worker\n1\nfake/model-z\ns\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_eq "catalogue consulted exactly once" 1 "$(marker_calls)"
  cleanup_sandbox
}

test_model_edit_without_catalogue_cancels() {
  echo "unavailable catalogue: model cannot be verified, cancel before any write"
  make_sandbox
  run_switch active bin-broken 'worker\n1\nfake/model-b\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_contains "unavailable-catalogue cancel is communicated" "$SB/out.log" "No se pudo cargar el catálogo"
  assert_eq "catalogue consulted exactly once" 1 "$(marker_calls)"
  cleanup_sandbox
}

test_model_only_inactive_decline() {
  echo "model-only on inactive profile + decline: profile saved with reset variant, no installer"
  make_sandbox
  run_switch profx bin-ok 'worker\n1\nfake/model-b\nn\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "profx worker model" "fake/model-b" "$(agent_model worker models.profx.json)"
  assert_eq "profx worker variant reset" "" "$(agent_variant worker models.profx.json)"
  assert_same_bytes "active map untouched" "$MODELS_SB/models.v2.json" "$SB/active.bak"
  assert_installer_never
  cleanup_sandbox
}

test_model_only_inactive_accept() {
  echo "model-only on inactive profile + accept: existing apply flow intact"
  make_sandbox
  run_switch profx bin-ok 'worker\n1\nfake/model-b\ns\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "profx worker model" "fake/model-b" "$(agent_model worker models.profx.json)"
  assert_eq "active map model after apply" "fake/model-b" "$(agent_model worker models.v2.json)"
  assert_eq "active map variant after apply (reset carried)" "" "$(agent_variant worker models.v2.json)"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  assert_eq "opencode models called once" 1 "$(marker_calls)"
  cleanup_sandbox
}

test_combined_same_model_keep_is_noop() {
  echo "combined edit, same model + keep: true no-op"
  make_sandbox
  run_switch active bin-ok 'worker\n3\nfake/model-a\nkeep\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_eq "worker variant still high" "high" "$(agent_variant worker models.v2.json)"
  cleanup_sandbox
}

test_combined_model_change_keep_resets() {
  echo "combined edit, model change + keep: variant resets to default and says so"
  make_sandbox
  run_switch active bin-ok 'worker\n3\nfake/model-b\nkeep\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "worker model updated" "fake/model-b" "$(agent_model worker models.v2.json)"
  assert_eq "worker variant reset (keep cannot reuse old model's variant)" "" "$(agent_variant worker models.v2.json)"
  assert_contains "keep-with-model-change reset is communicated" "$SB/out.log" "default"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  cleanup_sandbox
}

test_combined_values() {
  echo "combined edit with explicit values: pair written once"
  make_sandbox
  run_switch active bin-ok 'worker\n3\nfake/model-b\nlow\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "worker model updated" "fake/model-b" "$(agent_model worker models.v2.json)"
  assert_eq "worker variant updated" "low" "$(agent_variant worker models.v2.json)"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  cleanup_sandbox
}

test_legacy_variant_edit() {
  echo "legacy string entry + variant edit: becomes a pair keeping the model"
  make_sandbox
  run_switch active bin-broken 'legacy\n2\nhigh\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "legacy entry converted to pair" "pair" "$(agent_kind legacy models.v2.json)"
  assert_eq "legacy model preserved" "fake/model-b" "$(agent_model legacy models.v2.json)"
  assert_eq "legacy variant written" "high" "$(agent_variant legacy models.v2.json)"
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  assert_eq "installer ran exactly once" 1 "$(installer_runs)"
  cleanup_sandbox
}

test_legacy_model_edit_resets() {
  echo "legacy string entry + model edit: becomes a pair with default variant"
  make_sandbox
  run_switch active bin-ok 'legacy\n1\nfake/model-a\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "legacy entry converted to pair" "pair" "$(agent_kind legacy models.v2.json)"
  assert_eq "legacy model updated" "fake/model-a" "$(agent_model legacy models.v2.json)"
  assert_eq "legacy variant is default" "" "$(agent_variant legacy models.v2.json)"
  cleanup_sandbox
}

test_legacy_same_model_is_noop() {
  echo "legacy string entry + same model: byte-identical no-op"
  make_sandbox
  run_switch active bin-ok 'legacy\n1\nfake/model-b\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  cleanup_sandbox
}

test_sibling_metadata_preserved_on_write() {
  echo "writing a pair preserves small_model, inherit and sibling agents"
  make_sandbox
  run_switch active bin-broken 'worker\n2\nmax\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_eq "small_model preserved" "fake/small" "$(jq -r '.small_model' "$MODELS_SB/models.v2.json")"
  assert_eq "inherit preserved" "ghost-inherit" "$(jq -r '.inherit[0]' "$MODELS_SB/models.v2.json")"
  assert_eq "sibling helper untouched" "" "$(agent_variant helper models.v2.json)"
  assert_eq "sibling legacy stays legacy" "legacy" "$(agent_kind legacy models.v2.json)"
  cleanup_sandbox
}

test_cancel_at_variant_prompt() {
  echo "empty input at variant prompt cancels without writes"
  make_sandbox
  run_switch active bin-broken 'worker\n2\n\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  cleanup_sandbox
}

test_invalid_variant_value() {
  echo "unknown variant value is rejected before any write"
  make_sandbox
  run_switch active bin-broken 'worker\n2\nultra\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  cleanup_sandbox
}

test_invalid_action() {
  echo "invalid editing action cancels without writes"
  make_sandbox
  run_switch active bin-broken 'worker\n9\n'
  assert_eq "exit code" 0 "$RUN_RC"
  assert_no_writes
  assert_installer_never
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  cleanup_sandbox
}

test_unknown_agent() {
  echo "unknown agent name fails before catalogue load and never writes"
  make_sandbox
  run_switch active bin-ok 'ghost\n'
  assert_not_zero_rc "exit code"
  assert_no_writes
  assert_installer_never
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  cleanup_sandbox
}

test_agent_constructor_rejected() {
  echo "prototype-chain key 'constructor' is rejected as an agent (own-property check)"
  make_sandbox
  run_switch active bin-broken 'constructor\n2\nmax\n'
  assert_not_zero_rc "exit code"
  assert_no_writes
  assert_installer_never
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  cleanup_sandbox
}

test_agent_tostring_rejected() {
  echo "prototype-chain key 'toString' is rejected as an agent (own-property check)"
  make_sandbox
  run_switch active bin-broken 'toString\n2\nmax\n'
  assert_not_zero_rc "exit code"
  assert_no_writes
  assert_installer_never
  assert_eq "opencode models never called" 0 "$(marker_calls)"
  cleanup_sandbox
}

main() {
  # Pseudo-tty (python3 pty / script -qec) fzf coverage was evaluated and is
  # intentionally SKIPPED: staged fzf interaction never stabilized and an
  # unstable interaction test would hang or flake the deterministic suite.
  # The fzf branches remain structurally identical to the exercised `read`
  # paths (same prompts, same validation, same writes).
  echo "SKIP pseudo-tty fzf flow (unstable to automate deterministically; read-path covered below)"
  test_migration_data
  test_agent_list_shows_model_and_variant
  test_variant_only_active
  test_variant_only_inactive_declined
  test_variant_only_inactive_accepted
  test_variant_default_clears
  test_variant_keep_is_noop
  test_independent_same_model_different_variants
  test_model_only_active_resets_variant
  test_model_same_is_noop
  test_invalid_model_shape_rejected
  test_unknown_catalogue_model_rejected
  test_combined_invalid_model_keeps_variant
  test_inactive_invalid_model_no_apply
  test_model_edit_without_catalogue_cancels
  test_model_only_inactive_decline
  test_model_only_inactive_accept
  test_combined_same_model_keep_is_noop
  test_combined_model_change_keep_resets
  test_combined_values
  test_legacy_variant_edit
  test_legacy_model_edit_resets
  test_legacy_same_model_is_noop
  test_sibling_metadata_preserved_on_write
  test_cancel_at_variant_prompt
  test_invalid_variant_value
  test_invalid_action
  test_unknown_agent
  test_agent_constructor_rejected
  test_agent_tostring_rejected

  echo
  echo "passed=$PASS failed=$FAIL"
  if [ "$FAIL" -ne 0 ]; then
    echo "RESULT: RED (failures above)"
    exit 1
  fi
  echo "RESULT: GREEN"
}

main
