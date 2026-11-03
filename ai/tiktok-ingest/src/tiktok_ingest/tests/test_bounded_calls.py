"""Stability guarantees: no external call or termination can hang a run.

Covers the roadmap "Stability phase 1" items: every podman / nvidia-smi /
process probe carries a finite timeout, a stalled Whisper service ends in
an explicit deadline outcome instead of a forever-blocking read, and
SIGTERM/SIGHUP reach the existing interrupt-cleanup paths.
"""

from __future__ import annotations

import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tiktok_ingest import cli, runtime
from tiktok_ingest import whisper_lifecycle as wl
from tiktok_ingest.ollama_runtime import verify_pid_gone
from tiktok_ingest.prepare import StageDeadlineExceeded, resolve_image_id
from tiktok_ingest.tests.test_whisper_client import (
    FIXED_MASK,
    RFC_KEY,
    handshake_ok,
    text_frame,
)
from tiktok_ingest.vision import assert_whisper_stopped, query_gpu
from tiktok_ingest.whisper_client import transcribe_audio


class RecordingRunner:
    """Records (argv, timeout) and answers every call with a scripted rc."""

    def __init__(self, outputs: dict[str, tuple[int, str, str]] | None = None) -> None:
        self.calls: list[tuple[list[str], float | None]] = []
        self.outputs = outputs or {}

    def __call__(self, argv: list[str], timeout: float | None = None):
        self.calls.append((list(argv), timeout))
        for prefix, result in self.outputs.items():
            if " ".join(argv).startswith(prefix):
                return result
        return 0, "", ""

    def timeout_for(self, prefix: list[str]) -> float | None:
        for argv, timeout in self.calls:
            if argv[: len(prefix)] == prefix:
                return timeout
        raise AssertionError(f"no call starting with {prefix!r}: {self.calls!r}")


def _inspect_json(running: bool) -> str:
    import json

    return json.dumps(
        [
            {
                "Name": f"/{wl.WHISPER_CONTAINER_NAME}",
                "ImageName": "img",
                "Config": {"Image": "img", "Env": []},
                "State": {"Running": running, "Status": "running" if running else "exited"},
            }
        ]
    )


class SubprocessTimeoutTests(unittest.TestCase):
    """Every lifecycle/probe subprocess passes a finite, positive timeout."""

    def assertBounded(self, value: float | None) -> None:
        self.assertIsNotNone(value, "subprocess call has no timeout")
        assert value is not None
        self.assertGreater(value, 0)

    def test_inspect_whisper_is_bounded(self) -> None:
        runner = RecordingRunner({"podman inspect": (0, _inspect_json(True), "")})
        wl.inspect_whisper(runner)
        self.assertBounded(runner.timeout_for(["podman", "inspect"]))

    def test_stop_whisper_is_bounded_beyond_the_grace(self) -> None:
        runner = RecordingRunner({"podman inspect": (0, _inspect_json(False), "")})
        info = wl.WhisperServiceInfo(
            name=wl.WHISPER_CONTAINER_NAME, image="img", state="running", running=True, env=()
        )
        wl.stop_whisper(info, runner=runner)
        stop_timeout = runner.timeout_for(["podman", "stop"])
        self.assertBounded(stop_timeout)
        assert stop_timeout is not None
        # podman itself escalates to SIGKILL after the grace; our bound
        # must leave room for that escalation to finish.
        self.assertGreater(stop_timeout, wl.STOP_GRACE_SECONDS)
        self.assertBounded(runner.timeout_for(["podman", "ps"]))

    def test_restore_whisper_is_bounded(self) -> None:
        runner = RecordingRunner({"podman inspect": (0, _inspect_json(True), "")})
        info = wl.WhisperServiceInfo(
            name=wl.WHISPER_CONTAINER_NAME, image="img", state="exited", running=False, env=()
        )
        wl.restore_whisper(info, runner=runner)
        self.assertBounded(runner.timeout_for(["podman", "start"]))

    def test_vision_probes_are_bounded(self) -> None:
        runner = RecordingRunner({"nvidia-smi": (0, "24576, 1000, 23576\n", "")})
        query_gpu(runner)
        assert_whisper_stopped(runner)
        self.assertBounded(runner.timeout_for(["nvidia-smi"]))
        self.assertBounded(runner.timeout_for(["podman", "ps"]))

    def test_resolve_image_id_is_bounded(self) -> None:
        runner = RecordingRunner({"podman inspect": (0, "a" * 64 + "\n", "")})
        resolve_image_id("localhost/ffmpeg:pinned", runner=runner)
        self.assertBounded(runner.timeout_for(["podman", "inspect"]))

    def test_pid_probe_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "ollama.pid"
            pid_path.write_text("4242", encoding="ascii")
            runner = RecordingRunner()
            verify_pid_gone(pid_path, runner=runner)
        self.assertBounded(runner.timeout_for(["kill"]))

    def test_timed_out_stop_fails_closed(self) -> None:
        # A hung podman surfaces as the runner's normalized timeout result
        # (rc 124) and must stop the stage, never be read as a success.
        runner = RecordingRunner({"podman stop": (124, "", "runner timeout after 90s")})
        info = wl.WhisperServiceInfo(
            name=wl.WHISPER_CONTAINER_NAME, image="img", state="running", running=True, env=()
        )
        with self.assertRaisesRegex(wl.WhisperLifecycleError, "runner timeout"):
            wl.stop_whisper(info, runner=runner)

    def test_timed_out_inspect_is_not_a_missing_container(self) -> None:
        # Partial stderr captured before the timeout must not be read as
        # "the container does not exist".
        runner = RecordingRunner(
            {"podman inspect": (124, "", "runner timeout after 30s: no such")}
        )
        with self.assertRaises(wl.WhisperLifecycleError):
            wl.inspect_whisper(runner)

    def test_timed_out_pid_probe_does_not_confirm_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "ollama.pid"
            pid_path.write_text("4242", encoding="ascii")
            runner = RecordingRunner({"kill": (124, "", "runner timeout after 15s")})
            self.assertFalse(verify_pid_gone(pid_path, runner=runner))


