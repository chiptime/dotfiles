"""GPU lease tests: persisted marker, unified readiness wait, lease rules.

Zero podman, zero GPU: the lifecycle hooks are doubles and the clock is
deterministic. These tests prove the LEASE contract (S1 single owner,
S2 one health wait, S3 persisted marker) — identity verification, gates
and stage behavior are covered by their own suites.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import gpu_lease
from tiktok_ingest.state import StateRoot
from tiktok_ingest.tests.test_guided import (
    MANAGED_GRANT,
    GuidedTestCase,
    RestoreLifecycleFakeStages,
    ScriptedConsent,
    fake_id,
    seed_job,
    seed_scan,
)
from tiktok_ingest.whisper_lifecycle import WhisperServiceInfo


def fixture_info(
    env: tuple[tuple[str, str], ...] = (("DEVICE", "cuda"),)
) -> WhisperServiceInfo:
    return WhisperServiceInfo(
        name="voice-assistant-whisper",
        image="fixture/whisper:latest",
        state="running",
        running=True,
        env=env,
    )


class FakeClock:
    """Deterministic monotonic clock + sleep recorder (never sleeps)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class RecordingLifecycle:
    """Hook doubles that record calls and script failures."""

    def __init__(
        self,
        *,
        stop_raises: BaseException | None = None,
        restore_raises: BaseException | None = None,
        health_results: list[tuple[bool, str]] | None = None,
        marker_path: Path | None = None,
    ) -> None:
        self.stop_raises = stop_raises
        self.restore_raises = restore_raises
        self.health_results = list(health_results or [(True, "healthy")])
        self.calls: list[str] = []
        self.marker_path = marker_path
        self.marker_exists_at_stop: bool | None = None
        self.marker_payload_at_stop: dict[str, Any] | None = None

    def hooks(self) -> gpu_lease.LeaseHooks:
        return gpu_lease.LeaseHooks(
            stop=self.do_stop,
            restore=self.do_restore,
            health=self.do_health,
        )

    def do_stop(self, info: Any) -> Any:
        self.calls.append("stop")
        if self.marker_path is not None:
            self.marker_exists_at_stop = self.marker_path.exists()
            if self.marker_exists_at_stop:
                self.marker_payload_at_stop = json.loads(
                    self.marker_path.read_text(encoding="utf-8")
                )
        if self.stop_raises is not None:
            raise self.stop_raises
        return info

    def do_restore(self, info: Any) -> Any:
        self.calls.append("restore")
        if self.restore_raises is not None:
            raise self.restore_raises
        return info

    def do_health(self) -> tuple[bool, str]:
        self.calls.append("health")
        if len(self.health_results) > 1:
            return self.health_results.pop(0)
        return self.health_results[0]


class LeaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()
        self.marker_path = gpu_lease.lease_marker_path(self.state)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def marker(self, **kwargs: Any) -> gpu_lease.LeaseMarkerFile:
        kwargs.setdefault("pid", 4242)
        kwargs.setdefault(
            "start_time_reader", lambda pid: 31633 if pid == 4242 else None
        )
        kwargs.setdefault("now_iso", lambda: "2026-11-03T00:00:00Z")
        return gpu_lease.LeaseMarkerFile(self.state, **kwargs)

    def lease(
        self,
        lifecycle: RecordingLifecycle,
        *,
        marker: gpu_lease.LeaseMarkerFile | None = None,
    ) -> gpu_lease.GpuLease:
        if marker is None:
            marker = self.marker()
        return gpu_lease.GpuLease(hooks=lifecycle.hooks(), marker=marker)


