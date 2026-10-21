---
name: jest-delta
description: "Trigger: jest-delta, delta tests, tests rotos preexistentes, pre-existing test failures, verificar tests sin regresiones, regression testing jest, tests fallando antes de mis cambios, hiringrequestrepository. Run Jest suites with pre-existing broken tests and verify regression-free state deterministically."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# Skill: jest-delta

Deterministic regression verification for Jest test suites in repositories with pre-existing or historical failing tests.

## Activation Contract

Activate whenever:
1. Running or verifying Jest tests in a codebase known to have pre-existing broken tests (e.g. Clece SIS, `HiringRequestRepository.test.ts` with 67 historical failures).
2. The user or prompt asks to "verificar tests", "correr tests con jest-delta", "comprobar si rompí algo", or "verificar que no hay regresiones".
3. An agent observes failing tests in a test run and needs to verify if those failures were already present in `HEAD`.

## Hard Rules

1. **NEVER clone into `/tmp` or run `git archive`** to compare test results against HEAD.
2. **ALWAYS prepend `jest-delta`** when invoking Jest in dirty working trees or suites with historical failures:
   ```bash
   jest-delta --json -- <test-command...>
   ```
3. **Deterministic machine verification**:
   - In automated agent loops, ALWAYS pass `--json` to consume structured output.
   - When `exit code == 0` and `"regression": false`: the verification is **PASSED**. Do NOT attempt to fix or investigate pre-existing failures.
   - When `exit code == 1` and `"regression": true`: address **ONLY** the tests listed in `new_failures` / `new_failures_details`. Never touch unrelated tests.
4. **Caching awareness**:
   - Base failures are automatically cached per git commit SHA in `.git/jest-delta-cache/`.
   - If starting a task on a clean tree before writing code, optionally pre-warm the cache:
     ```bash
     jest-delta --record-base -- <test-command...>
     ```
   - When working on a dirty tree without prior cache, `jest-delta` automatically creates an ephemeral worktree at HEAD, symlinks dependencies, records the baseline, and removes the worktree.

## Decision Gates

| Context / State | Forbidden Action | Required Action |
|---|---|---|
| Broken suite with pre-existing failures | Run raw `npx jest` or `npm test` and guess whether failures are new | Run `jest-delta --json -- <test-command>` |
| Comparing against base commit | Clone repo to `/tmp` with `git archive` or stash/pop | Use `jest-delta` (uses cached baseline or auto ephemeral worktree) |
| `status: "PASS"` (0 new failures, N pre-existing) | Investigate or fix pre-existing failures | Mark test verification step as COMPLETE (exit 0) |
| `status: "FAIL"` (N new failures) | Fix pre-existing failures | Fix only the specific tests in `new_failures` |
| Monorepo subdirectory | Run from repo root if `node_modules` are in a subpackage | `cd` into the package directory (where `package.json` lives) and run `jest-delta` |

## Deterministic Execution Steps for Agents

1. **Locate the test command and target suite**:
   Identify the suite file (e.g. `srv/OperationalCenters/HiringRequest/HiringRequestRepository.test.ts`).
2. **Execute via `jest-delta` with `--json`**:
   ```bash
   jest-delta --json -- npx jest <path-to-test>
   ```
   Or if using npm scripts:
   ```bash
   jest-delta --json -- npm test -- <path-to-test>
   ```
3. **Parse and evaluate the result**:
   - Inspect JSON field `regression`:
     - If `false`: Proceed to commit / review.
     - If `true`: Read `new_failures_details` to inspect failure stack traces and fix the code.

## Output Contract

When reporting test verification to the user, state:
- Number of pre-existing historical failures ignored.
- Number of new failures introduced (must be 0 for passing verification).
- Any pre-existing failures that were serendipitously resolved.
- Confirmation that no `/tmp` clone or manual diff was required.

## References

- CLI tool: `~/.local/bin/jest-delta` -> `~/.dotfiles/scripts/jest-delta`
- Test suite: `~/.dotfiles/scripts/test_jest_delta.py`
