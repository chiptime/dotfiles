#!/usr/bin/env bun
/**
 * Attended one-shot schema migration for the task-resolver (spec R5,
 * design D4, task 3.4).
 *
 * Retrieves the Tareas data source, then PATCH-adds the 5 resolver
 * properties — Triage Status (select), Resolution Draft / Local Context
 * Ref / Draft ID (rich_text), Approval State (select) — skipping existing
 * properties and MERGING select options (additive only, never replaces).
 * A property that exists with a conflicting type is reported as a
 * conflict and the migration fails closed without sending anything.
 *
 *   --dry-run   print the planned PATCH without sending it. Without a
 *               token the plan is computed OFFLINE (existing schema
 *               unknown ⇒ all 5 planned) so the shape can be reviewed
 *               with zero network calls.
 *
 * Forbidden SIS data sources are refused the same way the detector
 * refuses them (shared ForbiddenSourceError guard). The API client is an
 * injectable fetch seam — tests mock it and make zero live calls.
 */

import {
	DEFAULT_DATA_SOURCE_ID,
	NOTION_API_BASE,
	NOTION_API_VERSION,
} from "../notion-writer.ts";
import { ForbiddenSourceError, assertAllowedSource, type FetchLike } from "./detector.ts";

/** Option set mirrors the design TriageStatus union (interfaces contract). */
export const TRIAGE_STATUS_OPTIONS = [
	"⏳ Pendiente",
	"🤖 Auto",
	"💡 Acción",
	"✋ Manual",
	"✔️ Hecho",
	"❌ Rechazado",
] as const;

/** Approval lifecycle mirror of the FSM approval states. */
export const APPROVAL_STATE_OPTIONS = ["Pendiente", "Aprobado", "Rechazado"] as const;

const select = (options: readonly string[]) => ({
	select: { options: options.map((name) => ({ name })) },
});

/** The 5 properties this migration owns (design D4; nothing else is touched). */
export const RESOLVER_PROPERTIES = {
	"Triage Status": select(TRIAGE_STATUS_OPTIONS),
	"Resolution Draft": { rich_text: {} },
	"Local Context Ref": { rich_text: {} },
	"Draft ID": { rich_text: {} },
	"Approval State": select(APPROVAL_STATE_OPTIONS),
} as const;

export type PropertySchema = Record<string, unknown>;

/** Shape of data_sources/{id}.properties as far as this migration reads it. */
export type DataSourceProperties = Record<
	string,
	{ type?: string; select?: { options?: { name?: string }[] } }
>;

export interface MigrationPlan {
	/** Property name → partial schema to PATCH (additions only). */
	additions: Record<string, PropertySchema>;
	skipped: string[];
	conflicts: string[];
}

/**
 * Pure planner. `existing === null` means offline (schema unknown): all 5
 * properties are planned as additions. Online, each property is either
 * added (missing), merged (select with missing options — only the missing
 * ones are sent), skipped (present and complete), or a conflict (present
 * with a different type — fail closed, never overwrite).
 */
export function planMigration(existing: DataSourceProperties | null): MigrationPlan {
	const plan: MigrationPlan = { additions: {}, skipped: [], conflicts: [] };
	for (const [name, desired] of Object.entries(RESOLVER_PROPERTIES)) {
		if (existing === null) {
			plan.additions[name] = desired as PropertySchema;
			continue;
		}
		const current = existing[name];
		if (current === undefined) {
			plan.additions[name] = desired as PropertySchema;
			continue;
		}
		if ("rich_text" in desired) {
			if (current.type === "rich_text") plan.skipped.push(name);
			else plan.conflicts.push(`${name}: expected rich_text, found ${current.type ?? "untyped"}`);
			continue;
		}
		// select property: additive option merge.
		const wanted = (desired as ReturnType<typeof select>).select.options.map(
			(option) => option.name,
		);
		if (current.type !== "select") {
			plan.conflicts.push(`${name}: expected select, found ${current.type ?? "untyped"}`);
			continue;
		}
		const present = new Set(
			(current.select?.options ?? []).map((option) => option.name),
		);
		const missing = wanted.filter((option) => !present.has(option));
		if (missing.length === 0) plan.skipped.push(name);
		else plan.additions[name] = select(missing);
	}
	return plan;
}

