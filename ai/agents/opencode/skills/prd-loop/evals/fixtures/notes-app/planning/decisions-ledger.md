# Decisions ledger — toy notes app (synthetic evaluation fixture)

> User-supplied planning constraints for evaluation. This is not a real
> production repository; every "confirmed" item below is fixture data.

## Confirmed product decisions

- Product: a tiny in-memory notes app for personal use. No persistence, no
  auth, no network (non-goal in every milestone).
- M1 (approved): finish note CRUD — update and delete are missing in
  `notes.py` and must complete the existing create/get/list baseline.
- M2 (approved): filter notes by tag and by free-text match on title/body.
- M3 (approved): export the current note list as Markdown or JSON (choice
  fixed per export call, no config file).
- Baseline behavior (create/get/list) must not change.

## Verification policy (user-supplied for this fixture)

- Runner: pytest via `python -m pytest` (config in `pyproject.toml`).
- Coverage: line coverage >= 80% measured with pytest-cov on Linux when the
  plugin is installed in the run environment. If pytest-cov is absent, report
  the environment gap; do not fabricate a coverage number. Branch coverage is
  not required by this policy.

## Non-goals

- No network, no persistence, no authentication, no UI.
