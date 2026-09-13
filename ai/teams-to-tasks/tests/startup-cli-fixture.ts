#!/usr/bin/env bun
// Dummy CLI only. Never invokes OpenCode, providers or MCP servers.
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";

if (process.argv.includes("--version")) {
	console.log("1.18.30");
	process.exit(0);
}
const config = JSON.parse(process.env.OPENCODE_CONFIG_CONTENT!);
const authPath = join(process.env.XDG_DATA_HOME!, "opencode/auth.json");
const auth = JSON.parse(readFileSync(authPath, "utf8"));
if (
	auth.chosen.key !== "DUMMY_STARTUP" ||
	(statSync(authPath).mode & 0o777) !== 0o600 ||
	process.env.OPENCODE_AUTH_CONTENT ||
	process.env.OPENCODE_PURE ||
	process.argv.includes("--pure") ||
	config.agent["teams-reader"].permission.read !== "deny" ||
	config.plugin.length !== 1
) {
	console.error("Dummy startup invariant failed");
	process.exit(2);
}
console.log(
	JSON.stringify({
		type: "error",
		error: {
			name: "UnknownError",
			data: { message: "Unexpected server error" },
		},
	}),
);
process.exit(1);
