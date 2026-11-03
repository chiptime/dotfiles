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
  no second stage ledger and persists no stage state: its report is a
  summary, never an authority parallel to the manifests. The ONE
  persisted artifact is the GPU lease marker (see
  :mod:`tiktok_ingest.gpu_lease`), lifecycle recovery state for the
  whisper swap — never stage progress.
- ``synthesize --from-file`` is validated-document INGESTION only. The
  guided command never generates, edits or auto-fills a synthesis
  document and never calls a text model.
- Stage outcome ``complete`` is NOT claim verdict ``confirmed``. The
  report keeps the two axes separate; verdicts live in
  ``verification.json`` and the emitted backlog entry.
- A fired gate is final: no automatic retry, no continued GPU load. A
  new invocation is a new authorization window (GateLedger semantics).
- Whisper is a shared service this coordinator only manages through
  the optional lifecycle hooks (identity-verified, bounded stop before
  vision, verified restore under a standing authorization asked BEFORE
  the stop). When those hooks are absent the documented standalone
  behavior holds: the read-only stopped-check passes or vision is
  blocked with the operator instruction. The ``guided-run`` CLI always
  supplies the hooks (:func:`whisper_coordinated_stage_functions`), so
  a running KNOWN service is coordinated automatically; programmatic
  callers keep the operator-managed default.
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
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import config
from . import gpu_lease
from .collection import (
    build_collection_plan,
    resolve_collection,
)
from .contracts import JobManifest, ProcessedEntry
from .engine import (  # noqa: F401 - re-exported seam (S2 single home)
    GUIDED_MAX_BATCH,
    ConsentWindow,
    GuidedError,
    GuidedItem,
    Selection,
    StageEngine,
    StageFunctions,
    run_dir_of,
)
from .gpu_lease import (  # noqa: F401 - re-exported bounds (S2 single home)
    WHISPER_RESTORE_READY_DEADLINE_SECONDS,
    WHISPER_RESTORE_READY_POLL_INTERVAL_SECONDS,
)
from .ollama_runtime import start_server, stop_server, wait_ready
from .fetch import fetch_url
from .prepare import prepare_video
from .runtime import Runner, subprocess_runner
from .state import Blocklist, Processed, StateRoot
from .synthesis import run_synthesis_stage
from .verify import run_verify_stage
from .vision import assert_whisper_stopped, query_gpu, run_vision_stage
from .whisper_client import health_precheck, run_audio_stage
from .whisper_lifecycle import inspect_whisper, restore_whisper, stop_whisper

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

# Bounded read-only readiness wait after the ONE authorized whisper
# restore. The bounds' single home is :mod:`tiktok_ingest.gpu_lease`
# (the ONE shared wait, S2); they are re-exported here because callers
# and tests reference them through this module.


# --------------------------------------------------------------------------
# Consent windows
# --------------------------------------------------------------------------


# Every label in a rendered consent block is padded to this width so the
# aligned colon column reads as a single table.
CONSENT_LABEL_WIDTH = len("destinations")


def render_consent_window(window: ConsentWindow) -> str:
    """The aligned, multi-line consent block shown BEFORE any effect.

    The first line is the window's one-line intent (its ``title``); the
    existing ids/destinations/operations/limits/effects follow as
    aligned, indented lists. Display-only: it grants nothing and carries
    no authority. Keeping it a pure function of the window makes the
    prompt deterministic and testable independently of the console.
    """

    def label(name: str) -> str:
        return name.ljust(CONSENT_LABEL_WIDTH)

    lines = ["== AUTHORIZATION REQUIRED ==", f"{label('intent')}: {window.title}"]
    if window.ids:
        lines.append(f"{label('ids')}: " + ", ".join(window.ids))

    def section(name: str, values: Sequence[str]) -> None:
        lines.append(f"{label(name)}:")
        lines.extend(f"    - {value}" for value in values)

    if window.destinations:
        section("destinations", window.destinations)
    if window.operations:
        section("operations", window.operations)
    if window.limits:
        section("limits", window.limits)
    if window.effects:
        section("effects", window.effects)
    return "\n".join(lines)


