"""Pure resource-gate logic for local inference (Milestone 3).

Every benchmark safety gate (methodology section 6) is implemented here as a
PURE function over injectable measurements:

- Parsers turn raw tool output (nvidia-smi CSV, /proc/vmstat, Ollama
  ``/api/ps`` JSON, server layer reports) into plain values. They raise
  :class:`GateParseError` on anything they cannot fully parse — never guess.
- Checks turn parsed values plus limits into a :class:`GateResult` with an
  explicit pass/fail and an auditable reason. Boundary semantics follow the
  methodology: ``free >= limit`` passes, ``delta <= limit`` passes.
- :class:`GateLedger` enforces the stop rule: once ANY gate fires inside one
  authorization window, the window is closed — no further loads, no retries
  (methodology section 6.5).

This module performs no I/O of its own: measurements come from injectable
runners in the calling stages, so every rule here is fixture-testable.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

# Gate identifiers (recorded in meta.json resource_gates).
GATE_FREE_VRAM = "free_vram"
GATE_VRAM_DELTA = "vram_delta"
GATE_SWAP_DELTA = "swap_delta"
GATE_OFFLOAD = "offload"
GATE_UNLOAD = "unload_verification"


class GateParseError(ValueError):
    """Raised when raw measurement output cannot be parsed exactly."""


class GateViolation(RuntimeError):
    """A gate fired. Final for the current authorization window."""

    def __init__(self, gate: str, reason: str, detail: dict[str, Any]) -> None:
        super().__init__(f"{gate} gate fired: {reason}")
        self.gate = gate
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------
# Parsers (pure functions over raw text)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GpuMemory:
    """One GPU's memory report in MiB (nvidia-smi ``nounits`` output)."""

    total_mib: int
    used_mib: int
    free_mib: int


def parse_nvidia_smi_mib(text: str) -> GpuMemory:
    """Parse ``--query-gpu=memory.total,memory.used,memory.free`` CSV output.

    Exactly three comma-separated non-negative integers are accepted, in
    that order, matching the benchmark query with ``noheader,nounits``.
    """
    stripped = (text or "").strip()
    if not stripped:
        raise GateParseError("nvidia-smi produced no output")
    if "\n" in stripped:
        raise GateParseError(
            "nvidia-smi reported multiple GPU rows; the gates expect the "
            "single-GPU host recorded in the benchmark"
        )
    parts = [part.strip() for part in stripped.split(",")]
    if len(parts) != 3:
        raise GateParseError(
            f"nvidia-smi output must have 3 fields (total, used, free), got {parts!r}"
        )
    values: list[int] = []
    for raw in parts:
        if not raw or not raw.lstrip("+").isdigit():
            raise GateParseError(f"nvidia-smi field {raw!r} is not a MiB integer")
        value = int(raw)
        if value < 0:
            raise GateParseError(f"nvidia-smi field {raw!r} is negative")
        values.append(value)
    total, used, free = values
    return GpuMemory(total_mib=total, used_mib=used, free_mib=free)


@dataclasses.dataclass(frozen=True)
class SwapSnapshot:
    """Cumulative swap I/O counters (pages) plus the host page size."""

    pswpin_pages: int
    pswpout_pages: int
    page_size_bytes: int

    @property
    def swap_io_bytes(self) -> int:
        return (self.pswpin_pages + self.pswpout_pages) * self.page_size_bytes


def parse_vmstat_swap(text: str, page_size_bytes: int) -> SwapSnapshot:
    """Parse ``pswpin``/``pswpout`` from /proc/vmstat content (pure).

    ``page_size_bytes`` is injected (``os.sysconf('SC_PAGE_SIZE')`` in
    production) so this stays a pure function and testable on any host.
    """
    if page_size_bytes <= 0:
        raise GateParseError("page size must be a positive byte count")
    values: dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in {"pswpin", "pswpout"}:
            key, raw = parts
            if not raw.isdigit():
                raise GateParseError(f"vmstat field {key} value {raw!r} is not an integer")
            values[key] = int(raw)
    missing = {"pswpin", "pswpout"} - values.keys()
    if missing:
        raise GateParseError(
            f"vmstat output is missing swap counters: {sorted(missing)}"
        )
    return SwapSnapshot(
        pswpin_pages=values["pswpin"],
        pswpout_pages=values["pswpout"],
        page_size_bytes=page_size_bytes,
    )


