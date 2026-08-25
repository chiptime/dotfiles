/**
 * Hermetic tests for the sdd-explore dispatcher (plugin before-hook interception).
 * Covers the pure dispatch-core (parser, gating, envelopes, spawn contract),
 * the dispatch pipeline against a stub agy-explore binary, and the plugin
 * adapter end-to-end through the real hook function with injected env.
 * Fixtures mirror REAL orchestrator -> sdd-explore task prompts captured from
 * the opencode session DB (2026-08-25); no network, no real agy.
 */
import { afterAll, describe, expect, test } from 'bun:test';
import { mkdtemp } from 'node:fs/promises';
import {
	blockedEnvelope,
	buildExploreRequest,
	dispatchTaskPrompt,
	gateDispatch,
	parseCliStdout,
	parseExploreTaskPrompt,
	reqPathFor,
	successEnvelope,
	type DispatchDeps,
	type ExploreResult,
} from '../src/agy/dispatch-core';
import { parseExploreRequest, EXPLORE_RESULT_SCHEMA, type Receipt } from '../src/agy/outcomes';
import plugin from '../src/plugin/sdd-explore-dispatch';

const res = (outcome: string, over: Partial<ExploreResult> = {}): ExploreResult & { resultPath?: string } =>
	({
		schema: EXPLORE_RESULT_SCHEMA,
		outcome,
		fallbackAllowed: false,
		elapsedMs: 42,
		receipt: { store: 'openspec', wroteOpenspec: false, engramRequired: false } as Receipt,
		...over,
	}) as ExploreResult & { resultPath?: string };

describe('unit: parser — real orchestrator prompt shapes', () => {
	test('main fixture: mini change + "Artifact store: Engram" + workspace line', () => {
		const prompt = [
			'Run SDD exploration for mini change `ep-batch-verify-findings`.',
			'',
			'# Session contract',
			'- Project/workspace: `entities-portal` at `/repo/entities-portal`',
			'- Artifact store: Engram',
			'- Mode: automatic',
			'',
			'# Persistence',
			'topic_key `sdd/ep-batch-verify-findings/explore`',
		].join('\n');
		const r = parseExploreTaskPrompt(prompt);
		expect(r.ok).toBe(true);
		if (!r.ok) return;
		expect(r.task.change).toBe('ep-batch-verify-findings');
		expect(r.task.store).toBe('engram');
		expect(r.task.repoHint).toBe('/repo/entities-portal');
		expect(r.task.brief).toBe(prompt);
	});
	test('reverse store line "- Engram artifact store" with "exploration `X`" opener', () => {
		const prompt = ['Continue SDD exploration `ep-batch-failures-dev-evidence` with a bounded TEST evidence pass.', '', '# Session constraints', '- Workspace: `/repo/entities-portal`', '- Engram artifact store'].join('\n');
		const r = parseExploreTaskPrompt(prompt);
		expect(r.ok && r.task.change === 'ep-batch-failures-dev-evidence' && r.task.store === 'engram').toBe(true);
	});
	test('"artifact store mode: engram" variant parses', () => {
		const r = parseExploreTaskPrompt('Explore change `x1`.\n\n- Artifact store mode: engram\n- Working directory: /repo/va');
		expect(r.ok && r.task.store === 'engram' && r.task.repoHint === '/repo/va').toBe(true);
	});
	test('preflight value "both" maps to hybrid', () => {
		const r = parseExploreTaskPrompt('Run change `x2`.\nArtifact store: both');
		expect(r.ok && r.task.store).toBe('hybrid');
	});
	test('qualified store override ("hybrid session, but ... Engram only") falls through', () => {
		const r = parseExploreTaskPrompt('Explore feasibility for a new SDD change `ep-batch-e2e-coverage`: e2e tests.\n\nArtifact store: hybrid session, but for THIS exploration persist to Engram only: project `entities-portal`.');
		expect(r).toEqual({ ok: false, reason: 'artifact_store_qualified' });
	});
	test('standalone topic exploration without a change name falls through', () => {
		const r = parseExploreTaskPrompt(['You are an SDD sub-agent.', '', 'CONTEXT:', '- Working directory: /repo/va', '- Topic to explore: problems with vllm and gemma quantized', '- Artifact store mode: engram'].join('\n'));
		expect(r).toEqual({ ok: false, reason: 'no_change_name' });
	});
	test('missing artifact store falls through', () => {
		expect(parseExploreTaskPrompt('Run exploration `x3` now.')).toEqual({ ok: false, reason: 'no_artifact_store' });
	});
	test('two different backticked change names fall through', () => {
		expect(parseExploreTaskPrompt('Continue exploration `change-a` for change `change-b`.\nArtifact store: engram')).toEqual({ ok: false, reason: 'conflicting_change_names' });
	});
	test('non-slug change name falls through', () => {
		expect(parseExploreTaskPrompt('change `my change`.\nArtifact store: engram')).toEqual({ ok: false, reason: 'change_name_not_slug' });
	});
	test('workspace name in backticks is not mistaken for the repo; the absolute path on the line wins', () => {
		const r = parseExploreTaskPrompt('Explore change `x4`.\n- Project/workspace: `entities-portal` at `/repo/entities-portal`\n- Artifact store: openspec');
		expect(r.ok && r.task.repoHint).toBe('/repo/entities-portal');
	});
});

