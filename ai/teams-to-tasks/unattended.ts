/** Build an isolated runtime config without persisting credentials or changing globals. */
import { mkdirSync, readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { diagnostic, provisionAuth, type Stage } from "./src/startup.ts";
import template from "./unattended.json";

// v1.18.30 packages/opencode/src/tool/read.ts derives worktree-relative resources
// (worktree "/" for non-Git workspaces), which can never match absolute rules, so
// native read stays denied. teams_read is OUR plugin tool: its ask pattern and its
// config rule are both absolute, a self-consistent pair immune to the CLI's
// per-session worktree (the run's work directory, not "/"). Like
// external-directory.ts, rules use absolute paths and never resolve symlinks.
function scopedAllow(roots: string[]) {
	const allow: Record<string, "deny" | "allow"> = { "*": "deny" };
	for (const root of roots) allow[`${root}/*`] = "allow";
	return allow;
}

export function buildConfig(
	source: any,
	prompt: string,
	home: string,
	images: string,
	dataHome: string,
) {
	const model = source.agent?.[source.default_agent]?.model ?? source.model;
	if (typeof model !== "string" || !model.includes("/"))
		throw new Error("Explicit user model selection required");
	const config: any = structuredClone(template);
	config.model = model;
	// Preserve the user's provider definitions and model selection, not interactive plugins/agents.
	config.provider = source.provider ?? {};
	config.agent["teams-reader"].model = model;
	config.agent["teams-reader"].variant =
		source.agent?.[source.default_agent]?.variant;
	config.mcp = {};
	for (const name of ["playwright_teams", "notion", "engram"]) {
		if (!source.mcp?.[name] || source.mcp[name].enabled === false)
			throw new Error(`Missing authorized MCP: ${name}`);
		config.mcp[name] = structuredClone(source.mcp[name]);
		if (config.mcp[name].type === "local") {
			config.mcp[name].environment = {
				...config.mcp[name].environment,
				HOME: home,
			};
		}
	}
	const browser = config.mcp.playwright_teams;
	if (
		browser.type !== "local" ||
		!browser.command?.includes("--executable-path")
	)
		throw new Error("Explicit browser executable required; no repair allowed");
	browser.command.push("--output-dir", images);
	const roots = [images, join(dataHome, "opencode/tool-output")].map((root) =>
		realpathSync(root),
	);
	if (roots.some((root) => /[?*\\]/.test(root)))
		throw new Error("Unsupported permission root");
	// Fail closed if the only owned plugin fails to load: native read stays denied.
	config.plugin = [
		[pathToFileURL(join(import.meta.dir, "read-plugin.ts")).href, { roots }],
	];
	config.agent["teams-reader"].permission.read = "deny";
	config.agent["teams-reader"].permission.teams_read = scopedAllow(roots);
	config.agent["teams-reader"].permission.external_directory =
		scopedAllow(roots);
	config.agent["teams-reader"].prompt =
		`${prompt}\n\nCurrent-run image directory: ${roots[0]}\nFor EACH screenshot, pass a new absolute filename such as ${join(roots[0]!, "image-1.png")}, then image-2.png, etc. Never pass ./ filenames or save under HOME. Read images and paged tool output with teams_read (filePath, offset: 1, limit: 2000); native read is disabled. This overrides supplied skills and paged-output hints naming read.`;
	return config;
}

export function isolatedEnv(
	env: NodeJS.ProcessEnv,
	runDir: string,
	dataHome: string,
	cacheHome: string,
	config: unknown,
): NodeJS.ProcessEnv {
	const clean = Object.fromEntries(
		Object.entries(env).filter(
			([key]) => !key.startsWith("OPENCODE_") && !key.startsWith("TEAMS_"),
		),
	);
	return {
		...clean,
		HOME: join(runDir, "home"),
		XDG_CONFIG_HOME: join(runDir, "config"),
		XDG_STATE_HOME: join(runDir, "state"),
		// Isolated per run: OpenCode's CLI pages large tool output under
		// ${XDG_DATA_HOME}/opencode/tool-output/<id>. Falling back to the real user's
		// ~/.local/share (as this used to do) leaked that cache outside the run sandbox and
		// outside the run's own permission allowlist, causing exit-zero runs with a denied
		// `read` on the model's own paged tool output.
		XDG_DATA_HOME: dataHome,
		// Isolated per run, mirroring XDG_DATA_HOME: falling back to the real user's ~/.cache
		// (as this used to do) let the child process read/write the real npm package cache,
		// dynamic-provider package cache, and cached binaries under ~/.cache/opencode
		// (packages/core/src/global.ts, packages/core/src/npm.ts). Nothing under
		// XDG_CACHE_HOME is ever a target of the model's own `read` tool calls — it is
		// exclusively OpenCode's internal npm/plugin package cache and cached bin/ binaries,
		// consumed by the CLI process itself, not exposed to the agent — so no matching
		// permission.read/external_directory entry is needed, only isolation of the path.
		XDG_CACHE_HOME: cacheHome,
		OPENCODE_CONFIG_CONTENT: JSON.stringify(config),
		OPENCODE_DISABLE_PROJECT_CONFIG: "1",
		OPENCODE_DISABLE_CLAUDE_CODE: "1",
		OPENCODE_DISABLE_EXTERNAL_SKILLS: "1",
		OPENCODE_DISABLE_DEFAULT_PLUGINS: "1",
		// Pure would suppress the sole owned reader plugin. Fresh isolated config/HOME
		// and disabled project discovery exclude arbitrary user plugins instead.
		NO_COLOR: "1",
	};
}

if (import.meta.main) {
	process.umask(0o077);
	let stage: Stage = "prepare";
	let cleanup = () => {};
	let runDir = process.env.TEAMS_RUN_DIR!;
	try {
		const home = process.env.HOME!;
		runDir = realpathSync(runDir);
		const images = join(runDir, "images");
		const dataHome = join(runDir, "data");
		const cacheHome = join(runDir, "cache");
		for (const dir of [
			"images",
			"data/opencode/tool-output",
			"cache",
			"home",
			"config",
			"state",
			"work",
		])
			mkdirSync(join(runDir, dir), { recursive: true, mode: 0o700 });
		diagnostic(runDir, stage, "ok");
		stage = "version";
		const version = Bun.spawnSync(["opencode", "--version"], {
			stdout: "pipe",
			stderr: "pipe",
			timeout: 10_000,
		});
		if (
			version.exitCode !== 0 ||
			version.stdout.toString().trim() !== "1.18.30"
		) {
			diagnostic(runDir, stage, "unsupported-runtime");
			throw new Error("Unsupported runtime");
		}
		// The supported native non-Git resolver returns '/'. Refuse unexpected Git
		// roots rather than derive permissions from an assumed project identity.
		const git = Bun.spawnSync(["git", "rev-parse", "--show-toplevel"], {
			cwd: join(runDir, "work"),
			stdout: "pipe",
			stderr: "pipe",
		});
		if (git.exitCode !== 128) throw new Error("Unexpected workspace");
		diagnostic(runDir, stage, "ok");
		stage = "config";
		const source = JSON.parse(
			readFileSync(join(home, ".config/opencode/opencode.json"), "utf8"),
		);
		const skills = join(import.meta.dir, "../agents/opencode/skills");
		const prompt = [
			readFileSync(join(import.meta.dir, "unattended.md"), "utf8"),
			...[
				"teams-to-tasks/SKILL.md",
				"notion-personal-backlog/SKILL.md",
				"notion-personal-backlog/assets/notion-personal-backlog-schema.md",
			].map((path) => readFileSync(join(skills, path), "utf8")),
			"The unattended restrictions and final JSON contract above override interactive steps; do not load other skills.",
		].join("\n\n");
		const config = buildConfig(source, prompt, home, images, dataHome);
		diagnostic(runDir, stage, "ok");
		stage = "auth";
		const auth = provisionAuth(
			process.env,
			dataHome,
			config.model.split("/")[0],
		);
		cleanup = auth.cleanup;
		diagnostic(runDir, stage, "ok", auth.source);
		stage = "spawn";
		const proc = Bun.spawn(
			[
				"opencode",
				"run",
				"--format",
				"json",
				"--agent",
				"teams-reader",
				process.env.TEAMS_PROMPT!,
			],
			{
				cwd: join(runDir, "work"),
				env: isolatedEnv(process.env, runDir, dataHome, cacheHome, config),
				stdout: "inherit",
				stderr: "inherit",
			},
		);
		diagnostic(runDir, stage, "ok");
		const code = await proc.exited;
		stage = "exit";
		diagnostic(runDir, stage, code === 0 ? "ok" : "child-nonzero");
		process.exitCode = code;
	} catch {
		// Config can contain secrets: never dump it or interpolate parsing errors.
		try {
			diagnostic(runDir, stage, "failed");
		} catch {
			/* Do not expose filesystem errors. */
		}
		console.error(
			"Unattended startup failed; inspect private startup.jsonl stage/category records",
		);
		process.exitCode = 1;
	} finally {
		try {
			cleanup();
		} catch {
			try {
				diagnostic(runDir, "cleanup", "failed");
			} catch {
				/* Fixed categories only. */
			}
			process.exitCode = 1;
		}
	}
}
