"""``collection-run``: one command from a collection URL to pending entries.

Phase 0 application layer (ROADMAP work packages 0.1-0.4). This module
COORDINATES; it implements no stage logic of its own:

- The browser inventory comes from :mod:`tiktok_ingest.browser`
  (headed, run-scoped profile, explicit operator confirmation,
  existing collection contracts).
- Each batch is driven by the EXISTING :func:`guided.run_guided`
  coordinator with its ``GuidedRun``/``StageFunctions`` seams — there
  is no second stage ledger, no copied stage logic, and every consent
  window, gate, fingerprint and cleanup rule of the guided coordinator
  applies unchanged.
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

Batch loop honesty: the plan is recomputed FRESH from current state
before every batch (a previously printed plan is never authority);
``new_processable`` and ``partial_resumable`` ids are eligible, capped
at the batch limit (hard maximum 5); completed and blocklisted ids are
skipped; the loop stops when nothing is eligible or when a batch makes
no NEW progress (denials/blocks loop-guard instead of re-asking
forever). An aggregate progress view is derived from the product's
stores at report time — it is a view, never a second state authority.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
from .guided import (
    GUIDED_MAX_BATCH,
    ConsentProvider,
    ConsentWindow,
    ConsoleConsentProvider,
    StageFunctions,
    _run_dir_of,
    default_stage_functions,
    run_guided,
)
from .state import Backlog, StateRoot
from .synthesis import run_synthesis_stage
from .synthesis_api import (
    TextApiConfig,
    build_evidence_payload,
    build_request_messages,
    load_text_api_config,
    read_evidence_text,
    request_json_document,
    sanitize_evidence_text,
)
from .whisper_lifecycle import (
    inspect_whisper,
    restore_whisper,
    stop_whisper,
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

        return run_synthesis_stage(video_id, state=state, document_fn=document_fn)

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


def _batch_progressed(report: dict[str, Any], batch: Sequence[str]) -> bool:
    """True when at least one id gained NEW completed work (not reuse)."""
    outcomes = report.get("stage_outcomes_this_run") or {}
    for video_id in batch:
        for record in (outcomes.get(video_id) or {}).values():
            if (
                isinstance(record, dict)
                and record.get("outcome") == "complete"
                and not record.get("reused", False)
            ):
                return True
    return False


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

    report: dict[str, Any] = {
        "mode": "execute",
        "collection_url": ref.collection_url,
        "collection_key": ref.collection_key,
        "state_root": str(state.root),
        "preflight": preflight_report,
        "browser": None,
        "batches": [],
        "interrupted": False,
        "hard_stop_reason": None,
    }
    exit_code = 0

    # Phase 1: browser inventory (consent-gated inside).
    try:
        browser_report = run_browser_inventory(
            state=state,
            ref=ref,
            consent=consent,
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

    # Phase 2: batches through the EXISTING guided coordinator. The
    # stage boundary is injectable; production defaults are built here
    # (real stages + whisper lifecycle hooks), and the fixture suite
    # injects doubles so NOTHING real ever runs in tests.
    stages = stages or build_stage_functions(
        api_transport=api_transport,
        podman_runner=podman_runner,
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

    previous_batch: list[str] | None = None
    previous_progressed = True
    try:
        while True:
            eligible, counts = _eligible_ids(state, collection_url)
            report.setdefault("final_plan_counts", counts)
            if not eligible:
                break
            cap = limit if limit is not None else GUIDED_MAX_BATCH
            batch = eligible[:cap]
            if batch == previous_batch and not previous_progressed:
                report["hard_stop_reason"] = (
                    "loop guard: the last batch made no new progress (denials "
                    "or blocks); stopping instead of re-asking; resolve the "
                    "reported blocks and re-invoke for a NEW authorization "
                    "window"
                )
                exit_code = 1
                break
            out(
                f"collection batch: {len(batch)} id(s) "
                f"({', '.join(batch)}); {max(0, len(eligible) - len(batch))} "
                "further eligible item(s) stay pending for later batches"
            )
            batch_exit, batch_report = run_guided(
                state=state,
                collection=collection_url,
                ids=batch,
                limit=cap,
                stages=stages,
                consent=consent,
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
            progressed = _batch_progressed(batch_report, batch)
            previous_batch, previous_progressed = batch, progressed
            if batch_report.get("interrupted"):
                report["interrupted"] = True
                report["hard_stop_reason"] = (
                    "interrupted: partial results are preserved by the stages; "
                    "re-invocation requires new authorization for pending "
                    "operations"
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
    except KeyboardInterrupt:
        report["interrupted"] = True
        report["hard_stop_reason"] = (
            "interrupted between batches: partial results are preserved by "
            "the stages; re-invocation requires new authorization for "
            "pending operations"
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
    "build_stage_functions",
    "make_auto_synthesizer",
    "preflight",
    "run_collection",
]