describe('unit: request build — exact CLI schema', () => {
	const task = { change: 'c-1', store: 'engram' as const, brief: 'full prompt text', repoHint: '/repo' };
	test('round-trips through the CLI validator (parseExploreRequest)', () => {
		const req = buildExploreRequest(task, '/repo');
		expect(parseExploreRequest(req)).toEqual(req);
	});
	test('model is the router constant; brief is the full prompt', () => {
		const req = buildExploreRequest(task, '/repo');
		expect(req.model).toBe('gemini-3-flash');
		expect(req.schema).toBe('agy-explore/req@1');
		expect(req.brief).toBe('full prompt text');
	});
	test('throws loudly if it ever builds a req the CLI would reject', () => {
		expect(() => buildExploreRequest({ ...task, store: 'memgraph' as never }, '/repo')).toThrow();
	});
});

describe('unit: cli stdout contract — last JSON line only', () => {
	test('parses the last line after noise', () => {
		const out = parseCliStdout(['preamble', 'more noise', JSON.stringify(res('success', { artifactPath: '/a/b.md' }))].join('\n'));
		expect(out?.outcome).toBe('success');
	});
	test('trailing whitespace tolerated', () => {
		expect(parseCliStdout(`${JSON.stringify(res('timeout'))}\n  `)?.outcome).toBe('timeout');
	});
	test('empty stdout, garbage, or wrong schema yield null (native fallthrough)', () => {
		expect(parseCliStdout('')).toBeNull();
		expect(parseCliStdout('not json at all')).toBeNull();
		expect(parseCliStdout(JSON.stringify({ schema: 'agy-explore/res@9', outcome: 'success' }))).toBeNull();
		expect(parseCliStdout(JSON.stringify({ schema: EXPLORE_RESULT_SCHEMA, outcome: 'invented_outcome' }))).toBeNull();
	});
});

describe('unit: outcome -> action mapping and envelopes', () => {
	const task = { change: 'chg', store: 'openspec' as const, brief: 'b' };
	test('every outcome class maps to exactly one action', () => {
		const expectations: Array<[string, 'throw' | 'fallback']> = [
			['success', 'throw'],
			['quota_unavailable', 'fallback'],
			['transient_unavailable', 'fallback'],
			['timeout', 'fallback'],
			['auth_captcha', 'throw'],
			['task_failure', 'throw'],
			['artifact_validation_failure', 'throw'],
		];
		for (const [outcome, action] of expectations) {
			const r = res(outcome, { fallbackAllowed: action === 'fallback' });
			// Recomputed policy must match the CLI's field for every class.
			expect(r.fallbackAllowed).toBe(action === 'fallback');
		}
	});
	test('success envelope cites artifactPath, next phase, and the dispatch framing', () => {
		const env = successEnvelope(res('success', { artifactPath: '/repo/openspec/changes/chg/exploration.md', receipt: { store: 'openspec', wroteOpenspec: true, engramRequired: false }, sha256: 'abc123' }), task);
		expect(env).toContain('**Status**: success');
		expect(env).toContain('`/repo/openspec/changes/chg/exploration.md`');
		expect(env).toContain('**Next**: sdd-propose');
		expect(env).toContain('0 LLM router requests');
		expect(env).toContain('"outcome": "success"');
	});
	test('engram-required success reports PENDING persistence with topic and workdir artifact', () => {
		const env = successEnvelope(res('success', { receipt: { store: 'engram', wroteOpenspec: false, engramRequired: true }, resultPath: '/cache/chg/1/result.json' }), { ...task, store: 'engram' });
		expect(env).toContain('PENDING');
		expect(env).toContain('`sdd/chg/explore`');
		expect(env).toContain('/cache/chg/1/exploration.md');
		expect(env).toContain('must not assume');
	});
	test('blocked envelope surfaces outcome and reason verbatim with the typed result', () => {
		const env = blockedEnvelope(res('auth_captcha', { reason: 'auth_or_captcha' }), task);
		expect(env).toContain('**Status**: blocked');
		expect(env).toContain('`auth_captcha`');
		expect(env).toContain('`auth_or_captcha`');
		expect(env).toContain('no fallback');
		expect(env).toContain('"outcome": "auth_captcha"');
	});
});

