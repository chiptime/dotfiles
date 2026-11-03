"""Injectable execution defaults shared by Milestone 2 modules.

Every side-effecting boundary (subprocess execution, HTTP) is a callable
parameter with a default here. Unit tests always inject fakes; production
wiring uses these defaults. Nothing in this module performs work at import
time.
"""

from __future__ import annotations

import signal
import subprocess
import threading
import urllib.request
from typing import Any, Callable

# A runner receives an argument vector plus an optional timeout in seconds
# and returns ``(returncode, stdout, stderr)`` as text.
Runner = Callable[..., tuple[int, str, str]]

# Upper bounds for short, read-only or lifecycle subprocesses. A hung
# podman/nvidia-smi must surface as an explicit failure (the runner
# normalizes a timeout to exit 124), never as a run that blocks forever.
PODMAN_QUERY_TIMEOUT_SECONDS = 30.0
PODMAN_START_TIMEOUT_SECONDS = 120.0
# Added on top of podman's own stop grace so its SIGKILL escalation can
# finish before we give up on the command.
PODMAN_STOP_EXTRA_SECONDS = 60.0
PROBE_TIMEOUT_SECONDS = 15.0
# Exit code the default runner reports when a subprocess timed out. Callers
# must never read it as a negative answer ("container missing", "process
# gone"): a timeout proves nothing.
TIMEOUT_RETURNCODE = 124

_termination_requested = False


def _on_termination(signum: int, frame: Any) -> None:  # noqa: ARG001
    global _termination_requested
    if _termination_requested:
        # Already unwinding: let the interrupt cleanup (e.g. restoring the
        # Whisper service) finish instead of aborting it half-way.
        return
    _termination_requested = True
    raise KeyboardInterrupt(f"terminated by signal {signal.Signals(signum).name}")


def install_termination_handlers() -> None:
    """Route SIGTERM/SIGHUP into the existing ``KeyboardInterrupt`` paths.

    Every orchestrator already restores shared services on Ctrl+C; a
    ``systemctl stop``, ``kill`` or a closed terminal must take the same
    path. Only the first termination request interrupts; repeats are
    ignored so cleanup can complete. No-op outside the main thread.
    """
    global _termination_requested
    if threading.current_thread() is not threading.main_thread():
        return
    _termination_requested = False
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_termination)


def subprocess_runner(
    argv: list[str], timeout: float | None = None
) -> tuple[int, str, str]:
    """Default runner: one subprocess, captured output, no shell."""
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # Normalize timeouts into a non-zero result so callers can classify
        # them (deadlines surface as explicit outcomes, never exceptions
        # leaking past the state layer).
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return (
            TIMEOUT_RETURNCODE,
            stdout,
            f"runner timeout after {timeout}s: {stderr}"[-2000:],
        )
    return proc.returncode, proc.stdout, proc.stderr


def default_urlopen(req: urllib.request.Request, timeout: float):  # type: ignore[no-untyped-def]
    """Default HTTP transport for oEmbed metadata (stdlib)."""
    return urllib.request.urlopen(req, timeout=timeout)
