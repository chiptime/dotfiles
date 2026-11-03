"""Stage engine: the consent-gated stage execution under guided-run.

Extracted from :mod:`tiktok_ingest.guided` (S2): this module owns WHAT
runs — fetch/prepare preparation, the whisper stop/restore coordination
around the GPU lease, the vision/audio GPU window, the synthesis
ingestion + verify boundary, the per-stage report records and the run
report — with NO terminal I/O and NO authorization decision of its own.
Every operator-facing seam is an injected adapter:

- ``approve(window) -> bool`` — the consent adapter; it DECIDES the
  window (interactive prompt, run-plan scope, test double). Building a
  :class:`ConsentWindow` value is data and happens here; asking is the
  adapter's job.
- ``notify(text)`` — the narration adapter (progress lines). The engine
  never prints.

The honesty rules are unchanged from the guided coordinator (see
:mod:`tiktok_ingest.guided`): progress is derived from the product's own
state, the GPU lease marker is the ONE persisted lifecycle artifact, a
fired gate is final, and whisper is coordinated only through the
lifecycle hooks resolved through ``StageFunctions``. ``GuidedRun`` is
the thin driver wiring ``consent.ask`` -> ``approve`` and ``out`` ->
``notify``; call order, messages, consent windows, report keys/defaults
and state are byte-identical to the pre-extraction coordinator (S4).
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from . import config
from . import gpu_lease
from .contracts import utc_now_iso
from .gpu_lease import WHISPER_RESTORE_READY_DEADLINE_SECONDS
from .state import Processed, StateRoot
from .synthesis import SynthesisDocument

GUIDED_MAX_BATCH = config.BUDGETS.batch_pilot_clips


class GuidedError(RuntimeError):
    """Usage-level error (unknown IDs, bad limit, malformed --synthesis)."""


# --------------------------------------------------------------------------
# Consent windows (data only — the DECISION is the approve adapter's)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ConsentWindow:
    """One explicit authorization request, shown BEFORE any effect runs.

    ``kind`` is one of ``tanda``, ``fetch``, ``prepare``, ``gpu``,
    ``verify``. Every field is display text for the operator; the
    guided command maps a granted window to exactly the operations
    named here and nothing else.

    ``model`` is the ONE non-display field: a structured binding
    attribute (e.g. the exact synthesis model for ``synthesis-api``
    windows) so run-scoped authorization wrappers can compare EXACT
    identity tokens instead of substrings of display prose. It grants
    nothing by itself and stays ``None`` for ordinary windows.
    """

    kind: str
    title: str
    ids: tuple[str, ...] = ()
    destinations: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------
# Injectable stage boundary (production defaults live in guided.py)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StageFunctions:
    """The stage boundary the engine coordinates; injectable for tests.

    Production defaults close over the REAL pipeline functions with
    their production transports. Every callable must return the stage's
    normal outcome object (``.outcome``/``.reason``) and keep the
    stage's own guarantees (fail-closed preconditions, gates, cleanup).

    Phase 0 optional hooks (default ``None`` keeps the documented
    standalone behavior — whisper stays operator-managed and blocks are
    surfaced): when ALL THREE whisper lifecycle hooks are provided,
    ``collection-run`` additionally coordinates bounded, identity-checked
    stop/restore of the KNOWN shared whisper service behind their own
    consent windows. The hooks are injectable for the fixture suite;
    nothing real runs in tests.
    """

    fetch: Callable[[str, StateRoot], Any]
    prepare: Callable[[str, StateRoot], Any]
    vision: Callable[[str, StateRoot], Any]
    audio: Callable[[str, StateRoot], Any]
    synthesize: Callable[[str, StateRoot, Path | None], Any]
    verify: Callable[[str, StateRoot], Any]
    # Raises with an operator-readable requirement unless NO whisper
    # container is running (read-only podman query).
    whisper_stopped: Callable[[], None]
    # (healthy, detail) for the shared whisper WS service (read-only).
    whisper_health: Callable[[], tuple[bool, str]]
    # True when something already answers on the isolated Ollama port.
    ollama_reachable: Callable[[], bool]
    ollama_start: Callable[[], Any]
    ollama_wait_ready: Callable[[Any], str]
    ollama_stop: Callable[[Any], None]
    # Optional Phase 0 whisper lifecycle coordination (all-or-nothing):
    # inspect returns the service identity/config (or None); stop and
    # restore perform the bounded, verified mutations of the KNOWN
    # shared service only (see whisper_lifecycle).
    whisper_inspect: Callable[[], Any] | None = None
    whisper_stop: Callable[[Any], Any] | None = None
    whisper_restore: Callable[[Any], Any] | None = None
    # Phase 0 addendum: read-only free-VRAM probe (MiB) used ONLY by the
    # collection-run swap-retry re-admission drift check (PRD FR-10/
    # FR-11). ``None`` keeps older fixture doubles working unchanged;
    # the vision stage's own admission gate stays authoritative in every
    # case — this seam only enables the pre-attempt drift check.
    gpu_free_mib: Callable[[], int] | None = None

    def whisper_lifecycle_ready(self) -> bool:
        return (
            self.whisper_inspect is not None
            and self.whisper_stop is not None
            and self.whisper_restore is not None
        )


# --------------------------------------------------------------------------
# Selection value objects (items + phase in, outcomes out)
# --------------------------------------------------------------------------


@dataclasses.dataclass
class GuidedItem:
    """One selected video with stage state derived from real state."""

    video_id: str
    canonical_url: str | None
    classification: str
    stages: dict[str, str]
    run_dir: str | None = None
    notes: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Selection:
    """The tanda chosen for this invocation plus everything left out."""

    items: list[GuidedItem] = dataclasses.field(default_factory=list)
    excluded: list[dict[str, str]] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [item.video_id for item in self.items]


def run_dir_of(state: StateRoot, video_id: str) -> str | None:
    entry = Processed(state).get(video_id)
    if entry is None or not entry.artifacts:
        return None
    return str(state.root / entry.artifacts[0])


# Historical private name kept as an alias for pre-S3 importers; new
# code (and the collection driver since S3) uses the public name.
_run_dir_of = run_dir_of


def lease_from_stages(
    stages: StageFunctions, state: StateRoot
) -> gpu_lease.GpuLease:
    """The ONE lease wiring (S3): a GPU lease whose lifecycle hooks are
    resolved through a stage bundle at call time, with the state-root
    lease marker. The stage engine's primary whisper swap and the
    collection driver's swap-retry window both build their lease here,
    so the hook/marker glue exists in exactly one place."""

    return gpu_lease.GpuLease(
        hooks=gpu_lease.LeaseHooks(
            stop=lambda info: stages.whisper_stop(info),
            restore=lambda info: stages.whisper_restore(info),
            health=lambda: stages.whisper_health(),
        ),
        marker=gpu_lease.LeaseMarkerFile(state),
    )


# --------------------------------------------------------------------------
# The stage engine
# --------------------------------------------------------------------------


class StageEngine:
    """One consent-gated stage execution over a selection.

    Everything of the former ``GuidedRun`` body except the adapters:
    stage execution, the whisper swap through the GPU lease, per-stage
    records, interruption bookkeeping and report building. Call order,
    messages, consent windows and report shape are byte-identical to the
    pre-extraction coordinator (S4); only the I/O seams moved.
    """

    def __init__(
        self,
        state: StateRoot,
        *,
        stages: StageFunctions,
        approve: Callable[[ConsentWindow], bool],
        notify: Callable[[str], None],
        auto_synthesizer: Callable[[str, StateRoot, Callable[[ConsentWindow], bool]], Any]
        | None = None,
    ) -> None:
        self.state = state
        self.stages = stages
        self._approve = approve
        self._notify = notify
        # Optional Phase 0 hook: called (video_id, state, ask) instead of
        # reporting waiting_for_synthesis when no document was supplied.
        # The provider implementation owns its own consent window (asked
        # through the approve adapter) and MUST route the result through
        # the existing validated-document ingestion stage.
        self.auto_synthesizer = auto_synthesizer
        self.consent_log: list[dict[str, Any]] = []
        self.stage_calls: list[str] = []
        self.failures: list[dict[str, str]] = []
        self.cleanup_notes: list[str] = []
        self._interrupted = False
        self._gpu_cleanup_failed = False
        # Whisper coordination (Phase 0 hooks): the GPU LEASE is the
        # single owner of this run's stop/restore swap (S1/S3) — it
        # writes the persisted marker BEFORE the stop, performs exactly
        # ONE start attempt, restores under the standing authorization
        # obtained BEFORE the stop, never restores when an interruption
        # landed inside the stop itself, and records honest bookkeeping
        # this coordinator maps onto its operator-facing reports. The
        # hooks are resolved through self.stages at call time.
        self._gpu_lease = lease_from_stages(stages, state)
        self.whisper_restore_failed = False
        # Standing-restore bookkeeping: the restore/recovery authorization
        # is asked BEFORE the stop (a Ctrl-C never grants anything, and a
        # fresh prompt may be unavailable after an interrupt), so recovery
        # runs under that prior authorization — never re-asked, never
        # retried, and always reported honestly.
        self._whisper_restore_authorized = False
        # Set when an interruption leaves a cleanup outcome UNKNOWN (for
        # example a Ctrl-C landing inside the whisper stop itself): the
        # generic interruption message must then NOT claim that the
        # applicable cleanup ran.
        self._interrupt_cleanup_uncertain = False
        self._progress_blocked_reason: str | None = None
        self._vision_window_closed = False

    # -- consent (asked through the injected adapter) ------------------------

    def _ask(self, window: ConsentWindow) -> bool:
        granted = self._approve(window)
        self.consent_log.append(
            {
                "window": window.kind,
                "ids": list(window.ids),
                "destinations": list(window.destinations),
                "granted": granted,
                "asked_at": utc_now_iso(),
            }
        )
        return granted

    # -- report helpers -----------------------------------------------------

    def _record(self, report_items: dict[str, Any], item: GuidedItem,
                stage: str, outcome: Any) -> None:
        value = getattr(outcome, "outcome", str(outcome))
        reason = str(getattr(outcome, "reason", ""))
        report_items.setdefault(item.video_id, {})[stage] = {
            "outcome": value,
            "reason": reason,
            "reused": bool(getattr(outcome, "reused", False)),
        }
        # Sequencing within THIS invocation follows the stage result just
        # returned; the durable state remains the product's own manifests.
        item.stages[stage] = value
        if value != "complete":
            self.failures.append(
                {
                    "id": item.video_id,
                    "stage": stage,
                    "outcome": value,
                    "reason": reason,
                }
            )

    def _item_stage_state(self, item: GuidedItem, stage: str) -> str:
        return item.stages.get(stage, "pending")

    # -- phases ---------------------------------------------------------------

    def _fetch_phase(self, selection: Selection, report_items: dict) -> None:
        pending = [
            item for item in selection.items
            if self._item_stage_state(item, "fetch") != "complete"
        ]
        if not pending:
            return
        window = ConsentWindow(
            kind="fetch",
            title="network download of the selected TikTok URLs",
            ids=tuple(item.video_id for item in pending),
            destinations=tuple(
                item.canonical_url or f"(unknown URL for {item.video_id})"
                for item in pending
            ),
            operations=(
                "gated pinned-extractor download (yt-dlp "
                f"{config.EXTRACTOR_PIN}) of exactly the URLs listed above",
                "oEmbed metadata request per URL",
            ),
            limits=(
                f"fetch deadline {config.BUDGETS.fetch_deadline_seconds}s per clip",
                f"max {config.BUDGETS.media_max_bytes} bytes per clip",
                "no automatic retry; any extractor gate failure is final (blocked)",
            ),
            effects=(
                "downloads media over the network into the local cache",
                "persists fetch.json/meta.json and the processed entry",
            ),
        )
        if not self._ask(window):
            selection.notes.append(
                "fetch window denied: no download ran; remaining stages that "
                "need media did not run"
            )
            return
        for item in pending:
            if not item.canonical_url:
                reason = (
                    "canonical URL unknown for this id (no scan item and no "
                    "inventory record); fetch cannot run — record the "
                    "collection scan or fetch the URL directly first"
                )
                self.failures.append(
                    {
                        "id": item.video_id,
                        "stage": "fetch",
                        "outcome": "failed",
                        "reason": reason,
                    }
                )
                report_items.setdefault(item.video_id, {})["fetch"] = {
                    "outcome": "failed",
                    "reason": reason,
                    "reused": False,
                }
                continue
            self.stage_calls.append(f"fetch:{item.video_id}")
            outcome = self.stages.fetch(item.canonical_url, self.state)
            self._record(report_items, item, "fetch", outcome)

    def _prepare_phase(self, selection: Selection, report_items: dict) -> None:
        pending = [
            item for item in selection.items
            if self._item_stage_state(item, "fetch") == "complete"
            and self._item_stage_state(item, "prepare") != "complete"
        ]
        if not pending:
            return
        window = ConsentWindow(
            kind="prepare",
            title="CPU preparation inside the pinned container",
            ids=tuple(item.video_id for item in pending),
            operations=(
                "FFmpeg/ffprobe via podman from "
                f"{config.FFMPEG_IMAGE_REF} (--network none, --read-only)",
                "hybrid frame extraction + 16 kHz mono WAV normalization",
            ),
            limits=(
                f"{config.BUDGETS.cpu_prep_deadline_seconds}s deadline per clip",
                f"{config.BUDGETS.cpu_prep_max_bytes} bytes derived-output cap per clip",
            ),
            effects=(
                "runs local containers (CPU only, no network)",
                "persists frames, sampling manifest and audio.wav",
            ),
        )
        if not self._ask(window):
            selection.notes.append("prepare window denied: no container ran")
            return
        for item in pending:
            self.stage_calls.append(f"prepare:{item.video_id}")
            outcome = self.stages.prepare(item.video_id, self.state)
            self._record(report_items, item, "prepare", outcome)

    def _whisper_pre_vision(
        self, selection: Selection, report_items: dict
    ) -> bool:
        """Ensure the whisper precondition for vision, coordinating the
        KNOWN shared service when (and only when) lifecycle hooks exist.

        Without hooks this is the documented standalone behavior: the
        read-only stopped-check either passes or vision is blocked with
        the operator instruction. With hooks, a running service is
        identity-verified against the known shared service, then TWO
        separate consent windows are asked BEFORE any mutation: the
        bounded stop, and the STANDING restore/recovery authorization
        for the same service/configuration (an interrupt never grants a
        window, and a fresh prompt may be unavailable after one, so the
        exact recovery authorization is obtained up front). Any denial
        or failed verification blocks BEFORE any mutation. Returns True
        when vision may proceed.
        """
        pending = [
            item for item in selection.items
            if self._item_stage_state(item, "prepare") == "complete"
            and self._item_stage_state(item, "vision") != "complete"
        ]
        if not pending:
            return True
        try:
            self.stages.whisper_stopped()
            return True
        except Exception as exc:  # noqa: BLE001 - operator-facing block
            if not self.stages.whisper_lifecycle_ready():
                for item in pending:
                    self.failures.append(
                        {
                            "id": item.video_id,
                            "stage": "vision",
                            "outcome": "blocked",
                            "reason": str(exc),
                        }
                    )
                    report_items.setdefault(item.video_id, {})["vision"] = {
                        "outcome": "blocked",
                        "reason": str(exc),
                        "reused": False,
                    }
                self._notify(f"vision blocked before any model load: {exc}")
                return False
            inspect = self.stages.whisper_inspect
            stop = self.stages.whisper_stop
            assert inspect is not None and stop is not None  # narrowing
            info = inspect()
            if info is None:
                reason = (
                    "the whisper stopped-check failed but the known shared "
                    "service cannot be identified (podman inspect finds no "
                    "such container); refusing to touch any service — "
                    f"original check: {exc}"
                )
                self._block_vision_all(pending, report_items, reason)
                return False
            # Identity verification BEFORE the consent window: the
            # operator is never asked to authorize stopping something
            # that would then fail the identity check (P0-FR-11).
            from .whisper_lifecycle import WhisperLifecycleError
            from .whisper_lifecycle import verify_known_service as _verify

            try:
                _verify(info)
            except WhisperLifecycleError as verify_exc:
                reason = (
                    f"whisper identity verification failed BEFORE any "
                    f"mutation or consent: {verify_exc}"
                )
                self._block_vision_all(pending, report_items, reason)
                return False
            service_text = getattr(info, "describe", lambda: str(info))()
            window = ConsentWindow(
                kind="whisper-stop",
                title=(
                    "stop the KNOWN shared whisper service for the vision "
                    "window (identity verified)"
                ),
                ids=tuple(item.video_id for item in pending),
                destinations=(service_text,),
                operations=(
                    f"podman stop --timeout 30 of exactly {service_text}",
                    "positive verification that it is no longer running",
                ),
                limits=(
                    "bounded stop: SIGTERM with a 30s grace, then podman "
                    "escalates to SIGKILL",
                    "only the known shared service is ever stopped — no "
                    "arbitrary or unidentified workload",
                    "the stop runs ONLY if the separate restore/recovery "
                    "authorization below is also granted: without it, no "
                    "mutation happens at all",
                ),
                effects=(
                    "the shared whisper service releases its GPU residency "
                    "until restored after this batch",
                    "the SAME service/configuration is restored under the "
                    "standing authorization asked next — after the vision "
                    "window, on failure, and on interruption (Ctrl-C)",
                ),
            )
            if not self._ask(window):
                reason = (
                    "whisper-stop window denied: no service was stopped "
                    "and no mutation occurred; vision is blocked for this "
                    "invocation"
                )
                self._block_vision_all(pending, report_items, reason)
                return False
            # Standing recovery authorization, asked BEFORE the stop: an
            # interrupt (Ctrl-C) never grants a window, and after one a
            # fresh prompt may be unavailable — so the exact restore/recovery
            # authorization for the SAME service/configuration is obtained
            # up front. It is a SEPARATE consent, never the stop consent
            # reused. Denial blocks BEFORE any mutation.
            restore_window = ConsentWindow(
                kind="whisper-restore",
                title=(
                    "standing recovery authorization: restore the SAME "
                    "service after the window (asked BEFORE the stop)"
                ),
                ids=tuple(item.video_id for item in pending),
                destinations=(service_text,),
                operations=(
                    f"podman start of exactly {service_text} once the GPU "
                    "window ends — normally, on failure, or after an "
                    "interruption (Ctrl-C) — WITHOUT asking again",
                    "positive verification of the same name/image/config",
                    "health check of the whisper service before any audio "
                    "work",
                ),
                limits=(
                    "one start attempt per execution context; no automatic "
                    "retry",
                    "this authorization is asked BEFORE the stop: denial "
                    "means the stop never runs (no mutation occurs) and "
                    "vision stays blocked",
                    "it covers ONLY this run's recovery of this exact "
                    "service/configuration — it is not the stop consent "
                    "reused and never carries into another run or batch",
                    "restore/health failure (or an interruption during the "
                    "restore) blocks ALL further progress and is reported "
                    "with the actual partial state; no rollback is "
                    "performed or implied",
                ),
                effects=(
                    "the shared whisper service returns to GPU residency "
                    "with its recorded identity/configuration",
                    "audio transcription may proceed only after restored "
                    "health is positively validated",
                ),
            )
            if not self._ask(restore_window):
                reason = (
                    "whisper-restore standing authorization denied: without "
                    "an exact prior authorization for the recovery of this "
                    "service, the stop does not happen (no mutation "
                    "occurred); vision is blocked for this invocation — "
                    "re-invoke to be asked again"
                )
                self._block_vision_all(pending, report_items, reason)
                return False
            try:
                # The lease is the single owner of the swap (S1): it
                # persists the lease marker BEFORE issuing the stop (S3)
                # and performs the stop through the verified hook. The
                # lease starts ONLY here, after BOTH consents.
                self._gpu_lease.acquire(info)
            except KeyboardInterrupt:
                # The stop result is UNKNOWN: the interruption may have
                # landed before, during or after the actual termination.
                # Never guess and never restore — the stop cannot be
                # verified as ours/completed, so recovery under the
                # standing authorization does not apply here. No retry,
                # no vision/audio on this path; re-raise keeps the run
                # interrupted. The operator gets the exact service and
                # the manual verification/recovery instruction.
                self._interrupt_cleanup_uncertain = True
                reason = (
                    f"whisper stop was interrupted: the stop result is "
                    f"UNKNOWN — the service ({service_text}) may be "
                    "RUNNING or STOPPED; verify it explicitly (podman "
                    "inspect/ps) and recover manually with this exact "
                    "service; NO restore was attempted because the stop "
                    "cannot be verified as completed by this run; no "
                    "further GPU work runs this invocation; no rollback "
                    "is performed or implied"
                )
                self.failures.append(
                    {
                        "id": "*",
                        "stage": "whisper-stop",
                        "outcome": "failed",
                        "reason": reason,
                    }
                )
                self.cleanup_notes.append(reason)
                self._notify(reason)
                raise
            except Exception as stop_exc:  # noqa: BLE001 - surfaced
                reason = (
                    f"whisper stop failed: {stop_exc}; the service state "
                    "must be verified explicitly before any GPU work; no "
                    "rollback is performed or implied"
                )
                self._block_vision_all(pending, report_items, reason)
                return False
            self._whisper_restore_authorized = True
            self.cleanup_notes.append(
                "shared whisper service stopped by this run after identity "
                "verification and TWO explicit consents (stop + standing "
                "restore); the restore runs under that standing "
                "authorization once the GPU window ends — normally, on "
                "failure, or after an interruption — and a restore/health "
                "failure blocks all progress with the actual partial state "
                "reported (no rollback promised)"
            )
            return True

    def _block_vision_all(
        self,
        pending: list[GuidedItem],
        report_items: dict,
        reason: str,
    ) -> None:
        for item in pending:
            self.failures.append(
                {
                    "id": item.video_id,
                    "stage": "vision",
                    "outcome": "blocked",
                    "reason": reason,
                }
            )
            report_items.setdefault(item.video_id, {})["vision"] = {
                "outcome": "blocked",
                "reason": reason,
                "reused": False,
            }
        self._notify(f"vision blocked before any model load: {reason}")

    def _whisper_pre_audio(self, selection: Selection, report_items: dict) -> bool:
        """Restore the whisper service THIS run stopped, before audio.

        Runs after the vision window (and the isolated-Ollama stop), on
        success AND failure paths of the batch. The restore executes
        under the STANDING authorization obtained BEFORE the stop (the
        separate ``whisper-restore`` window): it is never re-asked and
        never inferred. Returns True when audio may proceed (including
        when we never stopped anything).
        """
        if self._gpu_lease.stopped_info is None:
            return True
        return self._whisper_restore_now(
            report_items, context="after the vision window"
        )

    def _whisper_restore_now(self, report_items: dict, *, context: str) -> bool:
        """Restore under the standing authorization; report honestly.

        The GPU lease owns the swap mechanics (S1): the ONE bounded start
        of the SAME container (positive identity/config verification
        inside the hook), the persisted marker clear on the verified
        healthy path (S3), and exactly-once bookkeeping. This coordinator
        keeps the operator-facing honesty: after the start comes a
        BOUNDED read-only readiness wait (first probe immediate, fixed
        interval, small monotonic deadline — the ONE shared wait, S2),
        and any failure — or an interruption that leaves the restore
        incomplete — blocks ALL further progress (audio included): the
        run reports the actual partial service state with instructions
        and promises no rollback. Never re-asked, never retried, and
        the start itself is never repeated: one attempt per execution
        context, polling only.
        """
        lease = self._gpu_lease
        if lease.restore_attempted:
            # The ONE authorized restore already ran (or was in flight):
            # never a second start, never a re-ask — only the recorded
            # outcome decides whether progress may continue.
            return lease.restore_completed
        info = lease.stopped_info
        assert info is not None  # narrowing: callers check before calling
        service_text = getattr(info, "describe", lambda: str(info))()
        restore = self.stages.whisper_restore
        assert restore is not None  # narrowing: hooks are all-or-nothing
        try:
            outcome = lease.restore_once(
                waiting_feedback=lambda deadline: self._notify(
                    f"waiting for Whisper readiness after the restore "
                    f"(read-only health polling for up to {deadline:.0f}s; "
                    "no further start is attempted)"
                ),
                # Resolved HERE so tests patching ``engine.time`` keep
                # driving the deterministic clock through this seam.
                monotonic=time.monotonic,
                sleep=time.sleep,
            )
        except KeyboardInterrupt:
            # The interruption itself is never granted anything. Where it
            # landed decides the honest report: inside the start the
            # restore outcome is UNKNOWN; inside the readiness wait the
            # service WAS started (identity verified) and only its health
            # is UNKNOWN. No retry in either case.
            self.whisper_restore_failed = True
            if lease.restore_interrupt_phase == "start":
                reason = (
                    f"whisper restore was interrupted {context}: the service "
                    f"stopped by this run ({service_text}) is in an UNKNOWN "
                    "state (the start may or may not have completed) — verify "
                    "it explicitly (podman inspect/ps, health endpoint); ALL "
                    "further progress is blocked; no rollback is performed or "
                    "implied"
                )
            else:
                reason = (
                    f"whisper restore health wait was interrupted {context}: "
                    f"the service {service_text} was STARTED (identity "
                    "verified) but its health is UNKNOWN (the readiness "
                    "polling may or may not have completed) — verify it "
                    "explicitly (health endpoint, podman inspect/ps); ALL "
                    "further progress is blocked; the ONE authorized start "
                    "already happened and is never repeated; no rollback is "
                    "performed or implied"
                )
            self.failures.append(
                {"id": "*", "stage": "whisper-restore", "outcome": "failed", "reason": reason}
            )
            self.cleanup_notes.append(reason)
            self._notify(reason)
            raise
        if outcome.status == "restore_failed":
            exc = outcome.error
            assert exc is not None  # narrowing: status carries the error
            self.whisper_restore_failed = True
            reason = (
                f"whisper restore failed {context}: {exc}; the service "
                f"stopped by this run ({service_text}) may still be "
                "STOPPED or partially started — verify its state "
                "explicitly; ALL further progress is blocked; no rollback "
                "is performed or implied"
            )
            self.failures.append(
                {"id": "*", "stage": "whisper-restore", "outcome": "failed", "reason": reason}
            )
            self.cleanup_notes.append(reason)
            self._notify(reason)
            return False
        if outcome.status == "health_timeout":
            detail = outcome.detail
            self.whisper_restore_failed = True
            reason = (
                f"restored whisper service did not become healthy within "
                f"{WHISPER_RESTORE_READY_DEADLINE_SECONDS:.0f}s "
                f"({detail}); the service {service_text} was started but "
                "is NOT healthy — ALL further progress is blocked; verify "
                "the service state explicitly; the ONE authorized start "
                "is never repeated; no rollback is performed or implied"
            )
            self.failures.append(
                {"id": "*", "stage": "whisper-restore", "outcome": "failed", "reason": reason}
            )
            self.cleanup_notes.append(reason)
            self._notify(reason)
            return False
        self.cleanup_notes.append(
            f"shared whisper service restored and healthy {context} "
            f"({outcome.detail[:120]})"
        )
        return True

    def _vision_phase(self, selection: Selection, report_items: dict) -> list[str]:
        """Run pending vision stages inside one consented GPU window.

        Order and safety come from the stage itself (whisper-stopped
        precheck, GateLedger, verified unload). This coordinator only
        adds: explicit consent, isolated-server lifecycle for a server
        IT started, and the stop-on-failure policy. Returns the ids whose
        vision is complete after the phase.
        """
        pending = [
            item for item in selection.items
            if self._item_stage_state(item, "prepare") == "complete"
            and self._item_stage_state(item, "vision") != "complete"
        ]
        if not pending:
            return [
                item.video_id for item in selection.items
                if self._item_stage_state(item, "vision") == "complete"
            ]
        if self.stages.whisper_lifecycle_ready():
            self._notify(
                "gpu phase: whisper must be STOPPED for vision — when it is "
                "running, this run stops the KNOWN shared service behind "
                "explicit consent and restores it under the standing "
                "authorization"
            )
        else:
            self._notify(
                "gpu phase: whisper service must be STOPPED for vision "
                "(operator-managed; this command never stops or starts it)"
            )
        handle: Any = None
        try:
            try:
                self.stages.whisper_stopped()
            except Exception as exc:  # noqa: BLE001 - operator-facing block
                for item in pending:
                    self.failures.append(
                        {
                            "id": item.video_id,
                            "stage": "vision",
                            "outcome": "blocked",
                            "reason": str(exc),
                        }
                    )
                    report_items.setdefault(item.video_id, {})["vision"] = {
                        "outcome": "blocked",
                        "reason": str(exc),
                        "reused": False,
                    }
                self._notify(f"vision blocked before any model load: {exc}")
                return [
                    item.video_id for item in selection.items
                    if self._item_stage_state(item, "vision") == "complete"
                ]
            if self.stages.ollama_reachable():
                reason = (
                    "isolated Ollama already answers on "
                    f"{config.OLLAMA_API_BASE}; guided-run never adopts a "
                    "preexisting process (identity and scope cannot be "
                    "verified from outside); stop it explicitly and re-invoke"
                )
                for item in pending:
                    self.failures.append(
                        {
                            "id": item.video_id,
                            "stage": "vision",
                            "outcome": "blocked",
                            "reason": reason,
                        }
                    )
                    report_items.setdefault(item.video_id, {})["vision"] = {
                        "outcome": "blocked",
                        "reason": reason,
                        "reused": False,
                    }
                return [
                    item.video_id for item in selection.items
                    if self._item_stage_state(item, "vision") == "complete"
                ]
            handle = self.stages.ollama_start()
            self.cleanup_notes.append(
                "isolated Ollama started by this run; consent covers its stop "
                "at window end or failure"
            )
            self.stages.ollama_wait_ready(handle)
            for item in pending:
                self.stage_calls.append(f"vision:{item.video_id}")
                outcome = self.stages.vision(item.video_id, self.state)
                self._record(report_items, item, "vision", outcome)
                if getattr(outcome, "outcome", "") != "complete":
                    # Stop-on-failure policy: no further GPU load for ANY
                    # id this invocation; the stage's own bounded cleanup
                    # and verified unload already ran inside the stage.
                    self._vision_window_closed = True
                    self._notify(
                        "gpu window ended: no further model load this "
                        "invocation (gate/error is final for the window)"
                    )
                    break
        finally:
            if handle is not None:
                try:
                    self.stages.ollama_stop(handle)
                    self.cleanup_notes.append("isolated Ollama stopped cleanly")
                except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
                    self.cleanup_notes.append(
                        f"ISOLATED OLLAMA STOP FAILED: {exc}; the server this "
                        "run started may still be running — stop it explicitly "
                        f"(pid file {config.OLLAMA_PID_PATH})"
                    )
                    self.failures.append(
                        {
                            "id": "*",
                            "stage": "gpu-cleanup",
                            "outcome": "failed",
                            "reason": f"isolated Ollama stop failed: {exc}",
                        }
                    )
                    # Explicit block: with the server cleanup unverified the
                    # GPU window does not advance to audio.
                    self._gpu_cleanup_failed = True
        return [
            item.video_id for item in selection.items
            if self._item_stage_state(item, "vision") == "complete"
        ]

    def _audio_phase(
        self, selection: Selection, report_items: dict, eligible_ids: list[str]
    ) -> None:
        pending = [
            item for item in selection.items
            if item.video_id in eligible_ids
            and self._item_stage_state(item, "audio") != "complete"
        ]
        if not pending:
            return
        healthy, detail = self.stages.whisper_health()
        if not healthy:
            reason = (
                f"whisper service not ready for audio ({detail}); start it "
                "explicitly (operator-managed), then re-invoke guided-run — "
                "completed vision stages are reused with zero re-inference"
            )
            for item in pending:
                self.failures.append(
                    {
                        "id": item.video_id,
                        "stage": "audio",
                        "outcome": "blocked",
                        "reason": reason,
                    }
                )
                report_items.setdefault(item.video_id, {})["audio"] = {
                    "outcome": "blocked",
                    "reason": reason,
                    "reused": False,
                }
            self._notify(f"audio blocked: {reason}")
            return
        for item in pending:
            self.stage_calls.append(f"audio:{item.video_id}")
            outcome = self.stages.audio(item.video_id, self.state)
            self._record(report_items, item, "audio", outcome)

    def _collection_audio_phase(self, selection: Selection, report_items: dict) -> None:
        """Audio-only window: preparation, not vision, determines eligibility."""
        pending = [
            item for item in selection.items
            if self._item_stage_state(item, "prepare") == "complete"
            and self._item_stage_state(item, "audio") != "complete"
        ]
        if not pending:
            return
        window = ConsentWindow(
            kind="gpu",
            title="prepared-audio transcription window (before collection vision)",
            ids=tuple(item.video_id for item in pending),
            operations=(
                "unconditional full-audio transcription of exactly the prepared "
                "ids above through the shared whisper WS service; no vision "
                "completion is required",
                "no isolated Ollama lifecycle or vision model load",
            ),
            limits=(
                f"audio deadline {config.BUDGETS.audio_stage_deadline_seconds}s per id",
                "completed audio is reused, never re-transcribed; no retry",
                "health must pass before work; no authority to start a missing service",
            ),
            effects=("persists audio evidence and the existing audio stage record",),
        )
        if not self._ask(window):
            selection.notes.append("audio window denied: no transcription ran")
            return
        self._audio_phase(selection, report_items, [item.video_id for item in pending])

    def _gpu_phase(
        self, selection: Selection, report_items: dict, *, vision_only: bool = False
    ) -> None:
        vision_pending = [
            item for item in selection.items
            if self._item_stage_state(item, "prepare") == "complete"
            and self._item_stage_state(item, "vision") != "complete"
        ]
        audio_pending = [
            item for item in selection.items
            if not vision_only
            and self._item_stage_state(item, "vision") == "complete"
            and self._item_stage_state(item, "audio") != "complete"
        ]
        # The window names every id that COULD reach audio this invocation
        # (an id whose vision completes inside this window follows straight
        # into the audio phase); consent must name the superset, not just
        # the ids whose vision was already complete.
        audio_possible = [
            item for item in selection.items
            if not vision_only
            and self._item_stage_state(item, "prepare") == "complete"
            and self._item_stage_state(item, "audio") != "complete"
        ]
        if not vision_pending and not audio_pending:
            return
        # Conditional audio: ids that reach audio ONLY if their vision
        # completes inside this window. The consent line must state that
        # condition honestly — audio is possible, never guaranteed: a
        # vision failure, a fired gate or a denial means no audio for
        # that id. Ids whose audio is already complete are reused, never
        # re-transcribed, and are named as such.
        audio_pending_ids = {item.video_id for item in audio_pending}
        audio_conditional = [
            item for item in audio_possible
            if item.video_id not in audio_pending_ids
        ]
        audio_already_complete = [
            item for item in selection.items
            if not vision_only and self._item_stage_state(item, "audio") == "complete"
        ]
        window_ids: list[str] = [
            item.video_id for item in vision_pending
        ]
        for item in audio_possible:
            if item.video_id not in window_ids:
                window_ids.append(item.video_id)
        if audio_pending and audio_conditional:
            audio_operation = (
                "full-audio transcription through the shared whisper WS "
                f"service for the {len(audio_pending)} vision-complete "
                f"id(s), and CONDITIONALLY for the {len(audio_conditional)} "
                "id(s) whose vision completes inside this window — if an "
                "id's vision fails, a gate fires or the window closes, "
                "that id's audio does not run"
            )
        elif audio_pending:
            audio_operation = (
                "full-audio transcription through the shared whisper WS "
                "service per pending id"
            )
        elif audio_conditional:
            audio_operation = (
                "conditional full-audio transcription through the shared "
                f"whisper WS service for the {len(audio_conditional)} id(s) "
                "whose vision completes inside this window — audio runs "
                "only after that id's vision succeeds; vision failure, a "
                "fired gate or denial means no audio runs"
            )
        else:
            audio_operation = "no audio transcription (none pending)"
        if audio_already_complete:
            audio_operation += (
                f"; the {len(audio_already_complete)} id(s) whose audio is "
                "already complete are reused as-is, never re-transcribed"
            )
        window = ConsentWindow(
            kind="gpu",
            title=("local vision-only GPU window" if vision_only
                   else "local GPU inference window (vision and/or audio)"),
            ids=tuple(window_ids),
            operations=(
                (
                    "isolated Ollama lifecycle (start + stop) around the vision "
                    f"stages on {config.OLLAMA_API_BASE}"
                    if vision_pending
                    else "no isolated Ollama lifecycle (no vision pending)"
                ),
                (
                    f"gated vision inference ({config.VISION_MODEL}) per pending id"
                    if vision_pending
                    else "no vision inference (none pending)"
                ),
                audio_operation,
            ),
            limits=(
                f"free VRAM >= {config.GATES.free_vram_gate_mib} MiB before each "
                f"{config.VISION_MODEL} load",
                f"swap delta <= {config.GATES.swap_delta_limit_mib} MiB per phase",
                "zero CPU offload (any offloaded layer fails the run)",
                "a fired gate is FINAL for this window: no auto-retry",
                f"vision deadline {config.BUDGETS.vision_stage_deadline_seconds}s, "
                f"audio deadline {config.BUDGETS.audio_stage_deadline_seconds}s",
            ),
            effects=(
                "loads the vision model on the GPU and verifies unload after "
                "the stage (empty residency + observed release)",
                (
                    "whisper must be STOPPED for vision and RUNNING for "
                    "audio — when it is running, this run coordinates the "
                    "KNOWN shared service behind explicit consent (bounded "
                    "stop before vision, standing restore after the window)"
                    if self.stages.whisper_lifecycle_ready()
                    else "whisper must be STOPPED for vision and RUNNING "
                    "for audio — both are operator actions; this command "
                    "never manages that service"
                ),
                "the isolated Ollama server started here is stopped at window "
                "end or on failure",
            ),
        )
        if not self._ask(window):
            selection.notes.append(
                "gpu window denied: no inference ran and no server was started"
            )
            return
        # Whisper precondition (Phase 0 coordination when hooks exist):
        # without it vision below blocks in _vision_phase as before.
        if not self._whisper_pre_vision(selection, report_items):
            if vision_only:
                self._progress_blocked_reason = (
                    "whisper vision precondition failed: service state/authority "
                    "must be resolved before further collection progress"
                )
                return
            selection.notes.append(
                "vision blocked by the whisper precondition; audio for "
                "already-vision-complete ids may still proceed (the "
                "service is running in that case)"
            )
            # A service stopped by THIS run cannot exist on this path (a
            # successful stop implies vision was unblocked), so there is
            # nothing to restore; audio eligibility is decided by the
            # audio phase's own health precheck.
            self._audio_phase(
                selection,
                report_items,
                [
                    item.video_id for item in selection.items
                    if self._item_stage_state(item, "vision") == "complete"
                ],
            )
            return
        try:
            vision_complete = self._vision_phase(selection, report_items)
            if self._gpu_cleanup_failed:
                selection.notes.append(
                    "audio NOT attempted: the isolated Ollama stop failed and the "
                    "GPU window does not advance while cleanup is unverified; "
                    "resolve the server state explicitly, then re-invoke (vision "
                    "results already complete are reused)"
                )
                # The whisper service this run stopped is STILL restored: an
                # authorized window never leaves it stopped behind a block.
                self._whisper_pre_audio(selection, report_items)
                return
            if not self._whisper_pre_audio(selection, report_items):
                return
            if not vision_only:
                self._audio_phase(selection, report_items, vision_complete)
        except KeyboardInterrupt:
            # An interrupt never grants anything and never skips recovery.
            # The isolated Ollama server this run started was already
            # stopped by _vision_phase's finally; the whisper service this
            # run stopped is recovered here ONLY under the standing
            # authorization obtained BEFORE the stop — never re-asked
            # (a Ctrl-C must not depend on a fresh prompt), never retried.
            self._notify(
                "interrupted during the GPU window: the isolated Ollama "
                "server this run started was stopped by the window "
                "cleanup; the whisper service this run stopped (if any) "
                "is recovered next under its prior standing authorization"
            )
            if self._gpu_lease.stopped_info is not None:
                if not self._gpu_lease.restore_attempted:
                    self._whisper_restore_now(
                        report_items, context="after interruption (Ctrl-C)"
                    )
                elif not self._gpu_lease.restore_completed:
                    # A restore was already in flight when the interrupt
                    # landed: _whisper_restore_now's own handler already
                    # reported the UNKNOWN state and appended the failure
                    # record. Keep the blocking invariant (idempotent),
                    # with NO retry, NO new ask and NO duplicate report.
                    self.whisper_restore_failed = True
            raise
        except Exception as exc:  # unexpected boundary failure, not a stage outcome
            self._progress_blocked_reason = f"GPU boundary failed: {exc}"
            self.failures.append({
                "id": "*", "stage": "gpu", "outcome": "failed",
                "reason": self._progress_blocked_reason,
            })
        finally:
            # Also recover after an unexpected exception from start/readiness
            # hooks. Ordinary success and interrupt paths already consumed the
            # single restore; the lease's bookkeeping makes this check
            # idempotent.
            if self._gpu_lease.stopped_info is not None and not self._gpu_lease.restore_attempted:
                self._whisper_restore_now(report_items, context="after GPU boundary exit")

    def _synthesis_and_verify_phase(
        self,
        selection: Selection,
        report_items: dict,
        synthesis_docs: Mapping[str, Path],
        *,
        verify: bool = True,
    ) -> None:
        if (
            self.whisper_restore_failed
            or self._gpu_cleanup_failed
            or self._interrupt_cleanup_uncertain
            or self._progress_blocked_reason is not None
        ):
            selection.notes.append(
                "synthesis and verification blocked: service/GPU recovery is not verified"
            )
            return
        waiting: list[dict[str, str]] = []
        for item in selection.items:
            if (
                self._item_stage_state(item, "vision") != "complete"
                or self._item_stage_state(item, "audio") != "complete"
            ):
                continue
            if self._item_stage_state(item, "synthesis") == "complete":
                continue
            document = synthesis_docs.get(item.video_id)
            if document is None and self.auto_synthesizer is None:
                run_dir = item.run_dir or run_dir_of(self.state, item.video_id)
                waiting.append(
                    {
                        "id": item.video_id,
                        "video_md": f"{run_dir}/video.md" if run_dir else "video.md (no run dir yet)",
                        "audio_md": f"{run_dir}/audio.md" if run_dir else "audio.md (no run dir yet)",
                        "resume": (
                            "supply the operator-authored synthesis document and "
                            f"re-invoke: guided-run ... --synthesis "
                            f"{item.video_id}=<path>  (or synthesize "
                            f"{item.video_id} --from-file <path> directly); "
                            "synthesize only VALIDATES and persists the "
                            "document — no model calls, no generated content"
                        ),
                    }
                )
                continue
            if document is None:
                # Phase 0 automatic synthesis: the provider hook owns its
                # consent window (logged through self._ask) and MUST route
                # the result through the existing validated-document
                # ingestion stage — never around it.
                self.stage_calls.append(f"synthesize-api:{item.video_id}")
                outcome = self.auto_synthesizer(
                    item.video_id, self.state, self._ask
                )
                self._record(report_items, item, "synthesis", outcome)
                continue
            self.stage_calls.append(f"synthesize:{item.video_id}")
            outcome = self.stages.synthesize(item.video_id, self.state, document)
            self._record(report_items, item, "synthesis", outcome)
        for entry in waiting:
            self._notify(
                f"waiting for synthesis document for {entry['id']}: evidence at "
                f"{entry['video_md']} and {entry['audio_md']}; {entry['resume']}"
            )

        if not verify:
            return

        # Verify consent happens ONLY after the real candidate URLs are
        # known from the supplied/validated documents.
        verify_pending = [
            item for item in selection.items
            if self._item_stage_state(item, "synthesis") == "complete"
            and self._item_stage_state(item, "verify") != "complete"
        ]
        if not verify_pending:
            return
        urls: list[str] = []
        per_id_urls: dict[str, list[str]] = {}
        for item in verify_pending:
            run_dir = run_dir_of(self.state, item.video_id)
            doc_urls: list[str] = []
            if run_dir is not None:
                path = Path(run_dir) / "synthesis.json"
                if path.is_file():
                    try:
                        document = SynthesisDocument.from_dict(
                            json.loads(path.read_text(encoding="utf-8"))
                        )
                        for claim in document.claims:
                            for url in claim.candidate_urls:
                                if url not in doc_urls:
                                    doc_urls.append(url)
                        for entity in document.entities:
                            for url in entity.candidate_urls:
                                if url not in doc_urls:
                                    doc_urls.append(url)
                    except (OSError, ValueError) as exc:
                        self.failures.append(
                            {
                                "id": item.video_id,
                                "stage": "verify",
                                "outcome": "failed",
                                "reason": f"cannot read candidate URLs: {exc}",
                            }
                        )
                        continue
            per_id_urls[item.video_id] = doc_urls
            for url in doc_urls:
                if url not in urls:
                    urls.append(url)
        window = ConsentWindow(
            kind="verify",
            title="bounded verification retrieval of the EXACT candidate URLs",
            ids=tuple(item.video_id for item in verify_pending),
            destinations=tuple(urls) if urls else (
                "(no candidate URLs recorded — verify performs no network "
                "retrieval and records unverifiable/no_candidate_urls verdicts)",
            ),
            operations=(
                "bounded HTTP(S) retrieval of only the URLs listed above",
                "mechanical verdicts + pending backlog entry per id",
            ),
            limits=(
                f"{config.VERIFY_MAX_RESPONSE_BYTES} bytes per response",
                f"{config.VERIFY_TIMEOUT_SECONDS}s timeout, "
                f"{config.VERIFY_MAX_REDIRECTS} redirect hops, public http(s) only",
            ),
            effects=(
                "appends a PENDING backlog entry per id (duplicate-guarded); "
                "statuses are never edited here — review stays with "
                "backlog-list/show/decide/plan/apply",
            ),
        )
        if not self._ask(window):
            selection.notes.append(
                "verify window denied: no retrieval ran and no backlog entry "
                "was emitted this invocation"
            )
            return
        for item in verify_pending:
            if item.video_id not in per_id_urls:
                continue
            self.stage_calls.append(f"verify:{item.video_id}")
            outcome = self.stages.verify(item.video_id, self.state)
            self._record(report_items, item, "verify", outcome)

    # -- entry points ---------------------------------------------------------

    def execute_collection_phase(
        self, selection: Selection, phase: str
    ) -> dict[str, Any]:
        """One resource slice; the collection driver owns all barriers.

        Each call uses a fresh runner so standing restore authority and its
        consumed start attempt can never leak across actual stop cycles.
        This does not change the public guided-run selection or phase
        order. (Public since S2 so ``collection-run`` stops depending on
        the private ``GuidedRun`` hook; the driver keeps a one-line
        forwarder.)
        """
        if phase not in ("preparation", "audio", "vision", "synthesis"):
            raise GuidedError(f"unknown collection phase: {phase}")
        if len(selection.ids) > GUIDED_MAX_BATCH:
            raise GuidedError("collection phase exceeds the resource-window cap")
        report_items: dict[str, Any] = {}
        try:
            if phase == "preparation":
                window = ConsentWindow(
                    kind="tanda",
                    title="confirm this frozen collection resource slice",
                    ids=tuple(selection.ids),
                    operations=(
                        "prepare every frozen slice, then audio for every prepared "
                        "slice, then vision windows, then eligible synthesis, then "
                        "ONE separately authorized grouped verification",
                    ),
                    limits=(f"resource-window cap {GUIDED_MAX_BATCH}",),
                )
                if self._ask(window):
                    self._fetch_phase(selection, report_items)
                    self._prepare_phase(selection, report_items)
                else:
                    selection.notes.append("resource slice denied: preparation did not run")
            elif phase == "audio":
                self._collection_audio_phase(selection, report_items)
            elif phase == "vision":
                self._gpu_phase(selection, report_items, vision_only=True)
            else:
                self._synthesis_and_verify_phase(selection, report_items, {}, verify=False)
        except KeyboardInterrupt:
            self._interrupted = True
            if self._interrupt_cleanup_uncertain:
                self._notify(
                    "interrupted: cleanup state is NOT fully known; verify "
                    "and recover the exact Whisper service manually before progress"
                )
            selection.notes.append(
                "phase interrupted: partial stage evidence preserved; cleanup "
                "is unknown if the service stop itself was interrupted"
            )
        except Exception as exc:
            self._progress_blocked_reason = f"collection {phase} boundary failed: {exc}"
            self.failures.append({
                "id": "*", "stage": phase, "outcome": "failed",
                "reason": self._progress_blocked_reason,
            })
        return self.build_report(selection, report_items, mode="execute")

    def execute(
        self,
        selection: Selection,
        *,
        synthesis_docs: Mapping[str, Path] | None = None,
    ) -> dict[str, Any]:
        """Drive the selected tanda through consent-gated phases."""
        synthesis_docs = synthesis_docs or {}
        report_items: dict[str, Any] = {}
        tanda = ConsentWindow(
            kind="tanda",
            title="confirm this tanda (nothing has run yet)",
            ids=tuple(selection.ids),
            operations=(
                "process exactly these ids through fetch -> prepare -> vision "
                "-> audio -> synthesis ingestion -> verify, with a SEPARATE "
                "consent window before each effectful phase",
            ),
            limits=(f"batch cap {GUIDED_MAX_BATCH}; nothing outside this list is touched",),
            effects=(
                "each later window asks again before its own effects; denial "
                "of any window stops before that window's operations",
            ),
        )
        if not self._ask(tanda):
            selection.notes.append(
                "tanda confirmation denied: no stage ran, nothing was written"
            )
            return self.build_report(selection, report_items, mode="execute")

        try:
            self._fetch_phase(selection, report_items)
            self._prepare_phase(selection, report_items)
            self._gpu_phase(selection, report_items)
            self._synthesis_and_verify_phase(selection, report_items, synthesis_docs)
        except KeyboardInterrupt:
            self._interrupted = True
            if self._interrupt_cleanup_uncertain:
                self._notify(
                    "interrupted: partial results are preserved by the "
                    "stages themselves; cleanup state is NOT fully known "
                    "— see the UNKNOWN whisper-stop note above, verify "
                    "and recover the exact service manually before any "
                    "further GPU work; re-invocation needs new "
                    "authorization for pending operations"
                )
            else:
                self._notify(
                    "interrupted: partial results are preserved by the stages "
                    "themselves; applicable cleanup ran; re-invocation needs new "
                    "authorization for pending operations"
                )
        return self.build_report(selection, report_items, mode="execute")

    def build_report(
        self, selection: Selection, report_items: dict[str, Any], *, mode: str
    ) -> dict[str, Any]:
        waiting = []
        for item in selection.items:
            if (
                self._item_stage_state(item, "synthesis") != "complete"
                and self._item_stage_state(item, "audio") == "complete"
                and self._item_stage_state(item, "vision") == "complete"
            ):
                waiting.append(item.video_id)
        report: dict[str, Any] = {
            "mode": mode,
            "state_root": str(self.state.root),
            "selected": [
                {
                    "id": item.video_id,
                    "classification": item.classification,
                    "canonical_url": item.canonical_url,
                    "stages": dict(item.stages),
                    "run_dir": item.run_dir,
                }
                for item in selection.items
            ],
            "excluded": list(selection.excluded),
            "notes": list(selection.notes),
            "consent_log": list(self.consent_log),
            "stage_calls": list(self.stage_calls),
            "failures": list(self.failures),
            "cleanup_notes": list(self.cleanup_notes),
            "waiting_for_synthesis": waiting,
            "interrupted": self._interrupted,
            "whisper_restore_failed": self.whisper_restore_failed,
            "gpu_cleanup_failed": self._gpu_cleanup_failed,
            "progress_blocked_reason": self._progress_blocked_reason,
            "vision_window_closed": self._vision_window_closed,
            "stage_outcomes_this_run": report_items,
            "complete_is_not_confirmed": (
                "a stage outcome of 'complete' means the machinery finished "
                "honestly; claim verdict 'confirmed' can only come from the "
                "verification matrix — see verification.json and the backlog entry"
            ),
            "review_next": (
                "verify appends PENDING entries only; decide with the existing "
                "review CLI: backlog-list / backlog-show / backlog-decide "
                "--decisions-file F / backlog-plan / backlog-apply"
            ),
        }
        return report
