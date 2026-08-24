/**
 * JSONL metrics sink: one line per CLI run (schema agy-explore/metrics@1).
 * Records outcome, elapsed, model, router token overhead, fallback use.
 */
import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { isFallbackAllowed, type ExploreRequest, type ExploreResult, type Outcome } from './outcomes';

export interface MetricsRecord {
	schema: 'agy-explore/metrics@1';
	ts: string;
	change: string;
	model: string;
	outcome: Outcome;
	reason?: string;
	elapsedMs: number;
	routerTokens: number;
	/** CLI-observed: outcome is approved-unavailability (router delegates native in PR 3). */
	fallbackUsed: boolean;
}

export function recordFromResult(res: ExploreResult, req: ExploreRequest, routerTokens = 0): MetricsRecord {
	return {
		schema: 'agy-explore/metrics@1',
		ts: new Date().toISOString(),
		change: req.change,
		model: req.model,
		outcome: res.outcome,
		reason: res.reason,
		elapsedMs: res.elapsedMs,
		routerTokens,
		fallbackUsed: res.outcome !== 'success' && isFallbackAllowed(res.outcome),
	};
}

export function appendMetrics(path: string, rec: MetricsRecord): void {
	mkdirSync(dirname(path), { recursive: true });
	appendFileSync(path, JSON.stringify(rec) + '\n');
}

export function parseMetrics(text: string): MetricsRecord[] {
	return text
		.split('\n')
		.filter((l) => l.trim())
		.map((l) => {
			try {
				const r = JSON.parse(l);
				return r?.schema === 'agy-explore/metrics@1' ? (r as MetricsRecord) : null;
			} catch {
				return null;
			}
		})
		.filter((r): r is MetricsRecord => r !== null);
}
