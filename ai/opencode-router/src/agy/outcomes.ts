/**
 * Typed outcome contracts for the agy exploration backend (v1). Raw agy
 * exit codes are NOT trusted alone; classification combines spawn error,
 * timeout, the typed JSON envelope (--output-format json), run.log markers,
 * exit code, and artifact presence.
 *
 * classifyRun precedence (first match wins):
 * 1. spawnError ENOENT            → transient_unavailable (agy_absent)
 * 2. stalled                      → timeout (stall_detected) — the stream
 *    runner's stall watchdog killed the child after a stall window with no
 *    output line at all; recoverable like any other timeout
 * 3. timedOut OR exitCode 124     → timeout (timeout)
 * 4. exitCode 0 AND artifactBytes → success (ok) — a non-empty artifact on a
 *    clean exit is the authoritative success signal; log-pattern regexes gate
 *    only runs that miss this rule (failed runs)
 * 5. AUTH_RE matches log          → auth_captcha (auth_or_captcha)
 * 6. failed run AND envelope.status === 'ERROR' with the print-wait timeout
 *    signature in envelope.error → timeout (agy_print_wait_timeout) — the
 *    typed JSON envelope is the FIRST failed-run signal, ahead of the
 *    plain-text regex; any other envelope ERROR falls through to 7–10
 * 7. exitCode !== 0 (or null) AND PRINT_WAIT_TIMEOUT_RE matches log
 *                                → timeout (agy_print_wait_timeout) — kept as
 *    the plain-text fallback for runs without a parseable envelope
 * 8. exitCode !== 0 (or null) AND QUOTA_RE matches log
 *                                → quota_unavailable (quota_exhausted)
 * 9. exitCode !== 0 (or null) AND TRANSIENT_RE matches log
 *                                → transient_unavailable (provider_outage)
 *    Exit-code corroboration: the log is agy's COMBINED stdout+stderr, so
 *    quota/transient words can appear as noise on clean runs. They only
 *    count as unavailability (fallback) when the process exit code agrees.
 * 10. exitCode !== 0              → task_failure (nonzero_exit)
 * 11. artifactBytes missing/0      → artifact_validation_failure (artifact_missing_or_empty)
 */
import { type AgyEnvelope, type AgyUsage } from './spawn';

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
	/** agy conversation id from the JSON envelope (recovery handle); present only after a real run. */
	conversationId?: string;
	/** Token accounting from the JSON envelope; present only after a real run. */
	usage?: AgyUsage;
}
/** What the runner observed about one agy invocation. */
export interface RunSignal {
	exitCode: number | null;
	log?: string;
	/** e.g. ENOENT when the binary could not even start. */
	spawnError?: string;
	timedOut?: boolean;
	/** True when the stream runner's stall watchdog killed the run after stallMs of silence. */
	stalled?: boolean;
	/** Size in bytes of the expected artifact; 0/undefined means missing. */
	artifactBytes?: number;
	/** Parsed `--output-format json` envelope, when agy printed one. */
	envelope?: AgyEnvelope;
}
export interface Classification {
	outcome: Outcome;
	reason: string;
}
const AUTH_RE = /captcha|sign.?in|log.?in required|unauthenticated|forbidden|\b401\b|invalid credentials|authentication/i;
const QUOTA_RE = /quota|rate.?limit|\b429\b|resource.?exhausted|too many requests/i;
const TRANSIENT_RE = /unavailable|outage|overloaded|connection\s+(?:refused|reset|failed)|network\s+error|\b5\d\d\b|internal error|server error/i;
const PRINT_WAIT_TIMEOUT_RE = /timeout waiting for response/i;
/** Fallback to the native executor is allowed ONLY for approved unavailability. */
export function isFallbackAllowed(outcome: Outcome): boolean {
	return outcome === 'quota_unavailable' || outcome === 'transient_unavailable' || outcome === 'timeout';
}

/**
 * Deterministic exit/log/envelope → Outcome mapping. First match wins across
 * the 11 rules documented on this module: ENOENT, stall-watchdog kill
 * (stall_detected), plain timeout, artifact-backed success (exit 0 + artifact
 * present — immune to log patterns), AUTH gate, then, within FAILED runs, the
 * typed JSON envelope's ERROR status gates first (its print-wait timeout
 * signature maps to timeout, not task_failure), followed by the plain-text
 * print-wait regex fallback, the QUOTA/TRANSIENT regex gates for FAILED runs
 * only (nonzero/null exit — combined-output log noise on a clean exit is not
 * unavailability), nonzero exit, and empty artifact.
 */
export function classifyRun(signal: RunSignal): Classification {
	const log = signal.log ?? '';
	if (signal.spawnError === 'ENOENT') return { outcome: 'transient_unavailable', reason: 'agy_absent' };
	// Stall watchdog kill: no output line for the whole stall window. Checked
	// before the plain-timeout rule so a stalled run reports stall_detected;
	// either way the outcome is the recoverable timeout family.
	if (signal.stalled) return { outcome: 'timeout', reason: 'stall_detected' };
	if (signal.timedOut || signal.exitCode === 124) return { outcome: 'timeout', reason: 'timeout' };
	if (signal.exitCode === 0 && signal.artifactBytes) return { outcome: 'success', reason: 'ok' };
	if (AUTH_RE.test(log)) return { outcome: 'auth_captcha', reason: 'auth_or_captcha' };
	if (signal.exitCode !== 0) {
		// The typed JSON envelope (--output-format json) is the first failed-run
		// signal: its ERROR status with agy's own print-wait deadline signature is
		// a timeout, not a task failure — treat it as recoverable. Any other
		// envelope ERROR falls through to the regex gates below, which still run
		// against the log.
		if (signal.envelope?.status === 'ERROR' && /timeout waiting for response/i.test(signal.envelope.error ?? '')) {
			return { outcome: 'timeout', reason: 'agy_print_wait_timeout' };
		}
		// Plain-text fallback for runs without a parseable envelope: agy's print
		// client exits nonzero with this exact line when its own wait deadline fires.
		if (PRINT_WAIT_TIMEOUT_RE.test(log)) return { outcome: 'timeout', reason: 'agy_print_wait_timeout' };
		if (QUOTA_RE.test(log)) return { outcome: 'quota_unavailable', reason: 'quota_exhausted' };
		if (TRANSIENT_RE.test(log)) return { outcome: 'transient_unavailable', reason: 'provider_outage' };
		return { outcome: 'task_failure', reason: 'nonzero_exit' };
	}
	if (!signal.artifactBytes) return { outcome: 'artifact_validation_failure', reason: 'artifact_missing_or_empty' };
	return { outcome: 'success', reason: 'ok' };
}
/** Build a typed result; fallbackAllowed is always derived from the outcome. */
export function buildResult(
	outcome: Outcome,
	fields: { elapsedMs: number; reason?: string; artifactPath?: string; sha256?: string; receipt: Receipt; conversationId?: string; usage?: AgyUsage },
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
		conversationId: fields.conversationId,
		usage: fields.usage,
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
