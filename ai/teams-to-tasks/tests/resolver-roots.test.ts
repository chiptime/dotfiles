import { describe, expect, test } from "bun:test";
import {
	approvedRoots,
	parseProjects,
	type ProjectsIndex,
	type RootsEnv,
} from "../src/resolver/roots.ts";

const YAML = `# header comment — never a project
dotfiles:
  scope: personal
  status: active

sis:
  scope: work
  name: Clece SIS

recruiting-frontend:
  scope: work
  group: sis
  desc: Frontend Clece (Angular)

ghost:
  status: paused
`;

// Fake filesystem: only these paths resolve; values are their realpath pins.
const PINNED: Record<string, string> = {
	"/home/t/.dotfiles": "/data/dotfiles",
	"/home/t/Code/sis": "/data/work/sis",
	"/home/t/Code/dotfiles-satellite": "/data/work/satellite",
};
const env: RootsEnv = {
	home: "/home/t",
	codeRoot: "/home/t/Code",
	dotfilesRoot: "/home/t/.dotfiles",
	realpath: (path) => PINNED[path] ?? null,
};

describe("parseProjects", () => {
	test("extracts top-level keys and known fields, skips the rest", () => {
		const index = parseProjects(YAML);
		expect(Object.keys(index).sort()).toEqual([
			"dotfiles",
			"ghost",
			"recruiting-frontend",
			"sis",
		]);
		expect(index.sis).toEqual({ scope: "work", name: "Clece SIS" });
		expect(index["recruiting-frontend"]).toEqual({
			scope: "work",
			group: "sis",
		});
	});
});

describe("approvedRoots — mapping hits resolve (R2)", () => {
	test("dotfiles resolves to the realpath-pinned dotfiles root", () => {
		expect(approvedRoots("dotfiles", parseProjects(YAML), env)).toEqual([
			"/data/dotfiles",
		]);
	});

	test("a project under Code resolves by its yaml key", () => {
		expect(approvedRoots("sis", parseProjects(YAML), env)).toEqual([
			"/data/work/sis",
		]);
	});

	test("a declared name alias matches the same project", () => {
		expect(approvedRoots("Clece SIS", parseProjects(YAML), env)).toEqual([
			"/data/work/sis",
		]);
	});

	test("a grouped project falls back to its group root", () => {
		expect(
			approvedRoots("recruiting-frontend", parseProjects(YAML), env),
		).toEqual(["/data/work/sis"]);
	});
});

describe("approvedRoots — fail closed (R2)", () => {
	test("unknown Proyecto gets zero roots", () => {
		expect(approvedRoots("totes-unknown", parseProjects(YAML), env)).toEqual(
			[],
		);
	});

	test("a known project with no resolvable path on disk gets zero roots", () => {
		expect(approvedRoots("ghost", parseProjects(YAML), env)).toEqual([]);
	});

	test("empty index denies everything", () => {
		const index: ProjectsIndex = {};
		expect(approvedRoots("sis", index, env)).toEqual([]);
	});

	test("non-dotfiles key ignores the dotfiles convention even if present", () => {
		expect(approvedRoots("dotfiles-satellite", {
			"dotfiles-satellite": { scope: "personal" },
		}, env)).toEqual(["/data/work/satellite"]);
	});
});
