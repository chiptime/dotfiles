import { describe, expect, test } from "bun:test";
import { validateCompletion } from "../src/completion.ts";
import { encode, fixture, window } from "./completion-fixture.ts";

// R7 boundary guard. src/completion.ts (READ-ONLY dependency) fixes the
// ingestion action allowlist to create/enrich/resolve/digest. The resolver's
// `triage` action must NEVER be accepted through the ingestion path — Slice B
// adds it to notion-writer.ts only (design D5: completion.ts allowlist
// unchanged). This suite must stay green through Slice B.

function sweepWithActions(actions: unknown[]): string {
	const events = fixture();
	const lookup = structuredClone(events[1]);
	lookup.part.tool = lookup.part.callID = "notion_API-query-data-source";
	events.splice(5, 0, lookup); // sweep mode requires dedupe-lookup evidence
	const text = events.at(-2)!.part;
	text.text = JSON.stringify({ ...JSON.parse(text.text), actions });
	return encode(events);
}

describe("ingestion allowlist boundary (R7)", () => {
	test("control: a still-valid ingestion action passes", () => {
		expect(
			validateCompletion(
				sweepWithActions([{ action: "create", title: "T", notes: "n" }]),
				"",
				window,
			),
		).toContain("1 created, 0 updated");
	});

	test("an ingestion payload emitting triage is rejected by completion.ts", () => {
		expect(() =>
			validateCompletion(
				sweepWithActions([{ action: "triage", page_id: "p1", triage_status: "✋ Manual" }]),
				"",
				window,
			),
		).toThrow("unrecognized action type: triage");
	});

	test("triage hidden among valid actions is still rejected", () => {
		expect(() =>
			validateCompletion(
				sweepWithActions([
					{ action: "create", title: "T", notes: "n" },
					{ action: "triage", page_id: "p1", resolution_draft: "x".repeat(50) },
				]),
				"",
				window,
			),
		).toThrow("unrecognized action type: triage");
	});
});
