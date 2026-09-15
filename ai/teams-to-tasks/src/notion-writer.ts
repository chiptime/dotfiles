/**
 * Deterministic Notion API writer for teams-to-tasks.
 * Executes validated task creations and updates directly in the host
 * with strict schema enforcement, rate-limit retries, and clean error handling.
 */

export const DEFAULT_DATA_SOURCE_ID = "3d675532-da31-802b-b12f-000be53e99ac";
export const NOTION_API_VERSION = "2025-09-03";
export const NOTION_API_BASE = "https://api.notion.com/v1";

export type TaskPriority = "🔥 Alta" | "🟡 Media" | "🟢 Baja";
export type TaskStatus = "📥 Inbox" | "🔨 En curso" | "✅ Hecho" | "🗑️ Descartado";

export interface CreateTaskAction {
	action: "create";
	title: string;
	notes: string;
	priority?: TaskPriority;
	project?: string;
	status?: TaskStatus;
	due?: string; // YYYY-MM-DD
	fingerprint?: string;
}

export interface EnrichTaskAction {
	action: "enrich";
	page_id: string;
	notes?: string; // Full notes replacement
	notes_update?: string; // Content to append to existing notes
}

export interface ResolveTaskAction {
	action: "resolve";
	page_id: string;
}

export interface AppendDigestAction {
	action: "digest";
	parent_id: string;
	date_heading: string;
	sections: Array<{
		heading: string;
		bullets: string[];
	}>;
}

/**
 * Hard cap for the Resolution Draft mirror property (spec R7): Notion
 * rich_text payloads are capped at 2000 characters per text content node;
 * the writer refuses longer drafts before any request leaves.
 */
export const MAX_RESOLUTION_DRAFT_CHARS = 2000;

/**
 * Post-approval triage mirror update (spec R7 — resolver path ONLY).
 * Every field is optional and only provided fields are PATCHed: Estado /
 * Notas (fingerprint carriers, R5) are never touched by this action.
 * Ingestion (completion.ts) keeps its own allowlist and never emits it.
 */
export interface SetTriageAction {
	action: "triage";
	page_id: string;
	/** Triage Status select value (⏳ Pendiente/🤖 Auto/💡 Acción/✋ Manual/✔️ Hecho/❌ Rechazado). */
	triage_status?: string;
	/** Resolution Draft rich_text — capped at MAX_RESOLUTION_DRAFT_CHARS. */
	resolution_draft?: string;
	/** Local Context Ref rich_text (cited path+lines, machine-local pointer). */
	local_context_ref?: string;
	/** Approval State select value (Pendiente/Aprobado/Rechazado). */
	approval_state?: string;
	/** Draft ID rich_text — the h12 short form of the approved hash. */
	draft_id?: string;
}

export type NotionAction =
	| CreateTaskAction
	| EnrichTaskAction
	| ResolveTaskAction
	| AppendDigestAction
	| SetTriageAction;

export interface ActionResult {
	action: NotionAction["action"];
	id: string;
	success: boolean;
	error?: string;
}

export interface WriterOptions {
	token: string;
	dataSourceId?: string;
	baseUrl?: string;
	apiVersion?: string;
	fetchFn?: (
		input: string | URL | Request,
		init?: RequestInit,
	) => Promise<Response>;
	maxRetries?: number;
	retryDelayMs?: number;
}

export class NotionApiError extends Error {
	constructor(
		message: string,
		public status: number,
		public code?: string,
		public raw?: unknown,
	) {
		super(`Notion API Error (${status}${code ? ` ${code}` : ""}): ${message}`);
		this.name = "NotionApiError";
	}
}

export class NotionWriter {
	private token: string;
	private dataSourceId: string;
	private baseUrl: string;
	private apiVersion: string;
	private fetch: (
		input: string | URL | Request,
		init?: RequestInit,
	) => Promise<Response>;
	private maxRetries: number;
	private retryDelayMs: number;

	constructor(options: WriterOptions) {
		if (!options.token) {
			throw new Error("Notion API token is required");
		}
		this.token = options.token;
		this.dataSourceId = options.dataSourceId || DEFAULT_DATA_SOURCE_ID;
		this.baseUrl = options.baseUrl || NOTION_API_BASE;
		this.apiVersion = options.apiVersion || NOTION_API_VERSION;
		this.fetch = options.fetchFn || globalThis.fetch;
		this.maxRetries = options.maxRetries ?? 3;
		this.retryDelayMs = options.retryDelayMs ?? 1000;
	}

