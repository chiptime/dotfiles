import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	executeRun,
	readReceipts,
	receiptsPath,
	type ExecuteDeps,
} from "../src/resolver/execute.ts";
import { DEFAULT_DATA_SOURCE_ID } from "../src/notion-writer.ts";
import { canonicalJson } from "../src/resolver/draft-hash.ts";
import { persistRun, readRun, type PersistRunInput } from "../src/resolver/run-store.ts";

// Slice B executor suite (spec R5, R6, R7). All Notion I/O is mocked through
// the fetchFn injection seam (detector/writer precedent): zero live calls,
// zero credentials. Every temp state dir is machine-local and torn down.

const ROOT = "/data/work/sis";
const PAGE_ID = "page-exec-1";
const REVISION = "2026-09-15T10:00:00.000Z";
const CREDENTIAL = "NOTION_MAIN";
const SESSION = "session-7";

const TRIAGE_ACTION = {
	action: "triage",
	page_id: PAGE_ID,
	triage_status: "💡 Acción",
	resolution_draft: "Draft reply text",
} as const;

let stateDir: string;
beforeEach(() => {
	stateDir = mkdtempSync(join(tmpdir(), "resolver-execute-"));
});
afterEach(() => {
	rmSync(stateDir, { recursive: true, force: true });
});

function baseRun(overrides: Partial<PersistRunInput> = {}): PersistRunInput {
	return {
		roots: [ROOT],
		draft_id: "run000000001",
		page_id: PAGE_ID,
		revision: REVISION,
		destination: DEFAULT_DATA_SOURCE_ID,
		state: "Approved",
		credentialName: CREDENTIAL,
		sessionId: SESSION,
		metrics: { wallMs: 1000, tokens: 500, costUsd: 0.01, reads: 1 },
		draftMd: "# Draft\n\nReply text.",
		evidence: [
			{
				citedPath: `${ROOT}/src/api.ts`,
				realpath: `${ROOT}/src/api.ts`,
				bytes: 800,
				excerpt: "export const version = 2;",
			},
		],
		patchDiff: "--- a\n+++ b\n",
		actions: [{ ...TRIAGE_ACTION }],
		...overrides,
	};
}

interface MockNotionOptions {
	/** Revision the drift-check GET reports for the page. */
	fetchedRevision?: string;
	/** Sequential PATCH outcomes: "ok" | number (HTTP status). */
	patchScript?: Array<"ok" | number>;
}

interface CapturedCall {
	method: string;
	url: string;
	body?: any;
}

/**
 * Route-level Notion mock: GET /pages/:id answers the drift re-fetch,
 * PATCH /pages/:id walks the scripted outcome sequence. The GET body
 * deliberately mirrors an "Approval State: Aprobado" edit — the mirror
 * NEVER authorizes (R5) and the executor must ignore it.
 */
