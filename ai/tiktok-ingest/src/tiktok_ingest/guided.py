"""Guided operation: one consent-gated coordinator of the EXISTING pipeline.

``guided-run`` is a thin orchestrator, not a framework. It sequences the
existing stage functions — ``fetch_url → prepare_video → run_vision_stage
→ run_audio_stage → run_synthesis_stage --from-file → run_verify_stage``
— behind explicit, per-window operator authorization, and stops at the
synthesis boundary when the operator has not supplied the document.

Honesty rules carried into this module (they are the product's rules,
restated):

- All progress is DERIVED from the product's own state (``Processed``
  entries, ``meta.json`` stage outcomes, fingerprints). This module keeps
  no second stage ledger and persists nothing: its report is a summary,
  never an authority parallel to the manifests.
- ``synthesize --from-file`` is validated-document INGESTION only. The
  guided command never generates, edits or auto-fills a synthesis
  document and never calls a text model.
- Stage outcome ``complete`` is NOT claim verdict ``confirmed``. The
  report keeps the two axes separate; verdicts live in
  ``verification.json`` and the emitted backlog entry.
- A fired gate is final: no automatic retry, no continued GPU load. A
  new invocation is a new authorization window (GateLedger semantics).
- Whisper is an operator-managed shared service: never started, stopped
  or restored here. Vision requires it stopped; audio requires it
  healthy. Both preconditions are surfaced as explicit blocks.
- The guided command never edits ``backlog.jsonl`` statuses and never
  writes the blocklist; ``verify`` appends ``pending`` entries exactly
  as it does today (duplicate-guarded). Review stays with the existing
  backlog review CLI.
- There is no product-wide writer lock. Like every writer, guided-run
  requires an exclusive-writer operational window; see OPERATIONS.md.

Failure policy (simple, documented): network/CPU-stage failures are
per-item (the failing ID is recorded and other IDs continue); ANY
non-complete vision outcome (fired gate, error, interrupt) ends the GPU
phase for every remaining ID — no further model load this invocation.
The isolated Ollama server this command itself started is stopped at
window end or failure (the GPU consent covers that stop). An abrupt
kill cannot guarantee cleanup: recovery is by evidence of the real
state (PID file, ``status``, stage fingerprints), never by promise.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import config
from .collection import (
    build_collection_plan,
    resolve_collection,
)
from .contracts import JobManifest, ProcessedEntry, utc_now_iso
from .ollama_runtime import start_server, stop_server, wait_ready
from .pipeline import fetch_url
from .prepare import prepare_video
from .state import Blocklist, Processed, StateRoot
from .synthesis import SynthesisDocument, run_synthesis_stage
from .verify import run_verify_stage
from .vision import assert_whisper_stopped, run_vision_stage
from .whisper_client import health_precheck, run_audio_stage

# The canonical order this coordinator drives. "emit" is displayed but
# performed inside the verify stage (duplicate-guarded backlog append).
GUIDED_STAGES: tuple[str, ...] = (
    "fetch",
    "prepare",
    "vision",
    "audio",
    "synthesis",
    "verify",
)

GUIDED_MAX_BATCH = config.BUDGETS.batch_pilot_clips


class GuidedError(RuntimeError):
    """Usage-level error (unknown IDs, bad limit, malformed --synthesis)."""


# --------------------------------------------------------------------------
# Consent windows
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ConsentWindow:
    """One explicit authorization request, shown BEFORE any effect runs.

    ``kind`` is one of ``tanda``, ``fetch``, ``prepare``, ``gpu``,
    ``verify``. Every field is display text for the operator; the
    guided command maps a granted window to exactly the operations
    named here and nothing else.
    """

    kind: str
    title: str
    ids: tuple[str, ...] = ()
    destinations: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class ConsentProvider:
    """Authorization source. ``ask`` returns True ONLY on explicit grant."""

    def ask(self, window: ConsentWindow) -> bool:  # pragma: no cover - iface
        raise NotImplementedError


class ConsoleConsentProvider(ConsentProvider):
    """Interactive console prompts; denial on EOF or non-interactive stdin.

    A non-tty stdin NEVER grants consent (no environment is trusted to
    imply authorization), a closed stream (EOF) never does, and only the
    literal words ``yes`` / ``y`` grant a window. There is no global
    ``--yes``: each window is asked on its own.
    """

    def __init__(
        self,
        *,
        stdin: Any | None = None,
        out: Callable[[str], None] | None = None,
    ) -> None:
        self._stdin = stdin
        # Prompts are interactive UI: stderr keeps stdout parseable.
        self._out = out or (lambda text: print(text, file=sys.stderr))

    def ask(self, window: ConsentWindow) -> bool:
        lines = [f"== authorization required: {window.title} =="]
        if window.ids:
            lines.append("  ids: " + ", ".join(window.ids))
        if window.destinations:
            lines.append("  destinations:")
            lines.extend(f"    - {d}" for d in window.destinations)
        if window.operations:
            lines.append("  operations:")
            lines.extend(f"    - {op}" for op in window.operations)
        if window.limits:
            lines.append("  limits:")
            lines.extend(f"    - {lim}" for lim in window.limits)
        if window.effects:
            lines.append("  effects:")
            lines.extend(f"    - {eff}" for eff in window.effects)
        self._out("\n".join(lines))
        stream = self._stdin if self._stdin is not None else sys.stdin
        if self._stdin is None and not stream.isatty():
            self._out(
                "refused: non-interactive environment; consent is never "
                "assumed (re-run from a terminal or use --dry-run)"
            )
            return False
        try:
            self._out("authorize this window? [yes/no]", end=" ")
            answer = stream.readline()
        except OSError as exc:
            self._out(f"refused: could not read authorization ({exc})")
            return False
        if not answer:
            self._out("refused: end of input; consent is never assumed")
            return False
        granted = answer.strip().lower() in ("yes", "y")
        self._out("granted" if granted else "denied — stopping before this window")
        return granted


# --------------------------------------------------------------------------
# Injectable stage boundary (production defaults call the real stages)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StageFunctions:
    """The stage boundary guided-run coordinates; injectable for tests.

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

    def whisper_lifecycle_ready(self) -> bool:
        return (
            self.whisper_inspect is not None
            and self.whisper_stop is not None
            and self.whisper_restore is not None
        )