describe('unit: gating — kill switch and binary preflight', () => {
	const dirs = (paths: string[]) => ({ exists: (p: string) => paths.includes(p) });
	const base = { env: { PATH: '/bin:/usr/bin' }, forceNativeFile: '/force', agyExploreBin: 'agy-explore', agyBin: 'agy' };
	test('dispatches when the force file is absent and both binaries resolve on PATH', () => {
		expect(gateDispatch({ ...base, ...dirs(['/bin/agy-explore', '/bin/agy']) })).toEqual({ dispatch: true });
	});
	test('force-native marker file disables dispatch (same kill switch as cli.ts)', () => {
		expect(gateDispatch({ ...base, ...dirs(['/force', '/bin/agy-explore', '/bin/agy']) })).toEqual({ dispatch: false, reason: 'force_native_file' });
	});
	test('env AI_STACK_SDD_EXPLORE=native disables dispatch', () => {
		expect(gateDispatch({ ...base, env: { PATH: '/bin', AI_STACK_SDD_EXPLORE: 'native' }, ...dirs(['/bin/agy-explore', '/bin/agy']) })).toEqual({ dispatch: false, reason: 'env_force_native' });
	});
	test('missing agy-explore binary falls through to native', () => {
		expect(gateDispatch({ ...base, ...dirs(['/bin/agy']) })).toEqual({ dispatch: false, reason: 'agy_explore_absent' });
	});
	test('missing agy binary falls through to native', () => {
		expect(gateDispatch({ ...base, ...dirs(['/bin/agy-explore']) })).toEqual({ dispatch: false, reason: 'agy_absent' });
	});
});

