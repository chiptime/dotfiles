/**
 * Pure run-state machine for the task-resolver (spec R6).
 *
 * Every legal transition is listed in TRANSITIONS; anything unlisted throws
 * IllegalTransitionError (fail closed). Drift = the Notion page revision
 * changed, voiding any approval and re-entering analysis with a new hash.
 */

export type RunState =
	| "Detected"
	| "Analyzing"
	| "Pending approval"
	| "Approved"
	| "Executing"
	| "Executed"
	| "Manual required"
	| "Failed"
	| "Rejected";

export type RunEvent =
	| "analyze" // detector handed the item to the evaluator
	| "draft" // analysis produced a draft awaiting approval
	| "manual" // ambiguity / missing access / unsafe / budget exhausted
	| "fail" // analysis or execution failed
	| "approve" // human approved the exact hash (session-bound)
	| "reject" // human rejected the draft
	| "execute" // executor began dispatching approved actions
	| "complete" // all actions receipted
	| "drift"; // source revision changed

/** States with no outgoing transitions; a new revision starts a new run. */
export const TERMINAL_STATES: readonly RunState[] = [
	"Executed",
	"Manual required",
	"Failed",
	"Rejected",
];

/** The transition table — the single source of legal run evolution. */
export const TRANSITIONS: Record<RunState, Partial<Record<RunEvent, RunState>>> = {
	Detected: { analyze: "Analyzing" },
	Analyzing: { draft: "Pending approval", manual: "Manual required", fail: "Failed" },
	"Pending approval": { approve: "Approved", reject: "Rejected", drift: "Analyzing" },
	Approved: { execute: "Executing", drift: "Analyzing" },
	Executing: { complete: "Executed", fail: "Failed" },
	Executed: {},
	"Manual required": {},
	Failed: {},
	Rejected: {},
};

/** Thrown on any unlisted (from, event) pair — the caller must fail closed. */
export class IllegalTransitionError extends Error {
	constructor(
		public readonly from: RunState,
		public readonly event: RunEvent,
	) {
		super(`Illegal run transition: ${from} + ${event}`);
		this.name = "IllegalTransitionError";
	}
}

/** Advance the run state, or throw IllegalTransitionError when unlisted. */
export function transition(from: RunState, event: RunEvent): RunState {
	const to = TRANSITIONS[from]?.[event];
	if (!to) throw new IllegalTransitionError(from, event);
	return to;
}

/** Non-throwing probe mirroring transition(). */
export function canTransition(from: RunState, event: RunEvent): boolean {
	return TRANSITIONS[from]?.[event] !== undefined;
}
