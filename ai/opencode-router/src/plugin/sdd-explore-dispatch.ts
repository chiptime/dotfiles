/**
 * sdd-explore dispatcher plugin — thin adapter only.
 *
 * Intercepts task(subagent_type="sdd-explore") in tool.execute.before and runs
 * the deterministic agy-explore pipeline from src/agy/dispatch-core.ts:
 *   success/blocked -> throw the canonical SDD envelope (throw-only
 *     substitution is the proven pattern from sdd-task-result-artifacts.ts);
 *   approved unavailability -> mutate subagent_type to sdd-explore-fallback
 *     (args mutation is the proven pattern from opencode-review-transport.ts);
 *   ANY uncertainty -> return, so the native router agent runs unchanged.
 *
 * Wiring note (design option b): this file is bundled by the router repo's
 * build.sh into a single self-contained ~/.config/opencode/plugins/
 * sdd-explore-dispatch.ts artifact. The versioned source of truth stays in
 * the dotfiles router repo; no runtime cross-repo imports, so the plugin
 * survives reboots and upgrades with zero path magic. Rebuild via build.sh
 * after editing dispatch-core.ts.
 */
import type { Plugin } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import { existsSync, mkdirSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dispatchTaskPrompt, FALLBACK_SUBAGENT, TARGET_SUBAGENT, type RunOutcome } from "../agy/dispatch-core"

function runProcess(cmd: string, args: string[], opts: { cwd: string; timeoutMs: number }): Promise<RunOutcome> {
	return new Promise((resolve) => {
		let stdout = ""
		let stderr = ""
		let timedOut = false
		const child = spawn(cmd, args, { cwd: opts.cwd, stdio: ["ignore", "pipe", "pipe"] })
		const timer = setTimeout(() => {
			timedOut = true
			child.kill("SIGKILL")
		}, opts.timeoutMs)
		child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString("utf8") })
		child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString("utf8") })
		child.on("error", () => { clearTimeout(timer); resolve({ stdout, stderr, exitCode: null, timedOut }) })
		child.on("close", (code) => { clearTimeout(timer); resolve({ stdout, stderr, exitCode: code, timedOut }) })
	})
}

const SDDExploreDispatchPlugin: Plugin = async ({ directory, worktree, client }) => {
	const cwd = worktree || directory
	const debug = async (reason: string) => {
		try {
			await client.app.log({ body: { service: "sdd-explore-dispatch", level: "debug", message: `native fallthrough: ${reason}` } })
		} catch {
			// Logging must never break dispatch.
		}
	}
	return {
		"tool.execute.before": async (input, output) => {
			if (input.tool !== "task" || output.args?.subagent_type !== TARGET_SUBAGENT) return
			if (typeof output.args.prompt !== "string" || output.args.prompt.trim() === "") {
				await debug("task prompt unavailable")
				return
			}
			const env = process.env as Record<string, string>
			const action = await dispatchTaskPrompt(output.args.prompt, {
				env,
				cwd,
				forceNativeFile: env.AGY_EXPLORE_FORCE_NATIVE_FILE ?? `${homedir()}/.config/ai-stack/force-native`,
				agyExploreBin: env.AGY_EXPLORE_BIN ?? "agy-explore",
				agyBin: env.AGY_BIN ?? "agy",
				reqDir: env.AGY_EXPLORE_REQ_DIR ?? "/tmp/opencode/agy",
				// Outer wait = inner cap + 30s grace, both keyed off the same env
				// knob (CLI reads it too); default 1200s lets flash-high finish
				// dense briefs (heaviest measured run: 173s at old plain flash).
				timeoutMs: (Number(env.AGY_EXPLORE_TIMEOUT_MS ?? 0) || 1_200_000) + 30_000,
				exists: existsSync,
				mkdir: (p) => mkdirSync(p, { recursive: true }),
				writeFile: (p, text) => writeFileSync(p, text),
				run: runProcess,
			})
			if (action.action === "return") {
				await debug(action.reason)
				return
			}
			if (action.action === "fallback") {
				output.args.subagent_type = FALLBACK_SUBAGENT
				return
			}
			throw new Error(action.envelope)
		},
	}
}

export default SDDExploreDispatchPlugin
