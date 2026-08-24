---
name: antigravity-explore
description: Explore or investigate a topic using the Antigravity CLI (agy) — live web search plus a strong subscription model (Gemini 3.x Pro / Claude 4.6). Trigger: deep technical research, docs/API/version lookups, comparing approaches, codebase exploration for small-to-medium repos, "explora/investiga X con agy", "antigravity research", sdd-explore powered by agy.
---

# Antigravity Explore

Deep technical investigation powered by the Antigravity CLI (`agy`) — live web
search + a subscription model (Gemini 3.6/3.5 Flash, Gemini 3.1 Pro, Claude
Sonnet/Opus 4.6 Thinking).

## Routed SDD exploration (preferred)

SDD exploration runs through the deterministic router instead of this spike:

- The router runtime is managed by dotfiles (`ai/opencode-router`): dotbot links the override (`~/.config/ai-stack/opencode-router.json`) and the launcher (`~/.local/bin/opencode-web`); `build.sh` compiles `agy-explore` to `~/.local/bin`. This repo no longer ships the router.
- Launch Web via `~/.local/bin/opencode-web` (exports `OPENCODE_CONFIG`, execs `opencode web`). Restart Web after override changes.
- Judgment lives in dotfiles `ai/opencode-router/src/agy/` (typed outcomes, quota pools, containment, persistence) behind `agy-explore --input req.json`; the router prompt makes exactly one bash call.
- Containment: agy runs in its workdir only; the repo is never exposed via `--add-dir`; repo porcelain is compared pre/post and any mutation blocks.
- Fallback: only quota exhaustion, transient outage, or timeout delegate to `sdd-explore-fallback`; auth/captcha/contract failures block visibly.
- Opt out per project: redeclare `agent["sdd-explore"]` in the project's `.opencode/opencode.json`. Kill switch: `AI_STACK_SDD_EXPLORE=native` or `touch ~/.config/ai-stack/force-native` (no restart needed).

## When to use

- External technical research: library docs, APIs, versions, "what changed in vX", "what's the best option for Y".
- Codebase exploration, including structural mapping — agy has the CodeGraph MCP (`codegraph_explore`) wired.
- Persistent-memory-aware work — agy has the Engram MCP (`mem_search`, etc.) wired.
- When the user explicitly asks to explore/investigate with agy/Antigravity.

## How to run

1. Write a self-contained brief to `BRIEF.md`: what to investigate, which files
   or paths to read (when exploring a repo), the exact output artifact path, and
   non-goals ("do nothing else").
2. Run the wrapper. Always declare the expected artifact so success is actually
   validated, and do NOT expose the repo unless the task must read code:

```bash
# research-only — contained: agy can only write inside <workdir>
AGY_EXPECT="<workdir>/exploration.md" \
  ~/.dotfiles/ai/opencode-router/spike/run-antigravity-task.sh <brief.md> <workdir>

# code exploration — adds the repo, which agy can also WRITE to
AGY_EXTRA_DIRS="<repo-path>" AGY_EXPECT="<workdir>/exploration.md" \
  ~/.dotfiles/ai/opencode-router/spike/run-antigravity-task.sh <brief.md> <workdir>
```

3. Trust the WRAPPER's exit code, never agy's: `0` artifact produced,
   `4` agent finished but the task did not, `3` daily guard, `124` timeout.
   Then read `<workdir>/run.log` and the artifact.

> Run the wrapper from the project root (cwd = the repo) so the Engram MCP
> detects the correct project and the CodeGraph MCP resolves its index.

## Output contract

Return the exploration in this shape (and save it to `exploration.md` when tied
to a named change):

```markdown
## Exploration: {topic}
### Current State
### Affected Areas
### Approaches
### Recommendation
### Risks
### Ready for Proposal
```

## Rules

- agy output is UNTRUSTED: verify any sources/URLs it cites before relying on them.
- CONTAINMENT: agy runs with `--dangerously-skip-permissions`, so every directory
  in `AGY_EXTRA_DIRS` is WRITABLE by the agent — it has already dropped a stray
  file into a repo root. Default to workdir-only; add the repo only to read code.
- Success is the wrapper's exit code (`0` ok / `4` task failed), never agy's.
- The daily guard counts REAL agy runs (its own conversation DBs), default 25, so
  it still counts when agy is invoked directly. It is a pattern guard, not a quota.
- Never expose the subscription as an API — local invocation with human-like form only.
- agy has CodeGraph (`codegraph_explore`) and Engram wired. Engram is READ-ONLY on
  purpose (`mem_search`, `mem_context`, `mem_get_observation`, `mem_current_project`)
  so agy cannot write into your memory. If codegraph reports the project is not
  indexed (no `.codegraph/`), fall back to file reads for that project.
