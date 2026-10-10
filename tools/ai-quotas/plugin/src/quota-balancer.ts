/**
 * quota-balancer plugin — read-only advisor adapter (v1 manual advisor).
 * chat.message READs input.model only (its output is never read back by the
 * runtime — spike RESULTS.md), consults the local ai-quotas advice endpoint
 * (localhost, 300 ms timeout, size cap), runs the pure core decide(), and
 * surfaces ONE deduplicated toast: never writes, never scores, never throws
 * (R5/R7/R10); kill switch returns before any fetch (R6); no session.created
 * hook — the spike proved per-session pinning impossible.
 *
 * NOTE: This module MUST ONLY export the plugin factory as default. OpenCode's
 * plugin loader invokes all exported functions in a plugin file as separate
 * plugin factories; exporting auxiliary functions causes OpenCode to call them
 * with plugin context and push undefined into its internal hook list, crashing
 * server-side Provider.list with "n.provider is undefined".
 */
import type { Plugin } from "@opencode-ai/plugin"
import { existsSync } from "node:fs"
import { adviseOnce, readTextOrNull, type AdvisorState } from "./balancer/advisor"

const QuotaBalancerPlugin: Plugin = ({ client }) => {
	const states = new Map<string, AdvisorState>()
	return {
		"chat.message": async (input) => {
			const model = input.model
			if (!model?.providerID || !model?.modelID) return
			const key = `${input.sessionID}/${model.providerID}/${model.modelID}`
			let state = states.get(key)
			if (state === undefined) states.set(key, (state = { lastShown: null }))
			await adviseOnce(`${model.providerID}/${model.modelID}`, state, {
				env: process.env as Record<string, string>, fetchImpl: fetch, exists: existsSync, readText: readTextOrNull,
				showToast: (message) => client.tui.showToast({ body: { message, variant: "info" } }),
				logDebug: (message) => void client.app.log({ body: { service: "quota-balancer", level: "debug", message } }).catch(() => {}),
				now: Date.now, timeoutMs: 300, maxBytes: 65_536,
			})
		},
	}
}

export default QuotaBalancerPlugin
