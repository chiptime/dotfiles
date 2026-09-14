/**
 * Versioned local run store for the task-resolver (spec R4, R5, R9).
 *
 * Machine-local layout, NEVER committed (dotfiles-context rule):
 *
 *   <stateRoot>/runs/<draft-id>/{run.json,draft.md,evidence.json,patch.diff,actions.json}
 *   <stateRoot>/pending-queue.json    detector → evaluator handoff (design D1)
 *   <stateRoot>/seen.json             per-page dedupe memory
 *   <stateRoot>/receipts.jsonl        append-only replay guard (written in WU-B)
 *
 * run.json records the credential NAME and session id — secret VALUES never
 * persist anywhere (R5) — plus the metrics R9 needs (tokens, cost, wall
 * latency, reads, evidence bytes). persistRun() runs the evidence gate
 * FIRST: a violation leaves zero bytes on disk (fail closed, R2/R8).
 * Terminal runs older than 90 days are pruned; receipts and seen memory are
 * never pruned (retention, design D2).
 */

import {
	existsSync,
	mkdirSync,
	readFileSync,
	readdirSync,
	renameSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import type { RunState } from "./fsm.ts";
import { TERMINAL_STATES } from "./fsm.ts";
import { draftHash } from "./draft-hash.ts";
import type { TaskUsage } from "./budgets.ts";
import { checkEvidence, EvidenceGateError, type EvidenceItem } from "./evidence-gate.ts";

/** Tests (and only tests) override the state root through this env var. */
export const STATE_DIR_ENV = "TASK_RESOLVER_STATE_DIR";

export function defaultStateDir(): string {
	return (
		process.env[STATE_DIR_ENV] ??
		join(homedir(), ".local", "state", "task-resolver")
	);
}

/** Usage/metrics of one run — same shape budgets.ts ceilings compare against. */
export type RunMetrics = TaskUsage;

export interface RunRecord {
	version: 1;
	draft_id: string;
	page_id: string;
	revision: string;
	/** Notion destination the actions target — part of the hash binding. */
	destination: string;
	state: RunState;
	/** SHA-256 of the canonical approval binding (draft-hash.ts). */
	hash: string;
	/** Credential NAME only — the secret value never persists (R5). */
	credential_name: string;
	session_id: string;
	metrics: RunMetrics;
	created_at: string;
	updated_at: string;
}

/** Caller-supplied metrics; evidenceBytes is computed, not trusted. */
export type ReportedMetrics = Omit<TaskUsage, "evidenceBytes">;

export interface PersistRunInput {
	/** realpath-pinned approved roots — rechecked by the gate before persist. */
	roots: string[];
	draft_id: string;
	page_id: string;
	revision: string;
	destination: string;
	state: RunState;
	credentialName: string;
	sessionId: string;
	metrics: ReportedMetrics;
	draftMd: string;
	evidence: EvidenceItem[];
	/** Inert unified diff — persisted as data, never applied here (R4). */
	patchDiff: string;
	/** Ordered action list — order is significant (hash binding). */
	actions: unknown[];
}

export interface PersistedRun {
	dir: string;
	run: RunRecord;
}

/** draft_id becomes a directory name: no traversal, no separators, bounded. */
const DRAFT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/**
 * Persist one complete hashed run. Ordering is the security property:
 * validate id → gate the evidence → only then create directories and write
 * artifacts, with run.json last so its presence implies a complete run.
 */
export function persistRun(
	input: PersistRunInput,
	stateDir: string = defaultStateDir(),
): PersistedRun {
	if (!DRAFT_ID_PATTERN.test(input.draft_id)) {
		throw new Error(`invalid draft_id: ${input.draft_id}`);
	}
	const verdict = checkEvidence({
		roots: input.roots,
		evidence: input.evidence,
	});
	if (!verdict.allowed) throw new EvidenceGateError(verdict.violations);

	const evidenceBytes = input.evidence.reduce((sum, entry) => sum + entry.bytes, 0);
	const hash = draftHash({
		page_id: input.page_id,
		revision: input.revision,
		actions: input.actions,
		destination: input.destination,
		draft_id: input.draft_id,
		credential_name: input.credentialName,
		session_id: input.sessionId,
	});
	const now = new Date().toISOString();
	const run: RunRecord = {
		version: 1,
		draft_id: input.draft_id,
		page_id: input.page_id,
		revision: input.revision,
		destination: input.destination,
		state: input.state,
		hash,
		credential_name: input.credentialName,
		session_id: input.sessionId,
		metrics: { ...input.metrics, evidenceBytes },
		created_at: now,
		updated_at: now,
	};

	const dir = join(stateDir, "runs", input.draft_id);
	mkdirSync(dir, { recursive: true });
	writeFileSync(join(dir, "draft.md"), input.draftMd, "utf8");
	writeFileSync(
		join(dir, "evidence.json"),
		JSON.stringify(input.evidence, null, "\t") + "\n",
		"utf8",
	);
	writeFileSync(join(dir, "patch.diff"), input.patchDiff, "utf8");
	writeFileSync(
		join(dir, "actions.json"),
		JSON.stringify(input.actions, null, "\t") + "\n",
		"utf8",
	);
	writeFileSync(
		join(dir, "run.json"),
		JSON.stringify(run, null, "\t") + "\n",
		"utf8",
	);
	return { dir, run };
}

/** Read back a persisted run — the executor's only view of local state. */
export function readRun(draftId: string, stateDir: string = defaultStateDir()): RunRecord {
	return JSON.parse(
		readFileSync(join(stateDir, "runs", draftId, "run.json"), "utf8"),
	);
}

/** Terminal runs older than 90 days are pruned; everything else survives. */
export const RUN_RETENTION_MS = 90 * 24 * 60 * 60 * 1000;

/**
 * Delete runs/<id> directories whose run.json is in a terminal state AND
 * whose updated_at is strictly older than the retention window. Never
 * touches anything outside runs/ — receipts.jsonl, seen.json and
 * pending-queue.json outlive every run. Unreadable or unparsable run
 * directories are kept (fail closed against data loss). Returns the pruned
 * draft ids.
 */
export function pruneTerminalRuns(
	stateDir: string = defaultStateDir(),
	now: number = Date.now(),
	retentionMs: number = RUN_RETENTION_MS,
): string[] {
	const runsDir = join(stateDir, "runs");
	if (!existsSync(runsDir)) return [];
	const pruned: string[] = [];
	for (const entry of readdirSync(runsDir, { withFileTypes: true })) {
		if (!entry.isDirectory()) continue;
		const id = entry.name;
		try {
			const run = JSON.parse(
				readFileSync(join(runsDir, id, "run.json"), "utf8"),
			) as RunRecord;
			const updated = Date.parse(run.updated_at);
			if (
				TERMINAL_STATES.includes(run.state) &&
				Number.isFinite(updated) &&
				now - updated > retentionMs
			) {
				rmSync(join(runsDir, id), { recursive: true });
				pruned.push(id);
			}
		} catch {
			// No run.json or unparsable: never delete what we cannot prove prunable.
		}
	}
	return pruned.sort();
}

/** Detector → evaluator handoff items (design D1). */
export interface QueueItem {
	page_id: string;
	revision: string;
	title: string;
	detected_at: string;
}

export function readQueue(stateDir: string = defaultStateDir()): QueueItem[] {
	const path = join(stateDir, "pending-queue.json");
	if (!existsSync(path)) return [];
	return JSON.parse(readFileSync(path, "utf8")) as QueueItem[];
}

/** Replace the queue atomically (detector owns replacement semantics). */
export function writeQueue(items: QueueItem[], stateDir: string = defaultStateDir()): void {
	writeAtomic(join(stateDir, "pending-queue.json"), JSON.stringify(items, null, "\t") + "\n");
}

/** seen.json shape: page_id → last observed revision. */
export type SeenIndex = Record<string, { revision: string; seen_at: string }>;

export function readSeen(stateDir: string = defaultStateDir()): SeenIndex {
	const path = join(stateDir, "seen.json");
	if (!existsSync(path)) return {};
	return JSON.parse(readFileSync(path, "utf8")) as SeenIndex;
}

/** Record a page revision as seen (read-modify-write merge). */
export function markSeen(
	pageId: string,
	revision: string,
	stateDir: string = defaultStateDir(),
	at: string = new Date().toISOString(),
): SeenIndex {
	const seen = { ...readSeen(stateDir), [pageId]: { revision, seen_at: at } };
	writeAtomic(join(stateDir, "seen.json"), JSON.stringify(seen, null, "\t") + "\n");
	return seen;
}

/** Torn-read protection for the cron-writer files: temp file + same-dir rename. */
function writeAtomic(path: string, data: string): void {
	mkdirSync(dirname(path), { recursive: true });
	const tmp = join(dirname(path), `.${Math.random().toString(36).slice(2)}.tmp`);
	writeFileSync(tmp, data, "utf8");
	renameSync(tmp, path);
}

if (import.meta.main) {
	// Operator smoke: print the resolved state root (read-only, never writes).
	console.log(defaultStateDir());
}
