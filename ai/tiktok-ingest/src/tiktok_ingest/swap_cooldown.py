"""Read-only, bounded swap-quiet cooldown for the ONE vision swap-retry.

Phase 0 addendum (``prds/PHASE-0-SWAP-QUIET-RETRY-PRD.md``, decisions
D-04/D-06): after a recoverable vision ``swap_delta`` gate failure and a
NEW explicit retry consent, the host's systemwide swap I/O is MEASURED
(never mutated) until it quiets down, strictly inside the configured
bounds:

- sampling every 5 s of the CUMULATIVE ``pswpin + pswpout`` counters
  scaled by the page size — the rate is the need per interval computed
  from the cumulative counters, never a decreasing occupied-swap
  reading;
- admission eligible no earlier than 60 s after cooldown start (the
  floor and the stable windows may overlap: samples are collected from
  cooldown start, admission is evaluated no earlier than the floor);
- ONE hard cap of 180 s from cooldown start, NEVER restarted —
  instability, expiry-near misses and final-conditions drift reset only
  the consecutive-stable count, never the cap (oscillation never buys a
  fresh 180 s);
- stability = 3 consecutive intervals with swap I/O <= 1 MiB/s;
- unreadable, missing or implausible (e.g. decreasing) counters FAIL
  CLOSED: the retry is NOT executed and the measurement failure is
  reported distinctly from expiry.

These values are proposed operational heuristics (PRD A-03), not
efficacy claims: waiting may or may not quiet the host (A-01), and
nothing here promises a cure. The 600 s vision stage deadline and the
512 MiB per-attempt swap gate are SEPARATE budgets enforced by the
vision stage itself and are untouched by this module.

Every clock and counter source is injectable so the fixture suite runs
with a scripted monotonic clock and fake ``/proc/vmstat`` sequences —
nothing real ever sleeps or reads the host in tests.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable

from . import config
from .contracts import utc_now_iso
from .gates import SwapSnapshot

_MIB = 1024 * 1024


class SwapCooldownMeasurementError(RuntimeError):
    """Swap measurement became unreadable or implausible: fail closed.

    Distinct from expiry: the cooldown did not run out of time — it lost
    the ability to measure. The retry is NOT executed either way, but
    the two terminal states are reported differently (PRD FR-08).
    """


@dataclasses.dataclass(frozen=True)
class SwapCooldownSample:
    """One durable cooldown sample (audit evidence, PRD FR-16)."""

    elapsed_seconds: float
    wall_iso: str
    swap_io_bytes: int
    rate_bytes_per_s: float | None
    # "cooldown" (initial sampling) or "resample" (sampling resumed after
    # a final-conditions drift inside the SAME 180 s cap). Explicit
    # accounting of which samples belong to which budget phase (FR-12).
    phase: str
    stable_intervals: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "wall_iso": self.wall_iso,
            "swap_io_bytes": self.swap_io_bytes,
            "rate_bytes_per_s": (
                None
                if self.rate_bytes_per_s is None
                else round(self.rate_bytes_per_s, 3)
            ),
            "phase": self.phase,
            "stable_intervals": self.stable_intervals,
        }


def format_rate_mib_per_s(rate_bytes_per_s: float | None) -> str:
    """Render a measured rate for truthful narration (never fabricated)."""
    if rate_bytes_per_s is None:
        return "unavailable"
    return f"{rate_bytes_per_s / _MIB:.2f} MiB/s"


class SwapQuietWatcher:
    """Cumulative-counter sampler enforcing the D-04 bounds.

    The watcher owns ONLY the read-only measurement and its bookkeeping;
    the controller (collection-run) owns sleeping, narration and the
    re-admission callback. The single ``t0`` set by :meth:`start` is the
    anchor for the floor, the cap and every elapsed/remaining value:
    calling :meth:`reset_stability` after a drift NEVER moves ``t0``.
    """

    def __init__(
        self,
        *,
        vmstat_fn: Callable[[], SwapSnapshot],
        monotonic: Callable[[], float] = time.monotonic,
        interval_seconds: float | None = None,
        floor_seconds: float | None = None,
        cap_seconds: float | None = None,
        stable_intervals_needed: int | None = None,
        rate_limit_mib_per_s: float | None = None,
    ) -> None:
        self._vmstat_fn = vmstat_fn
        self._monotonic = monotonic
        self.interval_seconds = (
            config.SWAP_RETRY_COOLDOWN_INTERVAL_SECONDS
            if interval_seconds is None
            else float(interval_seconds)
        )
        self.floor_seconds = (
            config.SWAP_RETRY_COOLDOWN_FLOOR_SECONDS
            if floor_seconds is None
            else float(floor_seconds)
        )
        self.cap_seconds = (
            config.SWAP_RETRY_COOLDOWN_CAP_SECONDS
            if cap_seconds is None
            else float(cap_seconds)
        )
        self.stable_intervals_needed = (
            config.SWAP_RETRY_COOLDOWN_STABLE_INTERVALS
            if stable_intervals_needed is None
            else int(stable_intervals_needed)
        )
        self.rate_limit_bytes_per_s = (
            config.SWAP_RETRY_COOLDOWN_RATE_LIMIT_MIB_PER_S * _MIB
            if rate_limit_mib_per_s is None
            else float(rate_limit_mib_per_s) * _MIB
        )
        self._samples: list[SwapCooldownSample] = []
        self._stable = 0
        self._t0: float | None = None
        self._last_bytes: int | None = None
        self._last_t: float | None = None
        self._reset_count = 0

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> SwapCooldownSample:
        """Anchor sample at cooldown start (t0; rate not yet defined)."""
        if self._t0 is not None:
            raise RuntimeError("swap cooldown watcher already started")
        self._t0 = self._monotonic()
        snapshot = self._read()
        sample = SwapCooldownSample(
            elapsed_seconds=0.0,
            wall_iso=utc_now_iso(),
            swap_io_bytes=snapshot.swap_io_bytes,
            rate_bytes_per_s=None,
            phase="cooldown",
            stable_intervals=0,
        )
        self._samples.append(sample)
        self._last_bytes = snapshot.swap_io_bytes
        self._last_t = self._t0
        return sample

    def sample(self, phase: str = "cooldown") -> SwapCooldownSample:
        """Take one reading NOW and update the consecutive-stable count."""
        return self._take(phase)

    def sample_transition(self, phase: str = "post_stop") -> SwapCooldownSample:
        """One explicit reading right after a service transition.

        FR-12 (explicit accounting across the service transition): the
        raw cumulative value is recorded and — when measurable time has
        actually elapsed since the last sample — the interval rate
        participates in the accounting exactly like a normal sample
        (an above-threshold rate zeroes the stability count, so the
        caller's drift handling resets it). When NO time has elapsed
        (only possible under a non-advancing injected clock; real
        transitions always consume time), the reading is recorded with
        ``rate=None`` and judged inconclusively: no stability change and
        no ``last``-anchor update, so the NEXT sample's delta spans the
        whole transition honestly. The cap anchor ``t0`` is NEVER moved
        here.
        """
        assert self._last_t is not None
        if self._monotonic() - self._last_t <= 0:
            snapshot = self._read()
            sample = SwapCooldownSample(
                elapsed_seconds=self.elapsed_seconds,
                wall_iso=utc_now_iso(),
                swap_io_bytes=snapshot.swap_io_bytes,
                rate_bytes_per_s=None,
                phase=phase,
                stable_intervals=self._stable,
            )
            self._samples.append(sample)
            return sample
        return self._take(phase)

    def _take(self, phase: str) -> SwapCooldownSample:
        if self._t0 is None:
            raise RuntimeError("swap cooldown watcher not started")
        now = self._monotonic()
        snapshot = self._read()
        bytes_now = snapshot.swap_io_bytes
        assert self._last_bytes is not None and self._last_t is not None
        if bytes_now < self._last_bytes:
            raise SwapCooldownMeasurementError(
                "cumulative swap I/O counters decreased "
                f"({self._last_bytes} -> {bytes_now} bytes): the counters "
                "are implausible for a cumulative measure — failing closed"
            )
        dt = now - self._last_t
        if dt <= 0:
            raise SwapCooldownMeasurementError(
                f"non-positive sampling interval ({dt:.6f}s): the clock "
                "cannot support a rate measurement — failing closed"
            )
        rate = (bytes_now - self._last_bytes) / dt
        if rate <= self.rate_limit_bytes_per_s:
            self._stable += 1
        else:
            self._stable = 0
        sample = SwapCooldownSample(
            elapsed_seconds=now - self._t0,
            wall_iso=utc_now_iso(),
            swap_io_bytes=bytes_now,
            rate_bytes_per_s=rate,
            phase=phase,
            stable_intervals=self._stable,
        )
        self._samples.append(sample)
        self._last_bytes = bytes_now
        self._last_t = now
        return sample

    def reset_stability(self) -> None:
        """Final-conditions drift: reset the count, NEVER the cap (T13)."""
        self._stable = 0
        self._reset_count += 1

    # -- read-only state -----------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        assert self._t0 is not None
        return self._monotonic() - self._t0

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.cap_seconds - self.elapsed_seconds)

    @property
    def stable_count(self) -> int:
        return self._stable

    @property
    def reset_count(self) -> int:
        return self._reset_count

    @property
    def admitted(self) -> bool:
        """3 stable intervals AND the 60 s floor (windows may overlap)."""
        return (
            self._stable >= self.stable_intervals_needed
            and self.elapsed_seconds >= self.floor_seconds
        )

    @property
    def expired(self) -> bool:
        return self.elapsed_seconds >= self.cap_seconds

    @property
    def samples(self) -> tuple[SwapCooldownSample, ...]:
        return tuple(self._samples)

    # -- internals -----------------------------------------------------------

    def _read(self) -> SwapSnapshot:
        try:
            snapshot = self._vmstat_fn()
        except SwapCooldownMeasurementError:
            raise
        except Exception as exc:  # noqa: BLE001 - any read failure fails closed
            raise SwapCooldownMeasurementError(
                f"cannot read the swap I/O counters: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if snapshot is None or not isinstance(
            snapshot.swap_io_bytes, int
        ):
            raise SwapCooldownMeasurementError(
                "the swap counter source returned no usable cumulative "
                "value — failing closed"
            )
        return snapshot


__all__ = [
    "SwapCooldownMeasurementError",
    "SwapCooldownSample",
    "SwapQuietWatcher",
    "format_rate_mib_per_s",
]
