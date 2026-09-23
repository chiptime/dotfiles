"""CLI wiring: subcommands, flags, exit codes (fixture-only)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tiktok_ingest.cli import build_parser, main
from tiktok_ingest.contracts import StageOutcome
from tiktok_ingest.state import StateRoot
from tiktok_ingest.vision import VisionOutcome
from tiktok_ingest.whisper_client import AudioOutcome


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_subcommands_are_declared(self) -> None:
        commands = set(self.parser._subparsers._group_actions[0].choices)
        self.assertEqual(
            commands,
            {
                "install-extractor",
                "fetch-url",
                "prepare",
                "install-ollama",
                "import-model",
                "vision",
                "audio",
                "synthesize",
                "verify",
                "status",
                "inventory-collect",
                "inventory-status",
                "inventory-plan",
                "backlog-list",
                "backlog-show",
                "backlog-decide",
                "backlog-plan",
                "backlog-apply",
                "guided-run",
                "collection-run",
            },
        )

    def test_collection_run_parses_url_and_flags(self) -> None:
        args = self.parser.parse_args(
            [
                "collection-run",
                "https://www.tiktok.com/@user/collection/slug-1",
                "--dry-run",
                "--limit",
                "3",
                "--declared-count",
                "7",
                "--end-evidence",
                "end of list",
                "--state-root",
                "/tmp/state",
            ]
        )
        self.assertEqual(args.command, "collection-run")
        self.assertEqual(
            args.collection_url, "https://www.tiktok.com/@user/collection/slug-1"
        )
        self.assertTrue(args.dry_run)
        self.assertEqual(args.limit, 3)
        self.assertEqual(args.declared_count, 7)
        self.assertEqual(args.end_evidence, "end of list")

    def test_collection_run_missing_url_is_a_usage_error(self) -> None:
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(stderr):
            self.parser.parse_args(["collection-run"])
        self.assertEqual(ctx.exception.code, 2)

    def test_milestone_3_commands_parse(self) -> None:
        args = self.parser.parse_args(
            ["import-model", "--model", "qwen3.5:9b", "--share-root", "/tmp/sr"]
        )
        self.assertEqual(args.command, "import-model")
        self.assertEqual(args.model, "qwen3.5:9b")
        args = self.parser.parse_args(["vision", "123", "--force"])
        self.assertEqual(args.command, "vision")
        self.assertEqual(args.video_id, "123")
        self.assertTrue(args.force)
        args = self.parser.parse_args(["audio", "123"])
        self.assertEqual(args.command, "audio")
        args = self.parser.parse_args(["install-ollama"])
        self.assertEqual(args.command, "install-ollama")

    def test_fetch_url_parses_url_and_flags(self) -> None:
        args = self.parser.parse_args(
            [
                "fetch-url",
                "https://www.tiktok.com/@user/video/123",
                "--state-root",
                "/tmp/state",
                "--force",
            ]
        )
        self.assertEqual(args.command, "fetch-url")
        self.assertEqual(args.url, "https://www.tiktok.com/@user/video/123")
        self.assertTrue(args.force)

    def test_status_video_id_is_optional(self) -> None:
        args = self.parser.parse_args(["status"])
        self.assertIsNone(args.video_id)
        args = self.parser.parse_args(["status", "123"])
        self.assertEqual(args.video_id, "123")

    def test_a_command_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            self.parser.parse_args([])

    def test_help_mentions_never_implicit_install(self) -> None:
        help_text = self.parser.format_help()
        self.assertIn("never runs implicitly", help_text)


class MainExitCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_root = self.tmp / "state"

    def _main(self, argv: list[str]) -> int:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            return main(argv)

    def test_status_on_empty_state_exits_zero_with_json(self) -> None:
        exit_code = self._main(
            ["status", "--state-root", str(self.state_root)]
        )
        self.assertEqual(exit_code, 0)

    def test_unsupported_url_exits_two_with_error(self) -> None:
        exit_code = self._main(
            [
                "fetch-url",
                "https://example.com/not-tiktok",
                "--state-root",
                str(self.state_root),
            ]
        )
        self.assertEqual(exit_code, 2)

    def test_prepare_unknown_id_exits_one(self) -> None:
        exit_code = self._main(
            [
                "prepare",
                "4242424242424242424",
                "--state-root",
                str(self.state_root),
            ]
        )
        self.assertEqual(exit_code, 1)

    def test_module_entry_point_help_exits_zero(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            build_parser().parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)


class Milestone3DispatchTests(unittest.TestCase):
    """vision/audio dispatch: stage outcome -> exit code, --state-root wiring."""

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

    def test_audio_complete_exits_zero_and_prints_outcome(self) -> None:
        outcome = AudioOutcome(
            outcome=StageOutcome.COMPLETE.value,
            reason="ok",
            reused=False,
            video_id="123",
        )
        with mock.patch(
            "tiktok_ingest.cli.run_audio_stage", return_value=outcome
        ) as stage:
            code, printed = self._main(
                ["audio", "123", "--state-root", str(self.state_root)]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed)["outcome"], "complete")
        self.assertEqual(stage.call_args.kwargs["state"].root, self.state_root)

    def test_audio_blocked_exits_one(self) -> None:
        outcome = AudioOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="health down",
            reused=False,
            video_id="123",
        )
        with mock.patch("tiktok_ingest.cli.run_audio_stage", return_value=outcome):
            code, _ = self._main(["audio", "123", "--state-root", str(self.state_root)])
        self.assertEqual(code, 1)

    def test_vision_complete_exits_zero_and_prints_outcome(self) -> None:
        outcome = VisionOutcome(
            outcome=StageOutcome.COMPLETE.value,
            reason="ok",
            reused=False,
            video_id="123",
            frames_processed=29,
            rereads_used=0,
        )
        with mock.patch(
            "tiktok_ingest.cli.run_vision_stage", return_value=outcome
        ) as stage:
            code, printed = self._main(
                ["vision", "123", "--force", "--state-root", str(self.state_root)]
            )
        self.assertEqual(code, 0)
        document = json.loads(printed)
        self.assertEqual(document["outcome"], "complete")
        self.assertEqual(document["frames_processed"], 29)
        self.assertTrue(stage.call_args.kwargs["force"])

    def test_vision_blocked_exits_one(self) -> None:
        outcome = VisionOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="whisper container still running",
            reused=False,
            video_id="123",
        )
        with mock.patch("tiktok_ingest.cli.run_vision_stage", return_value=outcome):
            code, _ = self._main(["vision", "123", "--state-root", str(self.state_root)])
        self.assertEqual(code, 1)


class CollectionRunDispatchTests(unittest.TestCase):
    """collection-run dispatch: URL validation before any effect."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state_root = str(Path(self.tmp.name) / "state")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _main(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(argv)
        return code, stdout.getvalue()

    def test_invalid_url_exits_two_without_effects(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code, _ = self._main(
                [
                    "collection-run",
                    "https://www.tiktok.com/@user/video/123",
                    "--state-root",
                    self.state_root,
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("collection error", stderr.getvalue())
        self.assertFalse(Path(self.state_root).exists())

    def test_dry_run_valid_url_exits_zero(self) -> None:
        code, printed = self._main(
            [
                "collection-run",
                "https://www.tiktok.com/@user/collection/slug-1",
                "--dry-run",
                "--state-root",
                self.state_root,
            ]
        )
        self.assertEqual(code, 0)
        document = json.loads(printed)
        self.assertEqual(document["mode"], "dry-run")
        self.assertIn("preflight", document)
        # Dry run writes nothing: the state root is never created.
        self.assertFalse(Path(self.state_root).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
