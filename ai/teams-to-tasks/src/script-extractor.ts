/**
 * Teams-to-tasks Script Extractor — Direct Playwright-core extraction.
 *
 * Runs without LLM or OpenCode, extracting Teams search results directly from
 * the DOM with the persistent user profile. Emits structured messages and
 * planned Notion actions for comparison with the agent-based run.
 */
import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, type BrowserContext, type Page } from "playwright-core";
import { normalizeTimestamp, parseState } from "./core.ts";
import { resolveChromiumExecutablePath } from "../poller.ts";
import type { NotionAction, CreateTaskAction, AppendDigestAction } from "./notion-writer.ts";

const TEAMS_URL = "https://teams.microsoft.com";
const SEARCH_TERM = "que";
const SEARCH_BOX =
	'input#search-box-input, input[aria-label*="earch" i], input[placeholder*="earch" i]';
const RESULTS_ROW = '[role="treegrid"] [role="row"]';

export interface ExtractedMessage {
	author: string;
	timestamp: string;
	normalizedDate: string | null;
	chatTitle: string;
	snippet: string;
	fingerprint: string;
	isActionable: boolean;
	inWindow: boolean;
}

export interface ExtractionResult {
	runId: string;
	mode: string;
	windowStart: string;
	windowEnd: string;
	durationMs: number;
	messages: ExtractedMessage[];
	actions: NotionAction[];
	status: "complete" | "quiet" | "session_expired" | "error";
	error?: string;
}

/** Check if text contains task-like cues or requests directed to Bruno */
export function isActionableText(text: string): boolean {
	const lower = text.toLowerCase();
	// Action keywords in Spanish/English commonly found in team requests
	const actionPatterns = [
		/\b(revisa|revisar|revisas|puedes|podrías|podrias|mira|mirar|ojo)\b/,
		/\b(haz|hacer|crea|crear|sube|subir|pasa|pasar|envía|enviar|manda|mandar)\b/,
		/\b(urgente|pendiente|prioridad|deadline|para hoy|para mañana|vence)\b/,
		/\b(acuerda|acuerdate|acuérdate|recuerda|recordatorio)\b/,
		/\b(bloqueo|bloqueado|bloqueante|impedimento|ticket|jira|notion)\b/,
		/\?/, // questions often imply follow-up/action
	];
	return actionPatterns.some((pattern) => pattern.test(lower));
}

