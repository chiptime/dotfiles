"""Extractor gate, pinned wrapper and oEmbed cache (fixture-only, zero network)."""

from __future__ import annotations

import ast
import datetime
import json
import tempfile
import urllib.error
import unittest
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.extractor import (
    EXTRACTION_OUTCOMES,
    ExtractionResult,
    ExtractorError,
    GATE_CONDITIONS,
    GATE_PROBE_SOURCE,
    EXTRACTOR_RUNNER_SOURCE,
    OEmbedCache,
    OEmbedRecord,
    _result_from_runner_output,
    build_oembed_url,
    check_forbidden_options,
    check_version,
    evaluate_gate_report,
    fetch_oembed,
    is_record_fresh,
    oembed_cache_key,
    parse_oembed_payload,
    probe_gate,
    run_extraction,
    verify_extractor_gate,
)
from tiktok_ingest.state import DEFAULT_STATE_ROOT, StateRoot
from tiktok_ingest.tests import fixtures


class VersionPinTests(unittest.TestCase):
    def test_exact_pin_passes(self) -> None:
        ok, reason = check_version("2026.08.19\n")
        self.assertTrue(ok)
        self.assertIn("matches", reason)

    def test_any_other_version_fails(self) -> None:
        for version in ("2026.08.20", "2026.8.19", "master", "", "\n"):
            ok, reason = check_version(version)
            self.assertFalse(ok, f"version {version!r} must not pass")
            self.assertIn("refusing to execute", reason)

    def test_subprocess_newline_is_tolerated(self) -> None:
        ok, _ = check_version("2026.08.19\n")
        self.assertTrue(ok)

    def test_pin_constant_matches_methodology(self) -> None:
        self.assertEqual(config.EXTRACTOR_PACKAGE, "yt-dlp")
        self.assertEqual(config.EXTRACTOR_PIN, "2026.08.19")
        self.assertEqual(config.EXTRACTOR_REQUIREMENT, "yt-dlp==2026.08.19")


class GateEvaluationTests(unittest.TestCase):
    def test_full_report_passes(self) -> None:
        verdict = evaluate_gate_report(fixtures.gate_report())
        self.assertTrue(verdict.passed)
        self.assertEqual(
            {name for name, _, _ in verdict.checks}, set(GATE_CONDITIONS)
        )

    def test_missing_report_fails_closed(self) -> None:
        verdict = evaluate_gate_report(None)
        self.assertFalse(verdict.passed)
        self.assertIn("fail", verdict.reason.lower())

    def test_area_without_located_symbols_fails_closed(self) -> None:
        verdict = evaluate_gate_report(fixtures.gate_report(challenge_found=()))
        self.assertFalse(verdict.passed)
        self.assertIn("challenge_solving_blocked", verdict.failed_conditions())

    def test_wrong_version_fails(self) -> None:
        verdict = evaluate_gate_report(fixtures.gate_report(version="2026.09.01"))
        self.assertFalse(verdict.passed)
        self.assertIn("version_matches_pin", verdict.failed_conditions())

    def test_cookie_flags_must_be_true(self) -> None:
        verdict = evaluate_gate_report(
            fixtures.gate_report(ambient_cookies_blocked=False)
        )
        self.assertFalse(verdict.passed)
        self.assertIn("ambient_cookies_blocked", verdict.failed_conditions())

    def test_probe_error_fails_closed_without_execution(self) -> None:
        def runner(argv, timeout=None):
            return 0, fixtures.runner_json(
                {"schema": 1, "probe_error": "ImportError: no module"}
            ), ""

        with tempfile.TemporaryDirectory() as tmp:
            verdict = verify_extractor_gate(Path(tmp) / "venv", Path(tmp), runner=runner)
        self.assertFalse(verdict.passed)
        self.assertIn("probe", verdict.reason.lower())

    def test_probe_nonzero_exit_fails_closed(self) -> None:
        def runner(argv, timeout=None):
            return 1, "", "boom"

        with tempfile.TemporaryDirectory() as tmp:
            verdict = verify_extractor_gate(Path(tmp) / "venv", Path(tmp), runner=runner)
        self.assertFalse(verdict.passed)


class ForbiddenOptionTests(unittest.TestCase):
    def test_cookie_options_are_refused(self) -> None:
        for option in config.EXTRACTOR_FORBIDDEN_OPTIONS:
            ok, reason = check_forbidden_options(["yt-dlp", option, "x"])
            self.assertFalse(ok, option)
            self.assertIn("refusing", reason)

    def test_equals_form_is_refused(self) -> None:
        ok, _ = check_forbidden_options(["--cookies-from-browser=chrome"])
        self.assertFalse(ok)

    def test_benign_options_pass(self) -> None:
        ok, _ = check_forbidden_options(["yt-dlp", "--retries", "0"])
        self.assertTrue(ok)


