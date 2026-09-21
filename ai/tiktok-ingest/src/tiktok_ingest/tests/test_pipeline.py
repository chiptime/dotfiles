"""Pipeline integration on fixtures: gated fetch, reuse, resume, prepare.

ZERO network and ZERO podman: every boundary (gate probe, extraction,
podman, ffmpeg/ffprobe) is a fake runner keyed by argv patterns. The fake
emulates the container side by writing fixture artifacts into the mounted
host paths.
"""

from __future__ import annotations

import datetime
import json
import tempfile
import unittest
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.contracts import InventoryEntry
from tiktok_ingest.pipeline import (
    FetchOutcome,
    PipelineError,
    _record_inventory,
    fetch_url,
    video_status,
)
from tiktok_ingest.prepare import PrepareOutcome, prepare_video
from tiktok_ingest.state import (
    Blocklist,
    BlocklistEntry,
    InventoryStore,
    Processed,
    StateRoot,
)
from tiktok_ingest.tests import fixtures

CANONICAL_URL = "https://www.tiktok.com/@mralekai/video/7683273567433248022"
VIDEO_ID = "7683273567433248022"
MEDIA_SHA = __import__("hashlib").sha256(fixtures.mp4_bytes()).hexdigest()


class FakeUrlopen:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        body = json.dumps(fixtures.sample_oembed_payload()).encode("utf-8")

        class _Response:
            status = 200

            def read(self) -> bytes:
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()


class FakeExtractorRunner:
    """Fake boundary runner: gate probe, extraction, podman, ffmpeg."""

    def __init__(self, *, media_bytes: bytes = None, extraction_payload: dict | None = None,
                 gate_report: dict | None = None) -> None:
        self.calls: list[list[str]] = []
        self.media_bytes = media_bytes if media_bytes is not None else fixtures.mp4_bytes()
        self.extraction_payload = extraction_payload
        self.gate_report = gate_report if gate_report is not None else fixtures.gate_report()
        self.ffprobe_document_text = fixtures.ffprobe_document()

    @property
    def probe_calls(self) -> int:
        return sum(1 for argv in self.calls if "extractor-gate-probe" in argv[1])

    @property
    def download_calls(self) -> int:
        return sum(1 for argv in self.calls if "extractor-runner" in argv[1])

    @property
    def container_calls(self) -> int:
        return sum(1 for argv in self.calls if argv[:1] == ["podman"] and "run" in argv)

    def __call__(self, argv, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)

        if "extractor-gate-probe" in argv[1]:
            return 0, fixtures.runner_json(self.gate_report), ""

        if "extractor-runner" in argv[1]:
            if self.extraction_payload is not None:
                return 0, fixtures.runner_json(self.extraction_payload), ""
            dest = Path(argv[4])
            dest.mkdir(parents=True, exist_ok=True)
            media = dest / f"{VIDEO_ID}.mp4"
            media.write_bytes(self.media_bytes)
            payload = {
                "outcome": "complete",
                "reason": "one media asset downloaded under the configured budget",
                "detail": {"files": [str(media)]},
            }
            return 0, fixtures.runner_json(payload), ""

        if argv[0] == "podman" and "inspect" in argv:
            return 0, fixtures.FAKE_IMAGE_ID + "\n", ""

        if "ffprobe" in joined:
            return 0, self.ffprobe_document_text, ""

        if "ffmpeg" in argv and "-version" in argv:
            return 0, "ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright", ""

        if "-vf" in argv:
            filter_expression = argv[argv.index("-vf") + 1]
            if "eq(pts" in filter_expression:
                return self._extract_frames(argv, filter_expression)
            return 0, "", self._scan_stderr(filter_expression)

        if "pcm_s16le" in joined:
            return self._write_audio(argv)

        if "-f" in argv and "null" in argv:
            return 0, "", ""

        return 0, "", ""

    # -- container-side emulation helpers -----------------------------------

    def _work_host(self, argv: list[str]) -> Path:
        for index, item in enumerate(argv):
            if item == "-v" and argv[index + 1].endswith(":/work"):
                return Path(argv[index + 1].split(":/work")[0])
        raise AssertionError("no /work mount in container argv")

    def _scan_stderr(self, filter_expression: str) -> str:
        time_base = 12800
        if filter_expression.startswith("fps="):
            events = [(round(t * time_base), t) for t in _frange(0.0, 12.0, 0.5)]
        elif "crop=" in filter_expression:
            events = [(int(6.2 * time_base), 6.2)]
        else:
            events = [(int(3.0 * time_base), 3.0), (int(9.0 * time_base), 9.0)]
        return fixtures.showinfo_stderr(events)

    def _extract_frames(self, argv: list[str], filter_expression: str) -> tuple[int, str, str]:
        import re

        pts_values = [int(value) for value in re.findall(r"eq\(pts\\,(\d+)\)", filter_expression)]
        frames_host = self._work_host(argv) / "frames"
        frames_host.mkdir(parents=True, exist_ok=True)
        for number in range(1, len(pts_values) + 1):
            (frames_host / f"hybrid_{number:03d}.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"0" * 48
            )
        events = [(pts, pts / 12800) for pts in pts_values]
        return 0, "", fixtures.showinfo_stderr(events)

    def _write_audio(self, argv: list[str]) -> tuple[int, str, str]:
        audio_host = self._work_host(argv) / "audio.wav"
        audio_host.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 64)
        return 0, "", ""