export interface SchemaMigrateDeps {
	fetchFn: FetchLike;
	/** Absent + dryRun ⇒ offline plan; absent + apply ⇒ hard error. */
	token?: string;
	dataSourceId: string;
	dryRun: boolean;
}

export interface SchemaMigrateIo {
	print: (line: string) => void;
}

async function request(
	deps: SchemaMigrateDeps,
	path: string,
	method: "GET" | "PATCH",
	body?: unknown,
): Promise<{ ok: boolean; status: number; text: string }> {
	const response = await deps.fetchFn(`${NOTION_API_BASE}${path}`, {
		method,
		headers: {
			Authorization: `Bearer ${deps.token}`,
			"Notion-Version": NOTION_API_VERSION,
			"Content-Type": "application/json",
		},
		body: body === undefined ? undefined : JSON.stringify(body),
	});
	return { ok: response.ok, status: response.status, text: await response.text() };
}

/**
 * Run the migration. Returns a process exit code: 0 done (or planned, or
 * nothing to do), 1 any failure — including conflicts, which never send a
 * PATCH. Forbidden sources throw ForbiddenSourceError before any fetch.
 */
export async function runSchemaMigrate(
	deps: SchemaMigrateDeps,
	io: SchemaMigrateIo = { print: (line) => console.log(line) },
): Promise<number> {
	assertAllowedSource(deps.dataSourceId);

	let existing: DataSourceProperties | null = null;
	if (deps.token !== undefined && deps.token !== "") {
		const retrieved = await request(
			deps,
			`/data_sources/${deps.dataSourceId}`,
			"GET",
		);
		if (!retrieved.ok) {
			io.print(`retrieve failed: HTTP ${retrieved.status}: ${retrieved.text.slice(0, 200)}`);
			return 1;
		}
		existing = (JSON.parse(retrieved.text) as { properties?: DataSourceProperties })
			.properties ?? {};
	} else if (!deps.dryRun) {
		io.print("missing token: set NOTION_TOKEN (or NOTION_CLECE) for a live migration");
		return 1;
	} else {
		io.print("offline dry-run: existing schema unknown — plan assumes all 5 missing");
	}

	const plan = planMigration(existing);
	for (const name of plan.skipped) io.print(`skip: ${name} already present`);
	for (const conflict of plan.conflicts) io.print(`conflict: ${conflict}`);
	if (plan.conflicts.length > 0) {
		io.print("migration aborted: resolve conflicts manually (fail closed)");
		return 1;
	}
	if (Object.keys(plan.additions).length === 0) {
		io.print("nothing to migrate: schema already complete");
		return 0;
	}

	const patchBody = { properties: plan.additions };
	io.print(`${deps.dryRun ? "[dry-run] would send" : "sending"} PATCH:`);
	io.print(JSON.stringify(patchBody, null, "\t"));
	if (deps.dryRun) return 0;

	const patched = await request(
		deps,
		`/data_sources/${deps.dataSourceId}`,
		"PATCH",
		patchBody,
	);
	if (!patched.ok) {
		io.print(`patch failed: HTTP ${patched.status}: ${patched.text.slice(0, 200)}`);
		return 1;
	}
	io.print(`migrated ${Object.keys(plan.additions).length} propert(ies) additively`);
	return 0;
}

async function main(argv: string[]): Promise<number> {
	const dryRun = argv.includes("--dry-run");
	const token = process.env.NOTION_TOKEN || process.env.NOTION_CLECE;
	const dataSourceId = process.env.NOTION_DATA_SOURCE_ID || DEFAULT_DATA_SOURCE_ID;
	try {
		return await runSchemaMigrate(
			{ fetchFn: globalThis.fetch, token, dataSourceId, dryRun },
		);
	} catch (err) {
		console.error(err instanceof Error ? err.message : err);
		return 1;
	}
}

if (import.meta.main) {
	process.exit(await main(process.argv.slice(2)));
}
