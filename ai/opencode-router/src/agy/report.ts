/**
 * Savings report over metrics records: success/fallback rates, latency,
 * and explore-cost delta versus native runs (phase-D viability signals).
 */
import type { MetricsRecord } from './metrics';

const FALLBACK_OUTCOMES = new Set(['quota_unavailable', 'transient_unavailable', 'timeout']);
const BLOCKED_OUTCOMES = new Set(['auth_captcha', 'task_failure', 'artifact_validation_failure']);

export interface Summary {
	runs: number;
	successRate: number;
	fallbackRate: number;
	blockedCount: number;
	avgElapsedMs: number;
	byOutcome: Record<string, number>;
}

export function summarize(recs: MetricsRecord[]): Summary {
	const byOutcome: Record<string, number> = {};
	for (const r of recs) byOutcome[r.outcome] = (byOutcome[r.outcome] ?? 0) + 1;
	const total = recs.length;
	const denom = total || 1;
	return {
		runs: total,
		successRate: (byOutcome.success ?? 0) / denom,
		fallbackRate: recs.filter((r) => FALLBACK_OUTCOMES.has(r.outcome)).length / denom,
		blockedCount: recs.filter((r) => BLOCKED_OUTCOMES.has(r.outcome)).length,
		avgElapsedMs: recs.reduce((a, r) => a + r.elapsedMs, 0) / denom,
		byOutcome,
	};
}

export interface SavingsReport {
	runs: number;
	routedSuccess: number;
	fallbackRuns: number;
	avgRoutedMs: number;
	nativeAvgMs: number;
	latencyDeltaMs: number;
	latencyDeltaPct: number;
	routerTokensTotal: number;
}

/** Positive latencyDelta = routed runs are faster than the native baseline. */
export function savingsReport(recs: MetricsRecord[], nativeAvgMs: number): SavingsReport {
	const routed = recs.filter((r) => r.outcome === 'success');
	const avgRoutedMs = routed.length ? routed.reduce((a, r) => a + r.elapsedMs, 0) / routed.length : 0;
	return {
		runs: recs.length,
		routedSuccess: routed.length,
		fallbackRuns: recs.filter((r) => FALLBACK_OUTCOMES.has(r.outcome)).length,
		avgRoutedMs,
		nativeAvgMs,
		latencyDeltaMs: nativeAvgMs - avgRoutedMs,
		latencyDeltaPct: nativeAvgMs > 0 ? ((nativeAvgMs - avgRoutedMs) / nativeAvgMs) * 100 : 0,
		routerTokensTotal: recs.reduce((a, r) => a + r.routerTokens, 0),
	};
}
