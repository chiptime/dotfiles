"""Read-only ``summary`` view over durable per-video stage state.

This module renders the stage outcomes derived by
:func:`tiktok_ingest.guided.derive_item_stages` as either a compact human
table or a structured object (``--json``). It reads ONLY existing product
stores — the :class:`~tiktok_ingest.state.Processed` store, each run's
``meta.json`` manifest, the :class:`~tiktok_ingest.state.Backlog` queue,
the collection scan/inventory — plus, optionally, a previously saved
collection-run report under ``<state_root>/reports/<collection_key>/``.

It is read-only BY CONSTRUCTION: no state, manifest or backlog write path
is imported here, and the module never opens a browser, the network, a
GPU, a container or a model. The per-stage state is derived from ONE
source (the product's own manifests); there is no second ledger.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .collection import build_collection_plan, resolve_collection
from .contracts import utc_now_iso
from .guided import derive_item_stages
from .state import Backlog, InventoryStore, Processed, StateRoot

# Displayed in processing order (audio runs before vision in a run).
SUMMARY_STAGES: tuple[str, ...] = (
    "fetch",
    "prepare",
    "audio",
    "vision",
    "synthesis",
    "verify",
)

REPORTS_DIRNAME = "reports"
LATEST = "latest"

_AUTHOR_RE = re.compile(r"@([^/?#]+)")


class SummaryError(Exception):
    """A summary could not be produced from the requested source."""


def author_from_url(url: str | None) -> str | None:
    """Derive the TikTok author handle from a canonical video URL."""
    if not url:
        return None
    match = _AUTHOR_RE.search(url)
    return match.group(1) if match else None


def derive_next(stages: dict[str, str]) -> str:
    """One honest line naming the real next step for one item.

    Walks the stages in processing order. A recorded non-complete outcome
    is shown as-is (never relabeled ``pending``); a plain pending stage
    names itself, with the one special case that vision after a completed
    audio stage means the audio is already saved.
    """
    for stage in SUMMARY_STAGES:
        value = stages.get(stage, "pending")
        if value == "complete":
            continue
        if value == "pending":
            if stage == "vision" and stages.get("audio") == "complete":
                return "vision pending (audio saved)"
            return f"{stage} pending"
        return f"{stage}: {value}"
    return "complete"


def _row(video_id: str, stages: dict[str, str], author: str | None,
         backlog: str) -> dict[str, Any]:
    return {
        "id": video_id,
        "author": author,
        "stages": {stage: stages.get(stage, "pending") for stage in SUMMARY_STAGES},
        "backlog": backlog,
        "next": derive_next(stages),
    }


def _live_summary(state: StateRoot, collection: str | None) -> dict[str, Any]:
    inventory = {entry.stable_id: entry for entry in InventoryStore(state).load()}
    backlog = {str(entry.get("id")): entry for entry in Backlog(state).entries()}

    collection_view: dict[str, Any] | None = None
    canonical: dict[str, str] = {}
    if collection is not None:
        ref, scan = resolve_collection(state, collection)
        if scan is None:
            raise SummaryError(
                f"no persisted scan state for collection {collection!r}: a "
                "collection summary needs the collection's plan"
            )
        plan = build_collection_plan(state, scan)
        ids = [item.stable_id for item in plan.items]
        canonical = {item.stable_id: item.canonical_url for item in plan.items}
        collection_view = {
            "key": ref.collection_key,
            "url": ref.collection_url,
            "status": plan.scan_status,
            "status_reason": plan.scan_status_reason,
            "declared_count": plan.declared_count,
            "observed_count": plan.observed_count,
            "counts": dict(plan.counts),
        }
    else:
        ids = sorted(Processed(state).all().keys())

    items: list[dict[str, Any]] = []
    for video_id in ids:
        stages = derive_item_stages(state, video_id)
        entry = inventory.get(video_id)
        url = canonical.get(video_id) or (entry.canonical_url if entry else None)
        author = (entry.author if entry else None) or author_from_url(url)
        record = backlog.get(video_id)
        backlog_status = str(record.get("status")) if record else "none"
        items.append(_row(video_id, stages, author, backlog_status))

    return {
        "mode": "live",
        "state_root": str(state.root),
        "generated_at": utc_now_iso(),
        "collection": collection_view,
        "items": items,
    }


def _report_summary(state: StateRoot, report: dict[str, Any], path: Path) -> dict[str, Any]:
    run_plan = report.get("run_plan")
    urls: dict[str, str] = {}
    if isinstance(run_plan, dict):
        urls = {str(k): str(v) for k, v in (run_plan.get("canonical_urls") or {}).items()}

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch in report.get("batches") or []:
        for selected in batch.get("selected") or []:
            video_id = str(selected.get("id"))
            if video_id in seen:
                continue
            seen.add(video_id)
            stages = selected.get("stages") or {}
            url = selected.get("canonical_url") or urls.get(video_id)
            # A saved report records stage snapshots, never backlog status.
            items.append(_row(video_id, stages, author_from_url(url), "n/a"))

    return {
        "mode": "report",
        "state_root": str(state.root),
        "report_path": str(path),
        "generated_at": report.get("generated_at") or utc_now_iso(),
        "collection": {
            "key": report.get("collection_key"),
            "url": report.get("collection_url"),
        },
        "items": items,
    }


def resolve_report_path(
    state: StateRoot, report: str, collection: str | None
) -> Path:
    """Resolve ``--report`` to a concrete saved-report path.

    ``PATH`` is used verbatim; ``latest`` picks the newest saved report of
    the named collection (``--collection`` is then required).
    """
    if report != LATEST:
        path = Path(report).expanduser()
        if not path.is_file():
            raise SummaryError(f"report file not found: {path}")
        return path
    if collection is None:
        raise SummaryError(
            "--report latest needs --collection: there is no collection to "
            "pick a newest report from"
        )
    ref, _ = resolve_collection(state, collection)
    directory = state.root / REPORTS_DIRNAME / ref.collection_key
    if not directory.is_dir():
        raise SummaryError(
            f"no saved reports for collection {ref.collection_key!r} under {directory}"
        )
    candidates = sorted(path for path in directory.glob("*.json"))
    if not candidates:
        raise SummaryError(
            f"no saved reports for collection {ref.collection_key!r} under {directory}"
        )
    # The UTC timestamp file names sort chronologically.
    return candidates[-1]


def load_report(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SummaryError(f"could not read report {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SummaryError(f"report {path} is not a JSON object")
    return document


def build_summary(
    state: StateRoot, *, collection: str | None = None, report: str | None = None
) -> dict[str, Any]:
    """Build the read-only summary, from a saved report or live state."""
    if report is not None:
        path = resolve_report_path(state, report, collection)
        return _report_summary(state, load_report(path), path)
    return _live_summary(state, collection)


def guided_report_summary(report: dict[str, Any]) -> dict[str, Any]:
    """A summary-shaped view over a guided-run report's selected items.

    Read-only and derived ONLY from the report's own ``selected`` entries
    (which the guided coordinator itself derives from the product's own
    manifests). It reuses the same row/next-step derivation as the live
    view, so the table an end-of-run banner prints reads identically to
    the ``summary`` command. A guided report records stage snapshots,
    never backlog status, so that column is ``n/a``.
    """
    items: list[dict[str, Any]] = []
    for selected in report.get("selected") or []:
        stages = selected.get("stages") or {}
        items.append(
            _row(
                str(selected.get("id")),
                stages,
                author_from_url(selected.get("canonical_url")),
                "n/a",
            )
        )
    return {
        "mode": "guided-run",
        "state_root": report.get("state_root"),
        "generated_at": utc_now_iso(),
        "collection": None,
        "items": items,
    }


def render_table(summary: dict[str, Any]) -> str:
    """Render the human table with one row per item."""
    headers = ["id", "author", *SUMMARY_STAGES, "backlog", "next"]
    rows: list[list[str]] = []
    for item in summary.get("items") or []:
        stages = item.get("stages") or {}
        rows.append(
            [
                str(item.get("id")),
                str(item.get("author") or "-"),
                *[str(stages.get(stage, "pending")) for stage in SUMMARY_STAGES],
                str(item.get("backlog")),
                str(item.get("next")),
            ]
        )
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(row: list[str]) -> str:
        return "  ".join(
            cell.ljust(widths[index]) for index, cell in enumerate(row)
        ).rstrip()

    lines = [format_row(headers), "  ".join("-" * width for width in widths)]
    lines.extend(format_row(row) for row in rows)
    return "\n".join(lines)
