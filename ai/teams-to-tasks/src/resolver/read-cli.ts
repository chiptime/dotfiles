#!/usr/bin/env bun
/**
 * Attended read CLI for the task-resolver evaluator (spec R2, task 3.3).
 *
 * Usage:
 *   bun src/resolver/read-cli.ts <proyecto> <path> [--offset N] [--limit M]
 *
 * Read-only by construction: the Proyecto maps through roots.ts to
 * realpath-pinned approved roots, the requested file passes the SAME
 * evidence gate that guards persistence (checkEvidence), and only then is
 * it read through the proven scoped reader. Deny output mirrors the
 * evidence-gate verdicts verbatim so the evaluator cites the exact policy
 * that refused the read. No Notion, no writes, no shell — untrusted text
 * stays data (R8).
 *
 * Exit codes: 0 read, 1 denied (gate or scoped reader), 2 usage error.
 */

import { statSync } from "node:fs";
import { join } from "node:path";
import { approvedRoots, parseProjects, defaultRootsEnv, type RootsEnv } from "./roots.ts";
import { checkEvidence, type EvidenceItem } from "./evidence-gate.ts";
import { scopedReader, type ReadContext } from "../scoped-read.ts";

export const READ_CLI_EXIT = { OK: 0, DENIED: 1, USAGE: 2 } as const;

export interface ReadCliArgs {
	proyecto: string;
	filePath: string;
	offset?: number;
	limit?: number;
}

/** Every seam injectable: tests fake the environment, never ~/.dotfiles. */
export interface ReadCliIo {
	projectsYaml: string;
	env: RootsEnv;
	stdout: (line: string) => void;
	stderr: (line: string) => void;
	/**
	 * Permission-engine ask; the attended CLI has no engine, so the default
	 * is a no-op — the human running the command IS the authority here, and
	 * the gate above already made the deterministic decision.
	 */
	ask?: ReadContext["ask"];
}

/** Attended auto-allow: the operator invoked this binary directly. */
const permissiveAsk: ReadContext["ask"] = async () => {};

/**
 * One gated read. Deny paths print the gate's violations (exact verdict
 * strings) and return 1; the success path prints the cited realpath header
 * plus numbered lines from the scoped reader.
 */
export async function runReadCli(args: ReadCliArgs, io: ReadCliIo): Promise<number> {
	const index = parseProjects(io.projectsYaml);
	const roots = approvedRoots(args.proyecto, index, io.env);
	if (roots.length === 0) {
		io.stderr(`deny: unknown Proyecto '${args.proyecto}': no approved roots (fail closed)`);
		return READ_CLI_EXIT.DENIED;
	}

	const pinned = io.env.realpath(args.filePath);
	if (pinned === null) {
		io.stderr(`deny: path does not resolve: ${args.filePath}`);
		return READ_CLI_EXIT.DENIED;
	}
	let bytes: number;
	try {
		bytes = statSync(pinned).size;
	} catch {
		io.stderr(`deny: unreadable target: ${pinned}`);
		return READ_CLI_EXIT.DENIED;
	}

	// The EXACT gate that guards persistence decides here too — same policy,
	// same verdict strings, no excerpt (denied contents are never read).
	const item: EvidenceItem = { citedPath: args.filePath, realpath: pinned, bytes };
	const verdict = checkEvidence({ roots, evidence: [item] });
	if (!verdict.allowed) {
		for (const violation of verdict.violations) io.stderr(`deny: ${violation}`);
		return READ_CLI_EXIT.DENIED;
	}

	const reader = scopedReader(roots);
	try {
		const result = await reader(
			{ filePath: pinned, offset: args.offset, limit: args.limit },
			{ directory: io.env.home, ask: io.ask ?? permissiveAsk },
		);
		io.stdout(`# ${pinned} (${bytes} bytes)`);
		io.stdout(result.output);
		return READ_CLI_EXIT.OK;
	} catch {
		// TOCTOU/binary/large-file refusal from the reader's own checks.
		io.stderr(`deny: scoped reader refused: ${pinned}`);
		return READ_CLI_EXIT.DENIED;
	}
}

/** Parse `<proyecto> <path> [--offset N] [--limit M]`; null ⇒ usage error. */
export function parseReadCliArgs(argv: string[]): ReadCliArgs | null {
	const positional: string[] = [];
	let offset: number | undefined;
	let limit: number | undefined;
	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i]!;
		if (arg === "--offset" || arg === "--limit") {
			const value = Number(argv[++i]);
			if (!Number.isInteger(value) || value < 1) return null;
			if (arg === "--offset") offset = value;
			else limit = value;
		} else if (arg.startsWith("--")) {
			return null;
		} else {
			positional.push(arg);
		}
	}
	if (positional.length !== 2) return null;
	return { proyecto: positional[0]!, filePath: positional[1]!, offset, limit };
}

async function main(argv: string[]): Promise<number> {
	const args = parseReadCliArgs(argv);
	if (args === null) {
		console.error("usage: read-cli.ts <proyecto> <path> [--offset N] [--limit M]");
		return READ_CLI_EXIT.USAGE;
	}
	const projectsPath =
		process.env.PROJECTS_YAML ??
		join(import.meta.dir, "..", "..", "..", "..", "scripts", "projects.yaml");
	const { readFileSync } = await import("node:fs");
	return runReadCli(args, {
		projectsYaml: readFileSync(projectsPath, "utf8"),
		env: defaultRootsEnv(),
		stdout: (line) => console.log(line),
		stderr: (line) => console.error(line),
	});
}

if (import.meta.main) {
	process.exit(await main(process.argv.slice(2)));
}
