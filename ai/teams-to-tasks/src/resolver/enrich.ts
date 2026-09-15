#!/usr/bin/env bun
/**
 * Direct informational enrichment CLI for the task-resolver v2 (spec:
 * Direct Informational Enrichment + Enrichment Idempotency).
 *
 * Usage (from ai/teams-to-tasks/):
 *   bun src/resolver/enrich.ts <page-id> --status <hint> --draft-file <f> [--refs <r>]
 *
 * One read → skip check → ONE atomic triage PATCH. No FSM, no hash, no
 * receipts: the skip predicate (Draft ID OR Resolution Draft non-empty)
 * plus the writer's single-PATCH triage action make reruns idempotent.
 * Estado and Notas (fingerprint carriers) are never written; the v1
 * mirror fields (Approval State, Draft ID) are never written either —
 * writing Draft ID would resurrect hash semantics.
 *
 * Threat matrix (process integration): page_id is validated as a UUID
 * before any request leaves; the draft file is untrusted DATA read via
 * Bun.file and written verbatim — never interpolated into a shell; the
 * token comes from the environment only, never argv.
 *
 * Exit codes: 0 enriched/skipped, 1 refused or failed, 2 usage.
 */

import {
	MAX_RESOLUTION_DRAFT_CHARS,
	NOTION_API_BASE,
	NOTION_API_VERSION,
	NotionWriter,
	type SetTriageAction,
} from "../notion-writer.ts";
import type { FetchLike } from "./detector.ts";

/** The only Triage Status hints v2 classification may write. */
export const ENRICH_HINTS = ["🤖 Auto", "💡 Acción", "✋ Manual"] as const;
export type EnrichHint = (typeof ENRICH_HINTS)[number];

export const ENRICH_EXIT = { OK: 0, REFUSED: 1, USAGE: 2 } as const;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface EnrichArgs {
	pageId: string;
	status: string;
	draftFile: string;
	refs?: string;
}

/** Every seam injectable: tests fake fetch and the draft reader. */
export interface EnrichDeps {
	fetchFn: FetchLike;
	/** Env-sourced token; argv can never supply it (parser rejects unknown flags). */
	token: string;
	/** Draft-file reader seam; defaults to Bun.file().text(). */
	readText?: (path: string) => Promise<string>;
}

export interface EnrichIo {
	print: (line: string) => void;
}

export interface EnrichReport {
	outcome: "enriched" | "skipped" | "refused";
	pageId: string;
	/** Why a run skipped or was refused. */
	reason?: string;
}

type PageRecord = { properties?: Record<string, unknown> };

/** Joined plain text of a rich_text property ("" when absent or empty). */
function richTextPlain(page: PageRecord, property: string): string {
	const prop = page.properties?.[property] as
		| { rich_text?: Array<{ plain_text?: string; text?: { content?: string } }> }
		| undefined;
	if (!prop || !Array.isArray(prop.rich_text)) return "";
	return prop.rich_text.map((segment) => segment.plain_text ?? segment.text?.content ?? "").join("");
}

/** Read-only GET the skip predicate runs on. */
async function getPage(deps: EnrichDeps, pageId: string): Promise<PageRecord> {
	const res = await deps.fetchFn(`${NOTION_API_BASE}/pages/${pageId}`, {
		method: "GET",
		headers: {
			Authorization: `Bearer ${deps.token}`,
			"Notion-Version": NOTION_API_VERSION,
			"Content-Type": "application/json",
		},
	});
	if (!res.ok) {
		let text = "";
		try {
			text = await res.text();
		} catch {
			// body already consumed — status alone is enough to fail closed
		}
		throw new Error(`page retrieve failed: HTTP ${res.status}: ${text.slice(0, 200)}`);
	}
	return (await res.json()) as PageRecord;
}