	private async request<T = any>(
		path: string,
		method: "GET" | "POST" | "PATCH",
		body?: unknown,
	): Promise<T> {
		const url = `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
		const headers: Record<string, string> = {
			Authorization: `Bearer ${this.token}`,
			"Notion-Version": this.apiVersion,
			"Content-Type": "application/json",
		};

		let attempt = 0;
		while (true) {
			attempt++;
			let res: Response;
			try {
				res = await this.fetch(url, {
					method,
					headers,
					body: body ? JSON.stringify(body) : undefined,
				});
			} catch (netErr: any) {
				if (attempt <= this.maxRetries) {
					await this.sleep(this.retryDelayMs * Math.pow(2, attempt - 1));
					continue;
				}
				throw netErr;
			}

			if (res.ok) {
				return (await res.json()) as T;
			}

			// Parse error body if possible
			let errData: any;
			try {
				errData = await res.json();
			} catch {
				errData = { message: await res.text() };
			}

			// Rate limit (429) or transient server error (500, 502, 503, 504) -> retry
			if (
				(res.status === 429 || res.status >= 500) &&
				attempt <= this.maxRetries
			) {
				const retryAfter = res.headers.get("retry-after");
				const waitMs = retryAfter
					? parseInt(retryAfter, 10) * 1000
					: this.retryDelayMs * Math.pow(2, attempt - 1);
				await this.sleep(waitMs);
				continue;
			}

			throw new NotionApiError(
				errData?.message || `HTTP ${res.status}`,
				res.status,
				errData?.code,
				errData,
			);
		}
	}

	private sleep(ms: number): Promise<void> {
		return new Promise((resolve) => setTimeout(resolve, ms));
	}

	/**
	 * Create a new task page in the target data source.
	 */
	async createTask(action: CreateTaskAction): Promise<ActionResult> {
		const properties: Record<string, any> = {
			Título: {
				title: [{ text: { content: action.title } }],
			},
			Estado: {
				select: { name: action.status || "📥 Inbox" },
			},
			Prioridad: {
				select: { name: action.priority || "🟡 Media" },
			},
			Proyecto: {
				select: { name: action.project || "otro/vida" },
			},
			Notas: {
				rich_text: [{ text: { content: action.notes || "" } }],
			},
		};

		const dueDate = action.due || (action as any).deadline;
		if (dueDate) {
			properties.Vence = {
				date: { start: dueDate },
			};
		}

		const body = {
			parent: {
				type: "data_source_id",
				data_source_id: this.dataSourceId,
			},
			properties,
		};

		const response = await this.request<{ id: string }>("/pages", "POST", body);
		return {
			action: "create",
			id: response.id,
			success: true,
		};
	}

	/**
	 * Enrich an existing task page.
	 */
	async enrichTask(action: EnrichTaskAction): Promise<ActionResult> {
		let newNotes = action.notes;

		if (!newNotes && action.notes_update) {
			// Fetch existing page to retrieve current Notas
			const page = await this.request<{ properties: any }>(
				`/pages/${action.page_id}`,
				"GET",
			);
			const currentRichText = page.properties?.Notas?.rich_text || [];
			const currentText = currentRichText
				.map((t: any) => t.plain_text || t.text?.content || "")
				.join("");
			newNotes = currentText
				? `${currentText}\n\n${action.notes_update}`
				: action.notes_update;
		}

		const body = {
			properties: {
				Notas: {
					rich_text: [{ text: { content: newNotes || "" } }],
				},
			},
		};

		const response = await this.request<{ id: string }>(
			`/pages/${action.page_id}`,
			"PATCH",
			body,
		);
		return {
			action: "enrich",
			id: response.id,
			success: true,
		};
	}

	/**
	 * Mark a task as done.
	 */
	async resolveTask(action: ResolveTaskAction): Promise<ActionResult> {
		const body = {
			properties: {
				Estado: {
					select: { name: "✅ Hecho" },
				},
			},
		};

		const response = await this.request<{ id: string }>(
			`/pages/${action.page_id}`,
			"PATCH",
			body,
		);
		return {
			action: "resolve",
			id: response.id,
			success: true,
		};
	}

	/**
	 * Append digest blocks under a parent page or block.
	 */
	async appendDigest(action: AppendDigestAction): Promise<ActionResult> {
		const children: any[] = [
			{
				object: "block",
				type: "heading_2",
				heading_2: {
					rich_text: [{ text: { content: action.date_heading } }],
				},
			},
		];

		for (const section of action.sections) {
			children.push({
				object: "block",
				type: "heading_3",
				heading_3: {
					rich_text: [{ text: { content: section.heading } }],
				},
			});
			for (const bullet of section.bullets) {
				children.push({
					object: "block",
					type: "bulleted_list_item",
					bulleted_list_item: {
						rich_text: [{ text: { content: bullet } }],
					},
				});
			}
		}

		const response = await this.request<{ results: Array<{ id: string }> }>(
			`/blocks/${action.parent_id}/children`,
			"PATCH",
			{ children },
		);

		return {
			action: "digest",
			id: response.results?.[0]?.id || action.parent_id,
			success: true,
		};
	}

	/**
	 * Set triage mirror properties on an existing task page (resolver path).
	 * Selective by construction: absent fields never appear in the PATCH
	 * body, so fingerprints (Estado/Notas) and unrelated properties are
	 * preserved (spec R5, R7). 429/5xx retries come from request().
	 */
	async triageTask(action: SetTriageAction): Promise<ActionResult> {
		if (!action.page_id || typeof action.page_id !== "string") {
			throw new Error("triage action requires a page_id");
		}
		if (
			action.resolution_draft !== undefined &&
			action.resolution_draft.length > MAX_RESOLUTION_DRAFT_CHARS
		) {
			throw new Error(
				`resolution_draft exceeds ${MAX_RESOLUTION_DRAFT_CHARS} chars ` +
					`(${action.resolution_draft.length})`,
			);
		}

		const properties: Record<string, any> = {};
		if (action.triage_status !== undefined) {
			properties["Triage Status"] = { select: { name: action.triage_status } };
		}
		if (action.resolution_draft !== undefined) {
			properties["Resolution Draft"] = {
				rich_text: [{ text: { content: action.resolution_draft } }],
			};
		}
		if (action.local_context_ref !== undefined) {
			properties["Local Context Ref"] = {
				rich_text: [{ text: { content: action.local_context_ref } }],
			};
		}
		if (action.approval_state !== undefined) {
			properties["Approval State"] = { select: { name: action.approval_state } };
		}
		if (action.draft_id !== undefined) {
			properties["Draft ID"] = { rich_text: [{ text: { content: action.draft_id } }] };
		}
		if (Object.keys(properties).length === 0) {
			throw new Error("triage action sets no properties");
		}

		const response = await this.request<{ id: string }>(
			`/pages/${action.page_id}`,
			"PATCH",
			{ properties },
		);
		return {
			action: "triage",
			id: response.id,
			success: true,
		};
	}

	/**
	 * Execute an array of actions sequentially.
	 */
	async executeActions(actions: NotionAction[]): Promise<ActionResult[]> {
		const results: ActionResult[] = [];
		for (const action of actions) {
			try {
				const act = action.action || (action as any).type;
				switch (act) {
					case "create":
						results.push(await this.createTask(action as CreateTaskAction));
						break;
					case "enrich":
						results.push(await this.enrichTask(action as EnrichTaskAction));
						break;
					case "resolve":
						results.push(await this.resolveTask(action as ResolveTaskAction));
						break;
					case "digest":
						results.push(await this.appendDigest(action as AppendDigestAction));
						break;
					case "triage":
						results.push(await this.triageTask(action as SetTriageAction));
						break;
					default:
						throw new Error(`Unknown action: ${act}`);
				}
			} catch (err: any) {
				results.push({
					action: action.action,
					id: (action as any).page_id || "unknown",
					success: false,
					error: err instanceof Error ? err.message : String(err),
				});
				// Fail-closed: do not continue executing remaining writes if one fails
				break;
			}
		}
		return results;
	}
}

/** CLI entrypoint */
if (import.meta.main) {
	try {
		const token = process.env.NOTION_TOKEN || process.env.NOTION_CLECE;
		if (!token) {
			console.error("Missing NOTION_TOKEN or NOTION_CLECE environment variable");
			process.exit(1);
		}

		const dataSourceId =
			process.env.NOTION_DATA_SOURCE_ID || DEFAULT_DATA_SOURCE_ID;

		let rawInput = "";
		const fileArg = process.argv[2];
		if (fileArg) {
			rawInput = await Bun.file(fileArg).text();
		} else {
			rawInput = await Bun.stdin.text();
		}

		if (!rawInput.trim()) {
			console.error("No actions provided");
			process.exit(1);
		}

		const parsed = JSON.parse(rawInput);
		const actions: NotionAction[] = Array.isArray(parsed)
			? parsed
			: parsed.tasks || parsed.actions || [];

		const writer = new NotionWriter({ token, dataSourceId });
		const results = await writer.executeActions(actions);

		console.log(JSON.stringify(results));
		const failed = results.some((r) => !r.success);
		if (failed) {
			process.exit(1);
		}
	} catch (err: any) {
		console.error("Notion writer failed:", err?.message || err);
		process.exit(1);
	}
}
