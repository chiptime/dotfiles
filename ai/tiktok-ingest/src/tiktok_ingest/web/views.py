"""Read-only JSON views over the durable product state (S7, T2).

Every endpoint derives its response from the product's own stores ON
DISK on every request (P1-FR-01/02/14): the collection scan store, the
run-report directory, the processed/manifest state, the backlog queue
and the review readers. There is no cache, no second authority and no
write path here — the module imports readers only, and vocabulary
(outcome/status strings) is passed through unchanged (P1-FR-03).

Block sourcing: no single "blocks" store exists. Per-item blocks come
from the same manifests ``summary`` reads (:func:`derive_item_stages` +
:func:`derive_next`); run-level blocks come from each collection's
newest saved report (``hard_stop_reason``, per-batch ``failures`` and
``cleanup_notes``).

Secret hygiene (P1-FR-16): saved reports already record only the text
API endpoint ORIGIN and model, never key material; as defense in depth
every report document served is filtered so no key-like field name
survives.

Wire-up: ``register_views(app)`` registers all routes on an existing
``web.server.App``; ``create_app`` (server.py) must call it once so the
real ``ti web`` app serves these endpoints.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from urllib.parse import unquote, urlsplit
from typing import Any

from ..contracts import BACKLOG_STATUSES
from ..collection import (
    CollectionError,
    CollectionScanStore,
    build_collection_plan,
    parse_collection_url,
)
from ..engine import run_dir_of
from ..fetch import PipelineError, parse_tiktok_url, video_status
from ..guided import derive_item_stages
from ..review import ReviewError, list_entries
from ..state import Blocklist, InventoryStore, StateRoot, _KEY_RE, Processed
from ..summary import (
    REPORTS_DIRNAME,
    build_summary,
    derive_next,
    load_report,
)
from ..synthesis import SYNTHESIS_FILENAME
from ..verify import VERIFICATION_FILENAME
from .server import App, HTTPError, Request, Response, json_response

# Saved reports are named ``utc_now_iso().replace(":", "-") + ".json"``
# by ``collection_run._persist_report``; anything else is not a report.
_REPORT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.json$")
_SLUG_ID_SUFFIX_RE = re.compile(r"-\d{6,}$")


def collection_display_name(url: str | None) -> str | None:
    """Human name of a collection: its URL slug, decoded, without the
    trailing numeric id (``juegos%20de%20mesa-7540…`` -> ``juegos de mesa``).
    Display only; the collection key stays the identity."""
    if not url:
        return None
    slug = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    return _SLUG_ID_SUFFIX_RE.sub("", slug) or slug or None


def _saved_at(report_name: str) -> str:
    """The report's save time, from its writer-pattern file name."""
    stamp = report_name[: -len(".json")]
    date, time_part = stamp.split("T", 1)
    return f"{date}T{time_part.replace('-', ':')}"

# Evidence artifacts an operator may read through the web surface.
# ``meta.json`` is the engine's manifest, not evidence, and stays out.
_EVIDENCE_NAMES: tuple[str, ...] = (
    "video.md",
    "audio.md",
    SYNTHESIS_FILENAME,
    VERIFICATION_FILENAME,
)

# Defense in depth (P1-FR-16): reports as saved contain only the text
# API endpoint ORIGIN and model, but any field whose NAME looks like
# key material is dropped before a document leaves the process.
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:api_key|apikey|token|secret|password|passwd|credential)(?:_|$)",
    re.IGNORECASE,
)


def _sanitize(value: Any) -> Any:
    """Deep-copy ``value`` dropping key-like field names (values never
    invented, everything else passed through unchanged)."""
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if not _SECRET_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _state_of(request: Request) -> StateRoot:
    if request.state is None:  # pragma: no cover - create_app always sets it
        raise HTTPError(500, "no state root configured")
    return request.state.state_root


def _collection_resume_command(state: StateRoot, url: Any, key: str) -> str | None:
    """Only offer a command when the saved URL still identifies this collection."""
    try:
        ref = parse_collection_url(url)
    except (CollectionError, TypeError):
        return None
    if ref.collection_key != key:
        return None
    return shlex.join(["ti", ref.collection_url, "--state-root", str(state.root)])


