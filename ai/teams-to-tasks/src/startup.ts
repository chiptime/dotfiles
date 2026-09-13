import {
	appendFileSync,
	mkdirSync,
	readFileSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { join } from "node:path";

export function provisionAuth(
	env: NodeJS.ProcessEnv,
	dataHome: string,
	provider: string,
) {
	// Native v1.18.30 Auth.all gives process-only auth content precedence over
	// Global.Path.data/auth.json. Provision a private copy, never model-visible config.
	let content = env.OPENCODE_AUTH_CONTENT;
	if (!content) {
		try {
			content = readFileSync(
				join(
					env.XDG_DATA_HOME || join(env.HOME!, ".local/share"),
					"opencode/auth.json",
				),
				"utf8",
			);
		} catch (error: any) {
			if (error.code !== "ENOENT")
				throw new Error("Authentication provisioning failed");
		}
	}
	if (!content)
		return { source: "provider-environment" as const, cleanup() {} };
	try {
		const parsed = JSON.parse(content);
		if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
			throw new Error();
		const selected = parsed[provider];
		// Do not forward unrelated accounts or wellknown auth, which can load remote
		// configuration (including plugins) before the isolated config is merged.
		if (!selected)
			return { source: "provider-environment" as const, cleanup() {} };
		if (selected.type !== "api" && selected.type !== "oauth") throw new Error();
		if (selected.type === "api" && typeof selected.key !== "string")
			throw new Error();
		if (
			selected.type === "oauth" &&
			(typeof selected.access !== "string" ||
				typeof selected.refresh !== "string" ||
				!Number.isFinite(selected.expires))
		)
			throw new Error();
		content = JSON.stringify({ [provider]: selected });
	} catch {
		throw new Error("Invalid authentication source");
	}
	const file = join(dataHome, "opencode/auth.json");
	mkdirSync(join(dataHome, "opencode"), { recursive: true, mode: 0o700 });
	writeFileSync(file, content, { mode: 0o600, flag: "wx" });
	return {
		source: env.OPENCODE_AUTH_CONTENT
			? ("process-content" as const)
			: ("private-file" as const),
		cleanup() {
			try {
				unlinkSync(file);
			} catch (error: any) {
				if (error.code !== "ENOENT")
					throw new Error("Authentication cleanup failed");
			}
		},
	};
}

export type Stage =
	| "prepare"
	| "version"
	| "config"
	| "auth"
	| "spawn"
	| "exit"
	| "cleanup";
export function diagnostic(
	runDir: string,
	stage: Stage,
	category: "ok" | "failed" | "child-nonzero" | "unsupported-runtime",
	auth?: "provider-environment" | "process-content" | "private-file",
) {
	// Only fixed enums, no exception strings, provider names, configuration or paths.
	appendFileSync(
		join(runDir, "startup.jsonl"),
		JSON.stringify({ stage, category, ...(auth ? { auth } : {}) }) + "\n",
		{ mode: 0o600 },
	);
}