class ConsentProvider:
    """Authorization source. ``ask`` returns True ONLY on explicit grant.

    ``await_readiness`` is a SEPARATE press-Enter readiness signal (not
    an authorization): the default implementation fails closed because a
    provider without an interactive readiness channel can never confirm
    operator readiness. The standalone guided coordinator never calls
    it; only the browser inventory boundary uses it.
    """

    def ask(self, window: ConsentWindow) -> bool:  # pragma: no cover - iface
        raise NotImplementedError

    def await_readiness(self, prompt: str) -> bool:
        """Refuse by default: readiness is never assumed. NOT a grant."""
        return False


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
        out: Callable[..., None] | None = None,
    ) -> None:
        self._stdin = stdin
        # Prompts are interactive UI: stderr keeps stdout parseable.
        # ``end`` keeps the yes/no answer on the prompt line; an injected
        # ``out`` must accept the same keyword.
        self._out = out or (
            lambda text, end="\n": print(text, end=end, file=sys.stderr, flush=True)
        )

    def ask(self, window: ConsentWindow) -> bool:
        self._out(render_consent_window(window))
        stream = self._stdin if self._stdin is not None else sys.stdin
        if self._stdin is None and not stream.isatty():
            self._out(
                "refused: non-interactive environment; consent is never "
                "assumed — nothing ran (re-run from a terminal or use "
                "--dry-run)"
            )
            return False
        try:
            self._out("authorize this window? [yes/no]", end=" ")
            answer = stream.readline()
        except OSError as exc:
            self._out(
                f"refused: could not read authorization ({exc}); nothing "
                "ran and consent is never assumed"
            )
            return False
        if not answer:
            self._out(
                'refused: end of input; consent is never assumed — nothing '
                'ran for "' + window.title + '"'
            )
            return False
        granted = answer.strip().lower() in ("yes", "y")
        if granted:
            self._out("granted")
        else:
            self._out(
                'denied: nothing ran for "' + window.title + '" — stopping '
                "before this window"
            )
        return granted

    def await_readiness(self, prompt: str) -> bool:
        """Press-Enter readiness checkpoint; NOT a yes/no authorization.

        Any input line (typically just Enter) signals that the operator
        has finished the manual steps named in the prompt. EOF, a
        non-interactive stdin or a read error refuses: readiness is
        never assumed. The signal carries NO authority of its own — the
        effects that follow were already named by the prior browser
        authorization window.
        """
        self._out(prompt, end=" ")
        stream = self._stdin if self._stdin is not None else sys.stdin
        if self._stdin is None and not stream.isatty():
            self._out(
                "refused: non-interactive environment; readiness cannot "
                "be signaled (re-run from a terminal)"
            )
            return False
        try:
            answer = stream.readline()
        except OSError as exc:
            self._out(f"refused: could not read readiness ({exc})")
            return False
        if not answer:
            self._out("refused: end of input; readiness is never assumed")
            return False
        self._out("readiness signaled")
        return True


class _GuidedRunScopedConsent(ConsentProvider):
    """Consent over the ONE granted run plan for a guided-run invocation.

    Every phase window is discharged by the underlying
    ``RunScopedAuthorization`` when it is covered by the exact scope named
    in the single run-plan window (``tanda``/``prepare``/``fetch``/``gpu``/
    ``whisper-stop``/``whisper-restore`` and, when the text API endpoint and
    model are named, ``synthesis-api``). Anything outside that scope is
    forwarded to the human as genuinely new scope — it is never
    auto-granted.

    ``verify`` is deliberately NOT part of the run plan: candidate URLs only
    exist after synthesis, so the grouped verification window always reaches
    the human as its own decision, exactly as before.
    """

    def __init__(self, *, human: ConsentProvider, authorization: Any) -> None:
        self.human = human
        self.authorization = authorization

    def ask(self, window: ConsentWindow) -> bool:
        if window.kind == "verify":
            if getattr(self.authorization, "allow_verify", False):
                return self.authorization.ask(window)
            return self.human.ask(window)
        return self.authorization.ask(window)

    def await_readiness(self, prompt: str) -> bool:
        return self.human.await_readiness(prompt)


# --------------------------------------------------------------------------
# Injectable stage boundary (production defaults call the real stages)
# --------------------------------------------------------------------------
# StageFunctions itself lives in tiktok_ingest.engine (the S2 execution
# seam); the production default constructors below stay here because
# they wire the real pipeline functions (and the test seams that patch
# ``guided.subprocess_runner``) into that boundary.


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


