/**
 * Pure dispatch logic for the sdd-explore before-hook interceptor plugin.
 *
 * The plugin replaces the LLM "router agent" round-trips with a deterministic
 * pipeline: parse the orchestrator's task prompt -> build an ExploreRequest ->
 * run `agy-explore` -> map the typed result to one of three actions:
 *   - throw  : hand the canonical SDD envelope to the orchestrator (success or
 *              blocked; before-hook substitution is throw-only, which is the
 *              proven pattern from sdd-task-result-artifacts.ts)
 *   - fallback: mutate subagent_type to sdd-explore-fallback (approved
 *              unavailability only — today's exact router semantics)
 *   - return : fall through to the native path. NEVER guess: any parsing
 *              ambiguity, kill switch, missing binary, or CLI transport
 *              failure degrades to the native router agent, so the dispatcher
 *              is never worse than the status quo.
 *
 * Everything impure (fs, spawn) is injected, so tests run hermetically.
 * The brief sent to agy is the FULL task prompt verbatim: the old LLM router
 * lossily compressed it, while the native executor receives it whole — the
 * full prompt is both deterministic and strictly more faithful.
 */
import {
	EXPLORE_REQUEST_SCHEMA,
	EXPLORE_RESULT_SCHEMA,
	isFallbackAllowed,
	parseExploreRequest,
	type ExploreRequest,
	type ExploreResult,
	type Store,
} from './outcomes';

// agy renamed its model slugs in the 2026-09 CLI (plain "gemini-3-flash" is no
// longer accepted); keep the default as a valid current slug and allow an env
// override so future renames don't require a rebuild of binary + plugin.
export const ROUTER_MODEL = process.env.AGY_EXPLORE_MODEL ?? 'gemini-3.8-flash-high';
export const FALLBACK_SUBAGENT = 'sdd-explore-fallback';
export const TARGET_SUBAGENT = 'sdd-explore';
const OUTCOMES = new Set(['success', 'quota_unavailable', 'transient_unavailable', 'auth_captcha', 'timeout', 'task_failure', 'artifact_validation_failure']);
const STORE_TOKENS = ['both', 'hybrid', 'engram', 'openspec', 'none'] as const;

export interface ParsedTask {
	change: string;
	store: Store;
	/** Absolute workspace path stated in the prompt, when present. */
	repoHint?: string;
	/** Always the full prompt verbatim. */
	brief: string;
}

export type ParseResult = { ok: true; task: ParsedTask } | { ok: false; reason: string };