def _frange(start: float, stop: float, step: float) -> list[float]:
    values = []
    current = start
    while current <= stop + 1e-9:
        values.append(round(current, 4))
        current += step
    return values


class PipelineTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = StateRoot(self.tmp / "state")
        self.share = self.tmp / "share"
        self.venv = self.tmp / "venv"
        self.runner = FakeExtractorRunner()
        self.urlopen = FakeUrlopen()

    def fetch(self, url: str = CANONICAL_URL, *, force: bool = False) -> FetchOutcome:
        return fetch_url(
            url,
            state=self.state,
            share_root=self.share,
            venv_dir=self.venv,
            runner=self.runner,
            urlopen=self.urlopen,
            now_fn=lambda: datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc),
            force=force,
        )

    def prepare(self, video_id: str = VIDEO_ID, *, force: bool = False) -> PrepareOutcome:
        return prepare_video(video_id, state=self.state, runner=self.runner, force=force)


class FetchUrlTests(PipelineTestBase):
    def test_happy_path_gates_then_downloads_once(self) -> None:
        outcome = self.fetch()
        self.assertEqual(outcome.outcome, "complete")
        self.assertEqual(outcome.media_sha256, MEDIA_SHA)
        self.assertEqual(outcome.oembed_outcome, "complete")
        # Gate runs BEFORE the downloader; exactly one download, no retries.
        self.assertEqual(self.runner.probe_calls, 1)
        self.assertEqual(self.runner.download_calls, 1)
        probe_index = next(
            i for i, argv in enumerate(self.runner.calls)
            if "extractor-gate-probe" in argv[1]
        )
        download_index = next(
            i for i, argv in enumerate(self.runner.calls)
            if "extractor-runner" in argv[1]
        )
        self.assertLess(probe_index, download_index)

        # Media is content-addressed in the working cache.
        cache_path = self.state.cache_dir / f"{MEDIA_SHA}.mp4"
        self.assertTrue(cache_path.is_file())

        # Run artifacts: fetch.json + meta.json with the extraction record.
        run_dir = Path(outcome.run_dir)
        fetch_record = json.loads((run_dir / "fetch.json").read_text())
        self.assertEqual(fetch_record["origin"], "direct-url")
        self.assertEqual(fetch_record["outcome"], "complete")
        manifest = json.loads((run_dir / "meta.json").read_text())
        self.assertEqual(manifest["extraction"]["origin"], "direct-url")
        self.assertEqual(manifest["media_sha256"], MEDIA_SHA)

    def test_direct_url_origin_never_claims_collection(self) -> None:
        self.fetch()
        inventory = json.loads(self.state.inventory_path.read_text())
        self.assertEqual(len(inventory["entries"]), 1)
        entry = inventory["entries"][0]
        self.assertEqual(entry["input_origin"], "direct-url")
        self.assertEqual(entry["collections"], [])
        self.assertEqual(entry["observed_count"], 1)
        self.assertEqual(entry["stable_id"], VIDEO_ID)

    def test_repeated_unchanged_run_is_zero_extraction(self) -> None:
        self.fetch()
        calls_after_first = len(self.runner.calls)
        urlopen_after_first = self.urlopen.calls
        outcome = self.fetch()
        self.assertTrue(outcome.reused)
        self.assertEqual(outcome.outcome, "complete")
        self.assertEqual(len(self.runner.calls), calls_after_first)
        self.assertEqual(self.urlopen.calls, urlopen_after_first)

    def test_blocklisted_video_never_reaches_the_extractor(self) -> None:
        blocklist = Blocklist(self.state)
        blocklist.reject(
            BlocklistEntry(
                video_id=VIDEO_ID,
                rejected_at="2026-09-19T00:00:00Z",
                reason="operator rejected this item",
                source="tiktok",
            )
        )
        outcome = self.fetch()
        self.assertEqual(outcome.outcome, "blocked")
        self.assertIn("blocklist", outcome.reason)
        self.assertEqual(self.runner.calls, [], "no probe, no download, nothing")

    def test_gate_failure_blocks_without_download(self) -> None:
        self.runner.gate_report = fixtures.gate_report(challenge_found=())
        outcome = self.fetch()
        self.assertEqual(outcome.outcome, "blocked")
        self.assertEqual(self.runner.download_calls, 0)
        run_dir = Path(outcome.run_dir)
        record = json.loads((run_dir / "fetch.json").read_text())
        self.assertEqual(record["outcome"], "blocked")

    def test_denied_extraction_is_recorded_not_retried(self) -> None:
        self.runner.extraction_payload = {
            "outcome": "denied",
            "reason": "access denied by the server",
            "detail": {"observed": "HTTP 403"},
        }
        outcome = self.fetch()
        self.assertEqual(outcome.outcome, "denied")
        self.assertEqual(self.runner.download_calls, 1, "never retried automatically")
        record = json.loads((Path(outcome.run_dir) / "fetch.json").read_text())
        self.assertEqual(record["outcome"], "denied")
        self.assertEqual(record["retried"], False)

    def test_html_served_as_video_is_unsupported(self) -> None:
        self.runner.media_bytes = fixtures.html_bytes()
        outcome = self.fetch()
        self.assertEqual(outcome.outcome, "unsupported")
        self.assertIn("HTML", outcome.reason)
        # The rejected media is not cached and binds no hash.
        self.assertIsNone(outcome.media_sha256)
        cached = list(self.state.cache_dir.glob("*.mp4"))
        self.assertEqual(cached, [])

    def test_oversize_media_is_budget_exceeded(self) -> None:
        self.runner.ffprobe_document_text = fixtures.ffprobe_document(duration="700.0")
        outcome = self.fetch()
        self.assertEqual(outcome.outcome, "budget_exceeded")

    def test_unsupported_url_form_is_a_pipeline_error(self) -> None:
        with self.assertRaises(PipelineError):
            fetch_url(
                "https://vm.tiktok.com/ZMabcdef/",
                state=self.state,
                share_root=self.share,
                venv_dir=self.venv,
                runner=self.runner,
            )
        self.assertEqual(self.runner.calls, [])

    def test_force_reextracts_explicitly(self) -> None:
        self.fetch()
        calls_after_first = len(self.runner.calls)
        outcome = self.fetch(force=True)
        self.assertFalse(outcome.reused)
        self.assertGreater(len(self.runner.calls), calls_after_first)