class EmbeddedScriptTests(unittest.TestCase):
    def test_scripts_are_valid_python(self) -> None:
        ast.parse(GATE_PROBE_SOURCE)
        ast.parse(EXTRACTOR_RUNNER_SOURCE)

    def test_scripts_embed_the_pinned_constants(self) -> None:
        self.assertIn("'_call_api'", GATE_PROBE_SOURCE)
        self.assertIn("'_solve_challenge'", GATE_PROBE_SOURCE)
        self.assertIn('"--cookies"', EXTRACTOR_RUNNER_SOURCE)
        self.assertIn('"--cookies-from-browser"', EXTRACTOR_RUNNER_SOURCE)

    def test_runner_disables_retries_and_playlist(self) -> None:
        self.assertIn('"retries": 0', EXTRACTOR_RUNNER_SOURCE)
        self.assertIn('"noplaylist": True', EXTRACTOR_RUNNER_SOURCE)
        self.assertIn('"max_filesize"', EXTRACTOR_RUNNER_SOURCE)


class ExtractionResultParsingTests(unittest.TestCase):
    def test_valid_document_parses(self) -> None:
        result = _result_from_runner_output(
            fixtures.runner_json(
                {"outcome": "complete", "reason": "ok", "detail": {"files": ["/tmp/x.mp4"]}}
            ),
            "",
        )
        self.assertEqual(result.outcome, "complete")
        self.assertEqual(result.media_path, "/tmp/x.mp4")

    def test_unknown_outcome_becomes_failed(self) -> None:
        result = _result_from_runner_output(
            fixtures.runner_json({"outcome": "yolo", "reason": "?"}), ""
        )
        self.assertEqual(result.outcome, "failed")

    def test_garbage_output_becomes_failed(self) -> None:
        result = _result_from_runner_output("", "some stderr")
        self.assertEqual(result.outcome, "failed")
        self.assertIn("some stderr", result.detail["stderr"])

    def test_outcome_vocabulary_covers_prd(self) -> None:
        for outcome in ("complete", "denied", "unsupported", "challenge", "blocked",
                        "failed", "budget_exceeded"):
            self.assertIn(outcome, EXTRACTION_OUTCOMES)

    def test_result_requires_reason(self) -> None:
        with self.assertRaises(ExtractorError):
            ExtractionResult(outcome="blocked", reason="  ")


class RunExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.venv = self.tmp / "venv"
        self.share = self.tmp / "share"
        self.dest = self.tmp / "download"

    def test_gate_failure_blocks_before_download(self) -> None:
        calls: list[list[str]] = []

        def runner(argv, timeout=None):
            calls.append(list(argv))
            return 0, fixtures.runner_json(
                fixtures.gate_report(app_api_found=())
            ), ""

        result = run_extraction(
            "https://www.tiktok.com/@a/video/1",
            self.dest,
            venv_dir=self.venv,
            share_root=self.share,
            runner=runner,
        )
        self.assertEqual(result.outcome, "blocked")
        download_calls = [call for call in calls if "extractor-runner" in call[1]]
        self.assertEqual(download_calls, [], "no download after a failed gate")
        self.assertEqual(len(calls), 1, "only the gate probe ran")

    def test_gate_passes_then_one_download_runs(self) -> None:
        calls: list[list[str]] = []

        def runner(argv, timeout=None):
            calls.append(list(argv))
            if "extractor-gate-probe" in argv[1]:
                return 0, fixtures.runner_json(fixtures.gate_report()), ""
            media = self.dest / "42.mp4"
            self.dest.mkdir(parents=True, exist_ok=True)
            media.write_bytes(fixtures.mp4_bytes())
            return 0, fixtures.runner_json(
                {
                    "outcome": "complete",
                    "reason": "one media asset downloaded",
                    "detail": {"files": [str(media)]},
                }
            ), ""

        result = run_extraction(
            "https://www.tiktok.com/@a/video/42",
            self.dest,
            venv_dir=self.venv,
            share_root=self.share,
            runner=runner,
            deadline_seconds=30,
        )
        self.assertEqual(result.outcome, "complete")
        self.assertIsNotNone(result.media_path)
        download_calls = [call for call in calls if "extractor-runner" in call[1]]
        self.assertEqual(len(download_calls), 1, "exactly one download, never retried")

    def test_runner_reports_denied_without_retry(self) -> None:
        def runner(argv, timeout=None):
            if "extractor-gate-probe" in argv[1]:
                return 0, fixtures.runner_json(fixtures.gate_report()), ""
            return 0, fixtures.runner_json(
                {"outcome": "denied", "reason": "access denied", "detail": {}}
            ), ""

        result = run_extraction(
            "https://www.tiktok.com/@a/video/1",
            self.dest,
            venv_dir=self.venv,
            share_root=self.share,
            runner=runner,
        )
        self.assertEqual(result.outcome, "denied")
        self.assertIn("denied", result.reason)

    def test_deadline_becomes_budget_exceeded(self) -> None:
        def runner(argv, timeout=None):
            if "extractor-gate-probe" in argv[1]:
                return 0, fixtures.runner_json(fixtures.gate_report()), ""
            return 124, "", "timeout"

        result = run_extraction(
            "https://www.tiktok.com/@a/video/1",
            self.dest,
            venv_dir=self.venv,
            share_root=self.share,
            runner=runner,
            deadline_seconds=1,
        )
        self.assertEqual(result.outcome, "budget_exceeded")

    def test_probe_script_is_written_outside_git(self) -> None:
        def runner(argv, timeout=None):
            return 0, fixtures.runner_json(fixtures.gate_report()), ""

        run_extraction(
            "https://www.tiktok.com/@a/video/1",
            self.dest,
            venv_dir=self.venv,
            share_root=self.share,
            runner=runner,
        )
        probe_path = self.share / config.EXTRACTOR_GATE_PROBE_PATH.name
        self.assertTrue(probe_path.is_file())
        # Configured runtime locations live OUTSIDE Git under ~/.local/share.
        self.assertEqual(config.EXTRACTOR_GATE_PROBE_PATH.parent, config.SHARE_ROOT)
        self.assertIn(".local/share", str(config.SHARE_ROOT))
        self.assertIn(".local/state", str(DEFAULT_STATE_ROOT))