def parse_ollama_ps(text: str) -> list[dict[str, Any]]:
    """Parse the Ollama ``/api/ps`` document into its resident-model list.

    An empty list means no model is resident — the unload-verification
    precondition. Malformed JSON or a missing ``models`` key fails closed.
    """
    try:
        document = json.loads(text or "")
    except json.JSONDecodeError as exc:
        raise GateParseError(f"/api/ps output is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise GateParseError("/api/ps output has no 'models' list")
    return [entry for entry in document["models"] if isinstance(entry, dict)]


_LAYER_REPORT_RE = re.compile(
    r"offloa(?:ded|ding)\s+(\d+)/(\d+)\s+layers", re.IGNORECASE
)


def parse_layer_report(text: str) -> tuple[int, int] | None:
    """Extract ``(gpu_layers, total_layers)`` from a server log tail.

    Ollama logs GPU residency as ``offloaded N/M layers to GPU`` (or
    ``offloading`` while loading). The LAST match wins (most recent load).
    Returns ``None`` when the text carries no layer report — the caller
    decides what absence means (the offload gate treats it as unverifiable
    and fails closed).
    """
    last: tuple[int, int] | None = None
    for match in _LAYER_REPORT_RE.finditer(text or ""):
        gpu_layers = int(match.group(1))
        total_layers = int(match.group(2))
        if total_layers <= 0 or gpu_layers < 0 or gpu_layers > total_layers:
            raise GateParseError(
                f"implausible layer report {gpu_layers}/{total_layers}"
            )
        last = (gpu_layers, total_layers)
    return last


# --------------------------------------------------------------------------
# Checks (pure functions over parsed values + limits)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GateResult:
    """One gate evaluation. ``passed=False`` is final for the window."""

    gate: str
    passed: bool
    reason: str
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)


def check_free_vram(gpu: GpuMemory, require_mib: int) -> GateResult:
    """Free-VRAM gate: >= require_mib free before EACH Qwen load.

    A hard requirement (methodology section 6.1): the caller maps a failure
    to a ``blocked`` outcome — the load never happens on insufficient VRAM.
    """
    if gpu.free_mib >= require_mib:
        return GateResult(
            gate=GATE_FREE_VRAM,
            passed=True,
            reason=f"{gpu.free_mib} MiB free >= {require_mib} MiB required before load",
            detail={"free_mib": gpu.free_mib, "required_mib": require_mib,
                    "total_mib": gpu.total_mib, "used_mib": gpu.used_mib},
        )
    return GateResult(
        gate=GATE_FREE_VRAM,
        passed=False,
        reason=(
            f"only {gpu.free_mib} MiB free VRAM, below the {require_mib} MiB "
            "required before every Qwen load; refusing to load"
        ),
        detail={"free_mib": gpu.free_mib, "required_mib": require_mib,
                "total_mib": gpu.total_mib, "used_mib": gpu.used_mib},
    )


def check_vram_delta(
    current_used_mib: int, baseline_used_mib: int, limit_mib: int
) -> GateResult:
    """VRAM delta gate from the IMMEDIATE phase baseline (methodology 6.2)."""
    delta = current_used_mib - baseline_used_mib
    if delta <= limit_mib:
        return GateResult(
            gate=GATE_VRAM_DELTA,
            passed=True,
            reason=f"VRAM delta {delta} MiB <= {limit_mib} MiB limit from the "
            "immediate phase baseline",
            detail={"delta_mib": delta, "limit_mib": limit_mib,
                    "current_used_mib": current_used_mib,
                    "baseline_used_mib": baseline_used_mib},
        )
    return GateResult(
        gate=GATE_VRAM_DELTA,
        passed=False,
        reason=f"VRAM delta {delta} MiB exceeds the {limit_mib} MiB per-model "
        "limit measured from the immediate phase baseline",
        detail={"delta_mib": delta, "limit_mib": limit_mib,
                "current_used_mib": current_used_mib,
                "baseline_used_mib": baseline_used_mib},
    )


def check_swap_delta(
    current_bytes: int, baseline_bytes: int, limit_bytes: int
) -> GateResult:
    """Swap I/O delta gate from the IMMEDIATE baseline (methodology 6.4).

    The delta is always measured against an immediately pre-phase snapshot:
    ambient host swap growth must be attributed per process, never hidden
    (methodology section 8.4) — the stop rule stays intact either way.
    """
    delta = current_bytes - baseline_bytes
    if delta <= limit_bytes:
        return GateResult(
            gate=GATE_SWAP_DELTA,
            passed=True,
            reason=f"swap I/O delta {delta} B <= {limit_bytes} B limit from "
            "the immediate baseline",
            detail={"delta_bytes": delta, "limit_bytes": limit_bytes,
                    "current_bytes": current_bytes, "baseline_bytes": baseline_bytes},
        )
    return GateResult(
        gate=GATE_SWAP_DELTA,
        passed=False,
        reason=f"swap I/O delta {delta} B exceeds the {limit_bytes} B "
        "(512 MiB) phase limit measured from the immediate baseline; a "
        "gate stop is final for this authorization window",
        detail={"delta_bytes": delta, "limit_bytes": limit_bytes,
                "current_bytes": current_bytes, "baseline_bytes": baseline_bytes},
    )


