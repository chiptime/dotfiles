/**
 * Deterministic draft identity (spec R5).
 *
 * h = SHA-256 over canonical JSON of the approval-binding input. Canonical
 * JSON sorts object keys recursively and emits no whitespace, so semantically
 * identical drafts hash identically regardless of key order or formatting.
 * credential_name and session_id are part of the canonical input: an approval
 * never transfers across credentials or sessions (secret VALUES never persist
 * — only the name does).
 */

import { createHash } from "node:crypto";

/** Exactly the fields bound into an approval (tasks.md decisions header). */
export interface DraftHashInput {
	page_id: string;
	revision: string;
	/** Ordered action list — order is significant, arrays are NOT sorted. */
	actions: unknown[];
	destination: string;
	draft_id: string;
	credential_name: string;
	session_id: string;
}

/** Recursively key-sorted, whitespace-free JSON serialization. */
export function canonicalJson(value: unknown): string {
	return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(sortValue);
	if (value !== null && typeof value === "object") {
		return Object.fromEntries(
			Object.entries(value as Record<string, unknown>)
				.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
				.map(([key, val]) => [key, sortValue(val)]),
		);
	}
	return value;
}

/** Full SHA-256 hex digest of the canonical draft binding. */
export function draftHash(input: DraftHashInput): string {
	return createHash("sha256").update(canonicalJson(input), "utf8").digest("hex");
}

/** h[0:12] — the short form humans approve in chat (`approve <h12>`). */
export function shortHash(hash: string): string {
	return hash.slice(0, 12);
}
