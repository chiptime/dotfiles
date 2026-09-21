"""Hybrid baseline frame selection — pure logic (Milestone 2).

Implements the benchmark's hybrid sampler (methodology section 3.2) as
deterministic functions over candidate timestamp lists. ffmpeg scanning
and frame writing live behind injectable runners in ``prepare``; this
module is pure and fully unit-tested with fixtures.

Milestone-2 guarantees beyond the benchmark script:

- The supported duration is sampled END TO END: when thinning drops
  candidates, the LAST timeline candidate is always retained, so the tail
  is never silently truncated (MVP-PRD section 3.B).
- Intervals not covered by any retained frame are EXPOSED in the selection
  result (``uncovered``), never silently accepted.
- Text-ROI crop coordinates are recorded as METADATA ONLY: candidates are
  marked with a crop proposal; crops are never rendered or inferred here
  (the bounded targeted rereads belong to milestone 3 vision).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable

from . import config

# Candidate/selection reason vocabulary (stable, recorded in manifests).
REASON_UNIFORM = "uniform_coverage"
REASON_SCENE = "scene_change"
REASON_TEXT_REGION = "text_region_change"

_SELECTION_REASON_ORDER = (REASON_UNIFORM, REASON_SCENE, REASON_TEXT_REGION)


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One candidate frame position on the source timeline."""

    pts: int
    pts_time: float


@dataclasses.dataclass(frozen=True)
class SelectedFrame:
    """One retained baseline frame with its full selection provenance."""

    pts: int
    pts_time: float
    selection_reasons: tuple[str, ...]
    is_crop_candidate: bool
    crop: dict[str, Any] | None


@dataclasses.dataclass(frozen=True)
class UncoveredInterval:
    """A timeline interval longer than the coverage gap policy."""

    start_seconds: float
    end_seconds: float

    @property
    def length_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclasses.dataclass(frozen=True)
class SelectionResult:
    """Complete, auditable outcome of one baseline selection pass."""

    frames: tuple[SelectedFrame, ...]
    uncovered: tuple[UncoveredInterval, ...]
    counts: dict[str, int]
    duration_seconds: float
    budget_cap: int


def thin_uniform_by_gap(
    candidates: Iterable[Candidate],
    min_gap_seconds: float = config.SAMPLING_UNIFORM_MIN_GAP_SECONDS,
) -> list[Candidate]:
    """Greedily keep uniform candidates at least ``min_gap_seconds`` apart.

    Milestone-2 tail guarantee: the final timeline candidate (the fps scan
    covers the whole video, so the last candidate sits at the end of the
    supported duration) is always retained, so the tail is never silently
    truncated before the budget even applies (MVP-PRD section 3.B).
    """
    ordered = sorted(candidates, key=lambda item: item.pts_time)
    kept: list[Candidate] = []
    last_kept: float | None = None
    for candidate in ordered:
        if last_kept is None or (candidate.pts_time - last_kept) >= min_gap_seconds:
            kept.append(candidate)
            last_kept = candidate.pts_time
    if ordered and (not kept or kept[-1] is not ordered[-1]):
        kept.append(ordered[-1])
    return kept


def merge_candidate_sets(
    uniform: Iterable[Candidate],
    scene_change: Iterable[Candidate],
    text_region: Iterable[Candidate],
    dedup_window_seconds: float = config.SAMPLING_DEDUP_WINDOW_SECONDS,
) -> list[dict[str, Any]]:
    """Merge the three candidate sources, unioning near-duplicate timestamps.

    Candidates within ``dedup_window_seconds`` merge into one entry that
    accumulates every applicable selection reason.
    """
    merged: list[dict[str, Any]] = []

    def find_existing(pts_time: float) -> dict[str, Any] | None:
        for entry in merged:
            if abs(entry["pts_time"] - pts_time) <= dedup_window_seconds:
                return entry
        return None

    for reason, candidates in (
        (REASON_UNIFORM, uniform),
        (REASON_SCENE, scene_change),
        (REASON_TEXT_REGION, text_region),
    ):
        for candidate in sorted(candidates, key=lambda item: item.pts_time):
            existing = find_existing(candidate.pts_time)
            if existing is None:
                merged.append(
                    {
                        "pts": candidate.pts,
                        "pts_time": candidate.pts_time,
                        "selection_reasons": [reason],
                    }
                )
            elif reason not in existing["selection_reasons"]:
                existing["selection_reasons"].append(reason)
    merged.sort(key=lambda entry: entry["pts_time"])
    return merged


