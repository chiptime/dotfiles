"""Backlog review HTTP API tests (S7/S8, T3: record/plan/apply parity).

Every test drives the REAL loopback server (``create_server`` on port 0
with ``register_review`` applied) over ``http.client`` against a temp
state root seeded through the existing ``test_review`` helpers.

Parity rule (P1-FR-11): each web action is compared against the CLI
command run by ``main([...])`` on an identical twin temp state — same
fixture bytes, so per-line entry hashes match — asserting the resulting
backlog, decisions and receipt documents are identical except for
timestamps (``decided_at``/``applied_at``/``rejected_at``) and the
timestamped audit directory names.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from tiktok_ingest.cli import main
from tiktok_ingest.review import (
    build_plan,
    parse_decisions_file,
    record_decision,
)
from tiktok_ingest.review import ReviewError
from tiktok_ingest.state import StateRoot
from tiktok_ingest.tests.test_review import entry_dict, write_backlog
from tiktok_ingest.web.runner import WebRunner
from tiktok_ingest.web.server import (
    ServerState,
    create_app,
    create_server,
)

TEST_TOKEN = "test-review-api-token"
VIDEO_URL = "https://www.tiktok.com/@author/video/7112345678901234567"


def seed_review_state(state: StateRoot, ids: list[str]) -> dict[str, str]:
    """Backlog fixtures + one verification.json per referenced run dir.

    Returns the raw-line entry hashes (the decision concurrency tokens).
    """
    payload = write_backlog(state, [entry_dict(video_id) for video_id in ids])
    lines = payload.splitlines()
    for video_id, line in zip(ids, lines):
        run_dir = state.root / "runs" / "run-1" / video_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "verification.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "video_id": video_id,
                    "generated_at": "2026-09-18T12:00:00Z",
                    "rules_version": "2",
                    "claims": [
                        {
                            "claim": "FixtureTool has 12000 stars",
                            "verdict": "confirmed",
                            "reason": "fetched 1 candidate source(s)",
                            "evidence": "frame_001",
                            "note": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    return {
        video_id: hashlib.sha256(line).hexdigest()
        for video_id, line in zip(ids, lines)
    }


def run_cli(argv: list[str]) -> tuple[int, str]:
    """The CLI command with stdout+stderr captured (test_cli pattern)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = main(argv)
    return code, buffer.getvalue()


