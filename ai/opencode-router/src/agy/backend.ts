/**
 * Provider-neutral exploration seam (design decision 9): quota gate →
 * contained run → classify → validate → persist-on-success → typed result.
 * Phase D imports this with custom deps; only prompt/delegation are throwaway.
 */
import { readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { buildResult, classifyRun, type ExploreRequest, type ExploreResult, type Receipt } from './outcomes';
import { decidePool, parseSnapshot, parseSnapshotDir, poolForModel, type PoolDecision } from './quota';
import { runAgy, DEFAULT_STALL_MS, type AgyUsage, type SpawnRun } from './spawn';
import { persistExploration, type PersistOutcome } from './persist';
import { porcelain, REQUIRED_SECTIONS, validateExploration } from './validate';

export interface BackendRunCall {
	/** Resume this agy conversation (recovered from the first run's init event). */
	resumeConversationId?: string;
	/** Prompt for this invocation; defaults to the full exploration prompt. */
	prompt?: string;
}

export interface BackendDeps {
	decide: () => PoolDecision;
	/** Primary attempt (no args) and, at most once, the resume attempt (runExploration passes a BackendRunCall). */
	run: (call?: BackendRunCall) => SpawnRun | Promise<SpawnRun>;
	readArtifact: () => string;
	persist: (artifactText: string) => PersistOutcome;
	porcelain: () => string;
}

export interface DepsOverrides { agyBin?: string; quotaFile?: string; openspecRoot?: string; timeoutMs?: number }

/**
 * Compose the artifact-contract prompt for agy. Live e2e (2026-08-21) proved a
 * bare brief makes agy answer on stdout and never write the file, so the prompt
 * must pin the artifact itself: ./exploration.md, canonical sections (single
 * source of truth: REQUIRED_SECTIONS), verdict, containment, brief verbatim.
 */
export function buildExplorationPrompt(req: ExploreRequest): string {
	const last = REQUIRED_SECTIONS[REQUIRED_SECTIONS.length - 1];
	return [
		`You are the SDD exploration executor for change "${req.change}".`,
		'',
		'Write the COMPLETE exploration to ./exploration.md in your current working directory, as Markdown. The file MUST exist and be non-empty when you are done. Anything you print to stdout is ignored; the artifact file is the only deliverable.',
		'',
		'The document MUST contain exactly these section headers, in this order:',
		REQUIRED_SECTIONS.map((s) => `### ${s}`).join('\n'),
		'',
		`The final section (### ${last}) MUST end with an explicit verdict line: Yes or No.`,
		'',
		'Create or modify no other files: ./exploration.md is the only file you may write. Leave the repository, git state, and every configuration untouched.',
		'',
		'## Brief',
		'',
		req.brief,
	].join('\n');
}

/**
 * Continuation prompt for the one-shot resume. LIVE PROBE (2026-09-09):
 * `agy --conversation <id> -p "…"` continues the SAME conversation with full
 * memory — a keyword planted in turn 1 was recalled verbatim in turn 2
 * (num_turns 2, same conversation_id) — so the resume re-pins the artifact
 * contract and asks agy to finish instead of starting over.
 */
export const RESUME_PROMPT =
	'Continue the previous task to completion. Remember the artifact contract: write the COMPLETE exploration to ./exploration.md in your current working directory (canonical sections, explicit Yes/No verdict), then stop.';

/** Compose the agy-backed deps for a request; over lets the CLI inject paths. Missing snapshot → one allowed attempt (stale path). */
export function depsFor(req: ExploreRequest, workdir: string, over: DepsOverrides = {}): BackendDeps {
	// Default is the statusline v2 DIRECTORY; explicit option and AI_QUOTAS_FILE remain FILE overrides (old merged shape).
	const quotaLoc = over.quotaFile ?? process.env.AI_QUOTAS_FILE ?? `${homedir()}/.local/state/ai-quotas`;
	const bin = over.agyBin ?? process.env.AGY_BIN ?? 'agy';
	// Stall watchdog: env override for experiments, else the evidence-based
	// default (see DEFAULT_STALL_MS in spawn.ts — 600s; real runs stream
	// intermediate step_update events, so silence means a hung run).
	const stallMs = Number(process.env.AI_EXPLORE_STALL_MS) || DEFAULT_STALL_MS;
	return {
		decide: () => {
			try {
				const st = statSync(quotaLoc, { throwIfNoEntry: false });
				const snap = st?.isDirectory() ? parseSnapshotDir(quotaLoc) : st?.isFile() ? parseSnapshot(readFileSync(quotaLoc, 'utf8')) : null;
				return snap ? decidePool(snap, req.model) : { pool: poolForModel(req.model), allowed: true, reason: 'stale_snapshot' };
			} catch {
				return { pool: poolForModel(req.model), allowed: true, reason: 'stale_snapshot' };
			}
		},
		run: (call) =>
			runAgy({
				bin,
				prompt: call?.prompt ?? buildExplorationPrompt(req),
				workdir,
				timeoutMs: over.timeoutMs ?? 600_000,
				model: req.model,
				stallMs,
				resumeConversationId: call?.resumeConversationId,
			}),
		readArtifact: () => { try { return readFileSync(`${workdir}/exploration.md`, 'utf8'); } catch { return ''; } },
		persist: (text) => persistExploration({ store: req.store, change: req.change, artifactText: text, openspecRoot: over.openspecRoot ?? `${req.repo}/openspec`, workdir }),
		porcelain: () => porcelain(req.repo),
	};
}

/** Run one exploration end-to-end and return the typed result. Exactly one owner persists, only on validated success. */
export async function runExploration(req: ExploreRequest, deps?: BackendDeps): Promise<ExploreResult> {
	const d = deps ?? depsFor(req, `${homedir()}/.cache/ai-stack/agy-explore/${req.change}/${Date.now()}`);
	const emptyReceipt: Receipt = { store: req.store, wroteOpenspec: false, engramRequired: false };
	const gate = d.decide();
	if (!gate.allowed) return buildResult('quota_unavailable', { elapsedMs: 0, reason: gate.reason, receipt: emptyReceipt });
	const before = d.porcelain();
	const readArtifactFor = (r: SpawnRun) => (r.exitCode === 0 ? d.readArtifact() : '');
	const classifyAttempt = (r: SpawnRun, artifactText: string) =>
		classifyRun({ exitCode: r.exitCode, timedOut: r.timedOut, stalled: r.stalled, log: r.log, spawnError: r.spawnError, artifactBytes: Buffer.byteLength(artifactText), envelope: r.envelope });
	const run = await d.run();
	const artifactText = readArtifactFor(run);
	const cls = classifyAttempt(run, artifactText);
	// RESUME-ONCE: if the FIRST attempt classifies as timeout (our hard kill,
	// the stall watchdog, or agy's own print wait) and we recovered a
	// conversation id from its init event, make exactly ONE continuation
	// attempt with the same timeoutMs — worst case ≈ 2× budget. Guarded by
	// this single `if` (no loop): at most one resume per runExploration call.
	let finalRun = run;
	let finalCls = cls;
	let finalArtifact = artifactText;
	if (cls.outcome === 'timeout' && run.conversationId) {
		finalRun = await d.run({ resumeConversationId: run.conversationId, prompt: RESUME_PROMPT });
		finalArtifact = readArtifactFor(finalRun);
		finalCls = classifyAttempt(finalRun, finalArtifact);
	}
	// Observability from agy's envelope, attached only when present and only on
	// results that follow a real run (the quota gate never runs agy). The init
	// conversation id (stream runner) wins over the envelope's own field.
	const observed: { conversationId?: string; usage?: AgyUsage } = {};
	const conversationId = finalRun.conversationId ?? finalRun.envelope?.conversation_id;
	if (conversationId !== undefined) observed.conversationId = conversationId;
	if (finalRun.envelope?.usage !== undefined) observed.usage = finalRun.envelope.usage;
	if (finalCls.outcome !== 'success') return buildResult(finalCls.outcome, { elapsedMs: finalRun.elapsedMs, reason: finalCls.reason, receipt: emptyReceipt, ...observed });
	const v = validateExploration({ artifactText: finalArtifact, porcelainBefore: before, porcelainAfter: d.porcelain() });
	if (!v.ok) return buildResult('artifact_validation_failure', { elapsedMs: finalRun.elapsedMs, reason: v.problems.join(';'), receipt: emptyReceipt, ...observed });
	const p = d.persist(finalArtifact);
	return buildResult('success', { elapsedMs: finalRun.elapsedMs, reason: 'ok', artifactPath: p.artifactDest, sha256: p.sha256, receipt: p.receipt, ...observed });
}