def _evenly_spaced_indices(total: int, keep: int) -> list[int]:
    """Indices picking ``keep`` items spread over ``total`` (ends included).

    Maps ``keep`` positions onto the closed interval ``[0, total-1]`` so the
    FIRST and LAST candidates always survive thinning: the supported
    duration is sampled end to end (MVP-PRD section 3.B).
    """
    if total <= keep:
        return list(range(total))
    if keep == 1:
        return [0]
    return sorted(
        {round(index * (total - 1) / (keep - 1)) for index in range(keep)}
    )


def apply_budget(
    merged: list[dict[str, Any]],
    budget_cap: int = config.MAX_BASELINE_FRAMES,
) -> tuple[list[dict[str, Any]], int]:
    """Keep priority (scene/text) candidates first, fill with uniform ones.

    Returns ``(selected, dropped_count)``. When priority candidates alone
    exceed the cap they are thinned evenly across the whole timeline (with
    the last retained); the same tail guarantee applies to uniform fill.
    """
    priority = [
        entry
        for entry in merged
        if REASON_SCENE in entry["selection_reasons"]
        or REASON_TEXT_REGION in entry["selection_reasons"]
    ]
    uniform_only = [
        entry
        for entry in merged
        if REASON_SCENE not in entry["selection_reasons"]
        and REASON_TEXT_REGION not in entry["selection_reasons"]
    ]

    if len(priority) >= budget_cap:
        ordered = sorted(priority, key=lambda entry: entry["pts_time"])
        selected = [ordered[index] for index in _evenly_spaced_indices(len(ordered), budget_cap)]
        return sorted(selected, key=lambda entry: entry["pts_time"]), len(merged) - len(selected)

    remaining_slots = budget_cap - len(priority)
    uniform_sorted = sorted(uniform_only, key=lambda entry: entry["pts_time"])
    if len(uniform_sorted) <= remaining_slots:
        selected = priority + uniform_sorted
    else:
        picked = [
            uniform_sorted[index]
            for index in _evenly_spaced_indices(len(uniform_sorted), remaining_slots)
        ]
        selected = priority + picked
    selected.sort(key=lambda entry: entry["pts_time"])
    return selected, len(merged) - len(selected)


def crop_proposal() -> dict[str, Any]:
    """Lower-third text-ROI crop coordinates, recorded as METADATA ONLY."""
    return {
        "expression": (
            f"iw:ih*{config.SAMPLING_CROP_HEIGHT_FRACTION}:0:"
            f"ih*{config.SAMPLING_CROP_Y_OFFSET_FRACTION}"
        ),
        "height_fraction": config.SAMPLING_CROP_HEIGHT_FRACTION,
        "y_offset_fraction": config.SAMPLING_CROP_Y_OFFSET_FRACTION,
        "note": (
            "metadata only: crops are marked, never rendered or inferred in "
            "milestone 2 (targeted rereads belong to milestone 3 vision)"
        ),
    }


def flag_crop_candidates(
    selected: list[dict[str, Any]],
    cap: int = config.SAMPLING_CROP_CANDIDATE_CAP,
) -> None:
    """Mark up to ``cap`` text-region entries as crop candidates (in place)."""
    marked = 0
    for entry in selected:
        eligible = REASON_TEXT_REGION in entry["selection_reasons"] and marked < cap
        entry["is_crop_candidate"] = eligible
        entry["crop"] = crop_proposal() if eligible else None
        if eligible:
            marked += 1


