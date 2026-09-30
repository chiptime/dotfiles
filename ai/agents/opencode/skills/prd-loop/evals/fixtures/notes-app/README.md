# notes-app (synthetic prd-loop evaluation fixture)

Tiny toy repository used as inspectable source facts for prd-loop evaluation. It is
user-supplied fixture data, not a real project: the "confirmed decisions" live in
`planning/decisions-ledger.md` and are fixture facts with that uncertainty label, not
claims about any real environment.

Layout:

- `notes.py` — the app (in-memory notes)
- `test_notes.py` — tests
- `pyproject.toml` — pytest config
- `planning/decisions-ledger.md` — confirmed decisions, verification policy, non-goals
