"""Stage engine: the consent-gated stage execution under guided-run.

Extracted from :mod:`tiktok_ingest.guided` (S2): this module owns WHAT
runs — fetch/prepare preparation, the vision/audio model-inference
window, the synthesis ingestion + verify boundary, the per-stage report
records and the run report — with NO terminal I/O and NO authorization
decision of its own. Every operator-facing seam is an injected adapter:

- ``approve(window) -> bool`` — the consent adapter; it DECIDES the
  window (interactive prompt, run-plan scope, test double). Building a
  :class:`ConsentWindow` value is data and happens here; asking is the
  adapter's job.
- ``notify(text)`` — the narration adapter (progress lines). The engine
  never prints.

Every model call goes to the RESIDENT llm-hub services (chat for vision,
whisper WS for audio): the engine loads, unloads, starts and stops
nothing. The honesty rules are unchanged from the guided coordinator
(see :mod:`tiktok_ingest.guided`): progress is derived from the
product's own state, and a failed request is final for its window.
``GuidedRun`` is the thin driver wiring ``consent.ask`` -> ``approve``
and ``out`` -> ``notify``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from . import config
from .contracts import utc_now_iso
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
    stage's own guarantees (fail-closed preconditions, deadlines,
    verified reuse). Both model-serving services are RESIDENT llm-hub
    units: nothing here starts, stops or inspects any container, and no
    model is loaded or unloaded by this pipeline.
    """

    fetch: Callable[[str, StateRoot], Any]
    prepare: Callable[[str, StateRoot], Any]
    vision: Callable[[str, StateRoot], Any]
    audio: Callable[[str, StateRoot], Any]
    synthesize: Callable[[str, StateRoot, Path | None], Any]
    verify: Callable[[str, StateRoot], Any]
    # (healthy, detail) for the resident llm-hub-whisper WS service
    # (read-only health probe; used as the fail-closed precheck before
    # audio work).
    whisper_health: Callable[[], tuple[bool, str]]


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


# --------------------------------------------------------------------------
# The stage engine
# --------------------------------------------------------------------------


class StageEngine:
    """One consent-gated stage execution over a selection.

    Everything of the former ``GuidedRun`` body except the adapters:
    stage execution, per-stage records, interruption bookkeeping and
    report building. Vision and audio are plain requests to the
    resident llm-hub services — there is no service lifecycle, lease or
    GPU coordination to own.
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

    def _vision_phase(self, selection: Selection, report_items: dict) -> list[str]:
        """Run pending vision stages inside one consented window.

        Order and safety come from the stage itself (llm-hub readiness
        precheck, per-stage deadline). This coordinator only adds:
        explicit consent and the stop-on-failure policy. Returns the ids
        whose vision is complete after the phase.
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
        for item in pending:
            self.stage_calls.append(f"vision:{item.video_id}")
            outcome = self.stages.vision(item.video_id, self.state)
            self._record(report_items, item, "vision", outcome)
            if getattr(outcome, "outcome", "") != "complete":
                # Stop-on-failure policy: no further vision requests for
                # ANY id this invocation; completed stages stay reused.
                self._vision_window_closed = True
                self._notify(
                    "vision window ended: no further vision requests this "
                    "invocation (the failure is final for the window)"
                )
                break
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
                f"llm-hub-whisper is not ready for audio ({detail}); start it "
                "with `systemctl --user start llm-hub-whisper`, then re-invoke "
                "guided-run — completed vision stages are reused with zero "
                "re-inference"
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
                "ids above through the resident llm-hub-whisper WS service; no "
                "vision completion is required",
                "no vision requests in this window (audio-only)",
            ),
            limits=(
                f"audio deadline {config.BUDGETS.audio_stage_deadline_seconds}s per id",
                "completed audio is reused, never re-transcribed; no retry",
                "health must pass before work; the service is user-managed "
                "(systemctl --user start llm-hub-whisper) — this command "
                "never starts or stops it",
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
        # vision failure or a denial means no audio for that id. Ids whose
        # audio is already complete are reused, never re-transcribed, and
        # are named as such.
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
                "full-audio transcription through the resident llm-hub-whisper "
                f"WS service for the {len(audio_pending)} vision-complete "
                f"id(s), and CONDITIONALLY for the {len(audio_conditional)} "
                "id(s) whose vision completes inside this window — if an "
                "id's vision fails or the window closes, that id's audio "
                "does not run"
            )
        elif audio_pending:
            audio_operation = (
                "full-audio transcription through the resident llm-hub-whisper "
                "WS service per pending id"
            )
        elif audio_conditional:
            audio_operation = (
                "conditional full-audio transcription through the resident "
                f"llm-hub-whisper WS service for the {len(audio_conditional)} "
                "id(s) whose vision completes inside this window — audio runs "
                "only after that id's vision succeeds; vision failure or "
                "denial means no audio runs"
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
            title=("local vision-only window" if vision_only
                   else "local model-inference window (vision and/or audio)"),
            ids=tuple(window_ids),
            operations=(
                (
                    "vision inference requests to the RESIDENT llm-hub chat "
                    f"service ({config.LLM_HUB_CHAT_API_BASE}, model "
                    f"{config.VISION_MODEL}) per pending id"
                    if vision_pending
                    else "no vision inference (none pending)"
                ),
                audio_operation,
            ),
            limits=(
                f"vision deadline {config.BUDGETS.vision_stage_deadline_seconds}s, "
                f"audio deadline {config.BUDGETS.audio_stage_deadline_seconds}s "
                "per id",
                "no automatic retry; a failed request is final for this "
                "window",
                "the llm-hub services must already be running (user units "
                "llm-hub-chat / llm-hub-whisper); this run never starts, "
                "stops or inspects any container",
            ),
            effects=(
                "per-frame chat-completions requests to the resident llm-hub "
                "chat model and full-audio transcription requests to the "
                "resident llm-hub-whisper service; no model load/unload and "
                "no GPU coordination is performed by this run",
            ),
        )
        if not self._ask(window):
            selection.notes.append(
                "gpu window denied: no inference ran and no service was touched"
            )
            return
        self._notify(
            "model-inference window: vision frames go to the resident "
            "llm-hub chat service and audio to the resident llm-hub-whisper "
            "service; this run starts and stops nothing"
        )
        try:
            vision_complete = self._vision_phase(selection, report_items)
            if not vision_only:
                self._audio_phase(selection, report_items, vision_complete)
        except KeyboardInterrupt:
            self._notify(
                "interrupted during the model-inference window: partial "
                "results are preserved by the stages themselves; "
                "re-invocation needs new authorization for pending operations"
            )
            raise
        except Exception as exc:  # unexpected boundary failure, not a stage outcome
            self._progress_blocked_reason = f"GPU boundary failed: {exc}"
            self.failures.append({
                "id": "*", "stage": "gpu", "outcome": "failed",
                "reason": self._progress_blocked_reason,
            })

    def _synthesis_and_verify_phase(
        self,
        selection: Selection,
        report_items: dict,
        synthesis_docs: Mapping[str, Path],
        *,
        verify: bool = True,
    ) -> None:
        if self._progress_blocked_reason is not None:
            selection.notes.append(
                "synthesis and verification blocked: an earlier phase failed "
                "at its boundary"
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
            selection.notes.append(
                "phase interrupted: partial stage evidence preserved; "
                "re-invocation needs new authorization for pending operations"
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
            self._notify(
                "interrupted: partial results are preserved by the stages "
                "themselves; re-invocation needs new authorization for "
                "pending operations"
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
