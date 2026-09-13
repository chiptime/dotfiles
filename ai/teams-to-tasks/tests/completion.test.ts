import { describe, expect, test } from "bun:test";
import { validateCompletion } from "../src/completion.ts";
import { encode, fixture, window } from "./completion-fixture.ts";

describe("final assistant completion contract", () => {
	test("accepts terminal result with successful browser evidence", () => {
		expect(validateCompletion(encode(fixture()), "", window)).toContain("0 created, 0 updated");
	});
	test("ANSI diagnostics are never used as notification text", () => {
		expect(validateCompletion(encode(fixture()), "\x1b[31mnoise\x1b[0m", window)).not.toContain("noise");
	});
	test.each(["", "not json", "\x1b[0m{}"])("rejects missing/malformed events: %j", (raw) => {
		expect(() => validateCompletion(raw, "", window)).toThrow();
	});
	test("rejects zero-exit permission denial even with a valid final result", () => {
		expect(() => validateCompletion(encode(fixture()), "permission requested: external_directory; auto-rejecting", window)).toThrow("permission denied");
	});
	test.each(["failed", "session_expired"])("rejects %s result", (status) => {
		const events = fixture();
		const part = events.at(-2)!.part;
		part.text = JSON.stringify({ ...JSON.parse(part.text), status });
		expect(() => validateCompletion(encode(events), "", window)).toThrow();
	});
	test.each(["tool error", "missing close", "wrong window", "wrong session", "truncated", "tool echo", "wrong message", "length stop", "malformed result", "MCP error"])("rejects %s", (scenario) => {
		const events = fixture();
		if (scenario === "tool error") events[2].part.state.status = "error";
		if (scenario === "missing close") events.splice(5, 1);
		if (scenario === "wrong window") events.at(-2)!.part.text = events.at(-2)!.part.text.replace(window.runId, "other");
		if (scenario === "wrong session") events[1].sessionID = "other";
		if (scenario === "truncated") events.pop();
		if (scenario === "tool echo") { events[2].part.state.output = events.at(-2)!.part.text; events.splice(-2, 1); }
		if (scenario === "wrong message") events.at(-1)!.part.messageID = "other";
		if (scenario === "length stop") events.at(-1)!.part.reason = "length";
		if (scenario === "malformed result") events.at(-2)!.part.text = "```json\n{}\n```";
		if (scenario === "MCP error") events[2].part.state.output = "### Error\nSnapshot reference missing";
		expect(() => validateCompletion(encode(events), "", window)).toThrow();
	});
	test("only confirmed Notion response IDs prove writes; narration does not", () => {
		const events = fixture();
		const lookup = structuredClone(events[1]);
		lookup.part.tool = lookup.part.callID = "notion_API-query-data-source";
		const write = structuredClone(lookup);
		write.part.tool = write.part.callID = "notion_API-post-page";
		write.part.state.output = JSON.stringify({ object: "page", id: "page-created" });
		events.splice(5, 0, lookup, write);
		const text = events.at(-2)!.part;
		text.text = JSON.stringify({ ...JSON.parse(text.text), actions: [{ tool: write.part.tool, result_id: "page-created" }] });
		expect(validateCompletion(encode(events), "", window)).toContain("1 created");
		write.part.state.output = "I created page-created";
		expect(() => validateCompletion(encode(events), "", window)).toThrow("unverified Notion write");
	});

	// Deliberate, revertable allowance: stale snapshot refs on teams browser tools
	// are tolerated only with later recovery evidence on the SAME tool.
	const STALE = "Ref e12 not found in the current page snapshot";
	const toolEvent = (name: string, callID: string, error?: string) => ({
		type: "tool_use", timestamp: 123, sessionID: "session-test",
		part: { messageID: "msg-final", type: "tool", tool: name, callID,
			state: error === undefined ? { status: "completed", output: "OK", input: {} } : { status: "error", error, input: {} } },
	});
	test("tolerates a stale snapshot ref recovered by a later completed call to the same tool", () => {
		const events = fixture();
		events.splice(3, 0, toolEvent("playwright_teams_browser_click", "click-stale", STALE));
		expect(validateCompletion(encode(events), "", window)).toContain("0 created, 0 updated");
	});
	test("rejects an unrecovered stale snapshot ref", () => {
		const events = fixture();
		events.splice(4, 0, toolEvent("playwright_teams_browser_click", "click-stale", STALE));
		expect(() => validateCompletion(encode(events), "", window)).toThrow("tool failure");
	});
	test("recovery evidence must come from the same tool", () => {
		const events = fixture();
		events.splice(4, 1); // drop the successful snapshot
		events.splice(4, 0, toolEvent("playwright_teams_browser_snapshot", "snap-stale", STALE));
		expect(() => validateCompletion(encode(events), "", window)).toThrow("tool failure");
	});
	test("rejects a permission-denial browser error even when the same tool later succeeds", () => {
		const events = fixture();
		events.splice(3, 0, toolEvent("playwright_teams_browser_click", "click-denied", "The user rejected permission"));
		expect(() => validateCompletion(encode(events), "", window)).toThrow("tool failure");
	});
	test("rejects a stale-ref-shaped error on a non-teams browser tool even when it later succeeds", () => {
		const events = fixture();
		events.splice(5, 0,
			toolEvent("playwright_browser_click", "iso-click-stale", STALE),
			toolEvent("playwright_browser_click", "iso-click-ok"));
		expect(() => validateCompletion(encode(events), "", window)).toThrow("tool failure");
	});
});
