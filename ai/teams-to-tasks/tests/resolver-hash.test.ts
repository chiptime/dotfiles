import { describe, expect, test } from "bun:test";
import {
	canonicalJson,
	draftHash,
	shortHash,
	type DraftHashInput,
} from "../src/resolver/draft-hash.ts";

const base: DraftHashInput = {
	page_id: "3d675532-da31-802b-b12f-000be53e99ac-page",
	revision: "r7",
	actions: [
		{ action: "triage", props: { status: "✋ Manual", a: 1, b: 2 } },
		{ action: "note", text: "hi" },
	],
	destination: "3d675532-da31-802b-b12f-000be53e99ac",
	draft_id: "d-42",
	credential_name: "notion-token",
	session_id: "sess-9",
};

describe("canonicalJson", () => {
	test("sorts keys recursively and emits no whitespace", () => {
		expect(canonicalJson({ b: 1, a: { d: 2, c: 3 } })).toBe(
			'{"a":{"c":3,"d":2},"b":1}',
		);
	});

	test("preserves array order (action order is significant)", () => {
		expect(canonicalJson(["x", "a"])).toBe('["x","a"]');
	});
});

describe("draftHash stability (R5)", () => {
	test("key insertion order does not change the hash", () => {
		const reordered: DraftHashInput = {
			session_id: base.session_id,
			credential_name: base.credential_name,
			draft_id: base.draft_id,
			destination: base.destination,
			revision: base.revision,
			actions: base.actions,
			page_id: base.page_id,
		};
		expect(draftHash(reordered)).toBe(draftHash(base));
	});

	test("nested action key order does not change the hash", () => {
		const flipped: DraftHashInput = {
			...base,
			actions: [
				{ action: "triage", props: { b: 2, a: 1, status: "✋ Manual" } },
				base.actions[1],
			],
		};
		expect(draftHash(flipped)).toBe(draftHash(base));
	});

	test("different credential_name or session_id ⇒ different hash", () => {
		expect(draftHash({ ...base, credential_name: "other-token" })).not.toBe(
			draftHash(base),
		);
		expect(draftHash({ ...base, session_id: "sess-10" })).not.toBe(
			draftHash(base),
		);
	});

	test("revision drift rehashes (approval voids on revise)", () => {
		expect(draftHash({ ...base, revision: "r8" })).not.toBe(draftHash(base));
	});

	test("action order is part of the identity", () => {
		const swapped: DraftHashInput = {
			...base,
			actions: [base.actions[1], base.actions[0]],
		};
		expect(draftHash(swapped)).not.toBe(draftHash(base));
	});
});

describe("shortHash", () => {
	test("returns the first 12 hex chars of the digest", () => {
		const full = draftHash(base);
		expect(shortHash(full)).toBe(full.slice(0, 12));
		expect(shortHash(full)).toHaveLength(12);
		expect(shortHash(draftHash({ ...base, revision: "r9" }))).not.toBe(
			shortHash(draftHash(base)),
		);
	});
});
