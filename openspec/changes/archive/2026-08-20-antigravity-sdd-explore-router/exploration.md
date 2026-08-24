## Exploration: Antigravity-backed transparent router for `sdd-explore`

### Current State

The Gentle AI SDD orchestrator (`gentle-orchestrator`, primary agent in `~/.config/opencode/opencode.json`) delegates exploration via `task(subagent_type="sdd-explore")`. Its `permission.task` allow-list (opencode.json lines 50–72) grants `"sdd-explore": "allow"` and does NOT list `sdd-explore-fallback`. Today `sdd-explore` and `sdd-explore-fallback` are IDENTICAL twins: both `mode: subagent`, `model: deepseek/deepseek-v4-pro`, same prompt `{file:./prompts/sdd/sdd-explore.md}`, same tools `{bash,edit,read,write: true}`, `task` not enabled. The `sdd-explore` prompt (`~/.config/opencode/prompts/sdd/sdd-explore.md`) is byte-equivalent to the SKILL.md plus appended codegraph/artifact-language contract blocks, and it forbids the executor from delegating.

Persistence is owned by the phase executor, never the orchestrator. `engram-convention.md` fixes the deterministic schema `title/topic_key = sdd/{change-name}/{artifact-type}`, `type: architecture`, `capture_prompt: false`. `sdd-new.md`/`sdd-status.md` confirm sub-agents persist; the orchestrator only synthesizes. For `engram` store, `sdd-status.md` explicitly says do NOT run the native dispatcher (it reads only `openspec/changes/`).

The plugin `plugins/sdd-task-result-artifacts.ts` validates every SDD phase task result: it matches agents via `agent === phase || agent.startsWith(phase + "-")`, so both `sdd-explore` and `sdd-explore-fallback` classify as phase `sdd-explore`. Empty/malformed results throw and LATCH the session (`sdd_task_result_empty|malformed`). A router must therefore return a valid text envelope (not a nested `<task_result>`).

**Antigravity reality (from the validated spike):** `agy` v1.0.10 at `~/.local/bin/agy` (LOCAL WSL2 only — NOT installed in the Docker/VPS image). Non-interactive via `--print`, runs with `--dangerously-skip-permissions`; its own exit code lies (0 even on task failure), so `antigravity-spike/run-antigravity-task.sh` wraps it with a success contract: exit `0` success / `2` args/binary missing / `3` daily guard / `4` task failed (artifact missing/empty) / `124` timeout. agy has read-only Engram MCP (`mem_search/mem_context/mem_get_observation/mem_current_project`, confirmed in `~/.gemini/config/mcp_config.json` — no write tools) and CodeGraph MCP (`codegraph serve --mcp`) wired. agy output is UNTRUSTED and every dir in `AGY_EXTRA_DIRS` is WRITABLE by the agent (a stray file was already dropped into a repo root during the spike).

**Quota:** official Antigravity statusline writes `~/.config/ai-quotas/gemini.json` passively. Confirmed live schema: `provider: antigravity`, `active_model`, `plan_tier`, `updated_at`, `quota_gemini_5h` (remaining_fraction/percentage, reset_in_seconds, reset_time), `quota_gemini_weekly`, `quota_3p_5h` (models: "Claude Sonnet / Opus / GPT"), `quota_3p_weekly`, `context_window`. Two 5h pools reset independently (gemini vs 3p). No `/usage` polling.

**Model routing:** `provider.google` (via `opencode-antigravity-auth@latest` plugin) exposes `antigravity-gemini-3-flash`, `-3-pro`, `-3.1-pro`, `antigravity-claude-sonnet-4-6`, `-opus-4-6-thinking` with variants (high/low/medium/minimal). Cheap/free models already in use for thin agents: `opencode/deepseek-v4-flash-free`.

**Deployment:** `supervisor/opencode.conf` program `opencode-web` runs `opencode web --hostname 0.0.0.0 --port 4096`; Dockerfile binds host `~/.config/opencode` → `/home/opencode/.config/opencode` (compose line 45). OpenCode SDK exposes `OPENCODE_CONFIG_CONTENT` (in-process JSON injection) in `@opencode-ai/sdk`; a CLI-level `OPENCODE_CONFIG` file override was NOT confirmed in the local runtime — this is an unresolved question.