def _ollama_reachable() -> bool:
    """Read-only probe: does ANYTHING answer on the isolated port?

    Used only to refuse adopting a preexisting process; version and
    scope verification stay inside the vision stage preconditions.
    """
    import urllib.request

    url = f"{config.OLLAMA_API_BASE.rstrip('/')}/api/version"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
            response.read(64)
        return True
    except (OSError, ValueError):
        return False


def default_stage_functions() -> StageFunctions:
    """The real pipeline stages; guided-run adds no behavior of its own."""
    return StageFunctions(
        fetch=lambda url, state: fetch_url(url, state=state),
        prepare=lambda video_id, state: prepare_video(video_id, state=state),
        vision=lambda video_id, state: run_vision_stage(video_id, state=state),
        audio=lambda video_id, state: run_audio_stage(video_id, state=state),
        synthesize=lambda video_id, state, from_file: run_synthesis_stage(
            video_id, state=state, from_file=from_file
        ),
        verify=lambda video_id, state: run_verify_stage(video_id, state=state),
        whisper_stopped=lambda: assert_whisper_stopped(),
        whisper_health=lambda: health_precheck(),
        ollama_reachable=_ollama_reachable,
        ollama_start=lambda: start_server(),
        ollama_wait_ready=lambda handle: wait_ready(),
        ollama_stop=lambda handle: stop_server(handle),
    )


# --------------------------------------------------------------------------
# Read-only progress derivation (from the product's own state)
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


