"""Milestone-2 configuration constants: pins, paths, budgets, gate rules."""

from __future__ import annotations

import unittest

from tiktok_ingest import config


class ExtractorConfigTests(unittest.TestCase):
    def test_pin_matches_methodology(self) -> None:
        self.assertEqual(config.EXTRACTOR_REQUIREMENT, "yt-dlp==2026.08.19")

    def test_runtime_locations_live_outside_git_under_local_share(self) -> None:
        self.assertIn(".local/share", str(config.SHARE_ROOT))
        self.assertTrue(config.SHARE_ROOT.is_absolute())
        self.assertEqual(config.EXTRACTOR_VENV_DIR.parent, config.SHARE_ROOT)
        self.assertEqual(config.EXTRACTOR_RUNNER_PATH.parent, config.SHARE_ROOT)

    def test_gate_rules_exist_for_both_protected_areas(self) -> None:
        self.assertTrue(config.EXTRACTOR_GATE_APP_API_SYMBOLS)
        self.assertTrue(config.EXTRACTOR_GATE_CHALLENGE_SYMBOLS)
        self.assertNotEqual(
            set(config.EXTRACTOR_GATE_APP_API_SYMBOLS),
            set(config.EXTRACTOR_GATE_CHALLENGE_SYMBOLS),
        )

    def test_cookie_options_are_forbidden(self) -> None:
        self.assertIn("--cookies", config.EXTRACTOR_FORBIDDEN_OPTIONS)
        self.assertIn("--cookies-from-browser", config.EXTRACTOR_FORBIDDEN_OPTIONS)


class SamplingConfigTests(unittest.TestCase):
    def test_methodology_3_2_parameters(self) -> None:
        self.assertEqual(config.SAMPLING_UNIFORM_FPS, 2)
        self.assertEqual(config.SAMPLING_UNIFORM_MIN_GAP_SECONDS, 4.0)
        self.assertEqual(config.SAMPLING_SCENE_THRESHOLD, 0.3)
        self.assertEqual(config.SAMPLING_TEXT_REGION_THRESHOLD, 0.15)
        self.assertEqual(config.SAMPLING_DEDUP_WINDOW_SECONDS, 0.08)
        self.assertEqual(config.MAX_BASELINE_FRAMES, 64)
        self.assertEqual(config.SAMPLING_CROP_CANDIDATE_CAP, 16)

    def test_crop_band_geometry(self) -> None:
        self.assertEqual(config.SAMPLING_CROP_HEIGHT_FRACTION, 0.35)
        self.assertEqual(config.SAMPLING_CROP_Y_OFFSET_FRACTION, 0.65)


class ContainerConfigTests(unittest.TestCase):
    def test_image_ref_and_prefix_are_recorded(self) -> None:
        self.assertEqual(
            config.FFMPEG_IMAGE_REF, "localhost/voice-assistant_whisper:latest"
        )
        self.assertEqual(config.FFMPEG_IMAGE_ID_PREFIX, "51b152706d")


class BudgetConfigTests(unittest.TestCase):
    def test_milestone_2_media_budgets(self) -> None:
        self.assertEqual(config.BUDGETS.media_max_duration_seconds, 600)
        self.assertEqual(config.BUDGETS.media_max_bytes, 250 * 1024 * 1024)
        self.assertEqual(config.BUDGETS.fetch_deadline_seconds, 300)
        self.assertEqual(config.BUDGETS.metadata_ttl_seconds, 24 * 3600)

    def test_cpu_preparation_budgets(self) -> None:
        self.assertEqual(config.BUDGETS.cpu_prep_deadline_seconds, 1200)
        self.assertEqual(config.BUDGETS.cpu_prep_max_bytes, 2 * 1024 * 1024 * 1024)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
