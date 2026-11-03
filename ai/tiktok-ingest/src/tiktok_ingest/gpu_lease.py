"""GPU lease: the single owner of the whisper stop/restore GPU swap.

The lease (ROADMAP Stability S2 / ODD S1) is the ONE place that stops
the KNOWN shared whisper service for GPU work and restores it
afterwards. It owns three things and nothing else:

- the persisted lease marker (S3): ``<state root>/active_gpu_lease.json``,
  written atomically BEFORE the stop command is issued and removed only
  after a verified healthy restore, so a later run can reconcile a
  service left stopped by a dead run (PID + process-start-time make the
  owner check PID-reuse safe);
- the ONE post-restore readiness wait (S2):
  :func:`wait_for_whisper_health`, shared by guided and collection-run
  (immediate first probe, ~1 s interval, 60 s deadline);
- the exactly-one-start restore rule (S1): one ``podman start`` attempt
  per lease, restore in ``finally``, and NO restore when an interruption
  landed inside the stop itself (the stop result is UNKNOWN).

Honesty rules carried into this module: identity verification, mutation
and health probing run ONLY through the injected hooks (the existing
:class:`tiktok_ingest.guided.StageFunctions` whisper hooks, which wrap
:mod:`tiktok_ingest.whisper_lifecycle`); this module never runs podman
itself. The marker never holds secrets — only the identity env keys
allowed by ``WHISPER_IDENTITY_ENV_KEYS``. ``KeyboardInterrupt`` is never
swallowed: an interruption stays an interruption, and the lease records
where it landed so the caller can report the actual partial state.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Iterator

from . import state
from .contracts import utc_now_iso
from .whisper_lifecycle import WHISPER_IDENTITY_ENV_KEYS

# Bounded read-only readiness wait after the ONE authorized whisper
# restore: a freshly started service can need a moment before its
# health endpoint answers (observed in a real run: an immediate
# connection reset that a later read-only precheck showed healthy).
# Polling NEVER performs another start. Worst-case overshoot past the
# deadline is ONE probe — the health precheck's own bounded timeout
# (5s default, see whisper_client.health_precheck) — never unbounded.
# Single home for the bounds shared by guided and collection-run (S2);
# ``guided`` re-exports them for its existing tests and callers.
WHISPER_RESTORE_READY_DEADLINE_SECONDS = 60.0
WHISPER_RESTORE_READY_POLL_INTERVAL_SECONDS = 1.0

# Lease marker (S3): schema/version of ``active_gpu_lease.json``.
LEASE_MARKER_FILENAME = "active_gpu_lease.json"
LEASE_MARKER_SCHEMA = "tiktok-ingest.gpu-lease"
LEASE_MARKER_VERSION = 1

# The one readiness wait (S2) -----------------------------------------------


def wait_for_whisper_health(
    health: Callable[[], tuple[bool, str]],
    *,
    deadline_seconds: float = WHISPER_RESTORE_READY_DEADLINE_SECONDS,
    poll_interval_seconds: float = WHISPER_RESTORE_READY_POLL_INTERVAL_SECONDS,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    waiting_feedback: Callable[[float], None] | None = None,
) -> tuple[bool, str]:
    """Poll the ALREADY-RESTORED service until healthy or deadline.

    Read-only readiness polling after the one authorized restore: the
    first probe runs immediately (an already-healthy service never
    sleeps or waits), a failed probe is followed by exactly one bounded
    interval (no busy loop), and crossing the deadline stops with the
    last REAL detail — the caller then blocks all progress. Success
    stops the polling outright. ``KeyboardInterrupt`` is never caught
    here: an interruption stays an interruption. The total wait can
    overshoot ``deadline_seconds`` by at most one probe (the health
    precheck's own bounded timeout). ``waiting_feedback`` is optional
    bounded operator feedback, emitted once when the wait actually
    starts, never per probe.
    """
    now = monotonic or time.monotonic
    do_sleep = sleep or time.sleep
    started = now()
    announced = False
    while True:
        healthy, detail = health()
        if healthy:
            return True, detail
        if not announced and waiting_feedback is not None:
            waiting_feedback(deadline_seconds)
            announced = True
        if now() - started >= deadline_seconds:
            return False, detail
        do_sleep(poll_interval_seconds)


# The persisted lease marker (S3) -------------------------------------------


def lease_marker_path(state_root: Any) -> Path:
    """``<state root>/active_gpu_lease.json`` for the given StateRoot."""
    return Path(state_root.root) / LEASE_MARKER_FILENAME


def _read_proc_stat(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def process_start_time(
    pid: int, *, reader: Callable[[int], str | None] | None = None
) -> int | None:
    """Process start time (field 22 of ``/proc/<pid>/stat``, clock ticks).

    Field 22 (``starttime``) is stable for the life of the process and
    changes when a PID is recycled, which is what makes the marker's
    owner check PID-reuse safe. ``None`` when the stat line is unreadable
    or unparseable — never a guess. ``reader`` is injectable for tests.
    """
    read_stat = reader or _read_proc_stat
    raw = read_stat(pid)
    if raw is None:
        return None
    # comm (field 2) may contain spaces and parens: everything after the
    # LAST ')' is fields 3.. — field n is token[n - 3].
    tail = raw.rpartition(")")[2].split()
    try:
        return int(tail[22 - 3])
    except (IndexError, ValueError):
        return None


def owner_alive(
    marker: Mapping[str, Any] | None,
    *,
    start_time_reader: Callable[[int], int | None] = process_start_time,
) -> bool:
    """True only when the marker's owner POSITIVELY matches a live process.

    The owner is alive when the recorded PID still exists AND its
    current process start time equals the recorded one (a recycled PID
    with a different start time is a different process: NOT alive).
    Missing or malformed fields, an unreadable stat line, or a start
    time that was never recorded all answer False — liveness is never
    guessed, only positively verified.
    """
    if not isinstance(marker, Mapping):
        return False
    pid = marker.get("owner_pid")
    recorded = marker.get("owner_start_time")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False
    if not isinstance(recorded, int) or isinstance(recorded, bool):
        return False
    current = start_time_reader(pid)
    return current is not None and current == recorded


class LeaseMarkerFile:
    """The persisted lease marker, written before the stop (S3).

    ``state_root=None`` DISABLES persistence (some callers and test
    doubles have no state root): no marker is written and every
    operation is a no-op — behaviour is otherwise identical. The
    document holds only the owner identity (PID + start time), a
    timestamp and the VERIFIED whisper identity snapshot (name, image,
    identity env keys only — never secrets).
    """

    def __init__(
        self,
        state_root: Any | None = None,
        *,
        pid: int | None = None,
        start_time_reader: Callable[[int], int | None] = process_start_time,
        now_iso: Callable[[], str] = utc_now_iso,
    ) -> None:
        self._path = (
            lease_marker_path(state_root) if state_root is not None else None
        )
        self._pid = os.getpid() if pid is None else pid
        self._start_time_reader = start_time_reader
        self._now_iso = now_iso

    @property
    def path(self) -> Path | None:
        """Marker location, or None when persistence is disabled."""
        return self._path

    def document(self, info: Any) -> dict[str, Any]:
        """The marker payload for a verified service snapshot."""
        env = [
            [key, value]
            for key, value in getattr(info, "env", ())
            # Defensive: whisper_lifecycle already filters; never capture
            # anything outside the identity keys even if a caller passes
            # an unfiltered snapshot.
            if key in WHISPER_IDENTITY_ENV_KEYS
        ]
        return {
            "schema": LEASE_MARKER_SCHEMA,
            "version": LEASE_MARKER_VERSION,
            "owner_pid": self._pid,
            "owner_start_time": self._start_time_reader(self._pid),
            "created_at": self._now_iso(),
            "whisper": {
                "name": getattr(info, "name", ""),
                "image": getattr(info, "image", ""),
                "env": env,
            },
        }

    def write(self, info: Any) -> None:
        """Atomically persist the marker (no-op when disabled)."""
        if self._path is None:
            return
        state.atomic_write_json(self._path, self.document(info))

    def read(self) -> dict[str, Any] | None:
        """The marker document, or None when absent or disabled."""
        if self._path is None:
            return None
        return state.read_json_file(self._path)

    def clear(self) -> None:
        """Remove the marker (no-op when absent or disabled).

        Called ONLY after a verified healthy restore — every other
        outcome leaves the marker in place for the next-start reconcile.
        """
        if self._path is None:
            return
        self._path.unlink(missing_ok=True)


# The lease itself (S1) -----------------------------------------------------


@dataclasses.dataclass(frozen=True)
class LeaseHooks:
    """The lifecycle boundary the lease coordinates (injectable).

    Production closes over the existing ``StageFunctions`` whisper hooks
    (which wrap :mod:`tiktok_ingest.whisper_lifecycle`): ``stop`` and
    ``restore`` perform the bounded, identity-verified mutations of the
    KNOWN shared service only; ``health`` is the read-only precheck.
    """

    stop: Callable[[Any], Any]
    restore: Callable[[Any], Any]
    health: Callable[[], tuple[bool, str]]


@dataclasses.dataclass(frozen=True)
class RestoreOutcome:
    """Structured result of the ONE authorized restore attempt.

    ``status`` is one of:

    - ``"healthy"`` — the service was started, identity-verified and
      became healthy within the deadline (the marker is cleared);
    - ``"restore_failed"`` — the start command itself failed
      (``error``); the service may still be STOPPED or partially started;
    - ``"health_timeout"`` — the service was started and identity-verified
      but did not become healthy within the deadline (``detail`` is the
      last REAL health detail);
    - ``"not_stopped"`` — no stop was ever acquired: nothing ran;
    - ``"interrupted"`` — a ``KeyboardInterrupt`` landed in the attempt
      (it PROPAGATED out of :meth:`GpuLease.restore_once`; this status is
      only replayed by later idempotent calls).
    """

    status: str
    detail: str = ""
    error: BaseException | None = None


class GpuLease:
    """One authorized stop→…→restore window over the KNOWN whisper service.

    The lease is the single owner of the swap's bookkeeping (S1): the
    marker is written BEFORE the stop, exactly ONE start attempt is ever
    made per lease, the restore result is recorded honestly, and an
    interruption inside the stop itself (stop result UNKNOWN) NEVER
    triggers a restore. It renders no operator messages — callers keep
    their own exact reporting and map :class:`RestoreOutcome` (and the
    recorded interrupt phase) onto it.
    """

    def __init__(
        self,
        *,
        hooks: LeaseHooks,
        marker: LeaseMarkerFile | None = None,
    ) -> None:
        self._hooks = hooks
        self._marker = marker
        self._stopped_info: Any = None
        self._stop_interrupted = False
        self._restore_attempted = False
        self._restore_completed = False
        self._restore_interrupt_phase: str | None = None
        self._last_outcome = RestoreOutcome("not_stopped")

    # -- state (read-only views for callers) ------------------------------

    @property
    def stopped_info(self) -> Any:
        """The verified snapshot of the service THIS lease stopped."""
        return self._stopped_info

    @property
    def stop_interrupted(self) -> bool:
        """True when an interruption landed inside the stop (UNKNOWN)."""
        return self._stop_interrupted

    @property
    def restore_attempted(self) -> bool:
        """True once the ONE authorized restore attempt has begun."""
        return self._restore_attempted

    @property
    def restore_completed(self) -> bool:
        """True only after a verified healthy restore."""
        return self._restore_completed

    @property
    def restore_interrupt_phase(self) -> str | None:
        """Where a restore-interrupting KeyboardInterrupt landed.

        ``"start"`` — inside the start command (service state UNKNOWN);
        ``"health_wait"`` — inside the readiness wait (the service WAS
        started and identity-verified; only its health is UNKNOWN).
        """
        return self._restore_interrupt_phase

    # -- lifecycle ---------------------------------------------------------

    def acquire(self, info: Any) -> None:
        """Write the marker, then stop the verified service (S3 order).

        The marker is persisted BEFORE the stop command is issued — a
        dead run is then identifiable at the next start even if the stop
        itself was interrupted. On success the snapshot becomes
        ``stopped_info``. A ``KeyboardInterrupt`` from the stop marks the
        stop result UNKNOWN (``stop_interrupted``) and PROPAGATES: the
        caller must NOT restore on that path. Any other failure from the
        stop propagates unchanged; the marker stays (the next start
        reconciles from the real container state).
        """
        if self._marker is not None:
            self._marker.write(info)
        try:
            self._hooks.stop(info)
        except KeyboardInterrupt:
            self._stop_interrupted = True
            raise
        self._stopped_info = info

    def restore_once(
        self,
        *,
        waiting_feedback: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> RestoreOutcome:
        """The ONE authorized restore attempt + bounded readiness wait.

        Exactly one start per lease: a later call replays the recorded
        outcome without touching the hooks again. Without an acquired
        stop this is a no-op (``"not_stopped"``). A ``KeyboardInterrupt``
        (from the start or from the readiness wait — distinguished by
        :attr:`restore_interrupt_phase`) PROPAGATES and is recorded as
        ``"interrupted"``. The marker is cleared ONLY on the verified
        healthy path. ``monotonic``/``sleep`` are the injectable clock of
        the readiness wait.
        """
        if self._restore_attempted:
            return self._last_outcome
        if self._stopped_info is None:
            return RestoreOutcome("not_stopped")
        self._restore_attempted = True
        try:
            self._hooks.restore(self._stopped_info)
        except KeyboardInterrupt:
            self._restore_interrupt_phase = "start"
            self._last_outcome = RestoreOutcome("interrupted")
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
            self._last_outcome = RestoreOutcome("restore_failed", error=exc)
            return self._last_outcome
        try:
            healthy, detail = wait_for_whisper_health(
                self._hooks.health,
                waiting_feedback=waiting_feedback,
                monotonic=monotonic,
                sleep=sleep,
            )
        except KeyboardInterrupt:
            self._restore_interrupt_phase = "health_wait"
            self._last_outcome = RestoreOutcome("interrupted")
            raise
        if healthy:
            self._restore_completed = True
            self._last_outcome = RestoreOutcome("healthy", detail=detail)
            if self._marker is not None:
                self._marker.clear()
            return self._last_outcome
        self._last_outcome = RestoreOutcome("health_timeout", detail=detail)
        return self._last_outcome


@contextlib.contextmanager
def leased_window(
    hooks: LeaseHooks,
    info: Any,
    *,
    marker: LeaseMarkerFile | None = None,
    vram_check: Callable[[], None] | None = None,
    waiting_feedback: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Iterator[GpuLease]:
    """S1 context-manager shape: stop → [VRAM check] → body → restore.

    ``acquire`` runs in setup (marker before the stop; a ``KeyboardInterrupt``
    inside the stop propagates and the body NEVER runs — no restore is
    attempted for an UNKNOWN stop). The optional ``vram_check`` (free-VRAM
    verification hook) runs after the verified stop and before the body;
    its failure propagates and the ``finally`` still restores. The body
    itself runs under no extra wrapping: the ``finally`` performs the ONE
    restore attempt on every exit path (normal, exception, interrupt)
    exactly once. Callers needing per-branch reporting or intermediate
    restore points use :class:`GpuLease` directly instead.
    """
    lease = GpuLease(hooks=hooks, marker=marker)
    lease.acquire(info)
    try:
        if vram_check is not None:
            vram_check()
        yield lease
    finally:
        if lease.stopped_info is not None and not lease.restore_attempted:
            lease.restore_once(
                waiting_feedback=waiting_feedback,
                monotonic=monotonic,
                sleep=sleep,
            )


__all__ = [
    "GpuLease",
    "LEASE_MARKER_FILENAME",
    "LEASE_MARKER_SCHEMA",
    "LEASE_MARKER_VERSION",
    "LeaseHooks",
    "LeaseMarkerFile",
    "RestoreOutcome",
    "WHISPER_RESTORE_READY_DEADLINE_SECONDS",
    "WHISPER_RESTORE_READY_POLL_INTERVAL_SECONDS",
    "lease_marker_path",
    "leased_window",
    "owner_alive",
    "process_start_time",
    "wait_for_whisper_health",
]
