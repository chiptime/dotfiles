"""``collection-run``: one command from a collection URL to pending entries.

Phase 0 application layer (ROADMAP work packages 0.1-0.4). This module
COORDINATES; it implements no stage logic of its own:

- The browser inventory comes from :mod:`tiktok_ingest.browser`
  (headed, run-scoped profile, explicit operator authorization,
  existing collection contracts).
- Each batch is driven by the EXISTING :func:`guided.run_guided`
  coordinator with its ``GuidedRun``/``StageFunctions`` seams — there
  is no second stage ledger, no copied stage logic, and every gate,
  fingerprint and cleanup rule of the guided coordinator applies
  unchanged.
- Automatic synthesis goes through ONE OpenAI-compatible adapter
  (:mod:`tiktok_ingest.synthesis_api`) whose result is ALWAYS routed
  through the existing validated-document ingestion stage
  (``run_synthesis_stage``): the ``SynthesisDocument`` contract, the
  credential scrub and the resume fingerprint stay authoritative.
- The KNOWN shared whisper service is coordinated only through the
  guided coordinator's optional lifecycle hooks
  (:mod:`tiktok_ingest.whisper_lifecycle`): identity-verified,
  consented, bounded stop before vision and verified restore + health
  before audio, after every affected batch including failure paths.

Authorization contract (user-approved 2026-09-30 — TWO decisions plus
ONE grouped verification, no routine per-stage prompts):

1. ONE browser grant opens the headed browser for the exact
   collection and names the isolated profile, the audit HTML write and
   the scan/inventory merge. After the operator logs in by hand, a
   press-Enter READINESS checkpoint (not a yes/no authorization)
   signals visibility; the capture then runs under grant 1.
2. ONE whole-current-run plan grant covers the FROZEN inventory
   computed after the capture: the exact eligible ids and canonical
   download URLs (with their stage reuse states), batches of at most
   five, the whisper stop/restore authority for the exact service
   identity bound read-only BEFORE the plan, the synthesis
   endpoint/model, and the retry policy (each id/operation attempted
   at most once this invocation). Stage windows provably inside that
   scope are discharged by :class:`RunScopedAuthorization` and audited
   as ``covered_by_run_plan`` — never fabricated human yeses; anything
   else (unknown kinds, excess ids, unapproved destinations, whisper
   config drift) is forwarded to the human as genuinely new scope and
   never auto-granted.
3. Verification candidate URLs are unknown until synthesis, so the
   batches DEFER the verify window and the run asks ONE grouped
   authorization showing every exact validated URL before any
   retrieval.

There is no global ``--yes`` and no remembered approval: denial, EOF
and non-interactive input all fail closed, and every invocation asks
again. Progress narration goes to stderr (stage start/finish per id
with the actual outcome, reuse flag and measured duration; batch x/y);
stdout stays the single parseable JSON report. An aggregate progress
view is derived from the product's stores at report time — it is a
view, never a second state authority.

Phase 0 addendum (swap-quiet-retry PRD): after a batch whose vision
stage failed the ``swap_delta`` gate with VERIFIED cleanup, this module
additionally offers ONE bounded, measured, separately-consented vision
retry per video per invocation — a read-only cooldown (5 s cumulative-
counter samples, 60 s admission floor, 180 s never-restarted cap, 3
consecutive intervals <= 1 MiB/s), a named re-admission authority for
the SAME whisper service, and an append-only evidence archive in the
existing manifest. The run-plan grant NEVER covers it (a dedicated
``vision-swap-retry`` window goes to the human), a fired gate keeps the
original GPU window closed for the remaining same-window ids (they stay
pending even after a successful retry), and retry exhaustion is
video/window-scoped — never relabeled as the global service-
restoration hard stop.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import config
from .browser import (
    BrowserInventoryError,
    PlaywrightMissingError,
    run_browser_inventory,
)
from .collection import (
    CollectionError,
    build_collection_plan,
    parse_collection_url,
    resolve_collection,
)
from .contracts import JobManifest, StageOutcome, utc_now_iso
from .gates import GATE_SWAP_DELTA
from .guided import (
    GUIDED_MAX_BATCH,
    ConsentProvider,
    ConsentWindow,
    ConsoleConsentProvider,
    StageFunctions,
    _run_dir_of,
    default_stage_functions,
    derive_item_stages,
    run_guided,
)
from .state import Backlog, Processed, StateRoot, atomic_write_bytes
from .swap_cooldown import (
    SwapCooldownMeasurementError,
    SwapQuietWatcher,
    format_rate_mib_per_s,
)
from .synthesis import SynthesisDocument, run_synthesis_stage
from .synthesis_api import (
    TextApiConfig,
    build_evidence_payload,
    build_request_messages,
    load_text_api_config,
    read_evidence_text,
    request_json_document,
    sanitize_evidence_text,
)
from .vision import default_vmstat_fn
from .whisper_lifecycle import (
    WhisperLifecycleError,
    inspect_whisper,
    restore_whisper,
    same_service_config,
    stop_whisper,
    verify_known_service,
)

ELIGIBLE_CLASSIFICATIONS = ("new_processable", "partial_resumable")


class CollectionRunError(RuntimeError):
    """Usage-level collection-run failure (invalid input, preflight)."""


@dataclasses.dataclass(frozen=True)
class SynthesisDeniedOutcome:
    """Consent-denial result shaped like a stage outcome (blocked)."""

    outcome: str
    reason: str
    reused: bool = False


def preflight(
    *,
    state: StateRoot,
    collection_url: str,
    env: Mapping[str, str],
    browser_controller_injected: bool = False,
) -> dict[str, Any]:
    """Read-only preflight: dependencies, state, planned destinations.

    Performs no network, no browser, no podman and no writes — only
    local reads (an optional Playwright import check and environment
    variable NAMES for the text API).
    """
    ref = parse_collection_url(collection_url)
    if browser_controller_injected:
        playwright_ok, playwright_detail = True, "(injected browser boundary)"
    else:
        from .browser import playwright_available

        playwright_ok, playwright_detail = playwright_available()
    api_config, missing = load_text_api_config(env)
    scan = None
    try:
        _, scan = resolve_collection(state, collection_url)
    except CollectionError:
        scan = None
    return {
        "collection_url": ref.collection_url,
        "collection_key": ref.collection_key,
        "state_root": str(state.root),
        "playwright_available": playwright_ok,
        "playwright_detail": playwright_detail,
        "text_api_configured": api_config is not None,
        "text_api_missing_env": missing,
        "text_api_origin": api_config.origin() if api_config else None,
        "text_api_model": api_config.model if api_config else None,
        "scan_state_present": scan is not None,
        "scan_status": scan.status if scan is not None else None,
        "batch_cap": GUIDED_MAX_BATCH,
        "exclusive_writer": (
            "this run is a writer like any other: run it in an "
            "exclusive-writer operational window — never concurrently "
            "with guided-run, verify emissions or the review CLI"
        ),
        "planned_destinations": [
            "headed Playwright browser (inventory only; run-scoped profile)",
            "gated pinned-extractor downloads of the selected TikTok URLs",
            "isolated Ollama (vision) and the shared whisper service "
            "(audio) on this machine",
            *(
                [
                    f"sanitized evidence text to the configured text API at "
                    f"{api_config.origin()} (model {api_config.model})"
                ]
                if api_config
                else [
                    "(text API not configured: synthesis will stop at a "
                    "blocked, resumable state until the environment "
                    "variables are provided)"
                ]
            ),
            "bounded retrieval of exactly the validated candidate URLs",
        ],
    }


def make_auto_synthesizer(
    api_config: TextApiConfig,
    *,
    transport: Callable[..., Any],
    out: Callable[[str], None] | None = None,
) -> Callable[[str, StateRoot, Callable[[ConsentWindow], bool]], Any]:
    """Build the Phase 0 synthesis hook used by ``run_guided``.

    The hook asks its own consent window through the run's ``ask``
    logger (so the window lands in the run's consent log), then routes
    the provider result through the EXISTING validated-document
    ingestion stage. Denial yields a blocked, resumable outcome — never
    an invented classification and never a skip of the stage contract.
    Under ``collection-run`` the window is discharged by the run-plan
    grant (endpoint/model were named upfront), so the operator is not
    prompted per video.
    """
    out = out or (lambda _text: None)

    def auto_synthesize(
        video_id: str,
        state: StateRoot,
        ask: Callable[[ConsentWindow], bool],
    ) -> Any:
        window = ConsentWindow(
            kind="synthesis-api",
            title=(
                "automatic synthesis through the configured OpenAI-compatible "
                "text API"
            ),
            ids=(video_id,),
            destinations=(api_config.origin(),),
            operations=(
                f"ONE request to {api_config.origin()} (model "
                f"{api_config.model}) carrying ONLY sanitized evidence "
                "text derived from this video's video.md and audio.md",
                "tools, provider-side retrieval and command execution are "
                "DISABLED in the request; structured JSON is requested",
            ),
            limits=(
                f"timeout {api_config.timeout_seconds}s; exactly one "
                "attempt, no retry",
                "evidence text is path-scrubbed, secret-scrubbed and "
                f"capped per modality; no media, frames, browser state or "
                "credentials ever leave the machine",
            ),
            effects=(
                "the returned JSON becomes a CANDIDATE document only: it "
                "is persisted solely after passing the existing strict "
                "SynthesisDocument validation and credential scrub",
                "timeout/refusal/malformed output is a resumable "
                "synthesis stop (completed vision/audio are reused, "
                "never repeated)",
            ),
            # Structured identity for exact authorization matching —
            # never substring-matched from the display prose above.
            model=api_config.model,
        )
        if not ask(window):
            return SynthesisDeniedOutcome(
                outcome="blocked",
                reason=(
                    "synthesis API window denied: no request was sent and "
                    "nothing was persisted; re-invoke to be asked again "
                    "(completed stages are reused)"
                ),
            )

        def document_fn() -> Mapping[str, Any]:
            entry_run_dir = _run_dir_of(state, video_id)
            if entry_run_dir is None:
                raise RuntimeError(
                    "no run directory recorded for this ID; fetch must "
                    "complete first"
                )
            video_md, audio_md = read_evidence_text(entry_run_dir)
            payload = build_evidence_payload(
                sanitize_evidence_text(video_md),
                sanitize_evidence_text(audio_md),
            )
            messages = build_request_messages(payload)
            return request_json_document(api_config, messages, transport=transport)

        # Progress narration like every other stage: start line, then
        # finish with the ACTUAL outcome/reuse/duration. The network
        # call itself stays silent in between (no fabricated heartbeat)
        # and no evidence text is ever narrated.
        out(f"stage synthesis-api start: {video_id}")
        started = time.monotonic()
        try:
            result = run_synthesis_stage(
                video_id, state=state, document_fn=document_fn
            )
        except BaseException as exc:
            out(
                f"stage synthesis-api finish: {video_id} raised "
                f"{type(exc).__name__} after {time.monotonic() - started:.1f}s"
            )
            raise
        value = getattr(result, "outcome", result)
        reused = bool(getattr(result, "reused", False))
        out(
            f"stage synthesis-api finish: {video_id} {value} "
            f"(reused={reused}, {time.monotonic() - started:.1f}s)"
        )
        return result

    return auto_synthesize


def build_stage_functions(
    *,
    api_transport: Callable[..., Any],
    podman_runner: Callable[..., Any],
) -> StageFunctions:
    """Production stages + Phase 0 whisper lifecycle hooks.

    Everything is the REAL production default from
    :func:`default_stage_functions` — only the whisper lifecycle hooks
    are added (all-or-nothing, per the StageFunctions contract). The
    automatic synthesis path is injected separately through
    ``run_guided(auto_synthesizer=...)``, which routes results through
    the existing validated-document ingestion stage.
    """
    base = default_stage_functions()
    return dataclasses.replace(
        base,
        whisper_inspect=lambda: inspect_whisper(podman_runner),
        whisper_stop=lambda info: stop_whisper(info, runner=podman_runner),
        whisper_restore=lambda info: restore_whisper(info, runner=podman_runner),
    )


def _eligible_ids(state: StateRoot, collection_url: str) -> tuple[list[str], dict[str, int]]:
    ref, scan = resolve_collection(state, collection_url)
    if scan is None:
        raise CollectionRunError(
            f"no persisted scan state for {ref.collection_url}"
        )
    plan = build_collection_plan(state, scan)
    eligible = [
        item.stable_id
        for item in plan.items
        if item.classification in ELIGIBLE_CLASSIFICATIONS
    ]
    return eligible, dict(plan.counts)


def _frozen_inventory(
    state: StateRoot, collection_url: str
) -> tuple[tuple[str, ...], dict[str, str], dict[str, dict[str, str]], dict[str, int]]:
    """Freeze the run's exact work list ONCE, right after the capture.

    The frozen list is the ONLY selection authority for this
    invocation's batches: eligible ids in plan order with their
    canonical download URLs and current stage reuse states (derived
    from the product's own state). Ids that become eligible later are
    never absorbed without a new scope decision — a later invocation.
    """
    ref, scan = resolve_collection(state, collection_url)
    if scan is None:
        raise CollectionRunError(
            f"no persisted scan state for {ref.collection_url}"
        )
    plan = build_collection_plan(state, scan)
    ids: list[str] = []
    urls: dict[str, str] = {}
    stage_states: dict[str, dict[str, str]] = {}
    for item in plan.items:
        if item.classification not in ELIGIBLE_CLASSIFICATIONS:
            continue
        ids.append(item.stable_id)
        if item.canonical_url:
            urls[item.stable_id] = item.canonical_url
        stage_states[item.stable_id] = derive_item_stages(state, item.stable_id)
    return tuple(ids), urls, stage_states, dict(plan.counts)


def _bind_whisper_identity(
    stages: StageFunctions, out: Callable[[str], None]
) -> tuple[Any | None, str]:
    """Read-only whisper identity binding BEFORE the run-plan window.

    The operator must never be asked to authorize stopping a service
    whose identity is unknown, and the plan must name the exact
    service/configuration it covers. This performs ONLY the read-only
    inspect plus the positive known-service verification; any failure
    binds NOTHING (later whisper windows are asked as genuinely new
    scope, fail-closed on denial). A configuration that later differs
    from this binding is not covered either.
    """
    if not stages.whisper_lifecycle_ready():
        return None, (
            "whisper lifecycle coordination inactive (standalone "
            "semantics: the service is operator-managed and blocks are "
            "surfaced, never managed here)"
        )
    try:
        info = stages.whisper_inspect()
    except Exception as exc:  # noqa: BLE001 - read-only, reported honestly
        return None, (
            "whisper identity inspection failed read-only before the "
            f"plan ({exc}); no stop/restore is pre-approved — any later "
            "whisper window would be asked as new scope"
        )
    if info is None:
        return None, (
            "no known whisper container exists right now; if one appears "
            "during the run, its stop/restore needs a new authority "
            "question (never auto-granted)"
        )
    try:
        verify_known_service(info)
    except WhisperLifecycleError as exc:
        return None, (
            f"whisper identity verification failed before the plan: "
            f"{exc}; no stop/restore is pre-approved"
        )
    out(
        "whisper identity bound read-only before the plan: "
        f"{info.describe()}"
    )
    return info, (
        "identity verified read-only before the plan: the bounded stop "
        "before vision and the standing restore per GPU batch cycle are "
        "covered by this grant for exactly this service/configuration "
        "(restore honored once per actual stop cycle; errors/interrupts "
        "report the UNKNOWN state, no rollback, progress blocked); a "
        "changed configuration fails closed to a new authority question"
    )


@dataclasses.dataclass(frozen=True)
class RunPlan:
    """The scope granted by the ONE whole-current-run authorization.

    ``frozen_ids``/``canonical_urls``/``stage_states`` capture the
    frozen inventory; ``whisper_info`` carries the bound service object
    (``whisper_bound`` is its display text) so coverage checks compare
    exact identities, not wildcards.
    """

    collection_url: str
    frozen_ids: tuple[str, ...]
    canonical_urls: dict[str, str]
    stage_states: dict[str, dict[str, str]]
    batch_cap: int
    batch_count: int
    plan_counts: dict[str, int]
    whisper_bound: str | None
    whisper_note: str
    text_api_origin: str | None
    text_api_model: str | None
    whisper_info: Any = None


def build_run_plan_window(plan: RunPlan) -> ConsentWindow:
    """Human decision 2: accept the WHOLE current run exactly as named."""
    destinations: list[str] = [
        plan.canonical_urls.get(video_id, f"(no canonical URL recorded for {video_id})")
        for video_id in plan.frozen_ids
    ]
    if plan.whisper_bound:
        destinations.append(plan.whisper_bound)
    if plan.text_api_origin:
        destinations.append(plan.text_api_origin)
    reuse_lines: list[str] = []
    for video_id in plan.frozen_ids:
        done = [
            f"{stage}={state}"
            for stage, state in sorted(plan.stage_states[video_id].items())
            if state == "complete"
        ]
        if done:
            reuse_lines.append(
                f"id {video_id} resumes with {', '.join(done)} "
                "(reused as-is, never repeated)"
            )
    operations: tuple[str, ...] = (
        "process exactly the frozen ids above, in order, through fetch -> "
        "prepare -> vision -> audio -> synthesis ingestion -> verify, in "
        f"batches of at most {plan.batch_cap} ({plan.batch_count} batch(es)); "
        "each id is attempted in exactly ONE batch",
        "gated pinned-extractor download of exactly the frozen canonical "
        "URLs listed above (oEmbed metadata per URL), CPU preparation in "
        "the pinned container, GPU vision (isolated Ollama lifecycle + "
        "model) and audio through the shared whisper service, per batch",
        *reuse_lines,
        *(
            (
                f"automatic synthesis: ONE request per id to "
                f"{plan.text_api_origin} (model {plan.text_api_model}) "
                "carrying ONLY sanitized evidence text; timeout/refusal/"
                "malformed output is a blocked, resumable stop",
            )
            if plan.text_api_origin
            else (
                "(no text API configured: synthesis stops at a blocked, "
                "resumable state — no model calls)",
            )
        ),
        *(
            (
                f"whisper coordination for exactly {plan.whisper_bound}: "
                "bounded verified stop before vision, standing restore "
                "after the window (once per actual stop cycle, also on "
                "failure/interrupt); restore/health failure blocks all "
                "progress with the actual partial state, no rollback",
            )
            if plan.whisper_bound
            else (f"whisper: {plan.whisper_note}",)
        ),
        (
            "verification candidate URLs are unknown until synthesis: ONE "
            "additional grouped authorization will show every exact "
            "validated URL before any retrieval — nothing is verified "
            "before that separate decision"
        ),
    )
    limits = (
        f"each frozen id is attempted in exactly ONE batch of at most "
        f"{plan.batch_cap}: a failed id is never reattempted this "
        "invocation (safe stop; resumable by re-invoking)",
        "no automatic retry of any operation; a fired resource gate is "
        "final for its GPU window; unrelated progress never re-opens a "
        "failed id",
        "ids that become eligible later are never absorbed into this run "
        "(they need a new scope decision in a new invocation)",
        "this grant covers exactly the named ids/URLs/endpoint/model/"
        "service and the stage windows they discharge — anything else is "
        "asked as genuinely new scope and never auto-granted",
    )
    effects = (
        "successful verification appends a PENDING backlog entry per id "
        "(duplicate-guarded); statuses are never edited here — review "
        "stays with backlog-list/show/decide/plan/apply",
        *(
            (
                "synthesis sends ONLY sanitized, secret-scrubbed evidence "
                f"text to {plan.text_api_origin} (model "
                f"{plan.text_api_model}); no media, frames or credentials "
                "ever leave the machine",
            )
            if plan.text_api_origin
            else ()
        ),
        "per-stage durable state is written by the existing stages "
        "(fetch.json/meta.json, frames, audio, synthesis.json, "
        "verification.json) with their own fingerprints and resume rules",
    )
    return ConsentWindow(
        kind="run-plan",
        title=(
            "accept this WHOLE current run (one authorization for every "
            "batch and stage window named here)"
        ),
        ids=tuple(plan.frozen_ids),
        destinations=tuple(destinations),
        operations=operations,
        limits=limits,
        effects=effects,
    )


def _run_plan_dict(plan: RunPlan, granted: bool) -> dict[str, Any]:
    return {
        "granted": granted,
        "frozen_ids": list(plan.frozen_ids),
        "canonical_urls": dict(plan.canonical_urls),
        "stage_states": {
            video_id: dict(states)
            for video_id, states in plan.stage_states.items()
        },
        "batch_cap": plan.batch_cap,
        "batch_count": plan.batch_count,
        "plan_counts": dict(plan.plan_counts),
        "whisper": {"bound": plan.whisper_bound, "note": plan.whisper_note},
        "text_api": (
            {"origin": plan.text_api_origin, "model": plan.text_api_model}
            if plan.text_api_origin
            else None
        ),
    }


def _record_decision(
    log: list[dict[str, Any]], window: ConsentWindow, granted: bool, basis: str
) -> None:
    log.append(
        {
            "window": window.to_dict(),
            "granted": granted,
            "basis": basis,
            "asked_at": utc_now_iso(),
        }
    )


class _RecordingConsent(ConsentProvider):
    """Pass-through that records human decisions for the run audit."""

    def __init__(
        self,
        inner: ConsentProvider,
        log: list[dict[str, Any]],
        *,
        basis: str,
    ) -> None:
        self.inner = inner
        self.log = log
        self.basis = basis

    def ask(self, window: ConsentWindow) -> bool:
        granted = self.inner.ask(window)
        _record_decision(self.log, window, granted, self.basis)
        return granted

    def await_readiness(self, prompt: str) -> bool:
        return self.inner.await_readiness(prompt)


class RunScopedAuthorization(ConsentProvider):
    """Discharges stage windows covered by the granted run plan.

    Coverage is decided per window kind and is strictly subset-based —
    there is no wildcard grant of any kind:

    - ``tanda``/``prepare``/``gpu``: covered iff every id is inside the
      frozen inventory;
    - ``fetch``: additionally every destination must be one of the
      frozen canonical URLs;
    - ``synthesis-api``: the destination must be the endpoint named in
      the plan AND the model must appear in the window's operations;
    - ``whisper-stop``/``whisper-restore``: the window's service text
      must match the identity bound read-only before the plan (state
      text normalized, name/image/config exact);
    - ``verify``: ALWAYS deferred during batches — candidate URLs are
      unknown until synthesis, so ``run_collection`` asks ONE grouped
      verification window afterwards instead;
    - any other kind: NEVER auto-granted; forwarded to the human.

    Non-covered windows are forwarded to the HUMAN provider as a
    genuinely-new-scope question (fail-closed on denial). Every
    decision is audited with its basis so a covered discharge is never
    mistaken for a human yes.
    """

    def __init__(
        self,
        human: ConsentProvider,
        plan: RunPlan,
        *,
        out: Callable[[str], None] | None = None,
    ) -> None:
        self.human = human
        self.plan = plan
        self.authorization_log: list[dict[str, Any]] = []
        self._out = out or (lambda _text: None)

    # -- audit -------------------------------------------------------------

    def _record(
        self, window: ConsentWindow, decision: str, basis: str, granted: bool
    ) -> None:
        self.authorization_log.append(
            {
                "window": window.to_dict(),
                "decision": decision,
                "basis": basis,
                "granted": granted,
                "asked_at": utc_now_iso(),
            }
        )

    # -- coverage ----------------------------------------------------------

    # Stable shape of the model token in the generated synthesis window
    # prose ("ONE request to <origin> (model <token>) ..."), used ONLY
    # as the fallback binding source for legacy windows without the
    # structured ``model`` attribute. The comparison itself is ALWAYS
    # exact string equality — a model whose name merely CONTAINS the
    # plan model (prefix/suffix impersonation) extracts to a different
    # token and is never covered.
    _MODEL_TOKEN_RE = re.compile(r"\(model ([^)]+)\)")

    def _window_model(self, window: ConsentWindow) -> str | None:
        """The window's claimed model token, EXACT (never substring)."""
        model = getattr(window, "model", None)
        if model is not None:
            return str(model)
        for operation in window.operations:
            match = self._MODEL_TOKEN_RE.search(operation)
            if match:
                return match.group(1).strip()
        return None

    def _whisper_covered(self, destinations: Sequence[str]) -> bool:
        info = self.plan.whisper_info
        if info is None:
            return False
        accepted = {info.describe()}
        if not getattr(info, "running", True):
            # The bound service was stopped at plan time; the stop window
            # for the SAME identity now running differs only in the
            # volatile state segment. Name/image/config must still match
            # exactly (they are embedded in the compared text).
            accepted.add(
                dataclasses.replace(info, state="running", running=True).describe()
            )
        return all(destination in accepted for destination in destinations)

    def _coverage(self, window: ConsentWindow) -> tuple[bool, str]:
        plan = self.plan
        frozen = set(plan.frozen_ids)
        if set(window.ids) - frozen:
            return False, "ids outside the frozen run inventory"
        kind = window.kind
        if kind in ("tanda", "prepare", "gpu"):
            return True, "ids within the frozen run inventory"
        if kind == "fetch":
            approved = set(plan.canonical_urls.values())
            if window.destinations and all(
                destination in approved
                for destination in window.destinations
            ):
                return True, "exact canonical URLs within the frozen run inventory"
            return False, "destination URL not approved by the run plan"
        if kind == "synthesis-api":
            if (
                plan.text_api_origin is not None
                and list(window.destinations) == [plan.text_api_origin]
                and plan.text_api_model is not None
                and self._window_model(window) == plan.text_api_model
            ):
                return (
                    True,
                    "the endpoint and the EXACT model named in the run plan",
                )
            return (
                False,
                "synthesis endpoint/model not exactly the one named in "
                "the run plan",
            )
        if kind in ("whisper-stop", "whisper-restore"):
            if self._whisper_covered(window.destinations):
                return (
                    True,
                    "the exact service identity bound read-only before "
                    "the run plan",
                )
            return (
                False,
                "whisper service/configuration differs from the plan "
                "binding",
            )
        return False, f"kind {kind!r} is never auto-covered"

    # -- ConsentProvider ----------------------------------------------------

    def ask(self, window: ConsentWindow) -> bool:
        covered, basis = self._coverage(window)
        if covered:
            self._record(window, "covered_by_run_plan", basis, True)
            self._out(
                f"authorization: {window.kind} window covered by the run "
                "plan grant (no prompt)"
            )
            return True
        if window.kind == "verify":
            self._record(
                window,
                "deferred",
                "the run plan defers verification to ONE grouped "
                "authorization after synthesis",
                False,
            )
            return False
        granted = self.human.ask(window)
        self._record(
            window,
            "human_granted" if granted else "human_denied",
            f"genuinely new scope not covered by the run plan ({basis})",
            granted,
        )
        return granted


def _narrated_stages(
    stages: StageFunctions,
    out: Callable[[str], None],
    *,
    monotonic: Callable[[], float] | None = None,
) -> StageFunctions:
    """Progress narration around each REAL stage call (stderr only).

    A start line before the call and a finish line on return with the
    ACTUAL outcome, the reuse flag and the measured duration; an
    exception is narrated with its type and re-raised. Long calls stay
    silent between their start and finish lines — no heartbeat or
    percentage is ever fabricated.
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

    return dataclasses.replace(
        stages,
        fetch=wrap("fetch", stages.fetch),
        prepare=wrap("prepare", stages.prepare),
        vision=wrap("vision", stages.vision),
        audio=wrap("audio", stages.audio),
        verify=wrap("verify", stages.verify),
    )


def _collect_verify_candidates(
    state: StateRoot, frozen_ids: Sequence[str]
) -> tuple[list[str], list[str], dict[str, list[str]], list[tuple[str, str]]]:
    """Accumulate verification candidates from the product's own state.

    An id is ready when its synthesis stage is complete and verify is
    not; its exact candidate URLs come from the persisted, VALIDATED
    ``synthesis.json`` (claims and entities) — the same authority the
    guided coordinator uses. This is a read-only view, never a second
    ledger.
    """
    ready: list[str] = []
    for video_id in frozen_ids:
        stages = derive_item_stages(state, video_id)
        if (
            stages.get("synthesis") == "complete"
            and stages.get("verify") != "complete"
        ):
            ready.append(video_id)
    urls: list[str] = []
    per_id: dict[str, list[str]] = {}
    read_failures: list[tuple[str, str]] = []
    for video_id in ready:
        doc_urls: list[str] = []
        run_dir = _run_dir_of(state, video_id)
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
                    read_failures.append((video_id, str(exc)))
        per_id[video_id] = doc_urls
        for url in doc_urls:
            if url not in urls:
                urls.append(url)
    return ready, urls, per_id, read_failures


def _run_grouped_verification(
    *,
    state: StateRoot,
    narrated: StageFunctions,
    consent: ConsentProvider,
    human_decisions: list[dict[str, Any]],
    frozen_ids: Sequence[str],
    out: Callable[[str], None],
    report: dict[str, Any],
) -> bool:
    """ONE grouped verification authorization for the whole run.

    Every ready id's exact validated candidate URLs are displayed in a
    single window; only a grant runs the verify stages (once per id,
    under this authorization). Denial emits nothing and leaves the work
    resumable by re-invocation.
    """
    ready, urls, _per_id, read_failures = _collect_verify_candidates(
        state, frozen_ids
    )
    if not ready:
        report["verification"] = None
        return True
    window = ConsentWindow(
        kind="verify",
        title=(
            "bounded verification retrieval of the EXACT candidate URLs "
            "(ONE grouped decision for this whole run)"
        ),
        ids=tuple(ready),
        destinations=tuple(urls)
        if urls
        else (
            "(no candidate URLs recorded — verify performs no network "
            "retrieval and records unverifiable/no_candidate_urls verdicts)",
        ),
        operations=(
            "bounded HTTP(S) retrieval of only the URLs listed above, "
            "accumulated over this run's frozen ids",
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
    granted = consent.ask(window)
    _record_decision(
        human_decisions,
        window,
        granted,
        "human decision: grouped verification authorization",
    )
    outcomes: dict[str, Any] = {}
    for video_id, reason in read_failures:
        outcomes[video_id] = {
            "outcome": "failed",
            "reason": f"cannot read candidate URLs: {reason}",
            "reused": False,
        }
    if granted:
        blocked = {video_id for video_id, _ in read_failures}
        for video_id in ready:
            if video_id in blocked:
                continue
            outcome = narrated.verify(video_id, state)
            outcomes[video_id] = {
                "outcome": getattr(outcome, "outcome", outcome),
                "reason": str(getattr(outcome, "reason", "")),
                "reused": bool(getattr(outcome, "reused", False)),
            }
    else:
        out(
            "grouped verification denied: no retrieval ran and no backlog "
            "entry was emitted; re-invoke to be asked again"
        )
    report["verification"] = {
        "ids": ready,
        "candidate_urls": urls,
        "granted": granted,
        "outcomes": outcomes,
        "read_failures": [
            {"id": video_id, "reason": reason}
            for video_id, reason in read_failures
        ],
    }
    if not granted:
        return False
    return all(
        record.get("outcome") == "complete"
        for record in outcomes.values()
    )


# --------------------------------------------------------------------------
# Phase 0 addendum: ONE bounded vision swap-retry after measured quiet
# (prds/PHASE-0-SWAP-QUIET-RETRY-PRD.md). Every rule here traces to that
# PRD's decision ledger D-01..D-10; nothing weakens a gate, a consent
# rule or the fired-gate window semantics.
# --------------------------------------------------------------------------


class _SwapRetryBlocked(RuntimeError):
    """A lifecycle/restore failure inside the retry flow: progress is
    blocked globally with the actual state reported (T6 semantics — the
    existing service-restoration hard-stop category, never a relabel of
    gate exhaustion)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclasses.dataclass(frozen=True)
class SwapRetryEligibility:
    """Read-only eligibility answer from DURABLE typed evidence (FR-01)."""

    video_id: str
    eligible: bool
    reason: str
    overshoot_mib: float | None = None
    scope: dict[str, Any] = dataclasses.field(default_factory=dict)


def _load_job_manifest(
    state: StateRoot, video_id: str
) -> tuple[JobManifest, Path] | None:
    run_dir = _run_dir_of(state, video_id)
    if run_dir is None:
        return None
    path = Path(run_dir) / "meta.json"
    if not path.is_file():
        return None
    try:
        manifest = JobManifest.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError):
        return None
    return manifest, path


def _write_job_manifest(path: Path, manifest: JobManifest) -> None:
    atomic_write_bytes(
        path,
        json.dumps(
            manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n",
    )


def _swap_retry_eligibility(
    state: StateRoot, video_id: str
) -> SwapRetryEligibility:
    """ONLY a failed vision run whose TYPED gate id is ``swap_delta`` and
    whose failure cleanup positively verified release qualifies (D-01).

    Decided from the persisted typed gate field in ``meta.json``
    ``resource_gates`` — never by parsing reason prose (FR-16). Offload,
    unload-verification, service-identity, fetch/prepare/audio/synthesis/
    verify failures and deadline exhaustion never qualify.
    """
    loaded = _load_job_manifest(state, video_id)
    if loaded is None:
        return SwapRetryEligibility(
            video_id, False, "no durable manifest can be read for this id"
        )
    manifest, _path = loaded
    record = manifest.stages.get("vision")
    if record is None:
        return SwapRetryEligibility(
            video_id, False, "no vision stage record exists for this id"
        )
    if record.result.outcome is not StageOutcome.FAILED:
        return SwapRetryEligibility(
            video_id,
            False,
            f"vision outcome {record.result.outcome.value!r} is not a "
            "failed run (blocked/deadline outcomes are never retried)",
        )
    gates_detail = manifest.resource_gates.get("vision") or {}
    entries = gates_detail.get("gates") or []
    swap_failed = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("gate") == GATE_SWAP_DELTA
        and entry.get("passed") is False
    ]
    if not swap_failed:
        return SwapRetryEligibility(
            video_id,
            False,
            "the persisted typed gate ledger shows no failed swap_delta "
            "gate for the vision stage (prose reasons are never parsed)",
        )
    cleanup = gates_detail.get("cleanup") or {}
    if cleanup.get("release_verified") is not True:
        return SwapRetryEligibility(
            video_id,
            False,
            "the failed attempt's cleanup did not positively verify model "
            "release (unload verified, GPU release observed)",
        )
    last = swap_failed[-1]
    detail = last.get("detail") or {}
    overshoot: float | None = None
    delta_bytes = detail.get("delta_bytes")
    limit_bytes = detail.get("limit_bytes")
    if (
        isinstance(delta_bytes, int)
        and isinstance(limit_bytes, int)
        and delta_bytes > limit_bytes
    ):
        overshoot = round((delta_bytes - limit_bytes) / (1024 * 1024), 2)
    scope = {
        "vision_model": config.VISION_MODEL,
        "media_sha256": manifest.media_sha256,
        "sampling_manifest_sha256": record.input_sha256,
    }
    return SwapRetryEligibility(
        video_id,
        True,
        "failed vision swap_delta gate with verified cleanup",
        overshoot_mib=overshoot,
        scope=scope,
    )


def _overshoot_text(overshoot_mib: float | None) -> str:
    if overshoot_mib is None:
        return (
            "overshoot not recorded in this manifest's raw gate detail "
            "(pre-addendum evidence)"
        )
    return (
        f"+{overshoot_mib:.2f} MiB over the "
        f"{config.GATES.swap_delta_limit_mib} MiB budget"
    )


def _build_swap_retry_window(
    video_id: str, elig: SwapRetryEligibility, plan: RunPlan
) -> ConsentWindow:
    """The DEDICATED new consent (D-02): never covered by the run plan."""
    whisper_bound = plan.whisper_bound or "(whisper identity not bound)"
    return ConsentWindow(
        kind="vision-swap-retry",
        title=(
            "ONE bounded vision retry after a measured swap_delta gate "
            "failure (new scope: the run-plan grant does NOT cover this)"
        ),
        ids=(video_id,),
        destinations=(whisper_bound,),
        operations=(
            "RE-RUN of the vision stage ONLY for this id: valid fetch and "
            "prepare results are reused; completed audio, synthesis and "
            "verification are never repeated",
            "read-only cooldown sampling of the systemwide cumulative "
            f"swap I/O counters every {config.SWAP_RETRY_COOLDOWN_INTERVAL_SECONDS:.0f}s "
            "BEFORE the retry (no mutation of any kind while sampling)",
            f"stop the SAME known whisper service again ({whisper_bound}) "
            "for the retry window and restore it afterwards — THIS "
            "consent is that named lifecycle authority",
            "start a new isolated Ollama instance only if none answers on "
            f"{config.OLLAMA_API_BASE} (a preexisting one is never adopted)",
        ),
        limits=(
            "exactly ONE extra vision attempt for this id THIS invocation: "
            "no third attempt and no re-prompt after a failed second "
            "attempt; the video stays pending and resumable",
            f"cooldown bounds: admission no earlier than "
            f"{config.SWAP_RETRY_COOLDOWN_FLOOR_SECONDS:.0f}s, hard cap "
            f"{config.SWAP_RETRY_COOLDOWN_CAP_SECONDS:.0f}s from cooldown "
            "start (never restarted), stability = "
            f"{config.SWAP_RETRY_COOLDOWN_STABLE_INTERVALS} consecutive "
            f"intervals <= {config.SWAP_RETRY_COOLDOWN_RATE_LIMIT_MIB_PER_S:.2f} MiB/s; "
            "unreadable or implausible counters fail closed",
            f"gates UNCHANGED for the retry attempt: free VRAM >= "
            f"{config.GATES.free_vram_gate_mib} MiB, swap I/O delta <= "
            f"{config.GATES.swap_delta_limit_mib} MiB per attempt, zero "
            "CPU offload, vision stage deadline "
            f"{config.BUDGETS.vision_stage_deadline_seconds}s (fresh "
            "immediate baseline only at the attempt's own start)",
            f"measured failure being retried: swap_delta gate, "
            f"{_overshoot_text(elig.overshoot_mib)}",
            "scope fingerprints (exact — any change is a NEW authorization "
            f"question): model {elig.scope.get('vision_model')}, media "
            f"{elig.scope.get('media_sha256')}, sampling "
            f"{elig.scope.get('sampling_manifest_sha256')}",
        ),
        effects=(
            "a failed second attempt ends retrying for this id this "
            "invocation (retry exhausted, video/window-scoped — NOT the "
            "global service-restoration hard stop)",
            "the remaining ids of the original GPU window stay PENDING "
            "even if this retry succeeds: no silent skip, no reorder",
            "the original failure evidence and its partial artifacts are "
            "archived unmodified first; the retry is recorded as "
            "additional evidence, never as a replacement",
            "progress narration goes to stderr; the final stdout JSON "
            "report gains an additive per-video retry object",
        ),
    )


def _verify_whisper_restored_healthy(
    stages: StageFunctions, plan: RunPlan
) -> str | None:
    """FR-05/FR-06 pre-consent verification: the batch cycle's standing
    restore left the SAME known whisper service running and healthy.

    Returns ``None`` when verified; an error string (blocked_global,
    the service-restoration hard-stop category) otherwise. Read-only.
    """
    bound = plan.whisper_info
    if bound is None:
        return (
            "the whisper identity was not bound before the run plan; the "
            "SAME-service restore required before any retry consent "
            "cannot be positively verified"
        )
    try:
        current = stages.whisper_inspect()
    except Exception as exc:  # noqa: BLE001 - read-only, reported honestly
        return (
            f"cannot verify the restored whisper service before the retry "
            f"consent (inspect failed: {exc}); progress is blocked"
        )
    if current is None:
        return (
            "the known whisper service cannot be found after the batch "
            "cycle; the required healthy restore is NOT verified — "
            "progress is blocked; verify the service state explicitly"
        )
    if not getattr(current, "running", False):
        return (
            "the known whisper service is not running after the batch "
            "cycle; the required healthy restore is NOT verified — "
            "progress is blocked; verify the service state explicitly"
        )
    if not same_service_config(bound, current):
        return (
            "the restored whisper service does not match the identity/"
            f"configuration bound before the run plan (bound "
            f"{bound.describe()}; now {current.describe()}); the SAME "
            "service/config was not restored — progress is blocked; no "
            "rollback is performed or implied"
        )
    try:
        healthy, detail = stages.whisper_health()
    except Exception as exc:  # noqa: BLE001 - read-only, reported honestly
        return (
            f"cannot verify the restored whisper service's health before "
            f"the retry consent ({exc}); progress is blocked"
        )
    if not healthy:
        return (
            "the shared whisper service is restored but NOT healthy "
            f"({detail}); progress is blocked; verify the service state "
            "explicitly; no rollback is performed or implied"
        )
    return None


def _wait_whisper_healthy_for_retry(
    stages: StageFunctions,
    *,
    now: Callable[[], float],
    do_sleep: Callable[[float], None],
    deadline_seconds: float = 60.0,
) -> tuple[bool, str]:
    """Bounded read-only readiness polling after a retry-window restore.

    First probe immediate, one bounded interval between probes, one
    probe of overshoot at most; a crossing stops with the last REAL
    detail. ``KeyboardInterrupt`` is never caught here.
    """
    started = now()
    while True:
        healthy, detail = stages.whisper_health()
        if healthy:
            return True, detail
        if now() - started >= deadline_seconds:
            return False, detail
        do_sleep(1.0)


def _swap_retry_scope_now(
    state: StateRoot, video_id: str
) -> dict[str, Any] | None:
    loaded = _load_job_manifest(state, video_id)
    if loaded is None:
        return None
    manifest, _path = loaded
    record = manifest.stages.get("vision")
    return {
        "vision_model": config.VISION_MODEL,
        "media_sha256": manifest.media_sha256,
        "sampling_manifest_sha256": (
            record.input_sha256 if record is not None else None
        ),
    }


def _restore_whisper_after_drift(
    stages: StageFunctions,
    info: Any,
    *,
    now: Callable[[], float],
    do_sleep: Callable[[float], None],
    context: str,
) -> None:
    """Restore the known service after a drift, under the granted retry
    authority, so resampling NEVER proceeds with the shared service held
    down. Raises ``_SwapRetryBlocked`` (global fail-closed, actual state)
    when the restore cannot be positively completed."""
    try:
        stages.whisper_restore(info)
        healthy, _detail = _wait_whisper_healthy_for_retry(
            stages, now=now, do_sleep=do_sleep
        )
        if not healthy:
            raise _SwapRetryBlocked(
                f"whisper restoration after {context} did not become "
                "healthy: ALL further progress is blocked; verify the "
                "service state explicitly; no rollback is performed or "
                "implied"
            )
    except _SwapRetryBlocked:
        raise
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
        raise _SwapRetryBlocked(
            f"whisper restoration after {context} failed ({exc}): ALL "
            "further progress is blocked; verify the service state "
            "explicitly; no rollback is performed or implied"
        ) from exc


def _swap_retry_readmission(
    *,
    state: StateRoot,
    stages: StageFunctions,
    plan: RunPlan,
    elig: SwapRetryEligibility,
    watcher: SwapQuietWatcher,
    samples: list[dict[str, Any]],
    out: Callable[[str], None],
    now: Callable[[], float],
    do_sleep: Callable[[float], None],
) -> tuple[str, str | None]:
    """Re-admission under the retry consent's named authority (D-05).

    Returns ``(status, reason)``:

    - ``("ok", None)`` — whisper stopped under the retry authority,
      identical configuration re-checked, a fresh post-stop swap sample
      recorded and (when measurable) below threshold, fresh free-VRAM
      gate green; the retry attempt may run;
    - ``("drift", reason)`` — final-conditions drift (FR-11): the
      stability count must reset and sampling resumes inside the SAME
      180 s cap; whisper is left RUNNING (restored if it had been
      stopped — the shared service is NEVER held down while resampling);
    - ``("new_authorization", reason)`` — scope fingerprints changed
      since the consent (FR-24): the approved retry does not cover this
      scope; the video stays pending for a fresh authorization.

    Raises ``_SwapRetryBlocked`` (the existing global service-
    restoration fail-closed category, actual state reported) for
    identity/config mismatches and lifecycle failures: a foreign
    configuration answering under the known name, or a service that
    cannot be found or restored, is an operator-level anomaly that
    resampling must never paper over by assuming a healthy known
    service.
    """
    bound = plan.whisper_info
    assert bound is not None  # narrowing: the flow verified the binding
    # Scope fingerprints first (read-only, before any mutation).
    scope_now = _swap_retry_scope_now(state, elig.video_id)
    if scope_now is None:
        raise _SwapRetryBlocked(
            "the durable manifest disappeared while the swap-retry was "
            "consented; the retry cannot proceed and the scope cannot be "
            "verified — progress is blocked"
        )
    for key in ("media_sha256", "sampling_manifest_sha256"):
        if scope_now.get(key) != elig.scope.get(key):
            return (
                "new_authorization",
                "scope fingerprints changed since the retry consent "
                f"({key}: consent bound {elig.scope.get(key)!r}, now "
                f"{scope_now.get(key)!r}); a retry under changed scope is "
                "a NEW authorization question — the approved retry does "
                "not cover it",
            )
    current = stages.whisper_inspect()
    if current is None:
        raise _SwapRetryBlocked(
            "the bound whisper service cannot be found at re-admission "
            "(podman inspect finds no such container); the SAME known "
            "service cannot be restored — progress is blocked; verify the "
            "service state explicitly; no rollback is performed or implied"
        )
    if not getattr(current, "running", False):
        # Volatile-state drift (FR-11 "service state changed"), but the
        # shared service is DOWN: restore it under the granted retry
        # authority FIRST so resampling never proceeds with it held down.
        _restore_whisper_after_drift(
            stages, current, now=now, do_sleep=do_sleep,
            context="a not-running drift at re-admission",
        )
        return (
            "drift",
            "final conditions drifted: the bound whisper service was not "
            "running at re-admission; it was restored and verified "
            "healthy under the retry authority before resampling "
            "(pre-mutation healthy samples never guarantee post-change "
            "conditions); sampling resumes inside the SAME cap",
        )
    if not same_service_config(bound, current):
        # Identity-level mismatch: a DIFFERENT configuration is answering
        # under the known name. The retry consent authorizes stop/restore
        # of exactly the bound service/configuration — neither stopping
        # this foreign configuration nor "restoring" over it is safe, so
        # this follows the existing restoration fail-closed semantics
        # (T6/FR-06) with the actual state, instead of resampling on the
        # assumption of a healthy known service.
        raise _SwapRetryBlocked(
            "the whisper configuration at re-admission differs from the "
            f"plan binding (bound {bound.describe()}; now "
            f"{current.describe()}); the SAME known service cannot be "
            "safely stopped or restored — progress is blocked; verify "
            "the service state explicitly; no rollback is performed or "
            "implied"
        )
    # Stop under the retry consent's named lifecycle authority.
    try:
        stages.whisper_stop(current)
    except KeyboardInterrupt:
        # The stop result is UNKNOWN: never guess, never restore — the
        # operator verifies the exact service manually (mirrors the
        # standing whisper-stop interrupt semantics; no unsafe rollback).
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
        raise _SwapRetryBlocked(
            f"the whisper stop under the retry authority failed ({exc}); "
            "the service state must be verified explicitly before any "
            "GPU work; no rollback is performed or implied"
        ) from exc
    # Fresh post-stop swap sample (FR-12 explicit accounting): the stop
    # itself mutates the host, so the raw cumulative reading right after
    # the mutation is recorded and — when measurable time elapsed — its
    # interval rate participates: an above-threshold rate IS post-
    # mutation drift (the pre-stop stable windows never guaranteed
    # post-change conditions), resetting the stability count inside the
    # SAME cap.
    transition_sample = watcher.sample_transition("post_stop")
    samples.append(transition_sample.to_dict())
    if (
        transition_sample.rate_bytes_per_s is not None
        and transition_sample.rate_bytes_per_s
        > watcher.rate_limit_bytes_per_s
    ):
        drift = (
            "final conditions drifted: swap I/O measured "
            f"{format_rate_mib_per_s(transition_sample.rate_bytes_per_s)} "
            "in the first interval after the whisper stop (above "
            f"{config.SWAP_RETRY_COOLDOWN_RATE_LIMIT_MIB_PER_S:.2f} "
            "MiB/s); sampling resumes inside the SAME cap"
        )
        _restore_whisper_after_drift(
            stages, current, now=now, do_sleep=do_sleep,
            context="a post-stop swap drift at re-admission",
        )
        return ("drift", drift)
    # Fresh admission gate AFTER the transition (FR-10/FR-11): the
    # vision stage's own gate stays authoritative; this seam only
    # enables the pre-attempt drift check. A RAISING probe leaves the
    # service in a KNOWN stopped state (the stop above verified), so it
    # is restored under the granted authority before failing closed —
    # never left stopped while the flow ends.
    gpu_free = stages.gpu_free_mib
    if gpu_free is not None:
        try:
            free_mib = gpu_free()
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
            _restore_whisper_after_drift(
                stages, current, now=now, do_sleep=do_sleep,
                context="a failed free-VRAM probe at re-admission",
            )
            raise _SwapRetryBlocked(
                f"the free-VRAM probe at re-admission failed ({exc}); "
                "final conditions cannot be verified — failing closed "
                "with the whisper service restored healthy under the "
                "retry authority; no rollback is performed or implied"
            ) from exc
        if free_mib < config.GATES.free_vram_gate_mib:
            drift = (
                "final conditions drifted: only "
                f"{free_mib} MiB free VRAM at re-admission (need "
                f"{config.GATES.free_vram_gate_mib} MiB); sampling "
                "resumes inside the SAME cap"
            )
            _restore_whisper_after_drift(
                stages, current, now=now, do_sleep=do_sleep,
                context="a re-admission VRAM drift",
            )
            return ("drift", drift)
    out("whisper stop (retry authority): verified identical config")
    return ("ok", None)


def _archive_original_vision_attempt(
    state: StateRoot,
    video_id: str,
    elig: SwapRetryEligibility,
    window: ConsentWindow,
) -> dict[str, Any] | None:
    """Archive the ORIGINAL failure before the retry can overwrite it.

    Copies the whole ``vision/`` artifact directory (per-frame payloads,
    responses, records.json, crops) to a fresh ``vision-attempt-N``
    directory inside the SAME run dir and records the original stage
    record + gate evidence + scope in ``manifest.swap_retry`` —
    append-only evidence inside the existing manifest structure, never
    a second job ledger (PRD section 5.1 / FR-15).
    """
    loaded = _load_job_manifest(state, video_id)
    if loaded is None:
        return None
    manifest, path = loaded
    run_dir = path.parent
    vision_dir = run_dir / "vision"
    archive_name = "vision-attempt-1"
    index = 1
    while (run_dir / archive_name).exists():
        index += 1
        archive_name = f"vision-attempt-{index}"
    if vision_dir.is_dir():
        shutil.copytree(vision_dir, run_dir / archive_name)
    original_record = manifest.stages.get("vision")
    swap_retry: dict[str, Any] = {
        "original": {
            "archived_at": utc_now_iso(),
            "stage_record": (
                original_record.to_dict()
                if original_record is not None
                else None
            ),
            "resource_gates_vision": manifest.resource_gates.get("vision"),
            "archive_dir": archive_name if vision_dir.is_dir() else None,
            "scope": dict(elig.scope),
            "failure_reason": (
                original_record.result.reason
                if original_record is not None
                else None
            ),
        },
        "consent": {"window": window.to_dict(), "granted": True},
    }
    if manifest.swap_retry is not None:
        # A LATER invocation retrying again chains the previous retry
        # evidence instead of discarding it (per-invocation cap, not a
        # lifetime block — FR-17).
        swap_retry["prior"] = manifest.swap_retry
    manifest.swap_retry = swap_retry
    manifest.updated_at = utc_now_iso()
    _write_job_manifest(path, manifest)
    return swap_retry


def _record_swap_retry_attempt(
    state: StateRoot, video_id: str, attempt: dict[str, Any]
) -> None:
    """Persist the retry attempt's own provenance (append-only)."""
    loaded = _load_job_manifest(state, video_id)
    if loaded is None:
        return
    manifest, path = loaded
    if manifest.swap_retry is None:
        # Defensive: the archive step must have run first; never invent
        # evidence out of thin air.
        manifest.swap_retry = {"original": None, "consent": None}
    manifest.swap_retry["attempt"] = attempt
    manifest.updated_at = utc_now_iso()
    _write_job_manifest(path, manifest)


def _execute_swap_retry(
    *,
    state: StateRoot,
    stages: StageFunctions,
    narrated: StageFunctions,
    plan: RunPlan,
    video_id: str,
    elig: SwapRetryEligibility,
    window: ConsentWindow,
    record: dict[str, Any],
    samples: list[dict[str, Any]],
    out: Callable[[str], None],
    now: Callable[[], float],
    do_sleep: Callable[[float], None],
) -> None:
    """Run the ONE vision retry attempt inside its own bounded window.

    Precondition: re-admission already stopped whisper under the retry
    authority and checked the fresh final conditions. This function
    owns: the evidence archive, the isolated-Ollama lifecycle (never
    adopting a preexisting server), the single narrated vision call,
    the server stop, and the whisper restore + bounded health wait on
    EVERY exit path (success, failure, interrupt). Terminal state and
    the attempt record land in ``record``.
    """
    started_iso = utc_now_iso()
    started = now()
    archive = _archive_original_vision_attempt(state, video_id, elig, window)
    if archive is None:
        # Never reached in practice (eligibility read the manifest);
        # honest fail-closed rather than an ungated attempt.
        raise _SwapRetryBlocked(
            "the durable manifest disappeared before the retry attempt; "
            "the retry is not executed and progress is blocked"
        )
    stopped_info = plan.whisper_info
    assert stopped_info is not None  # narrowing: readmission verified it
    handle: Any = None
    restore_ok = False
    outcome: Any = None
    attempt_ran = False
    attempt_interrupted = False
    blocked: _SwapRetryBlocked | None = None
    try:
        if stages.ollama_reachable():
            record["state"] = "retry_not_executed_pending"
            message = (
                "isolated Ollama already answers on "
                f"{config.OLLAMA_API_BASE}: the retry never adopts a "
                "preexisting server; stop it explicitly and re-invoke. "
                "Video stays pending; the retry consent is exhausted for "
                "this invocation"
            )
            record["message"] = message
            out(message)
            return
        handle = stages.ollama_start()
        stages.ollama_wait_ready(handle)
        out(
            "vision retry attempt 2 begins: vision-only re-run with a "
            "fresh immediate baseline; gates unchanged"
        )
        attempt_ran = True
        try:
            outcome = narrated.vision(video_id, state)
        except KeyboardInterrupt:
            attempt_interrupted = True
            raise
    finally:
        if handle is not None:
            try:
                stages.ollama_stop(handle)
                out("isolated Ollama stopped cleanly (retry window)")
            except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
                record["ollama_stop_failed"] = str(exc)
                out(
                    f"ISOLATED OLLAMA STOP FAILED (retry window): {exc}; "
                    "the server this run started may still be running — "
                    "stop it explicitly "
                    f"(pid file {config.OLLAMA_PID_PATH})"
                )
        # Whisper restore on EVERY exit path, under the retry consent's
        # named authority (D-03/D-05): no shared service is left down.
        try:
            stages.whisper_restore(stopped_info)
            healthy, detail = _wait_whisper_healthy_for_retry(
                stages, now=now, do_sleep=do_sleep
            )
            if not healthy:
                raise _SwapRetryBlocked(
                    "whisper restoration after the retry window did not "
                    "become healthy: ALL further progress is blocked; "
                    "verify the service state explicitly; no rollback is "
                    "performed or implied"
                )
            restore_ok = True
            out("whisper restored healthy after the retry window")
        except _SwapRetryBlocked as exc:
            # The unrestored shared service is the most severe honest
            # fact: capture it so the attempt's own evidence is persisted
            # first, then let it propagate (it outranks the interrupt
            # report — an unrestored service blocks everything).
            blocked = exc
        except KeyboardInterrupt:
            if not attempt_interrupted:
                # An interrupt inside the restore itself: its outcome is
                # UNKNOWN — record honestly and let it propagate.
                record["restore_unknown_interrupt"] = True
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
            blocked = _SwapRetryBlocked(
                f"whisper restoration after the retry window failed "
                f"({exc}): ALL further progress is blocked; verify the "
                "service state explicitly; no rollback is performed or "
                "implied"
            )
        # Persist the attempt's own evidence on EVERY path where the
        # vision attempt actually started (success, failure, interrupt,
        # later restore failure) — append-only, never a replacement.
        if attempt_ran:
            finished_iso = utc_now_iso()
            outcome_value = (
                str(getattr(outcome, "outcome", outcome))
                if outcome is not None
                else "interrupted_unknown"
            )
            attempt: dict[str, Any] = {
                "started_at": started_iso,
                "finished_at": finished_iso,
                "duration_seconds": round(now() - started, 3),
                "outcome": outcome_value,
                "reason": (
                    str(getattr(outcome, "reason", ""))
                    if outcome is not None
                    else "vision retry interrupted; outcome UNKNOWN"
                ),
                "model": config.VISION_MODEL,
                "cooldown_samples": list(samples),
                "restore_verified_healthy": restore_ok,
            }
            record["attempt"] = attempt
            _record_swap_retry_attempt(state, video_id, attempt)
    if blocked is not None:
        raise blocked
    finished_iso = utc_now_iso()
    outcome_value = str(getattr(outcome, "outcome", outcome))
    attempt: dict[str, Any] = {
        "started_at": started_iso,
        "finished_at": finished_iso,
        "duration_seconds": round(now() - started, 3),
        "outcome": outcome_value,
        "reason": str(getattr(outcome, "reason", "")),
        "model": config.VISION_MODEL,
        "cooldown_samples": list(samples),
        "restore_verified_healthy": restore_ok,
    }
    record["attempt"] = attempt
    _record_swap_retry_attempt(state, video_id, attempt)
    if outcome_value == "complete":
        record["state"] = "retry_complete"
        message = (
            "swap-retry complete: vision re-ran successfully with gates "
            "green; the video's remaining stages (audio/synthesis/verify) "
            "stay pending for their own authorization/invocation"
        )
    else:
        record["state"] = "retry_exhausted"
        message = (
            "retry exhausted: no third attempt this invocation; video "
            "stays pending (resumable by re-invoking)"
        )
    record["message"] = message
    out(message)


def _run_swap_retry_flow(
    *,
    state: StateRoot,
    stages: StageFunctions,
    narrated: StageFunctions,
    human: ConsentProvider,
    human_decisions: list[dict[str, Any]],
    plan: RunPlan,
    batch_ids: Sequence[str],
    offered: set[str],
    out: Callable[[str], None],
    report: dict[str, Any],
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    vmstat_fn: Callable[[], Any] | None = None,
) -> None:
    """Offer the bounded swap-retry for every eligible id of ONE batch.

    Called after a batch whose whisper restoration succeeded. Order per
    video (FR-05): verify the SAME whisper service restored healthy
    FIRST, then ask the DEDICATED consent, then run the read-only
    cooldown with the callback-based re-admission, then the single
    vision-only attempt. Exactly ONE ask per video per invocation
    (denial included); a failed second attempt ends the matter.
    """
    if not stages.whisper_lifecycle_ready():
        return
    if plan.whisper_info is None:
        out(
            "note: no whisper identity was bound for this run; the "
            "bounded swap-retry (which must restore and re-stop the SAME "
            "service) is not offered"
        )
        return
    now = monotonic or time.monotonic
    do_sleep = sleep or time.sleep
    vmstat = vmstat_fn or default_vmstat_fn
    for video_id in batch_ids:
        if video_id in offered:
            continue
        elig = _swap_retry_eligibility(state, video_id)
        if not elig.eligible:
            continue
        offered.add(video_id)  # ONE ask per video per invocation (D-02)
        record: dict[str, Any] = {
            "id": video_id,
            "state": "retry_eligible",
            "eligible_reason": elig.reason,
            "overshoot_mib": elig.overshoot_mib,
            "scope": dict(elig.scope),
            "consent_granted": None,
            "samples": [],
            "stable_resets": [],
            "attempt": None,
            "message": None,
        }
        report["swap_retries"].append(record)
        out(
            f"swap-retry eligible: id {video_id} failed the vision "
            f"swap_delta gate ({_overshoot_text(elig.overshoot_mib)}) "
            "with verified cleanup"
        )
        # T16 (PRD: ANY state -> interrupted_unknown): pre-consent
        # verification, the consent ask and the whole cooldown sit inside
        # ONE per-video interrupt scope so a KeyboardInterrupt can never
        # propagate with this record still "retry_eligible" and no
        # durable evidence. The inner cooldown handler sets the terminal
        # state first; this outer scope covers the phases BEFORE any
        # mutation (nothing was stopped or started there).
        try:
            # FR-05: window closed + SAME whisper restored healthy
            # BEFORE the prompt (the batch cycle's standing restore did
            # the work; this verifies it positively, read-only).
            verify_error = _verify_whisper_restored_healthy(stages, plan)
            if verify_error is not None:
                record["state"] = "blocked_global"
                record["message"] = verify_error
                report["hard_stop_reason"] = verify_error
                out(verify_error)
                return
            window = _build_swap_retry_window(video_id, elig, plan)
            granted = human.ask(window)
            _record_decision(
                human_decisions,
                window,
                granted,
                "human decision: ONE bounded vision swap-retry (new scope, "
                "never covered by the run-plan grant)",
            )
            record["consent_granted"] = granted
            if not granted:
                record["state"] = "retry_declined_pending"
                message = (
                    "swap-retry declined: video stays pending and resumable "
                    "(re-invoke to be asked again)"
                )
                record["message"] = message
                out(message)
                continue
            # -- read-only cooldown + callback-based re-admission ------
            watcher = SwapQuietWatcher(vmstat_fn=vmstat, monotonic=now)
            out(
                f"vision swap-retry pause: id {video_id} failed swap_delta "
                f"gate ({_overshoot_text(elig.overshoot_mib)}, cleanup "
                "verified). Whisper restored healthy; isolated server "
                "stopped. Waiting for swap to quiet: sampling every "
                f"{watcher.interval_seconds:.0f}s, admission no earlier "
                f"than {watcher.floor_seconds:.0f}s, hard stop at "
                f"{watcher.cap_seconds:.0f}s. Nothing else runs meanwhile."
            )
            samples: list[dict[str, Any]] = record["samples"]
            next_narration = config.SWAP_RETRY_NARRATION_INTERVAL_SECONDS
            above_narrated = False
            phase = "cooldown"
            try:
                watcher.start()
                terminal = None
                while terminal is None:
                    if (
                        not watcher.admitted
                        and watcher.remaining_seconds <= 0
                    ):
                        terminal = "cooldown_expired"
                        break
                    do_sleep(
                        min(
                            watcher.interval_seconds,
                            watcher.remaining_seconds,
                        )
                    )
                    sample = watcher.sample(phase)
                    samples.append(sample.to_dict())
                    if watcher.admitted:
                        out(
                            f"[{sample.elapsed_seconds:.0f}s] swap "
                            f"{format_rate_mib_per_s(sample.rate_bytes_per_s)} "
                            f"— stable {watcher.stable_count}/"
                            f"{watcher.stable_intervals_needed} at "
                            f"{watcher.floor_seconds:.0f}s floor: admitting "
                            "retry"
                        )
                        status, reason = _swap_retry_readmission(
                            state=state,
                            stages=stages,
                            plan=plan,
                            elig=elig,
                            watcher=watcher,
                            samples=samples,
                            out=out,
                            now=now,
                            do_sleep=do_sleep,
                        )
                        if status == "ok":
                            # The 180 s TOTAL cap (never restarted, D-04/
                            # D-06) also gates the LAUNCH: re-admission
                            # transitions (bounded stop grace, health
                            # waits, probes) consume real time, and past
                            # the ORIGINAL deadline the attempt must not
                            # start. Whisper is STOPPED here (readmission
                            # stopped it) — restore it under the retry
                            # authority before expiring honestly.
                            if watcher.expired:
                                _restore_whisper_after_drift(
                                    stages,
                                    plan.whisper_info,
                                    now=now,
                                    do_sleep=do_sleep,
                                    context=(
                                        "a re-admission that consumed the "
                                        "remaining cooldown cap"
                                    ),
                                )
                                record["state"] = "cooldown_expired"
                                message = (
                                    f"cap exhausted during re-admission "
                                    f"transitions: retry not executed, "
                                    "video pending (resumable by "
                                    "re-invoking). Whisper restored "
                                    "healthy."
                                )
                                record["message"] = message
                                out(message)
                                terminal = record["state"]
                                break
                            _execute_swap_retry(
                                state=state,
                                stages=stages,
                                narrated=narrated,
                                plan=plan,
                                video_id=video_id,
                                elig=elig,
                                window=window,
                                record=record,
                                samples=samples,
                                out=out,
                                now=now,
                                do_sleep=do_sleep,
                            )
                            terminal = record["state"]
                            break
                        if status == "new_authorization":
                            assert reason is not None
                            record["state"] = "retry_not_executed_pending"
                            record["message"] = reason
                            out(reason)
                            terminal = record["state"]
                            break
                        # Drift (FR-11): reset the count, NEVER the cap;
                        # resampling continues inside the SAME 180 s
                        # budget (the readmission already restored whisper
                        # where it had stopped it).
                        assert reason is not None
                        out(reason)
                        record["stable_resets"].append(
                            {
                                "elapsed_seconds": round(
                                    sample.elapsed_seconds, 3
                                ),
                                "reason": reason,
                            }
                        )
                        watcher.reset_stability()
                        phase = "resample"
                        continue
                    if (
                        sample.elapsed_seconds + 1e-9 >= next_narration
                    ):
                        rate_text = format_rate_mib_per_s(
                            sample.rate_bytes_per_s
                        )
                        remaining = max(
                            0.0,
                            watcher.cap_seconds - sample.elapsed_seconds,
                        )
                        if sample.rate_bytes_per_s is not None and (
                            sample.rate_bytes_per_s
                            > watcher.rate_limit_bytes_per_s
                        ):
                            mood = (
                                "above "
                                f"{config.SWAP_RETRY_COOLDOWN_RATE_LIMIT_MIB_PER_S:.2f} MiB/s"
                                if not above_narrated
                                else "NOT recovered"
                            )
                            above_narrated = True
                            out(
                                f"[{sample.elapsed_seconds:.0f}s] swap "
                                f"{rate_text} — {mood} (stable "
                                f"{watcher.stable_count}/"
                                f"{watcher.stable_intervals_needed}), "
                                f"{remaining:.0f}s left"
                            )
                        else:
                            out(
                                f"[{sample.elapsed_seconds:.0f}s] swap "
                                f"{rate_text} — stable "
                                f"{watcher.stable_count}/"
                                f"{watcher.stable_intervals_needed}, "
                                f"{remaining:.0f}s left"
                            )
                        next_narration += (
                            config.SWAP_RETRY_NARRATION_INTERVAL_SECONDS
                        )
                    if watcher.expired:
                        terminal = "cooldown_expired"
                        break
                # Only the SAMPLING expiry writes the generic message
                # here; the re-admission gate above already recorded its
                # own state and message (record state is terminal).
                if (
                    terminal == "cooldown_expired"
                    and record["state"] == "retry_eligible"
                ):
                    record["state"] = "cooldown_expired"
                    message = (
                        f"cap reached without "
                        f"{watcher.stable_intervals_needed} stable intervals: "
                        "retry not executed, video pending (resumable by "
                        "re-invoking). Whisper remains healthy."
                    )
                    record["message"] = message
                    out(message)
            except SwapCooldownMeasurementError as exc:
                record["state"] = "measurement_unavailable"
                message = (
                    f"swap measurement unavailable: retry not executed, video "
                    f"pending (resumable by re-invoking) — {exc}"
                )
                record["message"] = message
                out(message)
                continue
            except _SwapRetryBlocked as exc:
                record["state"] = "blocked_global"
                record["message"] = exc.reason
                report["hard_stop_reason"] = exc.reason
                out(exc.reason)
                return
            except KeyboardInterrupt:
                record["state"] = "interrupted_unknown"
                message = (
                    "swap-retry interrupted: the video's retry state is "
                    "UNKNOWN for this invocation (nothing is assumed); the "
                    "applicable whisper restoration ran under the retry "
                    "authority unless its own report above says otherwise; "
                    "partial results are preserved by the stages"
                )
                record["message"] = message
                out(message)
                raise
        except KeyboardInterrupt:
            # T16 pre-mutation phases (read-only verification / consent
            # ask): the inner handler already set interrupted_unknown
            # when the interrupt landed inside the cooldown or attempt;
            # here nothing has been stopped or started yet.
            if record["state"] == "retry_eligible":
                record["state"] = "interrupted_unknown"
                message = (
                    "swap-retry interrupted before any mutation "
                    "(verification/consent phase): nothing was stopped or "
                    "started; the video's retry state is UNKNOWN for this "
                    "invocation; re-invoke to be asked again"
                )
                record["message"] = message
                out(message)
            raise


def run_collection(
    *,
    collection_url: str,
    state: StateRoot,
    consent: ConsentProvider | None = None,
    out: Callable[[str], None] | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    declared_count: int | None = None,
    end_evidence: str | None = None,
    browser_controller: Any | None = None,
    browser_profile_base: Path | str | None = None,
    api_transport: Callable[..., Any] | None = None,
    env: Mapping[str, str] | None = None,
    podman_runner: Callable[..., Any] | None = None,
    stages: StageFunctions | None = None,
    swap_retry_monotonic: Callable[[], float] | None = None,
    swap_retry_sleep: Callable[[float], None] | None = None,
    swap_retry_vmstat_fn: Callable[[], Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """The Phase 0 one-command journey for ONE collection URL."""
    out = out or (lambda text: print(text, file=sys.stderr))
    consent = consent or ConsoleConsentProvider()
    env = dict(env if env is not None else os.environ)
    ref = parse_collection_url(collection_url)

    from .runtime import default_urlopen, subprocess_runner

    api_transport = api_transport or default_urlopen
    podman_runner = podman_runner or subprocess_runner
    api_config, missing_env = load_text_api_config(env)

    preflight_report = preflight(
        state=state,
        collection_url=collection_url,
        env=env,
        browser_controller_injected=browser_controller is not None,
    )

    if dry_run:
        scan = None
        try:
            _, scan = resolve_collection(state, collection_url)
        except CollectionError:
            scan = None
        plan_view: dict[str, Any] = {"scan_state_present": scan is not None}
        if scan is not None:
            plan = build_collection_plan(state, scan)
            plan_view.update(
                {
                    "scan_status": plan.scan_status,
                    "declared_count": plan.declared_count,
                    "observed_count": plan.observed_count,
                    "counts": plan.counts,
                    "first_batch_would_be": [
                        item.stable_id
                        for item in plan.items
                        if item.classification in ELIGIBLE_CLASSIFICATIONS
                    ][: (limit or GUIDED_MAX_BATCH)],
                }
            )
        return 0, {
            "mode": "dry-run",
            "preflight": preflight_report,
            "plan": plan_view,
            "notes": [
                "dry run: zero browser, zero network, zero containers, "
                "zero GPU, zero API calls, zero writes",
            ],
        }

    human_decisions: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "mode": "execute",
        "collection_url": ref.collection_url,
        "collection_key": ref.collection_key,
        "state_root": str(state.root),
        "preflight": preflight_report,
        "browser": None,
        "run_plan": None,
        "batches": [],
        "verification": None,
        "swap_retries": [],
        "run_authorization": {
            "human_decisions": human_decisions,
            "stage_windows": [],
        },
        "interrupted": False,
        "hard_stop_reason": None,
    }
    exit_code = 0

    # Phase 1: browser inventory — HUMAN DECISION 1 (the browser grant,
    # which also names the readiness checkpoint and the capture/audit
    # writes) plus the non-authorizing press-Enter readiness signal.
    browser_consent = _RecordingConsent(
        consent, human_decisions, basis="human decision: browser grant"
    )
    try:
        browser_report = run_browser_inventory(
            state=state,
            ref=ref,
            consent=browser_consent,
            out=out,
            controller=browser_controller,
            profile_base=browser_profile_base,
            declared_count=declared_count,
            end_evidence=end_evidence,
        )
        report["browser"] = browser_report.to_dict()
    except PlaywrightMissingError as exc:
        report["hard_stop_reason"] = f"preflight: {exc}"
        report["batches"] = []
        return 1, report
    except BrowserInventoryError as exc:
        report["hard_stop_reason"] = f"browser inventory did not run: {exc}"
        return 1, report

    # Phase 2: freeze the inventory, bind the whisper identity read-only
    # and ask HUMAN DECISION 2 — the ONE whole-current-run plan grant.
    # The stage boundary is injectable; production defaults are built
    # here (real stages + whisper lifecycle hooks), and the fixture
    # suite injects doubles so NOTHING real ever runs in tests.
    stages_exec = stages or build_stage_functions(
        api_transport=api_transport,
        podman_runner=podman_runner,
    )
    narrated = _narrated_stages(stages_exec, out)
    frozen_ids, canonical_urls, stage_states, plan_counts = (
        _frozen_inventory(state, collection_url)
    )
    report["final_plan_counts"] = plan_counts
    cap = limit if limit is not None else GUIDED_MAX_BATCH

    authorization: RunScopedAuthorization | None = None
    if not frozen_ids:
        out(
            "no eligible items after the inventory: nothing to authorize "
            "or process (completed/blocklisted items are skipped by the "
            "plan itself)"
        )
    else:
        bound_info, whisper_note = _bind_whisper_identity(stages_exec, out)
        run_plan = RunPlan(
            collection_url=ref.collection_url,
            frozen_ids=frozen_ids,
            canonical_urls=canonical_urls,
            stage_states=stage_states,
            batch_cap=cap,
            batch_count=math.ceil(len(frozen_ids) / cap),
            plan_counts=plan_counts,
            whisper_bound=bound_info.describe() if bound_info is not None else None,
            whisper_note=whisper_note,
            text_api_origin=api_config.origin() if api_config else None,
            text_api_model=api_config.model if api_config else None,
            whisper_info=bound_info,
        )
        plan_window = build_run_plan_window(run_plan)
        plan_granted = consent.ask(plan_window)
        _record_decision(
            human_decisions,
            plan_window,
            plan_granted,
            "human decision: whole-current-run plan grant",
        )
        report["run_plan"] = _run_plan_dict(run_plan, plan_granted)
        if not plan_granted:
            report["hard_stop_reason"] = (
                "run plan denied: no downloads, containers, models, API "
                "calls or writes ran; only the already-authorized browser "
                "inventory happened; re-invoke to be asked again"
            )
            exit_code = 1
        else:
            authorization = RunScopedAuthorization(
                human=consent, plan=run_plan, out=out
            )
            report["run_authorization"]["stage_windows"] = (
                authorization.authorization_log
            )

    if api_config is None:
        out(
            "note: no text API configured (missing "
            + ", ".join(missing_env)
            + "); synthesis will stop at a blocked, resumable state"
        )
    auto_synthesizer = (
        make_auto_synthesizer(api_config, transport=api_transport, out=out)
        if api_config is not None
        else None
    )

    # Phase 3: batches are FIXED SLICES of the frozen inventory — each
    # frozen id is attempted in exactly ONE batch (a failed id is never
    # reattempted this invocation) and the loop is bounded by
    # construction: the remaining list strictly shrinks, so there is no
    # re-asking loop and nothing outside the frozen scope is absorbed.
    remaining = list(frozen_ids)
    batch_index = 0
    # Phase 0 addendum: per-invocation swap-retry bookkeeping. ONE ask
    # per video per invocation (denial included) — enforced here, never
    # as a lifetime StageRecord.attempts rejection (FR-17).
    swap_retry_offered: set[str] = set()
    if authorization is not None:
        try:
            while remaining:
                batch = remaining[:cap]
                del remaining[: len(batch)]
                batch_index += 1
                out(
                    f"collection batch {batch_index}/{run_plan.batch_count}: "
                    f"{len(batch)} id(s) ({', '.join(batch)}); "
                    f"{len(remaining)} frozen item(s) in later batches"
                )
                batch_exit, batch_report = run_guided(
                    state=state,
                    collection=collection_url,
                    ids=batch,
                    limit=cap,
                    stages=narrated,
                    consent=authorization,
                    out=out,
                    auto_synthesizer=auto_synthesizer,
                )
                report["batches"].append(
                    {
                        "ids": list(batch),
                        "exit_code": batch_exit,
                        "selected": batch_report.get("selected"),
                        "excluded": batch_report.get("excluded"),
                        "notes": batch_report.get("notes"),
                        "failures": batch_report.get("failures"),
                        "cleanup_notes": batch_report.get("cleanup_notes"),
                        "consent_log": batch_report.get("consent_log"),
                        "stage_outcomes_this_run": batch_report.get(
                            "stage_outcomes_this_run"
                        ),
                        "waiting_for_synthesis": batch_report.get(
                            "waiting_for_synthesis"
                        ),
                        "interrupted": batch_report.get("interrupted"),
                        "whisper_restore_failed": batch_report.get(
                            "whisper_restore_failed"
                        ),
                    }
                )
                if batch_exit != 0:
                    exit_code = 1
                if batch_report.get("waiting_for_synthesis"):
                    # Honest incompleteness: synthesis cannot proceed in
                    # this invocation (missing config or a resumable
                    # stop); the journey is not finished.
                    exit_code = 1
                if batch_report.get("interrupted"):
                    report["interrupted"] = True
                    report["hard_stop_reason"] = (
                        "interrupted: partial results are preserved by the "
                        "stages; re-invocation requires new authorization "
                        "for pending operations"
                    )
                    break
                if batch_report.get("whisper_restore_failed"):
                    report["hard_stop_reason"] = (
                        "whisper restoration failed: ALL further progress is "
                        "blocked; verify the service state explicitly; no "
                        "rollback is performed or implied"
                    )
                    exit_code = 1
                    break

                # Phase 0 addendum: the bounded swap-retry sub-flow for
                # this batch's eligible ids. Subsequent frozen batches
                # reconcile EXACTLY as the pre-existing driver does —
                # the retry adds no batch, no window and no model load
                # beyond the ONE separately-consented retry attempt,
                # and retry exhaustion is video/window-scoped (never a
                # global hard stop; D-08/OQ-03).
                _run_swap_retry_flow(
                    state=state,
                    stages=stages_exec,
                    narrated=narrated,
                    human=consent,
                    human_decisions=human_decisions,
                    plan=run_plan,
                    batch_ids=batch,
                    offered=swap_retry_offered,
                    out=out,
                    report=report,
                    monotonic=swap_retry_monotonic,
                    sleep=swap_retry_sleep,
                    vmstat_fn=swap_retry_vmstat_fn,
                )
                if report["hard_stop_reason"] is not None:
                    exit_code = 1
                    break

            # Phase 4: ONE grouped verification authorization covering
            # every ready id's exact validated candidate URLs. Skipped
            # after ANY nonrecoverable stop — an interruption OR a hard
            # stop such as a failed whisper restore (whose invariant is
            # "ALL further progress is blocked", verification and
            # backlog writes included): pending work waits for a new
            # invocation and its own new authorization.
            if (
                not report["interrupted"]
                and report["hard_stop_reason"] is None
            ):
                if not _run_grouped_verification(
                    state=state,
                    narrated=narrated,
                    consent=consent,
                    human_decisions=human_decisions,
                    frozen_ids=frozen_ids,
                    out=out,
                    report=report,
                ):
                    exit_code = 1
        except KeyboardInterrupt:
            report["interrupted"] = True
            report["hard_stop_reason"] = (
                "interrupted between batches: partial results are preserved "
                "by the stages; re-invocation requires new authorization "
                "for pending operations"
            )
            exit_code = 1

    # Aggregate view ONLY (derived from the product's stores now).
    _, final_counts = _eligible_ids(state, collection_url)
    report["final_plan_counts"] = final_counts
    report["backlog_pending_view"] = len(
        [
            entry
            for entry in Backlog(state).entries()
            if entry.get("status") == "pending"
        ]
    )
    report["review_next"] = (
        "verify appends PENDING entries only; decide with the existing "
        "review CLI: backlog-list / backlog-show / backlog-decide "
        "--decisions-file F / backlog-plan / backlog-apply"
    )
    report["complete_is_not_confirmed"] = (
        "a stage outcome of 'complete' means the machinery finished "
        "honestly; claim verdict 'confirmed' can only come from the "
        "verification matrix"
    )
    return exit_code, report


__all__ = [
    "CollectionRunError",
    "RunPlan",
    "RunScopedAuthorization",
    "build_run_plan_window",
    "build_stage_functions",
    "make_auto_synthesizer",
    "preflight",
    "run_collection",
]
