// Extracted from anomalyco/opencode v1.18.30 (MIT), commit
// 3104c1428ec91f809e5ab86631300de41eb6952e. Not the dev/core ReadTool.
// Resource expressions: packages/opencode/src/tool/{read,external-directory}.ts.
// Function bodies: packages/core/src/util/wildcard.ts and
// packages/opencode/src/permission/index.ts. Only type references are adapted.
import path from "node:path";

export function readResource(worktree: string, filepath: string) {
	return path.relative(worktree, filepath);
}

export function externalResource(filepath: string) {
	const dir = path.dirname(filepath);
	return path.join(dir, "*").replaceAll("\\", "/");
}

function match(input: string, pattern: string) {
	const normalized = input.replaceAll("\\", "/");
	let escaped = pattern
		.replaceAll("\\", "/")
		.replace(/[.+^${}()|[\]\\]/g, "\\$&")
		.replace(/\*/g, ".*")
		.replace(/\?/g, ".");
	if (escaped.endsWith(" .*")) escaped = escaped.slice(0, -3) + "( .*)?";
	return new RegExp(
		"^" + escaped + "$",
		process.platform === "win32" ? "si" : "s",
	).test(normalized);
}
const Wildcard = { match };
type Rule = { permission: string; pattern: string; action: string };
export function evaluate(
	permission: string,
	pattern: string,
	...rulesets: Rule[][]
): Rule {
	return (
		rulesets
			.flat()
			.findLast(
				(rule) =>
					Wildcard.match(permission, rule.permission) &&
					Wildcard.match(pattern, rule.pattern),
			) ?? {
			action: "ask",
			permission,
			pattern: "*",
		}
	);
}

export function fromConfig(
	config: Record<string, string | Record<string, string>>,
): Rule[] {
	return Object.entries(config).flatMap(([permission, value]) =>
		typeof value === "string"
			? [{ permission, pattern: "*", action: value }]
			: Object.entries(value).map(([pattern, action]) => ({
					permission,
					pattern,
					action,
				})),
	);
}
