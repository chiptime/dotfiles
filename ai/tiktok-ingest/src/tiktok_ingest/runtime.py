"""Injectable execution defaults shared by Milestone 2 modules.

Every side-effecting boundary (subprocess execution, HTTP) is a callable
parameter with a default here. Unit tests always inject fakes; production
wiring uses these defaults. Nothing in this module performs work at import
time.
"""

from __future__ import annotations

import subprocess
import urllib.request
from typing import Callable

# A runner receives an argument vector plus an optional timeout in seconds
# and returns ``(returncode, stdout, stderr)`` as text.
Runner = Callable[..., tuple[int, str, str]]


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
        return 124, stdout, f"runner timeout after {timeout}s: {stderr}"[-2000:]
    return proc.returncode, proc.stdout, proc.stderr


def default_urlopen(req: urllib.request.Request, timeout: float):  # type: ignore[no-untyped-def]
    """Default HTTP transport for oEmbed metadata (stdlib)."""
    return urllib.request.urlopen(req, timeout=timeout)
