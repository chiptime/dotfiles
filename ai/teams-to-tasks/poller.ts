#!/usr/bin/env bun
/**
 * teams-to-tasks poller — LLM-free, read-only preflight.
 *
 * Owns what the pure core cannot: state I/O, the hard deadline, signals, and
 * process exit codes. All browser I/O stays behind the TeamsSearchAdapter seam
 * (src/teams-page.ts); bun tests inject fakes. Exit codes: 0 news (agent
 * window), 1 no-news, 2 failure (cron.sh notifies). State (ISO-8601 UTC)
 * advances only on no-news; news defers advancement to cron.sh after the
 * gated agent succeeds; every failure leaves last_run byte-identical.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
	EXIT,
	decide,
	exitFor,
	formatState,
	normalizeTimestamp,
	parseState,
} from "./src/core.ts";
import type { TeamsSearchAdapter } from "./src/core.ts";
import { TeamsSearchPage } from "./src/teams-page.ts";

/** Dependencies of one poll; every seam is injectable so tests need no Chromium. */
export interface PollDeps {
	adapter: TeamsSearchAdapter;
	stateFile: string;
	/** Hard deadline for adapter.search(); expiry follows the failure path. */
	deadlineMs: number;
	/** Shared idempotent close — signals and runPoll must close exactly once. */
	close?: () => Promise<void>;
}

/** In-process deadline leaves cron's `timeout -k 15s 120s` a 20s SIGKILL backstop. */
const DEFAULT_DEADLINE_MS = 100_000;

/**
 * Newest usable full-chromium build in a Playwright cache dir, else null.
 * `chromium_headless_shell-*` dirs are excluded by pattern: the headless
 * shell cannot host the shared persistent profile.
 */
export function resolveChromiumExecutablePath(cacheDir: string): string | null {
	let entries: string[];
	try {
		entries = readdirSync(cacheDir);
	} catch {
		return null; // missing cache — installer smoke-check territory
	}
	const builds = entries
		.map((name) => /^chromium-(\d+)$/.exec(name))
		.filter((match): match is RegExpExecArray => match !== null)
		.map((match) => ({ rev: Number(match[1]), name: match[0] }))
		.sort((a, b) => b.rev - a.rev);
	for (const { name } of builds) {
		for (const layout of ["chrome-linux64", "chrome-linux"]) {
			const exe = join(cacheDir, name, layout, "chrome");
			if (existsSync(exe)) return exe;
		}
	}
	return null;
}

/** Idempotent close: exactly one adapter.close() however the paths overlap. */
function closeOnce(adapter: TeamsSearchAdapter): () => Promise<void> {
	let done = false;
	return async () => {
		if (done) return;
		done = true;
		try {
			await adapter.close();
		} catch (err) {
			// The profile is released, or the browser is already gone.
			console.error("adapter close failed:", err);
		}
	};
}

/** Rejects with a deadline error after `ms`; the timer never leaks. */
async function withDeadline<T>(work: Promise<T>, ms: number): Promise<T> {
	let timer: ReturnType<typeof setTimeout> | undefined;
	const expiry = new Promise<never>((_, reject) => {
		timer = setTimeout(
			() => reject(new Error(`poll deadline of ${ms}ms elapsed`)),
			ms,
		);
	});
	try {
		return await Promise.race([work, expiry]);
	} finally {
		clearTimeout(timer);
	}
}

/** Signal semantics shared by SIGTERM and SIGINT: close once, then exit 2. */
export function createSignalExit(
	close: () => Promise<void>,
	exit: (code: number) => void,
): () => void {
	let fired = false;
	return () => {
		if (fired) return;
		fired = true;
		void close()
			.catch((err) => console.error("close on signal failed:", err))
			.then(() => exit(EXIT.FAILURE));
	};
}

/** One poll: read state, bounded search, classify, write state, close once. */
export async function runPoll(deps: PollDeps): Promise<number> {
	const close = deps.close ?? closeOnce(deps.adapter);
	try {
		let stateText: string | null = null;
		try {
			stateText = readFileSync(deps.stateFile, "utf8");
		} catch {
			stateText = null; // absent file → bootstrap poll
		}
		let lastRun: Date | null = null;
		if (stateText !== null && stateText.trim() !== "") {
			lastRun = parseState(stateText);
			if (lastRun === null) {
				console.error(`invalid last_run state: ${stateText.trim()}`);
				return EXIT.FAILURE; // present but garbage — fail closed
			}
		}

		const pollStart = new Date(); // spec: no-news advances to poll start T
		const results = await withDeadline(deps.adapter.search(), deps.deadlineMs);
		const now = new Date(); // relative stamps ("ayer 09:15") resolve vs. now
		const stamps: Date[] = [];
		for (const { rawTimestamp } of results) {
			const stamp = normalizeTimestamp(rawTimestamp, now);
			if (stamp === null) {
				console.error(`unrecognized timestamp format: ${JSON.stringify(rawTimestamp)}`);
				return EXIT.FAILURE; // DOM-change signal — fail closed
			}
			stamps.push(stamp);
		}

		const outcome = decide(lastRun, stamps);
		if (outcome === "no-news") {
			mkdirSync(dirname(deps.stateFile), { recursive: true });
			writeFileSync(deps.stateFile, formatState(pollStart));
		}
		return exitFor(outcome);
	} catch (err) {
		console.error(err);
		return EXIT.FAILURE;
	} finally {
		await close();
	}
}

async function main(): Promise<number> {
	const home = process.env.HOME ?? ".";
	const cacheDir = process.env.TEAMS_PLAYWRIGHT_CACHE ?? join(home, ".cache", "ms-playwright");
	const executablePath = resolveChromiumExecutablePath(cacheDir);
	if (executablePath === null) {
		console.error(`no usable chromium-<rev> build under ${cacheDir}; aborting`);
		return EXIT.FAILURE;
	}

	const adapter = new TeamsSearchPage({
		executablePath,
		userDataDir:
			process.env.TEAMS_PROFILE_DIR ??
			join(home, ".local", "share", "opencode", "playwright-teams-profile"),
	});
	const close = closeOnce(adapter); // shared by signal handlers and runPoll
	const signalExit = createSignalExit(close, (code) => process.exit(code));
	process.on("SIGTERM", signalExit);
	process.on("SIGINT", signalExit);

	const deadlineEnv = Number(process.env.TEAMS_POLL_DEADLINE_MS);
	const deadlineMs =
		Number.isFinite(deadlineEnv) && deadlineEnv > 0 ? deadlineEnv : DEFAULT_DEADLINE_MS;
	return runPoll({
		adapter,
		close,
		stateFile:
			process.env.TEAMS_STATE_FILE ?? join(home, ".local", "state", "teams-to-tasks", "last_run"),
		deadlineMs,
	});
}

if (import.meta.main) {
	process.exit(await main());
}
