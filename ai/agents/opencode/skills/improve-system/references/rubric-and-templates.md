# improve-system — Evaluation Rubric & Output Templates

## 1. Operator Coaching Rubric (Human Feedback Loop)

Inspect `operatorSignals` and `automationCandidates.workflowsByProject` in the `improve-system --json` payload. Never give generic advice; always cite the `sessionId`, `project`, turn number, and exact prompt excerpt.

| Anti-Pattern | Signal in JSON | Root Cause & Coaching Prescription |
| --- | --- | --- |
| **1. Under-Specified Framing / Hidden Topology** | `correctionSpirals` or `telegraphicStarts` where turn 2–8 contains `"ojo, para que me entiendas"`, `"no me estoy refiriendo a"`, `"además te he dicho"` | The operator started with a symptom or vague request without stating the branch topology, target environment, or negative constraints. **Fix**: Show a `Before -> After` rewrite of Turn 1 that front-loads topology + target + constraints. |
| **2. Hidden Manual State Mutation** | Corrections mentioning `"fui yo quien modificó"`, local DB/env overrides, or unshared test data changes mid-session | The operator mutated state outside the session (DB rows, SAP SF dates, env files) and only revealed it after several debugging turns. **Fix**: State test fixtures and manual mutations in Turn 1, or automate fixture inspection via a CLI tool. |
| **3. Marathon / Multi-Phase Zombie Sessions** | `zombieSessions` with `userTurns >= 15` or spanning multiple days (`durationMin > 300`) mixing ideation, implementation, diagramming, and merges | Mixing 3+ distinct cognitive phases in one session degrades attention and wastes cache/tokens. **Fix**: Split at phase boundaries (1 session for `/grill-me` or roadmap, 1 for subagent implementation, 1 for review/merge). |
| **4. Unplanned Heavy Execution** | `unplannedHeavySessions` with `>= 6` turns and multiple corrections, without `plan`, `grill-me`, or `prd` | Jumping straight to implementation when requirements or edge cases are still fluid. **Fix**: Trigger `/grill-me` or `plan` mode before writing code when >3 files or domain edge cases are involved. |
| **5. Manual QA / Raw Log Relay** | `rawLogPastes` > 0 | The operator is acting as a human copy-paste runner between terminal/browser and chat. **Fix**: Give the agent a deterministic verification command (or build a helper script) so it self-verifies before yielding. |
| **6. Harness Underuse** | `missedSkillOpportunities` or manual prose describing branch merges, PR chains, defect loops, or timesheets | Explaining a workflow manually when an installed skill (`branch-audit`, `chained-pr`, `rdd-defect-workflow`, `judgment-day`, `daily-ai-timesheet`) already codifies it. |

---

## 2. Skill & Tool Factory Rubric (What to Build)

Cross-reference `automationCandidates.workflowBashClusters`, `agentFallbackClusters`, `workflowsByProject`, and `engramLearnings.highlights`. Filter out any slug already listed in `state.dismissedProposals` or `state.appliedProposals`.

### A. When to Propose a New CLI Tool (`~/.local/bin/<name>`)
Propose a zero-dependency CLI script (stored in `~/.dotfiles/ai/agents/opencode/skills/<skill>/bin/<name>` or `~/.dotfiles/scripts/` and symlinked to `~/.local/bin/`) when:
- Agents or the user repeatedly write ad-hoc scripts in `/tmp/opencode/` (e.g., querying SAP HANA traces via `@sap/hana-client` and `default-env.json`, comparing Jest failure deltas against `HEAD`, parsing PRD headers).
- A complex multi-flag command is run across 3+ sessions with fragile inline environment variables or pipes (e.g. `git -C <dir> status/log/branch` chains).

### B. When to Propose a New Skill (`~/.dotfiles/ai/agents/opencode/skills/<name>/SKILL.md`)
Propose a new skill (via `skill-creator`) when:
- 2+ sessions in `workflowsByProject` tackle the same domain workflow (e.g., reconciling SAP SF entities, validating branch promotion matrices across `master` / `deploy-develop` / feature branches) and required manual domain explanations from Bruno.
- A project has recurring non-obvious paths or credentials (e.g., where `default-env.json` lives, how symlinks map in `apps-workspaces`).

