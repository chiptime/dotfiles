/**
 * Hermetic tests for the quota-balancer read-only advisor (tasks 3.2/3.4).
 * Core: pure decide()/formatNotice() — kill switch, staleness, schema shape,
 * tier guard, notice flooding/dedupe, rendering. Adapter: transport
 * fail-open, toast failure, exactly-one-toast, zero mutation, kill-switch
 * zero fetch. No network: fetch injected or patched; fs via temp dirs only.
 */
import { describe, expect, test } from 'bun:test';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ADVICE_SCHEMA, decide, formatNotice, type BalancerAdvice, type DecideInput, type ShownState } from '../src/balancer/core';
import plugin, { adviseOnce, type AdvisorDeps, type AdvisorState } from '../src/plugin/quota-balancer';

const TIERS = {
	frontier: { 'anthropic/claude-opus-5': 'claude', 'zai/glm-4.7': 'zai' },
	extended: { 'zai/glm-4.7-air': 'zai', 'deepseek/deepseek-chat': 'deepseek' },
};
const advice = (over: Record<string, unknown> = {}): BalancerAdvice =>
	({ schema: ADVICE_SCHEMA, requested_model: 'anthropic/claude-opus-5', tier: 'frontier', recommended_model: 'zai/glm-4.7', switch: true, reason: 'requested_below_floor', advice_age_seconds: 42, requested_remaining: 0.12, ...over }) as BalancerAdvice;
const run = (over: Partial<DecideInput> = {}) => decide({ raw: advice(), killSwitch: false, tiers: TIERS, now: 1_000_000, lastShown: null, ...over });

describe('unit: core.decide (R5-R7)', () => {
	test('suppression: kill switch, stale 650, malformed/wrong schema, switch:false, off-tier, no tier truth', () => {
		const suppress: unknown[] = ['json-string', [], {}, advice({ schema: 'ai-quotas/balancer-advice@2' }), advice({ switch: 'yes' }), advice({ advice_age_seconds: '42' }), advice({ recommended_model: '' })];
		expect(run({ killSwitch: true }).show).toBe(false); // R6
		expect(run({ raw: advice({ advice_age_seconds: 650 }) }).show).toBe(false); // stale render
		for (const raw of suppress) expect(run({ raw }).show, JSON.stringify(raw)).toBe(false);
		expect(run({ raw: advice({ switch: false }) }).show).toBe(false);
		expect(run({ raw: advice({ recommended_model: 'deepseek/deepseek-chat' }) }).show).toBe(false); // off-tier rec
		expect(run({ tiers: null }).show).toBe(false);
	});
	test('fresh advice shows: 600 s boundary, notice text, changed-rec re-notice', () => {
		const d = run({ raw: advice({ advice_age_seconds: 600 }) });
		expect(d.show).toBe(true);
		expect(d.notice).toBe('quota: → zai/glm-4.7 · anthropic/claude-opus-5 at 12% · /models to switch');
		expect(run({ lastShown: { model: 'openai/gpt-5.2', at: 1_000_000 } }).show).toBe(true);
	});
	test('notice flooding: 10 same-advice messages -> exactly 1 shown; interval expiry re-notices', () => {
		let last: ShownState | null = null;
		let shown = 0;
		for (let i = 0; i < 10; i++) {
			const d = run({ lastShown: last, now: 1_000_000 + i * 1_000 });
			if (d.show) shown++;
			last = d.lastShown;
		}
		expect(shown).toBe(1);
		expect(run({ lastShown: last, now: 1_000_000 + 901_000 }).show).toBe(true);
	});
	test('formatNotice: % when present, omitted when absent, <= 80 chars, one line', () => {
		const { requested_remaining: _drop, ...noPct } = advice();
		const withPct = formatNotice(advice());
		expect(withPct).toBe('quota: → zai/glm-4.7 · anthropic/claude-opus-5 at 12% · /models to switch');
		expect(formatNotice(noPct)).toBe('quota: → zai/glm-4.7 · anthropic/claude-opus-5 · /models to switch');
		expect(withPct.length).toBeLessThanOrEqual(80);
		expect(withPct).not.toMatch(/\n|\x1b/);
	});
});
const jsonRes = (body: string, status = 200) => new Response(body, { status, headers: { 'content-length': String(body.length) } });
const deps = (over: Partial<AdvisorDeps> = {}): AdvisorDeps & { toasts: string[] } => {
	const toasts: string[] = [];
	return {
		env: {}, exists: () => false, logDebug: () => {}, now: () => 1_000_000, timeoutMs: 300, maxBytes: 65_536,
		fetchImpl: async () => jsonRes(JSON.stringify(advice())),
		readText: () => JSON.stringify({ notice_interval: 900, tiers: TIERS }),
		showToast: async (m) => void toasts.push(m), ...over, toasts,
	};
};

