import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	readdirSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { draftHash } from "../src/resolver/draft-hash.ts";
import type { RunState } from "../src/resolver/fsm.ts";
import {
	STATE_DIR_ENV,
	RUN_RETENTION_MS,
	defaultStateDir,
	markSeen,
	persistRun,
	pruneTerminalRuns,
	readQueue,
	readRun,
	readSeen,
	writeQueue,
	type PersistRunInput,
} from "../src/resolver/run-store.ts";
import type { EvidenceItem } from "../src/resolver/evidence-gate.ts";

// Tests NEVER touch the real ~/.local/state: every store gets a fresh temp
// dir via mkdtemp, torn down after each test (task 2.2 contract).

const ROOT = "/data/work/sis";
const DAY_MS = 24 * 60 * 60 * 1000;

let stateDir: string;
beforeEach(() => {
	stateDir = mkdtempSync(join(tmpdir(), "resolver-run-store-"));
});
afterEach(() => {
	rmSync(stateDir, { recursive: true, force: true });
});

function baseInput(overrides: Partial<PersistRunInput> = {}): PersistRunInput {
	return {
		roots: [ROOT],
		draft_id: "d1a2b3c4e5f6",
		page_id: "page-1",
		revision: "rev-1",
		destination: "Tareas",
		state: "Pending approval",
		credentialName: "NOTION_MAIN",
		sessionId: "session-7",
		metrics: { wallMs: 1000, tokens: 500, costUsd: 0.01, reads: 1 },
		draftMd: "# Draft\n\nReply text.",
		evidence: [
			{
				citedPath: `${ROOT}/src/api.ts`,
				realpath: `${ROOT}/src/api.ts`,
				bytes: 800,
				excerpt: "export const api = 1;",
				revision: "rev-1",
			},
		],
		patchDiff: "--- a/src/api.ts\n+++ b/src/api.ts\n@@ -1 +1 @@\n",
		actions: [{ action: "resolve", page_id: "page-1" }],
		...overrides,
	};
}

describe("complete hashed run persists all elements (R4)", () => {
	test("all five artifacts land under runs/<draft-id>", () => {
		const { dir, run } = persistRun(baseInput(), stateDir);
		expect(dir).toBe(join(stateDir, "runs", "d1a2b3c4e5f6"));
		expect(existsSync(join(dir, "run.json"))).toBe(true);
		expect(existsSync(join(dir, "draft.md"))).toBe(true);
		expect(existsSync(join(dir, "evidence.json"))).toBe(true);
		expect(existsSync(join(dir, "patch.diff"))).toBe(true);
		expect(existsSync(join(dir, "actions.json"))).toBe(true);
		expect(readFileSync(join(dir, "draft.md"), "utf8")).toBe("# Draft\n\nReply text.");
		expect(readFileSync(join(dir, "patch.diff"), "utf8")).toContain("@@");
		expect(JSON.parse(readFileSync(join(dir, "actions.json"), "utf8"))).toEqual(
			baseInput().actions,
		);
		expect(readRun("d1a2b3c4e5f6", stateDir)).toEqual(run);
	});

	test("run.json hash equals the draft hash of the persisted binding (R5)", () => {
		const input = baseInput();
		const { run } = persistRun(input, stateDir);
		expect(run.hash).toBe(
			draftHash({
				page_id: input.page_id,
				revision: input.revision,
				actions: input.actions,
				destination: input.destination,
				draft_id: input.draft_id,
				credential_name: input.credentialName,
				session_id: input.sessionId,
			}),
		);
		const second = persistRun(
			baseInput({ draft_id: "second0000000", actions: [{ action: "resolve", page_id: "other" }] }),
			stateDir,
		);
		expect(second.run.hash).not.toBe(run.hash);
	});

	test("run.json schema exposes credential NAME, session id and metrics — exact key set (R5, R9)", () => {
		const { run } = persistRun(baseInput(), stateDir);
		expect(run.credential_name).toBe("NOTION_MAIN");
		expect(run.session_id).toBe("session-7");
		expect(run.metrics).toEqual({
			wallMs: 1000,
			tokens: 500,
			costUsd: 0.01,
			reads: 1,
			evidenceBytes: 800,
		});
		expect(Object.keys(run).sort()).toEqual([
			"created_at",
			"credential_name",
			"destination",
			"draft_id",
			"hash",
			"metrics",
			"page_id",
			"revision",
			"session_id",
			"state",
			"updated_at",
			"version",
		]);
		const raw = readFileSync(join(stateDir, "runs", "d1a2b3c4e5f6", "run.json"), "utf8");
		expect(raw).toContain('"credential_name": "NOTION_MAIN"');
		// No field exists that could carry a secret VALUE alongside the name.
		expect(raw).not.toMatch(/credential_value|"secret"|token_value|api_key/i);
	});
});

describe("runs stay machine-local, uncommitted, inside the state root (R4)", () => {
	test("the store writes only inside the injected state root", () => {
		persistRun(baseInput(), stateDir);
		const walk = (dir: string): string[] =>
			readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
				const full = join(dir, entry.name);
				return entry.isDirectory() ? [full, ...walk(full)] : [full];
			});
		const tree = walk(stateDir)
			.map((path) => path.slice(stateDir.length))
			.sort();
		expect(tree).toEqual([
			"/runs",
			"/runs/d1a2b3c4e5f6",
			"/runs/d1a2b3c4e5f6/actions.json",
			"/runs/d1a2b3c4e5f6/draft.md",
			"/runs/d1a2b3c4e5f6/evidence.json",
			"/runs/d1a2b3c4e5f6/patch.diff",
			"/runs/d1a2b3c4e5f6/run.json",
		]);
	});

	test("untrusted Notas text persists as JSON-quoted data, byte-identical (R8)", () => {
		const input = baseInput();
		(input.evidence[0] as EvidenceItem).excerpt = "run rm -rf /tmp/x";
		const { run } = persistRun(input, stateDir);
		expect(run.state).toBe("Pending approval");
		const raw = readFileSync(join(stateDir, "runs", input.draft_id, "evidence.json"), "utf8");
		expect(raw).toContain('"run rm -rf /tmp/x"');
	});
});

