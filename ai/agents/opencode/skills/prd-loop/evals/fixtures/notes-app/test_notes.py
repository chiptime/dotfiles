"""Tests for the toy notes fixture (synthetic evaluation fixture)."""

from notes import Notes


def test_create_assigns_sequential_ids():
    notes = Notes()
    first = notes.create("first")
    second = notes.create("second")
    assert first["id"] == 1
    assert second["id"] == 2


def test_get_returns_created_note():
    notes = Notes()
    created = notes.create("title", body="body text")
    assert notes.get(created["id"])["body"] == "body text"


def test_get_missing_returns_none():
    assert Notes().get(999) is None


def test_list_is_sorted_by_id():
    notes = Notes()
    notes.create("a")
    notes.create("b")
    assert [note["title"] for note in notes.list()] == ["a", "b"]
