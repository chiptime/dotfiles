/**
 * Typed outcome contracts for the agy exploration backend (v1). Raw agy
 * exit codes are NOT trusted alone; classification combines spawn error,
 * timeout, run.log markers, exit code, and artifact presence.
 *
 * classifyRun precedence (first match wins):
 * 1. spawnError ENOENT            → transient_unavailable (agy_absent)
 * 2. timedOut OR exitCode 124     → timeout (timeout)
 * 3. exitCode 0 AND artifactBytes → success (ok) — a non-empty artifact on a
 *    clean exit is the authoritative success signal; log-pattern regexes gate
 *    only runs that miss this rule (failed runs)
 * 4. AUTH_RE matches log          → auth_captcha (auth_or_captcha)
 * 5. QUOTA_RE matches log         → quota_unavailable (quota_exhausted)
 * 6. TRANSIENT_RE matches log     → transient_unavailable (provider_outage)
 * 7. exitCode !== 0               → task_failure (nonzero_exit)
 * 8. artifactBytes missing/0      → artifact_validation_failure (artifact_missing_or_empty)
 */
export type Outcome =
	| 'success'
	| 'quota_unavailable'
	| 'transient_unavailable'
	| 'auth_captcha'
	| 'timeout'
	| 'task_failure'
	| 'artifact_validation_failure';
export type Store = 'engram' | 'openspec' | 'hybrid' | 'none';
export const EXPLORE_REQUEST_SCHEMA = 'agy-explore/req@1';
export const EXPLORE_RESULT_SCHEMA = 'agy-explore/res@1';
export interface ExploreRequest {
	schema: typeof EXPLORE_REQUEST_SCHEMA;
	change: string;
	store: Store;
	repo: string;
	brief: string;
	model: string;
}
export interface Receipt {
	store: string;
	wroteOpenspec: boolean;
	engramRequired: boolean;
}
export interface ExploreResult {
	schema: typeof EXPLORE_RESULT_SCHEMA;
	outcome: Outcome;
	reason?: string;
	fallbackAllowed: boolean;
	artifactPath?: string;
	sha256?: string;
	elapsedMs: number;
	receipt: Receipt;
}
/** What the runner observed about one agy invocation. */
export interface RunSignal {
	exitCode: number | null;
	log?: string;
	/** e.g. ENOENT when the binary could not even start. */
	spawnError?: string;
	timedOut?: boolean;
	/** Size in bytes of the expected artifact; 0/undefined means missing. */
	artifactBytes?: number;
}
export interface Classification {
	outcome: Outcome;
	reason: string;
}
const AUTH_RE = /captcha|sign.?in|log.?in required|unauthenticated|forbidden|\b401\b|invalid credentials|authentication/i;
const QUOTA_RE = /quota|rate.?limit|\b429\b|resource.?exhausted|too many requests/i;
const TRANSIENT_RE = /unavailable|outage|overloaded|connection\s+(?:refused|reset|failed)|network\s+error|\b5\d\d\b|internal error|server error/i;
/** Fallback to the native executor is allowed ONLY for approved unavailability. */
export function isFallbackAllowed(outcome: Outcome): boolean {
	return outcome === 'quota_unavailable' || outcome === 'transient_unavailable' || outcome === 'timeout';
}

/**
 * Deterministic exit/log → Outcome mapping. First match wins across the 8
 * rules documented on this module: ENOENT, timeout, artifact-backed success
 * (exit 0 + artifact present — immune to log patterns), then the AUTH/QUOTA/
 * TRANSIENT regex gates for failed runs, nonzero exit, and empty artifact.
 */
export function classifyRun(signal: RunSignal): Classification {
	const log = signal.log ?? '';
	if (signal.spawnError === 'ENOENT') return { outcome: 'transient_unavailable', reason: 'agy_absent' };
	if (signal.timedOut || signal.exitCode === 124) return { outcome: 'timeout', reason: 'timeout' };
	if (signal.exitCode === 0 && signal.artifactBytes) return { outcome: 'success', reason: 'ok' };
	if (AUTH_RE.test(log)) return { outcome: 'auth_captcha', reason: 'auth_or_captcha' };
	if (QUOTA_RE.test(log)) return { outcome: 'quota_unavailable', reason: 'quota_exhausted' };
	if (TRANSIENT_RE.test(log)) return { outcome: 'transient_unavailable', reason: 'provider_outage' };
	if (signal.exitCode !== 0) return { outcome: 'task_failure', reason: 'nonzero_exit' };
	if (!signal.artifactBytes) return { outcome: 'artifact_validation_failure', reason: 'artifact_missing_or_empty' };
	return { outcome: 'success', reason: 'ok' };
}
/** Build a typed result; fallbackAllowed is always derived from the outcome. */
export function buildResult(
	outcome: Outcome,
	fields: { elapsedMs: number; reason?: string; artifactPath?: string; sha256?: string; receipt: Receipt },
): ExploreResult {
	return {
		schema: EXPLORE_RESULT_SCHEMA,
		outcome,
		reason: fields.reason,
		fallbackAllowed: isFallbackAllowed(outcome),
		artifactPath: fields.artifactPath,
		sha256: fields.sha256,
		elapsedMs: fields.elapsedMs,
		receipt: fields.receipt,
	};
}

const STORES: Store[] = ['engram', 'openspec', 'hybrid', 'none'];

/** Parse and validate an ExploreRequest; null when it is not v1-shaped. */
export function parseExploreRequest(raw: unknown): ExploreRequest | null {
	if (typeof raw !== 'object' || raw === null) return null;
	const r = raw as Record<string, unknown>;
	const ok = r.schema === EXPLORE_REQUEST_SCHEMA
		&& typeof r.change === 'string' && r.change.length > 0
		&& typeof r.repo === 'string'
		&& typeof r.brief === 'string'
		&& typeof r.model === 'string'
		&& typeof r.store === 'string' && (STORES as string[]).includes(r.store);
	if (!ok) return null;
	return { schema: EXPLORE_REQUEST_SCHEMA, change: r.change as string, store: r.store as Store, repo: r.repo as string, brief: r.brief as string, model: r.model as string };
}
