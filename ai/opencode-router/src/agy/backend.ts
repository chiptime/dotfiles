/**
 * Provider-neutral exploration seam (design decision 9): quota gate →
 * contained run → classify → validate → persist-on-success → typed result.
 * Phase D imports this with custom deps; only prompt/delegation are throwaway.
 */
import { readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { buildResult, classifyRun, type ExploreRequest, type ExploreResult, type Receipt } from './outcomes';
import { decidePool, parseSnapshot, parseSnapshotDir, poolForModel, type PoolDecision } from './quota';
import { runAgy, type SpawnRun } from './spawn';
import { persistExploration, type PersistOutcome } from './persist';
import { porcelain, REQUIRED_SECTIONS, validateExploration } from './validate';

export interface BackendDeps {
	decide: () => PoolDecision;
	run: () => SpawnRun;
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

/** Compose the agy-backed deps for a request; over lets the CLI inject paths. Missing snapshot → one allowed attempt (stale path). */
export function depsFor(req: ExploreRequest, workdir: string, over: DepsOverrides = {}): BackendDeps {
	// Default is the statusline v2 DIRECTORY; explicit option and AI_QUOTAS_FILE remain FILE overrides (old merged shape).
	const quotaLoc = over.quotaFile ?? process.env.AI_QUOTAS_FILE ?? `${homedir()}/.local/state/ai-quotas`;
	const bin = over.agyBin ?? process.env.AGY_BIN ?? 'agy';
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
		run: () => runAgy({ bin, prompt: buildExplorationPrompt(req), workdir, timeoutMs: over.timeoutMs ?? 300_000 }),
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
	const run = d.run();
	const artifactText = run.exitCode === 0 ? d.readArtifact() : '';
	const signal = { exitCode: run.exitCode, timedOut: run.timedOut, log: run.log, spawnError: run.spawnError, artifactBytes: Buffer.byteLength(artifactText) };
	const cls = classifyRun(signal);
	if (cls.outcome !== 'success') return buildResult(cls.outcome, { elapsedMs: run.elapsedMs, reason: cls.reason, receipt: emptyReceipt });
	const v = validateExploration({ artifactText, porcelainBefore: before, porcelainAfter: d.porcelain() });
	if (!v.ok) return buildResult('artifact_validation_failure', { elapsedMs: run.elapsedMs, reason: v.problems.join(';'), receipt: emptyReceipt });
	const p = d.persist(artifactText);
	return buildResult('success', { elapsedMs: run.elapsedMs, reason: 'ok', artifactPath: p.artifactDest, sha256: p.sha256, receipt: p.receipt });
}