class TestLeaseMarker(LeaseTestCase):
    def test_marker_written_before_stop_with_identity_fields_only(self) -> None:
        info = fixture_info(env=(("DEVICE", "cuda"), ("SECRET_API_KEY", "x")))
        lifecycle = RecordingLifecycle(marker_path=self.marker_path)
        lease = self.lease(lifecycle)
        lease.acquire(info)
        # S3: the marker is on disk BEFORE the stop command is issued.
        self.assertIs(lifecycle.marker_exists_at_stop, True)
        payload = lifecycle.marker_payload_at_stop
        assert payload is not None  # narrowing for the type checker
        self.assertEqual(payload["schema"], gpu_lease.LEASE_MARKER_SCHEMA)
        self.assertEqual(payload["version"], gpu_lease.LEASE_MARKER_VERSION)
        self.assertEqual(payload["owner_pid"], 4242)
        self.assertEqual(payload["owner_start_time"], 31633)
        self.assertEqual(payload["created_at"], "2026-11-03T00:00:00Z")
        whisper = payload["whisper"]
        self.assertEqual(whisper["name"], "voice-assistant-whisper")
        self.assertEqual(whisper["image"], "fixture/whisper:latest")
        # Only WHISPER_IDENTITY_ENV_KEYS identity env — never secrets.
        self.assertEqual(whisper["env"], [["DEVICE", "cuda"]])
        self.assertNotIn("SECRET_API_KEY", json.dumps(payload))
        # The file on disk after the stop is the same document.
        on_disk = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, payload)
        self.assertIsNotNone(lease.stopped_info)

    def test_marker_cleared_only_after_verified_healthy_restore(self) -> None:
        lifecycle = RecordingLifecycle()
        lease = self.lease(lifecycle)
        lease.acquire(fixture_info())
        self.assertTrue(self.marker_path.exists())
        outcome = lease.restore_once()
        self.assertEqual(outcome.status, "healthy")
        self.assertFalse(self.marker_path.exists())

    def test_marker_kept_when_restore_fails(self) -> None:
        lifecycle = RecordingLifecycle(
            restore_raises=RuntimeError("fixture restore failure")
        )
        lease = self.lease(lifecycle)
        lease.acquire(fixture_info())
        outcome = lease.restore_once()
        self.assertEqual(outcome.status, "restore_failed")
        self.assertTrue(self.marker_path.exists())

    def test_marker_kept_when_health_times_out(self) -> None:
        lifecycle = RecordingLifecycle(
            health_results=[(False, "whisper health endpoint unreachable: fixture")]
        )
        lease = self.lease(lifecycle)
        lease.acquire(fixture_info())
        clock = FakeClock()
        outcome = lease.restore_once(monotonic=clock.monotonic, sleep=clock.sleep)
        self.assertEqual(outcome.status, "health_timeout")
        self.assertTrue(self.marker_path.exists())

    def test_marker_kept_and_no_restore_when_stop_interrupted(self) -> None:
        lifecycle = RecordingLifecycle(stop_raises=KeyboardInterrupt())
        lease = self.lease(lifecycle)
        with self.assertRaises(KeyboardInterrupt):
            lease.acquire(fixture_info())
        # S5: the stop result is UNKNOWN — no restore is attempted, the
        # marker stays so the next start can prove the stop was ours.
        self.assertTrue(lease.stop_interrupted)
        self.assertIsNone(lease.stopped_info)
        self.assertNotIn("restore", lifecycle.calls)
        self.assertTrue(self.marker_path.exists())

    def test_marker_optional_without_state_root(self) -> None:
        lifecycle = RecordingLifecycle()
        lease = gpu_lease.GpuLease(hooks=lifecycle.hooks(), marker=None)
        lease.acquire(fixture_info())
        outcome = lease.restore_once()
        self.assertEqual(outcome.status, "healthy")
        # No marker path anywhere: behaviour is otherwise identical.
        self.assertEqual(lifecycle.calls, ["stop", "restore", "health"])
        marker = gpu_lease.LeaseMarkerFile(None)
        marker.write(fixture_info())
        marker.clear()
        self.assertIsNone(marker.read())

    def test_marker_read_returns_none_when_absent_and_clear_is_idempotent(
        self,
    ) -> None:
        marker = self.marker()
        self.assertIsNone(marker.read())
        marker.clear()  # absent: no error
        marker.write(fixture_info())
        self.assertIsInstance(marker.read(), dict)
        marker.clear()
        self.assertIsNone(marker.read())


