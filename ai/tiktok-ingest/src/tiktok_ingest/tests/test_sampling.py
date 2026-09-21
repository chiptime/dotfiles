"""Pure hybrid sampler: methodology 3.2 parameters, tail and coverage rules."""

from __future__ import annotations

import unittest

from tiktok_ingest import config
from tiktok_ingest.sampling import (
    Candidate,
    REASON_SCENE,
    REASON_TEXT_REGION,
    REASON_UNIFORM,
    apply_budget,
    crop_proposal,
    flag_crop_candidates,
    merge_candidate_sets,
    select_baseline_frames,
    snap_candidates_to_stream,
    thin_uniform_by_gap,
    uncovered_intervals,
)


class SnapCandidatesTests(unittest.TestCase):
    def test_snaps_onto_nearest_real_frame_pts(self) -> None:
        # 59.94 fps stream: real frames at multiples of 200 ticks; the fps
        # grid lands on multiples of 23976 (4 s) which almost never exist.
        stream = [pts for pts in range(0, 48001, 200)]
        grid = [
            Candidate(pts=0, pts_time=0.0),
            Candidate(pts=23976, pts_time=2.0),
            Candidate(pts=47952, pts_time=4.0),
        ]
        snapped = snap_candidates_to_stream(grid, stream, 1 / 11988)
        self.assertEqual(
            [candidate.pts for candidate in snapped],
            [0, 24000, 48000],
        )

    def test_appends_true_last_stream_frame_for_tail_guarantee(self) -> None:
        stream = [0, 200, 400, 999999]
        grid = [Candidate(pts=200, pts_time=0.5)]
        snapped = snap_candidates_to_stream(grid, stream, 1 / 11988)
        self.assertEqual(snapped[-1].pts, 999999)

    def test_collapses_duplicate_snaps(self) -> None:
        stream = [0, 100, 200]
        grid = [
            Candidate(pts=95, pts_time=0.5),
            Candidate(pts=105, pts_time=0.6),
        ]
        snapped = snap_candidates_to_stream(grid, stream, 0.001)
        # Both collapse onto 100; the tail guarantee appends the last
        # stream frame (200).
        self.assertEqual([candidate.pts for candidate in snapped], [100, 200])

    def test_empty_stream_returns_candidates_unchanged(self) -> None:
        grid = [Candidate(pts=7, pts_time=0.7)]
        self.assertEqual(snap_candidates_to_stream(grid, [], 0.001), grid)


def uniform_range(start: float, stop: float, step: float) -> list[Candidate]:
    values = []
    time = start
    while time <= stop + 1e-9:
        values.append(Candidate(pts=round(time * 12800), pts_time=round(time, 4)))
        time += step
    return values


class ThinUniformTests(unittest.TestCase):
    def test_respects_minimum_gap(self) -> None:
        candidates = uniform_range(0.0, 12.0, 0.5)
        kept = thin_uniform_by_gap(candidates, min_gap_seconds=4.0)
        times = [candidate.pts_time for candidate in kept]
        self.assertEqual(times, [0.0, 4.0, 8.0, 12.0])

    def test_gap_is_inclusive(self) -> None:
        candidates = uniform_range(0.0, 8.0, 4.0)
        kept = thin_uniform_by_gap(candidates, min_gap_seconds=4.0)
        self.assertEqual(len(kept), 3)