def uncovered_intervals(
    selected_times: Iterable[float],
    duration_seconds: float,
    max_gap_seconds: float = config.SAMPLING_MAX_COVERAGE_GAP_SECONDS,
) -> list[UncoveredInterval]:
    """Expose timeline gaps with no retained frame (including the edges).

    A gap longer than ``max_gap_seconds`` between consecutive retained
    frames — or from 0 to the first / from the last to the duration end —
    is reported, never silently accepted.
    """
    times = sorted(time for time in selected_times if 0.0 <= time <= duration_seconds)
    if duration_seconds <= 0:
        return []
    if not times:
        return [
            UncoveredInterval(start_seconds=0.0, end_seconds=duration_seconds)
        ]
    intervals: list[UncoveredInterval] = []
    boundaries = [0.0, *times, duration_seconds]
    for start, end in zip(boundaries, boundaries[1:]):
        if end - start > max_gap_seconds:
            intervals.append(UncoveredInterval(start_seconds=start, end_seconds=end))
    return intervals


def snap_candidates_to_stream(
    candidates: Iterable[Candidate],
    stream_pts_sorted: Iterable[int],
    time_base: float,
) -> list[Candidate]:
    """Snap uniform candidates onto REAL stream frame timestamps.

    The ``fps`` scan reports output-slot timestamps, which coincide with
    actual frame PTS only when the frame interval divides the grid exactly
    (30 fps does; 59.94 fps almost never does — first real pilot found 27
    of 29 planned PTS absent from the stream). Each candidate is moved to
    the NEAREST real frame PTS and duplicates collapse; the true LAST
    stream frame is always appended so the tail guarantee operates on real
    frames (MVP-PRD section 3.B). Order by time, duplicates removed.
    """
    pts_list = sorted({int(pts) for pts in stream_pts_sorted})
    if not pts_list:
        return list(candidates)
    import bisect

    snapped: dict[int, Candidate] = {}
    for candidate in candidates:
        index = bisect.bisect_left(pts_list, candidate.pts)
        nearest = min(
            pts_list[max(0, index - 1) : index + 1],
            key=lambda pts: (abs(pts - candidate.pts), pts),
        )
        snapped.setdefault(
            nearest, Candidate(pts=nearest, pts_time=nearest * time_base)
        )
    tail_pts = pts_list[-1]
    snapped.setdefault(
        tail_pts, Candidate(pts=tail_pts, pts_time=tail_pts * time_base)
    )
    return [snapped[pts] for pts in sorted(snapped)]


def select_baseline_frames(
    uniform: Iterable[Candidate],
    scene_change: Iterable[Candidate],
    text_region: Iterable[Candidate],
    *,
    duration_seconds: float,
    budget_cap: int = config.MAX_BASELINE_FRAMES,
    dedup_window_seconds: float = config.SAMPLING_DEDUP_WINDOW_SECONDS,
    max_coverage_gap_seconds: float = config.SAMPLING_MAX_COVERAGE_GAP_SECONDS,
) -> SelectionResult:
    """Full pure selection pass: merge, budget, mark crops, expose gaps."""
    uniform_list = list(uniform)
    scene_list = list(scene_change)
    text_list = list(text_region)
    thinned_uniform = thin_uniform_by_gap(uniform_list)
    merged = merge_candidate_sets(
        thinned_uniform, scene_list, text_list, dedup_window_seconds
    )
    selected, dropped = apply_budget(merged, budget_cap)
    flag_crop_candidates(selected)
    frames = tuple(
        SelectedFrame(
            pts=entry["pts"],
            pts_time=entry["pts_time"],
            selection_reasons=tuple(
                reason
                for reason in _SELECTION_REASON_ORDER
                if reason in entry["selection_reasons"]
            ),
            is_crop_candidate=bool(entry["is_crop_candidate"]),
            crop=dict(entry["crop"]) if entry["crop"] else None,
        )
        for entry in selected
    )
    counts = {
        "uniform_candidates_generated": len(uniform_list),
        "uniform_after_gap_thinning": len(thinned_uniform),
        "scene_candidates": len(scene_list),
        "text_region_candidates": len(text_list),
        "merged_candidates": len(merged),
        "retained_frames": len(frames),
        "dropped_by_budget": dropped,
        "crop_candidates_marked": sum(1 for frame in frames if frame.is_crop_candidate),
    }
    return SelectionResult(
        frames=frames,
        uncovered=tuple(
            uncovered_intervals(
                (frame.pts_time for frame in frames),
                duration_seconds,
                max_coverage_gap_seconds,
            )
        ),
        counts=counts,
        duration_seconds=duration_seconds,
        budget_cap=budget_cap,
    )
