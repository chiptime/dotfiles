---
name: commit-hours-audit
description: "Trigger: commits en horario laboral, fuera de horario laboral, auditar fechas de commits, reescribir fechas de commits, commit dates, working hours, madrugada, tarde noche. Find commits made inside the user's working hours and preview/apply/verify the date rewrite that moves them out."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "2.0"
---

# commit-hours-audit

## Activation Contract

Load when asked to check that no commit falls inside working hours, or to move commits out of that window (rewrite author/committer dates), or when the user says "commits en horario laboral", "fuera de horario", "madrugada", "tarde noche", "reescribe las fechas de los commits".

Default rule: **Mon–Thu 09:00–19:00, Fri 08:00–16:00**. Days with no rule (weekend) have NO working hours.

## Hard Rules

- A commit violates when its **author OR committer** timestamp is inside a window. Weekends have no window.
- Preview-first: the audit never moves refs. It writes `plan.json`, `callback.py` and a ref snapshot into `<gitdir>/commit-hours-audit/<stamp>/`. Apply only after explicit confirmation of that exact plan.
- Commits are identified by **full commit id**, never by timestamp.
- Only the violating field moves. Proposals stay in the preferred slot (evening by default, never the small hours) AND keep dates monotonic: new date ≥ every parent, ≤ every child that keeps its date. When that is impossible inside the slot, the plan names the commit and the inversion; never hide it.
- Rewrite scope = every local branch and tag that contains a planned commit. Never rewrite `refs/remotes/*`, `refs/stash`, `refs/tags/backup/*`. Show the ref list, which refs are published, and the violators pulled in from those refs before asking for confirmation.
- Apply engine: `git filter-repo --force --refs <list> ^<frontier>… --commit-callback` keyed by `commit.original_id`, so history before the first planned commit keeps its ids and signatures. **Never use `git rebase` for this**: it drops merges and resets every replayed committer date to "now". If git-filter-repo is missing, stop and say so.
- Apply refuses on: uncommitted tracked changes, a git operation in progress, a rewritten branch checked out in another worktree, any ref (remote-tracking included) differing from the plan snapshot — after a `git fetch`, re-run the audit. Never auto-stash.
- Apply writes and verifies `git bundle create --all` first, and archives any previous `.git/filter-repo/` (filter-repo would chain its maps or stop at a prompt).
- Every apply records itself in the **machine-local audit log** `~/.local/share/commit-hours-audit/<repo>-<hash>/audit.jsonl` (append-only: `started`, `applied`, `restored`) and copies the bundle, plan and commit map next to it, outside the repository and never committed. If the log cannot be written, apply stops before rewriting. To find what an old or new commit id became, use `commit-hours-audit lookup <id>`; it works without the clone and follows chained rewrites.
- Success = `commit-hours-audit verify` passes per commit against the pre-rewrite history (tree, mapped parents, identities, dates only where planned, messages except remapped ids, refs, count) AND the re-audit of every rewritten commit reports 0. Commits the plan leaves out on purpose (outside `--range`, no past slot) keep their dates and are listed apart; those outside `--range` also keep their ids (a left-out commit after a rewritten one gets a new id, since its parent changed). Tip-tree equality is **NOT** proof.
- `apply`, `verify` and `remap-docs` reject audit options (`--ref`, `--rule`, …): the plan already fixes them.
- **Never call a timestamp "future" or "past" without checking the real clock** (`date`). Session time markers are not the system date.
- Rewriting published history requires a force-push; that is the human's decision. Print the `--force-with-lease=<ref>:<old-sha>` commands, never run them. If a push is rejected for `workflow` scope, push over the SSH alias of the CORRECT GitHub account — never weaken the token.
- The rule is configurable; never silently narrow or widen it. State the rule used.

## Decision Gates

| Situation | Action |
|---|---|
| Preferred slot not stated | `--slot evening` (default) |
| Proposals must not be dated after now | Default behaviour: it auto-walks back to a past slot |
| User explicitly wants same-day early morning | `--slot morning` |
| Only part of the history matters | `--range <base>..HEAD` |
| Another ref (e.g. the remote) | `--ref origin/main` |
| Every local branch and tag | `--all` |
| Plan warns "cannot keep commit order" | Show it; the user decides (other slot, `--range`, or accept the inversion) |
| Signed commits will lose their signature | Show the warning; the user decides |
| Tracked files cite old commit ids after apply | `commit-hours-audit remap-docs <plan-dir>` (preview), `--write` only on request |
| "What was commit X before / what is it now?" | `commit-hours-audit lookup <id>` |
| Machine-readable hand-off | `--json` |

## Execution Steps

1. Check `date`, then run the audit from the target repo: `commit-hours-audit [--ref <ref> | --all]`.
2. 0 violations: report clean (also check the remote-tracking ref the user cares about, e.g. `--ref origin/main`) and stop.
3. Present: rule and slot; table (id, field, actual, proposed, subject); refs to rewrite with their published flag; violators pulled in from other refs (`*`); remote-only refs; every warning (inversions, moved-back days, signed commits).
4. Ask for explicit confirmation. Do NOT apply, force-push, or tag without it.
5. Apply: `commit-hours-audit apply <plan-dir>` (guards → bundle → filter-repo → verify; refs are restored automatically if verification fails).
6. Report the verify line and the re-audit. On failure report the errors as printed; do not retry.
7. If tracked files cite old ids, offer `commit-hours-audit remap-docs <plan-dir>` (preview; `--write` only on request; it never commits — any follow-up commit must itself be outside working hours).
8. Print the push commands for published refs; push only on explicit request.

## Output Contract

Return: rule used, ref audited, violations count, the proposal table, refs to rewrite (published or not), warnings, plan path, apply command; after apply: rewritten refs (old → new), the verify line, the re-audit result, bundle path, audit log path, push commands.

## References

- `bin/commit-hours-audit` — the CLI (Node, zero npm deps, on PATH via `~/.local/bin`). `--help` documents every subcommand.
- `test/commit-hours-audit.test.js` — behavior tests: `node --test ai/agents/opencode/skills/commit-hours-audit/test/`.
- Requires `git` and Node >= 18; `apply` requires **git-filter-repo** (`uv tool install git-filter-repo`, tested with 2.47.0).
