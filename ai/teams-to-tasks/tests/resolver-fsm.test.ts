import { describe, expect, test } from "bun:test";
import {
	IllegalTransitionError,
	TERMINAL_STATES,
	transition,
	type RunEvent,
	type RunState,
} from "../src/resolver/fsm.ts";

describe("legal lifecycle walks", () => {
	test("full happy path Detected → Executed", () => {
		const path: Array<[RunEvent, RunState]> = [
			["analyze", "Analyzing"],
			["draft", "Pending approval"],
			["approve", "Approved"],
			["execute", "Executing"],
			["complete", "Executed"],
		];
		let state: RunState = "Detected";
		for (const [event, expected] of path) {
			state = transition(state, event);
			expect(state).toBe(expected);
		}
	});

	test("Analyzing → Manual required and Failed", () => {
		expect(transition("Analyzing", "manual")).toBe("Manual required");
		expect(transition("Analyzing", "fail")).toBe("Failed");
	});

	test("Pending approval → Rejected ends the run", () => {
		expect(transition("Pending approval", "reject")).toBe("Rejected");
	});

	test("Executing → Failed (mirror failure never implies success)", () => {
		expect(transition("Executing", "fail")).toBe("Failed");
	});
});

describe("drift re-enters Analyzing (R6)", () => {
	test("drift from Pending approval voids the pending draft", () => {
		expect(transition("Pending approval", "drift")).toBe("Analyzing");
	});

	test("drift from Approved voids the approval", () => {
		expect(transition("Approved", "drift")).toBe("Analyzing");
	});

	test("drift before analysis is refused (fail closed)", () => {
		expect(() => transition("Detected", "drift")).toThrow(IllegalTransitionError);
	});
});

describe("illegal transitions fail closed (R6)", () => {
	test("Detected → Executing directly is refused", () => {
		expect(() => transition("Detected", "execute")).toThrow(IllegalTransitionError);
	});

	test("skipping the human gate is refused (Detected → approve)", () => {
		expect(() => transition("Detected", "approve")).toThrow();
		expect(() => transition("Analyzing", "execute")).toThrow();
		expect(() => transition("Pending approval", "complete")).toThrow();
	});

	test("terminal states accept no transition", () => {
		for (const state of TERMINAL_STATES) {
			const events: RunEvent[] = [
				"analyze",
				"draft",
				"approve",
				"reject",
				"execute",
				"complete",
				"drift",
			];
			for (const event of events)
				expect(() => transition(state, event), `${state} + ${event}`).toThrow(
					IllegalTransitionError,
				);
		}
	});

	test("error names the refused pair", () => {
		try {
			transition("Detected", "execute");
			expect.unreachable();
		} catch (err) {
			expect(err).toBeInstanceOf(IllegalTransitionError);
			const e = err as IllegalTransitionError;
			expect(e.from).toBe("Detected");
			expect(e.event).toBe("execute");
			expect(e.message).toContain("Detected");
			expect(e.message).toContain("execute");
		}
	});
});
