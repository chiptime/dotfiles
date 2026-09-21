"""Stage validity and resume logic (MVP-PRD section 4).

A completed stage may be reused only when the media/content hash and the
relevant tool, model, prompt and configuration versions match. That pairing
is captured in a stage fingerprint::

    {"stage": ..., "input_sha256": ..., "versions": {...}}

Changing any part of a stage's fingerprint invalidates that stage and every
downstream stage in the canonical order (:data:`contracts.STAGE_ORDER`)::

    prepare -> vision -> audio -> synthesis -> verify -> emit

Downstream outputs were derived from inputs that no longer match, so they
cannot be trusted even when their own fingerprints look unchanged.

Resume is idempotent at the emit boundary: backlog appends are keyed by the
stable video ID and duplicates are refused by the store, so a resumed run
can never create duplicate backlog entries. Unchanged repeated runs reuse
every completed stage and perform zero recompute work.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Mapping

from .contracts import STAGE_ORDER, BacklogEntry, JobManifest, StageOutcome
from .state import Backlog


def downstream_of(stage: str) -> tuple[str, ...]:
    """Stages strictly downstream of ``stage`` in canonical order."""
    if stage not in STAGE_ORDER:
        raise ValueError(
            f"unknown stage {stage!r}; valid stages: {list(STAGE_ORDER)}"
        )
    index = STAGE_ORDER.index(stage)
    return STAGE_ORDER[index + 1 :]


def build_fingerprint(
    stage: str, input_sha256: str, versions: Mapping[str, str]
) -> dict[str, Any]:
    """Build a stage fingerprint from input content hash plus versions."""
    if stage not in STAGE_ORDER:
        raise ValueError(
            f"unknown stage {stage!r}; valid stages: {list(STAGE_ORDER)}"
        )
    if not input_sha256:
        raise ValueError("input_sha256 must be a non-empty content hash")
    return {
        "stage": stage,
        "input_sha256": str(input_sha256),
        "versions": {str(key): str(value) for key, value in sorted(versions.items())},
    }


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def fingerprints_equal(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Order-insensitive fingerprint comparison through canonical JSON."""
    if a is None or b is None:
        return False
    return _canonical(a) == _canonical(b)


@dataclasses.dataclass(frozen=True)
class StageDecision:
    """Per-stage resume verdict with an auditable reason."""

    stage: str
    action: str  # "reuse" | "recompute"
    reason: str


@dataclasses.dataclass(frozen=True)
class ResumePlan:
    """Which completed stages to reuse and which to recompute."""

    reusable: tuple[str, ...]
    invalidated: tuple[str, ...]
    decisions: tuple[StageDecision, ...]


def plan_resume(
    recorded: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
) -> ResumePlan:
    """Compare recorded completed-stage fingerprints against current inputs.

    ``recorded`` maps stage name to fingerprint for COMPLETED stages only
    (callers filter by outcome). ``current`` maps stage name to the
    fingerprint the next run would use. Walking the canonical order, a stage
    is reusable only while every upstream stage reused cleanly and its own
    fingerprint matches. The first mismatch invalidates that stage and all
    downstream stages.
    """
    reusable: list[str] = []
    invalidated: list[str] = []
    decisions: list[StageDecision] = []
    broken = False
    for stage in STAGE_ORDER:
        if broken:
            invalidated.append(stage)
            decisions.append(
                StageDecision(
                    stage=stage,
                    action="recompute",
                    reason=f"invalidated by upstream change in "
                    f"'{decisions[-1].stage}'"
                    if decisions
                    else "invalidated by upstream change",
                )
            )
            continue
        recorded_fp = recorded.get(stage)
        current_fp = current.get(stage)
        if recorded_fp is None:
            broken = True
            invalidated.append(stage)
            decisions.append(
                StageDecision(
                    stage=stage,
                    action="recompute",
                    reason="stage has no recorded completed fingerprint",
                )
            )
            continue
        if current_fp is None:
            broken = True
            invalidated.append(stage)
            decisions.append(
                StageDecision(
                    stage=stage,
                    action="recompute",
                    reason="current inputs provide no fingerprint for this stage",
                )
            )
            continue
        if fingerprints_equal(recorded_fp, current_fp):
            reusable.append(stage)
            decisions.append(
                StageDecision(
                    stage=stage,
                    action="reuse",
                    reason="media/content hash and versions match the recorded run",
                )
            )
        else:
            broken = True
            invalidated.append(stage)
            decisions.append(
                StageDecision(
                    stage=stage,
                    action="recompute",
                    reason="stage fingerprint changed (hash or version mismatch)",
                )
            )
    return ResumePlan(
        reusable=tuple(reusable),
        invalidated=tuple(invalidated),
        decisions=tuple(decisions),
    )


def completed_fingerprints(manifest: JobManifest) -> dict[str, dict[str, Any]]:
    """Fingerprints of completed stages only; everything else must recompute."""
    return {
        stage: record.fingerprint
        for stage, record in manifest.stages.items()
        if record.result.outcome is StageOutcome.COMPLETE and record.fingerprint
    }


def plan_resume_for_manifest(
    manifest: JobManifest, current: Mapping[str, Mapping[str, Any]]
) -> ResumePlan:
    """Plan resume for a job manifest against the current stage fingerprints."""
    return plan_resume(completed_fingerprints(manifest), current)


def emit_backlog_entry(backlog: Backlog, entry: BacklogEntry) -> str:
    """Append a backlog entry idempotently, keyed by the stable video ID.

    Returns ``"appended"`` or ``"skipped_duplicate"``; a resumed run that
    reaches emit twice for the same video can never create a duplicate.
    """
    return backlog.append(entry)
