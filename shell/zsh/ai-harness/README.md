# Alt+G Evaluation Harness

Lightweight pure-text evaluation system for the AI terminal widgets defined in
`shell/zsh/.zshrc` (ghost text on Ctrl+G, explain on Alt+E, heal on Alt+F —
the user-facing shortcut family referred to as "alt+g"). It mirrors the real
widget contract: same endpoint (`ZSH_AI_URL`), same model (`ZSH_AI_MODEL`),
same Bearer auth (`ZSH_AI_KEY`), same chat payload shape, and
`.choices[0].message.content` parsing. Test prompt changes and model switches
with git-diffable snapshots.

## Directory Structure

```
shell/zsh/ai-harness/
├── evaluate.sh         # Runner (Bash, defensive patterns)
├── evaluate.test.zsh   # Offline deterministic tests (temp fixtures + PATH mocks)
├── test_helper.zsh     # Test utilities (self-contained copy)
├── cases/              # Test case files (.txt)
├── snapshots/          # *.actual.txt (gitignored) + *.expected.txt baselines
└── README.md
```

## Quick Start

### 1. Create a test case

```bash
cat > harness/cases/my-case.txt << 'EOF'
=== Context ===
Directory: /home/bruno/Code/personal/hungry/compare-prices
Git branch: feature/auth

=== Buffer ===
como corro el scraper de eroski?

=== Expectations ===
Should suggest bun run or make command
EOF
```

### 2. Run evaluation

```bash
# Single case
./harness/evaluate.sh cases/my-case.txt

# All cases
./harness/evaluate.sh --all
```

### 3. Review changes

```bash
git diff harness/snapshots/
```

## Offline Baseline Comparison (`--diff`)

`--diff` compares existing snapshots against baselines WITHOUT any network access,
any LiteLLM dependency, or any dependency on `curl`/`jq`. It only requires `diff`,
and it never creates, modifies, or deletes snapshot files.

```bash
# Compare every snapshot (union of *.actual.txt and *.expected.txt names)
./harness/evaluate.sh --diff

# Compare one case (same space-to-hyphen name mapping as evaluation;
# the case file is used for its name only, so any cwd works)
./harness/evaluate.sh --diff cases/sample.txt
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All compared snapshots are equal to their baselines |
| `1` | At least one change or missing side was found |
| `2` | Operational error: invalid CLI, no snapshots at all, `diff` missing or failed |

Missing sides are reported distinctly: `Missing actual` (baseline exists, run an
evaluation first) vs `Missing baseline` (actual exists, no reviewed baseline yet).

### Baseline workflow (explicit, no promote flag)

`*.actual.txt` files are gitignored (non-deterministic LLM output). To establish a
reviewed baseline, a human reads the actual snapshot and then copies it explicitly:

```bash
# 1. Review the actual output
less harness/snapshots/sample.actual.txt

# 2. After human approval, establish the baseline
cp harness/snapshots/sample.actual.txt harness/snapshots/sample.expected.txt
```

`*.expected.txt` baselines are NOT gitignored, so reviewed baselines are trackable
in git while actual outputs stay local. There is intentionally no `--promote` flag:
baseline promotion is a human decision made with an explicit `cp`.

## Test Case Format

- `=== Context ===` — Environment metadata (directory, git branch, etc.)
- `=== Buffer ===` — **Required.** Terminal input buffer.
- `=== Expectations ===` — Optional. What you expect the AI to produce (for human review).

## Requirements

- Evaluation (`<case_file>` / `--all`): `curl`, `jq` — checked at runtime — plus
  the widget's LiteLLM endpoint from `ZSH_AI_URL`
  (default `http://127.0.0.1:4000/v1/chat/completions`)
- `--diff` (offline): `diff` always; the all-snapshots listing additionally uses
  coreutils `sort` + `mktemp` (all checked — failures exit 2). Never curl, jq,
  or LiteLLM.

## Environment Variables

Same variables the widgets in `shell/zsh/.zshrc` use — no extra configuration:

| Variable | Default | Description |
|----------|---------|-------------|
| `ZSH_AI_URL` | `http://127.0.0.1:4000/v1/chat/completions` | LiteLLM endpoint |
| `ZSH_AI_MODEL` | `qwen-3.6-27b` | Model name |
| `ZSH_AI_KEY` | _(empty — no auth header)_ | Bearer token (`LITELLM_MASTER_KEY` in `.zshrc`) |
| `ZSH_AI_TIMEOUT` | `10` | Request timeout in seconds |