describe('adapter: adviseOnce — transport fail-open, toast failure, dedupe (R6/R7)', () => {
	test('fresh switch surfaces exactly one toast; repeated advice stays at one; showToast rejection never escapes', async () => {
		const d = deps();
		const state: AdvisorState = { lastShown: null };
		await adviseOnce('anthropic/claude-opus-5', state, d);
		await adviseOnce('anthropic/claude-opus-5', state, d);
		await adviseOnce('anthropic/claude-opus-5', state, d);
		expect(d.toasts).toHaveLength(1);
		await expect(adviseOnce('anthropic/claude-opus-5', { lastShown: null }, deps({ showToast: async () => { throw new Error('headless'); } }))).resolves.toBeUndefined();
	});
	test('timeout / refused / HTTP 500 / oversized / malformed body: no throw, no toast', async () => {
		const hangUntilAbort: typeof fetch = (_u, o) => new Promise((_r, rej) => o?.signal?.addEventListener('abort', () => rej(new Error('aborted'))));
		const cases: Array<[string, typeof fetch]> = [
			['timeout', hangUntilAbort],
			['refused', (async () => { throw new Error('ECONNREFUSED'); }) as typeof fetch],
			['http-500', async () => jsonRes('internal error', 500)],
			['oversized', async () => jsonRes('x'.repeat(70_000))],
			['malformed-body', async () => jsonRes('not json at all')],
		];
		for (const [name, fetchImpl] of cases) {
			const d = deps({ fetchImpl });
			await expect(adviseOnce('anthropic/claude-opus-5', { lastShown: null }, { ...d, timeoutMs: 20 }), name).resolves.toBeUndefined();
			expect(d.toasts, name).toHaveLength(0);
		}
	});
	test('kill switch: zero fetches, zero toasts (R6, before any network)', async () => {
		let fetches = 0;
		const d = deps({ exists: () => true, fetchImpl: async () => (fetches++, jsonRes(JSON.stringify(advice()))) });
		await adviseOnce('anthropic/claude-opus-5', { lastShown: null }, d);
		expect(fetches).toBe(0);
		expect(d.toasts).toHaveLength(0);
	});
});
describe('adapter: chat.message hook — read-only seam through the real factory (R5/R10)', () => {
	test('fresh switch: exactly 1 toast via stub showToast spy, output never mutated', async () => {
		const dir = mkdtempSync(join(tmpdir(), 'qb-hook-'));
		writeFileSync(join(dir, 'balancer.json'), JSON.stringify({ notice_interval: 900, tiers: TIERS }));
		const toasts: string[] = [];
		const realFetch = globalThis.fetch;
		globalThis.fetch = (async () => jsonRes(JSON.stringify(advice()))) as typeof fetch;
		process.env.AI_QUOTAS_BALANCER_CONFIG = join(dir, 'balancer.json');
		process.env.QUOTA_BALANCER_FORCE_NATIVE_FILE = join(dir, 'force-native');
		try {
			const hooks = await (plugin as unknown as (i: unknown) => Promise<Record<string, (input: unknown, output: unknown) => Promise<void>>>)({
				client: { tui: { showToast: async (o: { body: { message: string } }) => void toasts.push(o.body.message) }, app: { log: async () => {} } },
			});
			const output = { message: undefined, parts: [] };
			for (let i = 0; i < 3; i++) await hooks['chat.message']({ sessionID: 's1', model: { providerID: 'anthropic', modelID: 'claude-opus-5' } }, output);
			expect(toasts).toHaveLength(1); // dedupe across messages
			expect(output).toEqual({ message: undefined, parts: [] }); // R5: chat keeps the requested model
		} finally {
			globalThis.fetch = realFetch;
			delete process.env.AI_QUOTAS_BALANCER_CONFIG;
			delete process.env.QUOTA_BALANCER_FORCE_NATIVE_FILE;
		}
	});
});