def check_no_offload(layer_report: tuple[int, int] | None) -> GateResult:
    """Zero-CPU-offload gate (methodology 6.3).

    ANY CPU-offloaded layer is a FAILED run, not a slow run. A missing
    report cannot prove zero offload and therefore also fails — fail closed.
    """
    if layer_report is None:
        return GateResult(
            gate=GATE_OFFLOAD,
            passed=False,
            reason="no GPU layer report available; zero CPU offload could "
            "not be verified — failing closed",
            detail={"gpu_layers": None, "total_layers": None},
        )
    gpu_layers, total_layers = layer_report
    if gpu_layers == total_layers:
        return GateResult(
            gate=GATE_OFFLOAD,
            passed=True,
            reason=f"all {total_layers}/{total_layers} layers GPU-resident; zero offload",
            detail={"gpu_layers": gpu_layers, "total_layers": total_layers},
        )
    return GateResult(
        gate=GATE_OFFLOAD,
        passed=False,
        reason=(
            f"{total_layers - gpu_layers} of {total_layers} layers CPU-offloaded "
            f"({gpu_layers}/{total_layers} on GPU); this is a failed run, not a slow run"
        ),
        detail={"gpu_layers": gpu_layers, "total_layers": total_layers},
    )


def check_unload(
    resident_models: list[dict[str, Any]],
    post_used_mib: int,
    session_baseline_used_mib: int,
    tolerance_mib: int,
) -> GateResult:
    """Unload verification (methodology section 5, MVP-PRD section 5).

    BOTH conditions must hold: ``/api/ps`` reports zero resident models AND
    observed GPU memory returned to within ``+tolerance_mib`` of the
    session baseline. Sequential API calls alone never prove release.
    """
    detail: dict[str, Any] = {
        "resident_models": [entry.get("name") for entry in resident_models],
        "post_used_mib": post_used_mib,
        "session_baseline_used_mib": session_baseline_used_mib,
        "tolerance_mib": tolerance_mib,
    }
    if resident_models:
        return GateResult(
            gate=GATE_UNLOAD,
            passed=False,
            reason="model residency is not empty after the unload request: "
            f"{detail['resident_models']}",
            detail=detail,
        )
    overshoot = post_used_mib - session_baseline_used_mib
    if overshoot > tolerance_mib:
        return GateResult(
            gate=GATE_UNLOAD,
            passed=False,
            reason=f"post-phase VRAM {post_used_mib} MiB is {overshoot} MiB above "
            f"the session baseline, beyond the +{tolerance_mib} MiB tolerance; "
            "GPU release not verified",
            detail=detail,
        )
    return GateResult(
        gate=GATE_UNLOAD,
        passed=True,
        reason=f"model residency empty and post-phase VRAM within "
        f"+{tolerance_mib} MiB of the session baseline "
        f"(overshoot {overshoot} MiB)",
        detail=detail,
    )


# --------------------------------------------------------------------------
# Authorization window: no retries after any gate fires
# --------------------------------------------------------------------------


class GateLedger:
    """Records gate results for ONE authorization window.

    :meth:`record` raises :class:`GateViolation` as soon as a failing gate
    result is registered, and from that moment :meth:`ensure_open` refuses
    every subsequent gated action: a gate stop is FINAL — no retries, no
    continuation, no auto-recovery. Resuming requires a NEW authorization
    window (a new stage invocation), which is an operator decision.
    """

    def __init__(self) -> None:
        self.results: list[GateResult] = []

    @property
    def violations(self) -> tuple[GateResult, ...]:
        return tuple(result for result in self.results if not result.passed)

    @property
    def closed(self) -> bool:
        return bool(self.violations)

    def ensure_open(self) -> None:
        """Refuse any further gated action once a gate has fired."""
        if self.closed:
            first = self.violations[0]
            raise GateViolation(
                first.gate,
                "authorization window closed: no retries after a gate fires "
                f"(first violation: {first.reason})",
                first.detail,
            )

    def record(self, result: GateResult) -> GateResult:
        """Record a gate result; raise immediately when it failed.

        Recording is refused outright once the window is closed: even a
        passing measurement cannot reopen an authorization window.
        """
        self.ensure_open()
        self.results.append(result)
        if not result.passed:
            raise GateViolation(result.gate, result.reason, result.detail)
        return result

    def to_jsonable(self) -> list[dict[str, Any]]:
        return [
            {"gate": result.gate, "passed": result.passed, "reason": result.reason}
            for result in self.results
        ]