class PrepareTests(PipelineTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.fetch_outcome = self.fetch()
        self.calls_after_fetch = len(self.runner.calls)

    def test_prepare_produces_frames_audio_and_provenance(self) -> None:
        outcome = self.prepare()
        self.assertEqual(outcome.outcome, "complete")
        self.assertFalse(outcome.reused)
        run_dir = Path(outcome.run_dir)

        frames = sorted((run_dir / "frames").glob("hybrid_*.png"))
        self.assertEqual(len(frames), outcome.frame_count)
        self.assertLessEqual(len(frames), config.MAX_BASELINE_FRAMES)
        self.assertGreater(len(frames), 0)

        audio = run_dir / "audio.wav"
        self.assertTrue(audio.is_file())
        self.assertEqual(audio.read_bytes()[:4], b"RIFF")

        manifest = json.loads((run_dir / "sampling-manifest.json").read_text())
        self.assertEqual(manifest["media_sha256"], MEDIA_SHA)
        self.assertEqual(manifest["container_image_id"], fixtures.FAKE_IMAGE_ID)
        self.assertEqual(manifest["audio"]["sample_rate_hz"], 16000)
        self.assertEqual(manifest["audio"]["channels"], 1)
        for frame in manifest["frames"]:
            self.assertEqual(len(frame["sha256"]), 64)
            self.assertTrue(frame["selection_reasons"])
            self.assertIn("timestamp_seconds", frame)
            # Crops are metadata only: no crop pixel artifacts exist.
            self.assertTrue(
                frame["is_crop_candidate"] is False or frame["crop_metadata_only"]
            )
        self.assertFalse((run_dir / "frames" / "crops").exists())

        # meta.json records the completed stage with timings and versions.
        meta = json.loads((run_dir / "meta.json").read_text())
        record = meta["stages"]["prepare"]
        self.assertEqual(record["result"]["outcome"], "complete")
        self.assertEqual(record["input_sha256"], MEDIA_SHA)
        self.assertEqual(len(record["output_sha256"]), 64)
        self.assertTrue(record["started_at"])
        self.assertTrue(record["finished_at"])
        self.assertEqual(
            meta["tool_versions"]["ffmpeg_container_image_id"], fixtures.FAKE_IMAGE_ID
        )

        # Processed state gains the prepare fingerprint.
        processed = Processed(self.state).get(VIDEO_ID)
        self.assertIn("prepare", processed.stage_fingerprints)

    def test_unchanged_prepare_is_zero_container_work(self) -> None:
        first = self.prepare()
        self.assertFalse(first.reused)
        containers_after_prepare = self.runner.container_calls
        second = self.prepare()
        self.assertTrue(second.reused)
        self.assertEqual(second.outcome, "complete")
        # Zero re-preparation: no ffmpeg container work on the repeat. The
        # only allowed extra call is the read-only `podman inspect` used to
        # resolve the image ID for the fingerprint.
        self.assertEqual(self.runner.container_calls, containers_after_prepare)

    def test_sampler_rule_change_invalidates_prepare(self) -> None:
        self.prepare()
        calls_after_prepare = len(self.runner.calls)
        original = config.SAMPLING_RULES_VERSION
        try:
            config.SAMPLING_RULES_VERSION = "99"
            outcome = self.prepare()
        finally:
            config.SAMPLING_RULES_VERSION = original
        self.assertFalse(outcome.reused)
        self.assertEqual(outcome.outcome, "complete")
        self.assertGreater(len(self.runner.calls), calls_after_prepare)

    def test_missing_derived_artifacts_force_recompute(self) -> None:
        outcome = self.prepare()
        run_dir = Path(outcome.run_dir)
        for png in (run_dir / "frames").glob("hybrid_*.png"):
            png.unlink()
        calls = len(self.runner.calls)
        second = self.prepare()
        self.assertFalse(second.reused)
        self.assertGreater(len(self.runner.calls), calls)

    def test_prepare_unknown_id_fails_cleanly(self) -> None:
        outcome = prepare_video(
            "9999999999999999999", state=self.state, runner=self.runner
        )
        self.assertEqual(outcome.outcome, "failed")
        self.assertIn("fetch-url", outcome.reason)

    def test_prepare_blocked_id_is_refused(self) -> None:
        # A blocklisted video cannot be prepared even with a fetch record.
        self.prepare()
        blocklist = Blocklist(self.state)
        blocklist.reject(
            BlocklistEntry(
                video_id=VIDEO_ID,
                rejected_at="2026-09-19T13:00:00Z",
                reason="operator rejected",
            )
        )
        rejected = self.prepare(force=True)
        self.assertEqual(rejected.outcome, "blocked")
        self.assertIn("blocklist", rejected.reason)

    def test_deadline_exceeded_records_budget_outcome(self) -> None:
        original = config.BUDGETS
        try:
            config.BUDGETS = type(original)(**{
                **{f.name: getattr(original, f.name) for f in original.__dataclass_fields__.values()},
                "cpu_prep_deadline_seconds": 0.0,
            })
            outcome = self.prepare(force=True)
        finally:
            config.BUDGETS = original
        self.assertEqual(outcome.outcome, "budget_exceeded")
        meta = json.loads((Path(outcome.run_dir) / "meta.json").read_text())
        self.assertEqual(
            meta["stages"]["prepare"]["result"]["outcome"], "budget_exceeded"
        )
        processed = Processed(self.state).get(VIDEO_ID)
        self.assertNotIn("prepare", processed.stage_fingerprints)


class StatusTests(PipelineTestBase):
    def test_status_reports_fetched_and_prepared_state(self) -> None:
        self.fetch()
        status = video_status(VIDEO_ID, state=self.state)
        self.assertEqual(status["items"][0]["video_id"], VIDEO_ID)
        self.assertEqual(status["items"][0]["extraction_outcome"], "complete")
        self.assertNotIn("prepare", status["items"][0].get("stages", []))

        self.prepare()
        status = video_status(VIDEO_ID, state=self.state)
        self.assertIn("prepare", status["items"][0]["stages"])
        self.assertEqual(status["items"][0]["stage_outcomes"]["prepare"], "complete")

    def test_status_of_unknown_id_and_empty_state(self) -> None:
        status = video_status("4242424242424242424", state=self.state)
        self.assertEqual(status["items"][0]["processed"], False)
        empty = video_status(None, state=self.state)
        self.assertEqual(empty["items"], [])


class RecordInventoryTests(unittest.TestCase):
    """``_record_inventory``: first observation wins (PRD section 4).

    A fetch is processing provenance, never an enumeration fact: it must
    preserve an existing entry's origin, discovery time, counts,
    completeness and collection associations, and may only fill a missing
    author.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = StateRoot(Path(tmp.name) / "state")
        self.now = datetime.datetime(2026, 9, 21, tzinfo=datetime.timezone.utc)

    def _collect(self, video_id: str, *, with_author: bool = True) -> None:
        InventoryStore(self.state).save(
            [
                InventoryEntry(
                    source="tiktok",
                    stable_id=video_id,
                    canonical_url=f"https://www.tiktok.com/@fix_author/video/{video_id}",
                    input_origin="collection",
                    discovered_at="2026-09-21T08:00:00Z",
                    collections=(
                        "https://www.tiktok.com/@bruno.narro/collection/fixture",
                    ),
                    declared_count=3,
                    observed_count=3,
                    completeness="complete",
                    author="fix_author" if with_author else None,
                )
            ]
        )

    def test_fetch_preserves_collection_association_and_origin(self) -> None:
        self._collect("1111111111111111111")
        _record_inventory(
            self.state, "1111111111111111111",
            "https://www.tiktok.com/@fix_author/video/1111111111111111111",
            "fix_author", self.now,
        )
        entry = InventoryStore(self.state).load()[0]
        self.assertEqual(entry.input_origin, "collection")
        self.assertEqual(entry.discovered_at, "2026-09-21T08:00:00Z")
        self.assertEqual(entry.declared_count, 3)
        self.assertEqual(entry.observed_count, 3)
        self.assertEqual(
            entry.collections,
            ("https://www.tiktok.com/@bruno.narro/collection/fixture",),
        )

    def test_fetch_fills_only_a_missing_author(self) -> None:
        self._collect("2222222222222222222", with_author=False)
        _record_inventory(
            self.state, "2222222222222222222",
            "https://www.tiktok.com/@fix_author/video/2222222222222222222",
            "fix_author", self.now,
        )
        entry = InventoryStore(self.state).load()[0]
        self.assertEqual(entry.author, "fix_author")
        self.assertEqual(entry.input_origin, "collection")
        self.assertEqual(entry.discovered_at, "2026-09-21T08:00:00Z")

    def test_fetch_creates_direct_url_entry_when_absent(self) -> None:
        _record_inventory(
            self.state, "3333333333333333333",
            "https://www.tiktok.com/@other/video/3333333333333333333",
            "other", self.now,
        )
        entries = InventoryStore(self.state).load()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].input_origin, "direct-url")
        self.assertEqual(entries[0].discovered_at, "2026-09-21T00:00:00Z")
        self.assertEqual(entries[0].collections, ())
        self.assertEqual(entries[0].completeness, "complete")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
