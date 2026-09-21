"""Prepare-stage flows: image resolution, container argv, budgets, fingerprints."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.prepare import (
    ContainerFFmpeg,
    DerivedOutputBudgetExceeded,
    DerivedOutputTracker,
    PrepareError,
    StageClock,
    StageDeadlineExceeded,
    container_argv,
    parse_showinfo_events,
    prepare_fingerprint,
    rebase_resampled_events,
    original_stream_events,
    resolve_image_id,
)
from tiktok_ingest.resume import build_fingerprint
from tiktok_ingest.tests import fixtures


class ResolveImageIdTests(unittest.TestCase):
    def test_full_id_is_returned(self) -> None:
        image_id = resolve_image_id(
            config.FFMPEG_IMAGE_REF,
            runner=lambda argv, timeout=None: (0, fixtures.FAKE_IMAGE_ID + "\n", ""),
        )
        self.assertEqual(image_id, fixtures.FAKE_IMAGE_ID)
        self.assertRegex(image_id, r"^[0-9a-f]{64}$")

    def test_resolution_failure_fails_closed(self) -> None:
        with self.assertRaises(PrepareError) as ctx:
            resolve_image_id(
                config.FFMPEG_IMAGE_REF,
                runner=lambda argv, timeout=None: (1, "", "no such image"),
            )
        self.assertIn("failing closed", str(ctx.exception))

    def test_prefix_or_tag_is_refused(self) -> None:
        with self.assertRaises(PrepareError):
            resolve_image_id(
                config.FFMPEG_IMAGE_REF,
                runner=lambda argv, timeout=None: (0, "51b152706d82\n", ""),
            )
        with self.assertRaises(PrepareError):
            resolve_image_id(
                config.FFMPEG_IMAGE_REF,
                runner=lambda argv, timeout=None: (0, "latest\n", ""),
            )


class ContainerArgvTests(unittest.TestCase):
    def test_network_isolated_read_only_with_mounts(self) -> None:
        argv = container_argv(
            fixtures.FAKE_IMAGE_ID,
            ["ffmpeg", "-version"],
            read_only_mounts=(("/state/cache", "/media"),),
            writable_mount=(("/state/runs/r/v"), "/work"),
        )
        self.assertEqual(argv[0], "podman")
        self.assertIn("--network", argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--tmpfs", argv)
        self.assertEqual(argv[argv.index("--tmpfs") + 1], "/tmp")
        self.assertIn("-v", argv)
        self.assertTrue(any(item.endswith(":ro") for item in argv))
        # The immutable image ID precedes the command (podman run ... ID CMD).
        image_index = argv.index(fixtures.FAKE_IMAGE_ID)
        self.assertEqual(argv[image_index + 1], "ffmpeg")

    def test_refuses_unresolved_image_ids(self) -> None:
        with self.assertRaises(PrepareError):
            container_argv("localhost/voice-assistant_whisper:latest", ["true"])


class ShowinfoParsingTests(unittest.TestCase):
    def test_parses_pts_events(self) -> None:
        stderr = fixtures.showinfo_stderr([(12800, 1.0), (25600, 2.0)])
        events = parse_showinfo_events(stderr)
        self.assertEqual(events, [(12800, 1.0), (25600, 2.0)])

    def test_rebase_onto_original_time_base(self) -> None:
        # fps=2 rewrites PTS onto a 1/fps time base; conversion uses the
        # exact integer PTS (methodology §8.5).
        rebased = rebase_resampled_events([(7, 1.0), (15, 2.5)], 2, 1 / 12800)
        self.assertEqual(rebased, [(44800, 1.0), (96000, 2.5)])

    def test_original_stream_events_keeps_exact_integer_pts(self) -> None:
        # Regression (first real run, 2026-09-19): recomputing pts from the
        # printed pts_time ("11.0667" -> 6 significant digits, tb 1/15360)
        # drifted ±1 tick and produced pts that do not exist in the stream,
        # silently dropping 10 of 29 planned frames at extraction. The
        # integer pts from showinfo must be kept as-is.
        events = [
            (169984, 11.0667),  # printed pts_time would rebase to 169985
            (260096, 16.9333),  # printed pts_time would rebase to 260095
            (320000, 20.8333),  # printed pts_time would rebase to 319999
        ]
        self.assertEqual(original_stream_events(events), events)
        time_base = 1 / 15360
        drifted = [round(pts_time / time_base) for _, pts_time in events]
        self.assertEqual(drifted, [169985, 260095, 319999])
        self.assertNotEqual([pts for pts, _ in events], drifted)


class StageClockTests(unittest.TestCase):
    def test_raises_past_deadline_with_explicit_outcome_context(self) -> None:
        ticks = iter([0.0, 10.0, 1300.0])
        clock = StageClock(1200.0, monotonic=lambda: next(ticks))
        clock.check()  # 10s elapsed: fine
        with self.assertRaises(StageDeadlineExceeded) as ctx:
            clock.check()  # 1300s elapsed: past the 20-minute deadline
        self.assertIn("1200", str(ctx.exception))

    def test_remaining_never_negative(self) -> None:
        state = {"now": 0.0}

        def clock_fn() -> float:
            return state["now"]

        clock = StageClock(10.0, monotonic=clock_fn)
        self.assertEqual(clock.remaining(), 10.0)
        state["now"] = 99.0
        self.assertEqual(clock.remaining(), 0.0)


class DerivedOutputTrackerTests(unittest.TestCase):
    def test_counts_files_under_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frames").mkdir()
            (root / "frames" / "a.png").write_bytes(b"x" * 100)
            (root / "audio.wav").write_bytes(b"y" * 50)
            tracker = DerivedOutputTracker(root, max_bytes=1000)
            self.assertEqual(tracker.current_bytes(), 150)
            tracker.check()

    def test_exceeding_cap_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "audio.wav").write_bytes(b"y" * 300)
            tracker = DerivedOutputTracker(root, max_bytes=200)
            with self.assertRaises(DerivedOutputBudgetExceeded):
                tracker.check()


class ContainerFFmpegPathMappingTests(unittest.TestCase):
    """Media and output paths must map to the container mounts."""

    def _ffmpeg(self, runner, run_dir, media):
        return ContainerFFmpeg(
            fixtures.FAKE_IMAGE_ID,
            runner=runner,
            media_path=media,
            run_dir=run_dir,
            clock=StageClock(1200.0),
        )

    def test_source_maps_to_read_only_media_mount(self) -> None:
        captured: list[list[str]] = []

        def runner(argv, timeout=None):
            captured.append(list(argv))
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "cache" / "abc.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(fixtures.mp4_bytes())
            run_dir = Path(tmp) / "run"
            ffmpeg = self._ffmpeg(runner, run_dir, media)
            ffmpeg.integrity_pass()
        argv = captured[0]
        self.assertIn("/media/abc.mp4", argv)
        self.assertNotIn(str(media), argv)
        self.assertIn("--network", argv)

    def test_outputs_map_to_writable_work_mount(self) -> None:
        captured: list[list[str]] = []

        def runner(argv, timeout=None):
            captured.append(list(argv))
            work_mount_host = next(
                item.split(":/work")[0]
                for item in argv
                if item.endswith(":/work")
            )
            for item in argv:
                if item.startswith("/work/") and item.endswith(".wav"):
                    target = Path(work_mount_host) / item[len("/work/"):]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "cache" / "abc.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(fixtures.mp4_bytes())
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            ffmpeg = self._ffmpeg(runner, run_dir, media)
            audio = ffmpeg.extract_audio()
            self.assertEqual(audio.name, "audio.wav")
            self.assertTrue(audio.is_file())
            mapped = captured[0]
            self.assertIn("/work/audio.wav", mapped)
            self.assertNotIn(str(run_dir / "audio.wav"), mapped)


class FingerprintTests(unittest.TestCase):
    def test_prepare_fingerprint_binds_media_image_and_sampler(self) -> None:
        fingerprint = prepare_fingerprint("ab" * 32, fixtures.FAKE_IMAGE_ID)
        self.assertEqual(fingerprint["stage"], "prepare")
        self.assertEqual(fingerprint["input_sha256"], "ab" * 32)
        self.assertEqual(
            fingerprint["versions"],
            {
                "ffmpeg_image_id": fixtures.FAKE_IMAGE_ID,
                "sampler_rules": config.SAMPLING_RULES_VERSION,
            },
        )

    def test_sampler_rule_change_changes_fingerprint(self) -> None:
        old = build_fingerprint(
            "prepare",
            "ab" * 32,
            {"ffmpeg_image_id": fixtures.FAKE_IMAGE_ID, "sampler_rules": "1"},
        )
        new = build_fingerprint(
            "prepare",
            "ab" * 32,
            {"ffmpeg_image_id": fixtures.FAKE_IMAGE_ID, "sampler_rules": "2"},
        )
        self.assertNotEqual(old, new)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
