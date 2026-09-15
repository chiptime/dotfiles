#!/usr/bin/env bun
/**
 * Deterministic post-approval executor for the task-resolver (spec R5, R6, R7).
 * DEPRECATED (v1, dormant): superseded by enrich.ts.
 *
 * ATTENDED ONLY. Execution authority lives exclusively in the local
 * machine-local `run.json` persisted by the evaluator (run-store): the
 * Notion mirror NEVER authorizes (R5) — Triage Status / Approval State /
 * Draft ID edited in Notion are display state the executor never reads.
 *
 * Fail-closed ordering, each step a hard gate before the next:
 *
 *   1. read run.json + actions.json (local state only)
 *   2. recompute the draft hash — actions must still hash to run.hash
 *   3. session + credential-NAME binding (approval never transfers)
 *   4. FSM legality: <state> + execute must be a listed transition
 *   5. drift re-fetch (read-only GET): revision changed ⇒ approval voids,
 *      run re-enters Analyzing with the voiding replacement hash
 *   6. receipt replay check: an identical executed action refuses the run
 *   7. action shape validation (triage only) before anything dispatches
 *   8. state → Executing, dispatch via NotionWriter.triageTask (429/5xx
 *      retried inside the writer), one append-only receipt per success
 *   9. all receipted ⇒ Executed; any failure ⇒ Failed with partial
 *      receipts preserved (mirror failure never implies success, R6)
 *
 * Receipts are append-only JSONL at <stateRoot>/receipts.jsonl and are
 * never pruned (design D2). Notification is OUTBOUND-ONLY: a best-effort
 * `wsl-notify-send` toast, fire-and-forget — no listener, no webhook,
 * never an inbound actuation channel (R5 decision).
 */

import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
	MAX_RESOLUTION_DRAFT_CHARS,
	NOTION_API_BASE,
	NOTION_API_VERSION,
	NotionWriter,
	type SetTriageAction,
} from "../notion-writer.ts";
import { IllegalTransitionError, transition, type RunState } from "./fsm.ts";
import { canonicalJson, draftHash, shortHash } from "./draft-hash.ts";
import { defaultStateDir, readRun, type RunRecord } from "./run-store.ts";

/** Injectable fetch seam — tests inject fakes, production injects fetch. */
export type FetchLike = (
	input: string | URL | Request,
	init?: RequestInit,
) => Promise<Response>;

export interface ExecuteDeps {
	fetchFn: FetchLike;
	/** Notion token — injected, never persisted by the executor. */
	token: string;
	/** Machine-local state root (tests inject temp dirs). */
	stateDir: string;
	/** The approving session — must match run.json session_id exactly. */
	sessionId: string;
	/** Credential NAME — must match run.json credential_name exactly. */
	credentialName: string;
	/**
	 * Outbound-only toast. Default: wsl-notify-send, fire-and-forget,
	 * best-effort (never blocks, never throws, never inbound).
	 */
	notify?: (message: string) => void;
	/** Retry backoff passed to the NotionWriter (tests shrink it). */
	retryDelayMs?: number;
}

/** One append-only replay-guard record (JSONL line in receipts.jsonl). */
export interface ReceiptRecord {
	draft_id: string;
	/** Full hash of the executed run's approval binding. */
	hash: string;
	action_index: number;
	/** Canonical JSON of the executed action — the replay identity. */
	action: string;
	result_id?: string;
	at: string;
}

export type ExecuteRefusalReason =
	| "illegal-transition"
	| "hash-mismatch"
	| "session-mismatch"
	| "credential-mismatch"
	| "replay"
	| "invalid-action"
	| "drift-check-failed";

export type ExecuteOutcomeKind = "executed" | "failed" | "drift" | "refused";

export interface ExecuteOutcome {
	kind: ExecuteOutcomeKind;
	/** Present iff kind === "refused". */
	reason?: ExecuteRefusalReason;
	/** Receipts appended by this invocation (partial preserved on failure). */
	receipts: number;
	/** Run state after the invocation (unchanged on refusal). */
	state: RunState;
	/** On drift: the replacement hash that voided the approval. */
	newHash?: string;
}

