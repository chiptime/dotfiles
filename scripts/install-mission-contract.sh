#!/usr/bin/env bash
# Upsert the shared Mission Contract into every harness's global instructions
# file as a marker block, and map the /mission command. Idempotent: re-running
# replaces the block in place. Generated blocks from other tools (gentle-ai,
# collie) are left untouched because only our markers are rewritten.
set -euo pipefail

DOTFILES="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
CONTRACT="$DOTFILES/ai/agents/shared/mission-contract.md"
COMMAND="$DOTFILES/ai/agents/opencode/commands/mission.md"
BEGIN='<!-- dotfiles:mission-contract begin -->'
END='<!-- dotfiles:mission-contract end -->'

TARGETS=(
  "$HOME/.config/opencode/AGENTS.md"
  "$HOME/.claude/CLAUDE.md"
  "$HOME/.codex/AGENTS.md"
  "$HOME/.pi/agent/AGENTS.md"
  "$HOME/.gemini/GEMINI.md"
)

[[ -f "$CONTRACT" ]] || { echo "missing $CONTRACT" >&2; exit 1; }

upsert() {
  local file="$1"
  python3 - "$file" "$CONTRACT" "$BEGIN" "$END" <<'PY'
import re, sys
path, contract, begin, end = sys.argv[1:5]
text = open(path, encoding="utf-8").read()
body = open(contract, encoding="utf-8").read().rstrip("\n")
block = f"{begin}\n{body}\n{end}"
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
if pattern.search(text):
    new = pattern.sub(lambda _: block, text, count=1)
else:
    new = text.rstrip("\n") + "\n\n" + block + "\n"
if new != text:
    open(path, "w", encoding="utf-8").write(new)
    print(f"updated {path}")
else:
    print(f"unchanged {path}")
PY
}

for target in "${TARGETS[@]}"; do
  if [[ -f "$target" ]]; then
    upsert "$target"
  else
    echo "skip (not installed) $target"
  fi
done

# /mission command for OpenCode and Claude Code.
for dir in "$HOME/.config/opencode/commands" "$HOME/.claude/commands"; do
  parent="$(dirname "$dir")"
  [[ -d "$parent" ]] || continue
  mkdir -p "$dir"
  ln -sfn "$COMMAND" "$dir/mission.md"
  echo "linked $dir/mission.md"
done