def _scan_facts(state: StateRoot, key: str) -> dict[str, Any] | None:
    """Load one scan strictly through the store (shape-checked key)."""
    store = CollectionScanStore(state)
    try:
        scan = store.load(key)
    except CollectionError:
        return None
    if scan is None:
        return None
    access_markers: list[str] = []  # unique, first-seen order, accumulated
    for capture in scan.captures:
        for marker in capture.access_markers:
            if marker not in access_markers:
                access_markers.append(marker)
    plan = build_collection_plan(state, scan)  # read-only classification
    return {
        "key": scan.collection.collection_key,
        "url": scan.collection.collection_url,
        "resume_command": _collection_resume_command(
            state, scan.collection.collection_url, key
        ),
        "name": collection_display_name(scan.collection.collection_url),
        "author": scan.collection.author,
        "kind": scan.collection.kind,
        "declared_count": scan.declared_count,
        "observed_count": len(scan.items),  # observed, never "processed"
        "status": scan.status,
        "status_reason": scan.status_reason,
        "end_of_list_evidence": scan.end_of_list_evidence,
        "stop_reason": scan.stop_reason,
        "access_markers": access_markers,
        "last_capture_at": (
            scan.captures[-1].captured_at if scan.captures else None
        ),
        "started_at": scan.started_at,
        "updated_at": scan.updated_at,
        "plan": {
            "scan_status": plan.scan_status,
            "scan_status_reason": plan.scan_status_reason,
            "counts": dict(plan.counts),
        },
    }


def _collections(request: Request) -> Response:
    state = _state_of(request)
    entries = []
    for key in CollectionScanStore(state).keys():
        facts = _scan_facts(state, key)
        if facts is not None:
            entries.append(facts)
    return json_response(200, {"collections": entries})


def _collection_detail(request: Request) -> Response:
    state = _state_of(request)
    facts = _scan_facts(state, request.params["key"])
    if facts is None:
        raise HTTPError(404, "collection not found")
    store = CollectionScanStore(state)
    try:
        scan = store.load(request.params["key"])
    except CollectionError:
        raise HTTPError(404, "collection not found") from None
    plan = build_collection_plan(state, scan)
    facts["items"] = [
        {
            "id": item.stable_id,
            "kind": item.kind,
            "url": item.canonical_url,
            "classification": item.classification,
            "detail": item.detail,
        }
        for item in plan.items
    ]
    return json_response(200, facts)


def _report_collections(state: StateRoot) -> list[str]:
    """Collection keys with saved reports, shape-checked via the store."""
    directory = state.root / REPORTS_DIRNAME
    if not directory.is_dir():
        return []
    store = CollectionScanStore(state)
    keys: list[str] = []
    try:
        candidates = sorted(directory.iterdir())
    except OSError:  # pragma: no cover - listing raced with a removal
        return []
    for path in candidates:
        if not path.is_dir():
            continue
        try:
            store.path_for(path.name)  # validates the 16-hex key shape
        except CollectionError:
            continue
        keys.append(path.name)
    return keys


def _row_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """The same complete/pending/failed split the summary footer shows."""
    complete = sum(1 for item in items if item.get("next") == "complete")
    pending = sum(1 for item in items if "pending" in str(item.get("next", "")))
    return {"complete": complete, "pending": pending, "other": len(items) - complete - pending}


def _runs(request: Request) -> Response:
    state = _state_of(request)
    entries: list[dict[str, Any]] = []
    for key in _report_collections(state):
        directory = state.root / REPORTS_DIRNAME / key
        names = sorted(
            path.name
            for path in directory.glob("*.json")
            if _REPORT_NAME_RE.match(path.name)
        )
        for name in names:
            path = directory / name
            try:
                summary = build_summary(state, report=str(path))
            except Exception:
                continue  # unreadable report: never break the listing
            entries.append(
                {
                    "collection_key": key,
                    "report": name,
                    # Saved reports carry no generation time; summary would
                    # report "now", so the file name's save time is used.
                    "saved_at": _saved_at(name),
                    "collection": summary.get("collection"),
                    "collection_name": collection_display_name(
                        (summary.get("collection") or {}).get("url")
                    ),
                    "counts": _row_counts(summary.get("items") or []),
                    "items": summary.get("items") or [],
                }
            )
    # Timestamp filenames sort chronologically; newest first overall.
    entries.sort(key=lambda entry: entry["report"], reverse=True)
    return json_response(200, {"runs": entries})


def _run_report(request: Request) -> Response:
    state = _state_of(request)
    key = request.params["collection_key"]
    name = request.params["report"]
    store = CollectionScanStore(state)
    try:
        store.path_for(key)  # strict key shape; no traversal
    except CollectionError:
        raise HTTPError(404, "collection not found") from None
    if not _REPORT_NAME_RE.match(name):
        raise HTTPError(404, "report not found")
    path = state.root / REPORTS_DIRNAME / key / name
    if not path.is_file():
        raise HTTPError(404, "report not found")
    try:
        document = load_report(path)
    except Exception:
        raise HTTPError(404, "report not found") from None
    return json_response(200, _sanitize(document))