/** Qualifier words after the store token mean the orchestrator overrode the session default (e.g. "hybrid session, but persist to Engram only"). */
const STORE_QUALIFIER_RE = /\b(but|only|except|instead|however)\b/i;
const CHANGE_PATTERNS = [/(?:^|[^a-z])change\s+`([^`]+)`/gi, /(?:^|[^a-z])exploration\s+`([^`]+)`/gi];
const CHANGE_SLUG_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const STORE_LINE_RE = /artifact\s+store(?:\s+mode)?\s*:\s*`?(both|hybrid|engram|openspec|none)\b([^\n]*)/i;
const STORE_REVERSE_RE = /`?(engram|openspec|hybrid|none)`?\s+artifact\s+store\b([^\n]*)/i;
const WORKSPACE_PATH_RE = /workspace:?[^\n]*?`(\/[^`]+)`/i;
const WORKDIR_PATH_RE = /working directory:?\s+`?(\/[^\s`]+)/i;

function parseStore(prompt: string): { store: Store } | { ok: false; reason: string } {
	const forward = STORE_LINE_RE.exec(prompt);
	const reverse = STORE_REVERSE_RE.exec(prompt);
	const picks: string[] = [];
	if (forward) picks.push(forward[1].toLowerCase());
	if (reverse) picks.push(reverse[1].toLowerCase());
	if (picks.length === 0) return { ok: false, reason: 'no_artifact_store' };
	const distinct = [...new Set(picks)];
	if (distinct.length > 1) return { ok: false, reason: 'conflicting_artifact_store' };
	// The remainder of the matched line must be inert: "Artifact store: hybrid session, but ..." is a deliberate override we cannot resolve deterministically.
	const remainder = (forward?.[2] ?? '') + (reverse?.[2] ?? '');
	if (STORE_QUALIFIER_RE.test(remainder)) return { ok: false, reason: 'artifact_store_qualified' };
	const token = distinct[0] === 'both' ? 'hybrid' : distinct[0];
	return { store: token as Store };
}

export function parseExploreTaskPrompt(prompt: string): ParseResult {
	const changes = new Set<string>();
	for (const re of CHANGE_PATTERNS) for (const m of prompt.matchAll(re)) changes.add(m[1].trim());
	if (changes.size === 0) return { ok: false, reason: 'no_change_name' };
	if (changes.size > 1) return { ok: false, reason: 'conflicting_change_names' };
	const change = [...changes][0];
	if (!CHANGE_SLUG_RE.test(change)) return { ok: false, reason: 'change_name_not_slug' };
	const store = parseStore(prompt);
	if (!('store' in store)) return { ok: false, reason: store.reason };
	const ws = WORKSPACE_PATH_RE.exec(prompt) ?? WORKDIR_PATH_RE.exec(prompt);
	const repoHint = ws?.[1];
	if (repoHint !== undefined && /\s/.test(repoHint)) return { ok: false, reason: 'workspace_path_invalid' };
	return { ok: true, task: { change, store: store.store, ...(repoHint === undefined ? {} : { repoHint }), brief: prompt } };
}

export interface GateInput {
	env: Record<string, string>;
	/** exists(p) answers "is this path present" for files and binaries. */
	exists: (path: string) => boolean;
	forceNativeFile: string;
	agyExploreBin: string;
	agyBin: string;
}

export type GateResult = { dispatch: true } | { dispatch: false; reason: string };

function binResolvable(bin: string, env: Record<string, string>, exists: (p: string) => boolean): boolean {
	if (bin.includes('/')) return exists(bin);
	return (env.PATH ?? '').split(':').some((dir) => dir !== '' && exists(`${dir}/${bin}`));
}

/** Same kill switch and binary preflight the CLI itself applies (cli.ts force-native + agy_absent), evaluated before we spend anything. */
export function gateDispatch(g: GateInput): GateResult {
	if (g.env.AI_STACK_SDD_EXPLORE === 'native') return { dispatch: false, reason: 'env_force_native' };
	if (g.exists(g.forceNativeFile)) return { dispatch: false, reason: 'force_native_file' };
	if (!binResolvable(g.agyExploreBin, g.env, g.exists)) return { dispatch: false, reason: 'agy_explore_absent' };
	if (!binResolvable(g.agyBin, g.env, g.exists)) return { dispatch: false, reason: 'agy_absent' };
	return { dispatch: true };
}

/** Build the exact req the CLI validates (schema agy-explore/req@1); model is the router constant. */
export function buildExploreRequest(task: ParsedTask, repo: string): ExploreRequest {
	const req: ExploreRequest = {
		schema: EXPLORE_REQUEST_SCHEMA,
		change: task.change,
		store: task.store,
		repo,
		brief: task.brief,
		model: ROUTER_MODEL,
	};
	// Self-check: if this ever stops round-tripping through the CLI's own
	// validator, dispatch must not proceed with a malformed req.
	if (parseExploreRequest(req) === null) throw new Error('dispatch-core built a request the CLI would reject');
	return req;
}

/** Same file contract the router prompt mandates: /tmp/opencode/agy/req-<change>.json, unique per change, never inside a repository. */
export function reqPathFor(change: string, reqDir: string): string {
	return `${reqDir.replace(/\/$/, '')}/req-${change}.json`;
}

/** The CLI prints JSON.stringify(result) as its LAST stdout line (cli.ts main). Noise before it is tolerated. */
export function parseCliStdout(stdout: string): (ExploreResult & { resultPath?: string }) | null {
	const lines = stdout.trim().split('\n');
	for (let i = lines.length - 1; i >= 0; i--) {
		const line = lines[i].trim();
		if (line === '') continue;
		try {
			const raw = JSON.parse(line) as unknown;
			if (typeof raw !== 'object' || raw === null) return null;
			const r = raw as Record<string, unknown>;
			if (r.schema !== EXPLORE_RESULT_SCHEMA || typeof r.outcome !== 'string' || !OUTCOMES.has(r.outcome)) return null;
			return raw as ExploreResult & { resultPath?: string };
		} catch {
			return null;
		}
	}
	return null;
}

function workdirArtifact(result: ExploreResult & { resultPath?: string }): string | undefined {
	return result.resultPath === undefined ? undefined : `${result.resultPath.replace(/[^/]+$/, '')}exploration.md`;
}

export function successEnvelope(result: ExploreResult & { resultPath?: string }, task: ParsedTask): string {
	const artifact = workdirArtifact(result);
	const artifactLines: string[] = [];
	if (result.receipt.wroteOpenspec && result.artifactPath) artifactLines.push(`openspec artifact \`${result.artifactPath}\``);
	if (result.receipt.engramRequired) {
		// The old LLM router performed this mem_save itself; a plugin has no MCP access, so persistence is reported as pending, never fabricated.
		artifactLines.push(`Engram topic \`sdd/${task.change}/explore\` — PENDING (dispatcher cannot call mem_save); artifact text at \`${artifact ?? 'run workdir'}\` (receipt.engramRequired=true)`);
	}
	if (artifactLines.length === 0) artifactLines.push(artifact === undefined ? 'inline only (store=none)' : `run artifact \`${artifact}\` (store=none)`);
	const risks = result.receipt.engramRequired
		? `Engram persistence pending (see Artifacts) — later phases must not assume \`sdd/${task.change}/explore\` exists in Engram yet.`
		: 'None';
	return [
		'SDD-EXPLORE DISPATCH (plugin sdd-explore-dispatch; 0 LLM router requests). This message IS the sdd-explore phase result — do not re-run the task.',
		'',
		`**Status**: success`,
		`**Summary**: Exploration for change \`${task.change}\` completed by the agy backend in ${result.elapsedMs}ms (outcome success, store ${task.store}).`,
		`**Artifacts**: ${artifactLines.join(' | ')}`,
		`**Next**: sdd-propose`,
		`**Risks**: ${risks}`,
		`**Skill Resolution**: none — deterministic agy dispatch (no sub-agent skills loaded)`,
		'',
		'Typed result (agy-explore/res@1):',
		'```json',
		JSON.stringify(result, null, 2),
		'```',
	].join('\n');
}

export function blockedEnvelope(result: ExploreResult & { resultPath?: string }, task: ParsedTask): string {
	return [
		'SDD-EXPLORE DISPATCH (plugin sdd-explore-dispatch; 0 LLM router requests). The agy backend BLOCKED this exploration; router policy allows no fallback for this outcome class. Surface it to the user; do not retry the phase.',
		'',
		`**Status**: blocked`,
		`**Summary**: agy-explore returned outcome \`${result.outcome}\` (reason: \`${result.reason ?? 'unknown'}\`) for change \`${task.change}\`; fallback is not allowed for this outcome class.`,
		`**Artifacts**: none`,
		`**Next**: none`,
		`**Risks**: Typed outcome \`${result.outcome}\` with reason \`${result.reason ?? 'unknown'}\` — see the result JSON below.`,
		`**Skill Resolution**: none — deterministic agy dispatch (no sub-agent skills loaded)`,
		'',
		'Typed result (agy-explore/res@1):',
		'```json',
		JSON.stringify(result, null, 2),
		'```',
	].join('\n');
}

export type DispatchAction =
	| { action: 'return'; reason: string }
	| { action: 'fallback' }
	| { action: 'throw'; envelope: string };

export interface RunOutcome {
	stdout: string;
	stderr: string;
	exitCode: number | null;
	timedOut: boolean;
}

export interface DispatchDeps {
	env: Record<string, string>;
	cwd: string;
	forceNativeFile: string;
	agyExploreBin: string;
	agyBin: string;
	reqDir: string;
	timeoutMs: number;
	exists: (path: string) => boolean;
	mkdir: (path: string) => void;
	writeFile: (path: string, text: string) => void;
	run: (cmd: string, args: string[], opts: { cwd: string; timeoutMs: number }) => Promise<RunOutcome>;
}

/**
 * Deterministic replacement for the LLM router's whole loop. Returns the
 * action the before hook must take; the plugin adapter performs the side
 * effects (mutate args / throw). Recomputes fallbackAllowed from the outcome
 * instead of trusting the CLI's field, so CLI drift cannot silently widen
 * the fallback gate.
 */
export async function dispatchTaskPrompt(prompt: string, d: DispatchDeps): Promise<DispatchAction> {
	const gate = gateDispatch(d);
	if (!gate.dispatch) return { action: 'return', reason: gate.reason };
	const parsed = parseExploreTaskPrompt(prompt);
	if (!parsed.ok) return { action: 'return', reason: `unparsed_task_prompt:${parsed.reason}` };
	const repo = parsed.task.repoHint !== undefined && d.exists(parsed.task.repoHint) ? parsed.task.repoHint : d.cwd;
	const req = buildExploreRequest(parsed.task, repo);
	const reqPath = reqPathFor(parsed.task.change, d.reqDir);
	d.mkdir(d.reqDir);
	d.writeFile(reqPath, JSON.stringify(req, null, 2) + '\n');
	let run: RunOutcome;
	try {
		run = await d.run(d.agyExploreBin, ['--input', reqPath], { cwd: d.cwd, timeoutMs: d.timeoutMs });
	} catch {
		return { action: 'return', reason: 'cli_spawn_error' };
	}
	if (run.timedOut || run.exitCode === null) return { action: 'return', reason: 'cli_timeout' };
	const result = parseCliStdout(run.stdout);
	if (result === null) {
		// No typed result: the CLI itself broke (version drift, crashed). Degrade to the native router rather than guessing.
		return { action: 'return', reason: 'cli_stdout_unparseable' };
	}
	if (result.outcome === 'success') return { action: 'throw', envelope: successEnvelope(result, parsed.task) };
	if (isFallbackAllowed(result.outcome)) return { action: 'fallback' };
	return { action: 'throw', envelope: blockedEnvelope(result, parsed.task) };
}
