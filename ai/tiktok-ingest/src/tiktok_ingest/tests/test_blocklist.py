"""Blocklist permanence: rejected IDs are skipped; force can never override."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tiktok_ingest.tests import fixtures
from tiktok_ingest.contracts import BlocklistEntry
from tiktok_ingest.state import Backlog, BlockedVideoError, Blocklist, StateError


class BlocklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = fixtures.make_state_root(self._tmp.name)

    def _reject(self, video_id: str = "1111111111111111111") -> None:
        Blocklist(self.state).reject(
            BlocklistEntry(
                video_id=video_id,
                rejected_at=fixtures.FIXED_NOW,
                reason="operator rejected the backlog entry",
                source="backlog",
            )
        )

    def test_rejected_id_is_blocked_and_persists_across_instances(self) -> None:
        self._reject()
        # A brand-new instance reads from disk: permanence across runs.
        fresh = Blocklist(self.state)
        self.assertTrue(fresh.is_blocked("1111111111111111111"))
        self.assertFalse(fresh.is_blocked("2222222222222222222"))

    def test_ensure_not_blocked_raises_for_rejected_ids(self) -> None:
        self._reject()
        with self.assertRaises(BlockedVideoError):
            Blocklist(self.state).ensure_not_blocked("1111111111111111111")
        Blocklist(self.state).ensure_not_blocked("2222222222222222222")

    def test_force_flag_can_never_override_rejection(self) -> None:
        self._reject()
        blocklist = Blocklist(self.state)
        path = self.state.blocklist_path
        before = path.read_text(encoding="utf-8")
        for force in (False, True):
            with self.subTest(force=force):
                with self.assertRaises(StateError):
                    blocklist.unblock("1111111111111111111", force=force)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertTrue(blocklist.is_blocked("1111111111111111111"))

    def test_reject_is_idempotent_and_first_entry_wins(self) -> None:
        self._reject()
        Blocklist(self.state).reject(
            BlocklistEntry(
                video_id="1111111111111111111",
                rejected_at="2026-09-19T00:00:00Z",
                reason="a different, later reason",
            )
        )
        entries = Blocklist(self.state).entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries["1111111111111111111"].reason, "operator rejected the backlog entry")

    def test_blocked_video_cannot_reenter_backlog_via_resume_flow(self) -> None:
        """Processing skips rejected IDs: ensure_not_blocked gates emission."""
        self._reject()
        backlog = Backlog(self.state)
        entry = fixtures.sample_backlog_entry()
        entry_id = entry.id
        Blocklist(self.state).reject(
            BlocklistEntry(
                video_id=entry_id,
                rejected_at=fixtures.FIXED_NOW,
                reason="operator rejected before re-ingest",
            )
        )
        with self.assertRaises(BlockedVideoError):
            Blocklist(self.state).ensure_not_blocked(entry_id)
        self.assertFalse(backlog.has(entry_id))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
