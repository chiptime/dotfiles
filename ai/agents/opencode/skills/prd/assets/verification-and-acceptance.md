# Verification & Acceptance Template for Technical PRDs

Apply this template when the PRD describes installation, infrastructure, or deployable/service behavior. Principle: every requirement is verified from the perspective of a real user — clean environment, no maintainer tribal knowledge, following only the published documentation. "Works on my machine" is not verification.

Adapted from the reference implementation: agent-tts `docs/prds/AT-11-instalable-first-run.md` (revision 2).

## 1. Verification levels and trigger rules

| Level | What | When it fires | Gate |
|---|---|---|---|
| **V1 — Automated tests** | Tests for installer/infra logic: path derivation, config/template generation, non-interactive flags, stubbed contracts | Every change touching the install/deploy surface (enumerate the globs: e.g. `deploy/`, `scripts/`, `bin/`, first-run, install docs) | CI / pre-commit |
| **V2 — Automated clean-room smoke** | Versioned harness (e.g. `scripts/acceptance/clean-install.sh`): sandboxed empty `HOME` (no dotfiles, no package managers, no pre-seeded secrets), runs the documented steps VERBATIM and asserts outcomes | Same trigger as V1 | Mandatory to approve the change |
| **V3 — Manual UAT as real user** | A human executes the acceptance checklist on a clean machine/VM following only the docs, timed, frictions recorded | Per release, and whenever a PR changes the documented install/first-run flow | Signed checklist before archiving the PRD |

Clean-room definition (V2): fresh `HOME`, minimal PATH, no prior secrets, network allowed only to the origins the documentation cites. The harness MUST be versioned in the repo so sampling is reproducible and auditable.

## 2. Gherkin acceptance scenarios

Write one scenario per verifiable outcome; tag each with the levels that check it, e.g. `[V2]` or `[V1+V2]`. Cover at minimum:

- Happy path install/first-run from a clean environment (non-standard install location included).
- Secrets handling: stored outside any repo, correct mode, never echoed in logs/argv/output.
- Explicit-consent optional downloads (and their degraded-but-functional rejection path).
- Machine-decoupling assertions (grep-level: no absolute personal paths, no hardcoded prefixes/domains in versioned files).
- Idempotent re-install preserving state.
- Diagnostic path: deliberately break one thing, verify the doctor/self-check names it with the exact fix command.
- Timed end-to-end UAT (V3) with the time budget from the RNFs.

Skeleton:

```gherkin
Scenario: <Outcome> (RF-xx-n, US-xx-n) [V2]
  Given a clean-room environment with no trace of the product
  And the repo cloned at a non-standard, arbitrary path
  When I run the documented install/first-run steps
  Then <observable outcome>
  And <no manual file edit was required>
```

## 3. RF-to-verification traceability matrix

| RF | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| RF-xx-1 | ✔ | ✔ | ✔ | <scenario name> |

Every FR row must carry at least one ✔. An FR with an empty row is a specification defect, not a testing gap.

## 4. Success metrics

Reference the verification section instead of duplicating it; list only outcome metrics (e.g. clean-machine install under the time budget, zero legacy references in the versioned tree, zero warnings from the platform's own listing command).