### Affected Areas

- `~/.config/opencode/opencode.json` (external, user-owned) — agent definitions `sdd-explore` (repurpose → router) and `sdd-explore-fallback` (native twin), per-agent `permission.task`, `tools.task`, model overrides, provider block.
- `~/.config/opencode/prompts/sdd/` — new router prompt; native `sdd-explore.md` stays as the fallback executor prompt.
- `~/.config/opencode/commands/sdd-explore.md` + `sdd-new.md` — likely unchanged (orchestrator gate contract must NOT change).
- `~/.config/opencode/plugins/sdd-task-result-artifacts.ts` — envelope/latch interaction; probably unchanged but must be respected by the router's return shape.
- `antigravity-spike/run-antigravity-task.sh` (repo) — base for the production `agy-runner` with typed outcomes.
- `.opencode/skills/antigravity-explore/SKILL.md` (repo) — stale: describes manual wrapper use, not router integration; needs update.
- `antigravity-spike/README.md` (repo) — spike evidence and the stray-file containment lesson.
- NEW (repo): `agy-runner` (typed-outcome wrapper), versioned JSON contract doc, quota-bucket reader, canonical-structure validator, tests `tests/*.test.ts`.
- Possibly `Dockerfile.opencode` / `docker-compose.yml` — only if agy + OAuth creds must be provisioned on the VPS path (currently agy is local-only).

### Approaches

1. **A — Direct native `sdd-explore` enhancement (no agy).** Keep the single agent, add agy as an optional tool call inside the executor prompt.
   - Pros: zero new agents, no nesting, no permission changes, persistence ownership trivial.
   - Cons: mixes provider-unavailable handling into the native executor; defeats the "transparent replacement" goal; no provider-neutral seam for D; agy still runs with the expensive `deepseek-v4-pro` host model for the routing decision.
   - Effort: Low.

2. **B — Cheap router under `sdd-explore` name + native `sdd-explore-fallback` (the agreed Phase 1 target).** Repurpose `sdd-explore` into a thin router (cheap/free model) that: reads quota bucket by requested model → invokes `agy-runner` → validates canonical structure → persists on agy success; on typed provider-unavailable outcomes, delegates `task(subagent_type="sdd-explore-fallback")` (the current native twin) and does NOT persist itself.
   - Pros: orchestrator contract unchanged; native fallback preserved; clear throwaway/reusable split (router prompt + nested delegation = throwaway; agy-runner + typed outcomes + quota + validator = reusable for D); cheap routing model.
   - Cons: requires sub-agent→sub-agent `task` delegation (currently forbidden for executors and unverified in runtime); needs a new per-agent permission block; double-persistence boundary must be enforced; extra config surface.
   - Effort: Medium.

3. **D — Custom AI SDK provider/model (Phase 2 target, no router).** Replace the router with a provider-neutral backend that streams agy/Antigravity through a custom provider package; native fallback built into the provider.
   - Pros: cleanest end state; no throwaway router; single code path; reuses B's runner/outcomes/quota/validator/fixtures/observability.
   - Cons: substantial provider-specific streaming, cancellation, usage, and native-fallback engineering; should be justified by measurements from B.
   - Effort: High.

### Recommendation

Adopt **B** as Phase 1, with the reusable backend (agy-runner typed outcomes, quota-bucket reader, canonical-structure validator, persistence owner, fixtures, observability) built as testable TypeScript (Bun) modules rather than pure bash, so Phase 2 (D) swaps only the router prompt + nested-delegation glue for a custom provider. Keep the router model cheap/free (`opencode/deepseek-v4-flash-free` class) and move as much routing/validation as possible into deterministic code (structure lint, quota read, exit-code→typed-outcome mapping) so the router's model judgment is minimal. Prefer workdir-only agy invocation with codegraph_explore MCP (read-only) for code context over `AGY_EXTRA_DIRS` repo exposure.

### Risks

