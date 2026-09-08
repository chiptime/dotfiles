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
}

/**
 * Build the agy print-mode argv. NOTE: no --add-dir beyond the workdir ever;
 * agy runs with skip-permissions so any added dir would be writable.
 */
export function buildAgyArgs(opts: SpawnOptions): string[] {
	const args = ['--print', opts.prompt, '--add-dir', opts.workdir, '--dangerously-skip-permissions'];
	// agy's print-mode client wait defaults to 5m0s; without an explicit value long
	// explorations die at 300s while our budgets (600s CLI / 630s plugin) never fire.
	// Derive the flag from timeoutMs so it fires slightly BEFORE our spawnSync
	// timeout, which stays strictly larger and remains the outer killer.
	const secs = Math.max(1, Math.floor((opts.timeoutMs - 10_000) / 1000));
	args.push('--print-timeout', `${secs}s`);
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
