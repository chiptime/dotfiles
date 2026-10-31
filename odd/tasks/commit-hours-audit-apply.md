# commit-hours-audit: safe apply path

Objective: replace the rebase-based apply path of the `commit-hours-audit` skill with an id-keyed, topology-preserving, verified rewrite (`git filter-repo`), as approved in L3.
Branch: `feat/commit-hours-audit-apply` (worktree `~/.dotfiles-worktrees/commit-hours-audit-apply`) · Delivery: `exception-ok` (this repo commits straight to `master`, no PR flow; the user merges the branch) · Test runner: `node --test ai/agents/opencode/skills/commit-hours-audit/test/`

## Specs

- **S1 Engine.** Move the apply path to "`git filter-repo --commit-callback` keyed by `commit.original_id`". Approved (L3): `git filter-repo --force --refs <explicit list> --commit-callback`, run by a new `commit-hours-audit apply <plan-dir>`; `apply.sh`, `amend.sh` and `dates.map` are no longer generated (legacy copies are removed when a new plan is written). Fixes "**Merges are destroyed.**" and "**The date map is keyed by author epoch, not by commit id.**". If git-filter-repo is missing, stop and say so.
- **S2 Scope.** "every ref that contains a violating commit (with an explicit list of the refs that will change, and which of them are published)". Approved: rewrite every local branch and tag that contains a planned commit; never `refs/remotes/*`, `refs/stash`, `refs/tags/backup/*`. Violators that appear only in those other refs are included in the plan and listed separately. Remote-only refs are listed, not rewritten. Tags report publication as unknown (the CLI never contacts a remote). New `--all` audits every local branch and tag. Fixes "**Only one ref is rewritten.**"
- **S3 Verification.** "replace the tip-tree check with a per-commit check against a pre-rewrite bundle (same tree, mapped parents, same identities, dates changed only where planned, messages identical except remapped ids, no missing refs, commit count explained, including dedup when a rewritten commit becomes identical to an existing one)." Plus re-audit = 0 on the rewritten commits; commits the plan leaves out on purpose (outside `--range`, no past slot) keep their dates and are listed apart; those outside `--range` also keep their ids, while a left-out commit after a rewritten one necessarily gets a new id (see L6, L7). New `commit-hours-audit verify <plan-dir>`; when it fails inside `apply`, the refs are restored from the snapshot and the command exits non-zero. Fixes "**The skill's own verification cannot detect it.**"
- **S4 Proposals.** "keep each new date ≥ its parents' and ≤ its descendants' dates, or warn explicitly when that is impossible inside the slot." Approved: only the violating field (author, committer or both) moves. Fixes "**Order with non-violating descendants is ignored.**"
- **S5 Commit map and docs.** "Emit an old→new commit map and offer a follow-up step that remaps hashes cited in tracked docs (skipping hashes embedded in names such as `backup/x-<sha>` or `file-<sha>.json`, and test fixtures)." Approved: `commit-hours-audit remap-docs <plan-dir>` previews by default, writes only with `--write`, never commits; maps of consecutive applied plans are chained. Fixes "**Commit ids cited in messages and files go stale.**"
- **S6 Safety.** "always write a full `git bundle --all` backup (not only a tag on HEAD), stash or refuse on a dirty worktree, and print the exact `--force-with-lease=<ref>:<old-sha>` push command for every published ref." Approved: refuse (never auto-stash) on dirty tracked files; also refuse on a git operation in progress, a rewritten branch checked out in another worktree, or any ref differing from the plan snapshot; archive a previous `.git/filter-repo/` so filter-repo never prompts or chains maps. Push commands are printed, never run.
- **S7 Audit fixes found while verifying the defects** (approved in the record): shas parsed without the leading newline (7-char ids in the table, valid `--json` shas, order checks live); rebase no longer resets unplanned committer dates (engine change); committer-only violators grouped by the violating field; morning slot never proposes the first working minute.
- **S8 SKILL.md.** Hard Rules, Execution Steps, Decision Gates, Output Contract updated as approved (record §4); help text documents the new subcommands.
- **S9 Tests.** "a fixture repo with merges, colliding timestamps, two refs sharing commits, and a non-violating child after a violator." Plus the approved cases T1–T12 (record §5), an injectable clock `COMMIT_HOURS_AUDIT_NOW`, isolated git config, scratch outside the worktree. Existing audit behavior (`--rule`, `--slot`, `--range`, clean output) stays pinned.

## Tasks

| ID | Specs | Route | Status | Evidence |
|---|---|---|---|---|
| T1 | S7, S9 | inline | done | RED 16/16 on base CLI → GREEN; feature commit |
| T2 | S4 | inline | done | tests "proposals never date…", "impossible order…" GREEN |
| T3 | S2, S1 (plan) | inline | done | tests T2/T3 GREEN |
| T4 | S1, S6 | inline | done | tests T5/T6/T11/missing filter-repo GREEN |
| T5 | S3 | inline | done | tests T7 (×2)/T8/T10 GREEN |
| T6 | S5 | inline | done | test T9 GREEN |
| T7 | S8 | inline | done | SKILL.md v2.0, `--help` |
| T8 | S1–S9 | delegated verifier (high risk: items 1, 5) | done | first pass on `5b3a904`: 5 blockers → correction `117f3dc` (L6); recheck: 4 FIXED, #5 wording → second correction (L7) |

