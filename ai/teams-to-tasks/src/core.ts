/**
 * Pure decision/state core for the teams-to-tasks poller.
 *
 * No browser, filesystem, or clock access: every function is deterministic
 * given its inputs (`now` is injected), so `bun test` covers all rules
 * without Chromium. Contracts live in
 * openspec/changes/teams-to-tasks-poller (spec + design).
 */

/** Outcome of comparing search-result timestamps against persisted state. */
export type Outcome = "news" | "no-news";

/** Process exit codes (spec: Deterministic exit codes). */
export const EXIT = { NEWS: 0, NO_NEWS: 1, FAILURE: 2 } as const;

/** Exit code for a classified (non-failure) outcome. */
export function exitFor(outcome: Outcome): 0 | 1 {
	return outcome === "news" ? EXIT.NEWS : EXIT.NO_NEWS;
}

/** Seam implemented by src/teams-page.ts; faked in tests. */
export interface TeamsSearchAdapter {
	search(): Promise<{ rawTimestamp: string }[]>;
	close(): Promise<void>; // idempotent
}

/**
 * Parse the persisted last_run state.
 *
 * Accepts the ISO-8601 UTC form written by `formatState` and the legacy
 * local `YYYY-MM-DD HH:MM` written by the previous cron.sh. Returns null
 * when absent or unparseable; the caller distinguishes bootstrap (file
 * missing) from failure (present but garbage).
 */
export function parseState(text: string | null): Date | null {
	if (text === null) return null;
	const trimmed = text.trim();
	if (trimmed === "") return null;

	const legacy = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(trimmed);
	if (legacy) {
		// Legacy state was written in local time by cron.sh's `date`.
		return new Date(
			Number(legacy[1]),
			Number(legacy[2]) - 1,
			Number(legacy[3]),
			Number(legacy[4]),
			Number(legacy[5]),
		);
	}

	if (trimmed.includes("T")) {
		const iso = new Date(trimmed);
		return Number.isNaN(iso.getTime()) ? null : iso;
	}
	return null;
}

/** Serialize a timestamp for the state file: ISO-8601 UTC. */
export function formatState(date: Date): string {
	return date.toISOString();
}

/**
 * Normalize a raw Teams search-result timestamp to a Date.
 *
 * Teams renders timestamps in the user's locale; the forms observed in the
 * sweep search (`que`, Date=Today) are:
 *  - `HH:MM`       — today at that local time
 *  - `ayer HH:MM`  — Spanish "yesterday" at that local time
 *  - full ISO-8601 — passthrough (spec examples, tests)
 * Relative forms resolve against the injected `now`. Returns null when the
 * form is unrecognized: the poller treats that as a DOM change and fails.
 */
export function normalizeTimestamp(raw: string, now: Date): Date | null {
	const trimmed = raw.trim();
	if (trimmed === "") return null;

	if (trimmed.includes("T")) {
		const iso = new Date(trimmed);
		return Number.isNaN(iso.getTime()) ? null : iso;
	}

	const timeOnly = /^(\d{1,2}):(\d{2})$/.exec(trimmed);
	if (timeOnly) {
		return atLocalTime(now, Number(timeOnly[1]), Number(timeOnly[2]));
	}

	const yesterday = /^ayer (\d{1,2}):(\d{2})$/i.exec(trimmed);
	if (yesterday) {
		const d = atLocalTime(now, Number(yesterday[1]), Number(yesterday[2]));
		d.setDate(d.getDate() - 1);
		return d;
	}

	return null;
}

function atLocalTime(day: Date, hours: number, minutes: number): Date {
	return new Date(
		day.getFullYear(),
		day.getMonth(),
		day.getDate(),
		hours,
		minutes,
	);
}

/**
 * Classify a completed search against the persisted watermark.
 *
 * Fingerprint-agnostic by design: news is purely timestamp comparison;
 * dedupe stays with the agent. Empty results are no-news (nothing to do),
 * even on bootstrap — news requires at least one message.
 */
export function decide(lastRun: Date | null, stamps: Date[]): Outcome {
	if (stamps.length === 0) return "no-news";
	if (lastRun === null) return "news";
	const newest = Math.max(...stamps.map((s) => s.getTime()));
	return newest > lastRun.getTime() ? "news" : "no-news";
}
