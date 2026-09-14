/**
 * Deterministic budget ceilings for the task-resolver (spec R9, R3).
 *
 * DEFAULT_BUDGETS is the ONLY place a budget literal lives — every consumer
 * (tests included) derives its numbers from it. checkExhaustion turns usage
 * into a verdict: any ceiling exceeded ⇒ MANUAL_REQUIRED, with the partial
 * evidence gathered so far retained for the human.
 */

export interface TaskBudget {
	/** Wall-clock ceiling per task analysis, milliseconds. */
	maxWallMs: number;
	maxTokens: number;
	maxCostUsd: number;
	maxReads: number;
	maxEvidenceBytes: number;
}

export interface SessionBudget {
	maxWallMs: number;
	maxCostUsd: number;
}

export interface Budgets {
	task: TaskBudget;
	session: SessionBudget;
}

export const DEFAULT_BUDGETS: Budgets = {
	task: {
		maxWallMs: 6 * 60_000,
		maxTokens: 80_000,
		maxCostUsd: 0.5,
		maxReads: 12,
		maxEvidenceBytes: 400 * 1024,
	},
	session: {
		maxWallMs: 30 * 60_000,
		maxCostUsd: 5,
	},
};

export interface TaskUsage {
	wallMs: number;
	tokens: number;
	costUsd: number;
	reads: number;
	evidenceBytes: number;
}

export interface SessionUsage {
	wallMs: number;
	costUsd: number;
}

export type BudgetVerdict = "WITHIN_BUDGET" | "MANUAL_REQUIRED";

export interface BudgetCheck {
	verdict: BudgetVerdict;
	/** Human-readable reason per blown ceiling (label + used > max). */
	reasons: string[];
	/** True when exhaustion hit mid-analysis, after some evidence existed. */
	partialEvidenceRetained: boolean;
}

/**
 * Compare usage against ceilings. A ceiling is a maximum: usage AT the
 * ceiling passes; strictly above it exhausts. Exhaustion never aborts
 * silently — the verdict routes the run to Manual required.
 */
export function checkExhaustion(
	task: TaskUsage,
	session: SessionUsage,
	budgets: Budgets = DEFAULT_BUDGETS,
): BudgetCheck {
	const limits: Array<[string, number, number]> = [
		["task.wallMs", task.wallMs, budgets.task.maxWallMs],
		["task.tokens", task.tokens, budgets.task.maxTokens],
		["task.costUsd", task.costUsd, budgets.task.maxCostUsd],
		["task.reads", task.reads, budgets.task.maxReads],
		["task.evidenceBytes", task.evidenceBytes, budgets.task.maxEvidenceBytes],
		["session.wallMs", session.wallMs, budgets.session.maxWallMs],
		["session.costUsd", session.costUsd, budgets.session.maxCostUsd],
	];
	const reasons = limits
		.filter(([, used, max]) => used > max)
		.map(([label, used, max]) => `${label} ${used} > ${max}`);
	const exhausted = reasons.length > 0;
	const hasPartialWork =
		task.evidenceBytes > 0 || task.reads > 0 || task.tokens > 0;
	return {
		verdict: exhausted ? "MANUAL_REQUIRED" : "WITHIN_BUDGET",
		reasons,
		partialEvidenceRetained: exhausted && hasPartialWork,
	};
}