class TestOwnerAlive(unittest.TestCase):
    def test_alive_when_pid_exists_and_start_time_matches(self) -> None:
        reader = lambda pid: 31633 if pid == 4242 else None  # noqa: E731
        marker = {"owner_pid": 4242, "owner_start_time": 31633}
        self.assertTrue(gpu_lease.owner_alive(marker, start_time_reader=reader))

    def test_dead_when_pid_missing(self) -> None:
        reader = lambda pid: None  # noqa: E731
        marker = {"owner_pid": 4242, "owner_start_time": 31633}
        self.assertFalse(gpu_lease.owner_alive(marker, start_time_reader=reader))

    def test_dead_on_start_time_mismatch_pid_reuse(self) -> None:
        # PID 4242 was recycled: same pid, different process start time.
        reader = lambda pid: 999999  # noqa: E731
        marker = {"owner_pid": 4242, "owner_start_time": 31633}
        self.assertFalse(gpu_lease.owner_alive(marker, start_time_reader=reader))

    def test_dead_on_malformed_marker(self) -> None:
        reader = lambda pid: 31633  # noqa: E731
        for marker in (
            {},
            {"owner_pid": 4242},
            {"owner_start_time": 31633},
            {"owner_pid": "4242", "owner_start_time": 31633},
            {"owner_pid": 4242, "owner_start_time": None},
        ):
            self.assertFalse(
                gpu_lease.owner_alive(marker, start_time_reader=reader), marker
            )

    def test_real_proc_stat_parsing_field_22(self) -> None:
        # comm (field 2) may contain spaces and parens: parsing must use
        # the LAST ')'. Fields 3..21 are placeholders; field 22 = 424242.
        tail_fields = ["R"] + [str(n) for n in range(4, 22)]  # fields 3..21
        stat_line = "4242 (python3 (venv)) " + " ".join(
            tail_fields + ["424242", "123456"]
        )
        reader = lambda pid: stat_line if pid == 4242 else None  # noqa: E731
        self.assertEqual(gpu_lease.process_start_time(4242, reader=reader), 424242)
        self.assertIsNone(gpu_lease.process_start_time(9999, reader=reader))

    def test_proc_stat_garbage_is_none_not_a_crash(self) -> None:
        reader = lambda pid: "no parens no fields"  # noqa: E731
        self.assertIsNone(gpu_lease.process_start_time(4242, reader=reader))


class TestUnifiedReadinessWait(unittest.TestCase):
    def test_immediate_healthy_first_probe_no_sleep_no_feedback(self) -> None:
        probes: list[tuple[bool, str]] = []
        feedback: list[float] = []

        def health() -> tuple[bool, str]:
            result = (True, "healthy")
            probes.append(result)
            return result

        clock = FakeClock()
        healthy, detail = gpu_lease.wait_for_whisper_health(
            health,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            waiting_feedback=feedback.append,
        )
        self.assertTrue(healthy)
        self.assertEqual(detail, "healthy")
        self.assertEqual(len(probes), 1)
        self.assertEqual(clock.slept, [])
        self.assertEqual(feedback, [])

    def test_transient_then_healthy_one_interval_feedback_once(self) -> None:
        results = [
            (False, "whisper health endpoint unreachable: connection reset"),
            (True, "healthy"),
        ]
        feedback: list[float] = []

        def health() -> tuple[bool, str]:
            return results.pop(0) if len(results) > 1 else results[0]

        clock = FakeClock()
        healthy, detail = gpu_lease.wait_for_whisper_health(
            health,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            waiting_feedback=feedback.append,
        )
        self.assertTrue(healthy)
        self.assertEqual(detail, "healthy")
        self.assertEqual(clock.slept, [1.0])
        self.assertEqual(feedback, [60.0])

    def test_deadline_timeout_returns_last_real_detail(self) -> None:
        calls = {"probes": 0}

        def health() -> tuple[bool, str]:
            calls["probes"] += 1
            return False, "whisper health endpoint unreachable: fixture"

        clock = FakeClock()
        healthy, detail = gpu_lease.wait_for_whisper_health(
            health, monotonic=clock.monotonic, sleep=clock.sleep
        )
        self.assertFalse(healthy)
        self.assertEqual(detail, "whisper health endpoint unreachable: fixture")
        # Bounded: immediate first probe plus one probe per ~1s interval,
        # stopping at the 60s deadline — no busy loop, no overshoot beyond
        # one probe.
        self.assertEqual(calls["probes"], 61)
        self.assertEqual(len(clock.slept), 60)
        self.assertTrue(all(seconds == 1.0 for seconds in clock.slept))
        self.assertGreaterEqual(clock.now, 60.0)

    def test_bounds_are_the_documented_ones(self) -> None:
        self.assertEqual(gpu_lease.WHISPER_RESTORE_READY_DEADLINE_SECONDS, 60.0)
        self.assertEqual(
            gpu_lease.WHISPER_RESTORE_READY_POLL_INTERVAL_SECONDS, 1.0
        )

    def test_keyboard_interrupt_propagates(self) -> None:
        def health() -> tuple[bool, str]:
            raise KeyboardInterrupt

        clock = FakeClock()
        with self.assertRaises(KeyboardInterrupt):
            gpu_lease.wait_for_whisper_health(
                health, monotonic=clock.monotonic, sleep=clock.sleep
            )


