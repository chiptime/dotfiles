/** Post-mortem classifier for failed runs: maps the agent exit code and
 * reconstructs WHERE the run died from the event stream. Emits one fixed
 * ASCII one-liner for the toast (never raw agent content) and writes the
 * full breakdown to <run_dir>/diag.txt. */
import { readFileSync } from "node:fs";

type Event = { type: string; timestamp?: number; part?: any };

const WALL_SECONDS: Record<string, number> = { sweep: 900, digest: 1500 };
const TOOL_STAGE: Record<string, string> = {
	"playwright_teams_browser_navigate": "discovery",
	"playwright_teams_browser_type": "discovery",
	"playwright_teams_browser_snapshot": "discovery",
	teams_read: "reading messages",
	"playwright_teams_browser_take_screenshot": "reading messages",
	"notion_API-query-data-source": "notion dedupe",
	"notion_API-post-search": "notion dedupe",
	"notion_API-get-block-children": "digest heading lookup",
};

const readText = (path: string): string => {
	try { return readFileSync(path, "utf8"); } catch { return ""; }
};

const localTime = (ms: number): string =>
	new Date(ms).toLocaleTimeString("en-GB", { hour12: false });

function parseEvents(raw: string): Event[] {
	return raw.split("\n").filter((line) => line.trim()).map((line) => {
		try { return JSON.parse(line) as Event; } catch { return { type: "invalid" }; }
	});
}

function stageOf(tools: any[], events: Event[]): string {
	if (!tools.length) return "boot (no tool calls)";
	const last = tools.at(-1).tool as string;
	if (last === "playwright_teams_browser_close") return "final contract composition";
	// A step_start as the last event means the model was mid-generation.
	if (events.at(-1)?.type === "step_start") return "generation (mid-response)";
	return TOOL_STAGE[last] ?? `post-${last}`;
}

function sessionSignal(events: Event[], stderr: string): boolean {
	const haystack = JSON.stringify(events).slice(0, 4_000_000) + stderr;
	return /SESION_EXPIRADA|sign in|iniciar sesión|login\.live|login\.microsoftonline/i.test(haystack);
}

export function diagnose(runDir: string, mode: string, agentRc: number, wallArg?: number): string {
	// The wall must be the one the run actually used: cron.sh passes it; the
	// table is only a fallback for manual post-mortems of unknown-vintage runs.
	const wall = wallArg ?? WALL_SECONDS[mode] ?? 900;
	const events = parseEvents(readText(`${runDir}/events.jsonl`));
	const stderr = readText(`${runDir}/agent.stderr`);
	const validation = readText(`${runDir}/validation.log`);

	const toolEvents = events.filter((e) => e.type === "tool_use" && e.part?.tool);
	const tools = toolEvents.map((e) => e.part);
	const counts: Record<string, number> = {};
	for (const p of tools) counts[p.tool] = (counts[p.tool] ?? 0) + 1;
	const firstTs = events.find((e) => e.timestamp)?.timestamp;
	const lastTs = events.findLast((e) => e.timestamp)?.timestamp;
	const lastFinish = events.findLast((e) => e.type === "step_finish")?.part;
	const ctxK = Math.round((lastFinish?.tokens?.total ?? 0) / 1000);
	const stage = stageOf(tools, events);
	const session = sessionSignal(events, stderr);

	// rc → cause
	let cause: string;
	if (agentRc === 124) cause = `timeout: SIGTERM at ${wall}s wall`;
	else if (agentRc === 137) cause = "SIGKILL (TERM ignored or OOM)";
	else if (agentRc === 0) cause = "agent exited 0 but validator rejected";
	else cause = `agent crash rc=${agentRc}`;

	const reads = counts["teams_read"] ?? 0;
	const shots = counts["playwright_teams_browser_take_screenshot"] ?? 0;
	const queries = (counts["notion_API-query-data-source"] ?? 0) + (counts["notion_API-post-search"] ?? 0);
	const writes = counts["notion_API-patch-block-children"] ?? 0;
	const activeMin = firstTs && lastTs ? ((lastTs - firstTs) / 60000).toFixed(1) : "0";
	// Approximate idle tail: wall minus active span (only meaningful for rc=124).
	const idleS = firstTs && lastTs && agentRc === 124
		? Math.max(0, Math.round(wall - (lastTs - firstTs) / 1000))
		: 0;

	const oneLine = [
		cause,
		...(agentRc === 0
			? [`reason=${validation.trim().split("\n").filter(Boolean).at(-1) ?? "unknown"}`]
			: []),
		`stage=${stage}`,
		`${reads} reads/${shots} shots/${queries} notion-q/${writes} writes`,
		`ctx ${ctxK}k`,
		`active ${activeMin}m`,
		agentRc === 124 ? `idle-before-kill ~${idleS}s` : "",
		`session=${session ? "SUSPECT" : "ok"}`,
	].filter(Boolean).join("; ");

	const lines: string[] = [
		`run_dir: ${runDir}`,
		`mode: ${mode}`,
		`agent_rc: ${agentRc}`,
		`cause: ${cause}`,
		`stage: ${stage}`,
		`session_signal: ${session}`,
		`context_tokens_at_last_finish: ${lastFinish?.tokens?.total ?? "n/a"}`,
		`active_span: ${activeMin} min (${firstTs ? localTime(firstTs) : "?"} -> ${lastTs ? localTime(lastTs) : "?"})`,
		`tool_counts: ${JSON.stringify(counts)}`,
		`last_tool: ${tools.at(-1)?.tool ?? "none"}`,
		`last_event_type: ${events.at(-1)?.type ?? "none"}`,
		"",
		"--- phase timeline (first call per tool) ---",
	];
	const seen = new Set<string>();
	for (const e of toolEvents) {
		if (seen.has(e.part.tool)) continue;
		seen.add(e.part.tool);
		lines.push(`${localTime(e.timestamp ?? 0)}  first ${e.part.tool}`);
	}
	if (stderr.trim()) lines.push("", "--- agent.stderr (tail) ---", ...stderr.trim().split("\n").slice(-10));
	if (validation.trim()) lines.push("", "--- validation.log ---", ...validation.trim().split("\n").slice(-5));
	lines.push("", "--- one-liner ---", oneLine);
	void Bun.write(`${runDir}/diag.txt`, lines.join("\n") + "\n");
	return oneLine;
}

if (import.meta.main) {
	const [runDir, mode, rc, wall] = process.argv.slice(2);
	if (!runDir || !mode || Number.isNaN(Number(rc))) {
		console.error("usage: bun src/diagnose.ts <run_dir> <mode> <agent_rc> [wall_seconds]");
		process.exit(2);
	}
	console.log(diagnose(runDir, mode, Number(rc), wall === undefined ? undefined : Number(wall)));
}
