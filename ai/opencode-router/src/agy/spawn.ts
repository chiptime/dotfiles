/**
 * Contained agy runner: workdir-only exposure (never --add-dir), hard
 * timeout, run.log capture, and the daily pattern guard counted from
 * agy's own conversation databases.
 */
import { mkdirSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

export interface SpawnOptions {
	bin: string;
	prompt: string;
	workdir: string;
	timeoutMs: number;
	env?: Record<string, string>;
	/** Requested model passed through to agy as `--model`; omitted when empty. */
	model?: string;
}

export interface SpawnRun {
	exitCode: number | null;
	timedOut: boolean;
	log: string;
	elapsedMs: number;
	spawnError?: string;
	/** Parsed `--output-format json` envelope from stdout; null/unset when absent or unparseable. */
	envelope?: AgyEnvelope;
}

/** Token accounting reported by agy's `--output-format json` envelope. */
export interface AgyUsage {
	input_tokens: number;
	output_tokens: number;
	thinking_tokens: number;
	cache_read_tokens: number;
	total_tokens: number;
}

/** Typed envelope agy prints on stdout in `--output-format json` mode. */
export interface AgyEnvelope {
	conversation_id?: string;
	status?: string;
	response?: string;
	error?: string;
	num_turns?: number;
	usage?: AgyUsage;
}

/**
 * Parse agy's `--output-format json` envelope from stdout. In json mode the
 * envelope is the whole stdout (possibly with a trailing newline); when other
 * noise precedes it, fall back to the LAST non-empty line. Returns null on
 * anything that is not an object with a string `status` field; never throws.
 */
export function parseAgyEnvelope(stdout: string): AgyEnvelope | null {
	const text = stdout.trim();
	if (text === '') return null;
	const candidates = [text, ...text.split('\n').reverse().map((l) => l.trim()).filter((l) => l !== '')];
	for (const cand of candidates) {
		try {
			const raw: unknown = JSON.parse(cand);
			if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) continue;
			const rec = raw as Record<string, unknown>;
			if (typeof rec.status !== 'string') continue;
			return raw as AgyEnvelope;
		} catch {
			continue;
		}
	}
	return null;
}

/**
 * Build the agy print-mode argv. NOTE: no --add-dir beyond the workdir ever;
 * agy runs with skip-permissions so any added dir would be writable.
 */
export function buildAgyArgs(opts: SpawnOptions): string[] {
	const args = ['--print', opts.prompt, '--add-dir', opts.workdir, '--dangerously-skip-permissions'];
	// agy's print-mode client wait defaults to 5m0s; without an explicit value long
	// explorations die at 300s while our budgets (AGY_EXPLORE_TIMEOUT_MS defaults:
	// 1200s CLI / 1230s plugin) never fire. Derive the flag from timeoutMs so it
	// fires slightly BEFORE our spawnSync timeout, which stays strictly larger and
	// remains the outer killer.
	const secs = Math.max(1, Math.floor((opts.timeoutMs - 10_000) / 1000));
	args.push('--print-timeout', `${secs}s`);
	// JSON mode: agy prints a typed envelope (status/error/conversation_id/usage)
	// on stdout instead of plain text — classification gets a structured signal,
	// and we keep the conversation_id recovery handle + token usage for free.
	args.push('--output-format', 'json');
	if (opts.model) args.push('--model', opts.model);
	return args;
}

/** Run agy with ONLY the workdir exposed; capture combined output to run.log. */
export function runAgy(opts: SpawnOptions): SpawnRun {
	mkdirSync(opts.workdir, { recursive: true });
	const start = Date.now();
	const proc = spawnSync(opts.bin, buildAgyArgs(opts), {
		cwd: opts.workdir,
		timeout: opts.timeoutMs,
		encoding: 'utf8',
		env: opts.env ? { ...process.env, ...opts.env } : process.env,
	});
	const elapsedMs = Date.now() - start;
	const timedOut = proc.error?.message.includes('ETIMEDOUT') ?? false;
	const log = `${proc.stdout ?? ''}${proc.stderr ?? ''}`;
	writeFileSync(`${opts.workdir}/run.log`, log);
	return {
		exitCode: proc.status,
		timedOut,
		log,
		elapsedMs,
		envelope: parseAgyEnvelope(proc.stdout ?? '') ?? undefined,
		spawnError: proc.error ? ((proc.error as NodeJS.ErrnoException).code === 'ENOENT' ? 'ENOENT' : proc.error.message) : undefined,
	};
}

/** Daily guard: count agy conversation DBs touched since the start of `day`. */
export function countRecentConversations(conversationsDir: string, day: Date): number {
	const since = new Date(day);
	since.setHours(0, 0, 0, 0);
	try {
		return readdirSync(conversationsDir)
			.filter((f) => f.endsWith('.db'))
			.filter((f) => statSync(`${conversationsDir}/${f}`).mtime >= since)
			.length;
	} catch {
		return 0;
	}
}
