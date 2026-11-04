"""Backlog review endpoints over HTTP (S7 + S8, T3: decide/plan/apply).

The pending-review view's write path: the SAME review semantics as the
CLI (P1-FR-11), operated through four endpoints registered on the
existing :class:`tiktok_ingest.web.server.App` by
:func:`register_review`:

- ``GET  /api/backlog/{video_id}``          ``backlog-show`` data
- ``POST /api/backlog/{video_id}/decision``  ``backlog-decide`` (one
  explicit decision, bound to the entry hash the client previewed)
- ``GET  /api/backlog/plan``                 ``backlog-plan`` preview
  plus the plan fingerprint apply must echo
- ``POST /api/backlog/apply``                ``backlog-apply``, bound
  to the previewed plan fingerprint

Everything durable goes through the review module — ``show_entry``,
``record_decision``, ``build_plan``, ``apply_decisions``,
``parse_decisions_file`` — never reimplemented here (P1-FR-01). The
decisions document is the state root's ``reviews/web-decisions.jsonl``
(the CLI's ``--decisions-file`` is an operator choice; the web surface
uses one deterministic, state-anchored location so reloads and the CLI
find the same decisions).

Decision vocabulary: request bodies carry the product's own operator
statuses ``accepted``/``rejected`` (P1-FR-03: the UI renames nothing);
the pending-review view is exactly accept-or-reject (P1-FR-08).

Apply confirmation: the CLI confirms by running ``backlog-apply`` after
inspecting ``backlog-plan``. A browser cannot "run a command after
reading it", so apply requires the client to echo ``plan_sha256`` — the
sha256 of the canonical JSON of the plan document served by
``GET /api/backlog/plan``. Apply recomputes the plan; a fingerprint
mismatch means the previewed plan no longer describes reality and the
request is refused with 409 before any write.

Single writer (S8 / P1-FR-13): while the web runner reports an active
run, decision and apply answer 409 (the runner's public
``active_view()`` is the only runner API used here). Concurrent review
writes through THIS process serialize on one lock. Cross-process
concurrency keeps the review module's honest model: the product has NO
cooperative lock (see review.py's header) — decisions are hash-bound
and apply revalidates before publishing — so an external CLI writer at
the same time remains that documented residual window; this module
invents no new cross-process locking (P1-FR-13 forbids a fabricated
product-wide lock).

No automatic decisions (P1-FR-12): every endpoint acts only on the
explicit request body; bodies with unknown fields are refused, so a
bulk payload (``{"all": true}``, extra ids, ...) can never be implied
into effect.

Secret hygiene (P1-FR-16): error bodies carry only review-module
messages (ids, paths, hashes) or this module's own conflict texts;
credentials never flow through the review stores this serves.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from ..review import (
    REVIEW_DIR_NAME,
    ReviewConflictError,
    ReviewError,
    apply_decisions,
    build_plan,
    parse_decisions_file,
    record_decision,
    show_entry,
)
from ..state import StateRoot
from .runner import WebRunner
from .server import (
    App,
    HTTPError,
    Request,
    Response,
    json_response,
    parse_json_body,
)

# The state-anchored decisions document for decisions made from the web.
WEB_DECISIONS_NAME = "web-decisions.jsonl"

# The two review actions of the pending view (P1-FR-08), named with the
# product's own status vocabulary (P1-FR-03).
REVIEW_ACTIONS: tuple[str, ...] = ("accepted", "rejected")

# Verbatim from review.build_plan's conflict reason for a stale entry
# hash — the review module's own message, reused (not rephrased).
STALE_HASH_MESSAGE = (
    "backlog line changed since the decision was recorded "
    "(entry hash mismatch): concurrent modification or obsolete decision"
)

ACTIVE_RUN_MESSAGE = (
    "a web run is active; wait for it to finish or cancel it before "
    "changing review decisions"
)

PLAN_CHANGED_MESSAGE = (
    "the plan changed since it was previewed (new decisions or changed "
    "backlog/blocklist lines); preview GET /api/backlog/plan again and "
    "confirm the new plan_sha256"
)

# ReviewError message prefixes -> HTTP status. These are review.py's
# stable operator-facing messages (the same texts the CLI prints after
# "review error:"); anything unrecognized is a 400.
_ERROR_STATUS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        404,
        (
            "unknown backlog id",
            "no backlog at",
            "decisions file not found",
            "decisions file is empty",
            "decisions file has no decisions",
        ),
    ),
    (
        409,
        (
            "decisions file already records",
            "backlog has duplicate entries",
        ),
    ),
)


def _status_for_review_error(exc: ReviewError) -> int:
    for status, prefixes in _ERROR_STATUS:
        if any(str(exc).startswith(prefix) for prefix in prefixes):
            return status
    return 400


def _http_error(exc: ReviewError) -> HTTPError:
    return HTTPError(_status_for_review_error(exc), str(exc))


def web_decisions_path(state: StateRoot) -> Path:
    """The deterministic decisions document location for the web."""
    return state.root / REVIEW_DIR_NAME / WEB_DECISIONS_NAME


def plan_fingerprint(plan: dict[str, Any]) -> str:
    """sha256 of the plan's canonical JSON — the apply confirmation
    token. Deterministic for identical state + decisions (the plan
    document carries no timestamps)."""
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _state_of(request: Request) -> StateRoot:
    if request.state is None:  # pragma: no cover - create_app always sets it
        raise HTTPError(500, "no state root configured")
    return request.state.state_root


def _refuse_active_run(runner: WebRunner) -> None:
    """S8: no review writes while a web run holds the single-writer slot."""
    if runner.active_view()["state"] == "running":
        raise HTTPError(409, ACTIVE_RUN_MESSAGE)


def _require_object_body(request: Request) -> dict[str, Any]:
    payload = parse_json_body(request)
    if not isinstance(payload, dict):
        raise HTTPError(400, "a JSON object body is required")
    return payload


def _reject_unknown_fields(payload: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise HTTPError(
            400,
            "unknown field(s) " + ", ".join(unknown)
            + "; allowed: " + ", ".join(allowed),
        )


def register_review(app: App, runner: WebRunner) -> None:
    """Register the backlog review endpoints on an existing app.

    Wiring (server.py is not edited by this unit): ``run_web`` already
    constructs the ``WebRunner`` and calls ``register_runs``; it must
    also call ``register_review(server.app, runner)`` right after
    (``create_app`` cannot: it has no runner — the runner is created in
    ``run_web``).
    """
    state = app.state.state_root
    decisions_path = web_decisions_path(state)
    # One lock serializes every review WRITE through this process.
    review_lock = threading.Lock()

    # ---- GET /api/backlog/{video_id} — the backlog-show data --------

    def get_entry(request: Request) -> Response:
        try:
            document = show_entry(_state_of(request), request.params["video_id"])
        except ReviewError as exc:
            raise _http_error(exc) from None
        return json_response(200, document)

    # ---- POST /api/backlog/{video_id}/decision -----------------------

    def post_decision(request: Request) -> Response:
        _refuse_active_run(runner)
        payload = _require_object_body(request)
        _reject_unknown_fields(payload, ("decision", "entry_sha256", "reason"))
        decision = payload.get("decision")
        if decision not in REVIEW_ACTIONS:
            raise HTTPError(
                400,
                f"decision must be one of {list(REVIEW_ACTIONS)}, "
                f"got {decision!r}",
            )
        entry_hash = payload.get("entry_sha256")
        if not isinstance(entry_hash, str) or not entry_hash.strip():
            raise HTTPError(400, "entry_sha256 is required")
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise HTTPError(400, "reason must be a string or null")

        state_root = _state_of(request)
        with review_lock, runner.review_write(ACTIVE_RUN_MESSAGE):
            # Hash binding: decide on the entry the client previewed.
            # (show_entry is the public reader that yields the current
            # raw-line hash; a mismatch is the review module's stale
            # conflict, refused with zero writes.)
            try:
                current = show_entry(state_root, request.params["video_id"])
            except ReviewError as exc:
                raise _http_error(exc) from None
            if current["entry_sha256"] != entry_hash:
                raise HTTPError(409, STALE_HASH_MESSAGE)
            try:
                record = record_decision(
                    state_root,
                    video_id=request.params["video_id"],
                    decision=decision,
                    decisions_file=decisions_path,
                    reason=reason,
                )
            except ReviewConflictError as exc:
                raise HTTPError(409, str(exc)) from None
            except ReviewError as exc:
                raise _http_error(exc) from None
        return json_response(
            200, {"recorded": record, "decisions_file": str(decisions_path)}
        )

    # ---- GET /api/backlog/plan — the backlog-plan preview -------------

    def get_plan(request: Request) -> Response:
        state_root = _state_of(request)
        try:
            decisions = parse_decisions_file(decisions_path)
            plan = build_plan(
                state_root, decisions, decisions_file=str(decisions_path)
            )
        except ReviewError as exc:
            raise _http_error(exc) from None
        return json_response(
            200, {"plan": plan, "plan_sha256": plan_fingerprint(plan)}
        )

    # ---- POST /api/backlog/apply --------------------------------------

    def post_apply(request: Request) -> Response:
        _refuse_active_run(runner)
        payload = _require_object_body(request)
        _reject_unknown_fields(payload, ("plan_sha256",))
        fingerprint = payload.get("plan_sha256")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise HTTPError(400, "plan_sha256 is required (echo GET /api/backlog/plan)")

        state_root = _state_of(request)
        with review_lock, runner.review_write(ACTIVE_RUN_MESSAGE):
            try:
                decisions = parse_decisions_file(decisions_path)
                plan = build_plan(
                    state_root, decisions, decisions_file=str(decisions_path)
                )
            except ReviewError as exc:
                raise _http_error(exc) from None
            if plan_fingerprint(plan) != fingerprint:
                raise HTTPError(409, PLAN_CHANGED_MESSAGE)
            try:
                receipt = apply_decisions(
                    state_root, decisions, decisions_file=str(decisions_path)
                )
            except ReviewConflictError as exc:
                raise HTTPError(409, str(exc)) from None
            except ReviewError as exc:
                raise _http_error(exc) from None
        return json_response(200, receipt)

    app.route("GET", "/api/backlog/{video_id}", get_entry)
    app.route("POST", "/api/backlog/{video_id}/decision", post_decision)
    app.route("GET", "/api/backlog/plan", get_plan)  # literal beats {video_id}
    app.route("POST", "/api/backlog/apply", post_apply)


__all__ = [
    "ACTIVE_RUN_MESSAGE",
    "PLAN_CHANGED_MESSAGE",
    "REVIEW_ACTIONS",
    "STALE_HASH_MESSAGE",
    "WEB_DECISIONS_NAME",
    "plan_fingerprint",
    "register_review",
    "web_decisions_path",
]