def _wait_for_whisper_health(
    health: Callable[[], tuple[bool, str]],
    *,
    deadline_seconds: float = WHISPER_RESTORE_READY_DEADLINE_SECONDS,
    poll_interval_seconds: float = WHISPER_RESTORE_READY_POLL_INTERVAL_SECONDS,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    waiting_feedback: Callable[[float], None] | None = None,
) -> tuple[bool, str]:
    """Thin delegation to the ONE shared readiness wait (S2).

    The implementation lives in
    :func:`tiktok_ingest.gpu_lease.wait_for_whisper_health` (identical
    bounds: immediate first probe, one bounded interval between probes,
    60 s deadline, ``KeyboardInterrupt`` never caught). This name stays
    because tests patch ``guided.time`` for the deterministic clock: the
    clock defaults are resolved HERE, in this module's namespace, so
    that seam keeps working unchanged.
    """
    return gpu_lease.wait_for_whisper_health(
        health,
        deadline_seconds=deadline_seconds,
        poll_interval_seconds=poll_interval_seconds,
        monotonic=monotonic if monotonic is not None else time.monotonic,
        sleep=sleep if sleep is not None else time.sleep,
        waiting_feedback=waiting_feedback,
    )


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
        whisper_stopped=lambda: assert_whisper_stopped(subprocess_runner),
        whisper_health=lambda: health_precheck(),
        ollama_reachable=_ollama_reachable,
        ollama_start=lambda: start_server(),
        ollama_wait_ready=lambda handle: wait_ready(),
        ollama_stop=lambda handle: stop_server(handle),
        gpu_free_mib=lambda: query_gpu(subprocess_runner).free_mib,
    )


def whisper_coordinated_stage_functions(
    *, runner: Runner = subprocess_runner
) -> StageFunctions:
    """Production stages WITH the Phase 0 whisper lifecycle hooks.

    This is what the ``guided-run`` CLI uses so it coordinates the KNOWN
    shared whisper service exactly like ``collection-run`` already does:
    when vision needs the GPU and that service is running, its identity
    is verified read-only, the bounded stop runs only behind its TWO
    explicit consent windows (``whisper-stop`` plus the STANDING
    ``whisper-restore`` authorization asked BEFORE the stop), and the
    same service is restored and health-checked afterwards — on success,
    failure and interruption alike, with UNKNOWN semantics when the stop
    or restore itself is interrupted. It adds NO new authority: the
    hooks are the existing :mod:`tiktok_ingest.whisper_lifecycle`
    functions and the coordinator keeps its fail-closed rules. The
    programmatic default (:func:`default_stage_functions`) stays
    operator-managed for callers that do not opt in.
    """
    return dataclasses.replace(
        default_stage_functions(),
        whisper_inspect=lambda: inspect_whisper(runner),
        whisper_stop=lambda info: stop_whisper(info, runner=runner),
        whisper_restore=lambda info: restore_whisper(info, runner=runner),
    )


# The stages narrated around each real call when ``guided-run`` opts into
# per-stage progress (start line, finish line with the ACTUAL outcome and
# measured duration). Synthesis is included: the validated-document
# ingestion is a real stage call in the standalone guided flow.
NARRATED_STAGES: tuple[str, ...] = (
    "fetch",
    "prepare",
    "vision",
    "audio",
    "synthesize",
    "verify",
)