/** Path of the append-only receipt log inside a state root. */
export function receiptsPath(stateDir: string = defaultStateDir()): string {
	return join(stateDir, "receipts.jsonl");
}

/**
 * Parse receipts.jsonl. Foreign, blank, or unparsable lines are skipped
 * (legacy fixtures, partial writes): only well-formed receipts participate
 * in replay matching — never a crash, never a bypass.
 */
export function readReceipts(stateDir: string = defaultStateDir()): ReceiptRecord[] {
	const path = receiptsPath(stateDir);
	if (!existsSync(path)) return [];
	const receipts: ReceiptRecord[] = [];
	for (const line of readFileSync(path, "utf8").split("\n")) {
		const trimmed = line.trim();
		if (trimmed === "") continue;
		try {
			const parsed = JSON.parse(trimmed) as Partial<ReceiptRecord>;
			if (
				typeof parsed.draft_id === "string" &&
				typeof parsed.hash === "string" &&
				typeof parsed.action === "string" &&
				typeof parsed.action_index === "number" &&
				Number.isInteger(parsed.action_index)
			) {
				receipts.push({
					draft_id: parsed.draft_id,
					hash: parsed.hash,
					action_index: Number(parsed.action_index),
					action: parsed.action,
					result_id: parsed.result_id,
					at: String(parsed.at ?? ""),
				});
			}
		} catch {
			// Skip malformed lines — fail closed, never fail open.
		}
	}
	return receipts;
}

/** Append one receipt line (append-only: the replay guard's memory). */
function appendReceipt(record: ReceiptRecord, stateDir: string): void {
	appendFileSync(receiptsPath(stateDir), `${JSON.stringify(record)}\n`, "utf8");
}

/** Rewrite run.json in place (state/hash/revision transitions). */
function writeRunRecord(run: RunRecord, stateDir: string): void {
	writeFileSync(
		join(stateDir, "runs", run.draft_id, "run.json"),
		JSON.stringify(run, null, "\t") + "\n",
		"utf8",
	);
}

/** Read the ordered action list bound into a run. */
function readActions(draftId: string, stateDir: string): unknown[] {
	const raw = JSON.parse(
		readFileSync(join(stateDir, "runs", draftId, "actions.json"), "utf8"),
	);
	if (!Array.isArray(raw)) throw new Error(`actions.json is not an array for ${draftId}`);
	return raw;
}

/** Upfront triage-action shape validation — refuses before anything dispatches. */
function validateTriageAction(action: unknown, index: number): asserts action is SetTriageAction {
	if (action === null || typeof action !== "object" || Array.isArray(action)) {
		throw new InvalidActionError(index, "not an object");
	}
	const candidate = action as Partial<SetTriageAction>;
	if (candidate.action !== "triage") {
		throw new InvalidActionError(index, `action kind '${String(candidate.action)}' is not triage`);
	}
	if (typeof candidate.page_id !== "string" || candidate.page_id.trim() === "") {
		throw new InvalidActionError(index, "missing page_id");
	}
	if (
		candidate.resolution_draft !== undefined &&
		candidate.resolution_draft.length > MAX_RESOLUTION_DRAFT_CHARS
	) {
		throw new InvalidActionError(
			index,
			`resolution_draft exceeds ${MAX_RESOLUTION_DRAFT_CHARS} chars`,
		);
	}
	const hasField =
		candidate.triage_status !== undefined ||
		candidate.resolution_draft !== undefined ||
		candidate.local_context_ref !== undefined ||
		candidate.approval_state !== undefined ||
		candidate.draft_id !== undefined;
	if (!hasField) throw new InvalidActionError(index, "sets no properties");
}

/** Distinguishes shape refusals from unexpected failures. */
export class InvalidActionError extends Error {
	constructor(
		public readonly index: number,
		reason: string,
	) {
		super(`invalid action[${index}]: ${reason}`);
		this.name = "InvalidActionError";
	}
}

