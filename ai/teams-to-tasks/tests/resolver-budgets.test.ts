import { describe, expect, test } from "bun:test";
import {
	DEFAULT_BUDGETS,
	checkExhaustion,
	type SessionUsage,
	type TaskUsage,
} from "../src/resolver/budgets.ts";

// Boundaries are derived from DEFAULT_BUDGETS — never restated here (R9).
const B = DEFAULT_BUDGETS;

/** Usage sitting exactly at every ceiling — a ceiling is a max, not a trip. */
const atCeiling: TaskUsage = {
	wallMs: B.task.maxWallMs,
	tokens: B.task.maxTokens,
	costUsd: B.task.maxCostUsd,
	reads: B.task.maxReads,
	evidenceBytes: B.task.maxEvidenceBytes,
};
const sessionOk: SessionUsage = {
	wallMs: B.session.maxWallMs,
	costUsd: B.session.maxCostUsd,
};

describe("DEFAULT_BUDGETS shape", () => {
	test("exposes per-task and per-session ceilings", () => {
		expect(Object.keys(B.task).sort()).toEqual([
			"maxCostUsd",
			"maxEvidenceBytes",
			"maxReads",
			"maxTokens",
			"maxWallMs",
		]);
		expect(Object.keys(B.session).sort()).toEqual(["maxCostUsd", "maxWallMs"]);
	});
});

describe("within budget passes (R9)", () => {
	test("usage at every ceiling is still WITHIN_BUDGET", () => {
		const check = checkExhaustion(atCeiling, sessionOk);
		expect(check.verdict).toBe("WITHIN_BUDGET");
		expect(check.reasons).toEqual([]);
		expect(check.partialEvidenceRetained).toBe(false);
	});

	test("zero usage is WITHIN_BUDGET", () => {
		const check = checkExhaustion(
			{ wallMs: 0, tokens: 0, costUsd: 0, reads: 0, evidenceBytes: 0 },
			{ wallMs: 0, costUsd: 0 },
		);
		expect(check.verdict).toBe("WITHIN_BUDGET");
	});
});

describe("exhaustion ⇒ MANUAL_REQUIRED with partial evidence retained (R9, R3)", () => {
	test("task token ceiling exceeded", () => {
		const check = checkExhaustion(
			{ ...atCeiling, tokens: B.task.maxTokens + 1, reads: 3, evidenceBytes: 128 },
			sessionOk,
		);
		expect(check.verdict).toBe("MANUAL_REQUIRED");
		expect(check.reasons.join("\n")).toContain("task.tokens");
		expect(check.partialEvidenceRetained).toBe(true);
	});

	test("task cost ceiling exceeded", () => {
		const check = checkExhaustion(
			{ ...atCeiling, costUsd: B.task.maxCostUsd + 0.01, reads: 1 },
			sessionOk,
		);
		expect(check.verdict).toBe("MANUAL_REQUIRED");
		expect(check.reasons.join("\n")).toContain("task.costUsd");
	});

	test("task wall clock exceeded with zero work retains nothing partial", () => {
		const check = checkExhaustion(
			{ wallMs: B.task.maxWallMs + 1, tokens: 0, costUsd: 0, reads: 0, evidenceBytes: 0 },
			sessionOk,
		);
		expect(check.verdict).toBe("MANUAL_REQUIRED");
		expect(check.reasons.join("\n")).toContain("task.wallMs");
		expect(check.partialEvidenceRetained).toBe(false);
	});

	test("reads and evidence ceilings are enforced", () => {
		const reads = checkExhaustion(
			{ ...atCeiling, reads: B.task.maxReads + 1, evidenceBytes: 1 },
			sessionOk,
		);
		expect(reads.verdict).toBe("MANUAL_REQUIRED");
		expect(reads.reasons.join("\n")).toContain("task.reads");
		const evidence = checkExhaustion(
			{ ...atCeiling, evidenceBytes: B.task.maxEvidenceBytes + 1, reads: 1 },
			sessionOk,
		);
		expect(evidence.verdict).toBe("MANUAL_REQUIRED");
		expect(evidence.reasons.join("\n")).toContain("task.evidenceBytes");
	});

	test("session ceilings exhausted defer to a later session as MANUAL_REQUIRED", () => {
		const wall = checkExhaustion(
			atCeiling,
			{ ...sessionOk, wallMs: B.session.maxWallMs + 1 },
		);
		expect(wall.verdict).toBe("MANUAL_REQUIRED");
		expect(wall.reasons.join("\n")).toContain("session.wallMs");
		const cost = checkExhaustion(atCeiling, {
			...sessionOk,
			costUsd: B.session.maxCostUsd + 0.01,
		});
		expect(cost.verdict).toBe("MANUAL_REQUIRED");
		expect(cost.reasons.join("\n")).toContain("session.costUsd");
	});

	test("every blown ceiling is reported, not just the first", () => {
		const check = checkExhaustion(
			{
				...atCeiling,
				tokens: B.task.maxTokens + 1,
				reads: B.task.maxReads + 1,
			},
			{ ...sessionOk, costUsd: B.session.maxCostUsd + 1 },
		);
		expect(check.reasons).toHaveLength(3);
	});

	test("custom budgets override the defaults (single source stays call-site)", () => {
		const tight = checkExhaustion(
			{ wallMs: 0, tokens: 0, costUsd: 0, reads: 0, evidenceBytes: 0 },
			sessionOk,
			{
				task: { ...B.task, maxReads: 0 },
				session: B.session,
			},
		);
		expect(tight.verdict).toBe("WITHIN_BUDGET");
	});
});
