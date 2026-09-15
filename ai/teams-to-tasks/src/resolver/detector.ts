#!/usr/bin/env bun
/**
 * Cron detector for the task-resolver (spec R1, design D1).
 * DEPRECATED (v1, dormant): superseded by enrich.ts.
 *
 * LLM-free, read-only poll: queries ONLY the Tareas data source for Estado
 * `📥 Inbox`, never the forbidden SIS data sources, deduplicates by
 * page/revision through seen.json, and hands new items to the attended
 * evaluator via pending-queue.json (run-store conventions).
 *
 * Process model mirrors the teams-to-tasks cron split (cron.sh precedent):
 *  - PAUSE file (<stateRoot>/PAUSE) skips the run cleanly (exit 0).
 *  - Single-instance lock (<stateRoot>/lock): a live holder refuses the
 *    run (skip, exit 0); a stale holder (dead pid) is reclaimed.
 *  - Exit codes: 0 news, 1 no-news, 2 failure (threat matrix: a Notion
 *    hard failure exits 2 with seen.json byte-untouched — no re-detection,
 *    every state write happens only after a fully successful query+parse).
 *
 * Every Notion interaction goes through an injectable fetch seam; tests
 * inject fakes and never touch the network or credentials.
 */

import {
	closeSync,
	existsSync,
	mkdirSync,
	openSync,
	readFileSync,
	rmSync,
	writeSync,
} from "node:fs";
import { join } from "node:path";
import {
	DEFAULT_DATA_SOURCE_ID,
	NOTION_API_BASE,
	NOTION_API_VERSION,
} from "../notion-writer.ts";
import { defaultStateDir, markSeen, readQueue, readSeen, writeQueue } from "./run-store.ts";

/** The Tareas data source — the ONLY default detection target. */
export const TAREAS_DATA_SOURCE_ID = DEFAULT_DATA_SOURCE_ID;

/**
 * Forbidden Clece SIS maintenance data sources (notion-personal-backlog
 * hard rule): never queried, never migrated, never written. Refused by
 * configuration AND by response scan — any component attempting one fails
 * closed (spec R1 scenario "Forbidden source refused").
 */
export const FORBIDDEN_SOURCE_IDS: readonly string[] = [
	"2345b76b-8c24-409e-a728-6074d38acefd",
	"797cb00f-e8dc-4762-be4c-6bd8c43f64d4",
];

/** The only Estado value the detector ever selects. */
export const INBOX_STATUS = "📥 Inbox";

/** Exit codes (poller.ts precedent: news/no-news/failure). */
export const DETECTOR_EXIT = { NEWS: 0, NO_NEWS: 1, FAILURE: 2 } as const;

export type DetectorOutcome = "news" | "no-news" | "paused" | "locked" | "failure";

/** Thrown whenever a forbidden source is configured or observed. */
export class ForbiddenSourceError extends Error {
	constructor(sourceId: string) {
		super(`forbidden SIS data source refused: ${sourceId}`);
		this.name = "ForbiddenSourceError";
	}
}

export type FetchLike = (
	input: string | URL | Request,
	init?: RequestInit,
) => Promise<Response>;

export interface DetectorDeps {
	fetchFn: FetchLike;
	/** Notion integration token — injected, never persisted by the detector. */
	token: string;
	/** Configurable via NOTION_DATA_SOURCE_ID; defaults to Tareas. */
	dataSourceId: string;
	/** Tests inject temp dirs; production resolves the machine-local default. */
	stateDir: string;
}

/** Refuse a forbidden target before anything else runs. */
export function assertAllowedSource(dataSourceId: string): void {
	if (FORBIDDEN_SOURCE_IDS.includes(dataSourceId)) {
		throw new ForbiddenSourceError(dataSourceId);
	}
}

/** The exact query body: Estado = 📥 Inbox, nothing else. */
export function buildInboxQuery() {
	return {
		filter: { property: "Estado", select: { equals: INBOX_STATUS } },
		page_size: 100,
	};
}

/** A detected Inbox item (queue + dedupe shape). */
export interface InboxPage {
	page_id: string;
	revision: string;
	title: string;
}

interface RawPage {
	id?: unknown;
	last_edited_time?: unknown;
	parent?: { type?: unknown; data_source_id?: unknown };
	properties?: Record<string, unknown>;
}

/**
 * Parse query results into InboxPage items. Fail-closed guards: the raw
 * payload must not reference any forbidden source, and every returned page
 * must be parented at the data source we queried (a page from anywhere
 * else means the response does not match the request — refuse).
 */
export function parseInboxPages(
	results: unknown,
	expectedSourceId: string,
	rawText: string,
): InboxPage[] {
	for (const forbidden of FORBIDDEN_SOURCE_IDS) {
		if (rawText.includes(forbidden)) throw new ForbiddenSourceError(forbidden);
	}
	if (!Array.isArray(results)) return [];
	const pages: InboxPage[] = [];
	for (const entry of results as RawPage[]) {
		const parent = entry.parent;
		if (
			parent === undefined ||
			parent.type !== "data_source_id" ||
			parent.data_source_id !== expectedSourceId
		) {
			throw new ForbiddenSourceError(String(parent?.data_source_id ?? "unknown-parent"));
		}
		if (typeof entry.id !== "string" || typeof entry.last_edited_time !== "string") continue;
		pages.push({
			page_id: entry.id,
			revision: entry.last_edited_time,
			title: extractTitle(entry.properties),
		});
	}
	return pages;
}

