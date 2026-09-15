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
		text?.type !== "text" || text?.part?.type !== "text" || !text.part?.messageID ||
		text.part.messageID !== finish.part?.messageID || !text.part.time?.end) fail("missing final assistant result");
	let result: any;
	let repaired = 0;
	try {
		result = JSON.parse(text!.part.text);
	} catch {
		// Production failure mode (4 consecutive digests, ~250k context): the
		// document is complete but the model drops 1-2 trailing closing
		// brackets. Repair ONLY that: reparse after appending the closers
		// implied by the bracket stack. Any other malformation, and every
		// substantive contract check below, stays fail-closed.
		const repairedRaw = balancedCloserRepair(text.part.text);
		if (repairedRaw === null) fail("malformed final assistant result");
		try {
			result = JSON.parse(repairedRaw);
			repaired = repairedRaw.length - text.part.text.length;
		} catch {
			fail("malformed final assistant result");
		}
	}
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
	// The agent must be strictly read-only: direct Notion writes are forbidden.
	if (tools.some((part) => ["notion_API-post-page", "notion_API-patch-page", "notion_API-patch-block-children"].includes(part.tool))) {
		fail("unexpected agent direct Notion write");
	}
	const actions = result.actions;
	if (actions.length && window.mode === "sweep" && !used("notion_API-query-data-source")) {
		fail("missing dedupe lookup evidence");
	}
	const hasDigest = actions.some((a: any) => (a?.action || a?.type) === "digest");
	if (hasDigest && window.mode === "digest" && !used("notion_API-get-block-children")) {
		fail("missing digest heading lookup evidence");
	}
	for (const a of actions) {
		if (!a || typeof a !== "object") fail("invalid action payload");
		const act = a.action || a.type;
		if (!["create", "enrich", "resolve", "digest"].includes(act)) {
			fail(`unrecognized action type: ${act}`);
		}
		if (act === "create") {
			if (!a.title || typeof a.title !== "string" || !a.title.trim()) fail("create action missing title");
			if (typeof a.notes !== "string") fail("create action missing notes");
		} else if (act === "enrich") {
			if (!a.page_id || typeof a.page_id !== "string") fail("enrich action missing page_id");
			if (typeof a.notes !== "string" && typeof a.notes_update !== "string") fail("enrich action missing notes");
		} else if (act === "resolve") {
			if (!a.page_id || typeof a.page_id !== "string") fail("resolve action missing page_id");
		} else if (act === "digest") {
			if (!a.parent_id || typeof a.parent_id !== "string") fail("digest action missing parent_id");
			if (!a.date_heading || typeof a.date_heading !== "string") fail("digest action missing date_heading");
			if (!Array.isArray(a.sections)) fail("digest action missing sections");
		}
	}
	const created = actions.filter((a: any) => (a.action || a.type) === "create").length;
	const updated = actions.filter((a: any) => ["enrich", "resolve"].includes(a.action || a.type)).length;
	const repairedNote = repaired > 0 ? `; final JSON repaired (+${repaired} closers)` : "";
	return `Validated ${window.mode}: ${created} created, ${updated} updated (que+Today scope)${repairedNote}`;
}

/** Appends only the trailing closing brackets implied by the bracket stack
 * (string- and escape-aware). Returns null when the text ends mid-string,
 * has more closers than openers anywhere, or exceeds the repair cap — those
 * are real malformations, never repaired. */
function balancedCloserRepair(raw: string): string | null {
	const stack: string[] = [];
	let inStr = false;
	let esc = false;
	for (const ch of raw) {
		if (esc) {
			esc = false;
			continue;
		}
		if (inStr && ch === "\\") {
			esc = true;
			continue;
		}
		if (ch === '"') {
			inStr = !inStr;
			continue;
		}
		if (inStr) continue;
		if (ch === "{" || ch === "[") stack.push(ch);
		else if (ch === "}" || ch === "]") {
			if (!stack.length) return null;
			stack.pop();
		}
	}
	if (inStr || !stack.length || stack.length > 8) return null;
	return raw + [...stack].reverse().map((c) => (c === "{" ? "}" : "]")).join("");
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

export function extractActions(raw: string): any[] {
	const events: Event[] = raw.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line));
	const text = events.at(-2);
	const result = JSON.parse(text!.part.text);
	return result.actions ?? result.tasks ?? [];
}

if (import.meta.main) {
	try {
		const [events, stderr, runId, mode, start, end, actionsOut] = process.argv.slice(2);
		const raw = await Bun.file(events).text();
		const err = await Bun.file(stderr).text();
		console.log(validateCompletion(raw, err, { runId, mode, start, end }));
		const targetFile = actionsOut || process.env.TEAMS_ACTIONS_FILE;
		if (targetFile) {
			const actions = extractActions(raw);
			await Bun.write(targetFile, JSON.stringify(actions, null, 2));
		}
	} catch (error) {
		console.error(error instanceof Error ? error.message : "completion validation failed");
		process.exit(1);
	}
}
