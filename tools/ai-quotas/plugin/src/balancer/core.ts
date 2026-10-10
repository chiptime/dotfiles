/**
 * Pure decision core for the quota-balancer read-only advisor (v1 manual
 * advisor). decide() maps one raw advice payload + injected state to
 * show/suppress; fs/clock/tier-config are injected so tests stay hermetic.
 * Fail-open by construction: kill switch, wrong schema, malformed shape,
 * staleness > 600 s, switch:false, off-tier rec, or a duplicate inside the
 * notice interval all yield { show: false }, never throw (R5-R7).
 */
export const ADVICE_SCHEMA = 'ai-quotas/balancer-advice@1';
export const MAX_ADVICE_AGE_SECONDS = 600;
export const DEFAULT_NOTICE_INTERVAL_MS = 900_000;
export const NOTICE_MAX_LENGTH = 80;

export interface BalancerAdvice {
	schema: string;
	requested_model: string;
	tier: string;
	recommended_model: string;
	switch: boolean;
	reason: string;
	advice_age_seconds: number;
	requested_remaining?: number;
	recommended_remaining?: number;
}

// Tier map comes from the same balancer config the server scores with (R3);
// ShownState is the last notice actually shown, threaded through decide().
export type TierMap = Record<string, Record<string, string>>;
export interface ShownState { model: string; at: number }
export interface DecideInput { raw: unknown; killSwitch: boolean; tiers: TierMap | null; now: number; lastShown: ShownState | null; noticeIntervalMs?: number }
export interface Decision { show: boolean; notice: string | null; lastShown: ShownState | null }

// Schema-tag + shape check; anything unexpected yields null, never throws.
export function parseAdvice(raw: unknown): BalancerAdvice | null {
	if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;
	const r = raw as Record<string, unknown>;
	const s = (v: unknown) => (typeof v === 'string' && v !== '' ? v : null);
	const requested_model = s(r.requested_model);
	const tier = s(r.tier);
	const recommended_model = s(r.recommended_model);
	if (r.schema !== ADVICE_SCHEMA || requested_model === null || tier === null || recommended_model === null || typeof r.switch !== 'boolean' || typeof r.reason !== 'string' || typeof r.advice_age_seconds !== 'number' || !Number.isFinite(r.advice_age_seconds) || r.advice_age_seconds < 0) return null;
	const a: BalancerAdvice = { schema: ADVICE_SCHEMA, requested_model, tier, recommended_model, switch: r.switch, reason: r.reason, advice_age_seconds: r.advice_age_seconds };
	for (const k of ['requested_remaining', 'recommended_remaining'] as const) {
		const v = r[k]; // additive/optional: absent or non-numeric is tolerated
		if (typeof v === 'number' && Number.isFinite(v)) a[k] = v;
	}
	return a;
}

export function decide(input: DecideInput): Decision {
	const keep = (): Decision => ({ show: false, notice: null, lastShown: input.lastShown });
	if (input.killSwitch) return keep(); // R6: exit before anything else
	const advice = parseAdvice(input.raw);
	if (advice === null) return keep(); // malformed / wrong schema
	if (advice.advice_age_seconds > MAX_ADVICE_AGE_SECONDS) return keep(); // stale render
	if (!advice.switch) return keep(); // server says keep the requested model
	const tierMap = input.tiers?.[advice.tier]; // off-tier guard (R5): rec AND req must be tier members
	if (tierMap === undefined || !(advice.recommended_model in tierMap) || !(advice.requested_model in tierMap)) return keep();
	const last = input.lastShown;
	if (last !== null && last.model === advice.recommended_model && input.now - last.at < (input.noticeIntervalMs ?? DEFAULT_NOTICE_INTERVAL_MS)) return keep(); // notice flooding
	return { show: true, notice: formatNotice(advice), lastShown: { model: advice.recommended_model, at: input.now } };
}

// `quota: → rec · req at N% · /models to switch` — the hint is dropped when the ids alone would breach the cap.
export function formatNotice(advice: BalancerAdvice): string {
	const base = `quota: → ${advice.recommended_model} · ${advice.requested_model}`;
	const pct = advice.requested_remaining === undefined ? '' : ` at ${Math.max(0, Math.min(100, Math.round(advice.requested_remaining * 100)))}%`;
	const hint = ' · /models to switch';
	return base.length + pct.length + hint.length <= NOTICE_MAX_LENGTH ? `${base}${pct}${hint}` : `${base}${pct}`;
}