def _blocks(request: Request) -> Response:
    state = _state_of(request)
    inventory = {entry.stable_id: entry for entry in InventoryStore(state).load()}
    blocked_ids = Blocklist(state).entries()
    items: list[dict[str, Any]] = []
    for video_id in sorted(Processed(state).all().keys()):
        stages = derive_item_stages(state, video_id)
        blocked = {
            stage: value
            for stage, value in stages.items()
            if value not in ("pending", "complete")
        }
        if not blocked:
            continue
        command = None
        entry = inventory.get(video_id)
        if entry is not None and video_id not in blocked_ids:
            try:
                parsed_id, url, _ = parse_tiktok_url(entry.canonical_url)
                if parsed_id == video_id:
                    command = shlex.join(["ti", url, "--state-root", str(state.root)])
            except (PipelineError, TypeError):
                pass
        items.append(
            {
                "id": video_id,
                "blocked_stages": blocked,  # recorded outcomes, unchanged
                "next": derive_next(stages),  # the resume line summary prints
                "resume_command": command,
            }
        )
    runs: list[dict[str, Any]] = []
    for key in _report_collections(state):
        directory = state.root / REPORTS_DIRNAME / key
        names = sorted(
            path.name
            for path in directory.glob("*.json")
            if _REPORT_NAME_RE.match(path.name)
        )
        if not names:
            continue
        try:
            document = load_report(directory / names[-1])
        except Exception:
            continue
        run_block: dict[str, Any] = {
            "collection_key": key,
            "report": names[-1],
            "collection_name": collection_display_name(
                document.get("collection_url")
            ),
            "saved_at": _saved_at(names[-1]),
        }
        if document.get("hard_stop_reason"):
            run_block["hard_stop_reason"] = document["hard_stop_reason"]
        failures: list[Any] = []
        cleanup_notes: list[str] = []
        for batch in document.get("batches") or []:
            failures.extend(batch.get("failures") or [])
            cleanup_notes.extend(batch.get("cleanup_notes") or [])
        if failures:
            run_block["failures"] = failures
        if cleanup_notes:
            run_block["cleanup_notes"] = cleanup_notes
        if len(run_block) > 4:  # identity fields alone are not a block
            run_block["resume_command"] = _collection_resume_command(
                state, document.get("collection_url"), key
            )
            runs.append(run_block)
    return json_response(200, {"items": items, "runs": runs})


def _video(request: Request) -> Response:
    state = _state_of(request)
    video_id = request.params["video_id"]
    payload = video_status(video_id, state=state)  # exactly what `ti status` shows
    run_dir = run_dir_of(state, video_id)
    evidence: list[str] = []
    if run_dir is not None:
        evidence = [
            name
            for name in _EVIDENCE_NAMES
            if (Path(run_dir) / name).is_file()
        ]
    payload["items"] = [
        {**item, "evidence": evidence} for item in payload["items"]
    ]
    return json_response(200, payload)


def _video_evidence(request: Request) -> Response:
    state = _state_of(request)
    video_id = request.params["video_id"]
    name = request.params["name"]
    # The same id validator the state root uses for run directories.
    if not _KEY_RE.match(video_id):
        raise HTTPError(404, "video not found")
    if name not in _EVIDENCE_NAMES:
        raise HTTPError(404, "evidence not found")
    run_dir = run_dir_of(state, video_id)
    if run_dir is None:
        raise HTTPError(404, "video not found")
    path = Path(run_dir) / name
    if not path.is_file():
        raise HTTPError(404, "evidence not found")
    content_type = (
        "application/json; charset=utf-8"
        if name.endswith(".json")
        else "text/plain; charset=utf-8"
    )
    return 200, {"Content-Type": content_type}, path.read_bytes()


def _backlog(request: Request) -> Response:
    state = _state_of(request)
    values = request.query.get("status") or []
    status = values[-1] if values else None
    if status is not None and status not in BACKLOG_STATUSES:
        # Same vocabulary check review.list_entries applies.
        raise HTTPError(
            400, f"unknown status filter {status!r}; valid: {list(BACKLOG_STATUSES)}"
        )
    if not state.backlog_path.exists():
        # Missing state root / no queue yet: an empty list, not an error.
        return json_response(200, {"entries": []})
    try:
        entries = list_entries(state, status)  # the backlog-list data
    except ReviewError as exc:
        raise HTTPError(400, str(exc)) from None
    return json_response(200, {"entries": entries})


def register_views(app: App) -> None:
    """Register every read-only JSON view on an existing app."""
    app.route("GET", "/api/collections", _collections)
    app.route("GET", "/api/collections/{key}", _collection_detail)
    app.route("GET", "/api/runs", _runs)
    app.route("GET", "/api/runs/{collection_key}/{report}", _run_report)
    app.route("GET", "/api/blocks", _blocks)
    app.route("GET", "/api/videos/{video_id}", _video)
    app.route("GET", "/api/videos/{video_id}/evidence/{name}", _video_evidence)
    app.route("GET", "/api/backlog", _backlog)
