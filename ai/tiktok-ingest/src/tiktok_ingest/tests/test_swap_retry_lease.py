"""Swap-retry GPU-lease tests: the lease owns the stop/restore window.

T2 of the GPU-lease ODD (S1/S3/S7): the collection-run swap retry stops
whisper through :class:`tiktok_ingest.gpu_lease.GpuLease`, so the
persisted marker ``<state root>/active_gpu_lease.json`` exists for
exactly the stop→…→restore window the retry owns — written BEFORE the
stop, cleared ONLY after the verified healthy restore, kept on every
other outcome (restore failure, health-wait timeout, interrupted stop,
interrupted restore) — and every drift/exit path that must not leave the
shared service down releases the lease with exactly ONE start.

All doubles come from the existing swap-retry harness: zero podman,
zero GPU, deterministic clock.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from tiktok_ingest import collection_run as cr
from tiktok_ingest import config
from tiktok_ingest import gpu_lease
from tiktok_ingest.tests.test_collection_run import KNOWN_INFO
from tiktok_ingest.tests.test_guided import fake_id
from tiktok_ingest.tests.test_swap_retry import (
    MIB,
    ScriptedVmstat,
    SwapRetryStages,
    SwapRetryTestCase,
)

GATE_MIB = config.GATES.free_vram_gate_mib


class SwapRetryLeaseTestCase(SwapRetryTestCase):
    """Harness addition: the lease-marker path of this run's state root."""

    def setUp(self) -> None:
        super().setUp()
        self.marker_path = gpu_lease.lease_marker_path(self.state)

    def marker_exists(self) -> bool:
        return self.marker_path.exists()

    def record_marker_at_restore(self, stages: SwapRetryStages) -> list[bool]:
        """Capture the marker's existence at each ``podman start``."""
        seen: list[bool] = []
        original = stages.do_whisper_restore

        def recording(info: Any) -> Any:
            seen.append(self.marker_exists())
            return original(info)

        stages.do_whisper_restore = recording
        return seen

    def slow_stop(self, stages: SwapRetryStages, seconds: float) -> None:
        """Make the stop consume fake time (measurable post-stop rate)."""
        original = stages.do_whisper_stop

        def slow(info: Any) -> Any:
            self.clock.sleep(seconds)
            return original(info)

        stages.do_whisper_stop = slow


class TestLeaseMarkerLifecycle(SwapRetryLeaseTestCase):
    """S3: the marker exists for exactly the stopped window."""

    def test_marker_present_during_window_and_cleared_after_restore(self) -> None:
        video_id = self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])
        marker_at_vision: list[bool] = []
        payloads: list[dict[str, Any]] = []
        original_vision = stages.do_vision

        def vision_sees_marker(vid: str, state: Any) -> Any:
            marker_at_vision.append(self.marker_exists())
            if self.marker_exists():
                payloads.append(
                    json.loads(self.marker_path.read_text(encoding="utf-8"))
                )
            return original_vision(vid, state)

        stages.do_vision = vision_sees_marker
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        # The marker was on disk while whisper was stopped for the
        # window (written BEFORE the stop command was issued).
        self.assertEqual(marker_at_vision, [True])
        self.assertEqual(payloads[0]["schema"], gpu_lease.LEASE_MARKER_SCHEMA)
        self.assertEqual(
            payloads[0]["whisper"]["name"], KNOWN_INFO.name
        )
        # Cleared after the verified healthy restore.
        self.assertFalse(self.marker_exists())
        self.assertEqual(stages.calls.count("whisper-stop-op"), 1)
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)

    def test_marker_kept_when_restore_fails_after_attempt(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"], whisper_restore_fails=True
        )
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn(
            "whisper restoration after the retry window failed",
            self.report["hard_stop_reason"],
        )
        # No verified healthy restore: the marker stays for reconcile.
        self.assertTrue(self.marker_exists())
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)

    def test_marker_kept_when_health_wait_times_out(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])
        health_calls = 0
        original_health = stages.do_whisper_health

        def failing_after_preconsent() -> tuple[bool, str]:
            nonlocal health_calls
            health_calls += 1
            if health_calls > 1:
                return False, "fixture: health endpoint unreachable"
            return original_health()

        stages.do_whisper_health = failing_after_preconsent
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn(
            "whisper restoration after the retry window did not become "
            "healthy",
            self.report["hard_stop_reason"],
        )
        # Health never verified: the marker stays.
        self.assertTrue(self.marker_exists())
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)

    def test_marker_kept_and_no_restore_when_stop_interrupted(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"], whisper_stop_raises_ki=True
        )
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "interrupted_unknown")
        # The stop result is UNKNOWN: no restore is attempted, and the
        # marker stays so the next start can prove the stop was ours.
        self.assertTrue(self.marker_exists())
        self.assertNotIn("whisper-restore-op", stages.calls)

    def test_marker_kept_when_restore_interrupted(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])

        def interrupted_restore(info: Any) -> Any:
            stages.calls.append("whisper-restore-op")
            raise KeyboardInterrupt()

        stages.do_whisper_restore = interrupted_restore
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "interrupted_unknown")
        self.assertTrue(record["restore_unknown_interrupt"])
        # The ONE start attempt was made and interrupted: never retried,
        # marker kept.
        self.assertTrue(self.marker_exists())
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)
        self.assertIn("ollama-stop", stages.calls)