- T1 audit parsing fix, raw dates, injectable clock, test harness and fixture.
- T2 constraint-aware proposals, per-field moves.
- T3 ref scope closure, publication, `plan.json` + `callback.py`.
- T4 `apply`: guards, bundle, filter-repo, restore on failure, push commands.
- T5 `verify`: per-commit, refs, counts, re-audit, inversions.
- T6 `remap-docs`.
- T7 SKILL.md and help.
- T8 independent verification of the whole feature.

Route evidence: every unit edits the same file (`bin/commit-hours-audit`), so no Writer trigger (no disjoint parallel units); the parent writes inline. T8 runs because the Risk line names item 1 (the tool rewrites existing history) and item 5 (new dependency: git-filter-repo).

## Log

- **L1 (2026-10-31, user, verbatim):**

  > # Discussion: redesign the apply path of the `commit-hours-audit` skill
  >
  > ## Context
  > Skill: `~/.dotfiles/ai/agents/opencode/skills/commit-hours-audit/` (SKILL.md + `bin/commit-hours-audit`, Node, zero deps).
  > It audits commits whose author/committer time falls inside the working-hours rule
  > (Mon–Thu 09:00–19:00, Fri 08:00–16:00) and writes a preview plan into
  > `<gitdir>/commit-hours-audit/` (`dates.map`, `amend.sh`, `apply.sh`).
  >
  > On 2026-10-30, in the `agent-tts` repo, the audit of `main` found 75 violating
  > commits (Oct 1–16) in a history of 835 commits that contains merge commits. The
  > generated `apply.sh` was NOT used because it would have lost history. The dates were
  > instead applied with `git filter-repo --commit-callback` (topology preserved, all refs
  > rewritten consistently), and every commit was verified against a pre-rewrite bundle.
  >
  > ## Verified defects in the current apply path (bin/commit-hours-audit, writePlan)
  > 1. **Merges are destroyed.** `apply.sh` runs
  >    `GIT_SEQUENCE_EDITOR=true git rebase -i --force-rebase <base> --exec amend.sh`
  >    with no `--rebase-merges`: the rebase flattens the history and drops every merge commit.
  > 2. **The skill's own verification cannot detect it.** SKILL.md says "re-run the audit
  >    and check that `HEAD^{tree}` is unchanged". A flattened rebase usually keeps the same
  >    tip tree, so the check passes while merges are lost.
  > 3. **The date map is keyed by author epoch, not by commit id.**
  >    `dates.map` lines are `<author_ts> <new_iso>` and `amend.sh` matches with
  >    `git log -1 --format=%ad --date=raw`. Two commits sharing an author second (common in
  >    scripted or rapid commits; e.g. two commits at 2026-10-02 10:41 in this run) collide,
  >    and any unrelated commit with the same timestamp would be rewritten too.
  > 4. **Only one ref is rewritten.** Other branches and tags that contain the same commits
  >    (here `feat/brain-parity-f1/f2`, already published) keep the old dates and diverge
  >    from the rewritten `main`.
  > 5. **Order with non-violating descendants is ignored.** `spread()` packs a day's
  >    violators into the evening window up to 23:59, so a non-violating child committed at
  >    19:26 can end up dated before its moved parent (5 new parent-after-child inversions
  >    in this run).
  > 6. **Commit ids cited in messages and files go stale.** The rebase leaves references to
  >    rewritten hashes in commit messages and docs unmapped, and no old→new map is emitted
  >    to fix them afterwards (filter-repo remaps hashes in messages and writes `commit-map`,
  >    and it also chains maps across consecutive runs).
  >
  > ## Questions to decide
  > - Should the apply path move to `git filter-repo --commit-callback` keyed by
  >   `commit.original_id` (requires the filter-repo dependency), or keep rebase with
  >   `--rebase-merges` plus id-keyed matching (harder: `--exec` runs per picked commit,
  >   merges are recreated, conflicts are possible)? Tradeoffs: dependency vs correctness,
  >   all refs vs a single ref, a fresh-clone/`--force` requirement, and the fact that
  >   filter-repo removes the `origin` remote and runs `reset --hard` (it must protect
  >   uncommitted work first).
  > - Scope: audit and rewrite one `--ref`, or every ref that contains a violating commit
  >   (with an explicit list of the refs that will change, and which of them are published)?
  > - Verification contract: replace the tip-tree check with a per-commit check against a
  >   pre-rewrite bundle (same tree, mapped parents, same identities, dates changed only
  >   where planned, messages identical except remapped ids, no missing refs, commit count
  >   explained, including dedup when a rewritten commit becomes identical to an existing one).
  > - Proposals: keep each new date ≥ its parents' and ≤ its descendants' dates, or warn
  >   explicitly when that is impossible inside the slot.
  > - Emit an old→new commit map and offer a follow-up step that remaps hashes cited in
  >   tracked docs (skipping hashes embedded in names such as `backup/x-<sha>` or
  >   `file-<sha>.json`, and test fixtures).
  > - Safety: always write a full `git bundle --all` backup (not only a tag on HEAD), stash
  >   or refuse on a dirty worktree, and print the exact `--force-with-lease=<ref>:<old-sha>`
  >   push command for every published ref.
  >
  > ## Expected output of the discussion
  > A short decision record (chosen approach + rejected alternatives with reasons), the
  > updated Hard Rules / Execution Steps for SKILL.md, and a test plan for the CLI: a
  > fixture repo with merges, colliding timestamps, two refs sharing commits, and a
  > non-violating child after a violator. Implementation only after the decision is approved. en español