def narrate_stages(
    stages: StageFunctions,
    out: Callable[[str], None],
    *,
    monotonic: Callable[[], float] | None = None,
    names: tuple[str, ...] = NARRATED_STAGES,
) -> StageFunctions:
    """Wrap each stage call with stderr-only start/finish progress.

    A start line before the call and a finish line on return with the
    ACTUAL outcome, the reuse flag and the measured duration; a raised
    exception is narrated with its type and re-raised unchanged. Long
    calls stay silent between their start and finish lines — no heartbeat
    or percentage is ever fabricated. Only narration is added: the stage
    semantics, gates, fingerprints and results are untouched. ``names``
    selects which stages are wrapped, so the Phase 0 application layer
    can narrate its own stage set (the synthesis boundary narrates
    itself) while ``guided-run`` narrates every stage.
    """
    now = monotonic or time.monotonic

    def wrap(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def narrated(first: str, state: StateRoot, *rest: Any) -> Any:
            # fetch receives the canonical URL; its last path segment is
            # the video id. Every other stage receives the id directly.
            label = first.rsplit("/", 1)[-1] if name == "fetch" else first
            out(f"stage {name} start: {label}")
            started = now()
            try:
                outcome = fn(first, state, *rest)
            except BaseException as exc:
                out(
                    f"stage {name} finish: {label} raised "
                    f"{type(exc).__name__} after {now() - started:.1f}s"
                )
                raise
            value = getattr(outcome, "outcome", outcome)
            reused = bool(getattr(outcome, "reused", False))
            out(
                f"stage {name} finish: {label} {value} "
                f"(reused={reused}, {now() - started:.1f}s)"
            )
            return outcome

        return narrated

    replacements = {
        name: wrap(name, getattr(stages, name)) for name in names
    }
    return dataclasses.replace(stages, **replacements)


# --------------------------------------------------------------------------
# Read-only progress derivation (from the product's own state)
# --------------------------------------------------------------------------
# GuidedItem itself lives in tiktok_ingest.engine (the S2 execution
# seam); this section keeps the derivation of per-item stage state from
# the product's own durable state.


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


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------
# Selection lives in tiktok_ingest.engine (items + phase in, outcomes
# out); the selection COMPUTATION against durable state stays here.


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
                    run_dir=run_dir_of(state, item.stable_id),
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
                run_dir=run_dir_of(state, video_id),
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
    """One guided invocation: consent adapters wired into the engine.

    Thin driver (S2): stage execution lives in
    :class:`tiktok_ingest.engine.StageEngine`; this class keeps the
    historical guided-run surface — the constructor signature, ``out``
    narration, ``execute``/``build_report`` and the collection phase
    hook — and wires the operator-facing adapters into the engine:
    ``consent.ask`` decides authorization (``approve``), ``out``
    narrates (``notify``). Messages, windows, ordering and reports are
    byte-identical to the pre-extraction coordinator (S4).
    """

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
        self._engine = StageEngine(
            state,
            stages=self.stages,
            approve=self.consent.ask,
            notify=self.out,
            auto_synthesizer=auto_synthesizer,
        )

    # -- public engine seam ---------------------------------------------------

    @property
    def engine(self) -> StageEngine:
        """The wired stage engine (S3): drivers drive phases through it
        (``runner.engine.execute_collection_phase``) instead of private
        driver members."""
        return self._engine

    # -- consent ------------------------------------------------------------

    def _ask(self, window: ConsentWindow) -> bool:
        return self._engine._ask(window)  # noqa: SLF001 - kept seam

    # -- entry points ---------------------------------------------------------

    def _execute_collection_phase(
        self, selection: Selection, phase: str
    ) -> dict[str, Any]:
        """One private resource slice; the collection driver owns all barriers.

        Each call uses a fresh runner so standing restore authority and its
        consumed start attempt can never leak across actual stop cycles.
        This does not change the public guided-run selection or phase order.
        """
        return self._engine.execute_collection_phase(selection, phase)

    def execute(
        self,
        selection: Selection,
        *,
        synthesis_docs: Mapping[str, Path] | None = None,
    ) -> dict[str, Any]:
        """Drive the selected tanda through consent-gated phases."""
        return self._engine.execute(selection, synthesis_docs=synthesis_docs)

    def build_report(
        self, selection: Selection, report_items: dict[str, Any], *, mode: str
    ) -> dict[str, Any]:
        return self._engine.build_report(selection, report_items, mode=mode)


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
    auto_verify: bool = False,
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

    out_fn = out if out is not None else (
        lambda text: print(text, file=sys.stderr)
    )
    if dry_run:
        runner = GuidedRun(
            state, stages=stages, consent=consent, out=out,
            auto_synthesizer=auto_synthesizer,
        )
        report = runner.build_report(selection, {}, mode="dry-run")
        report.pop("consent_log")
        report.pop("stage_outcomes_this_run")
        report["notes"].append(
            "dry run: zero consent prompts, zero writes, zero network/GPU work"
        )
        return 0, report

    human = consent if consent is not None else ConsoleConsentProvider()
    effective_stages = stages if stages is not None else default_stage_functions()
    frozen_ids = tuple(selection.ids)
    reconcile = None

    # The run plan is the ONE upfront authorization for the whole tanda:
    # it freezes the exact ids/URLs/service/endpoint so the single window
    # names them honestly, and the covered per-phase windows are then
    # discharged programmatically instead of prompted one by one. Without
    # any eligible id there is nothing to authorize or run.
    if frozen_ids:
        from .collection_run import (
            RunPlan,
            RunScopedAuthorization,
            _bind_whisper_identity,
            _reconcile_gpu_lease,
            build_run_plan_window,
        )

        reconcile = _reconcile_gpu_lease(state, effective_stages, out_fn)
        if reconcile.blocks:
            raise GuidedError(f"gpu lease reconcile: {reconcile.detail}")
        bound_info, whisper_note = _bind_whisper_identity(
            effective_stages, out_fn
        )
        text_api_origin: str | None = None
        text_api_model: str | None = None
        if auto_synthesizer is not None:
            from .synthesis_api import load_text_api_config

            api_config, _missing = load_text_api_config(os.environ)
            if api_config is not None:
                text_api_origin = api_config.origin()
                text_api_model = api_config.model
        plan_counts: dict[str, int] = {}
        for item in selection.items:
            plan_counts[item.classification] = (
                plan_counts.get(item.classification, 0) + 1
            )
        run_plan = RunPlan(
            collection_url=collection or "",
            frozen_ids=frozen_ids,
            canonical_urls={
                item.video_id: item.canonical_url
                for item in selection.items
                if item.canonical_url
            },
            stage_states={
                item.video_id: derive_item_stages(state, item.video_id)
                for item in selection.items
            },
            batch_cap=effective_limit,
            batch_count=(len(frozen_ids) + effective_limit - 1)
            // effective_limit,
            plan_counts=plan_counts,
            whisper_bound=(
                bound_info.describe() if bound_info is not None else None
            ),
            whisper_note=whisper_note,
            text_api_origin=text_api_origin,
            text_api_model=text_api_model,
            whisper_info=bound_info,
        )
        plan_window = build_run_plan_window(
            run_plan,
            sequence=(
                "process exactly the selected ids through fetch -> prepare "
                "-> vision -> audio -> synthesis ingestion -> verify, one id "
                "at a time, under ONE authorization for the whole tanda; "
                "each id is attempted in exactly ONE tanda"
            ),
            auto_verify=auto_verify,
        )
        if not human.ask(plan_window):
            report = GuidedRun(
                state, stages=effective_stages, consent=human, out=out_fn
            ).build_report(selection, {}, mode="execute")
            report["notes"].append(
                "run-plan confirmation denied: no stage ran and nothing was "
                "written; re-invoke to be asked again"
            )
            report["run_plan"] = {
                "granted": False,
                "window": plan_window.to_dict(),
            }
            if reconcile.action != "none":
                report["gpu_lease_reconcile"] = reconcile.to_dict()
            return 0, report

        authorization = RunScopedAuthorization(
            human=human, plan=run_plan, out=out_fn, allow_verify=auto_verify
        )
        runner_consent: ConsentProvider = _GuidedRunScopedConsent(
            human=human, authorization=authorization
        )
    else:
        authorization = None
        runner_consent = human

    runner = GuidedRun(
        state, stages=effective_stages, consent=runner_consent, out=out_fn,
        auto_synthesizer=auto_synthesizer,
    )
    report = runner.execute(selection, synthesis_docs=synthesis_docs)
    if authorization is not None:
        report["run_plan"] = {
            "granted": True,
            "frozen_ids": list(frozen_ids),
            "canonical_urls": {
                item.video_id: item.canonical_url
                for item in selection.items
                if item.canonical_url
            },
            "whisper": {
                "bound": run_plan.whisper_bound,
                "note": run_plan.whisper_note,
            },
            "text_api": (
                {"origin": text_api_origin, "model": text_api_model}
                if text_api_origin
                else None
            ),
            "stage_windows": authorization.authorization_log,
        }
    if reconcile is not None and reconcile.action != "none":
        report["gpu_lease_reconcile"] = reconcile.to_dict()
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
    "narrate_stages",
    "parse_synthesis_args",
    "render_consent_window",
    "run_guided",
    "select_from_plan",
    "select_ids",
    "whisper_coordinated_stage_functions",
]
