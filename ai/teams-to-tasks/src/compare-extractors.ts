/**
 * Compare outputs of Direct Script Extractor vs Agent MCP Runner.
 *
 * Runs post-execution in cron.sh, producing a side-by-side comparison artifact
 * and structured metrics to evaluate if the script extractor achieves equal
 * or superior efficacy without LLM token burn.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

interface ActionItem {
	action: string;
	title?: string;
	fingerprint?: string;
	[key: string]: any;
}

export function compareRuns(runDir: string): {
	scriptActionsCount: number;
	agentActionsCount: number;
	scriptMessagesCount: number;
	overlapCount: number;
	scriptOnly: string[];
	agentOnly: string[];
	scriptSummary: string;
	agentSummary: string;
} {
	const scriptActionsPath = join(runDir, "actions.script.json");
	const agentActionsPath = join(runDir, "actions.json");
	const scriptMsgsPath = join(runDir, "script-messages.json");
	const scriptSummaryPath = join(runDir, "script-summary.txt");
	const agentSummaryPath = join(runDir, "summary.txt");

	let scriptActions: ActionItem[] = [];
	let agentActions: ActionItem[] = [];
	let scriptMessages: any[] = [];
	let scriptSummary = "";
	let agentSummary = "";

	if (existsSync(scriptActionsPath)) {
		try {
			scriptActions = JSON.parse(readFileSync(scriptActionsPath, "utf8"));
		} catch {}
	}
	if (existsSync(agentActionsPath)) {
		try {
			agentActions = JSON.parse(readFileSync(agentActionsPath, "utf8"));
		} catch {}
	}
	if (existsSync(scriptMsgsPath)) {
		try {
			scriptMessages = JSON.parse(readFileSync(scriptMsgsPath, "utf8"));
		} catch {}
	}
	if (existsSync(scriptSummaryPath)) {
		try {
			scriptSummary = readFileSync(scriptSummaryPath, "utf8").trim();
		} catch {}
	}
	if (existsSync(agentSummaryPath)) {
		try {
			agentSummary = readFileSync(agentSummaryPath, "utf8").trim();
		} catch {}
	}

	const getFingerprint = (a: ActionItem) =>
		a.fingerprint || a.title || JSON.stringify(a);

	const scriptFPs = new Set(scriptActions.map(getFingerprint));
	const agentFPs = new Set(agentActions.map(getFingerprint));

	const overlap = [...scriptFPs].filter((fp) => agentFPs.has(fp));
	const scriptOnly = [...scriptFPs].filter((fp) => !agentFPs.has(fp));
	const agentOnly = [...agentFPs].filter((fp) => !scriptFPs.has(fp));

	const comparison = {
		scriptActionsCount: scriptActions.length,
		agentActionsCount: agentActions.length,
		scriptMessagesCount: scriptMessages.length,
		overlapCount: overlap.length,
		scriptOnly,
		agentOnly,
		scriptSummary,
		agentSummary,
	};

	// Write comparison markdown artifact
	const mdContent = `# Shadow Run Comparison: Script vs Agent MCP

- **Run Directory:** \`${runDir}\`
- **Timestamp:** ${new Date().toISOString()}

## Summary Metrics

| Metric | Direct Script Extractor | Agent MCP (LLM) |
|---|---|---|
| **Status / Summary** | ${scriptSummary || "n/a"} | ${agentSummary || "n/a"} |
| **Total Actions** | ${scriptActions.length} | ${agentActions.length} |
| **Total Messages Scanned** | ${scriptMessages.length} | *(in events.jsonl)* |
| **Common Actions (Overlap)** | ${overlap.length} | ${overlap.length} |
| **Unique Actions** | ${scriptOnly.length} | ${agentOnly.length} |

## Actions in Script Extractor
\`\`\`json
${JSON.stringify(scriptActions, null, 2)}
\`\`\`

## Actions in Agent MCP
\`\`\`json
${JSON.stringify(agentActions, null, 2)}
\`\`\`

## Discrepancies
- **Only in Script:** ${scriptOnly.length ? scriptOnly.join(", ") : "None"}
- **Only in Agent:** ${agentOnly.length ? agentOnly.join(", ") : "None"}
`;

	writeFileSync(join(runDir, "comparison.json"), JSON.stringify(comparison, null, 2));
	writeFileSync(join(runDir, "comparison.md"), mdContent);

	return comparison;
}

if (import.meta.main) {
	const runDir = process.argv[2] || process.env.TEAMS_RUN_DIR;
	if (!runDir || !existsSync(runDir)) {
		console.error("[compare-extractors] Usage: bun compare-extractors.ts <runDir>");
		process.exit(1);
	}

	const c = compareRuns(runDir);
	const line = `[SHADOW-TEST] script_actions=${c.scriptActionsCount} agent_actions=${c.agentActionsCount} overlap=${c.overlapCount} script_msgs=${c.scriptMessagesCount} | script: [${c.scriptSummary}] agent: [${c.agentSummary}]`;
	console.log(line);
}
