import { beforeEach, afterEach, describe, expect, test } from "bun:test";
import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	rmSync,
	writeFileSync,
	readFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createSignalExit, resolveChromiumExecutablePath, runPoll } from "../poller.ts";
import { EXIT } from "../src/core.ts";
import type { TeamsSearchAdapter } from "../src/core.ts";

/** Fake adapter: records closes; resolves, throws (e.g. locked profile), or hangs. */
function fakeAdapter(
	behavior: "resolve" | "throw" | "hang" = "resolve",
	stamps: string[] = ["2026-09-10T08:00:00.000Z"],
) {
	let closeCalls = 0;
	const adapter: TeamsSearchAdapter & { closeCount(): number } = {
		async search() {
			if (behavior === "throw") throw new Error("launch failed (profile locked?)");
			if (behavior === "hang") return new Promise<never>(() => {});
			return stamps.map((rawTimestamp) => ({ rawTimestamp }));
		},
		async close() {
			closeCalls++;
		},
		closeCount: () => closeCalls,
	};
	return adapter;
}

let stateDir: string;
let stateFile: string;

beforeEach(() => {
	stateDir = mkdtempSync(join(tmpdir(), "t2t-poller-"));
	stateFile = join(stateDir, "last_run");
});
afterEach(() => rmSync(stateDir, { recursive: true, force: true }));

const poll = (adapter: TeamsSearchAdapter, deadlineMs = 5_000) =>
	runPoll({ adapter, stateFile, deadlineMs });

describe("runPoll — exit codes and state advancement", () => {
	test("news exits 0 and writes nothing (advancement deferred to agent success)", async () => {
		writeFileSync(stateFile, "2026-09-09T18:30:00.000Z");
		expect(await poll(fakeAdapter())).toBe(EXIT.NEWS);
		expect(readFileSync(stateFile, "utf8")).toBe("2026-09-09T18:30:00.000Z");
	});

	test("bootstrap (no state) with messages is news; still writes nothing", async () => {
		expect(await poll(fakeAdapter())).toBe(EXIT.NEWS);
		expect(existsSync(stateFile)).toBe(false);
	});

	test("no-news exits 1 and advances state to poll start T", async () => {
		writeFileSync(stateFile, "2026-09-10T09:00:00.000Z");
		const before = Date.now();
		expect(await poll(fakeAdapter())).toBe(EXIT.NO_NEWS);
		const written = Date.parse(readFileSync(stateFile, "utf8"));
		expect(written).toBeGreaterThanOrEqual(before);
		expect(written).toBeLessThanOrEqual(Date.now());
	});

	test("empty results are no-news (and advance)", async () => {
		expect(await poll(fakeAdapter("resolve", []))).toBe(EXIT.NO_NEWS);
		expect(existsSync(stateFile)).toBe(true);
	});

	test("relative stamps ('10:42') flow through core normalization", async () => {
		writeFileSync(stateFile, "2020-01-01T00:00:00.000Z");
		expect(await poll(fakeAdapter("resolve", ["10:42"]))).toBe(EXIT.NEWS);
	});
});

describe("runPoll — failure paths: exit 2, close once, state untouched", () => {
	test("adapter throws (incl. locked profile): exit 2, close once, no state file", async () => {
		const adapter = fakeAdapter("throw");
		expect(await poll(adapter)).toBe(EXIT.FAILURE);
		expect(adapter.closeCount()).toBe(1);
		expect(existsSync(stateFile)).toBe(false);
	});

	test("hung search hits the deadline: exit 2, close once", async () => {
		const adapter = fakeAdapter("hang");
		expect(await poll(adapter, 25)).toBe(EXIT.FAILURE);
		expect(adapter.closeCount()).toBe(1);
	});

	test("invalid state fails closed before searching, byte-identical", async () => {
		writeFileSync(stateFile, "not a date");
		// Resolving adapter would be news (0); only the invalid-state path yields 2.
		expect(await poll(fakeAdapter())).toBe(EXIT.FAILURE);
		expect(readFileSync(stateFile, "utf8")).toBe("not a date");
	});

	test("unrecognized timestamp format (DOM change): exit 2, state untouched", async () => {
		writeFileSync(stateFile, "2026-09-10T09:00:00.000Z");
		const adapter = fakeAdapter("resolve", ["martes"]);
		expect(await poll(adapter)).toBe(EXIT.FAILURE);
		expect(readFileSync(stateFile, "utf8")).toBe("2026-09-10T09:00:00.000Z");
		expect(adapter.closeCount()).toBe(1);
	});
});

describe("signal handling", () => {
	test("handler closes once then exits 2; a second signal is a no-op", async () => {
		const exits: number[] = [];
		let closeCalls = 0;
		const handler = createSignalExit(
			async () => {
				closeCalls++;
			},
			(code) => exits.push(code),
		);
		handler();
		await new Promise((resolve) => setTimeout(resolve, 10));
		handler();
		expect(closeCalls).toBe(1);
		expect(exits).toEqual([EXIT.FAILURE]);
	});
});

describe("resolveChromiumExecutablePath", () => {
	// layout value = subdirectory holding the `chrome` binary; null = broken build.
	const makeCache = (layouts: Record<string, string | null>) => {
		const cache = mkdtempSync(join(tmpdir(), "t2t-cache-"));
		for (const [build, layout] of Object.entries(layouts)) {
			const exeDir = join(cache, build, layout ?? "no-binary");
			mkdirSync(exeDir, { recursive: true });
			if (layout !== null) writeFileSync(join(exeDir, "chrome"), "");
		}
		return cache;
	};

	test("newest usable build wins; broken newest falls back to older", () => {
		const cache = makeCache({ "chromium-1208": null, "chromium-1200": "chrome-linux64" });
		expect(resolveChromiumExecutablePath(cache)).toBe(
			join(cache, "chromium-1200", "chrome-linux64", "chrome"),
		);
		rmSync(cache, { recursive: true, force: true });
	});

	test("prefers the highest revision among usable builds", () => {
		const cache = makeCache({ "chromium-1208": "chrome-linux64", "chromium-1200": "chrome-linux" });
		expect(resolveChromiumExecutablePath(cache)).toBe(
			join(cache, "chromium-1208", "chrome-linux64", "chrome"),
		);
		rmSync(cache, { recursive: true, force: true });
	});

	test("ignores headless_shell builds; accepts the legacy chrome-linux layout", () => {
		const cache = makeCache({
			"chromium_headless_shell-1300": "chrome-headless-shell-linux64",
			"chromium-1208": "chrome-linux",
		});
		expect(resolveChromiumExecutablePath(cache)).toBe(
			join(cache, "chromium-1208", "chrome-linux", "chrome"),
		);
		rmSync(cache, { recursive: true, force: true });
	});

	test("missing cache dir or no usable build resolves null (poller exits 2)", () => {
		expect(resolveChromiumExecutablePath(join(stateDir, "nope"))).toBeNull();
		const empty = mkdtempSync(join(tmpdir(), "t2t-empty-"));
		expect(resolveChromiumExecutablePath(empty)).toBeNull();
		rmSync(empty, { recursive: true, force: true });
	});
});