def normalized_decisions(path: Path) -> str:
    """Decisions JSONL with ``decided_at`` zeroed (the only field that
    must differ between the web and CLI paths)."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record["decided_at"] = "<TS>"
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + "\n"


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ReviewApiTestCase(unittest.TestCase):
    """Fresh temp state + real loopback server with review endpoints."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = StateRoot(self.tmp / "state")
        self.state.ensure_layout()
        self.runner = WebRunner(self.state)
        self.port = self._serve(self.state, self.runner)
        self.hashes = seed_review_state(self.state, ["111", "222"])

    def _serve(self, state: StateRoot, runner: WebRunner) -> int:
        from tiktok_ingest.web.review_api import register_review

        app = create_app(ServerState(state_root=state, token=TEST_TOKEN))
        register_review(app, runner)
        server = create_server(state, port=0, app=app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # addCleanup is LIFO: shutdown runs first, join last.
        self.addCleanup(thread.join, 5.0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    # -- HTTP helpers ------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: bool = True,
        port: int | None = None,
    ) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(
            "127.0.0.1", port or self.port, timeout=10
        )
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-TI-Token"] = TEST_TOKEN
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = {"_raw": raw.decode("utf-8", "replace")}
        return response.status, document

    def get_json(self, path: str) -> tuple[int, dict]:
        return self.request("GET", path)

    def post_json(self, path: str, body: dict, **kwargs) -> tuple[int, dict]:
        return self.request("POST", path, body, **kwargs)

    def decide(self, video_id: str, decision: str, entry_sha256: str,
               reason: str | None = None, **kwargs) -> tuple[int, dict]:
        body: dict = {"decision": decision, "entry_sha256": entry_sha256}
        if reason is not None:
            body["reason"] = reason
        return self.post_json(f"/api/backlog/{video_id}/decision", body,
                              **kwargs)


class TestShowEntry(ReviewApiTestCase):
    def test_show_parity_with_backlog_show(self) -> None:
        twin = StateRoot(self.tmp / "twin")
        seed_review_state(twin, ["111"])
        code, out = run_cli(
            ["backlog-show", "111", "--state-root", str(twin.root)]
        )
        self.assertEqual(code, 0)
        status, data = self.get_json("/api/backlog/111")
        self.assertEqual(status, 200)
        self.assertEqual(data, json.loads(out))
        self.assertEqual(len(data["entry_sha256"]), 64)
        # Unknown id: the review module's honest refusal, as 404.
        status, data = self.get_json("/api/backlog/999")
        self.assertEqual(status, 404)
        self.assertIn("unknown backlog id", data["error"])


class TestDecision(ReviewApiTestCase):
    def test_accept_records_and_refuses_duplicates_and_bad_values(self) -> None:
        before = snapshot(self.state.root)
        status, data = self.decide("111", "accepted", self.hashes["111"])
        self.assertEqual(status, 200)
        self.assertEqual(data["recorded"]["decision"], "accepted")
        self.assertEqual(data["recorded"]["entry_sha256"], self.hashes["111"])
        decisions = self.state.root / "reviews" / "web-decisions.jsonl"
        self.assertTrue(decisions.is_file())
        # Backlog itself is untouched by a decision (CLI semantics).
        self.assertEqual(
            snapshot(self.state.root), {**before, "reviews/web-decisions.jsonl":
            hashlib.sha256(decisions.read_bytes()).hexdigest()}
        )
        # Duplicate decision for the same id: conflict, zero writes.
        before = snapshot(self.state.root)
        status, data = self.decide("111", "accepted", self.hashes["111"])
        self.assertEqual(status, 409)
        self.assertIn("already records a decision", data["error"])
        self.assertEqual(snapshot(self.state.root), before)
        # The web review view accepts or rejects only (P1-FR-08); the
        # status vocabulary itself is never renamed (P1-FR-03).
        for bad in ("accept", "pending", "confirmed"):
            status, data = self.decide("222", bad, self.hashes["222"])
            self.assertEqual(status, 400, bad)
            self.assertIn("decision must be one of", data["error"])

    def test_reject_without_reason_is_400_with_the_cli_message(self) -> None:
        before = snapshot(self.state.root)
        # The exact message record_decision raises for the CLI, too.
        with self.assertRaises(ReviewError) as ctx:
            record_decision(
                self.state,
                video_id="111",
                decision="rejected",
                decisions_file=self.tmp / "never.jsonl",
                reason=None,
            )
        status, data = self.decide("111", "rejected", self.hashes["111"])
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], str(ctx.exception))
        self.assertEqual(snapshot(self.state.root), before)  # zero writes

    def test_reject_with_reason_records_it(self) -> None:
        status, data = self.decide(
            "111", "rejected", self.hashes["111"], reason="not relevant"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["recorded"]["decision"], "rejected")
        self.assertEqual(data["recorded"]["reason"], "not relevant")
        # Nothing applied yet: the backlog line is unchanged.
        decisions = parse_decisions_file(
            self.state.root / "reviews" / "web-decisions.jsonl"
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(self.get_json("/api/backlog/111")[1]["entry"]["status"],
                         "pending")

    def test_stale_entry_hash_is_409_with_the_review_message(self) -> None:
        before = snapshot(self.state.root)
        stale = "0" * 64
        status, data = self.decide("111", "accepted", stale)
        self.assertEqual(status, 409)
        # The exact conflict reason build_plan produces for a stale
        # decision — the review module's own message, byte for byte.
        scratch = self.tmp / "stale.jsonl"
        record_decision(
            self.state, video_id="111", decision="accepted",
            decisions_file=scratch, reason=None,
        )
        document = json.loads(scratch.read_text(encoding="utf-8"))
        document["entry_sha256"] = stale
        plan = build_plan(self.state, [document])
        self.assertEqual(
            data["error"],
            plan["conflicts"][0]["reason"],
        )
        self.assertEqual(snapshot(self.state.root), before)  # zero writes


class TestPlan(ReviewApiTestCase):
    def test_plan_parity_with_backlog_plan(self) -> None:
        self.decide("111", "accepted", self.hashes["111"])
        self.decide("222", "rejected", self.hashes["222"], reason="off topic")

        twin = StateRoot(self.tmp / "twin")
        seed_review_state(twin, ["111", "222"])
        twin_file = self.tmp / "twin-decisions.jsonl"
        run_cli(["backlog-decide", "111", "--decision", "accepted",
                 "--decisions-file", str(twin_file),
                 "--state-root", str(twin.root)])
        run_cli(["backlog-decide", "222", "--decision", "rejected",
                 "--reason", "off topic", "--decisions-file", str(twin_file),
                 "--state-root", str(twin.root)])
        code, out = run_cli(
            ["backlog-plan", "--decisions-file", str(twin_file),
             "--state-root", str(twin.root)]
        )
        self.assertEqual(code, 0)

        status, data = self.get_json("/api/backlog/plan")
        self.assertEqual(status, 200)
        web_plan = dict(data["plan"])
        cli_plan = json.loads(out)
        # The only inherently path-dependent field.
        web_plan.pop("decisions_file")
        cli_plan.pop("decisions_file")
        self.assertEqual(web_plan, cli_plan)
        self.assertEqual(len(data["plan_sha256"]), 64)

    def test_plan_without_decisions_is_404(self) -> None:
        status, data = self.get_json("/api/backlog/plan")
        self.assertEqual(status, 404)
        self.assertIn("decisions file", data["error"])


class TestApply(ReviewApiTestCase):
    def _cli_twin_apply(self) -> dict:
        """Decide (accept 111, reject 222) + apply through the CLI on a
        twin state; returns the CLI receipt and the twin state."""
        twin = StateRoot(self.tmp / "twin")
        seed_review_state(twin, ["111", "222"])
        twin_file = self.tmp / "twin-decisions.jsonl"
        run_cli(["backlog-decide", "111", "--decision", "accepted",
                 "--decisions-file", str(twin_file),
                 "--state-root", str(twin.root)])
        run_cli(["backlog-decide", "222", "--decision", "rejected",
                 "--reason", "off topic", "--decisions-file", str(twin_file),
                 "--state-root", str(twin.root)])
        code, out = run_cli(
            ["backlog-apply", "--decisions-file", str(twin_file),
             "--state-root", str(twin.root)]
        )
        self.assertEqual(code, 0)
        return json.loads(out), twin, twin_file

    def test_apply_bound_to_preview_matches_cli_receipts_and_backups(self) -> None:
        self.decide("111", "accepted", self.hashes["111"])
        self.decide("222", "rejected", self.hashes["222"], reason="off topic")
        _status, plan_data = self.get_json("/api/backlog/plan")

        status, receipt = self.post_json(
            "/api/backlog/apply", {"plan_sha256": plan_data["plan_sha256"]}
        )
        self.assertEqual(status, 200)
        cli_receipt, twin, twin_file = self._cli_twin_apply()

        # Receipt parity on every non-timestamp field.
        for key in ("status", "applied", "already_applied", "changed"):
            self.assertEqual(receipt[key], cli_receipt[key], key)
        self.assertEqual(
            [(item["id"], item["result"]) for item in receipt["blocklist"]],
            [(item["id"], item["result"]) for item in cli_receipt["blocklist"]],
        )
        self.assertEqual(
            [item["path"] for item in receipt["files_written"]],
            [item["path"] for item in cli_receipt["files_written"]],
        )
        # backlog.jsonl's published bytes are timestamp-free (fixture
        # entries + status rewrite): byte-equal across both paths.
        # blocklist.json embeds rejected_at (second resolution), so it
        # is compared as a normalized document below instead.
        for web_file, cli_file in zip(
            receipt["files_written"], cli_receipt["files_written"]
        ):
            if web_file["path"] == "backlog.jsonl":
                self.assertEqual(web_file["sha256_after"], cli_file["sha256_after"])
        self.assertEqual(
            sorted(item["sha256"] or "" for item in receipt["backups"]),
            sorted(item["sha256"] or "" for item in cli_receipt["backups"]),
        )
        # Durable files: byte parity except timestamps.
        self.assertEqual(
            self.state.backlog_path.read_bytes(), twin.backlog_path.read_bytes()
        )
        self.assertEqual(
            normalized_decisions(self.state.root / "reviews" / "web-decisions.jsonl"),
            normalized_decisions(twin_file),
        )
        web_blocklist = json.loads(
            (self.state.root / "blocklist.json").read_text(encoding="utf-8")
        )
        cli_blocklist = json.loads(
            (twin.root / "blocklist.json").read_text(encoding="utf-8")
        )
        # rejected_at is the one legitimate timestamp difference.
        for document in (web_blocklist, cli_blocklist):
            for entry in document["entries"]:
                entry.pop("rejected_at", None)
        self.assertEqual(web_blocklist, cli_blocklist)
        # Exclusive backups exist on disk under the audit directory.
        audit_dirs = sorted(
            (self.state.root / "reviews").glob("backlog-apply-*/backup")
        )
        self.assertEqual(len(audit_dirs), 1)
        self.assertEqual(
            {p.name for p in audit_dirs[0].iterdir()}, {"backlog.jsonl"}
        )
        self.assertEqual(
            hashlib.sha256((audit_dirs[0] / "backlog.jsonl").read_bytes())
            .hexdigest(),
            receipt["backups"][0]["sha256"],
        )

    def test_apply_after_plan_changed_is_409_with_zero_writes(self) -> None:
        self.decide("111", "accepted", self.hashes["111"])
        _status, plan_data = self.get_json("/api/backlog/plan")
        fingerprint = plan_data["plan_sha256"]
        # A missing confirmation token is a client error.
        status, data = self.post_json("/api/backlog/apply", {})
        self.assertEqual(status, 400)
        # New decision after the preview: the plan fingerprint changed.
        self.decide("222", "accepted", self.hashes["222"])
        before = snapshot(self.state.root)
        status, data = self.post_json(
            "/api/backlog/apply", {"plan_sha256": fingerprint}
        )
        self.assertEqual(status, 409)
        self.assertIn("changed since", data["error"])
        self.assertEqual(snapshot(self.state.root), before)  # zero writes
        # The CURRENT fingerprint applies cleanly.
        _status, fresh = self.get_json("/api/backlog/plan")
        status, receipt = self.post_json(
            "/api/backlog/apply", {"plan_sha256": fresh["plan_sha256"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(sorted(receipt["applied"]), ["111", "222"])


class TestSingleWriter(ReviewApiTestCase):
    def test_decision_and_apply_refuse_while_run_active(self) -> None:
        release = threading.Event()

        def fake_ingest(*args, **kwargs):
            release.wait(timeout=10)
            return 0, {}

        runner = WebRunner(self.state, allow_runs=True, ingest=fake_ingest)
        port = self._serve(self.state, runner)
        runner.start(VIDEO_URL, {"url": VIDEO_URL})
        # LIFO: release the worker BEFORE shutdown joins it.
        self.addCleanup(runner.shutdown)
        self.addCleanup(release.set)
        self.assertEqual(runner.active_view()["state"], "running")

        status, data = self.decide(
            "111", "accepted", self.hashes["111"], port=port
        )
        self.assertEqual(status, 409)
        self.assertIn("run is active", data["error"])
        status, data = self.post_json(
            "/api/backlog/apply", {"plan_sha256": "0" * 64}, port=port
        )
        self.assertEqual(status, 409)
        self.assertIn("run is active", data["error"])


class TestToken(ReviewApiTestCase):
    def test_decision_without_token_is_403(self) -> None:
        status, data = self.decide(
            "111", "accepted", self.hashes["111"], token=False
        )
        self.assertEqual(status, 403)
        self.assertEqual(data["error"], "missing or invalid token")
        status, data = self.post_json(
            "/api/backlog/apply", {"plan_sha256": "0" * 64}, token=False
        )
        self.assertEqual(status, 403)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