class MergeTests(unittest.TestCase):
    def test_near_duplicates_merge_with_all_reasons(self) -> None:
        merged = merge_candidate_sets(
            [Candidate(pts=0, pts_time=5.0)],
            [Candidate(pts=640, pts_time=5.05)],
            [Candidate(pts=1280, pts_time=7.0)],
            dedup_window_seconds=0.08,
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(
            merged[0]["selection_reasons"], [REASON_UNIFORM, REASON_SCENE]
        )
        self.assertEqual(merged[1]["selection_reasons"], [REASON_TEXT_REGION])

    def test_outside_window_stays_separate(self) -> None:
        merged = merge_candidate_sets(
            [Candidate(pts=0, pts_time=5.0)],
            [Candidate(pts=640, pts_time=5.2)],
            [],
            dedup_window_seconds=0.08,
        )
        self.assertEqual(len(merged), 2)


class BudgetTests(unittest.TestCase):
    def test_under_cap_keeps_everything(self) -> None:
        merged = merge_candidate_sets(
            uniform_range(0.0, 12.0, 0.5),
            [Candidate(pts=640, pts_time=3.0)],
            [],
        )
        selected, dropped = apply_budget(merged, budget_cap=64)
        self.assertEqual(len(selected), len(merged))
        self.assertEqual(dropped, 0)

    def test_budget_cap_retains_priority_first(self) -> None:
        uniform = uniform_range(0.0, 300.0, 0.5)
        scene = [Candidate(pts=i, pts_time=float(i)) for i in range(10, 30)]
        merged = merge_candidate_sets(uniform, scene, [])
        selected, dropped = apply_budget(merged, budget_cap=32)
        self.assertLessEqual(len(selected), 32)
        self.assertGreater(dropped, 0)
        retained_scene = [
            entry
            for entry in selected
            if REASON_SCENE in entry["selection_reasons"]
        ]
        self.assertTrue(retained_scene, "scene candidates get priority")

    def test_tail_is_never_truncated(self) -> None:
        # 1600 candidates over ~800 s: thinning to 64 must retain the LAST
        # timeline candidate (MVP-PRD 3.B: sample the supported duration,
        # never silently truncate its tail).
        uniform = uniform_range(0.0, 799.5, 0.5)
        merged = merge_candidate_sets(uniform, [], [])
        selected, _ = apply_budget(merged, budget_cap=64)
        last_selected = max(entry["pts_time"] for entry in selected)
        last_available = max(entry["pts_time"] for entry in merged)
        self.assertEqual(last_selected, last_available)

    def test_priority_overload_also_keeps_tail(self) -> None:
        scene = [
            Candidate(pts=i, pts_time=float(i)) for i in range(0, 200)
        ]
        merged = merge_candidate_sets([], scene, [])
        selected, _ = apply_budget(merged, budget_cap=64)
        self.assertEqual(len(selected), 64)
        last_selected = max(entry["pts_time"] for entry in selected)
        self.assertEqual(last_selected, 199.0)


class CropCandidateTests(unittest.TestCase):
    def test_marks_at_most_the_cap_and_records_metadata_only(self) -> None:
        text = [
            Candidate(pts=i * 12800, pts_time=float(i)) for i in range(0, 20)
        ]
        merged = merge_candidate_sets([], [], text)
        flag_crop_candidates(merged, cap=16)
        marked = [entry for entry in merged if entry["is_crop_candidate"]]
        self.assertEqual(len(marked), 16)
        for entry in marked:
            self.assertIsNotNone(entry["crop"])
            self.assertIn("expression", entry["crop"])
            self.assertIn("metadata", entry["crop"]["note"])
        unmarked = [entry for entry in merged if not entry["is_crop_candidate"]]
        self.assertEqual(len(unmarked), 4)
        for entry in unmarked:
            self.assertIsNone(entry["crop"])

    def test_crop_proposal_matches_methodology_geometry(self) -> None:
        proposal = crop_proposal()
        self.assertEqual(proposal["expression"], "iw:ih*0.35:0:ih*0.65")
        self.assertEqual(proposal["height_fraction"], 0.35)
        self.assertEqual(proposal["y_offset_fraction"], 0.65)


class UncoveredIntervalTests(unittest.TestCase):
    def test_exposes_inner_and_edge_gaps(self) -> None:
        intervals = uncovered_intervals([5.0, 6.0], 20.0, max_gap_seconds=4.0)
        spans = [(i.start_seconds, i.end_seconds) for i in intervals]
        self.assertEqual(spans, [(0.0, 5.0), (6.0, 20.0)])

    def test_no_gaps_within_policy(self) -> None:
        intervals = uncovered_intervals([0.0, 4.0, 8.0], 8.0, max_gap_seconds=4.0)
        self.assertEqual(intervals, [])

    def test_empty_selection_exposes_everything(self) -> None:
        intervals = uncovered_intervals([], 10.0, max_gap_seconds=4.0)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].start_seconds, 0.0)
        self.assertEqual(intervals[0].end_seconds, 10.0)


class SelectBaselineFramesTests(unittest.TestCase):
    def test_end_to_end_counts_and_provenance(self) -> None:
        uniform = uniform_range(0.0, 12.0, 0.5)
        scene = [Candidate(pts=38400, pts_time=3.0)]
        text = [Candidate(pts=79360, pts_time=6.2)]
        result = select_baseline_frames(
            uniform, scene, text, duration_seconds=12.5
        )
        self.assertEqual(result.budget_cap, config.MAX_BASELINE_FRAMES)
        self.assertLessEqual(len(result.frames), config.MAX_BASELINE_FRAMES)
        self.assertGreater(len(result.frames), 0)
        reasons = {
            reason for frame in result.frames for reason in frame.selection_reasons
        }
        self.assertIn(REASON_UNIFORM, reasons)
        self.assertIn(REASON_SCENE, reasons)
        self.assertIn(REASON_TEXT_REGION, reasons)
        # Every frame keeps its source PTS and timestamp.
        for frame in result.frames:
            self.assertEqual(frame.pts, round(frame.pts_time * 12800))
        # The merged scene/text candidates are retained by priority.
        times = [frame.pts_time for frame in result.frames]
        self.assertIn(3.0, times)
        self.assertIn(6.2, times)
        self.assertEqual(result.counts["scene_candidates"], 1)
        self.assertEqual(result.counts["text_region_candidates"], 1)

    def test_full_duration_sampled_and_uncovered_exposed(self) -> None:
        # A 400-second low-motion clip: uniform thinning alone yields more
        # candidates than the budget, so the even-spacing fill creates gaps
        # longer than the coverage policy; they must be exposed, and the
        # tail must remain sampled.
        uniform = uniform_range(0.0, 399.5, 0.5)
        result = select_baseline_frames(uniform, [], [], duration_seconds=400.0)
        self.assertEqual(len(result.frames), config.MAX_BASELINE_FRAMES)
        self.assertTrue(result.uncovered, "coverage gaps must stay visible")
        last_frame = max(frame.pts_time for frame in result.frames)
        self.assertEqual(last_frame, 399.5, "tail must remain sampled")
        for interval in result.uncovered:
            self.assertGreater(
                interval.length_seconds,
                config.SAMPLING_MAX_COVERAGE_GAP_SECONDS,
            )

    def test_parameters_match_methodology(self) -> None:
        self.assertEqual(config.SAMPLING_UNIFORM_FPS, 2)
        self.assertEqual(config.SAMPLING_UNIFORM_MIN_GAP_SECONDS, 4.0)
        self.assertEqual(config.SAMPLING_SCENE_THRESHOLD, 0.3)
        self.assertEqual(config.SAMPLING_TEXT_REGION_THRESHOLD, 0.15)
        self.assertEqual(config.SAMPLING_DEDUP_WINDOW_SECONDS, 0.08)
        self.assertEqual(config.MAX_BASELINE_FRAMES, 64)
        self.assertEqual(config.SAMPLING_CROP_CANDIDATE_CAP, 16)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
