/**
 * Proyecto → approved-roots mapping (spec R2).
 *
 * Parses scripts/projects.yaml (READ-ONLY — this module never writes it)
 * into the known-project index, then resolves each Proyecto to realpath-
 * pinned local roots. Unknown Proyecto ⇒ zero roots: fail closed, the
 * evaluator classifies MANUAL_REQUIRED instead of guessing a path.
 *
 * Root conventions mirror scripts/projects-dashboard.sh: the `dotfiles`
 * key lives at ~/.dotfiles; every other project resolves under ~/Code by
 * its yaml key (plus its group's root when a group is declared). A
 * conventional path that does not exist on disk is dropped, not assumed.
 */

import { realpathSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export interface ProjectEntry {
	/** Display alias (e.g. "Clece SIS") — also accepted as Proyecto. */
	name?: string;
	scope?: string;
	status?: string;
	/** Group key (e.g. recruiting-frontend → sis): adds the group root. */
	group?: string;
}

export type ProjectsIndex = Record<string, ProjectEntry>;

const TOP_LEVEL = /^([A-Za-z0-9_.-]+):\s*(.*)$/;
const FIELD = /^\s+([A-Za-z0-9_]+):\s*(.*)$/;
const KNOWN_FIELDS = ["name", "scope", "status", "group"] as const;

/**
 * Minimal parser for the human-maintained one-block-per-repo shape
 * documented at the top of scripts/projects.yaml. Comments, blank lines
 * and unknown fields are skipped; any other top-level line ends the
 * current block (fail closed on format drift).
 */
export function parseProjects(yamlText: string): ProjectsIndex {
	const index: ProjectsIndex = {};
	let current: string | null = null;
	for (const rawLine of yamlText.split("\n")) {
		const line = rawLine.replace(/\r$/, "");
		const trimmed = line.trim();
		if (trimmed === "" || trimmed.startsWith("#")) continue;
		const indented = /^\s/.test(line);
		if (indented) {
			const field = FIELD.exec(line);
			if (current !== null && field && (KNOWN_FIELDS as readonly string[]).includes(field[1]))
				(index[current] as Record<string, string>)[field[1]] = unquote(field[2].trim());
			continue;
		}
		const top = TOP_LEVEL.exec(line);
		current = top ? top[1] : null;
		if (top) index[top[1]] = {};
	}
	return index;
}

function unquote(value: string): string {
	if (value.length >= 2 && (value.startsWith('"') || value.startsWith("'")))
		return value.slice(1, -1);
	return value;
}

/** Filesystem seam — tests inject a fake; production pins with realpath(3). */
export interface RootsEnv {
	home: string;
	codeRoot: string;
	dotfilesRoot: string;
	/** Canonical absolute path, or null when the path does not resolve. */
	realpath(path: string): string | null;
}

export function defaultRootsEnv(): RootsEnv {
	const home = homedir();
	return {
		home,
		codeRoot: join(home, "Code"),
		dotfilesRoot: join(home, ".dotfiles"),
		realpath: (path) => {
			try {
				return realpathSync(path);
			} catch {
				return null;
			}
		},
	};
}

/** Read + parse a projects.yaml file (read-only). */
export function loadProjects(path: string): ProjectsIndex {
	return parseProjects(readFileSync(path, "utf8"));
}

/**
 * Resolve the approved read roots for one Proyecto value. The Proyecto may
 * be a yaml key or a declared name alias. Every candidate is realpath-
 * pinned: missing paths are dropped, symlinks resolve to their canonical
 * target, duplicates collapse. Unknown Proyecto returns [] (fail closed).
 */
export function approvedRoots(
	proyecto: string,
	index: ProjectsIndex,
	env: RootsEnv,
): string[] {
	const key = matchKey(proyecto, index);
	if (key === null) return [];
	const entry = index[key]!;
	const candidates =
		key === "dotfiles"
			? [env.dotfilesRoot]
			: [
					join(env.codeRoot, key),
					...(entry.group && entry.group !== key
						? [join(env.codeRoot, entry.group)]
						: []),
				];
	const roots: string[] = [];
	for (const candidate of candidates) {
		const pinned = env.realpath(candidate);
		if (pinned !== null && !roots.includes(pinned)) roots.push(pinned);
	}
	return roots;
}

function matchKey(proyecto: string, index: ProjectsIndex): string | null {
	if (Object.prototype.hasOwnProperty.call(index, proyecto)) return proyecto;
	return Object.keys(index).find((key) => index[key]!.name === proyecto) ?? null;
}