class StallingSocket:
    """A socket whose script is a list of chunks; ``None`` means "no data yet".

    In blocking mode (timeout ``None``) an empty read would hang forever on a
    real socket, so the fake fails the test loudly instead of returning EOF.
    """

    def __init__(self, chunks: list[bytes | None], clock: "FakeClock | None" = None) -> None:
        self.chunks = list(chunks)
        self.clock = clock  # when set, every recv costs one second
        self.timeout: float | None = None
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        if self.clock is not None:
            self.clock.now += 1.0
        if self.chunks and self.chunks[0] is not None:
            chunk = self.chunks.pop(0)
            assert chunk is not None
            return chunk
        if self.chunks:
            self.chunks.pop(0)  # consume one "no data yet" step
        if self.timeout is None:
            raise AssertionError("blocking recv on a silent service would hang forever")
        raise TimeoutError("poll window elapsed")

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _text_fragments(text: str) -> tuple[bytes, bytes]:
    """One text message split into an unmasked first frame + continuation."""
    payload = text.encode("utf-8")
    half = len(payload) // 2
    first = bytes([0x01, half]) + payload[:half]  # FIN=0, opcode text
    rest = payload[half:]
    last = bytes([0x80, len(rest)]) + rest  # FIN=1, opcode continuation
    return first, last


class StalledWhisperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audio = Path(self._tmp.name) / "audio.wav"
        self.audio.write_bytes(b"\x00\x01" * 1000)

    def _transcribe(
        self, sock: StallingSocket, *, deadline: float, clock: "FakeClock | None" = None
    ) -> object:
        return transcribe_audio(
            self.audio,
            socket_factory=lambda host, port: sock,
            mask_key_provider=lambda n: FIXED_MASK,
            handshake_key=RFC_KEY,
            ack_wait_seconds=0.0,
            monotonic=clock or FakeClock(step=1.0),
            deadline=deadline,
        )

    def test_silent_service_ends_in_deadline_outcome(self) -> None:
        sock = StallingSocket([handshake_ok()] + [None] * 1000)
        with self.assertRaises(StageDeadlineExceeded):
            self._transcribe(sock, deadline=50.0)
        self.assertTrue(sock.closed)

    def test_trickling_service_still_hits_the_deadline(self) -> None:
        # A peer that keeps sending one byte at a time of a frame that never
        # completes resets every socket timeout; the deadline must still win.
        never_complete = bytes([0x81, 126, 0xFF, 0xFF]) + b"x" * 2000
        clock = FakeClock(step=0.0)  # time only passes while reading
        sock = StallingSocket(
            [handshake_ok()] + [never_complete[i : i + 1] for i in range(len(never_complete))],
            clock=clock,
        )
        with self.assertRaises(StageDeadlineExceeded):
            self._transcribe(sock, deadline=50.0, clock=clock)
        # Stopped near the deadline, not after draining the whole trickle.
        self.assertGreater(len(sock.chunks), 1500)

    def test_poll_timeouts_between_fragments_keep_the_message(self) -> None:
        first, last = _text_fragments(
            '{"type":"partial","text":"hola","accumulated":"hola"}'
        )
        sock = StallingSocket([handshake_ok(), None, first, None, None, last])
        result = self._transcribe(sock, deadline=1000.0)
        self.assertEqual(result.transcript, "hola")  # type: ignore[attr-defined]


class TerminationSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = {
            sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)
        }
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for sig, handler in self.previous.items():
            signal.signal(sig, handler)

    def test_sigterm_becomes_keyboard_interrupt(self) -> None:
        runtime.install_termination_handlers()
        with self.assertRaises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGTERM)

    def test_sighup_becomes_keyboard_interrupt(self) -> None:
        runtime.install_termination_handlers()
        with self.assertRaises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGHUP)

    def test_second_signal_does_not_interrupt_cleanup(self) -> None:
        runtime.install_termination_handlers()
        cleanup_finished = False
        try:
            signal.raise_signal(signal.SIGTERM)
        except KeyboardInterrupt:
            # Cleanup (e.g. restoring Whisper) runs here; a repeated
            # termination request must not abort it.
            signal.raise_signal(signal.SIGTERM)
            cleanup_finished = True
        self.assertTrue(cleanup_finished)

    def test_main_installs_the_handlers(self) -> None:
        with mock.patch.object(runtime, "install_termination_handlers") as install:
            with tempfile.TemporaryDirectory() as tmp:
                cli.main(["backlog-list", "--state-root", tmp])
        install.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
