import { describe, expect, test } from "bun:test";
import {
	DEFAULT_DATA_SOURCE_ID,
	NOTION_API_VERSION,
	NotionApiError,
	NotionWriter,
	type NotionAction,
} from "../src/notion-writer.ts";

function createMockFetch(
	handler: (url: string, init: RequestInit) => Promise<Response> | Response,
) {
	return (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
		const url = typeof input === "string" ? input : input.toString();
		return Promise.resolve(handler(url, init || {}));
	};
}

function jsonResponse(data: any, status = 200, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: { "Content-Type": "application/json", ...headers },
	});
}

describe("NotionWriter", () => {
	test("requires Notion token", () => {
		expect(() => new NotionWriter({ token: "" })).toThrow("token is required");
	});

	test("createTask sends correct 2025-09-03 schema and headers", async () => {
		let capturedUrl = "";
		let capturedInit: RequestInit | undefined;

		const mockFetch = createMockFetch((url, init) => {
			capturedUrl = url;
			capturedInit = init;
			return jsonResponse({ id: "created-page-123" });
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			dataSourceId: "custom-ds-id",
			fetchFn: mockFetch,
		});

		const result = await writer.createTask({
			action: "create",
			title: "Nueva tarea de prueba",
			notes: "teams:chat:bruno:2026-09-14-12:00\nDetalles de la tarea",
			priority: "🔥 Alta",
			project: "sis",
			due: "2026-09-15",
		});

		expect(result).toEqual({
			action: "create",
			id: "created-page-123",
			success: true,
		});

		expect(capturedUrl).toBe("https://api.notion.com/v1/pages");
		expect(capturedInit?.method).toBe("POST");

		const headers = capturedInit?.headers as Record<string, string>;
		expect(headers["Authorization"]).toBe("Bearer secret_test_token");
		expect(headers["Notion-Version"]).toBe(NOTION_API_VERSION);
		expect(headers["Content-Type"]).toBe("application/json");

		const body = JSON.parse(capturedInit?.body as string);
		expect(body.parent).toEqual({
			type: "data_source_id",
			data_source_id: "custom-ds-id",
		});
		expect(body.properties.Título.title[0].text.content).toBe("Nueva tarea de prueba");
		expect(body.properties.Estado.select.name).toBe("📥 Inbox");
		expect(body.properties.Prioridad.select.name).toBe("🔥 Alta");
		expect(body.properties.Proyecto.select.name).toBe("sis");
		expect(body.properties.Notas.rich_text[0].text.content).toContain("teams:chat:bruno");
		expect(body.properties.Vence.date.start).toBe("2026-09-15");
	});

	test("enrichTask updates Notas directly when notes are provided", async () => {
		let capturedUrl = "";
		let capturedInit: RequestInit | undefined;

		const mockFetch = createMockFetch((url, init) => {
			capturedUrl = url;
			capturedInit = init;
			return jsonResponse({ id: "page-abc" });
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			fetchFn: mockFetch,
		});

		const result = await writer.enrichTask({
			action: "enrich",
			page_id: "page-abc",
			notes: "Notas actualizadas completas",
		});

		expect(result).toEqual({
			action: "enrich",
			id: "page-abc",
			success: true,
		});

		expect(capturedUrl).toBe("https://api.notion.com/v1/pages/page-abc");
		expect(capturedInit?.method).toBe("PATCH");

		const body = JSON.parse(capturedInit?.body as string);
		expect(body.properties.Notas.rich_text[0].text.content).toBe("Notas actualizadas completas");
	});

	test("enrichTask appends to existing Notas when notes_update is provided", async () => {
		const calls: Array<{ url: string; method: string; body?: any }> = [];

		const mockFetch = createMockFetch((url, init) => {
			const method = init.method || "GET";
			const body = init.body ? JSON.parse(init.body as string) : undefined;
			calls.push({ url, method, body });

			if (method === "GET") {
				return jsonResponse({
					id: "page-abc",
					properties: {
						Notas: {
							rich_text: [{ text: { content: "Notas previas" } }],
						},
					},
				});
			}
			return jsonResponse({ id: "page-abc" });
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			fetchFn: mockFetch,
		});

		const result = await writer.enrichTask({
			action: "enrich",
			page_id: "page-abc",
			notes_update: "Update 14/09: Contexto nuevo",
		});

		expect(result.success).toBe(true);
		expect(calls).toHaveLength(2);
		expect(calls[0].method).toBe("GET");
		expect(calls[0].url).toBe("https://api.notion.com/v1/pages/page-abc");

		expect(calls[1].method).toBe("PATCH");
		expect(calls[1].body.properties.Notas.rich_text[0].text.content).toBe(
			"Notas previas\n\nUpdate 14/09: Contexto nuevo",
		);
	});

	test("resolveTask marks status as ✅ Hecho", async () => {
		let capturedInit: RequestInit | undefined;

		const mockFetch = createMockFetch((url, init) => {
			capturedInit = init;
			return jsonResponse({ id: "page-xyz" });
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			fetchFn: mockFetch,
		});

		const result = await writer.resolveTask({
			action: "resolve",
			page_id: "page-xyz",
		});

		expect(result).toEqual({
			action: "resolve",
			id: "page-xyz",
			success: true,
		});

		const body = JSON.parse(capturedInit?.body as string);
		expect(body.properties.Estado.select.name).toBe("✅ Hecho");
	});

	test("appendDigest appends structured digest blocks", async () => {
		let capturedUrl = "";
		let capturedBody: any;

		const mockFetch = createMockFetch((url, init) => {
			capturedUrl = url;
			capturedBody = JSON.parse(init.body as string);
			return jsonResponse({ results: [{ id: "block-1" }] });
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			fetchFn: mockFetch,
		});

		const result = await writer.appendDigest({
			action: "digest",
			parent_id: "digest-parent-page",
			date_heading: "📅 14 de Septiembre de 2026",
			sections: [
				{
					heading: "Temas tratados",
					bullets: ["Portal de Entidades: estado", "RPA Asociaciones"],
				},
			],
		});

		expect(result.success).toBe(true);
		expect(capturedUrl).toBe("https://api.notion.com/v1/blocks/digest-parent-page/children");
		expect(capturedBody.children).toHaveLength(4); // heading_2 + heading_3 + 2 bullets
		expect(capturedBody.children[0].type).toBe("heading_2");
		expect(capturedBody.children[0].heading_2.rich_text[0].text.content).toBe(
			"📅 14 de Septiembre de 2026",
		);
		expect(capturedBody.children[1].type).toBe("heading_3");
		expect(capturedBody.children[1].heading_3.rich_text[0].text.content).toBe("Temas tratados");
		expect(capturedBody.children[2].type).toBe("bulleted_list_item");
		expect(capturedBody.children[3].type).toBe("bulleted_list_item");
	});

	test("retries on HTTP 429 rate limits", async () => {
		let attempts = 0;

		const mockFetch = createMockFetch(() => {
			attempts++;
			if (attempts === 1) {
				return jsonResponse({ message: "rate limited" }, 429, { "retry-after": "0" });
			}
			return jsonResponse({ id: "recovered-page" });
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			fetchFn: mockFetch,
			retryDelayMs: 1,
		});

		const result = await writer.createTask({
			action: "create",
			title: "Tarea con rate-limit",
			notes: "Notas",
		});

		expect(attempts).toBe(2);
		expect(result.success).toBe(true);
		expect(result.id).toBe("recovered-page");
	});

	test("fails closed on HTTP 400 without retrying", async () => {
		let attempts = 0;

		const mockFetch = createMockFetch(() => {
			attempts++;
			return jsonResponse({ message: "validation_error", code: "validation_error" }, 400);
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			fetchFn: mockFetch,
			maxRetries: 3,
		});

		await expect(
			writer.createTask({
				action: "create",
				title: "Bad task",
				notes: "",
			}),
		).rejects.toThrow(NotionApiError);

		expect(attempts).toBe(1); // No retries on 400
	});

	test("executeActions executes sequentially and halts on error", async () => {
		let callCount = 0;

		const mockFetch = createMockFetch((url, init) => {
			callCount++;
			if (callCount === 2) {
				return jsonResponse({ message: "Server error" }, 500);
			}
			return jsonResponse({ id: `page-${callCount}` });
		});

		const writer = new NotionWriter({
			token: "secret_test_token",
			fetchFn: mockFetch,
			maxRetries: 0,
		});

		const actions: NotionAction[] = [
			{ action: "create", title: "Task 1", notes: "Notes 1" },
			{ action: "create", title: "Task 2", notes: "Notes 2" },
			{ action: "create", title: "Task 3", notes: "Notes 3" },
		];

		const results = await writer.executeActions(actions);
		expect(results).toHaveLength(2); // First succeeded, second failed, third never ran
		expect(results[0].success).toBe(true);
		expect(results[1].success).toBe(false);
		expect(results[1].error).toContain("Server error");
	});
});
