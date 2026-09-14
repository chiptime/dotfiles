import { expect, test } from "bun:test";
import { buildConfig, isolatedEnv } from "../unattended.ts";
import {
	mkdirSync,
	mkdtempSync,
	readFileSync,
	realpathSync,
	rmSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const source = {
	default_agent: "interactive",
	model: "other/model",
	agent: { interactive: { model: "chosen/model", variant: "chosen-variant" } },
	plugin: ["unwanted"],
	permission: "allow",
	mcp: {
		playwright_teams: {
			type: "local",
			command: ["mcp", "--executable-path", "/browser"],
		},
		notion: { type: "local", command: ["notion"] },
		engram: { type: "local", command: ["engram"] },
		other: { type: "local", command: ["other"] },
	},
};

/** buildConfig canonicalizes read/external_directory roots via realpathSync, so the
 * directories must exist on disk before calling it (unlike the model/browser checks,
 * which fail closed before touching the filesystem — see the next test). */
function scratchDirs() {
	const root = mkdtempSync(join(tmpdir(), "teams-unattended-"));
	const images = join(root, "images");
	const data = join(root, "data");
	mkdirSync(images, { recursive: true });
	mkdirSync(join(data, "opencode/tool-output"), { recursive: true });
	return { root, images, data };
}

test("isolated allowlist preserves user model but excludes interactive agents/plugins/permissions", () => {
	const { root, images, data } = scratchDirs();
	try {
		const config = buildConfig(
			source,
			"bounded prompt",
			"/real-home",
			images,
			data,
		);
		expect(config.model).toBe("chosen/model");
		expect(config.agent["teams-reader"].variant).toBe("chosen-variant");
		expect(Object.keys(config.agent)).toEqual(["teams-reader"]);
		expect(Object.keys(config.mcp)).toEqual([
			"playwright_teams",
			"notion",
			"engram",
		]);
		expect(config.plugin).toHaveLength(1);
		expect(config.plugin[0][0]).toEndWith("/teams-to-tasks/read-plugin.ts");
		expect(config.permission).toEqual({ "*": "deny" });
		const permissions = config.agent["teams-reader"].permission;
		expect(permissions["*"]).toBe("deny");
		for (const name of [
			"bash",
			"task",
			"edit",
			"question",
			"playwright_teams_browser_evaluate",
			"playwright_teams_browser_run_code_unsafe",
			"engram_mem_save",
			"notion_API-delete-a-block",
			"notion_API-post-page",
			"notion_API-patch-page",
			"notion_API-patch-block-children",
		])
			expect(permissions[name]).toBeUndefined();
		const roots = [
			realpathSync(images),
			realpathSync(join(data, "opencode/tool-output")),
		];
		expect(permissions.read).toBe("deny");
		// Absolute rules for the owned teams_read tool, same form as its ask pattern
		// and as external_directory: immune to the CLI's per-session worktree.
		expect(permissions.teams_read).toEqual(
			Object.fromEntries([
				["*", "deny"],
				...roots.map((r) => [`${r}/*`, "allow"]),
			]),
		);
		expect(permissions.external_directory).toEqual(
			Object.fromEntries([
				["*", "deny"],
				...roots.map((r) => [`${r}/*`, "allow"]),
			]),
		);
		expect(config.agent["teams-reader"].prompt).toContain(
			join(images, "image-1.png"),
		);
		expect(config.agent["teams-reader"].prompt).toContain(
			"Never pass ./ filenames",
		);
		expect(source.mcp.playwright_teams.command).toEqual([
			"mcp",
			"--executable-path",
			"/browser",
		]);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});

test("missing user selection or browser executable fails closed before touching the filesystem", () => {
	// Nonexistent paths are fine here: these checks must fail before any realpathSync call.
	expect(() => buildConfig({}, "", "/home", "/images", "/data")).toThrow(
		"model",
	);
	expect(() =>
		buildConfig(
			{
				...source,
				mcp: {
					...source.mcp,
					playwright_teams: { type: "local", command: ["mcp"] },
				},
			},
			"",
			"/home",
			"/images",
			"/data",
		),
	).toThrow("executable");
});

test("configured roots use canonical paths and exclude credentials", () => {
	const root = mkdtempSync(join(tmpdir(), "teams-realpath-"));
	try {
		const real = join(root, "real");
		mkdirSync(join(real, "images"), { recursive: true });
		mkdirSync(join(real, "data/opencode/tool-output"), { recursive: true });
		const linkedRun = join(root, "linked-run");
		symlinkSync(real, linkedRun);
		const images = join(linkedRun, "images"); // literal path, reached THROUGH the symlink
		const data = join(linkedRun, "data");
		const config = buildConfig(
			source,
			"prompt",
			join(root, "home"),
			images,
			data,
		);
		const patterns = Object.keys(
			config.agent["teams-reader"].permission.teams_read,
		).filter((p) => p !== "*");
		expect(patterns).toEqual([
			`${real}/images/*`,
			`${real}/data/opencode/tool-output/*`,
		]);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});

test("child environment isolates HOME/config/data/cache and removes inherited OpenCode overrides", () => {
	const env = isolatedEnv(
		{
			HOME: "/real-home",
			PATH: "/bin",
			OPENCODE_CONFIG: "/interactive",
			OPENCODE_CONFIG_DIR: "/plugins",
			OPENCODE_PERMISSION: "allow",
			TEAMS_PROMPT: "private",
		},
		"/run",
		"/run/data",
		"/run/cache",
		{},
	);
	expect(env.HOME).toBe("/run/home");
	expect(env.XDG_CONFIG_HOME).toBe("/run/config");
	// Never the real user's ~/.local/share fallback: that leaked OpenCode's paged
	// tool-output cache outside the run sandbox (confirmed root cause).
	expect(env.XDG_DATA_HOME).toBe("/run/data");
	expect(env.XDG_DATA_HOME).not.toContain("/real-home");
	// Mirrors the XDG_DATA_HOME fix: never the real user's ~/.cache fallback, which let the
	// child process touch the real npm/plugin package cache under ~/.cache/opencode.
	expect(env.XDG_CACHE_HOME).toBe("/run/cache");
	expect(env.XDG_CACHE_HOME).not.toContain("/real-home");
	expect(env.OPENCODE_CONFIG).toBeUndefined();
	expect(env.OPENCODE_CONFIG_DIR).toBeUndefined();
	expect(env.OPENCODE_PERMISSION).toBeUndefined();
	expect(env.TEAMS_PROMPT).toBeUndefined();
	expect(env.OPENCODE_DISABLE_PROJECT_CONFIG).toBe("1");
	expect(env.OPENCODE_PURE).toBeUndefined();
	expect(env.XDG_STATE_HOME).toBe("/run/state");
});

test("installed CLI accepts isolated config without executing agents or MCPs", () => {
	const root = mkdtempSync(join(tmpdir(), "teams-config-"));
	try {
		for (const dir of [
			"home",
			"config",
			"work/images",
			"data/opencode/tool-output",
			"cache",
			"state",
			"work",
		])
			mkdirSync(join(root, dir), { recursive: true });
		const config = buildConfig(
			source,
			"test-only prompt",
			join(root, "home"),
			join(root, "work/images"),
			join(root, "data"),
		);
		// Debug config must not connect to any server; only validate loading/merging.
		for (const name of Object.keys(config.mcp))
			config.mcp[name] = { enabled: false };
		const env = isolatedEnv(
			{
				...process.env,
				HOME: join(root, "home"),
				XDG_STATE_HOME: join(root, "state"),
			},
			root,
			join(root, "data"),
			join(root, "cache"),
			config,
		);
		// Linux network namespace makes dependency initialization strictly offline.
		// First-run initialization retries catalog/network requests inside the
		// no-network namespace: measured ~72s offline versus ~5s with network, so
		// the spawn timeout must exceed that backoff rather than kill a healthy
		// (slow) startup.
		const result = Bun.spawnSync(
			["unshare", "-Urn", Bun.which("opencode")!, "debug", "config"],
			{
				cwd: join(root, "work"),
				env,
				stdout: "pipe",
				stderr: "pipe",
				timeout: 180_000,
			},
		);
		expect(result.exitCode).toBe(0);
		const resolved = JSON.parse(result.stdout.toString());
		expect(resolved.default_agent).toBe("teams-reader");
		expect(resolved.agent["teams-reader"].permission["*"]).toBe("deny");
		expect(resolved.model).toBe("chosen/model");
		expect(resolved.agent["gentle-orchestrator"]).toBeUndefined();
		expect(resolved.plugin).toHaveLength(1);
		expect(resolved.agent["teams-reader"].permission.read).toBe("deny");
		expect(resolved.plugin[0][0]).toEndWith("/teams-to-tasks/read-plugin.ts");
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
}, 190_000);

test("launcher records sanitized stages for generic failure and removes private auth", () => {
	const root = mkdtempSync(join(tmpdir(), "teams-startup-"));
	try {
		const home = join(root, "source-home");
		const run = join(root, "run");
		const bin = join(root, "bin");
		for (const dir of [join(home, ".config/opencode"), run, bin])
			mkdirSync(dir, { recursive: true, mode: 0o700 });
		writeFileSync(
			join(home, ".config/opencode/opencode.json"),
			JSON.stringify(source),
		);
		// A shell shim invokes a fixture, never the actual CLI or any MCP command.
		writeFileSync(
			join(bin, "opencode"),
			`#!/bin/sh\nexec "${process.execPath}" "${join(import.meta.dir, "startup-cli-fixture.ts")}" "$@"\n`,
			{ mode: 0o700 },
		);
		const result = Bun.spawnSync(
			[process.execPath, join(import.meta.dir, "../unattended.ts")],
			{
				env: {
					PATH: `${bin}:/usr/bin:/bin`,
					HOME: home,
					TEAMS_RUN_DIR: run,
					TEAMS_PROMPT: "DUMMY_PROMPT",
					OPENCODE_AUTH_CONTENT: JSON.stringify({
						chosen: { type: "api", key: "DUMMY_STARTUP" },
					}),
				},
				stdout: "pipe",
				stderr: "pipe",
				timeout: 15_000,
			},
		);
		expect(result.exitCode).toBe(1);
		expect(result.stdout.toString()).toContain("UnknownError");
		expect(result.stderr.toString()).toBe("");
		const log = readFileSync(join(run, "startup.jsonl"), "utf8");
		expect(log).toContain(
			'"stage":"auth","category":"ok","auth":"process-content"',
		);
		expect(log).toContain('"stage":"exit","category":"child-nonzero"');
		expect(log).not.toContain("DUMMY_STARTUP");
		expect(log).not.toContain("DUMMY_PROMPT");
		expect(() => readFileSync(join(run, "data/opencode/auth.json"))).toThrow();
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});