- **agy is not in the Docker/VPS image.** The router's agy path works only where agy + OAuth creds exist (local WSL2 today). On the VPS the agy path silently degrades to native fallback unless agy is provisioned. Decide local-only vs. VPS provisioning explicitly.
- **Nested delegation unverified.** OpenCode sub-agent→sub-agent `task` may be restricted by the runtime; the orchestrator's `sdd-explore: allow` does not obviously transitively authorize the router to call `sdd-explore-fallback`. This is the single biggest feasibility risk for B.
- **Double persistence / recursion.** If the router persists on the fallback path (or the fallback persists on agy path) the `explore` artifact is written twice; the `sdd-explore-fallback` phase also triggers the SDD result latch on failure.
- **agy write containment.** `--dangerously-skip-permissions` makes any `AGY_EXTRA_DIRS` writable; a stray file already escaped once. Repo must stay read-only (workdir-only + codegraph MCP).
- **Untrusted agy output.** Must pass the canonical structure + envelope validation before persistence; no agy-sourced URLs trusted without verification.
- **External home config + restart.** The shared override lives outside the repo (user home); OpenCode Web must be restarted (supervisor `autorestart` does not guarantee config hot-reload) for agent/permission/model changes to take effect.
- **Quota misclassification.** Wrong bucket choice (gemini vs 3p) or stale `gemini.json` (old `updated_at`) can waste quota or fail fast; must select bucket by requested model, not `active_model`, and define staleness policy.
- **Wrapper exit codes are coarse.** The current wrapper does not distinguish auth/captcha vs transient-provider-unavailable vs artifact-validation-failure; typed outcomes require extending the wrapper (or parsing run.log), else fallback triggers on the wrong conditions.

### Unresolved Questions (ask before proposal — do NOT silently decide)

1. Does the installed OpenCode runtime support sub-agent→sub-agent `task`, and does the router need its own explicit `permission.task: {"sdd-explore-fallback": "allow"}`? (blocker for B)
2. Exact `OPENCODE_CONFIG` semantics: is there a CLI-level file/dir override for `opencode web`, how does it merge with `~/.config/opencode/opencode.json` and project `.opencode/`, and is the shared user-owned override a JSON file or a config directory?
3. Scope of Phase 1: local WSL2 only (agy present), or must the VPS container also run agy (provision binary + OAuth creds)?
4. Persistence ownership split: confirm router persists on agy success and does NOT persist on fallback; confirm the native `sdd-explore-fallback` persists only on its own path.
5. Cheap router model choice: free-tier vs. a small paid model; and how much routing/validation is deterministic code vs. model judgment.
6. Quota policy: minimum `remaining_fraction` threshold and `updated_at` staleness cutoff before falling back to native.
7. agy-runner placement/contract: new `scripts/` TS module + thin bash, or a standalone package; and the exact versioned JSON input/result schema (success | quota_unavailable | transient_unavailable | auth_captcha | timeout | task_failure | artifact_validation_failure).
8. Which Web projects (if any) must opt OUT of the router and keep native `sdd-explore`.

### Ready for Proposal

**No.** The exploration is bounded and approach B is confirmed as the target, but feasibility question #1 (nested delegation) and #2 (OPENCODE_CONFIG override semantics) are blockers that must be answered with evidence before `sdd-propose` can commit to the router design. The orchestrator should present the 8 unresolved questions above and ask the user to decide #3–#8 (and verify #1–#2 against the installed OpenCode runtime) before launching proposal.

### Envelope

- status: success
- executive_summary: Bounded a transparent Antigravity-backed router for `sdd-explore` (approach B) without changing the orchestrator contract; confirmed twin native agents, executor-owned persistence, quota/statusline schema, agy containment/exit-code gaps, and the B→D reusable seam. Flagged nested sub-agent delegation and OPENCODE_CONFIG override semantics as proposal blockers.
- artifacts: Engram `sdd/antigravity-sdd-explore-router/explore`
- next_recommended: sdd-propose (after blockers #1–#2 verified and #3–#8 answered)
- risks: see Risks above
- skill_resolution: paths-injected — 3 skills (sdd-explore, sdd-phase-common, antigravity-explore)
