"""Atomic-write behavior: a failed write must leave the original intact."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tiktok_ingest.tests import fixtures
from tiktok_ingest.state import (
    DEFAULT_STATE_ROOT,
    Backlog,
    StateRoot,
    atomic_write_bytes,
    atomic_write_json,
)


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _no_temp_files(self, directory: Path) -> None:
        leftovers = [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [], "temporary files left behind")

    def test_successful_write_replaces_content(self) -> None:
        path = self.dir / "state.json"
        atomic_write_bytes(path, b"first")
        atomic_write_bytes(path, b"second")
        self.assertEqual(path.read_bytes(), b"second")
        self._no_temp_files(self.dir)

    def test_failed_replace_leaves_original_intact(self) -> None:
        path = self.dir / "state.json"
        atomic_write_bytes(path, b"original")
        with mock.patch(
            "tiktok_ingest.state.os.replace", side_effect=OSError("killed before replace")
        ):
            with self.assertRaises(OSError):
                atomic_write_bytes(path, b"new-content")
        self.assertEqual(path.read_bytes(), b"original", "partial file replaced original")
        self._no_temp_files(self.dir)

    def test_failed_fsync_leaves_original_intact(self) -> None:
        path = self.dir / "state.json"
        atomic_write_bytes(path, b"original")
        with mock.patch(
            "tiktok_ingest.state.os.fsync", side_effect=OSError("disk gone")
        ):
            with self.assertRaises(OSError):
                atomic_write_bytes(path, b"new-content")
        self.assertEqual(path.read_bytes(), b"original")
        self._no_temp_files(self.dir)

    def test_failed_write_into_new_path_creates_nothing(self) -> None:
        path = self.dir / "nested" / "fresh.json"
        with mock.patch(
            "tiktok_ingest.state.os.replace", side_effect=OSError("killed before replace")
        ):
            with self.assertRaises(OSError):
                atomic_write_bytes(path, b"payload")
        self.assertFalse(path.exists())
        self._no_temp_files(path.parent)

    def test_atomic_json_round_trip(self) -> None:
        path = self.dir / "doc.json"
        atomic_write_json(path, {"root": "tecnología", "n": 1})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"root": "tecnología", "n": 1})


class BacklogDuplicateGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_duplicate_id_is_refused_atomically(self) -> None:
        state = fixtures.make_state_root(self._tmp.name)
        backlog = Backlog(state)
        entry = fixtures.sample_backlog_entry()
        self.assertEqual(backlog.append(entry), "appended")
        self.assertEqual(backlog.append(entry), "skipped_duplicate")
        self.assertEqual(len(backlog.entries()), 1)
        self.assertEqual(backlog.ids(), {entry.id})

    def test_run_dir_confined_to_state_root(self) -> None:
        state = fixtures.make_state_root(self._tmp.name)
        run_dir = state.run_dir("2026-09-18T12-00Z", "7683273567433248022")
        self.assertTrue(run_dir.is_relative_to(state.root))
        with self.assertRaises(Exception):
            state.run_dir("../escape", "id")
        with self.assertRaises(Exception):
            state.run_dir("run", "")

    def test_state_root_is_configurable_and_not_the_default(self) -> None:
        state = fixtures.make_state_root(self._tmp.name)
        self.assertNotEqual(state.root, DEFAULT_STATE_ROOT)
        self.assertEqual(
            state.backlog_path, state.root / "backlog.jsonl"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