def _manifest_for(state: StateRoot, entry: ProcessedEntry) -> JobManifest | None:
    if not entry.artifacts:
        return None
    path = state.root / entry.artifacts[0] / "meta.json"
    if not path.is_file():
        return None
    try:
        return JobManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def derive_item_stages(state: StateRoot, video_id: str) -> dict[str, str]:
    """Per-stage state: ``complete``, a recorded non-complete outcome, or
    ``pending``. Derived ONLY from Processed + meta.json — no second
    ledger. A recorded non-complete outcome is shown as-is and treated
    as pending work for a new invocation."""
    stages = {name: "pending" for name in GUIDED_STAGES}
    entry = Processed(state).get(video_id)
    if entry is None:
        return stages
    manifest = _manifest_for(state, entry)
    if manifest is None:
        return stages
    extraction_outcome = (manifest.extraction or {}).get("outcome")
    if extraction_outcome == "complete":
        stages["fetch"] = "complete"
    elif extraction_outcome is not None:
        stages["fetch"] = str(extraction_outcome)
    for stage in ("prepare", "vision", "audio", "synthesis", "verify"):
        record = manifest.stages.get(stage)
        if record is not None:
            stages[stage] = record.result.outcome.value
    return stages


def _run_dir_of(state: StateRoot, video_id: str) -> str | None:
    entry = Processed(state).get(video_id)
    if entry is None or not entry.artifacts:
        return None
    return str(state.root / entry.artifacts[0])


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


@dataclasses.dataclass
class Selection:
    """The tanda chosen for this invocation plus everything left out."""

    items: list[GuidedItem] = dataclasses.field(default_factory=list)
    excluded: list[dict[str, str]] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [item.video_id for item in self.items]


def _classify_id(
    state: StateRoot,
    video_id: str,
    *,
    scan_kind: str | None,
) -> tuple[str, str]:
    """Fresh classification of ONE id against CURRENT durable state."""
    if Blocklist(state).is_blocked(video_id):
        return (
            "rejected",
            "permanently blocklisted; no flag can override an operator rejection",
        )
    entry = Processed(state).get(video_id)
    if entry is not None and "emit" in entry.stage_fingerprints:
        return (
            "processed_complete",
            "emit fingerprint present; already complete — omitted, no implicit "
            "re-inference",
        )
    if scan_kind == "photo":
        return (
            "unsupported_photo",
            "photo posts are reported, not ingested in this MVP",
        )
    if entry is not None:
        return (
            "partial_resumable",
            "processed entry without an emit fingerprint; valid completed "
            "stages resume with zero re-inference",
        )
    return (
        "new_processable",
        "no processed or blocklist record; eligible for a separately authorized run",
    )


def select_from_plan(state: StateRoot, plan: Any, *, limit: int) -> Selection:
    """Plan-driven selection: NEW processable items, capped at ``limit``.

    The plan passed here must be computed FRESH from persisted scan
    state (this module always recomputes it; a previously printed plan
    document is never authority). Partial-resumable items are reachable
    through explicit ``--ids``.
    """
    selection = Selection()
    candidates: list[GuidedItem] = []
    for item in plan.items:
        classification = item.classification
        if classification == "new_processable":
            candidates.append(
                GuidedItem(
                    video_id=item.stable_id,
                    canonical_url=item.canonical_url,
                    classification=classification,
                    stages=derive_item_stages(state, item.stable_id),
                    run_dir=_run_dir_of(state, item.stable_id),
                )
            )
        else:
            selection.excluded.append(
                {
                    "id": item.stable_id,
                    "classification": classification,
                    "reason": item.detail,
                }
            )
    remaining = max(0, len(candidates) - limit)
    selection.items = candidates[:limit]
    if remaining:
        selection.notes.append(
            f"{remaining} further new-processable item(s) stay pending for a "
            "later batch (batch cap " + str(limit) + ")"
        )
    return selection