export async function extractTeamsMessages(options: {
	runDir: string;
	mode: string;
	windowStart: string;
	windowEnd: string;
	userDataDir: string;
	executablePath: string;
	timeoutMs?: number;
}): Promise<ExtractionResult> {
	const startTime = Date.now();
	const timeoutMs = options.timeoutMs ?? 45_000;
	const runId = options.runDir.split("/").pop() || "unknown-run";
	let context: BrowserContext | null = null;

	try {
		context = await chromium.launchPersistentContext(options.userDataDir, {
			executablePath: options.executablePath,
			headless: true,
			timeout: timeoutMs,
		});

		const page = await context.newPage();
		page.setDefaultTimeout(timeoutMs);

		await page.goto(TEAMS_URL, { waitUntil: "domcontentloaded" });

		const searchBox = page.locator(SEARCH_BOX).first();
		try {
			await searchBox.waitFor({ state: "visible", timeout: 20_000 });
		} catch {
			return {
				runId,
				mode: options.mode,
				windowStart: options.windowStart,
				windowEnd: options.windowEnd,
				durationMs: Date.now() - startTime,
				messages: [],
				actions: [],
				status: "session_expired",
				error: "Search box not found — session may be expired or login required",
			};
		}

		await searchBox.fill(SEARCH_TERM);
		await page.keyboard.press("Enter");

		const dateFilter = page.getByRole("button", { name: /^(date|fecha)/i });
		await dateFilter.click();
		const today = page
			.getByRole("option", { name: /^(today|hoy)$/i })
			.or(page.getByRole("menuitem", { name: /^(today|hoy)$/i }));
		await today.first().click();

		const resultsContainer = page.locator(
			'[role="list"], [role="treegrid"], [data-testid*="result" i]',
		).first();
		await resultsContainer.waitFor({ state: "attached", timeout: 20_000 });

		const rows = page.locator(RESULTS_ROW);
		const count = await rows.count();
		const extractedMessages: ExtractedMessage[] = [];
		const actions: NotionAction[] = [];

		const now = new Date();
		const startDate = parseState(options.windowStart);
		const endDate = parseState(options.windowEnd) || now;

		for (let i = 0; i < count; i++) {
			const row = rows.nth(i);
			let rawText = "";
			try {
				rawText = (await row.innerText()) || "";
			} catch {
				continue;
			}

			const lines = rawText
				.split("\n")
				.map((l) => l.trim())
				.filter(Boolean);
			if (!lines.length) continue;

			// Extract timestamp
			const timeMatch = rawText.match(/(\d{1,2}:\d{2}\s*(?:AM|PM)?)/i);
			const rawTimestamp = timeMatch ? timeMatch[1] : "";
			const normDate = rawTimestamp ? normalizeTimestamp(rawTimestamp, now) : null;

			// Identify author and chat/channel title
			let author = lines[0] || "Desconocido";
			let chatTitle = lines.length > 2 ? lines[1] : "Teams Chat";
			let snippet = lines.slice(2).join(" ");
			if (!snippet && lines.length > 1) {
				snippet = lines[1];
				chatTitle = "Teams Chat";
			}

			// Clean author if it includes timestamp
			author = author.replace(/(\d{1,2}:\d{2}\s*(?:AM|PM)?)/i, "").trim();

			// Ignore messages sent by Bruno himself
			const isSelf = /\bbruno\b/i.test(author);
			if (isSelf) continue;

			const inWindow = normDate
				? (startDate ? normDate > startDate : true) && normDate <= endDate
				: true;

			const actionable = isActionableText(snippet);
			const safeChat = chatTitle.replace(/[:\n]/g, "-").slice(0, 30);
			const safeAuthor = author.replace(/[:\n]/g, "-").slice(0, 30);
			const stampTag = normDate
				? normDate.toISOString().slice(0, 16).replace("T", "-")
				: "today";
			const fingerprint = `teams:${safeChat}:${safeAuthor}:${stampTag}`;

			const extracted: ExtractedMessage = {
				author,
				timestamp: rawTimestamp,
				normalizedDate: normDate ? normDate.toISOString() : null,
				chatTitle,
				snippet,
				fingerprint,
				isActionable: actionable,
				inWindow,
			};
			extractedMessages.push(extracted);

			if (options.mode === "sweep" && inWindow && actionable) {
				const title =
					snippet.length > 80
						? snippet.slice(0, 77) + "..."
						: snippet || `Tarea de ${author}`;
				const action: CreateTaskAction = {
					action: "create",
					title: `[Teams] ${title}`,
					notes: `${fingerprint}\n${author} en ${chatTitle} (${rawTimestamp}): "${snippet}"`,
					priority: "🟡 Media",
					project: "otro/vida",
					status: "📥 Inbox",
					fingerprint,
				};
				actions.push(action);
			}
		}

		if (options.mode === "digest" && extractedMessages.length > 0) {
			const digestAction: AppendDigestAction = {
				action: "digest",
				parent_id: "digest-teams-page",
				date_heading: now.toISOString().slice(0, 10),
				sections: [
					{
						heading: "Temas tratados",
						bullets: extractedMessages.map(
							(m) => `${m.author} en ${m.chatTitle}: ${m.snippet.slice(0, 120)}`,
						),
					},
					{
						heading: "Tareas identificadas",
						bullets: actions.map((a: any) => a.title || "Tarea"),
					},
				],
			};
			actions.push(digestAction);
		}

		return {
			runId,
			mode: options.mode,
			windowStart: options.windowStart,
			windowEnd: options.windowEnd,
			durationMs: Date.now() - startTime,
			messages: extractedMessages,
			actions,
			status: "complete",
		};
	} catch (err: any) {
		return {
			runId,
			mode: options.mode,
			windowStart: options.windowStart,
			windowEnd: options.windowEnd,
			durationMs: Date.now() - startTime,
			messages: [],
			actions: [],
			status: "error",
			error: String(err?.message || err),
		};
	} finally {
		if (context) {
			try {
				await context.close();
			} catch {}
		}
	}
}

if (import.meta.main) {
	const runDir = process.argv[2] || process.env.TEAMS_RUN_DIR || "/tmp/teams-run";
	const mode = process.argv[3] || process.env.TEAMS_MODE || "sweep";
	const windowStart = process.argv[4] || process.env.TEAMS_WINDOW_START || "";
	const windowEnd = process.argv[5] || process.env.TEAMS_WINDOW_END || "";

	const home = process.env.HOME || "/home/bruno";
	const cacheDir = join(home, ".cache/ms-playwright");
	const executablePath = resolveChromiumExecutablePath(cacheDir);

	if (!executablePath || !existsSync(executablePath)) {
		console.error(`[script-extractor] Chromium not found in ${cacheDir}`);
		process.exit(2);
	}

	const userDataDir = join(
		home,
		".local/share/opencode/playwright-teams-profile",
	);

	extractTeamsMessages({
		runDir,
		mode,
		windowStart,
		windowEnd,
		userDataDir,
		executablePath,
	})
		.then((result) => {
			const msgsPath = join(runDir, "script-messages.json");
			const actionsPath = join(runDir, "actions.script.json");
			const summaryPath = join(runDir, "script-summary.txt");

			writeFileSync(msgsPath, JSON.stringify(result.messages, null, 2));
			writeFileSync(actionsPath, JSON.stringify(result.actions, null, 2));

			const summary = `script_status=${result.status}; messages=${result.messages.length}; actions=${result.actions.length}; duration=${(result.durationMs / 1000).toFixed(1)}s`;
			writeFileSync(summaryPath, summary);
			console.log(`[script-extractor] ${summary}`);
			process.exit(result.status === "error" ? 1 : 0);
		})
		.catch((err) => {
			console.error("[script-extractor] Uncaught error:", err);
			process.exit(1);
		});
}
