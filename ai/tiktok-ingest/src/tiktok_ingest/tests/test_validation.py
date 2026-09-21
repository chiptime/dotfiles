"""Media validation: signatures, streams, budgets, hash binding (fixture-only)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.tests import fixtures
from tiktok_ingest.validation import (
    check_duration_budget,
    check_media_signature,
    check_size_budget,
    inspect_streams,
    parse_ffprobe_document,
    sha256_of_file,
    validate_media_file,
)


class SignatureTests(unittest.TestCase):
    def test_mp4_signature_accepted(self) -> None:
        check = check_media_signature(fixtures.mp4_bytes()[:64])
        self.assertTrue(check.ok)
        self.assertEqual(check.outcome, "complete")
        self.assertIn("MP4/MOV signature", check.reason)
        self.assertIn("isom", check.reason)

    def test_webm_signature_accepted(self) -> None:
        check = check_media_signature(fixtures.webm_bytes())
        self.assertTrue(check.ok)

    def test_html_served_as_video_is_unsupported(self) -> None:
        check = check_media_signature(fixtures.html_bytes())
        self.assertFalse(check.ok)
        self.assertEqual(check.outcome, "unsupported")
        self.assertIn("HTML", check.reason)

    def test_html_with_leading_whitespace_is_still_rejected(self) -> None:
        check = check_media_signature(b"\n\n  " + fixtures.html_bytes())
        self.assertFalse(check.ok)
        self.assertEqual(check.outcome, "unsupported")

    def test_any_markup_lead_is_rejected(self) -> None:
        for head in (b"<script>...", b"<?xml ", b"<!--"):
            check = check_media_signature(head)
            self.assertFalse(check.ok)
            self.assertEqual(check.outcome, "unsupported")

    def test_empty_file_is_a_failure(self) -> None:
        check = check_media_signature(b"")
        self.assertFalse(check.ok)
        self.assertEqual(check.outcome, "failed")

    def test_unknown_signature_is_unsupported(self) -> None:
        check = check_media_signature(b"\x00\x00\x00\x14not-a-media")
        self.assertFalse(check.ok)
        self.assertEqual(check.outcome, "unsupported")


class SizeBudgetTests(unittest.TestCase):
    def test_within_budget(self) -> None:
        check = check_size_budget(1024, config.BUDGETS)
        self.assertTrue(check.ok)

    def test_over_budget_is_explicit(self) -> None:
        check = check_size_budget(config.BUDGETS.media_max_bytes + 1, config.BUDGETS)
        self.assertFalse(check.ok)
        self.assertEqual(check.outcome, "budget_exceeded")


class StreamInspectionTests(unittest.TestCase):
    def test_video_and_audio_present(self) -> None:
        document = json.loads(fixtures.ffprobe_document())
        summary = inspect_streams(document)
        self.assertTrue(summary.has_video)
        self.assertTrue(summary.has_audio)
        self.assertEqual(summary.video_codec, "h264")
        self.assertEqual(summary.audio_codec, "aac")
        self.assertEqual(summary.duration_seconds, 12.5)

    def test_missing_audio_is_unsupported_capability(self) -> None:
        document = json.loads(fixtures.ffprobe_document(audio=False))
        summary = inspect_streams(document)
        self.assertTrue(summary.has_video)
        self.assertFalse(summary.has_audio)

    def test_cover_art_is_not_a_real_video_stream(self) -> None:
        document = json.loads(
            fixtures.ffprobe_document(video=False, attached_pic_video=True)
        )
        summary = inspect_streams(document)
        self.assertFalse(summary.has_video, "attached cover art is not video")
        self.assertTrue(summary.has_audio)

    def test_no_streams_at_all(self) -> None:
        summary = inspect_streams({"streams": [], "format": {}})
        self.assertFalse(summary.has_video)
        self.assertFalse(summary.has_audio)
        self.assertIsNone(summary.duration_seconds)


class DurationBudgetTests(unittest.TestCase):
    def test_within_budget(self) -> None:
        check = check_duration_budget(600.0, config.BUDGETS)
        self.assertTrue(check.ok)

    def test_over_budget_is_explicit_never_truncated(self) -> None:
        check = check_duration_budget(600.5, config.BUDGETS)
        self.assertFalse(check.ok)
        self.assertEqual(check.outcome, "budget_exceeded")
        self.assertIn("never silently truncated", check.reason)

    def test_missing_duration_is_a_failure(self) -> None:
        check = check_duration_budget(None, config.BUDGETS)
        self.assertFalse(check.ok)
        self.assertEqual(check.outcome, "failed")


class ParseTests(unittest.TestCase):
    def test_valid_document_parses(self) -> None:
        document = parse_ffprobe_document(fixtures.ffprobe_document())
        self.assertIn("streams", document)

    def test_garbage_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_ffprobe_document("not json")


class Sha256Tests(unittest.TestCase):
    def test_known_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob.bin"
            path.write_bytes(b"fixture-media-bytes")
            self.assertEqual(
                sha256_of_file(path),
                hashlib.sha256(b"fixture-media-bytes").hexdigest(),
            )


class ValidateMediaFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write_media(self, data: bytes) -> Path:
        path = self.dir / "media.mp4"
        path.write_bytes(data)
        return path

    def test_happy_path_binds_sha256(self) -> None:
        path = self._write_media(fixtures.mp4_bytes())
        result = validate_media_file(
            path,
            ffprobe_runner=lambda argv: (0, fixtures.ffprobe_document(), ""),
        )
        self.assertEqual(result.outcome, "complete")
        self.assertEqual(
            result.media_sha256, hashlib.sha256(fixtures.mp4_bytes()).hexdigest()
        )
        self.assertEqual(result.duration_seconds, 12.5)
        self.assertEqual(result.streams.audio_codec, "aac")

    def test_missing_file_fails(self) -> None:
        result = validate_media_file(
            self.dir / "absent.mp4", ffprobe_runner=lambda argv: (0, "", "")
        )
        self.assertEqual(result.outcome, "failed")

    def test_html_media_is_unsupported_without_ffprobe_call(self) -> None:
        calls: list[list[str]] = []

        def runner(argv):
            calls.append(argv)
            return 0, fixtures.ffprobe_document(), ""

        path = self._write_media(fixtures.html_bytes())
        result = validate_media_file(path, ffprobe_runner=runner)
        self.assertEqual(result.outcome, "unsupported")
        self.assertEqual(calls, [], "signature failure must stop before ffprobe")
        self.assertIsNone(result.media_sha256)

    def test_oversize_media_is_budget_exceeded_before_signature(self) -> None:
        calls: list[list[str]] = []

        def runner(argv):
            calls.append(argv)
            return 0, fixtures.ffprobe_document(), ""

        path = self.dir / "big.mp4"
        path.write_bytes(b"\x00" * (config.BUDGETS.media_max_bytes + 1))
        result = validate_media_file(path, ffprobe_runner=runner)
        self.assertEqual(result.outcome, "budget_exceeded")
        self.assertEqual(calls, [])
        self.assertIsNone(result.media_sha256, "rejected media binds no hash")

    def test_ffprobe_failure_is_failed_with_observed_error(self) -> None:
        path = self._write_media(fixtures.mp4_bytes())

        def runner(argv):
            return 1, "", "ffprobe: invalid data"

        result = validate_media_file(path, ffprobe_runner=runner)
        self.assertEqual(result.outcome, "failed")
        self.assertIn("invalid data", result.reason)

    def test_no_audio_track_is_unsupported(self) -> None:
        path = self._write_media(fixtures.mp4_bytes())
        result = validate_media_file(
            path,
            ffprobe_runner=lambda argv: (
                0,
                fixtures.ffprobe_document(audio=False),
                "",
            ),
        )
        self.assertEqual(result.outcome, "unsupported")
        self.assertIn("audio", result.reason)

    def test_oversize_duration_is_budget_exceeded(self) -> None:
        path = self._write_media(fixtures.mp4_bytes())
        result = validate_media_file(
            path,
            ffprobe_runner=lambda argv: (
                0,
                fixtures.ffprobe_document(duration="601.0"),
                "",
            ),
        )
        self.assertEqual(result.outcome, "budget_exceeded")
        self.assertIn("601", result.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