def select_ids(
    state: StateRoot,
    ids: Sequence[str],
    *,
    scan_items: Mapping[str, tuple[str, str | None]] | None = None,
    inventory_urls: Mapping[str, str] | None = None,
    limit: int,
) -> Selection:
    """Explicit-ID selection validated against CURRENT durable state.

    Unknown or duplicated ids are hard errors (never guesses). Blocked,
    photo and already-complete ids are excluded with the reason. New and
    partial-resumable ids are selected, capped at ``limit`` (an explicit
    selection above the cap is an error, not a silent truncation).
    """
    scan_items = scan_items or {}
    inventory_urls = inventory_urls or {}
    selection = Selection()
    seen: set[str] = set()
    unknown: list[str] = []
    selectable: list[GuidedItem] = []

    for raw in ids:
        video_id = raw.strip()
        if not video_id:
            raise GuidedError("empty video id in --ids")
        if video_id in seen:
            raise GuidedError(
                f"ambiguous selection: id {video_id} appears more than once"
            )
        seen.add(video_id)
        if (
            video_id not in scan_items
            and video_id not in inventory_urls
            and Processed(state).get(video_id) is None
        ):
            unknown.append(video_id)
            continue
        kind, url = scan_items.get(video_id, (None, None))
        if url is None:
            url = inventory_urls.get(video_id)
        classification, reason = _classify_id(state, video_id, scan_kind=kind)
        if classification in ("rejected", "processed_complete", "unsupported_photo"):
            selection.excluded.append(
                {"id": video_id, "classification": classification, "reason": reason}
            )
            continue
        selectable.append(
            GuidedItem(
                video_id=video_id,
                canonical_url=url,
                classification=classification,
                stages=derive_item_stages(state, video_id),
                run_dir=_run_dir_of(state, video_id),
            )
        )
    if unknown:
        raise GuidedError(
            "unknown video id(s): "
            + ", ".join(unknown)
            + "; ids must exist in the collection scan, the inventory, or the "
            "processed store"
        )
    if len(selectable) > limit:
        raise GuidedError(
            f"explicit selection of {len(selectable)} processable id(s) exceeds "
            f"the batch cap of {limit}; split the tanda into separate invocations"
        )
    selection.items = selectable
    return selection


# --------------------------------------------------------------------------
# The guided run
# --------------------------------------------------------------------------


