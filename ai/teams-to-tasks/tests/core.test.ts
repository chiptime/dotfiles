import { describe, expect, test } from "bun:test";
import {
	EXIT,
	decide,
	exitFor,
	formatState,
	normalizeTimestamp,
	parseState,
} from "../src/core.ts";

const at = (iso: string) => new Date(iso);
const WATERMARK = "2026-09-09T18:30:00.000Z";

describe("decide", () => {
	test("newer message is news", () => {
		expect(decide(at(WATERMARK), [at("2026-09-10T08:00:00.000Z")])).toBe("news");
	});

	test("equal watermark is no-news (strictly newer required)", () => {
		expect(decide(at(WATERMARK), [at(WATERMARK)])).toBe("no-news");
	});

	test("older messages are no-news", () => {
		expect(
			decide(at(WATERMARK), [
				at("2026-09-09T18:29:59.000Z"),
				at("2026-09-09T17:00:00.000Z"),
			]),
		).toBe("no-news");
	});

	test("a single newer stamp in the batch wins", () => {
		expect(
			decide(at(WATERMARK), [at(WATERMARK), at("2026-09-09T18:30:00.001Z")]),
		).toBe("news");
	});

	test("missing state bootstraps as news when messages exist", () => {
		expect(decide(null, [at("2026-09-10T08:00:00.000Z")])).toBe("news");
	});

	test("no messages is no-news even on bootstrap", () => {
		expect(decide(null, [])).toBe("no-news");
	});
});

describe("exit codes", () => {
	test("news maps to 0, no-news to 1, failure constant is 2", () => {
		expect(exitFor("news")).toBe(EXIT.NEWS);
		expect(exitFor("no-news")).toBe(EXIT.NO_NEWS);
		expect(EXIT.FAILURE).toBe(2);
	});
});

describe("parseState / formatState", () => {
	test("round-trips the ISO-8601 UTC form this module writes", () => {
		const d = at("2026-09-10T08:00:00.000Z");
		expect(parseState(formatState(d))?.getTime()).toBe(d.getTime());
	});

	test("accepts the offset-less ISO form used in spec examples", () => {
		expect(parseState("2026-09-09T18:30Z")?.toISOString()).toBe(WATERMARK);
	});

	test("parses legacy local `YYYY-MM-DD HH:MM` state as local time", () => {
		const parsed = parseState("2026-09-09 18:30");
		expect([
			parsed?.getFullYear(),
			parsed?.getMonth(),
			parsed?.getDate(),
			parsed?.getHours(),
			parsed?.getMinutes(),
		]).toEqual([2026, 8, 9, 18, 30]);
	});

	test("missing or blank state is null (bootstrap)", () => {
		expect(parseState(null)).toBeNull();
		expect(parseState("   ")).toBeNull();
	});

	test("unparseable state is null (poller must fail closed)", () => {
		expect(parseState("not a date")).toBeNull();
		expect(parseState("2026-09-09T99:99Z")).toBeNull();
	});
});

describe("normalizeTimestamp", () => {
	// Local 2026-09-10 12:00 — relative forms resolve against this `now`.
	const now = new Date(2026, 8, 10, 12, 0);

	test("ISO passthrough ignores the injected now", () => {
		expect(normalizeTimestamp("2026-09-10T08:00:00.000Z", now)?.toISOString()).toBe(
			"2026-09-10T08:00:00.000Z",
		);
	});

	test("time-only stamp resolves to today at that local time", () => {
		const t = normalizeTimestamp("10:42", now);
		expect([t?.getHours(), t?.getMinutes(), t?.getDate()]).toEqual([10, 42, 10]);
	});

	test("`ayer HH:MM` resolves to yesterday at that local time", () => {
		const t = normalizeTimestamp("ayer 09:15", now);
		expect([t?.getDate(), t?.getHours(), t?.getMinutes()]).toEqual([9, 9, 15]);
	});

	test("unrecognized form is null (DOM change signal)", () => {
		expect(normalizeTimestamp("martes", now)).toBeNull();
		expect(normalizeTimestamp("", now)).toBeNull();
		expect(normalizeTimestamp("2026-09-10T08:00:00.000Z", now)).not.toBeNull();
	});
});
