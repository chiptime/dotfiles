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
- Every model call goes to the RESIDENT llm-hub services (llama-chat
  for vision, llm-hub-whisper for audio): no container lifecycle, GPU
  lease or swap gating is coordinated here any more.

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
   five, the synthesis endpoint/model, and the retry policy (each
   id/operation attempted at most once this invocation). Stage windows
   provably inside that scope are discharged by
   :class:`RunScopedAuthorization` and audited as
   ``covered_by_run_plan`` — never fabricated human yeses; anything
   else (unknown kinds, excess ids, unapproved destinations) is
   forwarded to the human as genuinely new scope and never
   auto-granted.
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
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
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
    CollectionRef,
    build_collection_plan,
    parse_collection_url,
    resolve_collection,
)
from .contracts import utc_now_iso
from .engine import run_dir_of
from .guided import (
    GUIDED_MAX_BATCH,
    ConsentProvider,
    ConsentWindow,
    ConsoleConsentProvider,
    GuidedRun,
    StageFunctions,
    default_stage_functions,
    derive_item_stages,
    narrate_stages,
    select_ids,
)
from .state import (
    Backlog,
    StateRoot,
    atomic_write_json,
)
from .synthesis import SynthesisDocument, run_synthesis_stage
from .synthesis_api import (
    TextApiConfig,
    build_evidence_payload,
    build_request_messages,
    load_text_api_config,
    normalize_automatic_synthesis,
    read_evidence_text,
    request_json_document,
    sanitize_evidence_text,
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
    rescan: bool = False,
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
        "rescan": rescan,
        "batch_cap": GUIDED_MAX_BATCH,
        "exclusive_writer": (
            "this run is a writer like any other: run it in an "
            "exclusive-writer operational window — never concurrently "
            "with guided-run, verify emissions or the review CLI"
        ),
        "planned_destinations": [
            "headed Playwright browser (inventory only; run-scoped profile)",
            "gated pinned-extractor downloads of the selected TikTok URLs",
            "the resident llm-hub services on this machine (chat "
            f"{config.LLM_HUB_CHAT_API_BASE} for vision, whisper WS "
            f"{config.WHISPER_WS_URL} for audio)",
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
            entry_run_dir = run_dir_of(state, video_id)
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
            document = request_json_document(api_config, messages, transport=transport)
            return normalize_automatic_synthesis(document)

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


@dataclasses.dataclass(frozen=True)
class RunPlan:
    """The scope granted by the ONE whole-current-run authorization.

    ``frozen_ids``/``canonical_urls``/``stage_states`` capture the
    frozen inventory. Model inference needs no service binding any
    more: the resident llm-hub units are named by configuration, not
    stopped/restored per run.
    """

    collection_url: str
    frozen_ids: tuple[str, ...]
    canonical_urls: dict[str, str]
    stage_states: dict[str, dict[str, str]]
    batch_cap: int
    batch_count: int
    plan_counts: dict[str, int]
    text_api_origin: str | None
    text_api_model: str | None
    retry_failed: int = 1


def build_run_plan_window(
    plan: RunPlan,
    *,
    sequence: str | None = None,
    auto_verify: bool = False,
) -> ConsentWindow:
    """Human decision 2: accept the WHOLE current run exactly as named.

    ``sequence`` lets a caller name its own honest stage order (guided-run
    processes one id at a time) instead of the Phase 0 phase-first text;
    the default keeps collection-run's wording unchanged.
    """
    destinations: list[str] = [
        plan.canonical_urls.get(video_id, f"(no canonical URL recorded for {video_id})")
        for video_id in plan.frozen_ids
    ]
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
        sequence
        or (
            "process exactly the frozen ids above through preparation ALL -> "
            "audio ALL -> vision ALL -> eligible synthesis ALL -> grouped verify; "
            f"fixed resource slices of at most {plan.batch_cap} "
            f"({plan.batch_count} slice(s)) are visited once per phase, not "
            "complete-pipeline batches"
        ),
        "gated pinned-extractor download of exactly the frozen canonical "
        "URLs listed above (oEmbed metadata per URL), CPU preparation in "
        "the pinned container, vision through the RESIDENT llm-hub chat "
        f"service ({config.LLM_HUB_CHAT_API_BASE}, model "
        f"{config.VISION_MODEL}) and audio through the resident "
        "llm-hub-whisper WS service; audio requires prepared media, not "
        "vision completion; no model is loaded, unloaded, started or "
        "stopped by this run",
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
        (
            (
                "bounded safe verification of candidate URLs will run automatically "
                "under safe-URL rules (anti-SSRF, max 2MB, public http/https only) "
                "without an extra prompt"
            )
            if auto_verify
            else (
                "verification candidate URLs are unknown until synthesis: ONE "
                "additional grouped authorization will show every exact "
                "validated URL before any retrieval — nothing is verified "
                "before that separate decision"
            )
        ),
    )
    retry_policy = (
        f"failed items within a phase may be retried up to {plan.retry_failed} "
        "time(s) before advancing to the next phase; resource gates remain final"
        if getattr(plan, "retry_failed", 0) > 0
        else "a failed id is never reattempted this invocation (safe stop; resumable by re-invoking)"
    )
    limits = (
        f"each frozen id is attempted in exactly ONE batch of at most "
        f"{plan.batch_cap}: {retry_policy}",
        "no automatic retry of any operation; a failed model request is "
        "final for its window and its remaining ids stay pending; "
        "later frozen normal windows retain their existing attempt policy",
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
        "text_api": (
            {"origin": plan.text_api_origin, "model": plan.text_api_model}
            if plan.text_api_origin
            else None
        ),
        "retry_failed": getattr(plan, "retry_failed", 1),
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


class _AutoGrantedConsent(ConsentProvider):
    """Auto-grants the browser window while preserving await_readiness."""

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
        _record_decision(self.log, window, True, self.basis)
        return True

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
        allow_verify: bool = False,
    ) -> None:
        self.human = human
        self.plan = plan
        self.authorization_log: list[dict[str, Any]] = []
        self._out = out or (lambda _text: None)
        self.allow_verify = allow_verify

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
            if self.allow_verify:
                self._record(
                    window,
                    "covered_by_run_plan",
                    "bounded safe verification authorized in run plan",
                    True,
                )
                self._out(
                    "authorization: verify window covered by the run plan grant (no prompt)"
                )
                return True
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


# The Phase 0 narration stage set: the synthesis boundary narrates itself
# through the auto-synthesizer, so it is deliberately not wrapped here.
_PHASE0_NARRATED_STAGES: tuple[str, ...] = (
    "fetch",
    "prepare",
    "vision",
    "audio",
    "verify",
)


def _narrated_stages(
    stages: StageFunctions,
    out: Callable[[str], None],
    *,
    monotonic: Callable[[], float] | None = None,
) -> StageFunctions:
    """Progress narration around each REAL stage call (stderr only).

    Thin delegate to :func:`guided.narrate_stages` with the Phase 0 stage
    set, so the narration contract lives in exactly one place.
    """
    return narrate_stages(
        stages, out, monotonic=monotonic, names=_PHASE0_NARRATED_STAGES
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
        run_dir = run_dir_of(state, video_id)
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
    auto_verify: bool = False,
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
    if auto_verify:
        granted = True
        _record_decision(
            human_decisions,
            window,
            True,
            "human decision: grouped verification authorized upfront in run plan",
        )
    else:
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




def _phase_selection(
    state: StateRoot, ids: Sequence[str], canonical_urls: Mapping[str, str], cap: int
) -> Any:
    """Refresh state and exclusions without widening the frozen selection/URLs."""
    return select_ids(
        state, ids, limit=cap,
        scan_items={video_id: ("video", canonical_urls.get(video_id)) for video_id in ids},
    )


def _merge_phase_report(batch: dict[str, Any], result: dict[str, Any]) -> None:
    """Accumulate invocation evidence, never a second persistent stage ledger."""
    batch["selected"] = result["selected"]
    for name in ("excluded", "notes", "failures", "cleanup_notes", "consent_log"):
        for entry in result[name]:
            if entry not in batch[name]:
                batch[name].append(entry)
    for video_id, outcomes in result["stage_outcomes_this_run"].items():
        batch["stage_outcomes_this_run"].setdefault(video_id, {}).update(outcomes)
    batch["waiting_for_synthesis"] = result["waiting_for_synthesis"]
    batch["interrupted"] = batch.get("interrupted", False) or result[
        "interrupted"
    ]
    if result["failures"] or result["interrupted"]:
        batch["exit_code"] = 1


def _refresh_batch_report(state: StateRoot, batch: dict[str, Any]) -> None:
    """Final views include retry and grouped verification, not the plan snapshot."""
    waiting: list[str] = []
    for item in batch["selected"]:
        states = derive_item_stages(state, item["id"])
        item["stages"] = states
        item["run_dir"] = run_dir_of(state, item["id"])
        if states["audio"] == states["vision"] == "complete" and states["synthesis"] != "complete":
            waiting.append(item["id"])
        if any(value != "complete" for value in states.values()):
            batch["exit_code"] = 1
    batch["waiting_for_synthesis"] = waiting


def _persist_report(state: StateRoot, report: dict[str, Any]) -> Path:
    """Persist one finished run's report under the state root, atomically.

    Reports land under ``<state_root>/reports/<collection_key>/`` named by a
    UTC timestamp consistent with the run directories
    (``2026-10-12T09-55-16Z``). The state root is the ONLY destination, so a
    report is never written inside the repository. The atomic writer leaves
    no partial file on failure.
    """
    collection_key = str(report["collection_key"])
    timestamp = utc_now_iso().replace(":", "-")
    path = state.root / "reports" / collection_key / f"{timestamp}.json"
    atomic_write_json(path, report)
    return path


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
    stages: StageFunctions | None = None,
    auto_verify: bool = False,
    skip_browser_consent: bool = False,
    rescan: bool = False,
    retry_failed: int = 1,
) -> tuple[int, dict[str, Any]]:
    """The Phase 0 one-command journey for ONE collection URL or direct video URL."""
    out = out or (lambda text: print(text, file=sys.stderr))
    consent = consent or ConsoleConsentProvider()
    env = dict(env if env is not None else os.environ)
    is_video = False
    ref = parse_collection_url(collection_url)

    from .runtime import default_urlopen

    api_transport = api_transport or default_urlopen
    api_config, missing_env = load_text_api_config(env)

    preflight_report = preflight(
        state=state,
        collection_url=collection_url,
        env=env,
        browser_controller_injected=browser_controller is not None,
        rescan=rescan,
    )

    existing_scan = None
    try:
        _, existing_scan = resolve_collection(state, collection_url)
    except CollectionError:
        existing_scan = None

    can_resume = (
        not rescan
        and existing_scan is not None
        and len(existing_scan.items) > 0
        and existing_scan.status != "blocked"
    )

    if dry_run:
        scan = existing_scan
        plan_view: dict[str, Any] = {
            "scan_state_present": scan is not None,
            "rescan": rescan,
            "resuming": can_resume,
        }
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
        "phases": [],
        "verification": None,
        "run_authorization": {
            "human_decisions": human_decisions,
            "stage_windows": [],
        },
        "interrupted": False,
        "hard_stop_reason": None,
    }
    exit_code = 0

    # Phase 1: browser inventory — or resume from persisted scan state if already captured.
    if can_resume:
        assert existing_scan is not None
        plan = build_collection_plan(state, existing_scan)
        eligible = [
            item.stable_id
            for item in plan.items
            if item.classification in ELIGIBLE_CLASSIFICATIONS
        ]
        report["browser"] = {
            "reused": True,
            "collection_url": ref.collection_url,
            "observed_items": len(existing_scan.items),
            "scan_status": existing_scan.status,
            "scan_status_reason": existing_scan.status_reason,
            "audit_html_path": None,
            "profile_dir": None,
            "profile_removed": True,
            "cleanup_notes": [
                "reused persisted collection scan state; browser inventory skipped (pass --rescan to force new capture)"
            ],
        }
        if not eligible:
            out(
                f"collection {ref.collection_url} is already fully ingested "
                f"({plan.counts.get('complete', 0)} complete, 0 pending/resumable); "
                "pass --rescan to force new browser capture"
            )
        else:
            out(
                f"resuming collection {ref.collection_url} from existing scan "
                f"({len(eligible)} item(s) pending/resumable); pass --rescan to force new browser capture"
            )
    else:
        if skip_browser_consent:
            browser_consent = _AutoGrantedConsent(
                consent, human_decisions, basis="human decision: browser grant (authorized by command launch)"
            )
        else:
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
            out(f"collection-run report saved: {_persist_report(state, report)}")
            return 1, report
        except BrowserInventoryError as exc:
            report["hard_stop_reason"] = f"browser inventory did not run: {exc}"
            out(f"collection-run report saved: {_persist_report(state, report)}")
            return 1, report

    # Phase 2: freeze the inventory and ask HUMAN DECISION 2 — the ONE
    # whole-current-run plan grant. The stage boundary is injectable;
    # the production default is the plain real-stage bundle (no service
    # lifecycle to wire), and the fixture suite injects doubles so
    # NOTHING real ever runs in tests.
    stages_exec = stages or default_stage_functions()
    narrated = _narrated_stages(stages_exec, out)
    frozen_ids, canonical_urls, stage_states, plan_counts = (
        _frozen_inventory(state, collection_url)
    )
    # Seed a live dashboard (when the consent provider is one) with the
    # persisted stage states of the frozen inventory, so a resumed run
    # starts from the prior state instead of an empty matrix. Optional
    # duck-typed hook: console and test providers simply lack it.
    seed_prior = getattr(consent, "seed_prior_state", None)
    if callable(seed_prior):
        seed_prior(list(stage_states.items()))
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
        run_plan = RunPlan(
            collection_url=ref.collection_url,
            frozen_ids=frozen_ids,
            canonical_urls=canonical_urls,
            stage_states=stage_states,
            batch_cap=cap,
            batch_count=math.ceil(len(frozen_ids) / cap),
            plan_counts=plan_counts,
            text_api_origin=api_config.origin() if api_config else None,
            text_api_model=api_config.model if api_config else None,
            retry_failed=retry_failed,
        )
        plan_window = build_run_plan_window(run_plan, auto_verify=auto_verify)
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
                human=consent, plan=run_plan, out=out, allow_verify=auto_verify
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

    # Fixed resource slices are visited ONCE per phase. The OUTER loop
    # owns the global barriers, so neither an early slice nor a successful
    # retry can reach the text API while any normal vision slice remains.
    batches = [list(frozen_ids[i:i + cap]) for i in range(0, len(frozen_ids), cap)]
    phase_stages = {
        "preparation": ("fetch", "prepare"),
        "audio": ("audio",),
        "vision": ("vision",),
        "synthesis": ("synthesis",),
    }
    if authorization is not None:
        try:
            for phase, required_stages in phase_stages.items():
                evidence: dict[str, Any] = {
                    "phase": phase, "state": "running", "windows": [], "pending_ids": [],
                }
                report["phases"].append(evidence)
                for batch_index, batch_ids in enumerate(batches):
                    out(
                        f"collection phase {phase}: collection batch "
                        f"{batch_index + 1}/{len(batches)}: "
                        f"{len(batch_ids)} frozen id(s) ({', '.join(batch_ids)})"
                    )
                    selection = _phase_selection(state, batch_ids, canonical_urls, cap)
                    # One runner per phase/window.
                    runner = GuidedRun(
                        state, stages=narrated, consent=authorization, out=out,
                        auto_synthesizer=auto_synthesizer,
                    )
                    result = runner.engine.execute_collection_phase(selection, phase)
                    if phase == "preparation":
                        report["batches"].append({
                            "ids": batch_ids, "exit_code": 0, "selected": [],
                            "excluded": [], "notes": [], "failures": [],
                            "cleanup_notes": [], "consent_log": [],
                            "stage_outcomes_this_run": {}, "waiting_for_synthesis": [],
                            "interrupted": False,
                            "phases": [],
                        })
                    batch_report = report["batches"][batch_index]
                    _merge_phase_report(batch_report, result)
                    window_evidence = {
                        "phase": phase, "batch_index": batch_index + 1,
                        "stage_calls": result["stage_calls"],
                        "vision_window_closed": result["vision_window_closed"],
                    }
                    batch_report["phases"].append(window_evidence)
                    evidence["windows"].append(window_evidence)
                    if result["interrupted"]:
                        report["interrupted"] = True
                        report["hard_stop_reason"] = (
                            "interrupted: partial evidence preserved; new "
                            "authorization required"
                        )
                    elif result["progress_blocked_reason"] is not None:
                        report["hard_stop_reason"] = result["progress_blocked_reason"]
                    if report["hard_stop_reason"] is not None:
                        break

                # Intra-phase retries: retry failed items within the same phase
                # up to retry_failed times before advancing to the next phase.
                phase_prerequisites = {
                    "preparation": (),
                    "audio": ("prepare",),
                    "vision": ("prepare",),
                    "synthesis": ("audio", "vision"),
                }
                for retry_index in range(retry_failed):
                    if report["hard_stop_reason"] is not None or report["interrupted"]:
                        break
                    # If vision window was closed by a resource gate or error, do not retry
                    if phase == "vision" and any(w.get("vision_window_closed") for w in evidence["windows"]):
                        break
                    prereqs = phase_prerequisites.get(phase, ())
                    retry_candidates = [
                        video_id for video_id in frozen_ids
                        if all(derive_item_stages(state, video_id).get(prereq) == "complete"
                               for prereq in prereqs)
                        and any(derive_item_stages(state, video_id).get(stage) != "complete"
                                for stage in required_stages)
                    ]
                    if not retry_candidates:
                        break
                    out(
                        f"collection phase {phase}: retrying {len(retry_candidates)} "
                        f"failed item(s) (attempt {retry_index + 1}/{retry_failed}): "
                        f"{', '.join(retry_candidates)}"
                    )
                    retry_batches = [
                        list(retry_candidates[i:i + cap])
                        for i in range(0, len(retry_candidates), cap)
                    ]
                    for r_index, r_ids in enumerate(retry_batches):
                        selection = _phase_selection(state, r_ids, canonical_urls, cap)
                        runner = GuidedRun(
                            state, stages=narrated, consent=authorization, out=out,
                            auto_synthesizer=auto_synthesizer,
                        )
                        result = runner.engine.execute_collection_phase(selection, phase)
                        batch_target = None
                        for b in report["batches"]:
                            if any(vid in b["ids"] for vid in r_ids):
                                batch_target = b
                                break
                        if batch_target is not None:
                            _merge_phase_report(batch_target, result)
                        window_evidence = {
                            "phase": phase,
                            "batch_index": f"retry-{retry_index + 1}.{r_index + 1}",
                            "stage_calls": result["stage_calls"],
                            "vision_window_closed": result["vision_window_closed"],
                        }
                        if batch_target is not None:
                            batch_target["phases"].append(window_evidence)
                        evidence["windows"].append(window_evidence)
                        if result["interrupted"]:
                            report["interrupted"] = True
                            report["hard_stop_reason"] = (
                                "interrupted: partial evidence preserved; new "
                                "authorization required"
                            )
                        elif result["progress_blocked_reason"] is not None:
                            report["hard_stop_reason"] = result["progress_blocked_reason"]
                        if report["hard_stop_reason"] is not None:
                            break

                evidence["pending_ids"] = [
                    video_id for video_id in frozen_ids
                    if any(derive_item_stages(state, video_id)[stage] != "complete"
                           for stage in required_stages)
                ]
                evidence["state"] = (
                    "blocked" if report["hard_stop_reason"] is not None
                    else "ended_with_pending" if evidence["pending_ids"] else "complete"
                )
                out(
                    f"collection phase {phase} ended: {evidence['state']}; "
                    f"{len(evidence['pending_ids'])} frozen id(s) pending"
                )
                if report["hard_stop_reason"] is not None:
                    exit_code = 1
                    break

            # Phase 4: ONE grouped verification authorization covering
            # every ready id's exact validated candidate URLs. Skipped
            # after ANY nonrecoverable stop — an interruption OR a hard
            # boundary failure: pending work waits for a new invocation
            # and its own new authorization.
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
                    auto_verify=auto_verify,
                ):
                    exit_code = 1
        except KeyboardInterrupt:
            report["interrupted"] = True
            report["hard_stop_reason"] = (
                "interrupted between phase windows: partial results are preserved "
                "by the stages; re-invocation requires new authorization "
                "for pending operations"
            )
            exit_code = 1

    for batch_report in report["batches"]:
        _refresh_batch_report(state, batch_report)
        if batch_report["exit_code"]:
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
    out(f"collection-run report saved: {_persist_report(state, report)}")
    return exit_code, report


__all__ = [
    "CollectionRunError",
    "RunPlan",
    "RunScopedAuthorization",
    "build_run_plan_window",
    "make_auto_synthesizer",
    "preflight",
    "run_collection",
]