/** Default outbound toast: fire-and-forget wsl-notify-send, best-effort. */
function defaultNotify(message: string): void {
	try {
		const proc = Bun.spawn(["wsl-notify-send", "-c", "Task resolver", message], {
			stdin: "ignore",
			stdout: "ignore",
			stderr: "ignore",
		});
		proc.exited.catch(() => {}); // never observe, never block, never throw
	} catch {
		// Best-effort by contract — a missing binary changes nothing.
	}
}

/**
 * Read-only drift probe: fetch the page's last_edited_time. Only the
 * revision is consumed — mirror properties in the response are ignored
 * (the mirror never authorizes, R5).
 */
async function fetchRevision(deps: ExecuteDeps, pageId: string): Promise<string> {
	const response = await deps.fetchFn(`${NOTION_API_BASE}/pages/${pageId}`, {
		method: "GET",
		headers: {
			Authorization: `Bearer ${deps.token}`,
			"Notion-Version": NOTION_API_VERSION,
			"Content-Type": "application/json",
		},
	});
	if (!response.ok) {
		throw new Error(`drift re-fetch failed: HTTP ${response.status}`);
	}
	const page = (await response.json()) as { last_edited_time?: unknown };
	if (typeof page.last_edited_time !== "string") {
		throw new Error("drift re-fetch returned no last_edited_time");
	}
	return page.last_edited_time;
}

function refused(reason: ExecuteRefusalReason, state: RunState): ExecuteOutcome {
	return { kind: "refused", reason, receipts: 0, state };
}

/**
 * Execute one approved run. Refusals are structured outcomes, not throws:
 * a refused run leaves run.json, receipts and Notion byte-untouched (fail
 * closed). Drift rewrites the run to Analyzing with a voiding new hash.
 */
