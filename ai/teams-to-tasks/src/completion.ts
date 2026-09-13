/** Validate CLI JSON events, never a marker found in a prompt or tool echo. */
export type Window = { runId: string; mode: string; start: string; end: string };
type Event = { type: string; sessionID: string; part?: any };

export function validateCompletion(raw: string, stderr: string, window: Window): string {
	const fail = (reason: string): never => { throw new Error(reason); };
	// Permission requests are printed to stderr by the non-interactive CLI.
	if (/auto-rejecting|permission requested|rejected permission/i.test(stderr)) fail("permission denied");
	let events: Event[];
	try {
		events = raw.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line));
	} catch { return fail("invalid event stream"); }
	if (!events.length || events.some((event) => !event || typeof event.sessionID !== "string" ||
		event.sessionID !== events[0].sessionID || !["step_start", "step_finish", "text", "reasoning", "tool_use"].includes(event.type))) {
		fail("missing, mixed, or failed event stream");
	}
	const tools = events.filter((event) => event.type === "tool_use").map((event) => event.part);
	if (tools.some((part, index) => {
		if (part?.type !== "tool") return true;
		if (part.state?.status === "completed")
			return part.state?.metadata?.isError === true || toolOutputFailed(part.state?.output);
		// Only tolerance in this validator: a stale snapshot ref on a teams browser
		// tool, proven recovered by a LATER completed call to the SAME tool.
		return !recoveredStaleRef(part, index, tools);
	})) fail("tool failure");
	const finish = events.at(-1);
	const text = events.at(-2);
	if (finish?.type !== "step_finish" || finish.part?.reason !== "stop" ||
		text?.type !== "text" || text.part?.type !== "text" || !text.part?.messageID ||
		text.part.messageID !== finish.part?.messageID || !text.part.time?.end) fail("missing final assistant result");
	let result: any;
	try { result = JSON.parse(text!.part.text); } catch { return fail("malformed final assistant result"); }
	if (!result || result.version !== 1 || result.run_id !== window.runId || result.mode !== window.mode ||
		result.window_start !== window.start || result.window_end !== window.end) fail("completion window mismatch");
	if (result.status === "session_expired") fail("session expired: manual login required");
	if (result.status !== "complete" || result.coverage !== "que-today" || result.review_complete !== true ||
		result.browser_closed !== true || !Array.isArray(result.actions)) fail("incomplete sweep");
	const ids = new Set(tools.map((part) => part.callID));
	if (ids.size !== tools.length || tools.some((part) => typeof part.callID !== "string")) fail("invalid tool evidence");
	const used = (name: string) => tools.some((part) => part.tool === name);
	if (!used("playwright_teams_browser_navigate") || !used("playwright_teams_browser_snapshot") ||
		!used("playwright_teams_browser_type") || !used("playwright_teams_browser_click") ||
		tools.at(-1)?.tool !== "playwright_teams_browser_close") fail("missing browser completion evidence");
	if (!tools.some((part) => part.tool === "playwright_teams_browser_navigate" && /^https:\/\/teams\.microsoft\.com(?:\/|$)/.test(part.state.input?.url ?? "")) ||
		!tools.some((part) => part.tool === "playwright_teams_browser_type" && part.state.input?.text === "que")) fail("missing discovery evidence");
	const writes = tools.filter((part) => ["notion_API-post-page", "notion_API-patch-page", "notion_API-patch-block-children"].includes(part.tool));
	if (writes.length !== result.actions.length) fail("unaccounted Notion writes");
	if (writes.length && window.mode === "sweep" && !used("notion_API-query-data-source")) fail("missing dedupe lookup evidence");
	if (window.mode === "digest" && !used("notion_API-get-block-children")) fail("missing digest heading lookup evidence");
	const claimed = new Set<string>();
	for (const action of result.actions) {
		const write = writes.find((part) => part.tool === action?.tool && !claimed.has(part.callID) && responseIds(part.state.output).includes(action.result_id));
		if (!write) fail("unverified Notion write");
		claimed.add(write.callID);
	}
	const created = writes.filter((part) => part.tool === "notion_API-post-page").length;
	return `Validated ${window.mode}: ${created} created, ${writes.length - created} updated (que+Today scope)`;
}

function toolOutputFailed(output: unknown): boolean {
	if (typeof output !== "string") return true;
	if (/^\s*(### Error\b|Error:)/m.test(output)) return true;
	try {
		const value = JSON.parse(output);
		return value?.isError === true || value?.object === "error";
	} catch { return false; }
}

/** Deliberate, revertable policy: teams browser tools can legitimately observe a
 * stale snapshot ref mid-sweep (page changed under them) and recover via a fresh
 * snapshot + retry. The error is tolerable ONLY IF the tool is a
 * playwright_teams_browser_* tool, the message is exactly that failure mode, and
 * a LATER successful (completed, non-failing output) call to the SAME tool exists.
 * Permission denials, teams_read denials, Notion errors and unknown tools still
 * fail closed. Revert = delete this function and the third branch above. */
function recoveredStaleRef(part: any, index: number, tools: any[]): boolean {
	return (
		part?.state?.status === "error" &&
		typeof part?.tool === "string" &&
		part.tool.startsWith("playwright_teams_browser_") &&
		typeof part.state.error === "string" &&
		/Ref \S+ not found in the current page snapshot/.test(part.state.error) &&
		tools.some(
			(later, i) =>
				i > index &&
				later?.tool === part.tool &&
				later?.state?.status === "completed" &&
				later?.state?.metadata?.isError !== true &&
				!toolOutputFailed(later?.state?.output),
		)
	);
}

/** Only IDs from parsed successful Notion response objects count as write evidence. */
function responseIds(output: unknown): string[] {
	let value: any = output;
	if (typeof value === "string") {
		try { value = JSON.parse(value); } catch { return []; }
	}
	if (!value || value.isError === true || value.object === "error") return [];
	if ((value.object === "page" || value.object === "block") && typeof value.id === "string") return [value.id];
	if (value.object === "list" && Array.isArray(value.results)) return value.results.flatMap(responseIds);
	if (Array.isArray(value.content)) return value.content.flatMap((part: any) => responseIds(part.text));
	return [];
}

if (import.meta.main) {
	try {
		const [events, stderr, runId, mode, start, end] = process.argv.slice(2);
		console.log(validateCompletion(await Bun.file(events).text(), await Bun.file(stderr).text(), { runId, mode, start, end }));
	} catch (error) {
		console.error(error instanceof Error ? error.message : "completion validation failed");
		process.exit(1);
	}
}