function mockNotion(options: MockNotionOptions = {}) {
	const fetchedRevision = options.fetchedRevision ?? REVISION;
	const patchScript = options.patchScript ?? ["ok"];
	const calls: CapturedCall[] = [];
	let patchIndex = 0;

	const fetchFn = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
		const url = typeof input === "string" ? input : input.toString();
		const method = init?.method ?? "GET";
		calls.push({ method, url, body: init?.body ? JSON.parse(init.body as string) : undefined });

		if (method === "GET" && url.endsWith(`/pages/${PAGE_ID}`)) {
			return new Response(
				JSON.stringify({
					id: PAGE_ID,
					last_edited_time: fetchedRevision,
					properties: {
						"Approval State": { select: { name: "Aprobado" } }, // mirror bait — not authority
						"Triage Status": { select: { name: "🤖 Auto" } },
					},
				}),
				{ status: 200, headers: { "Content-Type": "application/json" } },
			);
		}
		if (method === "PATCH" && url.endsWith(`/pages/${PAGE_ID}`)) {
			const step = patchScript[patchIndex] ?? "ok";
			patchIndex++;
			if (step === "ok") {
				return new Response(JSON.stringify({ id: PAGE_ID }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			return new Response(JSON.stringify({ message: `HTTP ${step}`, code: "mock" }), {
				status: step,
				headers: { "Content-Type": "application/json" },
			});
		}
		return new Response(JSON.stringify({ message: "unexpected call" }), { status: 599 });
	};

	return {
		fetchFn,
		calls,
		patches: () => calls.filter((c) => c.method === "PATCH"),
		gets: () => calls.filter((c) => c.method === "GET"),
	};
}

function depsFor(
	mock: ReturnType<typeof mockNotion>,
	overrides: Partial<ExecuteDeps> = {},
): ExecuteDeps {
	return {
		fetchFn: mock.fetchFn,
		token: "secret_test_token",
		stateDir,
		sessionId: SESSION,
		credentialName: CREDENTIAL,
		retryDelayMs: 1,
		...overrides,
	};
}

describe("executeRun (R5/R6/R7)", () => {
	test("executes an approved run: Executed state, receipt appended, toast fired", async () => {
		const mock = mockNotion();
		const toasts: string[] = [];
		persistRun(baseRun(), stateDir);

		const outcome = await executeRun(
			depsFor(mock, { notify: (m) => toasts.push(m) }),
			"run000000001",
		);

		expect(outcome.kind).toBe("executed");
		expect(outcome.state).toBe("Executed");
		expect(readRun("run000000001", stateDir).state).toBe("Executed");

		const receipts = readReceipts(stateDir);
		expect(receipts).toHaveLength(1);
		expect(receipts[0].hash).toBe(readRun("run000000001", stateDir).hash);
		expect(receipts[0].action).toBe(canonicalJson({ ...TRIAGE_ACTION }));
		expect(receipts[0].draft_id).toBe("run000000001");

		expect(mock.patches()).toHaveLength(1);
		expect(mock.patches()[0].body.properties["Triage Status"].select.name).toBe("💡 Acción");
		// Outbound-only, fire-and-forget toast on the terminal state (R5).
		expect(toasts).toHaveLength(1);
		expect(toasts[0]).toContain("run000000001");
	});

	test("identical action replay is refused by its receipt (R6)", async () => {
		const mock = mockNotion();
		const run = persistRun(baseRun(), stateDir);
		// A prior execution already receipted this exact action.
		writeFileSync(
			receiptsPath(stateDir),
			`${JSON.stringify({
				draft_id: "run000000001",
				hash: run.run.hash,
				action_index: 0,
				action: canonicalJson({ ...TRIAGE_ACTION }),
				result_id: PAGE_ID,
				at: "2026-09-15T10:05:00.000Z",
			})}\n`,
			"utf8",
		);

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("refused");
		expect(outcome.reason).toBe("replay");
		// Fail closed: nothing dispatched, state untouched, receipt not duplicated.
		expect(mock.patches()).toHaveLength(0);
		expect(readRun("run000000001", stateDir).state).toBe("Approved");
		expect(readReceipts(stateDir)).toHaveLength(1);
	});

	test("source-revision drift voids approval: Analyzing with a new hash (R5/R6)", async () => {
		const newerRevision = "2026-09-15T11:30:00.000Z";
		const mock = mockNotion({ fetchedRevision: newerRevision });
		const run = persistRun(baseRun(), stateDir);
		const approvedHash = run.run.hash;

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("drift");
		expect(mock.patches()).toHaveLength(0); // approval voided BEFORE dispatch

		const after = readRun("run000000001", stateDir);
		expect(after.state).toBe("Analyzing");
		expect(after.revision).toBe(newerRevision);
		expect(after.hash).not.toBe(approvedHash);
		expect(outcome.newHash).toBe(after.hash);
	});

	test("illegal transition refused: a Detected run never executes, mirror approval ignored (R6/R5)", async () => {
		const mock = mockNotion(); // GET body says Approval State: Aprobado — must not authorize
		persistRun(baseRun({ state: "Detected" }), stateDir);

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("refused");
		expect(outcome.reason).toBe("illegal-transition");
		expect(mock.patches()).toHaveLength(0);
		expect(readRun("run000000001", stateDir).state).toBe("Detected"); // unchanged
		expect(readReceipts(stateDir)).toHaveLength(0);
	});

	test("429 is retried and the receipt is still recorded (R7)", async () => {
		const mock = mockNotion({ patchScript: [429, "ok"] });
		persistRun(baseRun(), stateDir);

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("executed");
		expect(mock.patches()).toHaveLength(2); // first 429, then success
		expect(readReceipts(stateDir)).toHaveLength(1);
		expect(readRun("run000000001", stateDir).state).toBe("Executed");
	});

	test("hash mismatch between run.json and actions.json refuses execution (R5)", async () => {
		const mock = mockNotion();
		persistRun(baseRun(), stateDir);
		// Tamper with the persisted actions after the hash was bound.
		writeFileSync(
			join(stateDir, "runs", "run000000001", "actions.json"),
			JSON.stringify([{ ...TRIAGE_ACTION, resolution_draft: "tampered" }], null, "\t"),
			"utf8",
		);

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("refused");
		expect(outcome.reason).toBe("hash-mismatch");
		expect(mock.patches()).toHaveLength(0);
		expect(readRun("run000000001", stateDir).state).toBe("Approved");
	});

	test("approval does not transfer across sessions (R5)", async () => {
		const mock = mockNotion();
		persistRun(baseRun(), stateDir);

		const outcome = await executeRun(depsFor(mock, { sessionId: "other-session" }), "run000000001");

		expect(outcome.kind).toBe("refused");
		expect(outcome.reason).toBe("session-mismatch");
		expect(mock.patches()).toHaveLength(0);
	});

	test("approval does not transfer across credential names (R5)", async () => {
		const mock = mockNotion();
		persistRun(baseRun(), stateDir);

		const outcome = await executeRun(
			depsFor(mock, { credentialName: "NOTION_OTHER" }),
			"run000000001",
		);

		expect(outcome.kind).toBe("refused");
		expect(outcome.reason).toBe("credential-mismatch");
		expect(mock.patches()).toHaveLength(0);
	});

	test("partial failure: Failed state, earlier receipts preserved (R6/R7)", async () => {
		const mock = mockNotion({ patchScript: ["ok", 400] });
		persistRun(
			baseRun({
				actions: [
					{ ...TRIAGE_ACTION },
					{ ...TRIAGE_ACTION, triage_status: "✋ Manual" },
				],
			}),
			stateDir,
		);

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("failed");
		expect(outcome.state).toBe("Failed");
		expect(readRun("run000000001", stateDir).state).toBe("Failed");
		// The first action's receipt survives the second action's failure.
		expect(readReceipts(stateDir)).toHaveLength(1);
		expect(readReceipts(stateDir)[0].action_index).toBe(0);
		// 400 does not retry: exactly one attempt per action.
		expect(mock.patches()).toHaveLength(2);
	});

	test("a non-triage action kind is refused before anything dispatches (R7)", async () => {
		const mock = mockNotion();
		persistRun(baseRun({ actions: [{ action: "create", title: "x", notes: "y" }] }), stateDir);

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("refused");
		expect(outcome.reason).toBe("invalid-action");
		expect(mock.patches()).toHaveLength(0);
		expect(readReceipts(stateDir)).toHaveLength(0);
	});

	test("an already-Executed run refuses re-execution (R6 replay guard)", async () => {
		const mock = mockNotion();
		persistRun(baseRun({ state: "Executed" }), stateDir);

		const outcome = await executeRun(depsFor(mock), "run000000001");

		expect(outcome.kind).toBe("refused");
		expect(outcome.reason).toBe("illegal-transition");
		expect(mock.patches()).toHaveLength(0);
	});

	test("receipts file is created only when an action lands", async () => {
		const mock = mockNotion();
		persistRun(baseRun({ state: "Detected" }), stateDir);
		await executeRun(depsFor(mock), "run000000001");
		expect(existsSync(receiptsPath(stateDir))).toBe(false);
	});
});
