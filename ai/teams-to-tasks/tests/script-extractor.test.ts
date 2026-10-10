import { describe, expect, test } from "bun:test";
import { isActionableText } from "../src/script-extractor.ts";
import { compareRuns } from "../src/compare-extractors.ts";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("script-extractor — isActionableText", () => {
	test("identifies actionable request phrasing in Spanish and English", () => {
		expect(isActionableText("¿Puedes revisar el PR que subí ayer?")).toBe(true);
		expect(isActionableText("Es urgente subir el fix a producción")).toBe(true);
		expect(isActionableText("Recuerda que tenemos deadline hoy")).toBe(true);
		expect(isActionableText("Hay un bloqueo en el pipeline")).toBe(true);
		expect(isActionableText("¿Cómo estás?")).toBe(true); // question
		expect(isActionableText("Por favor haz el traspaso")).toBe(true);
	});

	test("ignores non-actionable passive chatter", () => {
		expect(isActionableText("Hola a todos")).toBe(false);
		expect(isActionableText("Buenos días")).toBe(false);
		expect(isActionableText("gracias")).toBe(false);
	});
});

describe("compare-extractors — compareRuns", () => {
	test("computes overlap and discrepancies between script and agent actions", () => {
		const tmp = mkdtempSync(join(tmpdir(), "compare-test-"));
		try {
			const scriptActions = [
				{
					action: "create",
					title: "Task 1",
					fingerprint: "teams:chat1:user:1",
				},
				{
					action: "create",
					title: "Task 2 (script only)",
					fingerprint: "teams:chat1:user:2",
				},
			];
			const agentActions = [
				{
					action: "create",
					title: "Task 1",
					fingerprint: "teams:chat1:user:1",
				},
				{
					action: "create",
					title: "Task 3 (agent only)",
					fingerprint: "teams:chat1:user:3",
				},
			];

			writeFileSync(join(tmp, "actions.script.json"), JSON.stringify(scriptActions));
			writeFileSync(join(tmp, "actions.json"), JSON.stringify(agentActions));
			writeFileSync(join(tmp, "script-messages.json"), JSON.stringify([{ id: 1 }, { id: 2 }]));
			writeFileSync(join(tmp, "script-summary.txt"), "script_status=complete");
			writeFileSync(join(tmp, "summary.txt"), "agent_status=complete");

			const result = compareRuns(tmp);
			expect(result.scriptActionsCount).toBe(2);
			expect(result.agentActionsCount).toBe(2);
			expect(result.overlapCount).toBe(1);
			expect(result.scriptOnly).toEqual(["teams:chat1:user:2"]);
			expect(result.agentOnly).toEqual(["teams:chat1:user:3"]);
			expect(result.scriptSummary).toBe("script_status=complete");
			expect(result.agentSummary).toBe("agent_status=complete");
		} finally {
			rmSync(tmp, { recursive: true, force: true });
		}
	});
});
