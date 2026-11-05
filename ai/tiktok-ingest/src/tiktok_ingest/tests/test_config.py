"""Guard tests for the adopted defaults (P1-P4)."""

from __future__ import annotations

import dataclasses
import unittest

from tiktok_ingest import config


class AdoptedDefaultsTests(unittest.TestCase):
    def test_p1_vision_model_is_llm_hub_chat(self) -> None:
        self.assertEqual(config.VISION_MODEL, "qwen-3.8-27b")
        self.assertEqual(config.LLM_HUB_CHAT_API_BASE, "http://127.0.0.1:8081/v1")

    def test_p2_transcript_policy(self) -> None:
        self.assertEqual(config.WHISPER_MODEL, "large-v3")
        self.assertEqual(config.WHISPER_LANGUAGE, "es")
        self.assertTrue(config.TRANSCRIPT_POLICY.startswith("full-audio"))

    def test_p3_sampling(self) -> None:
        self.assertEqual(config.SAMPLING_STRATEGY, "hybrid")
        self.assertEqual(config.MAX_BASELINE_FRAMES, 64)
        self.assertEqual(config.MAX_TARGETED_REREADS, 8)

    def test_p4_budgets(self) -> None:
        budgets = config.BUDGETS
        self.assertEqual(budgets.batch_pilot_clips, 5)
        self.assertEqual(budgets.media_max_duration_seconds, 600)
        self.assertEqual(budgets.media_max_bytes, 250 * 1024 * 1024)
        self.assertEqual(budgets.fetch_deadline_seconds, 300)
        self.assertEqual(budgets.http_max_metadata_concurrent, 2)
        self.assertEqual(budgets.metadata_ttl_seconds, 24 * 3600)
        self.assertEqual(budgets.cpu_prep_deadline_seconds, 1200)
        self.assertEqual(budgets.cpu_prep_max_bytes, 2 * 1024 * 1024 * 1024)
        self.assertEqual(budgets.vision_stage_deadline_seconds, 900)
        self.assertEqual(budgets.audio_stage_deadline_seconds, 600)
        self.assertEqual(budgets.working_cache_bytes, 10 * 1024 * 1024 * 1024)

    def test_budget_dataclass_is_frozen(self) -> None:
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.BUDGETS.batch_pilot_clips = 99  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