/** Parse `<page-id> --status <s> --draft-file <f> [--refs <r>]`; null ⇒ usage error. */
export function parseEnrichArgs(argv: string[]): EnrichArgs | null {
	const positional: string[] = [];
	let status: string | undefined;
	let draftFile: string | undefined;
	let refs: string | undefined;
	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i]!;
		if (arg === "--status" || arg === "--draft-file" || arg === "--refs") {
			const value = argv[++i];
			if (value === undefined || value === "") return null;
			if (arg === "--status") status = value;
			else if (arg === "--draft-file") draftFile = value;
			else refs = value;
		} else if (arg.startsWith("--")) {
			return null; // unknown flag (--token included): fail closed
		} else {
			positional.push(arg);
		}
	}
	if (positional.length !== 1 || status === undefined || draftFile === undefined) return null;
	return { pageId: positional[0]!, status, draftFile, refs };
}

export async function runEnrich(
	args: EnrichArgs,
	deps: EnrichDeps,
	io: EnrichIo,
): Promise<EnrichReport> {
	const finish = (report: EnrichReport): EnrichReport => {
		io.print(JSON.stringify(report));
		return report;
	};
	const refuse = (reason: string): EnrichReport => ({
		outcome: "refused",
		pageId: args.pageId,
		reason,
	});

	// Pre-flight guards: zero requests on refusal.
	if (!UUID_RE.test(args.pageId)) {
		return finish(refuse(`page_id is not a UUID: ${args.pageId}`));
	}
	if (!(ENRICH_HINTS as readonly string[]).includes(args.status)) {
		return finish(refuse(`status must be one of: ${ENRICH_HINTS.join(" / ")}`));
	}

	// Idempotency read (GET): skip iff Draft ID OR Resolution Draft non-empty.
	// Local Context Ref is excluded — legitimately empty on ✋ Manual tasks.
	const page = await getPage(deps, args.pageId);
	const enrichedAlready = [richTextPlain(page, "Draft ID"), richTextPlain(page, "Resolution Draft")];
	if (enrichedAlready.some((value) => value !== "")) {
		return finish({
			outcome: "skipped",
			pageId: args.pageId,
			reason: "already enriched (Draft ID or Resolution Draft non-empty)",
		});
	}

	const readText = deps.readText ?? ((path: string) => Bun.file(path).text());
	let draft: string;
	try {
		draft = await readText(args.draftFile);
	} catch {
		return finish(refuse(`draft file unreadable: ${args.draftFile}`));
	}
	if (draft.length > MAX_RESOLUTION_DRAFT_CHARS) {
		return finish(
			refuse(`draft exceeds ${MAX_RESOLUTION_DRAFT_CHARS} chars (${draft.length})`),
		);
	}

	// Exactly three informational fields; Estado/Notas/Approval State/Draft
	// ID never present. Empty refs overwrite stale leftovers on re-enrich.
	const action: SetTriageAction = {
		action: "triage",
		page_id: args.pageId,
		triage_status: args.status,
		resolution_draft: draft,
		local_context_ref: args.refs ?? "",
	};
	const writer = new NotionWriter({ token: deps.token, fetchFn: deps.fetchFn });
	await writer.triageTask(action);
	return finish({ outcome: "enriched", pageId: args.pageId });
}

async function main(argv: string[]): Promise<number> {
	const args = parseEnrichArgs(argv);
	if (args === null) {
		console.error(
			"usage: enrich.ts <page-id> --status <🤖 Auto|💡 Acción|✋ Manual> --draft-file <f> [--refs <r>]",
		);
		return ENRICH_EXIT.USAGE;
	}
	const token = process.env.NOTION_TOKEN || process.env.NOTION_CLECE;
	if (!token) {
		console.error("missing token: set NOTION_TOKEN (or NOTION_CLECE)");
		return ENRICH_EXIT.REFUSED;
	}
	try {
		const report = await runEnrich(
			args,
			{ fetchFn: globalThis.fetch, token },
			{ print: (line) => console.log(line) },
		);
		return report.outcome === "refused" ? ENRICH_EXIT.REFUSED : ENRICH_EXIT.OK;
	} catch (err) {
		console.error(err instanceof Error ? err.message : err);
		return ENRICH_EXIT.REFUSED;
	}
}

if (import.meta.main) {
	process.exit(await main(process.argv.slice(2)));
}
