"""Read-only JSON view tests for the Phase 1 web interface (S7).

Every test drives the REAL loopback server (``create_server`` on port 0
with ``register_views`` applied) over ``http.client`` against a temp
state root seeded through the existing fixture helpers. Zero network
beyond loopback, zero writes: the read-only proof hashes the whole
state root before and after hitting every endpoint.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

from tiktok_ingest.contracts import (
    JobManifest,
    ProcessedEntry,
    StageOutcome,
    StageRecord,
    StageResult,
)
from tiktok_ingest.fetch import video_status
from tiktok_ingest.review import list_entries
from tiktok_ingest.state import Backlog, Processed, StateRoot
from tiktok_ingest.tests.fixtures import (
    FIXED_NOW,
    MEDIA_SHA256,
    sample_backlog_entry,
)
from tiktok_ingest.tests.test_guided import (
    COLLECTION_URL,
    REF,
    fake_id,
    seed_job,
    seed_scan,
)
from tiktok_ingest.tests.test_summary import saved_report, write_report
from tiktok_ingest.web.server import create_server
from tiktok_ingest.web.views import register_views

SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:api_key|apikey|token|secret|password|passwd|credential)(?:_|$)",
    re.IGNORECASE,
)

TS_NAME = "2026-10-12T09-55-16Z.json"
TS_NAME_OLDER = "2026-10-11T09-55-16Z.json"


def seed_blocked_job(state: StateRoot, video_id: str) -> Path:
    """Seed one job whose audio stage recorded a ``blocked`` outcome."""
    run_id = f"run-{video_id[-6:]}"
    run_dir = state.run_dir(run_id, video_id)
    manifest = JobManifest(
        video_id=video_id,
        media_sha256=MEDIA_SHA256,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )
    manifest.extraction = {"outcome": "complete", "reason": "fixture"}
    for stage in ("prepare",):
        manifest.stages[stage] = StageRecord(
            stage=stage,
            result=StageResult(outcome=StageOutcome.COMPLETE, reason="fixture"),
            fingerprint={"stage": stage, "input_sha256": MEDIA_SHA256, "versions": {}},
            input_sha256=MEDIA_SHA256,
        )
    manifest.stages["audio"] = StageRecord(
        stage="audio",
        result=StageResult(
            outcome=StageOutcome.BLOCKED, reason="fixture: whisper unavailable"
        ),
        input_sha256=MEDIA_SHA256,
    )
    (run_dir / "meta.json").write_text(
        json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
    )
    Processed(state).record(
        ProcessedEntry(
            video_id=video_id,
            media_sha256=MEDIA_SHA256,
            completed_at=FIXED_NOW,
            stage_fingerprints={},
            artifacts=[f"runs/{run_id}/{video_id}"],
        )
    )
    return run_dir


def guided_report(video_ids: list[str]) -> dict:
    """The saved-report fixture rewritten for THIS module's collection."""
    document = saved_report(video_ids)
    document["collection_key"] = REF.collection_key
    return document


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ViewsTestCase(unittest.TestCase):
    """Fresh temp state + real loopback server with the views registered."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()
        self.server = create_server(self.state, port=0)
        register_views(self.server.app)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            headers = {key.lower(): value for key, value in response.getheaders()}
        finally:
            conn.close()
        return response.status, headers, body

    def get_json(self, path: str) -> tuple[int, dict]:
        status, _headers, body = self.get(path)
        return status, json.loads(body.decode("utf-8"))


class TestCollections(ViewsTestCase):
    def test_list_shows_scan_facts_and_plan_counts(self) -> None:
        seed_scan(self.state, [1, 2])
        seed_job(
            self.state,
            fake_id(1),
            complete=("fetch", "prepare", "audio", "vision", "synthesis", "verify"),
            fingerprints=(
                "fetch", "prepare", "audio", "vision", "synthesis", "verify", "emit",
            ),
        )
        status, data = self.get_json("/api/collections")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["collections"]), 1)
        entry = data["collections"][0]
        self.assertEqual(entry["key"], REF.collection_key)
        self.assertEqual(entry["url"], COLLECTION_URL)
        self.assertEqual(entry["declared_count"], 2)
        self.assertEqual(entry["observed_count"], 2)
        self.assertEqual(entry["status"], "complete")
        self.assertEqual(
            entry["end_of_list_evidence"], "fixture end-of-list marker observed"
        )
        self.assertEqual(entry["access_markers"], [])
        self.assertTrue(entry["last_capture_at"])
        self.assertEqual(entry["plan"]["counts"]["processed_complete"], 1)
        self.assertEqual(entry["plan"]["counts"]["new_processable"], 1)

    def test_detail_includes_item_classifications(self) -> None:
        seed_scan(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch",))
        status, data = self.get_json(f"/api/collections/{REF.collection_key}")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["id"], fake_id(1))
        self.assertEqual(item["classification"], "partial_resumable")
        self.assertTrue(item["url"])
        self.assertTrue(item["detail"])

    def test_detail_unknown_and_malformed_keys_are_404(self) -> None:
        self.assertEqual(self.get("/api/collections/deadbeefdeadbeef")[0], 404)
        self.assertEqual(self.get("/api/collections/not-hex-at-all")[0], 404)
        self.assertEqual(self.get("/api/collections/..")[0], 404)


class TestRuns(ViewsTestCase):
    def test_runs_newest_first_with_summary_fields(self) -> None:
        write_report(self.state, REF.collection_key, TS_NAME_OLDER, guided_report([fake_id(1)]))
        write_report(self.state, REF.collection_key, TS_NAME, guided_report([fake_id(2)]))
        status, data = self.get_json("/api/runs")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["runs"]), 2)
        first = data["runs"][0]
        self.assertEqual(first["report"], TS_NAME)
        self.assertEqual(first["collection_key"], REF.collection_key)
        self.assertEqual(len(first["items"]), 1)
        item = first["items"][0]
        for field in ("id", "author", "stages", "backlog", "next"):
            self.assertIn(field, item)
        self.assertEqual(item["id"], fake_id(2))
        self.assertEqual(item["backlog"], "n/a")
        self.assertEqual(item["next"], "vision pending (audio saved)")

    def test_run_report_returns_saved_document(self) -> None:
        document = guided_report([fake_id(1)])
        document["preflight"] = {
            "text_api_origin": "https://api.fixture.example",
            "text_api_configured": True,
            "text_api_key": "sk-fixture-decoy",
        }
        write_report(self.state, REF.collection_key, TS_NAME, document)
        status, data = self.get_json(
            f"/api/runs/{REF.collection_key}/{TS_NAME}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["collection_key"], REF.collection_key)
        self.assertEqual(data["mode"], "execute")
        # The URL origin survives; the key-like field is dropped.
        self.assertEqual(
            data["preflight"]["text_api_origin"], "https://api.fixture.example"
        )
        self.assertNotIn("text_api_key", data["preflight"])
        self.assertNotIn("sk-fixture-decoy", json.dumps(data))

    def test_run_report_rejects_non_timestamp_names_and_traversal(self) -> None:
        write_report(self.state, REF.collection_key, TS_NAME, guided_report([fake_id(1)]))
        key = REF.collection_key
        for bad in (
            "latest",                                  # not a timestamp file
            "2026-10-12T09-55-16Z",                    # missing .json
            "x.json",                                  # not the writer pattern
            "..%2F..%2Fcollections%2F" + key + ".json",  # traversal payload
            "9999-99-99T99-99-99Z.json",               # wrong shape entirely
        ):
            self.assertEqual(self.get(f"/api/runs/{key}/{bad}")[0], 404, bad)
        # Unknown collection key and unknown (valid-shaped) report are 404 too.
        self.assertEqual(
            self.get(f"/api/runs/deadbeefdeadbeef/{TS_NAME}")[0], 404
        )
        self.assertEqual(
            self.get(f"/api/runs/{key}/2026-01-01T00-00-00Z.json")[0], 404
        )


class TestBlocks(ViewsTestCase):
    def test_blocks_from_manifest_outcomes(self) -> None:
        seed_scan(self.state, [1])
        seed_blocked_job(self.state, fake_id(1))
        seed_job(self.state, fake_id(2), complete=("fetch",))
        status, data = self.get_json("/api/blocks")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["items"]), 1)
        blocked = data["items"][0]
        self.assertEqual(blocked["id"], fake_id(1))
        # Outcomes pass through unchanged (P1-FR-03).
        self.assertEqual(blocked["blocked_stages"], {"audio": "blocked"})
        self.assertEqual(blocked["next"], "audio: blocked")

    def test_blocks_from_latest_report_hard_stop(self) -> None:
        document = guided_report([fake_id(1)])
        document["hard_stop_reason"] = (
            "isolated GPU cleanup unverified: ALL further progress blocked"
        )
        document["batches"][0]["failures"] = [
            {"id": fake_id(1), "stage": "audio", "reason": "fixture failure"}
        ]
        document["batches"][0]["cleanup_notes"] = ["fixture cleanup note"]
        write_report(self.state, REF.collection_key, TS_NAME, document)
        status, data = self.get_json("/api/blocks")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["runs"]), 1)
        run_block = data["runs"][0]
        self.assertEqual(run_block["collection_key"], REF.collection_key)
        self.assertEqual(run_block["report"], TS_NAME)
        self.assertEqual(
            run_block["hard_stop_reason"],
            "isolated GPU cleanup unverified: ALL further progress blocked",
        )
        self.assertEqual(run_block["failures"][0]["stage"], "audio")
        self.assertEqual(run_block["cleanup_notes"], ["fixture cleanup note"])


class TestVideos(ViewsTestCase):
    def test_status_parity_with_cli(self) -> None:
        run_dir = seed_job(
            self.state, fake_id(1), complete=("fetch", "prepare"), evidence=True
        )
        (run_dir / "synthesis.json").write_text('{"fixture": true}', encoding="utf-8")
        (run_dir / "verification.json").write_text("{}", encoding="utf-8")
        status, data = self.get_json(f"/api/videos/{fake_id(1)}")
        self.assertEqual(status, 200)
        expected = video_status(fake_id(1), state=self.state)
        served = dict(data)
        served_items = [dict(item) for item in served["items"]]
        for item in served_items:
            item.pop("evidence", None)
        self.assertEqual(served_items, expected["items"])
        self.assertEqual(served["inventory_size"], expected["inventory_size"])
        self.assertEqual(served["state_root"], expected["state_root"])
        # The evidence list names exactly the allow-listed artifacts present.
        self.assertEqual(
            sorted(served["items"][0]["evidence"]),
            ["audio.md", "synthesis.json", "verification.json", "video.md"],
        )

    def test_evidence_allowlist_serving(self) -> None:
        run_dir = seed_job(
            self.state, fake_id(1), complete=("fetch",), evidence=True
        )
        status, headers, body = self.get(
            f"/api/videos/{fake_id(1)}/evidence/video.md"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/plain; charset=utf-8")
        self.assertIn(b"video.md", body)
        (run_dir / "synthesis.json").write_text('{"fixture": true}', encoding="utf-8")
        status, headers, _body = self.get(
            f"/api/videos/{fake_id(1)}/evidence/synthesis.json"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            headers["content-type"], "application/json; charset=utf-8"
        )

    def test_evidence_rejects_traversal_and_unknown(self) -> None:
        seed_job(self.state, fake_id(1), complete=("fetch",), evidence=True)
        vid = fake_id(1)
        for bad_name in (
            "meta.json",               # real file, not allow-listed evidence
            "..%2F..%2Fprocessed.json",  # traversal payload
            "%2e%2e",                  # encoded dot-dot
        ):
            self.assertEqual(
                self.get(f"/api/videos/{vid}/evidence/{bad_name}")[0], 404,
                bad_name,
            )
        # A NUL byte is rejected by the server foundation before routing.
        self.assertEqual(
            self.get(f"/api/videos/{vid}/evidence/video.md%00")[0], 400
        )
        for bad_id in ("..", "%2e%2e", "runs"):
            self.assertEqual(
                self.get(f"/api/videos/{bad_id}/evidence/video.md")[0], 404,
                bad_id,
            )
        # Known id, allow-listed name, but no run dir on disk.
        self.assertEqual(
            self.get(f"/api/videos/{fake_id(9)}/evidence/video.md")[0], 404
        )


class TestBacklog(ViewsTestCase):
    def test_backlog_parity_with_review_list(self) -> None:
        Backlog(self.state).append(sample_backlog_entry())
        status, data = self.get_json("/api/backlog")
        self.assertEqual(status, 200)
        self.assertEqual(data["entries"], list_entries(self.state))
        status, data = self.get_json("/api/backlog?status=pending")
        self.assertEqual(status, 200)
        self.assertEqual(
            data["entries"], list_entries(self.state, "pending")
        )
        self.assertEqual(len(data["entries"]), 1)
        entry = data["entries"][0]
        self.assertEqual(entry["id"], "7683273567433248022")
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(len(entry["entry_sha256"]), 64)

    def test_backlog_invalid_status_is_400(self) -> None:
        self.assertEqual(self.get("/api/backlog?status=finished")[0], 400)


class TestEmptyState(ViewsTestCase):
    """A missing state root serves empty lists, never errors (P1-FR-14)."""

    def setUp(self) -> None:
        super().setUp()
        # Replace the seeded state with one whose root does not exist.
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        missing = StateRoot(Path(self.tmp.name) / "missing")
        self.server = create_server(missing, port=0)
        register_views(self.server.app)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def test_missing_state_root_returns_empty_lists(self) -> None:
        for path, list_key in (
            ("/api/collections", "collections"),
            ("/api/runs", "runs"),
            ("/api/blocks", "items"),
            ("/api/backlog", "entries"),
        ):
            status, data = self.get_json(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(data[list_key], [], path)
        status, data = self.get_json("/api/blocks")
        self.assertEqual(data["runs"], [])
        # Unknown video id reports the same honest shape as `ti status`.
        status, data = self.get_json("/api/videos/1234567890123456789")
        self.assertEqual(status, 200)
        self.assertFalse(data["items"][0]["processed"])


class TestSafety(ViewsTestCase):
    def _sweep(self) -> list[dict]:
        seed_scan(self.state, [1])
        seed_job(
            self.state, fake_id(1), complete=("fetch", "prepare"), evidence=True
        )
        Backlog(self.state).append(sample_backlog_entry())
        document = guided_report([fake_id(1)])
        document["preflight"] = {"text_api_key": "sk-fixture-decoy"}
        write_report(self.state, REF.collection_key, TS_NAME, document)
        paths = [
            "/api/collections",
            f"/api/collections/{REF.collection_key}",
            "/api/runs",
            f"/api/runs/{REF.collection_key}/{TS_NAME}",
            "/api/blocks",
            f"/api/videos/{fake_id(1)}",
            f"/api/videos/{fake_id(1)}/evidence/video.md",
            "/api/backlog",
            "/api/backlog?status=pending",
        ]
        payloads = []
        for path in paths:
            _status, _headers, body = self.get(path)
            try:
                payloads.append(json.loads(body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass  # plain-text evidence responses carry no JSON keys
        return payloads

    @staticmethod
    def _key_walk(value, found: list[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if SECRET_KEY_RE.search(str(key)):
                    found.append(str(key))
                TestSafety._key_walk(item, found)
        elif isinstance(value, list):
            for item in value:
                TestSafety._key_walk(item, found)

    def test_no_secret_like_keys_in_any_response(self) -> None:
        found: list[str] = []
        for payload in self._sweep():
            self._key_walk(payload, found)
        self.assertEqual(found, [])
        for payload in self._sweep():
            self.assertNotIn("sk-fixture-decoy", json.dumps(payload))

    def test_all_endpoints_write_nothing(self) -> None:
        self._sweep()
        # Re-seed deterministically: the sweep must leave identical bytes.
        before = snapshot(self.state.root)
        self._sweep()
        after = snapshot(self.state.root)
        self.assertEqual(before, after)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
