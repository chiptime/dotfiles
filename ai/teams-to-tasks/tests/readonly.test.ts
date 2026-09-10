import { beforeEach, describe, expect, mock, test } from "bun:test";

/**
 * Read-only guarantee (spec: Read-only guarantee / No mutation calls).
 *
 * The adapter is the ONLY surface that can touch Teams. This test drives the
 * real TeamsSearchPage against a recording fake of `playwright-core` and
 * asserts the complete interaction sequence is read-only: navigate, search
 * `que`, filter Date=Today, read timestamps — never a composer fill, send,
 * reply, react, comment, or any request/fetch mutation.
 */

let actions: string[] = [];

function recordingLocator(kind: string): Record<string, unknown> {
	const loc: Record<string, unknown> = {
		first() {
			actions.push(`first(${kind})`);
			return loc;
		},
		or() {
			actions.push(`or(${kind})`);
			return loc;
		},
		nth(i: number) {
			actions.push(`nth(${kind},${i})`);
			return recordingLocator(`${kind}#${i}`);
		},
		locator(selector: string) {
			actions.push(`locator(${kind})`);
			return recordingLocator(`${kind}>${selector}`);
		},
		async count() {
			actions.push(`count(${kind})`);
			return 1;
		},
		async waitFor() {
			actions.push(`waitFor(${kind})`);
		},
		async fill(value: string) {
			actions.push(`fill(${kind},"${value}")`);
		},
		async press(key: string) {
			actions.push(`press(${kind},"${key}")`);
		},
		async click() {
			actions.push(`click(${kind})`);
		},
		async textContent() {
			actions.push(`readText(${kind})`);
			return "Rodrigo Santos Saez 10:42 AM";
		},
		async allTextContents() {
			actions.push(`readText(${kind})`);
			return ["Rodrigo Santos Saez 10:42 AM"];
		},
	};
	return loc;
}

const fakePage: Record<string, unknown> = {
	setDefaultTimeout() {},
	keyboard: {
		async press(key: string) {
			actions.push(`keyboardPress(${key})`);
		},
	},
	async goto(url: string) {
		actions.push(`goto(${url})`);
	},
	locator(selector: string) {
		return recordingLocator(`sel:${selector}`);
	},
	getByRole(role: string, opts?: { name?: string | RegExp }) {
		const name = opts?.name ? `:${String(opts.name)}` : "";
		return recordingLocator(`role:${role}${name}`);
	},
	async close() {
		actions.push("close(page)");
	},
};

const fakeContext: Record<string, unknown> = {
	async newPage() {
		actions.push("newPage");
		return fakePage;
	},
	async close() {
		actions.push("close(context)");
	},
};

const chromiumStub = {
	async launchPersistentContext() {
		actions.push("launchPersistentContext");
		return fakeContext;
	},
};

mock.module("playwright-core", () => ({ chromium: chromiumStub }));

const { TeamsSearchPage } = await import("../src/teams-page.ts");

/** Any token that would indicate a Teams mutation (compose/send/reply/react/request). */
const MUTATION =
	/\b(send|enviar|reply|responder|react|reaccion|comment|comentar|publicar|compose|redactar|sendMessage|postMessage|newMessage|nuevoMensaje|\.request|fetch\()/i;

describe("TeamsSearchPage — read-only guarantee", () => {
	beforeEach(() => {
		actions = [];
	});

	test("adapter issues only read-only Teams actions", async () => {
		const adapter = new TeamsSearchPage({
			executablePath: "/fake/chrome",
			userDataDir: "/tmp/fake-profile",
		});
		const stamps = await adapter.search();
		await adapter.close();

		// Navigation is the Teams search page only.
		expect(actions.find((a) => a.startsWith("goto("))).toBe(
			"goto(https://teams.microsoft.com)",
		);

		// The only text ever filled is the read-only discovery term "que".
		const fills = actions.filter((a) => a.startsWith("fill("));
		expect(fills.length).toBeGreaterThan(0);
		for (const f of fills) {
			expect(f).toContain('"que"');
		}

		// No mutation control is ever targeted and no request/fetch is issued.
		expect(actions.join("\n")).not.toMatch(MUTATION);

		// Timestamp text was read (a quiet day yields the raw stamps, not a crash).
		expect(actions.some((a) => a.startsWith("readText("))).toBe(true);
		expect(stamps).toEqual([{ rawTimestamp: "10:42 AM" }]);
	});
});
