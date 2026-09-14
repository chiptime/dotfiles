/**
 * Deterministic evidence boundary filter for the task-resolver (spec R2, R8).
 *
 * The gate is the LAST line of defense before anything persists: it rechecks
 * every evidence citation against the realpath-pinned approved roots resolved
 * by roots.ts, denies credential-adjacent paths BY NAME (shell/private-env.sh
 * and friends are refused without their contents ever being read), denies
 * dependency/cache/profile path segments, scans quoted excerpts for secret
 * value shapes, and enforces the evidence size ceiling from
 * DEFAULT_BUDGETS. Any violation fails closed — run-store.persistRun throws
 * before a single byte reaches disk.
 *
 * Pure policy: no filesystem access, no shell, no ambient sessions. Untrusted
 * text (Notas, Teams quotes, drafts) is data — pattern-matched here, never
 * interpreted (R8). Containment reuses scoped-read.ts's contains(): both the
 * roots and each realpath must already be canonical absolute paths (pinned by
 * roots.ts and the scoped reader), so lexical containment is sound.
 */

import { isAbsolute } from "node:path";
import { DEFAULT_BUDGETS } from "./budgets.ts";
import { contains } from "../scoped-read.ts";

/** One cited evidence file, as pinned by the scoped reader. */
export interface EvidenceItem {
	/** Path as cited in the draft (informational; policy runs on realpath). */
	citedPath: string;
	/**
	 * Canonical absolute path pinned at read time (the scoped reader pins via
	 * its file descriptor). The gate re-checks containment against the roots.
	 */
	realpath: string;
	/** File size in bytes observed at read time. */
	bytes: number;
	/** Quoted excerpt — untrusted data, compared verbatim against secret shapes. */
	excerpt?: string;
	/** Repo/doc revision when one exists (R2: record paths/revisions). */
	revision?: string;
}

export interface EvidenceGateInput {
	/** realpath-pinned approved roots — the exact output of roots.approvedRoots(). */
	roots: string[];
	evidence: EvidenceItem[];
}

export interface EvidenceGateVerdict {
	allowed: boolean;
	/** Deterministic, human-readable violations in evidence order. */
	violations: string[];
}

/** Thrown by run-store.persistRun when the verdict refuses to persist. */
export class EvidenceGateError extends Error {
	constructor(public readonly violations: string[]) {
		super(
			`evidence gate failed closed (${violations.length} violation${
				violations.length === 1 ? "" : "s"
			}): ${violations.join("; ")}`,
		);
		this.name = "EvidenceGateError";
	}
}

// Credential-adjacent BASENAMES — denied wherever they appear. Deliberately
// fail-closed: a source file merely NAMED like a secret (token.service.ts,
// tokenizer.ts) is refused too; the cost is an occasional MANUAL_REQUIRED
// classification, never a leaked credential (R2).
const DENIED_NAME_PATTERNS: RegExp[] = [
	/^private-env(?:\.[^.]+)?$/, // the dotfiles secret store — by name/realpath, contents never read
	/^\.env(?:\.[^.]+)?$/, // .env, .env.local, .env.production
	/credential|secret|token/i,
	/^id_(?:rsa|ed25519|ecdsa|dsa)$/, // SSH private keys
	/\.pem$|\.key$|\.kdbx$|^\.netrc$|^auth\.(?:json|ya?ml)$/,
];

// Path SEGMENTS — denied wherever the segment appears in the realpath:
// third-party code (node_modules), the dotfiles vendored modules/, caches,
// keyrings and browser/profile data (the R2 deny list).
const DENIED_SEGMENTS = new Set([
	"node_modules",
	"modules",
	"__pycache__",
	".cache",
	".pytest_cache",
	".mypy_cache",
	".gradle",
	".ssh",
	".gnupg",
	".playwright-mcp",
	".mozilla",
	"google-chrome",
]);

// Secret VALUE shapes in quoted excerpts (R2: never persist credentials).
// Token family prefixes with long tails, and keyword assignments with long
// values. False positives fail closed; the injection canary
// "run rm -rf /tmp/x" matches neither (R8).
const SECRET_EXCERPT_PATTERNS: Array<[string, RegExp]> = [
	[
		"token-prefix",
		/\b(?:ghp_|gho_|github_pat_|sk-ant-|sk-|xoxb-|xoxp-|ntn_|secret_|AKIA)[A-Za-z0-9_-]{15,}/,
	],
	[
		"credential-assignment",
		/\b(?:api[_-]?key|token|secret|password|passwd|credential)s?\s*[:=]\s*["']?[A-Za-z0-9_.+/=-]{16,}["']?/i,
	],
];

/**
 * Check every citation and return a complete verdict. Violations accumulate
 * (never first-error-only) so the human sees the full deny picture; the
 * caller persists only when violations is empty.
 */
export function checkEvidence({ roots, evidence }: EvidenceGateInput): EvidenceGateVerdict {
	const cap = DEFAULT_BUDGETS.task.maxEvidenceBytes;
	const violations: string[] = [];
	let total = 0;
	evidence.forEach((entry, index) => {
		const label = `evidence[${index}]`;
		const base = entry.realpath.split("/").pop() ?? entry.realpath;

		if (!isAbsolute(entry.realpath)) {
			violations.push(`${label}: realpath must be canonical absolute: ${entry.realpath}`);
		} else if (!roots.some((root) => contains(root, entry.realpath))) {
			violations.push(`${label}: realpath outside approved roots: ${entry.realpath}`);
		}

		if (!Number.isInteger(entry.bytes) || entry.bytes < 0) {
			violations.push(`${label}: invalid byte count: ${entry.bytes}`);
		} else {
			if (entry.bytes > cap) {
				violations.push(`${label}: size ${entry.bytes} exceeds evidence cap ${cap}`);
			}
			total += entry.bytes;
		}

		if (DENIED_NAME_PATTERNS.some((pattern) => pattern.test(base))) {
			violations.push(`${label}: denied credential-adjacent name: ${base}`);
		}
		const segment = entry.realpath.split("/").find((part) => DENIED_SEGMENTS.has(part));
		if (segment !== undefined) {
			violations.push(`${label}: denied path segment '${segment}'`);
		}

		if (entry.excerpt !== undefined) {
			const secret = SECRET_EXCERPT_PATTERNS.find(([, pattern]) => pattern.test(entry.excerpt!));
			if (secret) violations.push(`${label}: secret value shape (${secret[0]}) in excerpt`);
		}
	});
	if (total > cap) {
		violations.push(`evidence total ${total} exceeds cap ${cap}`);
	}
	return { allowed: violations.length === 0, violations };
}
