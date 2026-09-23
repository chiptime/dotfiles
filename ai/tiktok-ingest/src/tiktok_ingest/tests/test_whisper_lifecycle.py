"""Whisper lifecycle tests: fake podman runner, zero real services.

Covers the bounded, identity-checked stop/restore coordination for the
KNOWN shared whisper service: positive identity/config verification
before any mutation, verified stops, same-service restoration, and
fail-closed behavior with operator-readable reasons that never promise
rollback.
"""

from __future__ import annotations

import json
import unittest

from tiktok_ingest import whisper_lifecycle as wl


def inspect_output(
    *,
    name: str = wl.WHISPER_CONTAINER_NAME,
    image: str = "localhost/voice-assistant_whisper:latest",
    running: bool = True,
    status: str = "running",
    env: dict[str, str] | None = None,
) -> str:
    env_entries = [
        "PATH=/usr/bin",
        *(f"{key}={value}" for key, value in (env or {}).items()),
        "SOME_APP_API_KEY=super-secret-value",
    ]
    return json.dumps(
        [
            {
                "Name": f"/{name}",
                "ImageName": image,
                "Config": {"Image": image, "Env": env_entries},
                "State": {"Running": running, "Status": status},
            }
        ]
    )


class FakeRunner:
    """Scripts podman argv -> (rc, out, err); records every call."""

    def __init__(self, script: dict[str, tuple[int, str, str]]) -> None:
        self.script = {tuple(argv): result for argv, result in script.items()}
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float | None = None):
        self.calls.append(list(argv))
        if tuple(argv) in self.script:
            return self.script[tuple(argv)]
        return 0, "", ""

    def saw(self, prefix: list[str]) -> bool:
        return any(call[: len(prefix)] == prefix for call in self.calls)


INSPECT_OK = inspect_output(
    env={"DEVICE": "cuda", "COMPUTE_TYPE": "float16", "LANGUAGE": "es"}
)


