"""Guard tests for the adopted defaults and gate constants (P1-P4)."""

from __future__ import annotations

import dataclasses
import unittest

from tiktok_ingest import config


class AdoptedDefaultsTests(unittest.TestCase):
    def test_p1_vision_models(self) -> None:
        self.assertEqual(config.VISION_MODEL, "qwen3.5:9b")
        self.assertEqual(config.HIGH_DENSITY_VISION_MODEL, "qwen3.8:27b")
        self.assertNotEqual(config.VISION_MODEL, config.HIGH_DENSITY_VISION_MODEL)

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
        self.assertEqual(budgets.vision_stage_deadline_seconds, 600)
        self.assertEqual(budgets.audio_stage_deadline_seconds, 600)
        self.assertEqual(budgets.working_cache_bytes, 10 * 1024 * 1024 * 1024)

    def test_isolation_gates_are_configuration_only(self) -> None:
        gates = config.GATES
        self.assertEqual(gates.isolated_ollama_version, "0.34.1")
        self.assertEqual(gates.global_ollama_version, "0.14.2")
        self.assertEqual(gates.free_vram_gate_mib, 20480)
        self.assertEqual(gates.swap_delta_limit_mib, 512)
        self.assertEqual(gates.vram_delta_limit_mib, {"qwen3.5:9b": 12288})
        self.assertFalse(gates.allow_gpu_co_residency)

    def test_budget_dataclass_is_frozen(self) -> None:
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.BUDGETS.batch_pilot_clips = 99  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
