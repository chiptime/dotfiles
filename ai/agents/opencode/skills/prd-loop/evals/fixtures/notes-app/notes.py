"""Toy in-memory notes app (synthetic evaluation fixture).

Planning fixture for prd-loop evaluation. Not a production service.
Confirmed baseline: create, get and list work; update, delete, filter and
export are intentionally missing (see planning/decisions-ledger.md).
"""


class Notes:
    """In-memory note store. IDs are sequential integers starting at 1."""

    def __init__(self):
        self._notes = {}
        self._next_id = 1

    def create(self, title, body=""):
        note = {"id": self._next_id, "title": title, "body": body, "tags": []}
        self._notes[self._next_id] = note
        self._next_id += 1
        return note

    def get(self, note_id):
        return self._notes.get(note_id)

    def list(self):
        return [self._notes[key] for key in sorted(self._notes)]
