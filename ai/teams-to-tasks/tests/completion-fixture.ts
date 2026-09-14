import type { Window } from "../src/completion.ts";

export const window: Window = { runId: "test-run", mode: "sweep", start: "2026-09-09T18:30:00.000Z", end: "2026-09-10T10:00:03Z" };

export function fixture(w = window) {
	const event = (type: string, part: any) => ({ type, timestamp: 123, sessionID: "session-test", part: { messageID: "msg-final", ...part } });
	const tool = (name: string, output = "OK") => event("tool_use", { type: "tool", tool: name, callID: name, state: { status: "completed", output, input: { url: "https://teams.microsoft.com", text: "que" } } });
	return [
		event("step_start", { type: "step-start" }),
		tool("playwright_teams_browser_navigate"),
		tool("playwright_teams_browser_type"),
		tool("playwright_teams_browser_click"),
		tool("playwright_teams_browser_snapshot"),
		tool("playwright_teams_browser_close"),
		event("text", { type: "text", time: { start: 1, end: 2 }, text: JSON.stringify({ version: 1, run_id: w.runId, mode: w.mode, window_start: w.start, window_end: w.end, status: "complete", coverage: "que-today", review_complete: true, browser_closed: true, actions: [] }) }),
		event("step_finish", { type: "step-finish", reason: "stop" }),
	];
}

export const encode = (events: any[]) => events.map((event) => JSON.stringify(event)).join("\n") + "\n";

if (import.meta.main) {
	const w = { runId: process.env.TEAMS_RUN_ID!, mode: process.env.TEAMS_MODE!, start: process.env.TEAMS_WINDOW_START!, end: process.env.TEAMS_WINDOW_END! };
	const events = fixture(w);
	if (w.mode === "digest") {
		const lookup = structuredClone(events[1]);
		lookup.part.tool = lookup.part.callID = "notion_API-get-block-children";
		events.splice(5, 0, lookup);
	}
	const scenario = process.argv[2] ?? "success";
	const text = events.at(-2)!.part;
	if (scenario === "missing") process.exit(0);
	if (scenario === "malformed") text.text = "not JSON";
	if (scenario === "expired") text.text = JSON.stringify({ ...JSON.parse(text.text), status: "session_expired" });
	if (scenario === "denied") {
		events[2].part.state.status = "error";
		events[2].part.state.error = "The user rejected permission";
	}
	if (scenario === "actions") {
		const lookup = structuredClone(events[1]);
		lookup.part.tool = lookup.part.callID = "notion_API-query-data-source";
		events.splice(5, 0, lookup);
		text.text = JSON.stringify({
			...JSON.parse(text.text),
			actions: [{ action: "create", title: "Task 1", notes: "Notes 1" }],
		});
	}
	if (scenario === "ansi") console.error("\x1b[93mordinary diagnostic\x1b[0m");
	console.log(encode(events));
}
