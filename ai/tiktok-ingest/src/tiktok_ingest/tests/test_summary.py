"""``summary`` tests: offline, read-only, deterministic fixtures only.

The summary command is a READ-ONLY view over the durable product stores
(Processed + meta.json manifests + Backlog + the collection scan) and over a
previously saved collection-run report. Nothing here touches a browser,
the network, a GPU, a container or a model.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tiktok_ingest import cli, summary
from tiktok_ingest.collection import (
    CollectionScanStore,
    extract_page_items,
    merge_observation,
    parse_collection_url,
    sync_inventory_from_scan,
)
from tiktok_ingest.state import StateRoot
from tiktok_ingest.tests.fixtures import FIXED_NOW
from tiktok_ingest.tests.test_guided import fake_id, seed_job

COLLECTION_URL = "https://www.tiktok.com/@fixture_author/collection/summary-col"
REF = parse_collection_url(COLLECTION_URL)
KEY = REF.collection_key

AUTHOR = "fixture_author"


def canonical(video_id: str) -> str:
    return f"https://www.tiktok.com/@fixture_author/video/{video_id}"


def seed_collection(state: StateRoot, video_ns: list[int]) -> None:
    """Persist scan state through the REAL merge path (no shortcuts)."""
    links = "".join(
        f'<a href="/@fixture_author/video/{fake_id(n)}">item</a>'
        for n in video_ns
    )
    html = (
        "<!DOCTYPE html><html><body>"
        f'<div data-testid="collection-container">{links}</div>'
        "</body></html>"
    )
    observation = extract_page_items(html, "data-testid", "collection-container")
    merged = merge_observation(
        None,
        REF,
        observation,
        declared_count=len(video_ns),
        end_evidence="fixture end-of-list marker observed",
        stop_reason=None,
        captured_at=FIXED_NOW,
        container_attr="data-testid",
        container_value="collection-container",
    )
    CollectionScanStore(state).save(merged)
    sync_inventory_from_scan(state, merged)


def saved_report(video_ids: list[str]) -> dict:
    """A minimal, well-formed collection-run report snapshot."""
    stages = {
        "fetch": "complete",
        "prepare": "complete",
        "audio": "complete",
        "vision": "pending",
        "synthesis": "pending",
        "verify": "pending",
    }
    return {
        "mode": "execute",
        "collection_key": KEY,
        "collection_url": COLLECTION_URL,
        "run_plan": {"canonical_urls": {vid: canonical(vid) for vid in video_ids}},
        "batches": [
            {
                "ids": list(video_ids),
                "selected": [
                    {
                        "id": vid,
                        "classification": "new_processable",
                        "canonical_url": canonical(vid),
                        "stages": dict(stages),
                        "run_dir": None,
                    }
                    for vid in video_ids
                ],
            }
        ],
    }


def write_report(state: StateRoot, key: str, name: str, report: dict) -> Path:
    path = state.root / "reports" / key / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class SummaryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()
        self.root_arg = str(self.state.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class TestHumanTable(SummaryTestCase):
    def test_one_row_per_item_with_derived_next(self) -> None:
        seed_collection(self.state, [1, 2])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare", "audio"))
        seed_job(
            self.state,
            fake_id(2),
            complete=("fetch", "prepare", "audio", "vision", "synthesis", "verify"),
        )
        code, out, err = run_cli(
            ["summary", "--collection", COLLECTION_URL, "--state-root", self.root_arg]
        )
        self.assertEqual(code, 0, err)
        # Header names every stage column, in processing order.
        for column in ("id", "author", "fetch", "prepare", "audio",
                       "vision", "synthesis", "verify", "backlog", "next"):
            self.assertIn(column, out)
        self.assertIn(fake_id(1), out)
        self.assertIn(fake_id(2), out)
        # The real pending reason is surfaced honestly.
        self.assertIn("vision pending (audio saved)", out)
        self.assertIn("complete", out)

    def test_author_is_derived_from_canonical_url(self) -> None:
        seed_collection(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch",))
        code, out, err = run_cli(
            ["summary", "--collection", COLLECTION_URL, "--state-root", self.root_arg]
        )
        self.assertEqual(code, 0, err)
        self.assertIn(AUTHOR, out)


class TestJsonMode(SummaryTestCase):
    def test_json_is_a_structured_object(self) -> None:
        seed_collection(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare", "audio"))
        code, out, err = run_cli(
            [
                "summary", "--collection", COLLECTION_URL,
                "--json", "--state-root", self.root_arg,
            ]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["mode"], "live")
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["id"], fake_id(1))
        self.assertEqual(item["author"], AUTHOR)
        self.assertEqual(item["stages"]["audio"], "complete")
        self.assertEqual(item["stages"]["vision"], "pending")
        self.assertEqual(item["next"], "vision pending (audio saved)")
        self.assertEqual(item["backlog"], "none")

    def test_no_collection_summarizes_all_processed_items(self) -> None:
        seed_job(self.state, fake_id(10), complete=("fetch", "prepare"))
        seed_job(
            self.state,
            fake_id(11),
            complete=("fetch", "prepare", "audio", "vision", "synthesis", "verify"),
        )
        code, out, err = run_cli(["summary", "--json", "--state-root", self.root_arg])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIsNone(data["collection"])
        ids = {item["id"] for item in data["items"]}
        self.assertEqual(ids, {fake_id(10), fake_id(11)})
        by_id = {item["id"]: item for item in data["items"]}
        self.assertEqual(by_id[fake_id(10)]["next"], "audio pending")
        self.assertEqual(by_id[fake_id(11)]["next"], "complete")


class TestCollectionFiltering(SummaryTestCase):
    def test_collection_limits_to_plan_items(self) -> None:
        seed_collection(self.state, [1, 2])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare"))
        seed_job(self.state, fake_id(3), complete=("fetch", "prepare"))
        code, out, err = run_cli(
            ["summary", "--collection", KEY, "--json", "--state-root", self.root_arg]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        ids = {item["id"] for item in data["items"]}
        self.assertEqual(ids, {fake_id(1), fake_id(2)})
        self.assertNotIn(fake_id(3), ids)
        self.assertEqual(data["collection"]["key"], KEY)
        self.assertIn("counts", data["collection"])

    def test_unknown_collection_key_fails_cleanly(self) -> None:
        code, out, err = run_cli(
            ["summary", "--collection", "deadbeefdeadbeef", "--state-root", self.root_arg]
        )
        self.assertEqual(code, 2)
        self.assertIn("summary", err)


class TestReportMode(SummaryTestCase):
    def test_report_latest_reads_saved_report(self) -> None:
        seed_collection(self.state, [1])
        write_report(
            self.state, KEY, "2026-10-12T09-55-16Z.json",
            saved_report([fake_id(1)]),
        )
        code, out, err = run_cli(
            [
                "summary", "--collection", KEY, "--report", "latest",
                "--json", "--state-root", self.root_arg,
            ]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["mode"], "report")
        self.assertEqual([item["id"] for item in data["items"]], [fake_id(1)])
        self.assertEqual(data["items"][0]["author"], AUTHOR)
        self.assertEqual(data["items"][0]["next"], "vision pending (audio saved)")

    def test_report_explicit_path(self) -> None:
        path = Path(self.tmp.name) / "saved.json"
        path.write_text(json.dumps(saved_report([fake_id(5)])), encoding="utf-8")
        code, out, err = run_cli(
            ["summary", "--report", str(path), "--json", "--state-root", self.root_arg]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual([item["id"] for item in data["items"]], [fake_id(5)])

    def test_report_latest_without_collection_fails_cleanly(self) -> None:
        code, out, err = run_cli(
            ["summary", "--report", "latest", "--state-root", self.root_arg]
        )
        self.assertEqual(code, 2)
        self.assertIn("summary", err)


class TestReadOnly(SummaryTestCase):
    def test_summary_writes_nothing(self) -> None:
        seed_collection(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare", "audio"))
        before = snapshot(self.state.root)
        run_cli(["summary", "--collection", COLLECTION_URL, "--state-root", self.root_arg])
        run_cli(["summary", "--json", "--state-root", self.root_arg])
        after = snapshot(self.state.root)
        self.assertEqual(before, after)


class TestGuidedReportSummary(SummaryTestCase):
    """The end-of-run banner reuses the summary row/next derivation."""

    def _report(self, video_ids: list[str]) -> dict:
        stages = {
            "fetch": "complete",
            "prepare": "complete",
            "audio": "complete",
            "vision": "pending",
            "synthesis": "pending",
            "verify": "pending",
        }
        return {
            "mode": "execute",
            "state_root": str(self.state.root),
            "selected": [
                {
                    "id": vid,
                    "classification": "new_processable",
                    "canonical_url": canonical(vid),
                    "stages": dict(stages),
                    "run_dir": None,
                }
                for vid in video_ids
            ],
        }

    def test_guided_report_becomes_one_row_per_selected_item(self) -> None:
        data = summary.guided_report_summary(self._report([fake_id(1), fake_id(2)]))
        self.assertEqual([item["id"] for item in data["items"]], [fake_id(1), fake_id(2)])
        first = data["items"][0]
        self.assertEqual(first["author"], AUTHOR)
        self.assertEqual(first["backlog"], "n/a")
        self.assertEqual(first["next"], "vision pending (audio saved)")
        table = summary.render_table(data)
        self.assertIn("next", table)
        self.assertIn(fake_id(1), table)

    def test_empty_selection_renders_a_header_only_table(self) -> None:
        data = summary.guided_report_summary({"selected": []})
        table = summary.render_table(data)
        self.assertIn("id", table)
        self.assertNotIn("vision pending", table)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