class GuidedRun:
    """One guided invocation: selection display + consent-gated execution."""

    def __init__(
        self,
        state: StateRoot,
        *,
        stages: StageFunctions | None = None,
        consent: ConsentProvider | None = None,
        out: Callable[[str], None] | None = None,
        auto_synthesizer: Callable[[str, StateRoot, Callable[[ConsentWindow], bool]], Any]
        | None = None,
    ) -> None:
        # Progress narration defaults to stderr; the JSON report is the
        # only stdout payload.
        self.state = state
        self.stages = stages or default_stage_functions()
        self.consent = consent or ConsoleConsentProvider()
        self.out = out or (lambda text: print(text, file=sys.stderr))
        # Optional Phase 0 hook: called (video_id, state, ask) instead of
        # reporting waiting_for_synthesis when no document was supplied.
        # The provider implementation owns its own consent window (asked
        # through the run's logger) and MUST route the result through the
        # existing validated-document ingestion stage.
        self.auto_synthesizer = auto_synthesizer
        self.consent_log: list[dict[str, Any]] = []
        self.stage_calls: list[str] = []
        self.failures: list[dict[str, str]] = []
        self.cleanup_notes: list[str] = []
        self._interrupted = False
        self._gpu_cleanup_failed = False
        # Whisper coordination state (Phase 0 hooks; None when not used).
        self._whisper_we_stopped: Any = None
        self.whisper_restore_failed = False
        # Standing-restore bookkeeping: the restore/recovery authorization
        # is asked BEFORE the stop (a Ctrl-C never grants anything, and a
        # fresh prompt may be unavailable after an interrupt), so recovery
        # runs under that prior authorization — never re-asked, never
        # retried, and always reported honestly.
        self._whisper_restore_authorized = False
        self._whisper_restore_attempted = False
        self._whisper_restore_completed = False
        # Set when an interruption leaves a cleanup outcome UNKNOWN (for
        # example a Ctrl-C landing inside the whisper stop itself): the
        # generic interruption message must then NOT claim that the
        # applicable cleanup ran.
        self._interrupt_cleanup_uncertain = False

    # -- consent ------------------------------------------------------------

    def _ask(self, window: ConsentWindow) -> bool:
        granted = self.consent.ask(window)
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
                self.out(f"vision blocked before any model load: {exc}")
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
                stop(info)
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
                self.out(reason)
                raise
            except Exception as stop_exc:  # noqa: BLE001 - surfaced
                reason = (
                    f"whisper stop failed: {stop_exc}; the service state "
                    "must be verified explicitly before any GPU work; no "
                    "rollback is performed or implied"
                )
                self._block_vision_all(pending, report_items, reason)
                return False
            self._whisper_we_stopped = info
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
        self.out(f"vision blocked before any model load: {reason}")

    def _whisper_pre_audio(self, selection: Selection, report_items: dict) -> bool:
        """Restore the whisper service THIS run stopped, before audio.

        Runs after the vision window (and the isolated-Ollama stop), on
        success AND failure paths of the batch. The restore executes
        under the STANDING authorization obtained BEFORE the stop (the
        separate ``whisper-restore`` window): it is never re-asked and
        never inferred. Returns True when audio may proceed (including
        when we never stopped anything).
        """
        if self._whisper_we_stopped is None:
            return True
        return self._whisper_restore_now(
            report_items, context="after the vision window"
        )

    def _whisper_restore_now(self, report_items: dict, *, context: str) -> bool:
        """Restore under the standing authorization; report honestly.

        Performs the bounded start of the SAME container, positive
        identity/config verification (inside the hook) and a health
        check. Any failure — or an interruption that leaves the restore
        incomplete — blocks ALL further progress (audio included): the
        run reports the actual partial service state with instructions
        and promises no rollback. Never re-asked, never retried.
        """
        info = self._whisper_we_stopped
        assert info is not None  # narrowing: callers check before calling
        service_text = getattr(info, "describe", lambda: str(info))()
        restore = self.stages.whisper_restore
        self._whisper_restore_attempted = True
        try:
            assert restore is not None  # narrowing: hooks are all-or-nothing
            restore(info)
        except KeyboardInterrupt:
            # The interruption itself is never granted anything; the
            # restore outcome is UNKNOWN and no retry happens.
            self.whisper_restore_failed = True
            reason = (
                f"whisper restore was interrupted {context}: the service "
                f"stopped by this run ({service_text}) is in an UNKNOWN "
                "state (the start may or may not have completed) — verify "
                "it explicitly (podman inspect/ps, health endpoint); ALL "
                "further progress is blocked; no rollback is performed or "
                "implied"
            )
            self.failures.append(
                {"id": "*", "stage": "whisper-restore", "outcome": "failed", "reason": reason}
            )
            self.cleanup_notes.append(reason)
            self.out(reason)
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
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
            self.out(reason)
            return False
        healthy, detail = self.stages.whisper_health()
        if not healthy:
            self.whisper_restore_failed = True
            reason = (
                f"restored whisper service failed its health check "
                f"({detail}); the service {service_text} was started but "
                "is NOT healthy — ALL further progress is blocked; verify "
                "the service state explicitly; no rollback is performed "
                "or implied"
            )
            self.failures.append(
                {"id": "*", "stage": "whisper-restore", "outcome": "failed", "reason": reason}
            )
            self.cleanup_notes.append(reason)
            self.out(reason)
            return False
        self._whisper_restore_completed = True
        self.cleanup_notes.append(
            f"shared whisper service restored and healthy {context} "
            f"({detail[:120]})"
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
        self.out(
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
                self.out(f"vision blocked before any model load: {exc}")
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
                    self.out(
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
        self, selection: Selection, report_items: dict, vision_complete: list[str]
    ) -> None:
        pending = [
            item for item in selection.items
            if item.video_id in vision_complete
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
            self.out(f"audio blocked: {reason}")
            return
        for item in pending:
            self.stage_calls.append(f"audio:{item.video_id}")
            outcome = self.stages.audio(item.video_id, self.state)
            self._record(report_items, item, "audio", outcome)

    def _gpu_phase(self, selection: Selection, report_items: dict) -> None:
        vision_pending = [
            item for item in selection.items
            if self._item_stage_state(item, "prepare") == "complete"
            and self._item_stage_state(item, "vision") != "complete"
        ]
        audio_pending = [
            item for item in selection.items
            if self._item_stage_state(item, "vision") == "complete"
            and self._item_stage_state(item, "audio") != "complete"
        ]
        # The window names every id that COULD reach audio this invocation
        # (an id whose vision completes inside this window follows straight
        # into the audio phase); consent must name the superset, not just
        # the ids whose vision was already complete.
        audio_possible = [
            item for item in selection.items
            if self._item_stage_state(item, "prepare") == "complete"
            and self._item_stage_state(item, "audio") != "complete"
        ]
        if not vision_pending and not audio_pending:
            return
        window_ids: list[str] = [
            item.video_id for item in vision_pending
        ]
        for item in audio_possible:
            if item.video_id not in window_ids:
                window_ids.append(item.video_id)
        window = ConsentWindow(
            kind="gpu",
            title="local GPU inference window (vision and/or audio)",
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
                (
                    "full-audio transcription through the shared whisper WS "
                    "service per pending id"
                    if audio_pending
                    else "no audio transcription (none pending)"
                ),
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
                "whisper must be STOPPED for vision and RUNNING for audio — "
                "both are operator actions; this command never manages that "
                "service",
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
            self._audio_phase(selection, report_items, vision_complete)
        except KeyboardInterrupt:
            # An interrupt never grants anything and never skips recovery.
            # The isolated Ollama server this run started was already
            # stopped by _vision_phase's finally; the whisper service this
            # run stopped is recovered here ONLY under the standing
            # authorization obtained BEFORE the stop — never re-asked
            # (a Ctrl-C must not depend on a fresh prompt), never retried.
            self.out(
                "interrupted during the GPU window: the isolated Ollama "
                "server this run started was stopped by the window "
                "cleanup; the whisper service this run stopped (if any) "
                "is recovered next under its prior standing authorization"
            )
            if self._whisper_we_stopped is not None:
                if not self._whisper_restore_attempted:
                    self._whisper_restore_now(
                        report_items, context="after interruption (Ctrl-C)"
                    )
                elif not self._whisper_restore_completed:
                    # A restore was already in flight when the interrupt
                    # landed: _whisper_restore_now's own handler already
                    # reported the UNKNOWN state and appended the failure
                    # record. Keep the blocking invariant (idempotent),
                    # with NO retry, NO new ask and NO duplicate report.
                    self.whisper_restore_failed = True
            raise

    def _synthesis_and_verify_phase(
        self,
        selection: Selection,
        report_items: dict,
        synthesis_docs: Mapping[str, Path],
    ) -> None:
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
                run_dir = item.run_dir or _run_dir_of(self.state, item.video_id)
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
            self.out(
                f"waiting for synthesis document for {entry['id']}: evidence at "
                f"{entry['video_md']} and {entry['audio_md']}; {entry['resume']}"
            )

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
            run_dir = _run_dir_of(self.state, item.video_id)
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
                self.out(
                    "interrupted: partial results are preserved by the "
                    "stages themselves; cleanup state is NOT fully known "
                    "— see the UNKNOWN whisper-stop note above, verify "
                    "and recover the exact service manually before any "
                    "further GPU work; re-invocation needs new "
                    "authorization for pending operations"
                )
            else:
                self.out(
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


# --------------------------------------------------------------------------
# CLI-facing entry point
# --------------------------------------------------------------------------


def parse_synthesis_args(raw: Sequence[str]) -> dict[str, Path]:
    """Parse repeatable ``ID=PATH`` arguments into a mapping."""
    docs: dict[str, Path] = {}
    for item in raw:
        if "=" not in item:
            raise GuidedError(
                f"malformed --synthesis {item!r}: expected ID=PATH"
            )
        video_id, _, path = item.partition("=")
        video_id = video_id.strip()
        path = path.strip()
        if not video_id or not path:
            raise GuidedError(
                f"malformed --synthesis {item!r}: both ID and PATH are required"
            )
        if video_id in docs:
            raise GuidedError(f"duplicate --synthesis for id {video_id}")
        docs[video_id] = Path(path)
    return docs


def _inventory_urls(state: StateRoot) -> dict[str, str]:
    from .state import InventoryStore

    return {
        entry.stable_id: entry.canonical_url
        for entry in InventoryStore(state).load()
    }


def run_guided(
    *,
    state: StateRoot,
    collection: str | None = None,
    ids: Sequence[str] | None = None,
    limit: int | None = None,
    synthesis: Mapping[str, Path] | None = None,
    dry_run: bool = False,
    stages: StageFunctions | None = None,
    consent: ConsentProvider | None = None,
    out: Callable[[str], None] | None = None,
    auto_synthesizer: Callable[[str, StateRoot, Callable[[ConsentWindow], bool]], Any]
    | None = None,
) -> tuple[int, dict[str, Any]]:
    """Select a tanda, then plan-only (dry run) or consent-gated execute.

    Selection is ALWAYS computed fresh from persisted scan state and the
    CURRENT blocklist/processed stores: a previously printed plan is not
    authority for this invocation.
    """
    if collection is None and not ids:
        raise GuidedError("guided-run requires --collection and/or --ids")
    effective_limit = limit if limit is not None else GUIDED_MAX_BATCH
    if not 1 <= effective_limit <= GUIDED_MAX_BATCH:
        raise GuidedError(
            f"--limit must be between 1 and the batch cap {GUIDED_MAX_BATCH}"
        )
    synthesis_docs = dict(synthesis or {})

    scan_items: dict[str, tuple[str, str | None]] = {}
    selection: Selection
    if collection is not None:
        ref, scan = resolve_collection(state, collection)
        if scan is None:
            from .collection import CollectionError

            raise CollectionError(
                f"no persisted scan state for {ref.collection_url}"
            )
        if ids:
            scan_items = {
                item.stable_id: (item.kind, item.canonical_url)
                for item in scan.items
            }
            selection = select_ids(
                state,
                ids,
                scan_items=scan_items,
                inventory_urls=_inventory_urls(state),
                limit=effective_limit,
            )
        else:
            plan = build_collection_plan(state, scan)
            selection = select_from_plan(state, plan, limit=effective_limit)
    else:
        selection = select_ids(
            state,
            ids or (),
            inventory_urls=_inventory_urls(state),
            limit=effective_limit,
        )

    for video_id in synthesis_docs:
        if video_id not in selection.ids:
            raise GuidedError(
                f"--synthesis id {video_id} is not part of the selected tanda"
            )

    runner = GuidedRun(
        state, stages=stages, consent=consent, out=out,
        auto_synthesizer=auto_synthesizer,
    )
    if dry_run:
        report = runner.build_report(selection, {}, mode="dry-run")
        report.pop("consent_log")
        report.pop("stage_outcomes_this_run")
        report["notes"].append(
            "dry run: zero consent prompts, zero writes, zero network/GPU work"
        )
        return 0, report

    report = runner.execute(selection, synthesis_docs=synthesis_docs)
    exit_code = 1 if (report["failures"] or report["interrupted"]) else 0
    return exit_code, report


__all__ = [
    "ConsentProvider",
    "ConsentWindow",
    "ConsoleConsentProvider",
    "GuidedError",
    "GuidedItem",
    "GuidedRun",
    "GUIDED_MAX_BATCH",
    "GUIDED_STAGES",
    "Selection",
    "StageFunctions",
    "default_stage_functions",
    "derive_item_stages",
    "parse_synthesis_args",
    "run_guided",
    "select_from_plan",
    "select_ids",
]
