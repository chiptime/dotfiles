import { realpathSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { contains, scopedReader } from "./src/scoped-read.ts";

// Exactly one owned plugin. No SDK/npm imports: v1.18.30 accepts legacy JSON
// argument schemas and ToolResult attachments (tool/registry.ts, plugin/tool.ts).
export default async function (
	_input: unknown,
	options?: Record<string, unknown>,
) {
	if (
		!Array.isArray(options?.roots) ||
		options.roots.length !== 2 ||
		options.roots.some((root) => typeof root !== "string")
	)
		throw new Error("Invalid scoped reader roots");
	const roots = (options.roots as string[]).map((root) => realpathSync(root));
	return {
		tool: {
			teams_read: {
				description:
					"Read current-run screenshot images or paged tool-output files. Supports native image attachments. Use absolute filePath; text pagination uses offset (1-based) and limit (maximum 2000). Directories and other files are denied.",
				args: {
					filePath: { type: "string" },
					offset: { type: "integer", minimum: 1 },
					limit: { type: "integer", minimum: 1, maximum: 2000 },
				},
				execute: scopedReader(roots),
			},
		},
		"tool.execute.before": async (
			input: { tool: string },
			output: { args: { filename?: unknown } },
		) => {
			if (input.tool !== "playwright_teams_browser_take_screenshot") return;
			const name = output.args.filename;
			if (
				typeof name !== "string" ||
				!isAbsolute(name) ||
				!contains(roots[0]!, resolve(name))
			)
				throw new Error(
					"Screenshot requires an absolute current-run images path",
				);
			// Only a direct child is accepted; no nested directories or symlink targets.
			if (realpathSync(dirname(name)) !== roots[0])
				throw new Error("Screenshot directory denied");
			try {
				realpathSync(name);
			} catch (error: any) {
				if (error.code === "ENOENT") return;
				throw new Error("Screenshot target denied");
			}
			throw new Error(
				"Screenshot target already exists; choose a new filename",
			);
		},
	};
}