/** First title-type property wins; absent ⇒ untitled (never a hard failure). */
function extractTitle(properties: Record<string, unknown> | undefined): string {
	for (const value of Object.values(properties ?? {})) {
		const parts = (value as { title?: { plain_text?: string; text?: { content?: string } }[] })
			?.title;
		if (Array.isArray(parts) && parts.length > 0) {
			const text = parts
				.map((part) => part.plain_text ?? part.text?.content ?? "")
				.join("");
			if (text.trim() !== "") return text;
		}
	}
	return "untitled";
}

/** Outcome → process exit code (paused/locked skip like cron.sh). */
export function exitForOutcome(outcome: DetectorOutcome): number {
	switch (outcome) {
		case "news":
			return DETECTOR_EXIT.NEWS;
		case "no-news":
			return DETECTOR_EXIT.NO_NEWS;
		case "failure":
			return DETECTOR_EXIT.FAILURE;
		case "paused":
		case "locked":
			return 0;
	}
}

/** Lock release callback; removing the file is the whole release. */
export type LockRelease = () => void;

function tryCreateLock(lockPath: string, pid: number, iso: string): boolean {
	try {
		const fd = openSync(lockPath, "wx");
		try {
			writeSync(fd, `${pid} ${iso}\n`);
		} finally {
			closeSync(fd);
		}
		return true;
	} catch (err) {
		if ((err as NodeJS.ErrnoException).code === "EEXIST") return false;
		throw err;
	}
}

/**
 * Single-instance lock via exclusive create. A lock held by a LIVE process
 * (checked through /proc — Linux only, like the rest of the subsystem)
 * refuses the run; a lock whose holder is dead is reclaimed (cron timeout
 * backstop cleans up the rest); unparsable lock content stays held — fail
 * closed, a human investigates.
 */
export function acquireLock(
	stateDir: string,
	pid: number = process.pid,
): LockRelease | null {
	const lockPath = join(stateDir, "lock");
	mkdirSync(stateDir, { recursive: true });
	const now = new Date().toISOString();
	if (tryCreateLock(lockPath, pid, now)) {
		return () => rmSync(lockPath, { force: true });
	}
	let held = true;
	try {
		const holderPid = Number(readFileSync(lockPath, "utf8").split(" ")[0]);
		if (Number.isInteger(holderPid) && holderPid > 0) {
			held = holderPid === process.pid || existsSync(`/proc/${holderPid}`);
		}
	} catch {
		held = true; // unreadable/unparsable — never steal blind
	}
	if (held) return null;
	rmSync(lockPath, { force: true });
	return tryCreateLock(lockPath, pid, now) ? () => rmSync(lockPath, { force: true }) : null;
}

/**
 * One detection cycle. Ordering is the safety property: source assertion →
 * PAUSE → lock → query → parse → dedupe-select → queue write → seen write.
 * Every throw path (forbidden source, HTTP failure, network error) happens
 * BEFORE the first state write, so seen.json stays byte-untouched on
 * failure (no re-detection on the retry).
 */
export async function runDetection(deps: DetectorDeps): Promise<DetectorOutcome> {
	assertAllowedSource(deps.dataSourceId);

	if (existsSync(join(deps.stateDir, "PAUSE"))) return "paused";

	const release = acquireLock(deps.stateDir);
	if (release === null) return "locked";
	try {
		const response = await deps.fetchFn(
			`${NOTION_API_BASE}/data_sources/${deps.dataSourceId}/query`,
			{
				method: "POST",
				headers: {
					Authorization: `Bearer ${deps.token}`,
					"Notion-Version": NOTION_API_VERSION,
					"Content-Type": "application/json",
				},
				body: JSON.stringify(buildInboxQuery()),
			},
		);
		if (!response.ok) {
			throw new Error(`Notion query failed: HTTP ${response.status}`);
		}
		const rawText = await response.text();
		const body = JSON.parse(rawText) as { results?: unknown };
		const pages = parseInboxPages(body.results, deps.dataSourceId, rawText);

		const seen = readSeen(deps.stateDir);
		const fresh = pages.filter((page) => seen[page.page_id]?.revision !== page.revision);
		if (fresh.length === 0) return "no-news";

		const queue = readQueue(deps.stateDir);
		for (const page of fresh) {
			const alreadyQueued = queue.some(
				(item) => item.page_id === page.page_id && item.revision === page.revision,
			);
			if (!alreadyQueued) {
				queue.push({
					page_id: page.page_id,
					revision: page.revision,
					title: page.title,
					detected_at: new Date().toISOString(),
				});
			}
		}
		// Queue first, then seen: a crash between the two self-heals (the
		// next run re-selects the page, re-dedupe skips the queued item).
		writeQueue(queue, deps.stateDir);
		for (const page of fresh) markSeen(page.page_id, page.revision, deps.stateDir);
		return "news";
	} finally {
		release();
	}
}

async function main(): Promise<number> {
	const token = process.env.NOTION_TOKEN || process.env.NOTION_CLECE;
	if (!token) {
		console.error("missing NOTION_TOKEN / NOTION_CLECE environment variable");
		return DETECTOR_EXIT.FAILURE;
	}
	const dataSourceId = process.env.NOTION_DATA_SOURCE_ID || TAREAS_DATA_SOURCE_ID;
	try {
		const outcome = await runDetection({
			fetchFn: globalThis.fetch,
			token,
			dataSourceId,
			stateDir: defaultStateDir(),
		});
		console.log(`detector: ${outcome}`);
		return exitForOutcome(outcome);
	} catch (err) {
		console.error(err instanceof Error ? err.message : err);
		return DETECTOR_EXIT.FAILURE;
	}
}

if (import.meta.main) {
	process.exit(await main());
}
