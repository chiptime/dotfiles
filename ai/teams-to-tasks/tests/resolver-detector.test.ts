import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	realpathSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { NOTION_API_BASE } from "../src/notion-writer.ts";
import { readQueue, readSeen } from "../src/resolver/run-store.ts";
import type { RootsEnv } from "../src/resolver/roots.ts";
import {
	DETECTOR_EXIT,
	FORBIDDEN_SOURCE_IDS,
	INBOX_STATUS,
	TAREAS_DATA_SOURCE_ID,
	ForbiddenSourceError,
	acquireLock,
	buildInboxQuery,
	exitForOutcome,
	parseInboxPages,
	runDetection,
} from "../src/resolver/detector.ts";
import { runReadCli } from "../src/resolver/read-cli.ts";
import {
	APPROVAL_STATE_OPTIONS,
	RESOLVER_PROPERTIES,
	TRIAGE_STATUS_OPTIONS,
	planMigration,
	runSchemaMigrate,
} from "../src/resolver/schema-migrate.ts";

// All Notion interactions go through injected fetch fakes: zero live API
// calls, zero credentials (task 3.1 contract). Store tests get a fresh temp
// state dir per test — the real ~/.local/state is never touched.

function jsonResponse(data: unknown, status = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

type RecordedCall = { url: string; init: RequestInit };

function recorder(
	handler: (url: string, init: RequestInit) => Promise<Response> | Response,
) {
	const calls: RecordedCall[] = [];
	const fetch = (input: string | URL | Request, init?: RequestInit) => {
		const url = typeof input === "string" ? input : input.toString();
		const record = { url, init: init ?? {} };
		calls.push(record);
		return Promise.resolve(handler(url, record.init));
	};
	return { fetch, calls };
}

function mkPage(
	id: string,
	title: string,
	lastEdited: string,
	dataSourceId: string = TAREAS_DATA_SOURCE_ID,
) {
	return {
		id,
		last_edited_time: lastEdited,
		parent: { type: "data_source_id", data_source_id: dataSourceId },
		properties: {
			Título: {
				type: "title",
				title: [{ type: "text", plain_text: title }],
			},
		},
	};
}

const P1 = mkPage("page-1", "Revisar login SIS", "2026-09-15T09:00:00.000Z");
const P2 = mkPage("page-2", "Actualizar README", "2026-09-15T10:30:00.000Z");

let stateDir: string;
beforeEach(() => {
	stateDir = mkdtempSync(join(tmpdir(), "resolver-detector-"));
});
afterEach(() => {
	rmSync(stateDir, { recursive: true, force: true });
});

function depsWith(
	fetch: (input: string | URL | Request, init?: RequestInit) => Promise<Response>,
	dataSourceId: string = TAREAS_DATA_SOURCE_ID,
) {
	return { fetchFn: fetch, token: "test-token", dataSourceId, stateDir };
}

describe("detector: inbox query targets only Tareas Estado 📥 Inbox (R1)", () => {
	test("queries the configured data source with the Inbox select filter", async () => {
		const { fetch, calls } = recorder(() => jsonResponse({ results: [P1] }));
		const outcome = await runDetection(depsWith(fetch));
		expect(outcome).toBe("news");
		expect(calls.length).toBe(1);
		expect(calls[0]!.url).toBe(
			`${NOTION_API_BASE}/data_sources/${TAREAS_DATA_SOURCE_ID}/query`,
		);
		expect(calls[0]!.init.method).toBe("POST");
		const headers = calls[0]!.init.headers as Record<string, string>;
		expect(headers["Authorization"]).toBe("Bearer test-token");
		expect(JSON.parse(calls[0]!.init.body as string)).toEqual(buildInboxQuery());
		expect(buildInboxQuery()).toEqual({
			filter: { property: "Estado", select: { equals: INBOX_STATUS } },
			page_size: 100,
		});
	});

	test("an env-configured non-default data source id is honored", async () => {
		const { fetch, calls } = recorder(() => jsonResponse({ results: [] }));
		const outcome = await runDetection(depsWith(fetch, "11111111-2222-3333-4444-555555555555"));
		expect(outcome).toBe("no-news");
		expect(calls[0]!.url).toBe(
			`${NOTION_API_BASE}/data_sources/11111111-2222-3333-4444-555555555555/query`,
		);
	});

	test("first detection queues items and marks them seen", async () => {
		const { fetch } = recorder(() => jsonResponse({ results: [P1, P2] }));
		await runDetection(depsWith(fetch));
		const queue = readQueue(stateDir);
		expect(queue.map((item) => item.page_id)).toEqual(["page-1", "page-2"]);
		expect(queue[0]).toEqual({
			page_id: "page-1",
			revision: "2026-09-15T09:00:00.000Z",
			title: "Revisar login SIS",
			detected_at: queue[0]!.detected_at,
		});
		expect(queue.every((item) => Number.isNaN(Date.parse(item.detected_at)) === false)).toBe(
			true,
		);
		const seen = readSeen(stateDir);
		expect(seen["page-1"]!.revision).toBe("2026-09-15T09:00:00.000Z");
		expect(seen["page-2"]!.revision).toBe("2026-09-15T10:30:00.000Z");
	});

	test("a page without a parsable title is queued as untitled", async () => {
		const bare = {
			...P1,
			properties: {},
		};
		const { fetch } = recorder(() => jsonResponse({ results: [bare] }));
		await runDetection(depsWith(fetch));
		expect(readQueue(stateDir)[0]!.title).toBe("untitled");
	});
});

describe("detector: unchanged rerun detects exactly once (R1)", () => {
	test("second unchanged run adds nothing: queue unchanged, seen byte-identical", async () => {
		const { fetch } = recorder(() => jsonResponse({ results: [P1, P2] }));
		expect(await runDetection(depsWith(fetch))).toBe("news");
		const seenAfterFirst = readFileSync(join(stateDir, "seen.json"), "utf8");
		const queueAfterFirst = readFileSync(join(stateDir, "pending-queue.json"), "utf8");

		expect(await runDetection(depsWith(fetch))).toBe("no-news");
		expect(readFileSync(join(stateDir, "seen.json"), "utf8")).toBe(seenAfterFirst);
		expect(readFileSync(join(stateDir, "pending-queue.json"), "utf8")).toBe(queueAfterFirst);
		expect(readQueue(stateDir).length).toBe(2);
	});

	test("a revision change re-detects the page under the new revision", async () => {
		let pages = [P1];
		const { fetch } = recorder(() => jsonResponse({ results: pages }));
		expect(await runDetection(depsWith(fetch))).toBe("news");

		const edited = mkPage("page-1", "Revisar login SIS", "2026-09-15T11:00:00.000Z");
		pages = [edited];
		expect(await runDetection(depsWith(fetch))).toBe("news");

		const queue = readQueue(stateDir);
		expect(queue.map((item) => item.revision)).toEqual([
			"2026-09-15T09:00:00.000Z",
			"2026-09-15T11:00:00.000Z",
		]);
		expect(readSeen(stateDir)["page-1"]!.revision).toBe("2026-09-15T11:00:00.000Z");
	});
});

describe("detector: forbidden SIS sources refused fail-closed (R1)", () => {
	test("a forbidden source configured as the target is refused before any fetch", async () => {
		const { fetch, calls } = recorder(() => jsonResponse({ results: [] }));
		await expect(
			runDetection(depsWith(fetch, FORBIDDEN_SOURCE_IDS[0]!)),
		).rejects.toBeInstanceOf(ForbiddenSourceError);
		expect(calls.length).toBe(0);
	});

	test("a response referencing a forbidden source is refused, nothing persisted", async () => {
		const poisoned = mkPage(
			"page-evil",
			"x",
			"2026-09-15T09:00:00.000Z",
			FORBIDDEN_SOURCE_IDS[1]!,
		);
		const { fetch, calls } = recorder(() => jsonResponse({ results: [poisoned] }));
		await expect(runDetection(depsWith(fetch))).rejects.toBeInstanceOf(ForbiddenSourceError);
		expect(calls.length).toBe(1); // the query itself — never a second call
		expect(existsSync(join(stateDir, "seen.json"))).toBe(false);
		expect(existsSync(join(stateDir, "pending-queue.json"))).toBe(false);
	});

	test("parseInboxPages refuses pages parented outside the queried source", () => {
		expect(() =>
			parseInboxPages([mkPage("p", "t", "r", "other-source")], TAREAS_DATA_SOURCE_ID, "{}"),
		).toThrow(ForbiddenSourceError);
		expect(parseInboxPages([P1], TAREAS_DATA_SOURCE_ID, "{}").length).toBe(1);
	});
});

describe("detector: single-instance lock (threat matrix: cron process integration)", () => {
	test("a lock held by a live process refuses the run before any fetch or write", async () => {
		writeFileSync(join(stateDir, "lock"), `${process.pid} 2026-09-15T00:00:00Z\n`);
		const { fetch, calls } = recorder(() => jsonResponse({ results: [P1] }));
		expect(await runDetection(depsWith(fetch))).toBe("locked");
		expect(calls.length).toBe(0);
		expect(existsSync(join(stateDir, "seen.json"))).toBe(false);
		expect(existsSync(join(stateDir, "pending-queue.json"))).toBe(false);
	});

	test("acquireLock creates, releases, and reacquires; unparsable locks stay held", () => {
		const release = acquireLock(stateDir);
		expect(release).not.toBeNull();
		expect(acquireLock(stateDir)).toBeNull(); // our own live pid holds it
		release!();
		expect(acquireLock(stateDir)).not.toBeNull();

		writeFileSync(join(stateDir, "lock"), "not-a-pid\n");
		expect(acquireLock(stateDir)).toBeNull(); // fail closed on garbage
	});

	test("a stale lock from a dead process is reclaimed and detection proceeds", async () => {
		const victim = Bun.spawn(["sleep", "5"]);
		victim.kill();
		await victim.exited;
		writeFileSync(join(stateDir, "lock"), `${victim.pid} 2026-09-15T00:00:00Z\n`);

		const { fetch } = recorder(() => jsonResponse({ results: [P1] }));
		expect(await runDetection(depsWith(fetch))).toBe("news");
		expect(readQueue(stateDir).length).toBe(1);
	});
});

describe("detector: PAUSE parity with teams-to-tasks cron", () => {
	test("a PAUSE file skips the run: no fetch, no writes, exit stays clean", async () => {
		writeFileSync(join(stateDir, "PAUSE"), "");
		const { fetch, calls } = recorder(() => jsonResponse({ results: [P1] }));
		expect(await runDetection(depsWith(fetch))).toBe("paused");
		expect(calls.length).toBe(0);
		expect(existsSync(join(stateDir, "seen.json"))).toBe(false);
		expect(existsSync(join(stateDir, "pending-queue.json"))).toBe(false);
		expect(existsSync(join(stateDir, "lock"))).toBe(false);
	});
});

describe("detector: Notion hard failure ⇒ rc 2, seen.json untouched (threat matrix)", () => {
	test("an HTTP 500 fails closed before any state write", async () => {
		const { fetch } = recorder(() => jsonResponse({ message: "internal" }, 500));
		await expect(runDetection(depsWith(fetch))).rejects.toThrow(/Notion query failed/i);
		expect(existsSync(join(stateDir, "seen.json"))).toBe(false);
		expect(existsSync(join(stateDir, "pending-queue.json"))).toBe(false);
	});

	test("a network-level rejection fails closed before any state write", async () => {
		const { fetch } = recorder(() => Promise.reject(new Error("ECONNREFUSED")));
		await expect(runDetection(depsWith(fetch))).rejects.toThrow("ECONNREFUSED");
		expect(existsSync(join(stateDir, "seen.json"))).toBe(false);
	});

	test("exit codes: news 0, no-news 1, paused/locked skip 0, failure 2", () => {
		expect(exitForOutcome("news")).toBe(DETECTOR_EXIT.NEWS);
		expect(exitForOutcome("no-news")).toBe(DETECTOR_EXIT.NO_NEWS);
		expect(exitForOutcome("paused")).toBe(0);
		expect(exitForOutcome("locked")).toBe(0);
		expect(exitForOutcome("failure")).toBe(DETECTOR_EXIT.FAILURE);
	});
});

// ---------------------------------------------------------------------------
// read-cli (task 3.3, R2): attended gated read whose deny output mirrors the
// evidence-gate verdicts verbatim.
// ---------------------------------------------------------------------------

function projectFixture() {
	const codeRoot = mkdtempSync(join(tmpdir(), "resolver-read-cli-"));
	const projectDir = join(codeRoot, "myproj");
	mkdirSync(join(projectDir, "src"), { recursive: true });
	writeFileSync(join(projectDir, "src", "api.ts"), "export const api = 1;\n", "utf8");
	writeFileSync(join(projectDir, "private-env.sh"), "export SECRET=x\n", "utf8");
	const env: RootsEnv = {
		home: codeRoot,
		codeRoot,
		dotfilesRoot: join(codeRoot, "dotfiles"),
		realpath: (path) => {
			try {
				return realpathSync(path);
			} catch {
				return null;
			}
		},
	};
	const yaml = "myproj:\n  name: My Project\n  group: myproj\n";
	return { env, yaml, projectDir, dispose: () => rmSync(codeRoot, { recursive: true, force: true }) };
}

function ioFor(env: RootsEnv, yaml: string) {
	const out: string[] = [];
	const err: string[] = [];
	return {
		io: {
			projectsYaml: yaml,
			env,
			stdout: (s: string) => out.push(s),
			stderr: (s: string) => err.push(s),
		},
		out,
		err,
	};
}

describe("read-cli: gated attended read (R2)", () => {
	test("an allowed path returns the cited realpath with numbered lines", async () => {
		const f = projectFixture();
		try {
			const { io, out } = ioFor(f.env, f.yaml);
			const rc = await runReadCli(
				{ proyecto: "myproj", filePath: join(f.projectDir, "src", "api.ts") },
				io,
			);
			expect(rc).toBe(0);
			const text = out.join("\n");
			expect(text).toContain(realpathSync(join(f.projectDir, "src", "api.ts")));
			expect(text).toMatch(/1: export const api = 1;/);
		} finally {
			f.dispose();
		}
	});

	test("a credential-adjacent name is denied with the gate's own verdict text", async () => {
		const f = projectFixture();
		try {
			const { io, out, err } = ioFor(f.env, f.yaml);
			const rc = await runReadCli(
				{ proyecto: "myproj", filePath: join(f.projectDir, "private-env.sh") },
				io,
			);
			expect(rc).toBe(1);
			expect(out.length).toBe(0); // contents never printed
			expect(err.join("\n")).toContain(
				"denied credential-adjacent name: private-env.sh",
			);
		} finally {
			f.dispose();
		}
	});

	test("an unknown Proyecto yields zero roots and denies fail-closed", async () => {
		const f = projectFixture();
		try {
			const { io, err } = ioFor(f.env, f.yaml);
			const rc = await runReadCli(
				{ proyecto: "unknown-proyecto", filePath: join(f.projectDir, "src", "api.ts") },
				io,
			);
			expect(rc).toBe(1);
			expect(err.join("\n")).toContain("no approved roots");
		} finally {
			f.dispose();
		}
	});

	test("a path outside the approved roots is denied", async () => {
		const f = projectFixture();
		try {
			const outside = mkdtempSync(join(tmpdir(), "resolver-outside-"));
			writeFileSync(join(outside, "loose.txt"), "x\n", "utf8");
			const { io, err } = ioFor(f.env, f.yaml);
			const rc = await runReadCli(
				{ proyecto: "myproj", filePath: join(outside, "loose.txt") },
				io,
			);
			expect(rc).toBe(1);
			expect(err.join("\n")).toContain("outside approved roots");
			rmSync(outside, { recursive: true, force: true });
		} finally {
			f.dispose();
		}
	});
});

// ---------------------------------------------------------------------------
// schema-migrate (task 3.4, R5): attended additive migration of the 5
// resolver properties onto the Tareas data source.
// ---------------------------------------------------------------------------

describe("schema-migrate: plan (R5)", () => {
	test("offline plan (existing unknown) adds all 5 properties", () => {
		const plan = planMigration(null);
		expect(Object.keys(plan.additions).sort()).toEqual(
			Object.keys(RESOLVER_PROPERTIES).sort(),
		);
		expect(plan.skipped).toEqual([]);
		expect(plan.conflicts).toEqual([]);
	});

	test("existing rich_text properties are skipped; select options merge additively", () => {
		const existing = {
			"Resolution Draft": { type: "rich_text", rich_text: {} },
			"Triage Status": {
				type: "select",
				select: { options: [{ name: TRIAGE_STATUS_OPTIONS[0]! }, { name: TRIAGE_STATUS_OPTIONS[1]! }] },
			},
		};
		const plan = planMigration(existing);
		expect(plan.skipped).toEqual(["Resolution Draft"]);
		expect(plan.additions["Triage Status"]).toEqual({
			select: {
				options: TRIAGE_STATUS_OPTIONS.slice(2).map((name) => ({ name })),
			},
		});
		expect(plan.additions["Approval State"]).toBeDefined();
		expect(plan.additions["Local Context Ref"]).toBeDefined();
		expect(plan.additions["Draft ID"]).toBeDefined();
	});

	test("a property present with a conflicting type is reported, never overwritten", () => {
		const plan = planMigration({
			"Triage Status": { type: "number" },
		});
		expect(plan.conflicts.length).toBe(1);
		expect(plan.conflicts[0]).toContain("Triage Status");
		expect(plan.additions["Triage Status"]).toBeUndefined();
	});

	test("a fully-migrated data source plans nothing", () => {
		const complete = Object.fromEntries(
			Object.entries(RESOLVER_PROPERTIES).map(([name, schema]) => [
				name,
				name === "Triage Status" || name === "Approval State"
					? {
							type: "select",
							select: {
								options: (schema as { select: { options: { name: string }[] } }).select.options,
							},
						}
					: { type: "rich_text", rich_text: {} },
			]),
		);
		const plan = planMigration(complete);
		expect(plan.additions).toEqual({});
		expect(plan.conflicts).toEqual([]);
	});
});

describe("schema-migrate: run (R5)", () => {
	test("offline dry-run without a token makes zero network calls and prints the plan", async () => {
		const { fetch, calls } = recorder(() => jsonResponse({}));
		const lines: string[] = [];
		const rc = await runSchemaMigrate(
			{ fetchFn: fetch, token: undefined, dataSourceId: TAREAS_DATA_SOURCE_ID, dryRun: true },
			{ print: (s) => lines.push(s) },
		);
		expect(rc).toBe(0);
		expect(calls.length).toBe(0);
		const text = lines.join("\n");
		for (const name of Object.keys(RESOLVER_PROPERTIES)) expect(text).toContain(name);
		expect(text).toContain("dry-run");
	});

	test("dry-run with a token retrieves the schema and never PATCHes", async () => {
		const { fetch, calls } = recorder((url) => {
			if (url.endsWith(`/data_sources/${TAREAS_DATA_SOURCE_ID}`)) {
				return jsonResponse({
					object: "data_source",
					properties: { "Resolution Draft": { type: "rich_text", rich_text: {} } },
				});
			}
			throw new Error(`unexpected call: ${url}`);
		});
		const rc = await runSchemaMigrate(
			{ fetchFn: fetch, token: "test-token", dataSourceId: TAREAS_DATA_SOURCE_ID, dryRun: true },
			{ print: () => {} },
		);
		expect(rc).toBe(0);
		expect(calls.length).toBe(1);
		expect(calls[0]!.init.method).toBe("GET");
	});

	test("apply PATCHes only the missing properties and options", async () => {
		const { fetch, calls } = recorder((url, init) => {
			if (url.endsWith(`/data_sources/${TAREAS_DATA_SOURCE_ID}`) && init.method === "GET") {
				return jsonResponse({
					object: "data_source",
					properties: {
						"Triage Status": {
							type: "select",
							select: { options: [{ name: TRIAGE_STATUS_OPTIONS[0]! }] },
						},
						"Draft ID": { type: "rich_text", rich_text: {} },
					},
				});
			}
			return jsonResponse({ object: "data_source" });
		});
		const rc = await runSchemaMigrate(
			{ fetchFn: fetch, token: "test-token", dataSourceId: TAREAS_DATA_SOURCE_ID, dryRun: false },
			{ print: () => {} },
		);
		expect(rc).toBe(0);
		const patch = calls.find(
			(call) => call.init.method === "PATCH",
		);
		expect(patch!.url).toBe(`${NOTION_API_BASE}/data_sources/${TAREAS_DATA_SOURCE_ID}`);
		const body = JSON.parse(patch!.init.body as string);
		expect(Object.keys(body.properties).sort()).toEqual([
			"Approval State",
			"Local Context Ref",
			"Resolution Draft",
			"Triage Status",
		]);
		expect(body.properties["Triage Status"]).toEqual({
			select: { options: TRIAGE_STATUS_OPTIONS.slice(1).map((name) => ({ name })) },
		});
	});

	test("nothing to migrate sends nothing", async () => {
		const complete = Object.fromEntries(
			Object.entries(RESOLVER_PROPERTIES).map(([name, schema]) => [
				name,
				name === "Triage Status" || name === "Approval State"
					? {
							type: "select",
							select: {
								options: (schema as { select: { options: { name: string }[] } }).select.options,
							},
						}
					: { type: "rich_text", rich_text: {} },
			]),
		);
		const { fetch, calls } = recorder(() =>
			jsonResponse({ object: "data_source", properties: complete }),
		);
		const rc = await runSchemaMigrate(
			{ fetchFn: fetch, token: "test-token", dataSourceId: TAREAS_DATA_SOURCE_ID, dryRun: false },
			{ print: () => {} },
		);
		expect(rc).toBe(0);
		expect(calls.filter((call) => call.init.method === "PATCH").length).toBe(0);
	});

	test("a type conflict fails closed without sending a PATCH", async () => {
		const { fetch, calls } = recorder((url, init) => {
			if (init.method === "GET") {
				return jsonResponse({
					object: "data_source",
					properties: { "Approval State": { type: "number", number: {} } },
				});
			}
			throw new Error("PATCH must not happen on conflict");
		});
		const rc = await runSchemaMigrate(
			{ fetchFn: fetch, token: "test-token", dataSourceId: TAREAS_DATA_SOURCE_ID, dryRun: false },
			{ print: () => {} },
		);
		expect(rc).toBe(1);
		expect(calls.filter((call) => call.init.method === "PATCH").length).toBe(0);
	});

	test("a forbidden SIS data source is refused before any fetch", async () => {
		const { fetch, calls } = recorder(() => jsonResponse({}));
		await expect(
			runSchemaMigrate(
				{ fetchFn: fetch, token: "test-token", dataSourceId: FORBIDDEN_SOURCE_IDS[0]!, dryRun: false },
				{ print: () => {} },
			),
		).rejects.toBeInstanceOf(ForbiddenSourceError);
		expect(calls.length).toBe(0);
	});
});
