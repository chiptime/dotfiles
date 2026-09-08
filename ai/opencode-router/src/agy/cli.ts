/**
 * CLI entry for `agy-explore --input req.json`. Composes the Phase-1 typed
 * backend (no routing logic here — classification/quota live in src/agy/*);
 * this module owns result.json (+ metrics) writes. Raw exit codes are never
 * a success signal: the router reads result.json / stdout JSON.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { buildResult, parseExploreRequest, type ExploreRequest, type ExploreResult } from './outcomes';
import { depsFor, runExploration } from './backend';
import { appendMetrics, recordFromResult } from './metrics';

export interface CliOptions {
	inputPath: string;
	workdir?: string;
	openspecRoot?: string;
	metricsPath?: string;
	forceNativeFile?: string;
	resultPath?: string;
	agyBin?: string;
	quotaFile?: string;
	timeoutMs?: number;
	env?: Record<string, string>;
}

const NO_RECEIPT = (store: string) => ({ store, wroteOpenspec: false, engramRequired: false });

function binResolvable(bin: string, env: Record<string, string>): boolean {
	if (bin.includes('/')) return existsSync(bin);
	return (env.PATH ?? '').split(':').some((d) => existsSync(`${d}/${bin}`));
}

export async function runCli(opts: CliOptions): Promise<ExploreResult & { resultPath: string }> {
	const env = { ...process.env, ...opts.env } as Record<string, string>;
	let req: ExploreRequest | null = null;
	try {
		req = parseExploreRequest(JSON.parse(readFileSync(opts.inputPath, 'utf8')));
	} catch {
		req = null;
	}
	const workdir = opts.workdir ?? `${homedir()}/.cache/ai-stack/agy-explore/${req?.change ?? 'invalid'}/${Date.now()}`;
	const resultPath = opts.resultPath ?? `${workdir}/result.json`;
	const write = (res: ExploreResult) => {
		mkdirSync(workdir, { recursive: true });
		writeFileSync(resultPath, JSON.stringify(res, null, '\t') + '\n');
		if (req) appendMetrics(opts.metricsPath ?? `${homedir()}/.config/ai-stack/state/agy-explore-metrics.jsonl`, recordFromResult(res, req, Number(env.AGY_ROUTER_TOKENS ?? 0) || 0));
		return { ...res, resultPath };
	};
	if (!req) return write(buildResult('task_failure', { elapsedMs: 0, reason: 'invalid_request', receipt: NO_RECEIPT('none') }));
	const forceFile = opts.forceNativeFile ?? `${homedir()}/.config/ai-stack/force-native`;
	if (env.AI_STACK_SDD_EXPLORE === 'native' || existsSync(forceFile)) {
		return write(buildResult('transient_unavailable', { elapsedMs: 0, reason: 'force_native', receipt: NO_RECEIPT(req.store) }));
	}
	const bin = opts.agyBin ?? env.AGY_BIN ?? 'agy';
	if (!binResolvable(bin, env)) {
		return write(buildResult('transient_unavailable', { elapsedMs: 0, reason: 'agy_absent', receipt: NO_RECEIPT(req.store) }));
	}
	// AGY_EXPLORE_TIMEOUT_MS is the single budget knob: CLI inner cap and the
	// plugin's outer wait both derive from it (outer adds a grace buffer).
	// Default 1200s (20 min): flash-high's heaviest measured run was 173s, but
	// dense briefs with long print waits deserve the full window.
	const res = await runExploration(req, depsFor(req, workdir, { agyBin: bin, quotaFile: opts.quotaFile ?? env.AI_QUOTAS_FILE, openspecRoot: opts.openspecRoot, timeoutMs: opts.timeoutMs ?? (Number(env.AGY_EXPLORE_TIMEOUT_MS) || 1_200_000) }));
	return write(res);
}

async function main() {
	const i = process.argv.indexOf('--input');
	if (i === -1 || !process.argv[i + 1]) {
		console.error('usage: agy-explore --input req.json');
		process.exit(2);
	}
	const res = await runCli({ inputPath: process.argv[i + 1] });
	console.log(JSON.stringify(res));
}

if (import.meta.main) void main();
