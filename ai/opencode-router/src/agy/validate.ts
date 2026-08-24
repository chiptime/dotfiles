/**
 * Canonical exploration validation: section lint + git porcelain
 * pre/post containment check. Repo mutation during an agy run is a
 * blocking failure (artifact_validation_failure) — never fallback.
 */
import { execFileSync } from 'node:child_process';

/** Canonical exploration sections (antigravity-explore output contract). */
export const REQUIRED_SECTIONS = ['Current State', 'Affected Areas', 'Approaches', 'Recommendation', 'Risks', 'Ready for Proposal'];

export interface SectionLint {
	ok: boolean;
	missing: string[];
}

export interface ValidationReport {
	ok: boolean;
	problems: string[];
}

/** Pure: which canonical sections are absent from the artifact text. */
export function lintSections(artifactText: string): SectionLint {
	const missing = REQUIRED_SECTIONS.filter((s) => !artifactText.includes(`### ${s}`));
	return { ok: missing.length === 0, missing };
}

/** Pure: any difference in `git status --porcelain` output is a mutation. */
export function detectMutation(porcelainBefore: string, porcelainAfter: string): boolean {
	return porcelainBefore !== porcelainAfter;
}

/** Pure: combine section lint + mutation check into one blocking report. */
export function validateExploration(input: { artifactText: string; porcelainBefore: string; porcelainAfter: string }): ValidationReport {
	const problems: string[] = [];
	const lint = lintSections(input.artifactText);
	if (!lint.ok) problems.push(`missing_sections:${lint.missing.join(',')}`);
	if (detectMutation(input.porcelainBefore, input.porcelainAfter)) problems.push('repo_mutated');
	return { ok: problems.length === 0, problems };
}

/** Real git porcelain snapshot of a repo (hash input for detectMutation). */
export function porcelain(repo: string): string {
	try {
		return execFileSync('git', ['-C', repo, 'status', '--porcelain'], { encoding: 'utf8' });
	} catch {
		return '';
	}
}
