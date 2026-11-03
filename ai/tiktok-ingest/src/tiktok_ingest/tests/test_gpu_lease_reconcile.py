"""Startup reconcile of a stale GPU lease marker and the concurrency guard.

Specs S4-S6 of odd/tasks/tiktok-ingest-gpu-lease.md: a marker left by a
dead run is reconciled before any new GPU work (automatic restore under
the original run's restore grant, fail closed on anything unproven), and
a marker owned by a live foreign process blocks a new GPU window.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import gpu_lease
from tiktok_ingest.guided import GuidedError
from tiktok_ingest.state import StateRoot
from tiktok_ingest.tests.test_collection_run import (
    KNOWN_INFO,
    CollectionRunTestCase,
    CollectionStages,
    RecordingConsent,
)
from tiktok_ingest.tests.test_gpu_lease import FakeClock, fixture_info
from tiktok_ingest.tests.test_guided import (
    GuidedTestCase,
    RestoreLifecycleFakeStages,
    ScriptedConsent,
    fake_id,
    seed_job,
    seed_scan,
)
from tiktok_ingest.whisper_lifecycle import WhisperServiceInfo

DEAD_PID = 2_000_000_000  # above any Linux pid_max: never a live process


def write_marker(
    state: StateRoot,
    info: WhisperServiceInfo,
    *,
    pid: int = DEAD_PID,
    start_time: int | None = 1,
) -> Path:
    marker = gpu_lease.LeaseMarkerFile(
        state, pid=pid, start_time_reader=lambda _pid: start_time
    )
    marker.write(info)
    assert marker.path is not None
    return marker.path


def live_foreign_pid() -> tuple[int, int]:
    """The parent process: alive, not us, with its real start time."""
    pid = os.getppid()
    start = gpu_lease.process_start_time(pid)
    assert start is not None
    return pid, start


class FakeService:
    """inspect/restore/health doubles over one scripted service state."""

    def __init__(
        self,
        info: WhisperServiceInfo | None,
        *,
        restore_raises: BaseException | None = None,
        health: list[tuple[bool, str]] | None = None,
        inspect_raises: BaseException | None = None,
    ) -> None:
        self.info = info
        self.restore_raises = restore_raises
        self.health_script = list(health or [(True, "healthy")])
        self.inspect_raises = inspect_raises
        self.calls: list[str] = []

    def inspect(self) -> WhisperServiceInfo | None:
        self.calls.append("inspect")
        if self.inspect_raises is not None:
            raise self.inspect_raises
        return self.info

    def restore(self, info: WhisperServiceInfo) -> WhisperServiceInfo:
        self.calls.append("restore")
        if self.restore_raises is not None:
            raise self.restore_raises
        assert self.info is not None
        self.info = dataclasses.replace(self.info, running=True, state="running")
        return self.info

    def health(self) -> tuple[bool, str]:
        self.calls.append("health")
        if len(self.health_script) > 1:
            return self.health_script.pop(0)
        return self.health_script[0]


def stopped(info: WhisperServiceInfo) -> WhisperServiceInfo:
    return dataclasses.replace(info, running=False, state="exited")


class ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = StateRoot(Path(self._tmp.name) / "state")
        self.clock = FakeClock()

    def reconcile(self, service: FakeService) -> gpu_lease.ReconcileResult:
        return gpu_lease.reconcile_lease(
            gpu_lease.LeaseMarkerFile(self.state),
            inspect=service.inspect,
            restore=service.restore,
            health=service.health,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )

    def test_no_marker_is_a_no_op(self) -> None:
        service = FakeService(fixture_info())
        result = self.reconcile(service)
        self.assertEqual(result.action, "none")
        self.assertEqual(service.calls, [])

    def test_dead_owner_and_stopped_service_is_restored_once(self) -> None:
        path = write_marker(self.state, fixture_info())
        service = FakeService(stopped(fixture_info()))
        result = self.reconcile(service)
        self.assertEqual(result.action, "restored")
        self.assertEqual(service.calls.count("restore"), 1)
        self.assertIn("health", service.calls)
        self.assertFalse(path.exists())

    def test_dead_owner_and_running_service_only_clears_the_marker(self) -> None:
        path = write_marker(self.state, fixture_info())
        service = FakeService(fixture_info())
        result = self.reconcile(service)
        self.assertEqual(result.action, "cleared")
        self.assertNotIn("restore", service.calls)
        self.assertFalse(path.exists())

    def test_identity_mismatch_fails_closed(self) -> None:
        path = write_marker(self.state, fixture_info())
        other = dataclasses.replace(stopped(fixture_info()), image="other/whisper:2")
        service = FakeService(other)
        result = self.reconcile(service)
        self.assertEqual(result.action, "blocked")
        self.assertIn("identity", result.detail)
        self.assertNotIn("restore", service.calls)
        self.assertTrue(path.exists())

    def test_env_mismatch_fails_closed(self) -> None:
        path = write_marker(self.state, fixture_info())
        other = stopped(fixture_info(env=(("DEVICE", "cpu"),)))
        result = self.reconcile(FakeService(other))
        self.assertEqual(result.action, "blocked")
        self.assertTrue(path.exists())

    def test_missing_container_fails_closed(self) -> None:
        path = write_marker(self.state, fixture_info())
        result = self.reconcile(FakeService(None))
        self.assertEqual(result.action, "blocked")
        self.assertTrue(path.exists())

    def test_inspect_failure_fails_closed(self) -> None:
        path = write_marker(self.state, fixture_info())
        service = FakeService(fixture_info(), inspect_raises=RuntimeError("podman hung"))
        result = self.reconcile(service)
        self.assertEqual(result.action, "blocked")
        self.assertIn("podman hung", result.detail)
        self.assertTrue(path.exists())

    def test_restore_failure_keeps_the_marker(self) -> None:
        path = write_marker(self.state, fixture_info())
        service = FakeService(
            stopped(fixture_info()), restore_raises=RuntimeError("start failed")
        )
        result = self.reconcile(service)
        self.assertEqual(result.action, "blocked")
        self.assertIn("start failed", result.detail)
        self.assertEqual(service.calls.count("restore"), 1)
        self.assertTrue(path.exists())

    def test_health_timeout_keeps_the_marker(self) -> None:
        path = write_marker(self.state, fixture_info())
        service = FakeService(
            stopped(fixture_info()), health=[(False, "unreachable")]
        )
        result = self.reconcile(service)
        self.assertEqual(result.action, "blocked")
        self.assertIn("unreachable", result.detail)
        self.assertEqual(service.calls.count("restore"), 1)
        self.assertTrue(path.exists())

    def test_interrupted_restore_propagates_and_keeps_the_marker(self) -> None:
        path = write_marker(self.state, fixture_info())
        service = FakeService(stopped(fixture_info()), restore_raises=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.reconcile(service)
        self.assertTrue(path.exists())

    def test_live_foreign_owner_blocks_without_touching_the_service(self) -> None:
        pid, start = live_foreign_pid()
        path = write_marker(self.state, fixture_info(), pid=pid, start_time=start)
        service = FakeService(stopped(fixture_info()))
        result = self.reconcile(service)
        self.assertEqual(result.action, "blocked_live_owner")
        self.assertIn(str(pid), result.detail)
        self.assertEqual(service.calls, [])
        self.assertTrue(path.exists())

    def test_unreadable_marker_fails_closed(self) -> None:
        path = gpu_lease.lease_marker_path(self.state)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        service = FakeService(stopped(fixture_info()))
        result = self.reconcile(service)
        self.assertEqual(result.action, "blocked")
        self.assertEqual(service.calls, [])
        self.assertTrue(path.exists())

    def test_result_serializes_for_reports(self) -> None:
        write_marker(self.state, fixture_info())
        result = self.reconcile(FakeService(stopped(fixture_info())))
        document = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(document["action"], "restored")


class AcquireGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = StateRoot(Path(self._tmp.name) / "state")
        self.stops: list[Any] = []

    def lease(self) -> gpu_lease.GpuLease:
        return gpu_lease.GpuLease(
            hooks=gpu_lease.LeaseHooks(
                stop=self.stops.append,
                restore=lambda info: info,
                health=lambda: (True, "healthy"),
            ),
            marker=gpu_lease.LeaseMarkerFile(self.state),
        )

    def test_live_foreign_owner_refuses_before_any_stop(self) -> None:
        pid, start = live_foreign_pid()
        path = write_marker(self.state, fixture_info(), pid=pid, start_time=start)
        before = path.read_bytes()
        with self.assertRaises(gpu_lease.GpuLeaseBusyError):
            self.lease().acquire(fixture_info())
        self.assertEqual(self.stops, [])
        self.assertEqual(path.read_bytes(), before)

    def test_dead_owner_marker_is_taken_over(self) -> None:
        path = write_marker(self.state, fixture_info())
        self.lease().acquire(fixture_info())
        self.assertEqual(len(self.stops), 1)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["owner_pid"], os.getpid()
        )


class StoppedAtStartGuidedStages(RestoreLifecycleFakeStages):
    """Whisper left stopped by a dead run: inspect reports the real state."""

    def __init__(self) -> None:
        super().__init__()
        self.whisper_running = False

    def _info(self) -> object:
        info = super()._info()
        return dataclasses.replace(
            info,  # type: ignore[arg-type]
            running=self.whisper_running,
            state="running" if self.whisper_running else "exited",
        )


class GuidedReconcileTests(GuidedTestCase):
    def _run(self, stages: RestoreLifecycleFakeStages) -> tuple[int, dict[str, Any]]:
        from tiktok_ingest.guided import run_guided

        return run_guided(
            state=self.state,
            ids=[fake_id(1)],
            stages=stages.functions(),
            consent=ScriptedConsent([("run-plan", True)]),
            out=lambda _text: None,
        )

    def test_stale_marker_is_restored_before_the_run_binds_whisper(self) -> None:
        seed_scan(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare"))
        path = write_marker(self.state, fixture_info())
        stages = StoppedAtStartGuidedStages()
        _code, report = self._run(stages)
        self.assertEqual(report["gpu_lease_reconcile"]["action"], "restored")
        self.assertEqual(stages.calls[0], "whisper-restore")
        self.assertFalse(path.exists())

    def test_live_foreign_owner_stops_guided_before_any_mutation(self) -> None:
        seed_scan(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare"))
        pid, start = live_foreign_pid()
        write_marker(self.state, fixture_info(), pid=pid, start_time=start)
        stages = StoppedAtStartGuidedStages()
        with self.assertRaisesRegex(GuidedError, "another tiktok-ingest run"):
            self._run(stages)
        self.assertNotIn("whisper-restore", stages.calls)
        self.assertNotIn("whisper-stop", stages.calls)


class InspectableCollectionStages(CollectionStages):
    """inspect reports a stopped container instead of ``None``."""

    def do_whisper_inspect(self) -> WhisperServiceInfo | None:
        self.calls.append("whisper-inspect")
        return dataclasses.replace(
            KNOWN_INFO,
            running=self.whisper_running,
            state="running" if self.whisper_running else "exited",
        )


class CollectionReconcileTests(CollectionRunTestCase):
    def test_stale_marker_is_restored_before_the_plan(self) -> None:
        path = write_marker(self.state, KNOWN_INFO)
        stages = InspectableCollectionStages(whisper_running=False)
        code, report, _ = self.run_collection(stages, RecordingConsent())
        self.assertEqual(code, 0, report)
        self.assertEqual(report["gpu_lease_reconcile"]["action"], "restored")
        restore_index = stages.calls.index("whisper-restore-op")
        self.assertLess(restore_index, stages.calls.index("whisper-stop-op"))
        self.assertFalse(path.exists())

    def test_unproven_identity_hard_stops_the_run(self) -> None:
        path = write_marker(
            self.state, dataclasses.replace(KNOWN_INFO, image="other/whisper:9")
        )
        stages = InspectableCollectionStages(whisper_running=False)
        code, report, _ = self.run_collection(stages, RecordingConsent())
        self.assertEqual(code, 1)
        self.assertIn("gpu lease", report["hard_stop_reason"])
        self.assertNotIn("whisper-restore-op", stages.calls)
        self.assertNotIn("vision", " ".join(stages.calls))
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