class OEmbedTests(unittest.TestCase):
    def test_url_is_quoted(self) -> None:
        url = build_oembed_url("https://www.tiktok.com/@a/video/1")
        self.assertTrue(url.startswith(config.OEMBED_ENDPOINT + "?url="))
        self.assertNotIn("://", url.split("url=")[1])

    def test_payload_parsing(self) -> None:
        payload = parse_oembed_payload(json.dumps(fixtures.sample_oembed_payload()))
        self.assertEqual(payload["author_name"], "fixture-author")
        with self.assertRaises(ExtractorError):
            parse_oembed_payload(b"<html>not json</html>")

    class _Response:
        status = 200

        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_fetch_success_records_time_and_outcome(self) -> None:
        def urlopen(request, timeout=None):
            return self._Response(json.dumps(fixtures.sample_oembed_payload()).encode())

        record = fetch_oembed(
            "https://www.tiktok.com/@a/video/1",
            urlopen=urlopen,
            fetched_at="2026-09-19T12:00:00Z",
        )
        self.assertEqual(record.outcome, "complete")
        self.assertEqual(record.fetched_at, "2026-09-19T12:00:00Z")
        self.assertEqual(record.payload["title"], "Fixture video")

    def test_http_403_is_denied_not_failed(self) -> None:
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError("url", 403, "forbidden", None, None)

        record = fetch_oembed("https://www.tiktok.com/@a/video/1", urlopen=urlopen)
        self.assertEqual(record.outcome, "denied")

    def test_network_error_is_failed(self) -> None:
        def urlopen(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        record = fetch_oembed("https://www.tiktok.com/@a/video/1", urlopen=urlopen)
        self.assertEqual(record.outcome, "failed")


class OEmbedCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = fixtures.make_state_root(self._tmp.name)
        self.cache = OEmbedCache(self.state)
        self.now = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def _record(self, outcome: str = "complete") -> OEmbedRecord:
        return OEmbedRecord(
            canonical_url="https://www.tiktok.com/@a/video/1",
            fetched_at="2026-09-19T10:00:00Z",
            outcome=outcome,
            reason="fixture",
            payload=fixtures.sample_oembed_payload() if outcome == "complete" else {},
        )

    def test_roundtrip(self) -> None:
        record = self._record()
        self.cache.put(record)
        loaded = self.cache.get(record.canonical_url)
        self.assertEqual(loaded.payload["title"], "Fixture video")
        self.assertEqual(loaded.outcome, "complete")

    def test_ttl_boundary(self) -> None:
        record = self._record()
        fresh = is_record_fresh(record, self.now)
        stale = is_record_fresh(
            record,
            self.now + datetime.timedelta(seconds=config.BUDGETS.metadata_ttl_seconds),
        )
        self.assertTrue(fresh)
        self.assertFalse(stale)

    def test_failure_outcomes_are_never_served_as_fresh(self) -> None:
        self.cache.put(self._record(outcome="denied"))
        self.assertIsNone(self.cache.get_fresh("https://www.tiktok.com/@a/video/1", self.now))

    def test_cache_key_is_deterministic(self) -> None:
        key_a = oembed_cache_key("https://www.tiktok.com/@a/video/1")
        key_b = oembed_cache_key("https://www.tiktok.com/@a/video/1")
        key_c = oembed_cache_key("https://www.tiktok.com/@a/video/2")
        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)

    def test_missing_entry_is_none(self) -> None:
        self.assertIsNone(self.cache.get("https://www.tiktok.com/@a/video/999"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
