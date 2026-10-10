# quota-balancer — Phase 0 mutation-capability spike (task 0.2)

Date: 2026-08-26 · Probe: `mutation-probe.ts` (same directory) · Spec: R10 · Gates: tasks 3.1–3.5

## Environment
- Runtime: opencode **1.18.21** (linuxbrew). Plugin contract: `@opencode-ai/plugin` **1.18.16** at `~/.config/opencode/node_modules` — the surface live plugins compile against (same 1.18.x generation as the runtime).

## Evidence (behavior-first; re-run: `bun test ./spike/mutation-probe.test.ts` · `bun spike/mutation-probe.ts`)
- **Type**: `chat.message` puts `model?: {providerID, modelID}` on the read-only input; output is `{message: UserMessage, parts: Part[]}` — no writable model surface.
- **Runtime**: `chat.message` dispatch is `trigger("chat.message",{…,model:t.model,…},{message,parts})` and the result is not captured — output mutations are never read back. Control: output readback IS real where it exists (`chat.headers`: `{headers:g}=yield*trigger(…)`), and `chat.params` output IS read back but is model-free (`{temperature,topP,topK,maxOutputTokens,options}`). The only writable-model output in the whole hook map is `experimental.provider.small_model` (small/fast-model feature, not the chat model).
- **Fallback surfaces**: `session.created` arrives via the `event` hook (`EventSessionCreated{info:Session}`), but `Session` carries no model field and `client.session.update` body is `{title?}` only — the model is per-request state passed in each `session.prompt`. `session.prompt({model})` exists only for caller-originated prompts: calling it from `chat.message` would duplicate the user message and recurse into its own hook. TUI: `tui.openModels` is an interactive dialog; `tui.executeCommand` runs keybind commands (e.g. `agent_cycle`) — no programmatic model-set.

## Verdict
1. Per-message model mutation via `chat.message`: **NOT SUPPORTED** (type + runtime proof).
2. Literal per-session pin via `session.created` + `session.update`: **NOT SUPPORTED** (`Session` has no model; update body is title-only).
3. Abort + re-`session.prompt` with the advised model: **rejected** — duplicates the message, recurses into its own hook, races the in-flight request; violates R5/R7.

## Consequence for design — design-revision-needed
R5's automatic rewrite has no actuation surface in the installed contract, and R10's specified `session.created` fallback premise is unavailable too. Tier-safe v1 options: (a) notice-only advisory — consult the advisor on `chat.message` (input.model is readable), emit `client.app.log`/`tui.showToast` recommending the tier-equivalent model, user applies it via the model picker; (b) block auto-switching on an upstream model-mutation hook and keep the advisor endpoint inert. Spec R10 and tasks 3.1–3.5 need revision before Phase 3 apply.

**Rollback:** delete `spike/mutation-probe.ts` + `spike/RESULTS.md` only (the rest of `spike/` is the unrelated antigravity spike).
