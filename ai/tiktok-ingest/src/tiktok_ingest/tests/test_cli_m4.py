"""Milestone 4 CLI wiring: synthesize/verify subcommands and status state."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tiktok_ingest.cli import build_parser, main
from tiktok_ingest.contracts import StageOutcome
from tiktok_ingest.synthesis import SynthesisOutcome
from tiktok_ingest.verify import VerifyOutcome
from tiktok_ingest.state import StateRoot
from tiktok_ingest.tests.fixtures import sample_synthesis_document
from tiktok_ingest.tests.test_guided import fake_id, seed_job, seed_scan


class Milestone4ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_synthesize_parses_from_file_and_flags(self) -> None:
        args = self.parser.parse_args(
            [
                "synthesize",
                "123",
                "--from-file",
                "/tmp/synthesis.json",
                "--state-root",
                "/tmp/state",
                "--force",
            ]
        )
        self.assertEqual(args.command, "synthesize")
        self.assertEqual(args.video_id, "123")
        self.assertEqual(args.from_file, Path("/tmp/synthesis.json"))
        self.assertTrue(args.force)

    def test_synthesize_from_file_defaults_to_none(self) -> None:
        args = self.parser.parse_args(["synthesize", "123"])
        self.assertIsNone(args.from_file)
        self.assertFalse(args.force)

    def test_verify_parses(self) -> None:
        args = self.parser.parse_args(["verify", "123", "--state-root", "/tmp/s"])
        self.assertEqual(args.command, "verify")
        self.assertEqual(args.video_id, "123")
        self.assertFalse(args.force)


class Milestone4DispatchTests(unittest.TestCase):
    """synthesis/verify dispatch: outcome -> exit code, --state-root wiring."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_root = self.tmp / "state"

    def _main(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_synthesize_complete_exits_zero_and_prints_outcome(self) -> None:
        outcome = SynthesisOutcome(
            outcome=StageOutcome.COMPLETE.value,
            reason="ok",
            reused=False,
            video_id="123",
            claims=3,
        )
        with mock.patch(
            "tiktok_ingest.cli.run_synthesis_stage", return_value=outcome
        ) as stage:
            code, printed = self._main(
                [
                    "synthesize",
                    "123",
                    "--from-file",
                    str(self.tmp / "doc.json"),
                    "--state-root",
                    str(self.state_root),
                ]
            )
        self.assertEqual(code, 0)
        document = json.loads(printed)
        self.assertEqual(document["outcome"], "complete")
        self.assertEqual(document["claims"], 3)
        self.assertEqual(
            stage.call_args.kwargs["from_file"], self.tmp / "doc.json"
        )
        self.assertEqual(stage.call_args.kwargs["state"].root, self.state_root)

    def test_synthesize_blocked_exits_one(self) -> None:
        outcome = SynthesisOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="no text-model credential configured; supply --from-file",
            reused=False,
            video_id="123",
        )
        with mock.patch(
            "tiktok_ingest.cli.run_synthesis_stage", return_value=outcome
        ):
            code, _ = self._main(
                ["synthesize", "123", "--state-root", str(self.state_root)]
            )
        self.assertEqual(code, 1)

    def test_verify_complete_exits_zero_and_prints_outcome(self) -> None:
        outcome = VerifyOutcome(
            outcome=StageOutcome.COMPLETE.value,
            reason="ok",
            reused=False,
            video_id="123",
            backlog_result="appended",
            claims=2,
        )
        with mock.patch(
            "tiktok_ingest.cli.run_verify_stage", return_value=outcome
        ) as stage:
            code, printed = self._main(
                ["verify", "123", "--state-root", str(self.state_root)]
            )
        self.assertEqual(code, 0)
        document = json.loads(printed)
        self.assertEqual(document["backlog_result"], "appended")
        self.assertEqual(stage.call_args.kwargs["state"].root, self.state_root)

    def test_verify_failed_exits_one(self) -> None:
        outcome = VerifyOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="synthesis stage is not complete",
            reused=False,
            video_id="123",
        )
        with mock.patch("tiktok_ingest.cli.run_verify_stage", return_value=outcome):
            code, _ = self._main(
                ["verify", "123", "--state-root", str(self.state_root)]
            )
        self.assertEqual(code, 1)

    def test_status_shows_synthesis_verify_and_backlog_state(self) -> None:
        code, printed = self._main(
            ["status", "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 0)
        document = json.loads(printed)
        self.assertEqual(document["items"], [])

    def test_manual_cli_resume_preserves_historical_artifacts_and_verdict(self) -> None:
        state = StateRoot(self.state_root)
        state.ensure_layout()
        video_id = fake_id(1)
        seed_scan(state, [1])
        run_dir = seed_job(state, video_id, complete=("fetch", "prepare", "audio", "vision"), evidence=True)
        document = sample_synthesis_document()
        for claim in document["claims"]:
            claim["verdict"] = "confirmed"
            claim["candidate_urls"] = []
        for entity in document["entities"]:
            if isinstance(entity, dict):
                entity["candidate_urls"] = []
        source = self.tmp / "operator.json"
        source.write_text(json.dumps(document), encoding="utf-8")
        synthesis = ["synthesize", video_id, "--from-file", str(source),
                     "--state-root", str(self.state_root)]
        verify = ["verify", video_id, "--state-root", str(self.state_root)]

        def hashes() -> dict[str, str]:
            return {str(p.relative_to(self.state_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.state_root.rglob("*") if p.is_file()}

        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("unexpected DNS lookup")):
            code, output = self._main(synthesis)
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(output)["reused"])
            code, output = self._main(verify)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["backlog_result"], "appended")
            before = hashes()
            code, output = self._main(synthesis)
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output)["reused"])
            code, output = self._main(verify)
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output)["reused"])
            self.assertEqual(json.loads(output)["backlog_result"], "already_emitted")
        self.assertEqual(before, hashes())
        self.assertEqual(len(state.backlog_path.read_text(encoding="utf-8").splitlines()), 1)
        verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(verification["claims"][0]["verdict"], "confirmed")
        self.assertIn("kept operator-supplied verdict", verification["claims"][0]["reason"])
        self.assertEqual(verification["claims"][0]["fetches"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
