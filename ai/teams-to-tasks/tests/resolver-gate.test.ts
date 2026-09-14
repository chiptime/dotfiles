import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { DEFAULT_BUDGETS } from "../src/resolver/budgets.ts";
import {
	checkEvidence,
	type EvidenceItem,
} from "../src/resolver/evidence-gate.ts";

// R2/R8 security suite. The gate is a pure policy filter over realpath-pinned
// citations and never touches the filesystem: every path below is a
// constructed string. shell/private-env.sh is therefore denied BY NAME /
// realpath — its contents are never read by any module or test here.

const ROOT = "/data/work/sis";

const item = (overrides: Partial<EvidenceItem> = {}): EvidenceItem => ({
	citedPath: `${ROOT}/src/api.ts`,
	realpath: `${ROOT}/src/api.ts`,
	bytes: 120,
	excerpt: "export const api = 1;",
	revision: "rev-1",
	...overrides,
});

describe("allowed evidence passes (R2)", () => {
	test("a clean citation inside the approved root", () => {
		const verdict = checkEvidence({ roots: [ROOT], evidence: [item()] });
		expect(verdict.allowed).toBe(true);
		expect(verdict.violations).toEqual([]);
	});

	test("a citation at exactly the evidence cap passes (ceiling is a max)", () => {
		const cap = DEFAULT_BUDGETS.task.maxEvidenceBytes;
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ bytes: cap })],
		});
		expect(verdict.allowed).toBe(true);
	});
});

describe("secret and credential-adjacent paths denied by name/realpath (R2)", () => {
	test.each([
		["the dotfiles secret store", `${ROOT}/shell/private-env.sh`],
		["a private-env copy at root top", `${ROOT}/private-env.sh`],
		["dotenv file", `${ROOT}/.env`],
		["dotenv variant in a subdir", `${ROOT}/config/.env.production`],
		["credentials json", `${ROOT}/config/credentials.json`],
		["ssh private key", `${ROOT}/keys/id_rsa`],
		["pem key material", `${ROOT}/certs/server.pem`],
		["secrets yaml", `${ROOT}/secrets.yaml`],
		["token file", `${ROOT}/tokens.txt`],
		["auth json", `${ROOT}/auth.json`],
		["netrc", `${ROOT}/.netrc`],
		["password vault", `${ROOT}/vault.kdbx`],
	])("%s denied", (_label, realpath) => {
		const verdict = checkEvidence({ roots: [ROOT], evidence: [item({ realpath })] });
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("credential-adjacent");
	});

	test("private-env.sh is denied with no excerpt present — contents never observed", () => {
		const { excerpt: _neverRead, ...bare } = item({
			realpath: `${ROOT}/shell/private-env.sh`,
		});
		const verdict = checkEvidence({ roots: [ROOT], evidence: [bare] });
		expect(verdict.allowed).toBe(false);
	});
});

describe("dependency, cache and profile path segments denied (R2)", () => {
	test.each([
		["node_modules", `${ROOT}/node_modules/lib/index.js`],
		["vendored modules dir", `${ROOT}/modules/dotly/main.zsh`],
		["python bytecode cache", `${ROOT}/__pycache__/core.cpython-312.pyc`],
		["generic cache", `${ROOT}/.cache/yarn/v6/pkg`],
		["playwright mcp profile", `${ROOT}/.playwright-mcp/profile/default/Cookies`],
		["ssh dir", `${ROOT}/.ssh/config`],
		["gpg keyring dir", `${ROOT}/.gnupg/pubring.kbx`],
		["firefox profile", `${ROOT}/.mozilla/firefox/abc.default/places.sqlite`],
		["chrome profile", `${ROOT}/.config/google-chrome/Default/Cookies`],
	])("%s segment denied", (_label, realpath) => {
		const verdict = checkEvidence({ roots: [ROOT], evidence: [item({ realpath })] });
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("denied path segment");
	});
});

describe("realpath escapes refused (R2)", () => {
	test("a realpath outside every approved root is refused", () => {
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [
				item({ citedPath: `${ROOT}/docs/../../../etc/passwd`, realpath: "/etc/passwd" }),
			],
		});
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("outside approved roots");
	});

	test("empty approved roots deny everything (unknown Proyecto fails closed)", () => {
		const verdict = checkEvidence({ roots: [], evidence: [item()] });
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("outside approved roots");
	});

	test("a non-absolute realpath is refused, not string-matched", () => {
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ realpath: "src/api.ts" })],
		});
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("canonical absolute");
	});
});

describe("size caps enforced from DEFAULT_BUDGETS (R2, R9)", () => {
	test("a single item above the evidence cap is denied", () => {
		const cap = DEFAULT_BUDGETS.task.maxEvidenceBytes;
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ bytes: cap + 1 })],
		});
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("exceeds evidence cap");
	});

	test("items individually under the cap but together above it are denied", () => {
		const cap = DEFAULT_BUDGETS.task.maxEvidenceBytes;
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [
				item({ bytes: cap / 2 }),
				item({ bytes: cap / 2 }),
				item({ bytes: cap / 2 }),
			],
		});
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("evidence total");
	});

	test("an invalid byte count is denied, never summed", () => {
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ bytes: -1 })],
		});
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("invalid byte count");
	});
});

describe("secret value shapes in quoted excerpts denied (R2)", () => {
	test("a github token shape in an excerpt is denied", () => {
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ excerpt: "run with ghp_0123456789abcdefghijklmnopqrstuv" })],
		});
		expect(verdict.allowed).toBe(false);
		expect(verdict.violations.join("\n")).toContain("secret value shape");
	});

	test("a notion token shape in an excerpt is denied", () => {
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ excerpt: 'NOTION_TOKEN="ntn_513824750971356uoayzbc"' })],
		});
		expect(verdict.allowed).toBe(false);
	});

	test("a credential assignment without a known prefix is denied", () => {
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ excerpt: 'api_key = "a1b2c3d4e5f6g7h8i9j0k1l2"' })],
		});
		expect(verdict.allowed).toBe(false);
	});
});

describe("untrusted text stays inert quoted data (R8)", () => {
	test("a shell-injection string in Notas-derived text neither trips the gate nor executes", () => {
		const verdict = checkEvidence({
			roots: [ROOT],
			evidence: [item({ excerpt: "run rm -rf /tmp/x" })],
		});
		expect(verdict.allowed).toBe(true);
		expect(verdict.violations).toEqual([]);
	});

	// "Nothing executes" is a structural property, so it is pinned structurally:
	// the two modules that consume untrusted text must contain no process-
	// spawning mechanism whatsoever. If either ever grows one, this fails.
	test("the gate and run-store sources contain no execution mechanism", () => {
		for (const module of [
			"../src/resolver/evidence-gate.ts",
			"../src/resolver/run-store.ts",
		]) {
			const source = readFileSync(new URL(module, import.meta.url), "utf8");
			expect(source).not.toMatch(
				/child_process|Bun\.spawn|spawnSync|\bexecSync\b|\bexecve\b|passThrough:{/,
			);
		}
	});
});

describe("violations are complete and deterministic (R2)", () => {
	test("every violation is reported in evidence order", () => {
		const verdict = checkEvidence({
			roots: [],
			evidence: [
				item({ realpath: "/etc/passwd", bytes: -1 }),
				item({ realpath: `${ROOT}/.env` }),
			],
		});
		expect(verdict.violations.length).toBeGreaterThanOrEqual(4);
		expect(verdict.violations[0]).toContain("evidence[0]");
		expect(verdict.violations.at(-1)).toContain("evidence[1]");
	});
});
