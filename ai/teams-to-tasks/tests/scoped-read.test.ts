import { expect, test } from "bun:test";
import {
	linkSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	renameSync,
	rmSync,
	statSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import { join } from "node:path";
import plugin from "../read-plugin.ts";
import { scopedReader } from "../src/scoped-read.ts";
import { diagnostic, provisionAuth } from "../src/startup.ts";
import {
	evaluate,
	externalResource,
	fromConfig,
	readResource,
} from "./runtime-v11830.ts";

function fixture() {
	const root = mkdtempSync("/tmp/teams-reader-test-");
	const images = join(root, "images");
	const outputs = join(root, "data/opencode/tool-output");
	for (const dir of [images, outputs])
		mkdirSync(dir, { recursive: true, mode: 0o700 });
	const roots = [images, outputs];
	const permissions = fromConfig({
		"*": "deny",
		read: "deny",
		teams_read: Object.fromEntries([
			["*", "deny"],
			// The owned teams_read tool asks and allows with ABSOLUTE paths (immune to
			// the CLI's session worktree); the "/"-relative form stays unmatched.
			...roots.map((r) => [`${r}/*`, "allow"]),
		]),
		external_directory: Object.fromEntries([
			["*", "deny"],
			...roots.map((r) => [`${r}/*`, "allow"]),
		]),
	});
	const ctx = {
		directory: root,
		async ask(req: { permission: string; patterns: string[] }) {
			for (const pattern of req.patterns)
				if (evaluate(req.permission, pattern, permissions).action !== "allow")
					throw new Error("Permission denied");
		},
	};
	return {
		root,
		roots,
		images,
		outputs,
		ctx,
		permissions,
		dispose: () => rmSync(root, { recursive: true, force: true }),
	};
}

test("version-matched native resource/evaluator mismatch and replacement permissions", () => {
	const f = fixture();
	try {
		const image = join(f.images, "a.png");
		expect(
			evaluate(
				"read",
				readResource("/", image),
				fromConfig({ read: { "*": "deny", [`${f.images}/*`]: "allow" } }),
			).action,
		).toBe("deny");
		// The owned teams_read tool asks with the absolute path and its absolute
		// rule allows it; the native read "/"-relative resource form never matches.
		expect(
			evaluate("teams_read", image, f.permissions).action,
		).toBe("allow");
		expect(
			evaluate("teams_read", readResource("/", image), f.permissions)
				.action,
		).toBe("deny");
		expect(
			evaluate("external_directory", externalResource(image), f.permissions)
				.action,
		).toBe("allow");
		expect(
			evaluate("read", readResource("/", image), f.permissions).action,
		).toBe("deny");
		for (const target of [
			join(f.root, "data/opencode/auth.json"),
			join(f.images, "../secret"),
			join(f.root, "sibling/images/a"),
			f.images,
		]) {
			expect(evaluate("teams_read", target, f.permissions).action).toBe(
				"deny",
			);
			expect(
				evaluate("teams_read", readResource("/", target), f.permissions)
					.action,
			).toBe("deny");
		}
	} finally {
		f.dispose();
	}
});

test("opened-target reader returns attachments and paged text through native evaluator", async () => {
	const f = fixture();
	try {
		const image = join(f.images, "a.png");
		writeFileSync(image, Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
		const p = await plugin({}, { roots: f.roots });
		const result = await p.tool.teams_read.execute({ filePath: image }, f.ctx);
		expect(result.attachments?.[0]?.mime).toBe("image/png");
		const output = join(f.outputs, "tool-1");
		writeFileSync(output, "one\ntwo\nthree");
		expect(
			(
				await scopedReader(f.roots)(
					{ filePath: output, offset: 2, limit: 1 },
					f.ctx,
				)
			).output,
		).toBe("2: two\nContinue with offset=3.");
		await expect(
			scopedReader(f.roots)(
				{ filePath: output },
				{
					...f.ctx,
					ask: async () => {
						throw new Error("Permission denied");
					},
				},
			),
		).rejects.toThrow("Permission denied");
	} finally {
		f.dispose();
	}
});

test("guard rejects real credential symlinks, hardlinks, traversal, directories and sibling runs", async () => {
	const f = fixture();
	try {
		const secret = join(f.root, "data/opencode/auth.json");
		writeFileSync(secret, "DUMMY_SECRET");
		const link = join(f.images, "secret.png");
		symlinkSync(secret, link);
		const hardlink = join(f.images, "hard.png");
		linkSync(secret, hardlink);
		for (const filePath of [
			link,
			hardlink,
			secret,
			`${f.images}/../data/opencode/auth.json`,
			f.images,
			join(f.root, "sibling/images/a.png"),
			"/proc/self/environ",
		]) {
			await expect(scopedReader(f.roots)({ filePath }, f.ctx)).rejects.toThrow(
				"Scoped read denied",
			);
		}
		const directoryLink = join(f.images, "escape");
		symlinkSync(join(f.root, "data/opencode"), directoryLink);
		await expect(
			scopedReader(f.roots)(
				{ filePath: join(directoryLink, "auth.json") },
				f.ctx,
			),
		).rejects.toThrow("Scoped read denied");
	} finally {
		f.dispose();
	}
});

test("pathname swap after open cannot redirect bytes to a credential", async () => {
	const f = fixture();
	try {
		const image = join(f.images, "text");
		const secret = join(f.root, "secret");
		writeFileSync(image, "SAFE");
		writeFileSync(secret, "DUMMY_SECRET");
		let swapped = false;
		const ctx = {
			...f.ctx,
			async ask(req: { permission: string; patterns: string[] }) {
				await f.ctx.ask(req);
				if (!swapped) {
					swapped = true;
					renameSync(image, join(f.images, "old"));
					symlinkSync(secret, image);
				}
			},
		};
		expect(
			(await scopedReader(f.roots)({ filePath: image }, ctx)).output,
		).toContain("SAFE");
	} finally {
		f.dispose();
	}
});

test("screenshot guard requires a new absolute direct-child path", async () => {
	const f = fixture();
	try {
		const p = await plugin({}, { roots: f.roots });
		const hook = p["tool.execute.before"];
		await hook(
			{ tool: "playwright_teams_browser_take_screenshot" },
			{ args: { filename: join(f.images, "new.png") } },
		);
		for (const filename of [
			undefined,
			"./new.png",
			join(f.root, "home/new.png"),
			`${f.images}/../outside.png`,
		])
			await expect(
				hook(
					{ tool: "playwright_teams_browser_take_screenshot" },
					{ args: { filename } },
				),
			).rejects.toThrow();
		writeFileSync(join(f.images, "existing.png"), "existing");
		await expect(
			hook(
				{ tool: "playwright_teams_browser_take_screenshot" },
				{ args: { filename: join(f.images, "existing.png") } },
			),
		).rejects.toThrow();
	} finally {
		f.dispose();
	}
});

test("native auth sources are available only as private selected-provider data and cleaned up", () => {
	const f = fixture();
	try {
		const home = join(f.root, "source");
		mkdirSync(join(home, ".local/share/opencode"), { recursive: true });
		writeFileSync(
			join(home, ".local/share/opencode/auth.json"),
			JSON.stringify({
				chosen: { type: "api", key: "DUMMY_FILE" },
				unrelated: { type: "wellknown", key: "unused", token: "unused" },
			}),
		);
		const data = join(f.root, "data");
		const authFile = join(data, "opencode/auth.json");
		const first = provisionAuth({ HOME: home }, data, "chosen");
		expect(first.source).toBe("private-file");
		expect(Object.keys(JSON.parse(readFileSync(authFile, "utf8")))).toEqual([
			"chosen",
		]);
		expect(statSync(authFile).mode & 0o777).toBe(0o600);
		first.cleanup();
		const second = provisionAuth(
			{
				HOME: home,
				OPENCODE_AUTH_CONTENT: JSON.stringify({
					chosen: { type: "api", key: "DUMMY_PROCESS" },
				}),
			},
			data,
			"chosen",
		);
		expect(JSON.parse(readFileSync(authFile, "utf8")).chosen.key).toBe(
			"DUMMY_PROCESS",
		);
		second.cleanup();
		expect(() => readFileSync(authFile)).toThrow();
		expect(() =>
			provisionAuth(
				{ OPENCODE_AUTH_CONTENT: "DUMMY_MALFORMED" },
				data,
				"chosen",
			),
		).toThrow("Invalid authentication source");
		expect(() =>
			provisionAuth(
				{ OPENCODE_AUTH_CONTENT: '{"chosen":{"type":"wellknown"}}' },
				data,
				"chosen",
			),
		).toThrow("Invalid authentication source");
	} finally {
		f.dispose();
	}
});

test("startup diagnostics contain fixed categories only and are private", () => {
	const f = fixture();
	try {
		diagnostic(f.root, "auth", "failed");
		diagnostic(f.root, "exit", "child-nonzero");
		const file = join(f.root, "startup.jsonl");
		expect(readFileSync(file, "utf8")).toBe(
			'{"stage":"auth","category":"failed"}\n{"stage":"exit","category":"child-nonzero"}\n',
		);
		expect(statSync(file).mode & 0o777).toBe(0o600);
	} finally {
		f.dispose();
	}
});
