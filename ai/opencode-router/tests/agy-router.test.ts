/**
 * TDD tests for the provider-neutral agy exploration backend.
 * Source of truth: dotfiles `ai/opencode-router` (moved out of the ai-stack
 * VPS repo — this tree owns src, config, rendered override, and launcher).
 * Covers outcome classification, fallback policy, quota selection, prompt
 * contract, rendered-config integrity, and CLI smoke runs against a temp HOME.
 */
import { afterAll, describe, expect, test } from 'bun:test';
import { appendFileSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { mkdtemp } from 'node:fs/promises';
import {
	EXPLORE_REQUEST_SCHEMA,
	EXPLORE_RESULT_SCHEMA,
	buildResult,
	classifyRun,
	isFallbackAllowed,
	parseExploreRequest,
	type Outcome,
	type RunSignal,
} from '../src/agy/outcomes';
import {
	decidePool,
	hintActive,
	isStale,
	parseSnapshot,
	parseSnapshotDir,
	poolForModel,
	readHint,
	writeHint,
	type QuotaHint,
	type QuotaSnapshot,
} from '../src/agy/quota';
import { detectMutation, lintSections, REQUIRED_SECTIONS, validateExploration } from '../src/agy/validate';
import { persistExploration } from '../src/agy/persist';
import { buildAgyArgs, countRecentConversations, parseAgyEnvelope, runAgy, type SpawnRun } from '../src/agy/spawn';
import { buildExplorationPrompt, depsFor, runExploration, type BackendDeps } from '../src/agy/backend';
import { runCli, type CliOptions } from '../src/agy/cli';
import { appendMetrics, parseMetrics, recordFromResult, type MetricsRecord } from '../src/agy/metrics';
import { savingsReport, summarize } from '../src/agy/report';
import { renderTemplate } from '../src/agy/render';

const ROOT = `${import.meta.dir}/..`;
const TEMPLATE = `${ROOT}/config/opencode-router.template.json`;
const PROMPT_FILE = `${ROOT}/config/prompts/sdd-explore-router.md`;
const RENDERED = `${ROOT}/opencode-router.json`;
const CLI = `${ROOT}/src/agy/cli.ts`;
const LAUNCHER = `${ROOT}/bin/opencode-web.sh`;
const sh = (cmd: string, env: Record<string, string> = {}) => {
	const r = Bun.spawnSync(['bash', '-c', cmd], { env: { ...process.env, ...env } });
	return { exitCode: r.exitCode, stdout: r.stdout.toString(), stderr: r.stderr.toString() };
};

const STUB = `${import.meta.dir}/helpers/stub-agy.sh`;

/** Shared CLI integration harness: tmp repo/snapshot/req + runCli wiring. */
async function cliSetup(stubMode: string, reqOver: Record<string, unknown> = {}) {
	const root = await mkdtemp('/tmp/agy-cli-');
	const repo = `${root}/repo`;
	const change = `chg-${Math.random().toString(36).slice(2, 8)}`;
	await Bun.write(`${root}/snapshot.json`, JSON.stringify({ active_model: 'Claude Sonnet 4.6', quota_gemini_5h: { remaining_fraction: 0.9, reset_time: '2099-01-01T00:00:00Z' }, quota_gemini_weekly: { remaining_fraction: 0.9 }, quota_3p_5h: { remaining_fraction: 0.9, reset_time: '2099-01-01T00:00:00Z' }, quota_3p_weekly: { remaining_fraction: 0.9 } }));
	const input = `${root}/req.json`;
	await Bun.write(input, JSON.stringify({ schema: 'agy-explore/req@1', change, store: 'openspec', repo, brief: 'explore the router seam', model: 'Gemini 3.7 Flash (High)', ...reqOver }));
	process.env.AGY_STUB_MODE = stubMode;
	return {
		root, repo, change, input, workdir: `${root}/run`,
		run: (o: Partial<CliOptions> = {}) => runCli({ inputPath: input, workdir: `${root}/run`, openspecRoot: `${root}/openspec`, quotaFile: `${root}/snapshot.json`, agyBin: STUB, timeoutMs: 5000, metricsPath: `${root}/metrics.jsonl`, forceNativeFile: `${root}/force-native`, ...o }),
	};
}

describe('unit: outcomes — classify run signals', () => {
	const cases: Array<[string, RunSignal, Outcome, string]> = [
		['missing binary maps to transient agy_absent', { exitCode: null, spawnError: 'ENOENT' }, 'transient_unavailable', 'agy_absent'],
		['timeout flag maps to timeout', { exitCode: null, timedOut: true }, 'timeout', 'timeout'],
		['exit 124 from timeout(1) maps to timeout', { exitCode: 124, log: '' }, 'timeout', 'timeout'],
		['auth/captcha log maps to auth_captcha', { exitCode: 1, log: 'agent hit a CAPTCHA wall; authentication required' }, 'auth_captcha', 'auth_or_captcha'],
		['quota log + nonzero exit maps to quota_unavailable', { exitCode: 1, log: '429 quota exceeded: RESOURCE_EXHAUSTED' }, 'quota_unavailable', 'quota_exhausted'],
		['exit-code corroboration: quota word on clean exit is NOT unavailability', { exitCode: 0, artifactBytes: 0, log: '429 quota exceeded' }, 'artifact_validation_failure', 'artifact_missing_or_empty'],
		['exit-code corroboration: transient word on clean exit is NOT unavailability', { exitCode: 0, artifactBytes: 0, log: 'server error 503, service overloaded, connection refused' }, 'artifact_validation_failure', 'artifact_missing_or_empty'],
		['outage log + nonzero exit maps to transient_unavailable', { exitCode: 1, log: 'server error 503, service overloaded, connection refused' }, 'transient_unavailable', 'provider_outage'],
		['agy print-wait timeout line + nonzero exit maps to timeout', { exitCode: 1, log: 'Error: timeout waiting for response' }, 'timeout', 'agy_print_wait_timeout'],
		['other nonzero exit maps to task_failure', { exitCode: 2, log: 'usage: agy <prompt>' }, 'task_failure', 'nonzero_exit'],
		['empty artifact maps to artifact_validation_failure', { exitCode: 0, log: 'finished cleanly', artifactBytes: 0 }, 'artifact_validation_failure', 'artifact_missing_or_empty'],
		['clean run with artifact maps to success', { exitCode: 0, log: 'wrote exploration.md', artifactBytes: 412 }, 'success', 'ok'],
		['quota word does not override artifact-backed success', { exitCode: 0, artifactBytes: 412, log: '429 quota exceeded: RESOURCE_EXHAUSTED' }, 'success', 'ok'],
		['auth word does not override artifact-backed success', { exitCode: 0, artifactBytes: 412, log: 'captcha wall; authentication required' }, 'success', 'ok'],
	];
	for (const [name, signal, outcome, reason] of cases) {
		test(name, () => {
			const got = classifyRun(signal);
			expect(got.outcome).toBe(outcome);
			expect(got.reason).toBe(reason);
		});
	}

	test('auth markers take precedence over quota markers', () => {
		expect(classifyRun({ exitCode: 1, log: '429 rate limit AND captcha challenge' }).outcome).toBe('auth_captcha');
	});

	test('artifact-backed success outranks auth and quota markers', () => {
		expect(classifyRun({ exitCode: 0, artifactBytes: 412, log: '429 rate limit AND captcha challenge' }).outcome).toBe('success');
	});

	test('timeout takes precedence over log markers', () => {
		expect(classifyRun({ exitCode: 124, log: 'quota exceeded', timedOut: true }).outcome).toBe('timeout');
	});

	test('agy print-wait signature outranks quota/transient markers on a failed run', () => {
		const cls = classifyRun({ exitCode: 1, log: 'Error: timeout waiting for response (429 quota exceeded)' });
		expect(cls).toEqual({ outcome: 'timeout', reason: 'agy_print_wait_timeout' });
	});

	test('agy print-wait timeout is recoverable: buildResult derives fallbackAllowed=true', () => {
		const cls = classifyRun({ exitCode: 1, log: 'Error: timeout waiting for response' });
		const res = buildResult(cls.outcome, { elapsedMs: 0, reason: cls.reason, receipt: { store: 'none', wroteOpenspec: false, engramRequired: false } });
		expect(res.outcome).toBe('timeout');
		expect(res.fallbackAllowed).toBe(true);
	});

	test('exit-code corroboration: quota/transient log noise on a clean exit never allows fallback', () => {
		expect(isFallbackAllowed(classifyRun({ exitCode: 0, artifactBytes: 0, log: '429 quota exceeded' }).outcome)).toBe(false);
		expect(isFallbackAllowed(classifyRun({ exitCode: 0, artifactBytes: 0, log: '503 service unavailable' }).outcome)).toBe(false);
	});

	test('exit-code corroboration: the same logs with a nonzero exit do allow fallback', () => {
		expect(isFallbackAllowed(classifyRun({ exitCode: 1, log: '429 quota exceeded' }).outcome)).toBe(true);
		expect(isFallbackAllowed(classifyRun({ exitCode: 1, log: '503 service unavailable' }).outcome)).toBe(true);
	});

	test('envelope ERROR + timeout error wins even when the log regex ALSO matches', () => {
		const cls = classifyRun({
			exitCode: 1,
			log: 'Error: timeout waiting for response',
			envelope: { status: 'ERROR', error: 'timeout waiting for response', conversation_id: 'conv-1' },
		});
		expect(cls).toEqual({ outcome: 'timeout', reason: 'agy_print_wait_timeout' });
	});

	test('envelope ERROR + timeout error classifies with NO log marker at all', () => {
		const cls = classifyRun({ exitCode: 1, log: '', envelope: { status: 'ERROR', error: 'timeout waiting for response' } });
		expect(cls).toEqual({ outcome: 'timeout', reason: 'agy_print_wait_timeout' });
	});

	test('envelope ERROR with a non-timeout error + clean log falls to task_failure', () => {
		const cls = classifyRun({ exitCode: 1, log: '', envelope: { status: 'ERROR', error: 'model refused the task' } });
		expect(cls).toEqual({ outcome: 'task_failure', reason: 'nonzero_exit' });
	});

	test('envelope ERROR still yields to AUTH markers in the log', () => {
		const cls = classifyRun({ exitCode: 1, log: 'captcha challenge; authentication required', envelope: { status: 'ERROR', error: 'captcha required' } });
		expect(cls).toEqual({ outcome: 'auth_captcha', reason: 'auth_or_captcha' });
	});

	test('envelope SUCCESS never shortcuts the artifact-backed success rule', () => {
		expect(classifyRun({ exitCode: 0, artifactBytes: 0, log: '', envelope: { status: 'SUCCESS' } })).toEqual({ outcome: 'artifact_validation_failure', reason: 'artifact_missing_or_empty' });
	});
});

describe('unit: outcomes — fallback policy', () => {
	const fallbackCases: Array<[Outcome, boolean]> = [
		['success', false],
		['quota_unavailable', true],
		['transient_unavailable', true],
		['auth_captcha', false],
		['timeout', true],
		['task_failure', false],
		['artifact_validation_failure', false],
	];
	for (const [outcome, allowed] of fallbackCases) {
		test(`${outcome} fallbackAllowed=${allowed}`, () => {
			expect(isFallbackAllowed(outcome)).toBe(allowed);
		});
	}
});

describe('unit: outcomes — typed result builder and schemas', () => {
	test('result carries versioned schema and computed fallbackAllowed', () => {
		const res = buildResult('timeout', {
			elapsedMs: 1200,
			reason: 'timeout',
			receipt: { store: 'none', wroteOpenspec: false, engramRequired: false },
		});
		expect(res.schema).toBe('agy-explore/res@1');
		expect(res.outcome).toBe('timeout');
		expect(res.fallbackAllowed).toBe(true);
		expect(res.elapsedMs).toBe(1200);
	});

	test('success result never allows fallback', () => {
		const res = buildResult('success', {
			elapsedMs: 50,
			receipt: { store: 'openspec', wroteOpenspec: true, engramRequired: false },
		});
		expect(res.fallbackAllowed).toBe(false);
	});

	test('buildResult passes conversationId and usage through to the typed result', () => {
		const usage = { input_tokens: 1, output_tokens: 2, thinking_tokens: 0, cache_read_tokens: 0, total_tokens: 3 };
		const res = buildResult('timeout', {
			elapsedMs: 5,
			reason: 'agy_print_wait_timeout',
			receipt: { store: 'none', wroteOpenspec: false, engramRequired: false },
			conversationId: 'conv-9',
			usage,
		});
		expect(res.conversationId).toBe('conv-9');
		expect(res.usage).toEqual(usage);
	});

	test('buildResult omits envelope observability when not provided', () => {
		const res = buildResult('task_failure', { elapsedMs: 0, reason: 'invalid_request', receipt: { store: 'none', wroteOpenspec: false, engramRequired: false } });
		expect(res.conversationId).toBeUndefined();
		expect(res.usage).toBeUndefined();
	});

	test('schema constants are exactly the v1 literals', () => {
		expect(EXPLORE_REQUEST_SCHEMA).toBe('agy-explore/req@1');
		expect(EXPLORE_RESULT_SCHEMA).toBe('agy-explore/res@1');
	});

	test('parseExploreRequest accepts a valid v1 request', () => {
		const req = parseExploreRequest({
			schema: 'agy-explore/req@1',
			change: 'antigravity-sdd-explore-router',
			store: 'hybrid',
			repo: '/tmp/repo',
			brief: 'Explore routing options',
			model: 'Gemini 3.7 Flash (High)',
		});
		expect(req?.store).toBe('hybrid');
		expect(req?.change).toBe('antigravity-sdd-explore-router');
	});

	test('parseExploreRequest rejects wrong schema and bad store', () => {
		expect(parseExploreRequest({ schema: 'agy-explore/req@2', change: 'x', store: 'engram', repo: '/r', brief: 'b', model: 'm' })).toBeNull();
		expect(parseExploreRequest({ schema: 'agy-explore/req@1', change: 'x', store: 'memgraph', repo: '/r', brief: 'b', model: 'm' })).toBeNull();
	});
});

describe('unit: quota — pool by requested model, staleness, thresholds, hint cache', () => {
	/** Real passive snapshot shape from ~/.config/ai-quotas/gemini.json (fractions replaced). */
	const rawSnapshot = (gemini5h: number, geminiWeekly: number, tp5h = 1, resetIn = '2099-01-01T00:00:00Z') => ({
		active_model: 'Claude Sonnet 4.6',
		quota_gemini_5h: { remaining_fraction: gemini5h, remaining_percentage: gemini5h * 100, reset_in_seconds: 3600, reset_time: resetIn },
		quota_gemini_weekly: { remaining_fraction: geminiWeekly },
		quota_3p_5h: { remaining_fraction: tp5h, remaining_percentage: tp5h * 100, reset_in_seconds: 3600, reset_time: resetIn },
		quota_3p_weekly: { remaining_fraction: 1 },
	});
	test('pool selected by requested model name, not active_model', () => {
		expect(poolForModel('Gemini 3.7 Flash (High)')).toBe('gemini');
		expect(poolForModel('gemini-2.5-pro')).toBe('gemini');
		expect(poolForModel('Claude Sonnet 4.6')).toBe('3p');
		expect(poolForModel('GPT-5')).toBe('3p');
	});
	test('parseSnapshot reads the passive snapshot shape', () => {
		const snap = parseSnapshot(rawSnapshot(0.96, 0.97));
		expect(snap?.pools.gemini.fiveHour).toBe(0.96);
		expect(snap?.pools.gemini.weekly).toBe(0.97);
		expect(snap?.pools['3p'].fiveHour).toBe(1);
		expect(snap?.activeModel).toBe('Claude Sonnet 4.6');
	});
	test('parseSnapshot rejects garbage input', () => {
		expect(parseSnapshot('not json at all')).toBeNull();
		expect(parseSnapshot({ provider: 'antigravity' })).toBeNull();
	});
	test('healthy pool within threshold is allowed', () => {
		const snap = parseSnapshot(rawSnapshot(0.96, 0.97)) as QuotaSnapshot;
		expect(decidePool(snap, 'Gemini 3.7 Flash (High)')).toMatchObject({ pool: 'gemini', allowed: true });
	});
	test('exhausted 5h window blocks the pool', () => {
		const snap = parseSnapshot(rawSnapshot(0.03, 0.9)) as QuotaSnapshot;
		const d = decidePool(snap, 'Gemini 3.7 Flash (High)');
		expect(d.allowed).toBe(false);
		expect(d.reason).toBe('threshold_exhausted');
	});
	test('exhausted weekly budget blocks the pool', () => {
		const snap = parseSnapshot(rawSnapshot(0.9, 0.02)) as QuotaSnapshot;
		expect(decidePool(snap, 'Gemini 3.7 Flash (High)').allowed).toBe(false);
	});
	test('gemini exhaustion does not block a 3p request', () => {
		const snap = parseSnapshot(rawSnapshot(0.01, 0.01)) as QuotaSnapshot;
		expect(decidePool(snap, 'Claude Sonnet 4.6')).toMatchObject({ pool: '3p', allowed: true });
	});
	test('stale snapshot (reset passed) allows one real attempt', () => {
		const snap = parseSnapshot(rawSnapshot(0.01, 0.01, 1, '2020-01-01T00:00:00Z')) as QuotaSnapshot;
		const d = decidePool(snap, 'Gemini 3.7 Flash (High)');
		expect(d.allowed).toBe(true);
		expect(d.reason).toBe('stale_snapshot');
	});
	test('isStale is pool-scoped and reset-time driven', () => {
		const snap = parseSnapshot(rawSnapshot(0.5, 0.5, 0.5, '2020-01-01T00:00:00Z')) as QuotaSnapshot;
		expect(isStale(snap, 'gemini', new Date('2021-01-01T00:00:00Z'))).toBe(true);
		expect(isStale(snap, 'gemini', new Date('2019-01-01T00:00:00Z'))).toBe(false);
	});
	test('hint roundtrip: write, read back, expire after reset_time', async () => {
		const dir = await mkdtemp('/tmp/agy-hint-');
		const path = `${dir}/router-quota-hint.json`;
		const hint: QuotaHint = { schema: 'agy-explore/quota-hint@1', pool: 'gemini', blocked: true, resetTime: '2099-01-01T00:00:00Z', savedAt: '2026-08-20T00:00:00Z' };
		writeHint(path, hint);
		expect(readHint(path)).toEqual(hint);
		expect(readHint(`${dir}/missing.json`)).toBeNull();
		expect(hintActive(hint, new Date('2098-01-01T00:00:00Z'))).toBe(true);
		expect(hintActive(hint, new Date('2100-01-01T00:00:00Z'))).toBe(false);
	});
});

describe('unit: quota — statusline v2 directory', () => {
	/** Exact bytes of the 4 files ~/.local/state/ai-quotas/ writes (captured 2026-08-20, statusline v2). */
	const V2_GEMINI_5H = `{
  "provider": "gemini",
  "kind": "window",
  "used": 3.68,
  "limit": 100,
  "unit": "percent",
  "label": "5h window",
  "display_name": "Google Gemini",
  "resets_at": "2026-08-20T21:14:05Z",
  "fetched_at": "2026-08-20T16:58:14Z",
  "source": "local-log"
}
`;
	const V2_GEMINI_WEEKLY = `{
  "provider": "gemini",
  "kind": "window",
  "used": 4.01,
  "limit": 100,
  "unit": "percent",
  "label": "Weekly",
  "display_name": "Google Gemini",
  "resets_at": "2026-08-24T10:11:30Z",
  "fetched_at": "2026-08-20T16:58:14Z",
  "source": "local-log"
}
`;
	const V2_3P_5H = `{
  "provider": "gemini",
  "kind": "window",
  "used": 0,
  "limit": 100,
  "unit": "percent",
  "label": "3P 5h window",
  "display_name": "Google Gemini",
  "resets_at": "2026-08-20T21:53:45Z",
  "fetched_at": "2026-08-20T16:58:14Z",
  "source": "local-log"
}
`;
	const V2_3P_WEEKLY = `{
  "provider": "gemini",
  "kind": "window",
  "used": 0,
  "limit": 100,
  "unit": "percent",
  "label": "3P Weekly",
  "display_name": "Google Gemini",
  "resets_at": "2026-08-27T16:53:45Z",
  "fetched_at": "2026-08-20T16:58:14Z",
  "source": "local-log"
}
`;
	const V2_ALL: Record<string, string> = { 'gemini-5h.json': V2_GEMINI_5H, 'gemini-weekly.json': V2_GEMINI_WEEKLY, 'gemini-3p-5h.json': V2_3P_5H, 'gemini-3p-weekly.json': V2_3P_WEEKLY };
	/** Deterministic synthetic v2 file (default resets in 2099 so decide() tests never see wall-clock staleness). */
	const v2File = (over: Record<string, unknown> = {}) =>
		JSON.stringify({ provider: 'gemini', kind: 'window', used: 0, limit: 100, unit: 'percent', label: 'window', display_name: 'Google Gemini', resets_at: '2099-01-01T00:00:00Z', fetched_at: '2026-08-20T16:58:14Z', source: 'local-log', ...over });
	async function v2Dir(files: Record<string, string> = V2_ALL) {
		const dir = await mkdtemp('/tmp/agy-quota-v2-');
		for (const [name, text] of Object.entries(files)) await Bun.write(`${dir}/${name}`, text);
		return dir;
	}
	const FRESH = new Date('2026-08-20T17:00:00Z');

	test('parseSnapshotDir maps filenames to pools/windows and converts percentages to fractions', async () => {
		const snap = parseSnapshotDir(await v2Dir());
		expect(snap?.pools.gemini.fiveHour).toBeCloseTo(0.9632, 10);
		expect(snap?.pools.gemini.weekly).toBeCloseTo(0.9599, 10);
		expect(snap?.pools['3p'].fiveHour).toBe(1);
		expect(snap?.pools['3p'].weekly).toBe(1);
		expect(snap?.pools.gemini.resetTime).toBe('2026-08-20T21:14:05Z');
		expect(snap?.pools['3p'].resetTime).toBe('2026-08-20T21:53:45Z');
		expect(snap?.updatedAt).toBe('2026-08-20T16:58:14Z');
	});
	test('remaining fraction math: used 13.57% ⇒ 0.8643; clamped to [0, 1]', async () => {
		const dir = await v2Dir({
			'gemini-5h.json': v2File({ used: 13.57, label: '5h window' }),
			'gemini-weekly.json': v2File({ used: 4.01, label: 'Weekly' }),
			'gemini-3p-5h.json': v2File({ used: 150, label: '3P 5h window' }),
			'gemini-3p-weekly.json': v2File({ used: -10, label: '3P Weekly' }),
		});
		const snap = parseSnapshotDir(dir);
		expect(snap?.pools.gemini.fiveHour).toBeCloseTo(0.8643, 10);
		expect(snap?.pools['3p'].fiveHour).toBe(0);
		expect(snap?.pools['3p'].weekly).toBe(1);
	});
	test('filename — not the per-file provider field — decides the pool', async () => {
		for (const t of [V2_GEMINI_5H, V2_GEMINI_WEEKLY, V2_3P_5H, V2_3P_WEEKLY]) expect(JSON.parse(t).provider).toBe('gemini');
		const snap = parseSnapshotDir(await v2Dir()) as QuotaSnapshot;
		expect(snap.pools['3p'].resetTime).toBe('2026-08-20T21:53:45Z');
		expect(decidePool(snap, 'Claude Sonnet 4.6', { now: FRESH })).toMatchObject({ pool: '3p', allowed: true });
	});
	test('missing weekly file degrades only that pool: decidePool grants the single stale attempt', async () => {
		const { 'gemini-weekly.json': _omit, ...rest } = V2_ALL;
		const snap = parseSnapshotDir(await v2Dir(rest));
		expect(snap).not.toBeNull();
		expect(decidePool(snap as QuotaSnapshot, 'Gemini 3.7 Flash (High)', { now: FRESH })).toMatchObject({ pool: 'gemini', allowed: true, reason: 'stale_snapshot' });
		expect(decidePool(snap as QuotaSnapshot, 'Claude Sonnet 4.6', { now: FRESH })).toMatchObject({ pool: '3p', allowed: true, reason: 'within_threshold' });
	});
	test('decidePool on captured v2 data: fresh ⇒ within_threshold, past reset ⇒ stale per pool', async () => {
		const snap = parseSnapshotDir(await v2Dir()) as QuotaSnapshot;
		expect(decidePool(snap, 'Gemini 3.7 Flash (High)', { now: FRESH })).toMatchObject({ allowed: true, reason: 'within_threshold', resetTime: '2026-08-20T21:14:05Z' });
		const afterGeminiReset = new Date('2026-08-20T21:30:00Z');
		expect(decidePool(snap, 'Gemini 3.7 Flash (High)', { now: afterGeminiReset })).toMatchObject({ allowed: true, reason: 'stale_snapshot' });
		expect(decidePool(snap, 'Claude Sonnet 4.6', { now: afterGeminiReset })).toMatchObject({ pool: '3p', allowed: true, reason: 'within_threshold' });
	});
	test('exhausted v2 percentages block the pool through the new reader', async () => {
		const dir = await v2Dir({
			'gemini-5h.json': v2File({ used: 99.9, resets_at: '2099-01-01T00:00:00Z' }),
			'gemini-weekly.json': v2File({ used: 0 }),
			'gemini-3p-5h.json': v2File({ used: 0 }),
			'gemini-3p-weekly.json': v2File({ used: 0 }),
		});
		const d = decidePool(parseSnapshotDir(dir) as QuotaSnapshot, 'Gemini 3.7 Flash (High)');
		expect(d).toMatchObject({ pool: 'gemini', allowed: false, reason: 'threshold_exhausted' });
	});
	test('absent or empty directory yields null and depsFor fails open (stale path)', async () => {
		expect(parseSnapshotDir('/tmp/agy-quota-v2-nope')).toBeNull();
		expect(parseSnapshotDir(await v2Dir({}))).toBeNull();
		const req = { schema: 'agy-explore/req@1' as const, change: 'c2', store: 'openspec' as const, repo: '/r', brief: 'b', model: 'Gemini 3.7 Flash (High)' };
		expect(depsFor(req, '/tmp/w', { quotaFile: '/tmp/agy-quota-v2-nope' }).decide()).toMatchObject({ pool: 'gemini', allowed: true, reason: 'stale_snapshot' });
	});
	test('depsFor resolution: directory ⇒ parseSnapshotDir, single file ⇒ parseSnapshot (override chain intact)', async () => {
		const req = { schema: 'agy-explore/req@1' as const, change: 'c3', store: 'openspec' as const, repo: '/r', brief: 'b', model: 'Gemini 3.7 Flash (High)' };
		const future = await v2Dir({ 'gemini-5h.json': v2File({ used: 3.68 }), 'gemini-weekly.json': v2File({ used: 4.01 }), 'gemini-3p-5h.json': v2File({ used: 0 }), 'gemini-3p-weekly.json': v2File({ used: 0 }) });
		expect(depsFor(req, '/tmp/w', { quotaFile: future }).decide()).toMatchObject({ pool: 'gemini', allowed: true, reason: 'within_threshold' });
		const single = await v2Dir();
		await Bun.write(`${single}-old.json`, JSON.stringify({ quota_gemini_5h: { remaining_fraction: 0.9, reset_time: '2099-01-01T00:00:00Z' }, quota_gemini_weekly: { remaining_fraction: 0.9 }, quota_3p_5h: { remaining_fraction: 0.9, reset_time: '2099-01-01T00:00:00Z' }, quota_3p_weekly: { remaining_fraction: 0.9 } }));
		expect(depsFor(req, '/tmp/w', { quotaFile: `${single}-old.json` }).decide()).toMatchObject({ pool: 'gemini', allowed: true, reason: 'within_threshold' });
	});
});

describe('unit: validate (threat) — repo mutation blocks, persists nothing', () => {
	const validArtifact = ['## Exploration: x', '### Current State', '### Affected Areas', '### Approaches', '### Recommendation', '### Risks', '### Ready for Proposal'].join('\n');
	test('lintSections accepts the canonical exploration shape', () => {
		expect(lintSections(validArtifact)).toEqual({ ok: true, missing: [] });
	});
	test('lintSections reports every missing canonical section', () => {
		const r = lintSections('## Exploration: x\n### Current State');
		expect(r.ok).toBe(false);
		expect(r.missing).toEqual(['Affected Areas', 'Approaches', 'Recommendation', 'Risks', 'Ready for Proposal']);
	});
	test('THREAT: porcelain change between pre and post run is a mutation', () => {
		expect(detectMutation('?? openspec/', '?? openspec/\n M src/agy/quota.ts')).toBe(true);
		expect(detectMutation('?? openspec/', '?? openspec/')).toBe(false);
	});
	test('THREAT: mutated repo fails validation and blocks (no fallback)', () => {
		const v = validateExploration({ artifactText: validArtifact, porcelainBefore: 'A', porcelainAfter: 'B' });
		expect(v.ok).toBe(false);
		expect(v.problems).toEqual(['repo_mutated']);
		const c = classifyRun({ exitCode: 0, log: 'ok', artifactBytes: 100 });
		const blocked = !v.ok ? ({ outcome: 'artifact_validation_failure', reason: 'repo_mutated' } as const) : c;
		expect(isFallbackAllowed(blocked.outcome)).toBe(false);
	});
	test('missing sections also fail validation without blocking fallback rules', () => {
		const v = validateExploration({ artifactText: '### Current State', porcelainBefore: 'A', porcelainAfter: 'A' });
		expect(v.ok).toBe(false);
		expect(v.problems).toEqual(['missing_sections:Affected Areas,Approaches,Recommendation,Risks,Ready for Proposal']);
	});
});

describe('unit: persist — exactly-once receipt per store; none persists nothing', () => {
	const artifact = '## Exploration: t\n### Current State\nok';
	async function setup() {
		const root = await mkdtemp('/tmp/agy-persist-');
		return { root, opts: (store: string) => ({ store, change: 'demo-change', artifactText: artifact, openspecRoot: `${root}/openspec`, workdir: `${root}/run` }) };
	}
	test('openspec store writes exploration.md once with receipt', async () => {
		const { root, opts } = await setup();
		const r = persistExploration(opts('openspec') as any);
		expect(r.receipt).toMatchObject({ store: 'openspec', wroteOpenspec: true, engramRequired: false });
		const dest = `${root}/openspec/changes/demo-change/exploration.md`;
		expect(await Bun.file(dest).text()).toBe(artifact);
		expect(JSON.parse(await Bun.file(r.receiptPath).text()).receipt.store).toBe('openspec');
	});
	test('engram store writes NO canonical file; router must mem_save', async () => {
		const { root, opts } = await setup();
		const r = persistExploration(opts('engram') as any);
		expect(r.receipt).toMatchObject({ store: 'engram', wroteOpenspec: false, engramRequired: true });
		const written: string[] = [];
		for await (const f of new Bun.Glob('**/*').scan({ cwd: root })) written.push(f);
		expect(written.filter((f) => f.endsWith('.md'))).toEqual([]);
		expect(written).toContain('run/receipt.json');
	});
	test('hybrid store writes the file AND requires engram', async () => {
		const { root, opts } = await setup();
		const r = persistExploration(opts('hybrid') as any);
		expect(r.receipt).toMatchObject({ store: 'hybrid', wroteOpenspec: true, engramRequired: true });
		expect(await Bun.file(`${root}/openspec/changes/demo-change/exploration.md`).text()).toBe(artifact);
	});
	test('none store persists NOTHING canonical (receipt-only)', async () => {
		const { root, opts } = await setup();
		const r = persistExploration(opts('none') as any);
		expect(r.receipt).toMatchObject({ store: 'none', wroteOpenspec: false, engramRequired: false });
		expect(await Bun.file(`${root}/openspec/changes/demo-change/exploration.md`).exists()).toBe(false);
	});
	test('EXACTLY-ONCE: second persist returns the same receipt and never rewrites', async () => {
		const { root, opts } = await setup();
		const first = persistExploration(opts('openspec') as any);
		const dest = `${root}/openspec/changes/demo-change/exploration.md`;
		await Bun.write(dest, 'TAMPERED');
		const second = persistExploration(opts('openspec') as any);
		expect(second.receipt).toEqual(first.receipt);
		expect(await Bun.file(dest).text()).toBe('TAMPERED');
	});
});

describe('unit: spawn — timeout, workdir-only args, run.log, daily guard', () => {
	test('successful stub run: exit 0, log captured, run.log written in workdir', async () => {
		const dir = await mkdtemp('/tmp/agy-spawn-');
		const stub = `${dir}/stub.sh`;
		await Bun.write(stub, '#!/bin/sh\necho "agy says hi"\nprintf "%s" "$*" > args.txt\nexit 0\n');
		Bun.spawnSync(['chmod', '+x', stub]);
		const r = runAgy({ bin: stub, prompt: 'do it', workdir: dir, timeoutMs: 5000 });
		expect(r.exitCode).toBe(0);
		expect(r.timedOut).toBe(false);
		expect(r.log).toContain('agy says hi');
		expect(r.elapsedMs).toBeGreaterThanOrEqual(0);
		expect(await Bun.file(`${dir}/run.log`).text()).toContain('agy says hi');
	});
	test('WORKDIR-ONLY: args contain the prompt, never an --add-dir repo path', async () => {
		const dir = await mkdtemp('/tmp/agy-spawn-');
		const stub = `${dir}/stub.sh`;
		await Bun.write(stub, '#!/bin/sh\nprintf "%s" "$*" > "$0.args"\ncat "$0.args" > /dev/null\nexit 0\n');
		Bun.spawnSync(['chmod', '+x', stub]);
		runAgy({ bin: stub, prompt: 'explore briefly', workdir: dir, timeoutMs: 5000 });
		const passed = await Bun.file(`${stub}.args`).text();
		expect(passed).toContain('explore briefly');
		// Containment invariant: every --add-dir target must be the workdir itself; the repo (or any other dir) is never exposed.
		const addDirs = passed.split(' ').filter((_, i, a) => a[i - 1] === '--add-dir');
		expect(addDirs).toEqual([dir]);
		expect(passed).not.toContain('/home/bruno/Code');
	});
	test('stub exceeding timeoutMs is killed and flagged timedOut', async () => {
		const dir = await mkdtemp('/tmp/agy-spawn-');
		const stub = `${dir}/slow.sh`;
		await Bun.write(stub, '#!/bin/sh\nsleep 5\n');
		Bun.spawnSync(['chmod', '+x', stub]);
		const r = runAgy({ bin: stub, prompt: 'x', workdir: dir, timeoutMs: 150 });
		expect(r.timedOut).toBe(true);
		expect(r.exitCode).not.toBe(0);
	});
	test('--model passthrough: appended when provided, absent when missing/empty', async () => {
		const dir = await mkdtemp('/tmp/agy-spawn-');
		const stub = `${dir}/stub.sh`;
		await Bun.write(stub, '#!/bin/sh\nprintf "%s" "$*" > "$0.args"\nexit 0\n');
		Bun.spawnSync(['chmod', '+x', stub]);
		runAgy({ bin: stub, prompt: 'x', workdir: dir, timeoutMs: 5000, model: 'gemini-3.8-flash-high' });
		expect(await Bun.file(`${stub}.args`).text()).toContain('--model gemini-3.8-flash-high');
		runAgy({ bin: stub, prompt: 'x', workdir: dir, timeoutMs: 5000, model: '' });
		expect(await Bun.file(`${stub}.args`).text()).not.toContain('--model');
		runAgy({ bin: stub, prompt: 'x', workdir: dir, timeoutMs: 5000 });
		expect(await Bun.file(`${stub}.args`).text()).not.toContain('--model');
	});
	test('depsFor wiring: req.model reaches the agy argv via run()', async () => {
		const dir = await mkdtemp('/tmp/agy-spawn-');
		const stub = `${dir}/stub.sh`;
		await Bun.write(stub, '#!/bin/sh\nprintf "%s" "$*" > "$0.args"\nexit 0\n');
		Bun.spawnSync(['chmod', '+x', stub]);
		const req = { schema: 'agy-explore/req@1' as const, change: 'model-pass', store: 'openspec' as const, repo: '/r', brief: 'b', model: 'gemini-3.1-pro' };
		depsFor(req, dir, { agyBin: stub, timeoutMs: 5000 }).run();
		expect(await Bun.file(`${stub}.args`).text()).toContain('--model gemini-3.1-pro');
	});
	test('daily guard counts only conversation DBs from today', async () => {
		const dir = await mkdtemp('/tmp/agy-guard-');
		await Bun.write(`${dir}/a.db`, 'x');
		await Bun.write(`${dir}/b.db`, 'x');
		await Bun.write(`${dir}/notes.txt`, 'x');
		const now = new Date();
		expect(countRecentConversations(dir, now)).toBe(2);
		expect(countRecentConversations(`${dir}/missing`, now)).toBe(0);
	});
});

describe('unit: spawn — buildAgyArgs derives the print-wait deadline', () => {
	test('600s budget yields --print-timeout 590s (fires before our spawnSync timeout)', () => {
		const args = buildAgyArgs({ bin: 'agy', prompt: 'p', workdir: '/w', timeoutMs: 600_000 });
		const i = args.indexOf('--print-timeout');
		expect(i).toBeGreaterThan(-1);
		expect(args[i + 1]).toBe('590s');
	});
	test('tiny budgets clamp to the 1s floor', () => {
		const args = buildAgyArgs({ bin: 'agy', prompt: 'p', workdir: '/w', timeoutMs: 5000 });
		const i = args.indexOf('--print-timeout');
		expect(args[i + 1]).toBe('1s');
	});
	test('flag order: after --dangerously-skip-permissions, value flags together, optional --model pair last', () => {
		expect(buildAgyArgs({ bin: 'agy', prompt: 'p', workdir: '/w', timeoutMs: 630_000, model: 'm1' })).toEqual([
			'--print', 'p', '--add-dir', '/w', '--dangerously-skip-permissions', '--print-timeout', '620s', '--output-format', 'json', '--model', 'm1',
		]);
		expect(buildAgyArgs({ bin: 'agy', prompt: 'p', workdir: '/w', timeoutMs: 600_000 })).not.toContain('--model');
	});
	test('print-timeout never echoes the raw spawn timeout', () => {
		const args = buildAgyArgs({ bin: 'agy', prompt: 'p', workdir: '/w', timeoutMs: 600_000 });
		expect(args.join(' ')).not.toContain('600000');
		expect(args.join(' ')).not.toContain('--print-timeout 600s');
	});
});

describe('unit: spawn — agy JSON envelope parsing (--output-format json)', () => {
	const envelope = {
		conversation_id: '0f7c1b2e-1111-4aaa-9bbb-2c2c2c2c2c2c',
		status: 'ERROR',
		response: '',
		error: 'timeout waiting for response',
		duration_seconds: 12,
		num_turns: 3,
		usage: { input_tokens: 10, output_tokens: 20, thinking_tokens: 0, cache_read_tokens: 5, total_tokens: 30 },
	};
	test('valid envelope object parses', () => {
		expect(parseAgyEnvelope(JSON.stringify(envelope))).toEqual(envelope);
	});
	test('trailing newline tolerated', () => {
		expect(parseAgyEnvelope(`${JSON.stringify(envelope)}\n`)).toEqual(envelope);
	});
	test('multi-line stdout parses the LAST non-empty line', () => {
		expect(parseAgyEnvelope(`agy: warming up\nnotice: something else\n${JSON.stringify(envelope)}`)).toEqual(envelope);
	});
	test('object without a string status field yields null', () => {
		expect(parseAgyEnvelope('{"conversation_id":"x","usage":{"total_tokens":1}}')).toBeNull();
		expect(parseAgyEnvelope('{"status":42}')).toBeNull();
	});
	test('garbage yields null', () => {
		expect(parseAgyEnvelope('not json at all')).toBeNull();
	});
	test('empty stdout yields null', () => {
		expect(parseAgyEnvelope('')).toBeNull();
		expect(parseAgyEnvelope('  \n \n')).toBeNull();
	});
});

describe('unit: prompt — artifact contract', () => {
	/** Live-e2e lesson (2026-08-21): bare-brief prompts make agy answer on stdout and never write the file. */
	const req = { schema: 'agy-explore/req@1' as const, change: 'agy-live-contract', store: 'openspec' as const, repo: '/repo', brief: 'Explore the agy routing seam: why do live runs fail artifact_missing_or_empty?', model: 'Gemini 3.7 Flash (High)' };
	test('carries the brief verbatim under a ## Brief heading', () => {
		const prompt = buildExplorationPrompt(req);
		expect(prompt).toContain('## Brief');
		expect(prompt).toContain(req.brief);
	});
	test('instructs agy to write the artifact to ./exploration.md', () => {
		expect(buildExplorationPrompt(req)).toContain('./exploration.md');
	});
	test('emits a "### <section>" line for every REQUIRED_SECTIONS entry (no hardcoded list)', () => {
		const prompt = buildExplorationPrompt(req);
		for (const s of REQUIRED_SECTIONS) expect(prompt).toContain(`### ${s}`);
	});
	test('names the change and states stdout is ignored', () => {
		const prompt = buildExplorationPrompt(req);
		expect(prompt).toContain(req.change);
		expect(prompt.toLowerCase()).toContain('stdout is ignored');
	});
	test('verdict + containment: final section ends Yes/No; no other files touched', () => {
		const prompt = buildExplorationPrompt(req);
		expect(prompt).toMatch(/yes or no/i);
		expect(prompt).toMatch(/no other files/i);
	});
});

describe('unit: backend — provider-neutral runExploration seam', () => {
	const validArtifact = ['## Exploration: x', '### Current State', '### Affected Areas', '### Approaches', '### Recommendation', '### Risks', '### Ready for Proposal'].join('\n');
	const req = { schema: 'agy-explore/req@1' as const, change: 'c1', store: 'openspec' as const, repo: '/repo', brief: 'b', model: 'Gemini 3.7 Flash (High)' };
	const okRun: SpawnRun = { exitCode: 0, timedOut: false, log: 'done', elapsedMs: 5 };
	function deps(over: Partial<BackendDeps> & { porcelainCalls?: string[] } = {}): BackendDeps {
		const calls: string[] = over.porcelainCalls ?? ['P', 'P'];
		let persistCalled = 0;
		return {
			decide: over.decide ?? (() => ({ pool: 'gemini' as const, allowed: true, reason: 'within_threshold' })),
			run: over.run ?? (() => okRun),
			readArtifact: over.readArtifact ?? (() => validArtifact),
			persist: over.persist ?? (() => { persistCalled++; return { receipt: { store: 'openspec', wroteOpenspec: true, engramRequired: false }, receiptPath: '/w/receipt.json', artifactDest: '/w/exploration.md', sha256: 'abc' }; }),
			porcelain: () => (calls.length > 1 ? calls.shift()! : calls[0]),
			persistCalledRef: () => persistCalled,
		} as BackendDeps;
	}
	test('success: validated run persists once, returns success result', async () => {
		const d = deps();
		const res = await runExploration(req, d);
		expect(res.outcome).toBe('success');
		expect(res.fallbackAllowed).toBe(false);
		expect(res.artifactPath).toBeDefined();
		expect((d as any).persistCalledRef()).toBe(1);
	});
	test('THREAT end-to-end: repo mutation blocks, persists NOTHING, no fallback', async () => {
		const d = deps({ porcelainCalls: ['P', 'P-MUTATED'] });
		const res = await runExploration(req, d);
		expect(res.outcome).toBe('artifact_validation_failure');
		expect(res.fallbackAllowed).toBe(false);
		expect((d as any).persistCalledRef()).toBe(0);
	});
	test('quota-blocked pool: approved unavailability, agy never runs, fallback allowed', async () => {
		let ran = 0;
		const d = deps({ decide: () => ({ pool: 'gemini' as const, allowed: false, reason: 'threshold_exhausted' }) , run: () => { ran++; return okRun; } });
		const res = await runExploration(req, d);
		expect(res.outcome).toBe('quota_unavailable');
		expect(res.fallbackAllowed).toBe(true);
		expect(ran).toBe(0);
		expect((d as any).persistCalledRef()).toBe(0);
	});

	test('envelope observability: conversationId and usage surface on the success result', async () => {
		const runWithEnvelope: SpawnRun = { ...okRun, envelope: { status: 'SUCCESS', conversation_id: 'conv-1', usage: { input_tokens: 1, output_tokens: 2, thinking_tokens: 0, cache_read_tokens: 0, total_tokens: 3 } } };
		const d = deps({ run: () => runWithEnvelope });
		const res = await runExploration(req, d);
		expect(res.outcome).toBe('success');
		expect(res.conversationId).toBe('conv-1');
		expect(res.usage?.total_tokens).toBe(3);
	});

	test('envelope observability: conversationId surfaces on a failed-run result too', async () => {
		const runWithEnvelope: SpawnRun = { ...okRun, exitCode: 1, envelope: { status: 'ERROR', error: 'timeout waiting for response', conversation_id: 'conv-2' } };
		const d = deps({ run: () => runWithEnvelope });
		const res = await runExploration(req, d);
		expect(res.outcome).toBe('timeout');
		expect(res.reason).toBe('agy_print_wait_timeout');
		expect(res.conversationId).toBe('conv-2');
	});

	test('quota gate early return attaches no envelope observability (no run happened)', async () => {
		const runWithEnvelope: SpawnRun = { ...okRun, envelope: { status: 'SUCCESS', conversation_id: 'conv-never' } };
		const d = deps({ decide: () => ({ pool: 'gemini' as const, allowed: false, reason: 'threshold_exhausted' }), run: () => runWithEnvelope });
		const res = await runExploration(req, d);
		expect(res.outcome).toBe('quota_unavailable');
		expect(res.conversationId).toBeUndefined();
		expect(res.usage).toBeUndefined();
	});
});

describe('integration: cli — stub agy via AGY_BIN (result.json, persistence, preflight)', () => {
	afterAll(() => { delete process.env.AGY_STUB_MODE; delete process.env.AGY_STUB_MUTATE_REPO; });
	test('success: typed result, exploration.md persisted, receipt + result.json written', async () => {
		const s = await cliSetup('success');
		const res = await s.run();
		expect(res.outcome).toBe('success');
		expect(res.fallbackAllowed).toBe(false);
		expect(res.artifactPath).toBe(`${s.root}/openspec/changes/${s.change}/exploration.md`);
		expect(await Bun.file(res.artifactPath!).text()).toContain('### Ready for Proposal');
		expect(JSON.parse(await Bun.file(`${s.workdir}/result.json`).text()).schema).toBe('agy-explore/res@1');
		expect(JSON.parse(await Bun.file(`${s.workdir}/receipt.json`).text()).receipt.store).toBe('openspec');
	});
	test('timeout: killed run classifies timeout and allows fallback', async () => {
		const s = await cliSetup('timeout');
		const res = await s.run({ timeoutMs: 250 });
		expect(res.outcome).toBe('timeout');
		expect(res.fallbackAllowed).toBe(true);
		expect(res.receipt.wroteOpenspec).toBe(false);
	});
	test('auth failure: blocks (no fallback) and persists nothing', async () => {
		const s = await cliSetup('auth');
		const res = await s.run();
		expect(res.outcome).toBe('auth_captcha');
		expect(res.fallbackAllowed).toBe(false);
		expect(await Bun.file(`${s.workdir}/receipt.json`).exists()).toBe(false);
		expect(await Bun.file(`${s.root}/openspec/changes/${s.change}/exploration.md`).exists()).toBe(false);
	});
	test('empty artifact: artifact_validation_failure, blocks, persists nothing', async () => {
		const s = await cliSetup('empty');
		const res = await s.run();
		expect(res.outcome).toBe('artifact_validation_failure');
		expect(res.reason).toBe('artifact_missing_or_empty');
		expect(res.fallbackAllowed).toBe(false);
		expect(await Bun.file(`${s.root}/openspec/changes/${s.change}/exploration.md`).exists()).toBe(false);
	});
	test('THREAT integration: stub mutates the repo → blocks, no persistence', async () => {
		const s = await cliSetup('mutate');
		Bun.spawnSync(['git', 'init', '--quiet', s.repo]);
		process.env.AGY_STUB_MUTATE_REPO = s.repo;
		const res = await s.run();
		expect(res.outcome).toBe('artifact_validation_failure');
		expect(res.reason).toContain('repo_mutated');
		expect(res.fallbackAllowed).toBe(false);
		expect(await Bun.file(`${s.root}/openspec/changes/${s.change}/exploration.md`).exists()).toBe(false);
		expect(await Bun.file(`${s.workdir}/receipt.json`).exists()).toBe(false);
	});
	test('force-native kill switch: agy never spawns, native path signaled', async () => {
		const s = await cliSetup('success');
		await Bun.write(`${s.root}/force-native`, '');
		const res = await s.run();
		expect(res.outcome).toBe('transient_unavailable');
		expect(res.reason).toBe('force_native');
		expect(res.fallbackAllowed).toBe(true);
		expect(await Bun.file(`${s.workdir}/stub-invoked.marker`).exists()).toBe(false);
	});
	test('agy absent: transient agy_absent with fallback allowed', async () => {
		const s = await cliSetup('success');
		const res = await s.run({ agyBin: `${s.root}/no-agy` });
		expect(res.outcome).toBe('transient_unavailable');
		expect(res.reason).toBe('agy_absent');
		expect(res.fallbackAllowed).toBe(true);
	});
	test('missing quota snapshot: one real attempt still allowed (stale path)', async () => {
		const s = await cliSetup('success');
		const res = await s.run({ quotaFile: `${s.root}/missing-snapshot.json` });
		expect(res.outcome).toBe('success');
	});
	test('invalid request: contract failure blocks, result.json still written', async () => {
		const s = await cliSetup('success');
		await Bun.write(s.input, '{"schema":"agy-explore/req@9"}');
		const res = await s.run();
		expect(res.outcome).toBe('task_failure');
		expect(res.reason).toBe('invalid_request');
		expect(res.fallbackAllowed).toBe(false);
		expect(JSON.parse(await Bun.file(`${s.workdir}/result.json`).text()).reason).toBe('invalid_request');
	});
});

describe('unit: metrics — record schema, JSONL sink, parse', () => {
	const req = { schema: 'agy-explore/req@1' as const, change: 'c9', store: 'openspec' as const, repo: '/r', brief: 'b', model: 'Gemini 3.7 Flash (High)' };
	test('recordFromResult maps a success result; fallbackUsed false', () => {
		const rec = recordFromResult(buildResult('success', { elapsedMs: 42, receipt: { store: 'openspec', wroteOpenspec: true, engramRequired: false } }), req, 120);
		expect(rec.schema).toBe('agy-explore/metrics@1');
		expect(rec.outcome).toBe('success');
		expect(rec.change).toBe('c9');
		expect(rec.routerTokens).toBe(120);
		expect(rec.fallbackUsed).toBe(false);
	});
	test('recordFromResult marks approved-unavailability as fallbackUsed', () => {
		expect(recordFromResult(buildResult('timeout', { elapsedMs: 1, receipt: { store: 'none', wroteOpenspec: false, engramRequired: false } }), req).fallbackUsed).toBe(true);
		expect(recordFromResult(buildResult('auth_captcha', { elapsedMs: 1, receipt: { store: 'none', wroteOpenspec: false, engramRequired: false } }), req).fallbackUsed).toBe(false);
	});
	test('appendMetrics + parseMetrics roundtrip; malformed and foreign lines skipped', async () => {
		const dir = await mkdtemp('/tmp/agy-metrics-');
		const path = `${dir}/metrics.jsonl`;
		const a = recordFromResult(buildResult('success', { elapsedMs: 5, receipt: { store: 'none', wroteOpenspec: false, engramRequired: false } }), req);
		const b = recordFromResult(buildResult('timeout', { elapsedMs: 6, receipt: { store: 'none', wroteOpenspec: false, engramRequired: false } }), req);
		appendMetrics(path, a);
		appendMetrics(path, b);
		appendFileSync(path, 'not json\n{"schema":"other@1"}\n');
		const parsed = parseMetrics(await Bun.file(path).text());
		expect(parsed).toHaveLength(2);
		expect(parsed.map((r) => r.outcome)).toEqual(['success', 'timeout']);
	});
});

describe('unit: report — rates, latency, savings delta', () => {
	const rec = (outcome: string, elapsedMs: number, routerTokens = 0): MetricsRecord =>
		({ schema: 'agy-explore/metrics@1', ts: '2026-08-20T00:00:00Z', change: 'c', model: 'm', outcome: outcome as MetricsRecord['outcome'], elapsedMs, routerTokens, fallbackUsed: false });
	const recs = [rec('success', 100, 10), rec('success', 300, 20), rec('timeout', 200, 5), rec('auth_captcha', 50)];
	test('summarize derives exact rates, blocked count, average latency', () => {
		const s = summarize(recs);
		expect(s.runs).toBe(4);
		expect(s.successRate).toBe(0.5);
		expect(s.fallbackRate).toBe(0.25);
		expect(s.blockedCount).toBe(1);
		expect(s.avgElapsedMs).toBe(162.5);
		expect(s.byOutcome).toEqual({ success: 2, timeout: 1, auth_captcha: 1 });
	});
	test('savingsReport computes routed latency delta vs native baseline', () => {
		const s = savingsReport(recs, 400);
		expect(s.routedSuccess).toBe(2);
		expect(s.avgRoutedMs).toBe(200);
		expect(s.latencyDeltaMs).toBe(200);
		expect(s.latencyDeltaPct).toBe(50);
		expect(s.fallbackRuns).toBe(1);
		expect(s.routerTokensTotal).toBe(35);
	});
	test('savingsReport handles zero native baseline without dividing by zero', () => {
		expect(savingsReport(recs, 0).latencyDeltaPct).toBe(0);
	});
});

describe('integration: metrics wiring — CLI appends one line per run', () => {
	afterAll(() => { delete process.env.AGY_STUB_MODE; });
	test('successful CLI run appends exactly one metrics line', async () => {
		const s = await cliSetup('success');
		await s.run();
		const lines = parseMetrics(await Bun.file(`${s.root}/metrics.jsonl`).text());
		expect(lines).toHaveLength(1);
		expect(lines[0].outcome).toBe('success');
		expect(lines[0].change).toBe(s.change);
	});
});

describe('contract: binding — depth/permission/recursion before production binding', () => {
	test('template caps nesting: subagent_depth is exactly 2', async () => {
		expect(JSON.parse(await Bun.file(TEMPLATE).text()).subagent_depth).toBe(2);
	});
	test('router agent inherits the local sdd-explore model while owning the routing prompt', async () => {
		const t = JSON.parse(await Bun.file(TEMPLATE).text());
		expect('model' in t.agent['sdd-explore']).toBe(false);
		expect(t.agent['sdd-explore'].prompt).toBe('@prompt-file');
	});
	test('router task permission allows ONLY sdd-explore-fallback (wildcard deny)', async () => {
		const t = JSON.parse(await Bun.file(TEMPLATE).text());
		expect(t.agent['sdd-explore'].permission.task).toEqual({ '*': 'deny', 'sdd-explore-fallback': 'allow' });
	});
	test('fallback agent exists, denies task entirely, allows nothing else', async () => {
		const t = JSON.parse(await Bun.file(TEMPLATE).text());
		const fb = t.agent['sdd-explore-fallback'];
		expect(fb.tools?.task).toBe(false);
		expect(fb.permission?.task).toEqual({ '*': 'deny' });
	});
	test('renderTemplate inlines the prompt file, marking throwaway prompt vs native fallback', async () => {
		const rendered = renderTemplate(JSON.parse(await Bun.file(TEMPLATE).text()), await Bun.file(PROMPT_FILE).text());
		expect(rendered.agent['sdd-explore'].prompt).toContain('agy-explore --input');
		expect(rendered.agent['sdd-explore-fallback'].prompt).not.toBe('@prompt-file');
		expect(rendered.subagent_depth).toBe(2);
	});
	test('router prompt: <=60 lines, exactly one bash call, fallback delegation rule', async () => {
		const text = await Bun.file(PROMPT_FILE).text();
		const lines = text.split('\n').length;
		expect(lines).toBeLessThanOrEqual(60);
		expect(text.match(/```bash/g)?.length ?? 0).toBe(1);
		expect(text).toContain('agy-explore --input');
		expect(text).toContain('sdd-explore-fallback');
		expect(text).toContain('never nested');
	});
});

describe('contract: rendered config integrity (dotfiles mode)', () => {
	test('committed opencode-router.json deep-equals renderTemplate(config template, prompt)', async () => {
		const template = JSON.parse(await Bun.file(TEMPLATE).text());
		const promptText = await Bun.file(PROMPT_FILE).text();
		const committed = JSON.parse(await Bun.file(RENDERED).text());
		expect(committed).toEqual(renderTemplate(template, promptText));
	});
	test('committed rendered config inherits model ownership and carries the routing contract', async () => {
		const committed = JSON.parse(await Bun.file(RENDERED).text());
		expect(committed.subagent_depth).toBe(2);
		expect('model' in committed.agent['sdd-explore']).toBe(false);
		expect(committed.agent['sdd-explore'].prompt).toContain('agy-explore --input');
	});
});

describe('smoke: CLI end-to-end via bun run (router bash call path), force-native, quota immutability', () => {
	/**
	 * Dotfiles mode: no installer exists. Materialize the temp HOME directly
	 * (snapshot + req) and run the CLI source the binary is compiled from.
	 */
	async function homeFixture() {
		const home = await mkdtemp('/tmp/agy-smoke-');
		const repo = `${home}/repo`;
		const snap = `${home}/snapshot.json`;
		await Bun.write(snap, JSON.stringify({ quota_gemini_5h: { remaining_fraction: 0.9, reset_time: '2099-01-01T00:00:00Z' }, quota_gemini_weekly: { remaining_fraction: 0.9 }, quota_3p_5h: { remaining_fraction: 0.9, reset_time: '2099-01-01T00:00:00Z' }, quota_3p_weekly: { remaining_fraction: 0.9 } }));
		const req = `${home}/req.json`;
		const change = `smoke-${Math.random().toString(36).slice(2, 8)}`;
		await Bun.write(req, JSON.stringify({ schema: 'agy-explore/req@1', change, store: 'openspec', repo, brief: 'smoke the routed path', model: 'Gemini 3.7 Flash (High)' }));
		return { home, repo, snap, req, change };
	}
	const cliRun = (home: string, req: string, mode: string) =>
		sh(`bun run '${CLI}' --input '${req}'`, { HOME: home, AGY_BIN: STUB, AGY_STUB_MODE: mode, AI_QUOTAS_FILE: `${home}/snapshot.json` });
	const resultJson = (r: { stdout: string }) => JSON.parse(r.stdout.trim().split('\n').pop()!);
	const runRoot = (home: string, change: string) => `${home}/.cache/ai-stack/agy-explore/${change}`;
	const runDir = async (home: string, change: string) => {
		for await (const f of new Bun.Glob(`**/result.json`).scan({ cwd: runRoot(home, change) })) return `${runRoot(home, change)}/${f.split('/').slice(0, -1).join('/')}`;
		throw new Error('no result.json');
	};
	test('4.3 routed success persists the artifact + metrics per run', async () => {
		const s = await homeFixture();
		const res = resultJson(cliRun(s.home, s.req, 'success'));
		expect(res.outcome).toBe('success');
		const artifact = `${s.repo}/openspec/changes/${s.change}/exploration.md`;
		expect(await Bun.file(artifact).text()).toContain('### Ready for Proposal');
		const first = await Bun.file(artifact).text();
		await Bun.write(artifact, `${first}\n<!-- once -->`);
		cliRun(s.home, s.req, 'success');
		const rd = await runDir(s.home, s.change);
		const receipt = JSON.parse(await Bun.file(`${rd}/receipt.json`).text());
		expect(receipt.receipt.store).toBe('openspec');
		const lines = parseMetrics(await Bun.file(`${s.home}/.config/ai-stack/state/agy-explore-metrics.jsonl`).text());
		expect(lines.map((l) => l.outcome)).toEqual(['success', 'success']);
	});
	test('4.4 force-native kill switch: agy never spawns', async () => {
		const s = await homeFixture();
		await Bun.write(`${s.home}/.config/ai-stack/force-native`, '');
		const res = resultJson(cliRun(s.home, s.req, 'success'));
		expect(res).toMatchObject({ outcome: 'transient_unavailable', reason: 'force_native', fallbackAllowed: true });
		let markers = 0;
		for await (const _f of new Bun.Glob(`**/stub-invoked.marker`).scan({ cwd: runRoot(s.home, s.change) })) markers++;
		expect(markers).toBe(0);
	});
	test('4.4 rollback: removing the force-native file re-enables routing', async () => {
		const s = await homeFixture();
		const force = `${s.home}/.config/ai-stack/force-native`;
		await Bun.write(force, '');
		expect(resultJson(cliRun(s.home, s.req, 'success'))).toMatchObject({ outcome: 'transient_unavailable', reason: 'force_native' });
		rmSync(force);
		expect(resultJson(cliRun(s.home, s.req, 'success'))).toMatchObject({ outcome: 'success', reason: 'ok' });
	});
	test('4.5 quota snapshot is never modified; metrics record classified outcomes', async () => {
		const s = await homeFixture();
		const before = createHash('sha256').update(await Bun.file(s.snap).text()).digest('hex');
		cliRun(s.home, s.req, 'auth');
		cliRun(s.home, s.req, 'quota');
		const after = createHash('sha256').update(await Bun.file(s.snap).text()).digest('hex');
		expect(after).toBe(before);
		const metrics = parseMetrics(await Bun.file(`${s.home}/.config/ai-stack/state/agy-explore-metrics.jsonl`).text());
		expect(metrics.map((m) => [m.outcome, m.fallbackUsed])).toEqual([['auth_captcha', false], ['quota_unavailable', true]]);
	});
});

describe('contract: launcher fail-closed', () => {
	test('script text: config guard + exit 2 fire before the OPENCODE_CONFIG export; PATH prepends ~/.local/bin', async () => {
		const text = await Bun.file(LAUNCHER).text();
		expect(text).toContain('[ ! -f "$CONFIG" ]');
		expect(text).toContain('exit 2');
		const guard = text.indexOf('[ ! -f "$CONFIG" ]');
		const configExport = text.indexOf('export OPENCODE_CONFIG');
		expect(guard).toBeGreaterThanOrEqual(0);
		expect(configExport).toBeGreaterThan(guard);
		expect(text).toContain('export PATH="$HOME/.local/bin:$PATH"');
	});
	test('missing config exits 2 with "not installed" on stderr — guard fires before any exec', () => {
		const missing = `/tmp/agy-router-not-installed-${Date.now()}.json`;
		const r = sh(`bash '${LAUNCHER}'`, { AI_STACK_ROUTER_CONFIG: missing });
		expect(r.exitCode).toBe(2);
		expect(r.stderr).toContain('not installed');
	});
});