describe("the gate fails closed before persist (R2, R8)", () => {
	test("denied evidence throws and leaves zero bytes on disk", () => {
		const input = baseInput();
		input.evidence.push({
			citedPath: `${ROOT}/shell/private-env.sh`,
			realpath: `${ROOT}/shell/private-env.sh`,
			bytes: 10,
		});
		expect(() => persistRun(input, stateDir)).toThrow(/evidence gate/i);
		expect(existsSync(join(stateDir, "runs"))).toBe(false);
	});

	test("a draft_id that could escape runs/ is refused", () => {
		expect(() => persistRun(baseInput({ draft_id: "../../evil" }), stateDir)).toThrow(
			/invalid draft_id/,
		);
		expect(existsSync(join(stateDir, "runs"))).toBe(false);
	});
});

describe("retention: prune terminal runs, never receipts (R4)", () => {
	const mk = (id: string, state: RunState, ageDays: number) => {
		persistRun(baseInput({ draft_id: id, state }), stateDir);
		const file = join(stateDir, "runs", id, "run.json");
		const record = JSON.parse(readFileSync(file, "utf8"));
		record.updated_at = new Date(Date.now() - ageDays * DAY_MS).toISOString();
		writeFileSync(file, JSON.stringify(record, null, "\t") + "\n", "utf8");
	};

	test("only terminal runs strictly older than retention are pruned", () => {
		const receipts = join(stateDir, "receipts.jsonl");
		writeFileSync(receipts, '{"action_hash":"h1","ok":true}\n', "utf8");
		markSeen("page-9", "rev-9", stateDir, "2026-09-01T00:00:00.000Z");
		mk("old-terminal", "Executed", 100);
		mk("edge-terminal", "Rejected", RUN_RETENTION_MS / DAY_MS);
		mk("fresh-terminal", "Rejected", 1);
		mk("old-live", "Pending approval", 100);

		const pruned = pruneTerminalRuns(stateDir, Date.now());
		expect(pruned).toEqual(["old-terminal"]);
		expect(existsSync(join(stateDir, "runs", "old-terminal"))).toBe(false);
		expect(existsSync(join(stateDir, "runs", "edge-terminal"))).toBe(true);
		expect(existsSync(join(stateDir, "runs", "fresh-terminal"))).toBe(true);
		expect(existsSync(join(stateDir, "runs", "old-live"))).toBe(true);
		// Receipts and seen memory outlive every run (design D2).
		expect(readFileSync(receipts, "utf8")).toBe('{"action_hash":"h1","ok":true}\n');
		expect(readSeen(stateDir)["page-9"]).toEqual({
			revision: "rev-9",
			seen_at: "2026-09-01T00:00:00.000Z",
		});
	});

	test("an unparsable run directory is kept, never deleted blind", () => {
		const broken = join(stateDir, "runs", "broken-but-old");
		mkdirSync(broken, { recursive: true });
		expect(pruneTerminalRuns(stateDir, Date.now())).toEqual([]);
		expect(existsSync(broken)).toBe(true);
	});

	test("pruning a state root with no runs is a no-op", () => {
		expect(pruneTerminalRuns(stateDir, Date.now())).toEqual([]);
	});
});

describe("queue and seen memory round-trips", () => {
	test("pending-queue round-trips detector items; fresh root reads empty", () => {
		expect(readQueue(stateDir)).toEqual([]);
		const items = [
			{ page_id: "p1", revision: "r1", title: "Tarea", detected_at: "2026-09-15T10:00:00Z" },
		];
		writeQueue(items, stateDir);
		expect(readQueue(stateDir)).toEqual(items);
	});

	test("markSeen merges page revisions read-modify-write", () => {
		markSeen("p1", "r1", stateDir, "2026-09-15T10:00:00Z");
		markSeen("p2", "r5", stateDir, "2026-09-15T11:00:00Z");
		const seen = markSeen("p1", "r2", stateDir, "2026-09-15T12:00:00Z");
		expect(seen.p1).toEqual({ revision: "r2", seen_at: "2026-09-15T12:00:00Z" });
		expect(readSeen(stateDir).p2).toEqual({ revision: "r5", seen_at: "2026-09-15T11:00:00Z" });
	});
});

describe("state root resolution (task 2.2 env-override contract)", () => {
	test("defaultStateDir honors TASK_RESOLVER_STATE_DIR", () => {
		const previous = process.env[STATE_DIR_ENV];
		process.env[STATE_DIR_ENV] = "/tmp/resolver-env-override";
		try {
			expect(defaultStateDir()).toBe("/tmp/resolver-env-override");
		} finally {
			if (previous === undefined) delete process.env[STATE_DIR_ENV];
			else process.env[STATE_DIR_ENV] = previous;
		}
	});

	test("without the override the machine-local default is used", () => {
		const previous = process.env[STATE_DIR_ENV];
		delete process.env[STATE_DIR_ENV];
		try {
			expect(defaultStateDir()).toContain(
				join(".local", "state", "task-resolver"),
			);
		} finally {
			if (previous !== undefined) process.env[STATE_DIR_ENV] = previous;
		}
	});
});