### C. When to Propose a System / Skill Patch (`Gotchas` or `AGENTS.md`)
Propose a targeted patch when:
- **Engram Discoveries**: `engramLearnings.highlights` contains technical gotchas or bugfixes (e.g. `$links DELETE` vs `nav {}`, percent-encoding bypasses, MDF upsert quirks) that are not yet codified in the corresponding domain skill or tool.
- **Agent Discipline**: `agentFallbackClusters` shows subagents heavily using `grep`/`find`/`sed`/`python3 -c` in `bash` instead of native `read`/`edit`/`grep` tools, or creating git worktrees inside `/tmp/opencode/` (violating the CodeGraph worktree rule).

---

## 3. Project Effort & Focus Rubric (Time-Ledger & Tracker)

Cross-reference `projectEffort.ledger` and `projectEffort.projectsYamlAudit`:
1. **Dormant Active Projects**: Projects marked `status: active` in `scripts/projects.yaml` that logged 0 AI hours in the window. Coach Bruno on whether to pause them (`status: paused`) or triage their `next:` block.
2. **High-Effort Streams**: Projects concentrating >30% of weekly hours. Verify if their `next:` actionable reflects the latest state or if work is churning on unresolved blockers.
3. **Missing / Stale Next**: Active projects lacking an actionable concrete next step.

---

## 4. Report Template

Use this structure when presenting the bi-weekly review (in Spanish, direct, technical, zero fluff):

```markdown
## Loop de Mejora (`improve-system`) — Ventana: {days} días

**Métricas del Operador (OpenCode + Antigravity)**:
- **Sesiones auditadas**: {totalSessions} ({opencodeRootSessions} OpenCode + {agySessions} Antigravity · {subagentSessions} subagentes) · {totalUserTurns} turnos de usuario
- **Tasa de Corrección / Fricción**: **{correctionRatePct}%** ({correctionTurns}/{totalFollowUpTurns} turnos de seguimiento) {trend_vs_previous}
- **Espirales de corrección**: {correctionSpiralSessions} · **Sesiones maratón/zombi**: {zombieSessions} · **Arranques telegráficos**: {telegraphicSessions}
- **Esfuerzo en Proyectos (Time-Ledger)**: **{totalHours}h** de IA registradas en {entriesCount} entradas.

---

### 1. Foco y Esfuerzo en Proyectos (Time-Ledger & projects.yaml)
- **Top Proyectos por dedicación**:
  - `{proyecto-1}`: `{N}`h — *{nota o hito principal}*
  - `{proyecto-2}`: `{N}`h — *{nota o hito principal}*
- **Proyectos Activos Durmientes (0h en ventana)**: `{proy-A}`, `{proy-B}` (¿pausar o priorizar?).
- **Alineación de Next**: {diagnóstico de si el esfuerzo coincide con el backlog de projects.yaml}.

---

### 2. Espejo del Operador (Qué estás haciendo o comunicando mal)

1. **{Patrón detectado}** — *Sesión `{title}` (`{project}`, {userTurns} turnos)*
   - **Qué dijiste**: `"{quoted turn}"`
   - **Qué falló en tu comunicación/workflow**: {diagnóstico directo}
   - **Cómo debiste plantearlo (Turno 1 ideal / Workflow)**:
     > `{prompt reescrito o comando/skill que debiste invocar}`

*(Máximo 3 hallazgos priorizados por impacto real)*

---

### 3. Aprendizajes Durables de Engram (Gotchas & Hallazgos a Promocionar)
- **{tipo} en {proyecto}**: {título y hallazgo técnico de Engram}.
- *Prescripción*: {a qué skill `## Gotchas` o tool permanente debe incorporarse para que no se pierda}.

---

### 4. Fábrica de Herramientas y Skills (Qué construir o parchear hoy)

| # | Slug | Tipo | Propuesta | Evidencia en tus sesiones |
|---|---|---|---|---|
| 1 | `{slug-1}` | `CLI Tool` / `New Skill` / `Skill Gotcha` | **{Título corto}**: {qué hace exactamente y dónde vive} | {N sesiones / comandos repetidos / fricción evitada} |
| 2 | `{slug-2}` | ... | ... | ... | ... |

---

### 5. Salud del Arnés (OpenCode & AGY)
- **Disciplina de herramientas**: {observación sobre subagentes, fallbacks a bash (grep/find), etc.}
- **Skills & MCPs**: {skills usadas vs oportunidades perdidas; MCPs activos sin uso}
- **Lo que está funcionando muy bien**: {1-2 puntos fuertes respaldados por datos}

---

¿Qué propuestas de la tabla quieres que **aplique/construya ahora mismo** (ej. `"aplica 1 y 2"`, `"descarta 3"`) y registramos la pasada con `improve-system --record-run`?
```
