/**
 * quota-balancer plugin — read-only advisor adapter (v1 manual advisor).
 * chat.message READs input.model only (its output is never read back by the
 * runtime — spike RESULTS.md), consults the local ai-quotas advice endpoint
 * (localhost, 300 ms timeout, size cap), runs the pure core decide(), and
 * surfaces ONE deduplicated toast: never writes, never scores, never throws
 * (R5/R7/R10); kill switch returns before any fetch (R6); no session.created
 * hook — the spike proved per-session pinning impossible.
 */
import type { Plugin } from "@opencode-ai/plugin"
import { existsSync, readFileSync } from "node:fs"
import { homedir } from "node:os"
import { DEFAULT_NOTICE_INTERVAL_MS, decide, type ShownState, type TierMap } from "../balancer/core"

/**
 * Narrow callable surface of fetch. Bun's global `fetch` carries a required
 * `preconnect` namespace property (`typeof fetch`) that the advisor never
 * uses; typing deps against the plain callable keeps fakes honest.
 */
export type FetchLike = (input: string | URL | Request, init?: RequestInit) => Promise<Response>

export interface AdvisorDeps {
	env: Record<string, string>
	fetchImpl: FetchLike
	exists: (path: string) => boolean
	readText: (path: string) => string | null
	showToast: (message: string) => Promise<void>
	logDebug: (message: string) => void
	now: () => number
	timeoutMs: number
	maxBytes: number
}
export interface AdvisorState { lastShown: ShownState | null }
const readTextOrNull = (p: string): string | null => { try { return readFileSync(p, "utf8") } catch { return null } }

// Same config file the server scores with; absent/unparseable -> no tier truth.
function loadTiers(d: AdvisorDeps): { tiers: TierMap | null; noticeIntervalMs: number } {
	try {
		const raw = JSON.parse(d.readText(d.env.AI_QUOTAS_BALANCER_CONFIG ?? `${homedir()}/.config/ai-stack/balancer.json`) ?? "") as { tiers?: unknown; notice_interval?: unknown }
		const tiers = typeof raw.tiers === "object" && raw.tiers !== null ? (raw.tiers as TierMap) : null
		const interval = typeof raw.notice_interval === "number" && raw.notice_interval > 0 ? raw.notice_interval * 1000 : DEFAULT_NOTICE_INTERVAL_MS
		return { tiers, noticeIntervalMs: interval }
	} catch {
		return { tiers: null, noticeIntervalMs: DEFAULT_NOTICE_INTERVAL_MS }
	}
}

// One bounded fetch (AbortController timeout, HTTP status, size cap, JSON parse); any failure -> null (fail open).
async function fetchAdvice(url: string, d: AdvisorDeps): Promise<unknown> {
	const controller = new AbortController()
	const timer = setTimeout(() => controller.abort(), d.timeoutMs)
	try {
		const res = await d.fetchImpl(url, { signal: controller.signal })
		if (!res.ok) return null
		if (Number(res.headers?.get?.("content-length") ?? 0) > d.maxBytes) return null
		const text = await res.text()
		if (text.length > d.maxBytes) return null
		return JSON.parse(text) as unknown
	} catch {
		return null // refused / timed out / aborted / unparsable: fail open
	} finally {
		clearTimeout(timer)
	}
}

export async function adviseOnce(model: string, state: AdvisorState, d: AdvisorDeps): Promise<void> {
	// R6 kill switch: exit before any network (core re-checks for defense in depth).
	if (d.exists(d.env.QUOTA_BALANCER_FORCE_NATIVE_FILE ?? `${homedir()}/.config/ai-stack/force-native`)) return
	const { tiers, noticeIntervalMs } = loadTiers(d)
	const port = Number(d.env.AI_QUOTAS_PORT ?? 0) || 47623 // server.rs DEFAULT_PORT
	const raw = await fetchAdvice(`http://127.0.0.1:${port}/api/balancer/recommend?model=${encodeURIComponent(model)}`, d)
	if (raw === null) d.logDebug(`balancer advice unavailable (${model})`)
	const decision = decide({ raw, killSwitch: false, tiers, now: d.now(), lastShown: state.lastShown, noticeIntervalMs })
	state.lastShown = decision.lastShown
	if (!decision.show || decision.notice === null) return
	try {
		await d.showToast(decision.notice)
	} catch {
		// Toast surface failure (headless/no-TUI): silent, chat proceeds (R7).
	}
}

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