export async function executeRun(
	deps: ExecuteDeps,
	draftId: string,
): Promise<ExecuteOutcome> {
	// 1. Local state only — the mirror is never consulted for authority.
	const run = readRun(draftId, deps.stateDir);
	const actions = readActions(draftId, deps.stateDir);

	// 2. Hash binding: the persisted actions must still hash to run.hash.
	const recomputed = draftHash({
		page_id: run.page_id,
		revision: run.revision,
		actions,
		destination: run.destination,
		draft_id: run.draft_id,
		credential_name: run.credential_name,
		session_id: run.session_id,
	});
	if (recomputed !== run.hash) return refused("hash-mismatch", run.state);

	// 3. Approval never transfers across sessions or credentials.
	if (deps.sessionId !== run.session_id) return refused("session-mismatch", run.state);
	if (deps.credentialName !== run.credential_name) {
		return refused("credential-mismatch", run.state);
	}

	// 4. FSM legality — unlisted transitions refuse fail-closed.
	try {
		transition(run.state, "execute");
	} catch (err) {
		if (err instanceof IllegalTransitionError) {
			return refused("illegal-transition", run.state);
		}
		throw err;
	}

	// 5. Drift re-fetch: a changed revision voids the approval (R6).
	let fetchedRevision: string;
	try {
		fetchedRevision = await fetchRevision(deps, run.page_id);
	} catch (err) {
		console.error(err instanceof Error ? err.message : err);
		return refused("drift-check-failed", run.state);
	}
	if (fetchedRevision !== run.revision) {
		const newHash = draftHash({
			page_id: run.page_id,
			revision: fetchedRevision,
			actions,
			destination: run.destination,
			draft_id: run.draft_id,
			credential_name: run.credential_name,
			session_id: run.session_id,
		});
		const drifted = transition(run.state, "drift"); // → Analyzing
		run.state = drifted;
		run.revision = fetchedRevision;
		run.hash = newHash;
		run.updated_at = new Date().toISOString();
		writeRunRecord(run, deps.stateDir);
		return { kind: "drift", receipts: 0, state: drifted, newHash };
	}

	// 6. Receipt replay guard: an identical executed action refuses the run.
	const executed = readReceipts(deps.stateDir);
	const pending = actions.map((action) => canonicalJson(action));
	if (pending.some((canonical) => executed.some((r) => r.action === canonical))) {
		return refused("replay", run.state);
	}

	// 7. Shape validation for every action before anything dispatches.
	try {
		actions.forEach((action, index) => validateTriageAction(action, index));
	} catch (err) {
		if (err instanceof InvalidActionError) return refused("invalid-action", run.state);
		throw err;
	}

	// 8. Dispatch under the FSM: Approved → Executing → Executed | Failed.
	run.state = transition(run.state, "execute");
	run.updated_at = new Date().toISOString();
	writeRunRecord(run, deps.stateDir);

	const notify = deps.notify ?? defaultNotify;
	const writer = new NotionWriter({
		token: deps.token,
		fetchFn: deps.fetchFn,
		dataSourceId: run.destination,
		retryDelayMs: deps.retryDelayMs,
	});

	let appended = 0;
	for (const [index, action] of actions.entries()) {
		const typed = action as SetTriageAction;
		try {
			const result = await writer.triageTask(typed);
			appendReceipt(
				{
					draft_id: run.draft_id,
					hash: run.hash,
					action_index: index,
					action: pending[index],
					result_id: result.id,
					at: new Date().toISOString(),
				},
				deps.stateDir,
			);
			appended++;
		} catch (err) {
			// Partial receipts preserved; the mirror was never told success.
			console.error(err instanceof Error ? err.message : err);
			run.state = transition(run.state, "fail"); // Executing → Failed
			run.updated_at = new Date().toISOString();
			writeRunRecord(run, deps.stateDir);
			notify(`Task resolver: FAILED ${run.draft_id} (${shortHash(run.hash)}) — receipts: ${appended}/${actions.length}`);
			return { kind: "failed", receipts: appended, state: run.state };
		}
	}

	run.state = transition(run.state, "complete"); // Executing → Executed
	run.updated_at = new Date().toISOString();
	writeRunRecord(run, deps.stateDir);
	notify(`Task resolver: executed ${run.draft_id} (${shortHash(run.hash)}) — ${appended} receipt(s)`);
	return { kind: "executed", receipts: appended, state: run.state };
}

/**
 * Attended CLI: bun src/resolver/execute.ts <draft-id> [--session <id>].
 * The session id is mandatory (TASK_RESOLVER_SESSION_ID or --session):
 * execution without session binding is a hard refusal. The credential
 * NAME is derived from whichever env var provided the token — the value
 * itself never persists.
 */
async function main(): Promise<number> {
	const draftId = process.argv[2];
	if (!draftId) {
		console.error("usage: bun src/resolver/execute.ts <draft-id> [--session <id>]");
		return 1;
	}
	const sessionFlag = process.argv.indexOf("--session");
	const sessionId =
		sessionFlag !== -1 ? process.argv[sessionFlag + 1] : process.env.TASK_RESOLVER_SESSION_ID;
	if (!sessionId) {
		console.error("missing session binding: pass --session <id> or TASK_RESOLVER_SESSION_ID");
		return 1;
	}
	let credentialName: string | undefined;
	let token: string | undefined;
	if (process.env.NOTION_TOKEN) {
		credentialName = "NOTION_TOKEN";
		token = process.env.NOTION_TOKEN;
	} else if (process.env.NOTION_CLECE) {
		credentialName = "NOTION_CLECE";
		token = process.env.NOTION_CLECE;
	}
	if (!token || !credentialName) {
		console.error("missing NOTION_TOKEN / NOTION_CLECE environment variable");
		return 1;
	}

	const outcome = await executeRun(
		{
			fetchFn: globalThis.fetch,
			token,
			stateDir: defaultStateDir(),
			sessionId,
			credentialName,
		},
		draftId,
	);
	console.log(JSON.stringify(outcome));
	return outcome.kind === "executed" || outcome.kind === "drift" ? 0 : 1;
}

if (import.meta.main) {
	process.exit(await main());
}