class TestInspect(unittest.TestCase):
    def test_inspect_parses_identity_and_filters_env(self) -> None:
        runner = FakeRunner(
            {("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (0, INSPECT_OK, "")}
        )
        info = wl.inspect_whisper(runner)
        assert info is not None
        self.assertEqual(info.name, wl.WHISPER_CONTAINER_NAME)
        self.assertTrue(info.running)
        self.assertEqual(
            dict(info.env),
            {"DEVICE": "cuda", "COMPUTE_TYPE": "float16", "LANGUAGE": "es"},
        )
        self.assertNotIn(
            "super-secret-value",
            info.describe(),
            "unrelated container secrets are never captured or reported",
        )

    def test_inspect_missing_container_returns_none(self) -> None:
        runner = FakeRunner(
            {
                ("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (
                    125,
                    "",
                    "Error: no such container",
                )
            }
        )
        self.assertIsNone(wl.inspect_whisper(runner))

    def test_inspect_podman_failure_fails_closed(self) -> None:
        runner = FakeRunner(
            {
                ("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (
                    1,
                    "",
                    "podman daemon unhappy",
                )
            }
        )
        with self.assertRaises(wl.WhisperLifecycleError) as ctx:
            wl.inspect_whisper(runner)
        self.assertIn("refusing to touch any service", str(ctx.exception))

    def test_inspect_garbage_output_fails_closed(self) -> None:
        runner = FakeRunner(
            {
                ("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (
                    0,
                    "not-json",
                    "",
                )
            }
        )
        with self.assertRaises(wl.WhisperLifecycleError):
            wl.inspect_whisper(runner)


class TestVerifyIdentity(unittest.TestCase):
    def test_wrong_name_is_refused_before_mutation(self) -> None:
        info = wl._parse_inspect(inspect_output(name="some-other-whisper"))
        with self.assertRaises(wl.WhisperLifecycleError) as ctx:
            wl.verify_known_service(info)
        self.assertIn("no arbitrary or unidentified workload", str(ctx.exception))

    def test_missing_image_is_refused(self) -> None:
        raw = json.dumps(
            [
                {
                    "Name": f"/{wl.WHISPER_CONTAINER_NAME}",
                    "Config": {"Image": "", "Env": []},
                    "State": {"Running": True, "Status": "running"},
                }
            ]
        )
        with self.assertRaises(wl.WhisperLifecycleError):
            wl.verify_known_service(wl._parse_inspect(raw))


class TestStop(unittest.TestCase):
    def test_stop_is_verified_with_positive_checks(self) -> None:
        stopped = inspect_output(
            running=False, status="exited",
            env={"DEVICE": "cuda", "COMPUTE_TYPE": "float16", "LANGUAGE": "es"},
        )
        runner = FakeRunner(
            {
                ("podman", "stop", "--timeout", "30", wl.WHISPER_CONTAINER_NAME): (0, "", ""),
                ("podman", "ps", "--format", "{{.Names}}"): (0, "other-container", ""),
                ("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (0, stopped, ""),
            }
        )
        info = wl._parse_inspect(INSPECT_OK)
        after = wl.stop_whisper(info, runner=runner)
        self.assertFalse(after.running)
        self.assertTrue(runner.saw(["podman", "stop", "--timeout", "30"]))
        self.assertTrue(runner.saw(["podman", "ps"]))
        self.assertTrue(runner.saw(["podman", "inspect"]))
        # order: stop -> ps -> inspect
        self.assertLess(
            runner.calls.index(["podman", "stop", "--timeout", "30", wl.WHISPER_CONTAINER_NAME]),
            runner.calls.index(["podman", "ps", "--format", "{{.Names}}"]),
        )

    def test_stop_command_failure_reports_without_rollback_promise(self) -> None:
        runner = FakeRunner(
            {
                ("podman", "stop", "--timeout", "30", wl.WHISPER_CONTAINER_NAME): (
                    125, "", "stop failed",
                )
            }
        )
        info = wl._parse_inspect(INSPECT_OK)
        with self.assertRaises(wl.WhisperLifecycleError) as ctx:
            wl.stop_whisper(info, runner=runner)
        message = str(ctx.exception)
        self.assertIn("may or may not have stopped", message)
        self.assertIn("no rollback is performed or implied", message)
        self.assertFalse(runner.saw(["podman", "ps"]))

    def test_stop_with_service_still_running_fails(self) -> None:
        runner = FakeRunner(
            {
                ("podman", "stop", "--timeout", "30", wl.WHISPER_CONTAINER_NAME): (0, "", ""),
                ("podman", "ps", "--format", "{{.Names}}"): (
                    0, wl.WHISPER_CONTAINER_NAME, "",
                ),
            }
        )
        info = wl._parse_inspect(INSPECT_OK)
        with self.assertRaises(wl.WhisperLifecycleError) as ctx:
            wl.stop_whisper(info, runner=runner)
        self.assertIn("still appears in podman ps", str(ctx.exception))

    def test_stop_refuses_unidentified_service_before_mutation(self) -> None:
        runner = FakeRunner({})
        info = wl._parse_inspect(inspect_output(name="not-the-known-service"))
        with self.assertRaises(wl.WhisperLifecycleError):
            wl.stop_whisper(info, runner=runner)
        self.assertEqual(runner.calls, [], "no mutation may run for an unidentified service")


class TestRestore(unittest.TestCase):
    def test_restore_starts_the_same_container_and_verifies(self) -> None:
        runner = FakeRunner(
            {
                ("podman", "start", wl.WHISPER_CONTAINER_NAME): (0, "", ""),
                ("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (0, INSPECT_OK, ""),
            }
        )
        before = wl._parse_inspect(INSPECT_OK)
        after = wl.restore_whisper(before, runner=runner)
        self.assertTrue(after.running)
        self.assertTrue(wl.same_service_config(before, after))
        self.assertTrue(runner.saw(["podman", "start", wl.WHISPER_CONTAINER_NAME]))

    def test_restore_with_changed_config_is_rejected(self) -> None:
        changed = inspect_output(env={"DEVICE": "cpu", "COMPUTE_TYPE": "int8"})
        runner = FakeRunner(
            {
                ("podman", "start", wl.WHISPER_CONTAINER_NAME): (0, "", ""),
                ("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (0, changed, ""),
            }
        )
        before = wl._parse_inspect(INSPECT_OK)
        with self.assertRaises(wl.WhisperLifecycleError) as ctx:
            wl.restore_whisper(before, runner=runner)
        self.assertIn("SAME service/config was not restored", str(ctx.exception))
        self.assertIn("no rollback", str(ctx.exception))

    def test_restore_start_failure_blocks_without_rollback(self) -> None:
        runner = FakeRunner(
            {
                ("podman", "start", wl.WHISPER_CONTAINER_NAME): (
                    125, "", "cannot start",
                )
            }
        )
        before = wl._parse_inspect(INSPECT_OK)
        with self.assertRaises(wl.WhisperLifecycleError) as ctx:
            wl.restore_whisper(before, runner=runner)
        self.assertIn("NOT restored", str(ctx.exception))

    def test_restore_not_running_after_start_is_rejected(self) -> None:
        not_running = inspect_output(running=False, status="exited")
        runner = FakeRunner(
            {
                ("podman", "start", wl.WHISPER_CONTAINER_NAME): (0, "", ""),
                ("podman", "inspect", wl.WHISPER_CONTAINER_NAME): (0, not_running, ""),
            }
        )
        before = wl._parse_inspect(INSPECT_OK)
        with self.assertRaises(wl.WhisperLifecycleError) as ctx:
            wl.restore_whisper(before, runner=runner)
        self.assertIn("does not report a running state", str(ctx.exception))


class TestSameConfig(unittest.TestCase):
    def test_same_config_detection(self) -> None:
        a = wl._parse_inspect(INSPECT_OK)
        b = wl._parse_inspect(INSPECT_OK)
        self.assertTrue(wl.same_service_config(a, b))
        changed = wl._parse_inspect(inspect_output(env={"DEVICE": "cpu"}))
        self.assertFalse(wl.same_service_config(a, changed))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