class TestDriftPathsReleaseLeaseOnce(SwapRetryLeaseTestCase):
    """Every drift/exit path releases the window's lease exactly once."""

    def test_not_running_drift_restores_once_without_marker(self) -> None:
        """Site: not-running drift at re-admission.

        The pipeline did NOT stop whisper in this window, so no lease
        and NO marker exist for that restore: exactly one start under
        the granted retry authority, exactly as before the lease.
        """
        self.seed_failed_job()
        not_running = dataclasses.replace(
            KNOWN_INFO, running=False, state="exited"
        )
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            whisper_inspect_sequence=[KNOWN_INFO, not_running],
        )
        marker_at_restore = self.record_marker_at_restore(stages)
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        # Drift restore (no marker: not our stop) + final window restore
        # (marker present): two starts total, one per need.
        self.assertEqual(marker_at_restore, [False, True])
        self.assertEqual(stages.calls.count("whisper-stop-op"), 1)
        self.assertEqual(stages.calls.count("whisper-restore-op"), 2)
        self.assertFalse(self.marker_exists())

    def test_post_stop_measurement_failure_releases_lease_once(self) -> None:
        """Site: post-stop swap measurement failure at re-admission."""
        self.seed_failed_job()
        vmstat = ScriptedVmstat(deltas=[0.4 * MIB] * 13 + [None])
        stages = SwapRetryStages(vision_outcomes=["complete"])
        marker_at_restore = self.record_marker_at_restore(stages)
        self.run_flow(stages, vmstat=vmstat)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "measurement_unavailable")
        self.assertIn(
            "swap measurement unavailable", record["message"]
        )
        # The stop was verified, so the window was released (one start)
        # before the honest terminal state; marker cleared by it.
        self.assertEqual(marker_at_restore, [True])
        self.assertEqual(stages.calls.count("whisper-stop-op"), 1)
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)
        self.assertFalse(self.marker_exists())

    def test_post_stop_swap_drift_releases_each_window_once(self) -> None:
        """Site: post-stop swap-rate drift at re-admission."""
        self.seed_failed_job()
        vmstat = ScriptedVmstat(
            deltas=[0.4 * MIB] * 13 + [20 * MIB] + [0.4 * MIB] * 60
        )
        stages = SwapRetryStages(vision_outcomes=["complete"])
        self.slow_stop(stages, 5.0)
        marker_at_restore = self.record_marker_at_restore(stages)
        self.run_flow(stages, vmstat=vmstat)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        self.assertEqual(len(record["stable_resets"]), 1)
        # Two stop windows, one release each; every release of a window
        # WE stopped sees the marker, and the healthy restore clears it.
        self.assertEqual(marker_at_restore, [True, True])
        self.assertEqual(stages.calls.count("whisper-stop-op"), 2)
        self.assertEqual(stages.calls.count("whisper-restore-op"), 2)
        self.assertFalse(self.marker_exists())

    def test_gpu_probe_failure_releases_lease_once(self) -> None:
        """Site: raising free-VRAM probe after the verified stop."""
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])

        def raising_probe() -> int:
            raise RuntimeError("fixture: nvidia-smi failed")

        stages.do_gpu_free = raising_probe
        marker_at_restore = self.record_marker_at_restore(stages)
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn(
            "the free-VRAM probe at re-admission failed",
            record["message"],
        )
        self.assertEqual(marker_at_restore, [True])
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)
        self.assertFalse(self.marker_exists())

    def test_vram_drift_releases_each_window_once(self) -> None:
        """Site: insufficient free VRAM at re-admission (T13 shape)."""
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            gpu_free_sequence=[15000, GATE_MIB + 1],
        )
        marker_at_restore = self.record_marker_at_restore(stages)
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        self.assertIn(
            "only 15000 MiB free VRAM",
            record["stable_resets"][0]["reason"],
        )
        self.assertEqual(marker_at_restore, [True, True])
        self.assertEqual(stages.calls.count("whisper-stop-op"), 2)
        self.assertEqual(stages.calls.count("whisper-restore-op"), 2)
        self.assertFalse(self.marker_exists())

    def test_cap_exhaustion_at_readmission_releases_lease_once(self) -> None:
        """Site: re-admission consumed the remaining cooldown cap."""
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])
        self.slow_stop(stages, 130.0)
        marker_at_restore = self.record_marker_at_restore(stages)
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "cooldown_expired")
        self.assertIn(
            "cap exhausted during re-admission transitions",
            record["message"],
        )
        self.assertIn("Whisper restored healthy", record["message"] or "")
        self.assertEqual(marker_at_restore, [True])
        self.assertEqual(stages.calls.count("whisper-stop-op"), 1)
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)
        self.assertFalse(self.marker_exists())

    def test_archive_failure_releases_lease_once(self) -> None:
        """Site: failed original-evidence archive inside the retry."""
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])
        marker_at_restore = self.record_marker_at_restore(stages)
        with unittest.mock.patch.object(
            cr,
            "_archive_original_vision_attempt",
            side_effect=RuntimeError("fixture: archive blew up"),
        ):
            self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn(
            "the original-failure evidence archive failed",
            self.report["hard_stop_reason"],
        )
        self.assertEqual(marker_at_restore, [True])
        self.assertEqual(stages.calls.count("whisper-restore-op"), 1)
        self.assertFalse(self.marker_exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
