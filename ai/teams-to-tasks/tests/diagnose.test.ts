import { describe, expect, test } from "bun:test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { diagnose } from "../src/diagnose.ts";

function runFixture(events: unknown[], opts?: { stderr?: string; validation?: string }): string {
	const dir = mkdtempSync(join(tmpdir(), "teams-diag-"));
	mkdirSync(dir, { recursive: true });
	writeFileSync(
		join(dir, "events.jsonl"),
		events.map((e) => JSON.stringify(e)).join("\n") + "\n",
	);
	writeFileSync(join(dir, "agent.stderr"), opts?.stderr ?? "");
	if (opts?.validation !== undefined) writeFileSync(join(dir, "validation.log"), opts.validation);
	return dir;
}

const tool = (name: string, ts: number, status = "completed") => ({
	type: "tool_use",
	timestamp: ts,
	part: { type: "tool", tool: name, callID: `c-${name}-${ts}`, state: { status, output: "ok" } },
});
const stepFinish = (ts: number, total: number) => ({
	type: "step_finish",
	timestamp: ts,
	part: { reason: "tool-calls", tokens: { total } },
});

describe("failure diagnosis one-liner", () => {
	test("rc=124 maps to timeout with stage after browser close", () => {
		const dir = runFixture([
			tool("playwright_teams_browser_navigate", 1000),
			tool("teams_read", 2000),
			tool("playwright_teams_browser_close", 3000),
			stepFinish(3500, 232_000),
			{ type: "step_start", timestamp: 3600 },
		]);
		const line = diagnose(dir, "sweep", 124, 900);
		expect(line).toContain("timeout: SIGTERM at 900s wall");
		expect(line).toContain("stage=final contract composition");
		expect(line).toContain("ctx 232k");
		expect(line).toContain("session=ok");
	});

	test("rc=0 surfaces the validator rejection reason", () => {
		const dir = runFixture([tool("teams_read", 1000), stepFinish(2000, 10_000)], {
			validation: "malformed final assistant result\n",
		});
		const line = diagnose(dir, "digest", 0, 1500);
		expect(line).toContain("agent exited 0 but validator rejected");
		expect(line).toContain("reason=malformed final assistant result");
	});

	test("login markers flip the session signal", () => {
		const dir = runFixture([
			{
				type: "tool_use",
				timestamp: 1000,
				part: { type: "tool", tool: "playwright_teams_browser_navigate", callID: "c1", state: { status: "completed", output: "Iniciar sesión" } },
			},
		]);
		const line = diagnose(dir, "sweep", 1, 900);
		expect(line).toContain("session=SUSPECT");
		expect(line).toContain("agent crash rc=1");
	});

	test("empty event stream classifies as boot without throwing", () => {
		const dir = runFixture([]);
		const line = diagnose(dir, "sweep", 137, 900);
		expect(line).toContain("SIGKILL");
		expect(line).toContain("stage=boot (no tool calls)");
	});

	test("diag.txt is written with the phase timeline", () => {
		const dir = runFixture([
			tool("playwright_teams_browser_navigate", 1000),
			tool("teams_read", 2000),
		]);
		diagnose(dir, "sweep", 124, 900);
		const text = require("node:fs").readFileSync(join(dir, "diag.txt"), "utf8");
		expect(text).toContain("--- phase timeline (first call per tool) ---");
		expect(text).toContain("first teams_read");
	});
});
