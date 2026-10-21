"""Unit tests for unified ingestion entry point (`tiktok_ingest.ingest`)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest.collection import CollectionError
from tiktok_ingest.contracts import utc_now_iso
from tiktok_ingest.ingest import IngestError, run_ingest
from tiktok_ingest.state import StateRoot


class TestIngest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateRoot(Path(self.tmp.name))
        self.state.ensure_layout()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_invalid_url_raises_ingest_error(self) -> None:
        with self.assertRaises(IngestError) as ctx:
            run_ingest("https://google.com", state=self.state, dry_run=True)
        self.assertIn("unsupported TikTok URL", str(ctx.exception))

    def test_collection_url_dry_run_dispatches_cleanly(self) -> None:
        exit_code, report = run_ingest(
            "https://www.tiktok.com/@author/collection/cool-stuff-7123456789012345678",
            state=self.state,
            dry_run=True,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["mode"], "dry-run")
        self.assertIn("plan", report)

    def test_collection_url_dry_run_with_rescan(self) -> None:
        exit_code, report = run_ingest(
            "https://www.tiktok.com/@author/collection/cool-stuff-7123456789012345678",
            state=self.state,
            dry_run=True,
            rescan=True,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["mode"], "dry-run")
        self.assertTrue(report["plan"]["rescan"])

    def test_collection_url_resumes_without_browser_when_scan_exists(self) -> None:
        from unittest import mock
        from tiktok_ingest.tests.test_collection_run import seed_scan_for_run

        url = "https://www.tiktok.com/@fixture_author/collection/run-col"
        seed_scan_for_run(self.state, [1, 2])

        with mock.patch("tiktok_ingest.collection_run.run_browser_inventory") as browser_mock:
            exit_code, report = run_ingest(
                url,
                state=self.state,
                dry_run=False,
                consent=mock.Mock(ask=mock.Mock(return_value=False)),
                rescan=False,
            )
            # Browser inventory was NOT called because of resume
            browser_mock.assert_not_called()
            self.assertTrue(report["browser"]["reused"])

    def test_collection_url_rescan_forces_browser_inventory(self) -> None:
        from unittest import mock
        from tiktok_ingest.tests.test_collection_run import seed_scan_for_run

        url = "https://www.tiktok.com/@fixture_author/collection/run-col"
        seed_scan_for_run(self.state, [1, 2])

        fake_browser_report = mock.Mock(
            to_dict=mock.Mock(return_value={"fake": "report"})
        )
        with mock.patch(
            "tiktok_ingest.collection_run.run_browser_inventory",
            return_value=fake_browser_report,
        ) as browser_mock:
            exit_code, report = run_ingest(
                url,
                state=self.state,
                dry_run=False,
                consent=mock.Mock(ask=mock.Mock(return_value=False)),
                rescan=True,
            )
            # Browser inventory WAS called because rescan=True
            browser_mock.assert_called_once()
            self.assertEqual(report["browser"], {"fake": "report"})

    def test_direct_video_url_dry_run_dispatches_cleanly(self) -> None:
        video_url = "https://www.tiktok.com/@author/video/7123456789012345678"
        exit_code, report = run_ingest(
            video_url,
            state=self.state,
            dry_run=True,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual([item["id"] for item in report["selected"]], ["7123456789012345678"])
        # Inventory was seeded
        from tiktok_ingest.state import InventoryStore
        entries = InventoryStore(self.state).load()
        self.assertIn("7123456789012345678", [e.stable_id for e in entries])


if __name__ == "__main__":
    unittest.main()
