# PRD-loop preparation skill

## Objective

Create a reusable `prd-loop` planning skill that composes the existing `prd` skill
with a proportional, deterministic handoff for a future authorized execution loop.

## Authorization and scope

- User confirmed `Crear prd-loop`, preserving `prd`, and three safe behavior cases.
- Author only the new skill, its references/templates/evaluation definitions, and
  this recovery document; install its local skill symlink and refresh the registry.
- Do not modify `prd/SKILL.md` or `prd/assets/`, both owned by parallel work.
- Do not implement the agent-tts roadmap, install an execution daemon, invoke SDD,
  change models/permissions/review mode, or perform paid/remote operations.
- Initial preparation did not authorize commits, staging, push, PR or merge.
  The subsequent user-confirmed delivery authorizes only this skill and this
  document on `feat/prd-loop` in `origin` (`chiptime/dotfiles`), from an isolated
  worktree using origin's configured SSH connection. Git destination checks and
  push are authorized; PR creation, merge and all unrelated changes remain excluded.
  The user accepted `size:exception` for this cohesive approximately 625-line unit.
  Existing unrelated WIP, the main checkout's index and its branch must stay intact.

## Route and forecast

- Route: delegated direct, not SDD. Mapping required more than three files;
  authoring spans the new skill, reference/template and evaluation definitions.
- One writer; independent evaluation workers may write only isolated scratch outputs.
- Forecast: approximately 300–450 authored lines, excluding generated registry
  and machine-local evaluation output. File count is not a universal loop policy.
- Effective TDD: no project setting or applicable runner was found by the mapper
  for this content-only skill. Do not infer TDD from unrelated projects' tests.
- Checks: readback/frontmatter/reference structure; three with-skill and baseline
  behavioral evaluations; symlink resolution; registry index; preserve original PRD.

## Tasks

- [x] PL-01 Map current PRD, skill installation, registry and portable loop patterns.
  Evidence: read-only mapper `ses_f0cc056c6ffefRZlU48VxPDu9c`; user scope confirmed.
- [x] PL-02 Author the new planning-only skill and supporting assets/eval definitions.
  Initial authoring evidence: 9 files, 448 lines total, all under
  `ai/agents/opencode/skills/prd-loop/`: `SKILL.md` (83 lines, valid YAML
  frontmatter `name: prd-loop`, body ~1032 tokens by chars/4),
  `references/loop-contract.md` (189), `assets/launch-request-template.md`
  (44, 19 visible placeholders + not-launchable banner), `evals/evals.json`
  (exact skill-creator schema: `skill_name` + 3 integer-id evals
  `{id, prompt, expected_output, files}`; no assertions — parent adds while
  evals run) and 5 tiny labeled synthetic fixtures under
  `evals/fixtures/notes-app/` (notes CRUD/list baseline, tests, pytest config,
  decisions ledger with supplied — not hardwired — coverage policy).
  Structural readback observed: frontmatter parsed with PyYAML, bundled links
  resolve, eval file refs exist skill-root relative, fixture sources compile
  (syntax only; NOT executed, no pass claimed — pytest unavailable to the
  writer), no `/home/bruno`-style absolutes in reusable body/assets.
  `prd` untouched by this writer: `SKILL.md` sha256 `19ced39f…` and
  `assets/verification-and-acceptance.md` sha256 `3a0994b8…` identical
  pre/post; its dirty git state is the parallel actor's, preserved.
  No symlink/registry install, no git staging, no memory writes.
- [x] PL-03 Observe structural and behavior checks; record honest limitations.
  Evidence (iteration 2, parent-graded against source v0.1.1): with_skill 15/15
  vs baseline 9/15; tiny-work and blocked-question guards pass; first generated
  pack counts coherent and literal cwds in every gate row. Metadata and outputs
  under `/tmp/opencode/prd-loop-workspace/iteration-2/` (`benchmark.json`,
  `benchmark.md`, `review.html`, `eval-1-multi-milestone/`,
  `eval-2-tiny-work/`, `eval-3-unresolved-product/`). Honest limitations, not
  hidden: (1) the first generated README's execution-plan hash is stale and one
  line citation is off by one — ungraded defects, recorded not fixed; (2) missing
  pytest/pytest-cov environment checks were surfaced as blocked instead of
  claiming a runnable launch; (3) three cases with one sample per case is a
  bounded observation, not a general guarantee; (4) no timing or token metrics
  available. Iteration-1 history: 14/14 vs 9/14 with an ungraded rubric-external
  defect (README quoted 7 gates G01-G07 vs 9 G01-G09 in the authority table,
  plus one same-as-G1 cwd), fixed by the bounded 0.1.1 revision (finalization
  readback, literal cwd, consent fields replaced by explicit paste
  authorization, assertions 15 = 7/4/4, Case-1 cwd criterion clarified).