class TestLeaseRules(LeaseTestCase):
    def test_exactly_one_start_per_lease(self) -> None:
        lifecycle = RecordingLifecycle()
        lease = self.lease(lifecycle)
        lease.acquire(fixture_info())
        first = lease.restore_once()
        self.assertEqual(first.status, "healthy")
        # A second restore_once NEVER re-invokes the start hook: it
        # replays the recorded outcome of the ONE authorized attempt.
        second = lease.restore_once()
        self.assertEqual(second.status, "healthy")
        self.assertEqual(lifecycle.calls.count("restore"), 1)
        self.assertTrue(lease.restore_attempted)
        self.assertTrue(lease.restore_completed)

    def test_restore_once_without_stop_is_a_no_op(self) -> None:
        lifecycle = RecordingLifecycle()
        lease = self.lease(lifecycle)
        outcome = lease.restore_once()
        self.assertEqual(outcome.status, "not_stopped")
        self.assertEqual(lifecycle.calls, [])

    def test_interrupted_restore_is_recorded_and_never_retried(self) -> None:
        lifecycle = RecordingLifecycle(restore_raises=KeyboardInterrupt())
        lease = self.lease(lifecycle)
        lease.acquire(fixture_info())
        with self.assertRaises(KeyboardInterrupt):
            lease.restore_once()
        self.assertEqual(lease.restore_interrupt_phase, "start")
        self.assertTrue(lease.restore_attempted)
        self.assertFalse(lease.restore_completed)
        outcome = lease.restore_once()  # replay: no second start
        self.assertEqual(outcome.status, "interrupted")
        self.assertEqual(lifecycle.calls.count("restore"), 1)
        self.assertTrue(self.marker_path.exists())

    def test_interrupted_health_wait_recorded_separately(self) -> None:
        lifecycle = RecordingLifecycle()

        def raise_ki() -> tuple[bool, str]:
            raise KeyboardInterrupt

        hooks = gpu_lease.LeaseHooks(
            stop=lifecycle.do_stop,
            restore=lifecycle.do_restore,
            health=raise_ki,
        )
        lease = gpu_lease.GpuLease(hooks=hooks, marker=self.marker())
        lease.acquire(fixture_info())
        with self.assertRaises(KeyboardInterrupt):
            lease.restore_once()
        self.assertEqual(lease.restore_interrupt_phase, "health_wait")
        self.assertTrue(lease.restore_attempted)
        self.assertFalse(lease.restore_completed)
        self.assertTrue(self.marker_path.exists())

    def test_context_manager_restores_in_finally_on_exception(self) -> None:
        lifecycle = RecordingLifecycle()
        with self.assertRaises(RuntimeError):
            with gpu_lease.leased_window(
                lifecycle.hooks(), fixture_info(), marker=self.marker()
            ):
                raise RuntimeError("fixture body failure")
        self.assertEqual(lifecycle.calls, ["stop", "restore", "health"])
        self.assertFalse(self.marker_path.exists())

    def test_context_manager_restores_in_finally_on_interrupt(self) -> None:
        lifecycle = RecordingLifecycle()
        with self.assertRaises(KeyboardInterrupt):
            with gpu_lease.leased_window(
                lifecycle.hooks(), fixture_info(), marker=self.marker()
            ):
                raise KeyboardInterrupt
        self.assertEqual(lifecycle.calls.count("restore"), 1)
        self.assertFalse(self.marker_path.exists())

    def test_context_manager_no_restore_when_stop_interrupted(self) -> None:
        lifecycle = RecordingLifecycle(stop_raises=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            with gpu_lease.leased_window(
                lifecycle.hooks(), fixture_info(), marker=self.marker()
            ):
                self.fail("body must not run when the stop itself interrupted")
        self.assertEqual(lifecycle.calls, ["stop"])
        self.assertTrue(self.marker_path.exists())

    def test_context_manager_vram_check_after_stop_and_covered_by_finally(
        self,
    ) -> None:
        lifecycle = RecordingLifecycle()
        checks: list[str] = []
        body_ran: list[str] = []

        with gpu_lease.leased_window(
            lifecycle.hooks(), fixture_info(), marker=self.marker()
        ) as lease:
            body_ran.append("first")
            self.assertIsNotNone(lease.stopped_info)

        def failing_check() -> None:
            checks.append("vram-failed")
            raise RuntimeError("fixture: not enough free VRAM")

        with self.assertRaises(RuntimeError):
            with gpu_lease.leased_window(
                lifecycle.hooks(),
                fixture_info(),
                marker=self.marker(),
                vram_check=failing_check,
            ):
                body_ran.append("second")

        # The check runs after the verified stop and BEFORE any body
        # work, and the finally still restored the stopped service.
        self.assertEqual(body_ran, ["first"])
        self.assertEqual(checks, ["vram-failed"])
        self.assertEqual(lifecycle.calls.count("stop"), 2)
        self.assertEqual(lifecycle.calls.count("restore"), 2)
        self.assertFalse(self.marker_path.exists())


class TestGuidedMarkerAroundGpuPhase(GuidedTestCase):
    """The marker is written/cleared around a REAL GuidedRun GPU phase."""

    def test_marker_written_before_stop_and_cleared_after_healthy_restore(
        self,
    ) -> None:
        marker_path = gpu_lease.lease_marker_path(self.state)
        seed_scan(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare"))

        class MarkerProbingStages(RestoreLifecycleFakeStages):
            def __init__(self) -> None:
                super().__init__()
                self.marker_at_vision: dict[str, Any] | None = None

            def do_vision(self, video_id: str, state: StateRoot) -> Any:
                if marker_path.exists():
                    self.marker_at_vision = json.loads(
                        marker_path.read_text(encoding="utf-8")
                    )
                return super().do_vision(video_id, state)

        stages = MarkerProbingStages()
        code, report, _ = self.run_guided_with(
            stages, ScriptedConsent(list(MANAGED_GRANT)), ids=[fake_id(1)]
        )
        self.assertEqual(code, 0)
        self.assertFalse(report["whisper_restore_failed"])
        # During the vision window (service stopped) the marker existed
        # and held the verified identity snapshot.
        assert stages.marker_at_vision is not None  # narrowing
        self.assertEqual(
            stages.marker_at_vision["schema"], gpu_lease.LEASE_MARKER_SCHEMA
        )
        self.assertEqual(
            stages.marker_at_vision["whisper"]["name"], "voice-assistant-whisper"
        )
        # After the verified healthy restore the marker is gone.
        self.assertFalse(marker_path.exists())
        self.assertEqual(len(stages.stop_calls), 1)
        self.assertEqual(len(stages.restore_calls), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
