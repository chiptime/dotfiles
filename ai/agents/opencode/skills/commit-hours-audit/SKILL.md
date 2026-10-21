---
name: commit-hours-audit
description: "Trigger: commits en horario laboral, fuera de horario laboral, auditar fechas de commits, reescribir fechas de commits, commit dates, working hours, madrugada, tarde noche. Find commits made inside the user's working hours and preview/apply the date rewrite that moves them out."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# commit-hours-audit

## Activation Contract

Load when asked to check that no commit falls inside working hours, or to move commits out of that window (rewrite author/committer dates), or when the user says "commits en horario laboral", "fuera de horario", "madrugada", "tarde noche", "reescribe las fechas de los commits".

Default rule: **Mon–Thu 09:00–19:00, Fri 08:00–16:00**. Days with no rule (weekend) have NO working hours.

## Hard Rules

- A commit violates when its **author OR committer** timestamp is inside a window. Weekends are outside working hours, so a weekend commit is NOT a violation.
- The CLI is **preview-first**: it never rewrites. It writes a plan into `<gitdir>/commit-hours-audit/` and prints the apply command. Apply only after an explicit confirmation.
- Preferred slot is **evening, never the small hours** (madrugada). When the evening of the commit's own day is still in the future, the proposal moves back day by day until the slot is past — it does not fall back to 02:00–09:00 unless asked (`--slot morning`).
- **Never call a timestamp "future" or "past" without checking the real clock** (`date`). Session time markers are not the system date.
- Applying always creates a backup tag `backup/pre-datefix-<stamp>` first (`apply.sh` does it). After applying, verify: re-run the audit (expect 0 violations) AND `git rev-parse HEAD^{tree}` must equal the value taken before.
- Rewriting published history requires a force-push; that is the human's decision. If the push is rejected for `workflow` scope (workflow files), push over the SSH alias of the CORRECT GitHub account — never weaken the token.
- The rule is configurable; never silently narrow or widen it. State the rule used.

## Decision Gates

| Situation | Action |
|---|---|
| Preferred slot not stated | `--slot evening` (default) |
| Proposals must not be dated after now | Default behaviour: it auto-walks back to a past slot |
| User explicitly wants same-day early morning | `--slot morning` |
| Only part of the history matters | `--range <base>..HEAD` |
| Another ref (e.g. the remote) | `--ref origin/main` |
| Machine-readable hand-off | `--json` |

## Execution Steps

1. Run the audit from the target repo: `commit-hours-audit` (defaults: `--ref HEAD`, default rule).
2. If it reports 0 violations, report clean and stop. Also check the remote the user cares about: `commit-hours-audit --ref origin/main`.
3. If there are violations, present a compact table: SHA, actual date/time, proposed date/time, subject — plus every warning. State the rule and the slot used.
4. Ask for explicit confirmation. Do NOT apply, force-push, or tag without it.
5. On confirmation, apply: `bash <gitdir>/commit-hours-audit/apply.sh`.
6. Verify: re-run the audit (expect 0) and compare `git rev-parse HEAD^{tree}` with the recorded value. Report both.
7. If the branch is published, say plainly that a force-push is required and that hashes change; push only on explicit request.

## Output Contract

Return: rule used, ref audited, violations count, the proposal table, warnings (date shift / out-of-order risk), plan path, apply command, and the post-apply verification (violations = 0 and tree hash unchanged).

## References

- `bin/commit-hours-audit` — the CLI (Node, zero deps, on PATH via `~/.local/bin`).
- Requires `git` and Node >= 18.