- [x] PL-04 Install only the new mapping and update the skill registry.
  Evidence (observed install session): target verified absent before write
  (no other-actor conflict); idempotent
  `ln -sfn /home/bruno/.dotfiles/ai/agents/opencode/skills/prd-loop
  /home/bruno/.config/opencode/skills/prd-loop` applied; link resolution and
  live-file equality verified (SKILL.md sha256 `70670eff…` identical through
  source and link, frontmatter version 0.1.1 reads live). Registry refreshed
  with the repo-owned `gentle-ai skill-registry refresh --force` (cwd
  `/home/bruno/.dotfiles`): 27 skills indexed at `.atl/skill-registry.md`,
  cache `.atl/.skill-registry.cache.json` regenerated (no hit), `.atl/`
  already gitignored via `.gitignore` line 3 (no gitignore change). Both rows
  present exactly once: `prd` and `prd-loop`, each scope `user`, path
  `/home/bruno/.config/opencode/skills/prd(-loop)/SKILL.md`; no duplicate
  skips for either. Other real-dir skills' drift untouched; files for `prd`
  and settings/models/permissions untouched. Users need an OpenCode restart
  for runtime discovery of the new skill.
- [ ] PL-05 Publish the authorized unit from an isolated worktree on `feat/prd-loop`.
  Verify the exact staged allowlist, secret checks, commit identity and remote SHA.
  Record the implementation commit in this document; do not amend or force-push.

## Acceptance

- Existing `prd` remains byte-identical to its pre-task snapshot, except any
  independently attributed parallel edits; never revert those edits.
- Drafting/planning never authorizes implementation or fabricates runnable tools.
- Small work stays compact; larger work has locked design, stable tasks, gates,
  finite budgets, resume identity and explicit stop conditions.
- Verification policy is resolved from the target project/user, not fixed to
  agent-tts, 90% coverage, kcov, long-poll or its paths.
- All three cases have recorded output: multi-milestone preparation, tiny-work
  proportionality and an unresolved product decision that blocks without guessing.

## Progress and evidence

- Planning complete; PL-02 authored, PL-03 and PL-04 closed (evidence above).
  Current doc progress finishes against source skill v0.1.1.
- Bounded revision (skill 0.1.1) applied after iteration-1 grading; see PL-03
  evidence. Revised content totals: 500 authored lines across the 9 prd-loop
  files (heuristic `wc -l` recount) — exceeds the 300–450 planning forecast
  because the revision adds finalization-readback and consent rules; the
  overrun figure is heuristic only. No commit or PR requested, so no commit
  exception applies.
- Install verification: source fixture checksums unchanged pre/post —
  `prd/SKILL.md` sha256 `19ced39f…` and
  `prd/assets/verification-and-acceptance.md` `3a0994b8…` (the modified/untracked
  git state of `prd/` is the parallel actor's, preserved, never reverted).
  Generic secret scan of the 9 newly authored prd-loop files: clean. Git:
  nothing staged, no commits made, branch `master` with all pre-existing
  unrelated WIP intact.
- Engram mirror: parent observation #9639 is synchronized with this final
  document at close-out. No memory or session writes were performed by the
  install worker; the parent owns the mirror and registry persistence.
- Commit evidence: pending PL-05; preparation itself produced no commits
  (historical record retained). Delivery under way from isolated worktree
  `/home/bruno/.dotfiles-worktrees/prd-loop-publish`, branch `feat/prd-loop`
  cut from published `origin/master` base `a8f4ba4f…` (verified by fetch plus
  `ls-remote` identity match; branch previously absent local and remote):
  one local work-unit commit holding exactly the 10-file allowlist under the
  accepted `size:exception` (cohesive ~625 lines, `delivery_strategy:
  exception-ok`) is pending in this delivery, and push remains pending for the
  next authorized actor. PL-05 stays unchecked until the remote SHA is
  verified; no PR, merge, amend or force-push.
- Rollback boundary: remove only the newly created `prd-loop` skill and its new
  symlink/registry row; do not remove unrelated skills, PRD changes or
  evaluation data.
- Runtime note: users need an OpenCode restart for the new skill to be
  discoverable; the registry index itself is already updated.