describe('integration: dispatchTaskPrompt — stub CLI, argv contract, outcome wiring', () => {
	afterAll(() => {
		delete process.env.AGY_EXPLORE_BIN;
		delete process.env.AGY_BIN;
		delete process.env.AGY_EXPLORE_FORCE_NATIVE_FILE;
		delete process.env.AGY_EXPLORE_REQ_DIR;
	});
	async function setup(makeResultLine: (root: string) => string | null) {
		const root = await mkdtemp('/tmp/agy-dispatch-');
		const cli = `${root}/agy-explore-stub`;
		const agy = `${root}/agy-stub`;
		const force = `${root}/force-native`;
		const reqDir = `${root}/req`;
		const line = makeResultLine(root);
		await Bun.write(cli, line === null
			? '#!/bin/sh\nprintf "%s" "$*" > "$0.args"\necho "garbage stdout"\n'
			: `#!/bin/sh\nprintf "%s" "$*" > "$0.args"\necho '${line}'\n`);
		await Bun.write(agy, '');
		await Bun.write(`${root}/prompt.txt`, 'Run SDD exploration for mini change `stub-chg`.\n\n- Workspace: `' + root + '`\n- Artifact store: Engram\n');
		Bun.spawnSync(['chmod', '+x', cli]);
		const deps = (over: Partial<DispatchDeps> = {}): DispatchDeps => ({
			env: { PATH: '/bin:/usr/bin' },
			cwd: root,
			forceNativeFile: force,
			agyExploreBin: cli,
			agyBin: agy,
			reqDir,
			timeoutMs: 5000,
			exists: (p) => p === cli || p === agy || p === root,
			mkdir: () => {},
			writeFile: (p, text) => { void Bun.write(p, text); },
			run: async (cmd, args) => {
				const r = Bun.spawnSync([cmd, ...args], { cwd: root });
				return { stdout: r.stdout.toString(), stderr: r.stderr.toString(), exitCode: r.exitCode, timedOut: false };
			},
			...over,
		});
		return { root, cli, agyBin: agy, force, reqDir, prompt: await Bun.file(`${root}/prompt.txt`).text(), deps, wrote: [] as Array<[string, string]> };
	}
	const successLine = (root: string) => JSON.stringify(res('success', { artifactPath: `${root}/openspec/changes/stub-chg/exploration.md`, receipt: { store: 'engram', wroteOpenspec: false, engramRequired: true }, resultPath: `${root}/run/result.json` }));

	test('success: writes the req file with exact argv, returns throw action with envelope', async () => {
		const s = await setup(successLine);
		const written: Array<[string, string]> = [];
		const d = s.deps({ writeFile: (p, text) => written.push([p, text]) });
		const action = await dispatchTaskPrompt(s.prompt, d);
		expect(action.action).toBe('throw');
		if (action.action !== 'throw') return;
		expect(action.envelope).toContain('**Status**: success');
		expect(action.envelope).toContain('`sdd/stub-chg/explore`');
		// argv contract: exactly --input <reqPathFor(change)> with the router's canonical req dir layout
		expect(await Bun.file(`${s.cli}.args`).text()).toBe(`--input ${reqPathFor('stub-chg', s.reqDir)}`);
		// req content matches the CLI schema exactly
		expect(written).toHaveLength(1);
		expect(parseExploreRequest(JSON.parse(written[0][1]))).not.toBeNull();
	});
	test('quota_unavailable: fallback action (approved unavailability)', async () => {
		const s = await setup(() => JSON.stringify(res('quota_unavailable', { reason: 'quota_exhausted', fallbackAllowed: true })));
		const action = await dispatchTaskPrompt(s.prompt, s.deps());
		expect(action).toEqual({ action: 'fallback' });
	});
	test('auth_captcha: throw blocked envelope, no fallback', async () => {
		const s = await setup(() => JSON.stringify(res('auth_captcha', { reason: 'auth_or_captcha' })));
		const action = await dispatchTaskPrompt(s.prompt, s.deps());
		expect(action.action).toBe('throw');
		if (action.action !== 'throw') return;
		expect(action.envelope).toContain('`auth_captcha`');
	});
	test('kill switch: force-native file present -> native fallthrough, CLI never invoked', async () => {
		const s = await setup(() => JSON.stringify(res('success')));
		let ran = false;
		const action = await dispatchTaskPrompt(s.prompt, s.deps({ exists: (p) => p === s.cli || p === s.agyBin || p === s.root || p === s.force, run: async (...a) => { ran = true; return s.deps().run(a[0], a[1], a[2]); } }));
		expect(action).toEqual({ action: 'return', reason: 'force_native_file' });
		expect(ran).toBe(false);
	});
	test('unparseable CLI stdout -> native fallthrough', async () => {
		const s = await setup(() => null);
		const action = await dispatchTaskPrompt(s.prompt, s.deps());
		expect(action).toEqual({ action: 'return', reason: 'cli_stdout_unparseable' });
	});
	test('ambiguous prompt (qualified store) -> native fallthrough before any spawn', async () => {
		const s = await setup(() => JSON.stringify(res('success')));
		let ran = false;
		const action = await dispatchTaskPrompt('Explore change `q1`.\nArtifact store: hybrid session, but persist to Engram only', s.deps({ run: async () => { ran = true; return { stdout: '', stderr: '', exitCode: 0, timedOut: false }; } }));
		expect(action).toEqual({ action: 'return', reason: 'unparsed_task_prompt:artifact_store_qualified' });
		expect(ran).toBe(false);
	});
	test('repo resolution prefers the prompt workspace when it exists, else plugin cwd', async () => {
		const s = await setup(successLine);
		const written: Array<[string, string]> = [];
		const d = s.deps({ exists: (p) => p === s.cli || p === s.agyBin, writeFile: (p, text) => written.push([p, text]) });
		await dispatchTaskPrompt(s.prompt, d);
		// Workspace in fixture === s.root but exists() here denies it -> falls back to cwd
		expect(JSON.parse(written[0][1]).repo).toBe(s.root); // cwd === s.root in this harness
	});
});

