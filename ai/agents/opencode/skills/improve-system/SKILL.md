---
name: improve-system
description: "Trigger: improve-system, mejorar mi sistema, auditar mi workflow, feedback loop ia, que hago mal con la ia, analizar mis sesiones, automejora. Unified meta-improvement engine: session coaching, setup audit, experience capture, and skill/CLI tool discovery."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# improve-system

## Activation Contract

Load when the user runs `/improve-system` or asks to improve their system, review their setup, capture an experience/lesson, recap their sessions, audit workflow friction, or discover new skills and tools to build from past session history.

## Hard Rules

- Base all session critiques and tooling proposals on real telemetry from `improve-system --json` (`sessionId`, `project`, turn number, prompt/command quotes). Never invent generic prompting tips.
- Maintain **Dual-Runtime Parity**: audit sessions from both OpenCode and Antigravity (AGY). New skills are created in `~/.dotfiles/ai/agents/opencode/skills/` and symlinked to both `~/.config/opencode/skills/` and `~/.gemini/config/skills/`; CLI tools are symlinked to `~/.local/bin/`.
- Ecosystem boundaries: `projects-tracker` is the **micro-closer** (invoked per session via "archiva y cierra" to record hours in `time-ledger` and update `projects.yaml`). `improve-system` is the **macro-auditor** (invoked 1-2 times weekly to audit multi-session trends, project focus drift, and unpromoted Engram learnings).
- Do NOT modify `gentle-ai` managed files or inject blocking execution gates into `AGENTS.md`. The guardrails are coaching principles inside this skill to guide and help, not block.
- Audit **both sides of the loop**: (1) Operator prompting and workflow friction, and (2) System & Harness opportunities (new skills, CLI tools in `~/.local/bin`, skill `## Gotchas`, dead MCPs).
- Never re-propose any slug listed in `state.dismissedProposals` or `state.appliedProposals`.
- Preview-first: present proposals in a structured table and apply/create them only after explicit user approval.
- Always persist state after runs: use `improve-system --record-run` and record durable lessons in Engram (`mem_save`).

## Decision Gates (4 Modes)

| Mode | Trigger | Action |
| --- | --- | --- |
| **Session & Workflow Review** *(Default)* | Bare `/improve-system`, "recap", "analizar sesiones", bi-weekly cadence | Run `improve-system --json` (or `--days 7` if first run), analyze friction metrics and top operator anti-patterns, surface 2-4 tool/skill candidates, and compare trend. |
| **Setup Review** | "review my setup", "cómo está mi setup", "auditar entorno" | Run `improve-system --json`, audit `~/.config/opencode/` (skills, MCPs in `opencode.json`, project structure), and assess workflow fit. |
| **Experience Capture** | "capture this", "guarda este aprendizaje", user shares a win or lesson | Extract structured lesson (What happened, Key learning, Why it matters) and save directly to Engram via `mem_save` (`discovery` or `pattern`). |
| **System Audit / Cleanup** | "audit", "health check", "qué está obsoleto", "limpiar rot" | Detect unused MCPs, dead/broken skills, and repeated bash scripts in `/tmp` to prune or convert to permanent tools. |

## Execution Steps

1. Detect the mode from user intent (default to **Session & Workflow Review** on bare invocation or scheduled runs).
2. Run `improve-system --json` (adding `--days <N>` or `--project <name>` if specified by the user).
3. Read `references/rubric-and-templates.md` for the evaluation rubric and report templates.
4. If in **Session & Workflow Review**:
   - Compare current metrics against `previousRunMetrics` (Correction Rate, Spirals, Zombie sessions).
   - Audit **Project Focus & Effort**: inspect `projectEffort` (hours from `time-ledger`, dormant active projects in `projects.yaml`, bottleneck streams).
   - Audit **Engram Unpromoted Learnings**: inspect `engramLearnings` (recent discoveries/gotchas needing promotion to skills, gotchas, or tools).
   - Formulate the **Operator Mirror**: 2–3 concrete communication critiques from real sessions, showing exact `Before -> After` prompt rewrites.
   - Formulate the **Skill & Tool Factory**: 2–4 high-impact proposals with unique slugs (new skills, CLI tools in `~/.local/bin/`, or skill Gotchas).
   - Report What's Working Well (subagents, Engram, verification habits).
5. If in **Experience Capture**: extract and call `mem_save(title, content, type: "pattern"|"discovery", topic_key: "...")`.
6. On user approval of proposals:
   - For `New Skill`: scaffold with `skill-creator`, save under `~/.dotfiles/ai/agents/opencode/skills/<name>/`, symlink to `~/.config/opencode/skills/<name>`, and refresh registry.
   - For `CLI Tool`: write executable script under `~/.dotfiles/scripts/` or skill `bin/`, and symlink to `~/.local/bin/<name>`.
   - Run `improve-system --record-run --apply <slug> --dismiss <slug>`.

## Output Contract

Return the structured report defined in `references/rubric-and-templates.md`:
- Metric summary and trend vs. previous run.
- Operator Mirror (concrete prompt critiques + `Before -> After` rewrites).
- Skill & Tool Factory table (`#`, `Slug`, `Tipo`, `Propuesta`, `Evidencia`).
- Harness Health & Next actions.

## References

- `bin/improve-system` — zero-dep Node telemetry CLI (`~/.local/bin/improve-system`).
- `references/rubric-and-templates.md` — coaching rubric, anti-pattern catalogs, and output formats.