- **L2 (2026-10-31, analysis):** reproduced in a scratch fixture: rebase dropped the merge (7→6 commits) while tip tree and re-audit both passed; rebase reset the committer date of every replayed unplanned commit to "now"; `collect()` leaves a leading `\n` in every sha after the first (6-char ids, corrupt `--json`, dead order warning). Same-second commits get identical proposals (key collision harmless for that pair; real harm: committer-only violators sharing an author epoch with a non-violator, or same instant in different offsets). filter-repo 2.47.0 source: `--refs` implies `--partial` (keeps `origin`, no origin→heads migration, no reflog expire/gc, stash untouched) but `reset --hard` still runs; `already_ran` < 24 h chains `commit-map`, > 24 h prompts via `input()`; message remap keeps length and also rewrites hashes embedded in names. Prototype with `--refs` + `commit.original_id` kept the merge, rewrote shared commits identically across refs, moved the tag, left `origin/main` untouched. Rejected: rebase `--rebase-merges` (re-does merges, committer = now, one ref, no map), `filter-branch` (discouraged, no message remap), fast-export/fast-import in Node (re-implements filter-repo), filter-repo without `--refs` (drops origin, reflogs, gc). git-filter-repo is installed with `uv` but declared nowhere in the dotfiles.
- **L3 (2026-10-31, user, verbatim):** "en pocas palabras" → summary given → "si" (decision record approved, including: violators found only in the other rewritten refs are included in the plan).
- **L5 (2026-10-31, evidence):** `node --test` 17/17 GREEN (~3.4 s). Real-history smoke on a scratch clone of this repo (267 commits, 3 merges): 93 dates moved, merges and tree kept, verify OK, weekday re-audit 0, remap-docs found 58 citations. Found during implementation: filter-repo strips the signature of every exported commit, so an untouched signed commit before the first planned one changed id; fixed by exporting only descendants of planned commits (`^<frontier>` in `--refs`) and warning about signed commits that do get rewritten (test "earlier signed commits keep their id", RED → GREEN). Differential vs base CLI on the clone: same totals/violators/fields for default, `--ref`, `--range`, `--slot morning`, `--rule`, and identical errors for bad args. Secret scan clean.
- **L6 (2026-10-31, T8 verifier on `5b3a904`):** suite 17/17; S1, S4, S7, S9 PASS. Blockers, all confirmed against the code and fixed in one correction batch with regression tests (RED 9/9 → GREEN 26/26): (1) `apply`/`verify` accepted and ignored audit options (`--ref`, `--rule`) → rejected now, `--json` rejected for apply; (2) nested annotated tags were peeled one level only → full peel via `cat-file --batch-check`, apply rewrites tag-of-tag; remote-only refs were listed only when a plan existed → computed from every violator; (3) the snapshot guard skipped unclaimed remote-tracking refs → every ref is compared (S6 says "any ref"); (4) `remap-docs` dropped non-ASCII file names (`git grep` quoting) → `git grep -z`; (5) a `--range` apply reported "1 still inside working hours" for commits before the range → verify now counts rewritten commits only and lists left-out ones (`leftOut`, reason `outside --range` / `no past slot`); S3 wording made precise to match the approved `--range` semantics (older history untouched). Advisory adopted: warn when a detached HEAD stays on rewritten history. Advisories not adopted: `--allow-future` has no effect (pre-existing at baseline).
- **L7 (2026-10-31, T8 recheck on `117f3dc`):** suite 26/26; blockers 1–4 FIXED (all 18 audit-option/subcommand combinations rejected; nested tag rewritten with and without the inner ref; moved/new/deleted remote-tracking refs refused with refs and worktree hashes preserved; Unicode and space-containing doc names remapped). Blocker 5: the `--range` case is fixed; the recheck flagged that a "no past slot" commit after a rewritten one changes id although S3 said left-out commits "keep their ids". That promise was wrong (a child of a rewritten commit cannot keep its id); the behavior is the only possible one. Second and last correction: S3 and SKILL.md wording fixed (dates kept; ids kept only outside `--range`) and the scenario pinned by a test. Verifier scratch retained for the parent to remove: `/tmp/opencode/chav-dHsD4O`, `/tmp/opencode/chav-xpGvgc`.
- **L4 (2026-10-31):** worktree created from `master` 98a607f; the main checkout has unrelated uncommitted work, so the feature lives in its own worktree and the live skill stays untouched until the user merges. RDD is off (clone-local), so no native review; ordinary checks plus T8.