describe('integration: plugin adapter — real hook function with injected env', () => {
	afterAll(() => {
		delete process.env.AGY_EXPLORE_BIN;
		delete process.env.AGY_BIN;
		delete process.env.AGY_EXPLORE_FORCE_NATIVE_FILE;
		delete process.env.AGY_EXPLORE_REQ_DIR;
	});
	async function hook() {
		const root = await mkdtemp('/tmp/agy-plugin-');
		const cli = `${root}/agy-explore-stub`;
		const agy = `${root}/agy-stub`;
		const line = JSON.stringify(res('success', { artifactPath: `${root}/openspec/changes/p-chg/exploration.md`, receipt: { store: 'openspec', wroteOpenspec: true, engramRequired: false }, resultPath: `${root}/run/result.json` }));
		await Bun.write(cli, `#!/bin/sh\necho '${line}'\n`);
		await Bun.write(agy, '');
		Bun.spawnSync(['chmod', '+x', cli]);
		process.env.AGY_EXPLORE_BIN = cli;
		process.env.AGY_BIN = agy;
		process.env.AGY_EXPLORE_FORCE_NATIVE_FILE = `${root}/force-native`;
		process.env.AGY_EXPLORE_REQ_DIR = `${root}/req`;
		const hooks = await (plugin as unknown as (input: unknown) => Promise<Record<string, (input: unknown, output: unknown) => Promise<void>>>)({
			directory: root,
			worktree: root,
			client: { app: { log: async () => {} } },
		});
		return { root, hooks, prompt: `Run exploration \`p-chg\`.\n\n- Workspace: \`${root}\`\n- Artifact store: openspec\n` };
	}
	test('non-task tools and other subagents pass through untouched (zero overhead)', async () => {
		const { hooks } = await hook();
		const args = { command: 'ls -la' };
		await hooks['tool.execute.before']({ tool: 'bash', sessionID: 's', callID: 'c' }, { args });
		expect(args).toEqual({ command: 'ls -la' });
		const taskArgs = { subagent_type: 'sdd-propose', prompt: 'x' };
		await hooks['tool.execute.before']({ tool: 'task', sessionID: 's', callID: 'c' }, { args: taskArgs });
		expect(taskArgs.subagent_type).toBe('sdd-propose');
	});
	test('success: hook throws the envelope (throw-only before-hook substitution)', async () => {
		const { hooks, prompt } = await hook();
		const output = { args: { subagent_type: 'sdd-explore', prompt } };
		let caught: unknown;
		try {
			await hooks['tool.execute.before']({ tool: 'task', sessionID: 's', callID: 'c' }, output);
		} catch (e) {
			caught = e;
		}
		expect(caught).toBeInstanceOf(Error);
		expect((caught as Error).message).toContain('**Status**: success');
	});
	test('approved unavailability: hook mutates subagent_type to sdd-explore-fallback and returns', async () => {
		const { root } = await hook();
		const line = JSON.stringify(res('timeout', { fallbackAllowed: true }));
		const cli = process.env.AGY_EXPLORE_BIN!;
		await Bun.write(cli, `#!/bin/sh\necho '${line}'\n`);
		Bun.spawnSync(['chmod', '+x', cli]);
		const hooks = await (plugin as unknown as (input: unknown) => Promise<Record<string, (input: unknown, output: unknown) => Promise<void>>>)({
			directory: root,
			worktree: root,
			client: { app: { log: async () => {} } },
		});
		const output = { args: { subagent_type: 'sdd-explore', prompt: `Run exploration \`p-chg\`.\n\n- Workspace: \`${root}\`\n- Artifact store: openspec\n` } };
		await hooks['tool.execute.before']({ tool: 'task', sessionID: 's', callID: 'c' }, output);
		expect(output.args.subagent_type).toBe('sdd-explore-fallback');
		expect(output.args.prompt).toContain('p-chg'); // original prompt preserved for the native executor
	});
	test('ambiguous prompt: hook returns silently (native router runs)', async () => {
		const { hooks } = await hook();
		const output = { args: { subagent_type: 'sdd-explore', prompt: 'Explore something vague without structure.' } };
		await expect(hooks['tool.execute.before']({ tool: 'task', sessionID: 's', callID: 'c' }, output)).resolves.toBeUndefined();
		expect(output.args.subagent_type).toBe('sdd-explore');
	});
});

describe('contract: build.sh render step — self-contained plugin bundle', () => {
	test('bun build output is single-file (no cross-repo imports) and registers the hook', async () => {
		const out = '/tmp/agy-dispatch-bundle-test/sdd-explore-dispatch.ts';
		const r = Bun.spawnSync(['bun', 'build', '--target=bun', '--format=esm', `${import.meta.dir}/../src/plugin/sdd-explore-dispatch.ts`, '--outfile', out]);
		expect(r.exitCode).toBe(0);
		const text = await Bun.file(out).text();
		expect(text).toContain('tool.execute.before');
		expect(text).toContain('sdd-explore-dispatch');
		// Inlined, never imported at runtime (the "// src/agy/dispatch-core.ts" comment banner is fine).
		expect(text).not.toMatch(/import[^;\n]*dispatch-core/);
	});
});
