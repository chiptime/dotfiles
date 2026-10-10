import { existsSync, readFileSync } from "node:fs"
import { homedir } from "node:os"
import { DEFAULT_NOTICE_INTERVAL_MS, decide, type ShownState, type TierMap } from "./core"

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
export const readTextOrNull = (p: string): string | null => { try { return readFileSync(p, "utf8") } catch { return null } }

// Same config file the server scores with; absent/unparseable -> no tier truth.
export function loadTiers(d: AdvisorDeps): { tiers: TierMap | null; noticeIntervalMs: number } {
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
export async function fetchAdvice(url: string, d: AdvisorDeps): Promise<unknown> {
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
	if (!d || typeof d?.exists !== "function") return
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
