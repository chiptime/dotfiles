#!/usr/bin/env python3
"""
extract_frame_candidates.py

Container-side, CPU-only frame candidate extraction (no GPU, no network).
Uses only Python 3 stdlib plus the `ffmpeg`/`ffprobe` binaries on PATH.
Scene-change and text-region-change detection is delegated to ffmpeg's own
filters via subprocess -- no cv2/numpy/PIL dependency.

CLI:
    python3 extract_frame_candidates.py <video_mp4_path> <output_dir> \
        [--manifest <existing_manifest_json_path>]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

BUDGET_CAP = 64
CROP_CANDIDATE_CAP = 16
UNIFORM_MIN_GAP_SECONDS = 4.0
UNIFORM_FPS = 2
SCENE_CHANGE_THRESHOLD = 0.3
TEXT_REGION_SCENE_THRESHOLD = 0.15
DEDUP_MATCH_WINDOW_SECONDS = 0.08

SUB_0_5S_COVERAGE_GAP_NOTE = (
    "Uniform candidates are sampled at ~2fps (500ms native gap). Any "
    "on-screen text/URL/code with a visible duration under ~0.5s can fall "
    "entirely between two uniform samples. Scene-change and "
    "text-region-change candidates partially mitigate this by catching hard "
    "cuts and lower-third content changes, but this remains a documented "
    "coverage gap for very short on-screen text."
)

TEXT_REGION_CROP_NOTE = (
    "Text-region-change detection crops a lower-third band (0.35 of frame "
    "height, y offset 0.65) before scene detection, since on-screen "
    "captions/subtitles typically live there. This is a starting heuristic, "
    "not guaranteed to cover all on-screen text placement -- e.g. terminal "
    "windows or code editors can be centered or full-frame."
)


def run_subprocess(cmd, timeout=None):
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def probe_video_stream(video_path, errors):
    """Return (time_base_str, time_base_float, r_frame_rate, duration) via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=time_base,r_frame_rate,duration",
        "-of",
        "json",
        video_path,
    ]
    returncode, stdout, stderr = run_subprocess(cmd)
    if returncode != 0:
        raise RuntimeError("ffprobe stream probe failed: %s" % stderr.strip()[-500:])

    data = json.loads(stdout)
    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError("ffprobe returned no video streams for %s" % video_path)

    stream = streams[0]
    time_base_str = stream.get("time_base", "1/1")
    num_str, den_str = time_base_str.split("/")
    time_base_float = float(num_str) / float(den_str)
    r_frame_rate = stream.get("r_frame_rate")
    duration = stream.get("duration")
    duration_float = float(duration) if duration is not None else None

    return time_base_str, time_base_float, r_frame_rate, duration_float


SHOWINFO_PTS_RE = re.compile(r"pts:\s*(\d+)\s+pts_time:\s*([\d.]+)")


def parse_showinfo_events(stderr_text):
    """Extract (pts, pts_time) pairs from ffmpeg showinfo stderr output."""
    events = []
    for match in SHOWINFO_PTS_RE.finditer(stderr_text):
        pts = int(match.group(1))
        pts_time = float(match.group(2))
        events.append((pts, pts_time))
    return events


def rebase_events_to_original_pts(events, time_base_float):
    """
    Recompute each event's `pts` as round(pts_time / time_base) against the
    ORIGINAL stream's time_base, discarding the raw parsed pts integer.

    This matters because some ffmpeg filters (notably `fps=N`) rewrite PTS
    onto their own synthetic output timebase -- the raw `pts` showinfo
    reports after such a filter does NOT correspond to any real frame
    position in the original, unfiltered stream, even though `pts_time`
    (an absolute wall-clock second value) remains accurate. Filters that
    don't resample (e.g. `select`, `crop`) pass original PTS through
    unchanged, so this recomputation is a no-op (mod rounding) for those --
    it is only a real fix for fps-resampled candidates. Applying it
    uniformly to every candidate source keeps one code path and guarantees
    every downstream `pts` value is valid against the ORIGINAL stream,
    which is what the final `eq(pts\\,X)` extraction pass selects against.
    """
    rebased = []
    for _raw_pts, pts_time in events:
        original_pts = round(pts_time / time_base_float)
        rebased.append((original_pts, pts_time))
    return rebased


def generate_uniform_candidates(video_path, fps, time_base_float, errors):
    """
    Metadata-only pass: generate ~fps-per-second candidate timestamps across
    the whole video using showinfo, writing no frames to disk (-f null -).

    NOTE: the `fps=N` filter rewrites PTS onto its own synthetic timebase,
    so the raw pts values from this pass are corrected against the
    original stream's time_base via rebase_events_to_original_pts() before
    being returned -- see that function's docstring.
    """
    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-vf",
        "fps=%d,showinfo" % fps,
        "-f",
        "null",
        "-",
    ]
    returncode, _, stderr = run_subprocess(cmd)
    if returncode != 0 and "pts_time" not in stderr:
        errors.append({"step": "generate_uniform_candidates", "stderr": stderr.strip()[-800:]})
        return []
    events = parse_showinfo_events(stderr)
    return rebase_events_to_original_pts(events, time_base_float)


def generate_scene_change_candidates(video_path, threshold, time_base_float, errors):
    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-vf",
        "select='gt(scene,%s)',showinfo" % threshold,
        "-vsync",
        "vfr",
        "-f",
        "null",
        "-",
    ]
    returncode, _, stderr = run_subprocess(cmd)
    if returncode != 0 and "pts_time" not in stderr:
        errors.append({"step": "generate_scene_change_candidates", "stderr": stderr.strip()[-800:]})
        return []
    events = parse_showinfo_events(stderr)
    # `select` does not resample/rebase PTS, so this recomputation is a
    # no-op (mod rounding) here -- applied uniformly for one code path.
    return rebase_events_to_original_pts(events, time_base_float)


def generate_text_region_candidates(video_path, threshold, time_base_float, errors):
    crop_expr = "crop=iw:ih*0.35:0:ih*0.65"
    select_expr = "select='gt(scene\\,%s)'" % threshold
    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-vf",
        "%s,%s,showinfo" % (crop_expr, select_expr),
        "-vsync",
        "vfr",
        "-f",
        "null",
        "-",
    ]
    returncode, _, stderr = run_subprocess(cmd)
    if returncode != 0 and "pts_time" not in stderr:
        errors.append({"step": "generate_text_region_candidates", "stderr": stderr.strip()[-800:]})
        return []
    events = parse_showinfo_events(stderr)
    # `crop` does not resample/rebase PTS either -- same no-op guarantee.
    return rebase_events_to_original_pts(events, time_base_float)


def build_uniform_coverage_subset(uniform_events):
    """Greedily keep uniform candidates at least ~4s apart."""
    kept = []
    last_kept_time = None
    for pts, pts_time in sorted(uniform_events, key=lambda e: e[1]):
        if last_kept_time is None or (pts_time - last_kept_time) >= UNIFORM_MIN_GAP_SECONDS:
            kept.append((pts, pts_time))
            last_kept_time = pts_time
    return kept


def merge_candidate_sets(uniform, scene_change, text_region):
    """
    Merge three (pts, pts_time) candidate lists into a single list of dicts,
    unioning near-duplicate timestamps (within DEDUP_MATCH_WINDOW_SECONDS)
    and accumulating all applicable selection_reasons.
    """
    merged = []

    def find_existing(pts_time):
        for entry in merged:
            if abs(entry["pts_time"] - pts_time) <= DEDUP_MATCH_WINDOW_SECONDS:
                return entry
        return None

    for pts, pts_time in sorted(uniform, key=lambda e: e[1]):
        existing = find_existing(pts_time)
        if existing is None:
            merged.append(
                {"pts": pts, "pts_time": pts_time, "selection_reasons": ["uniform_coverage"]}
            )
        elif "uniform_coverage" not in existing["selection_reasons"]:
            existing["selection_reasons"].append("uniform_coverage")

    for pts, pts_time in sorted(scene_change, key=lambda e: e[1]):
        existing = find_existing(pts_time)
        if existing is None:
            merged.append(
                {"pts": pts, "pts_time": pts_time, "selection_reasons": ["scene_change"]}
            )
        elif "scene_change" not in existing["selection_reasons"]:
            existing["selection_reasons"].append("scene_change")

    for pts, pts_time in sorted(text_region, key=lambda e: e[1]):
        existing = find_existing(pts_time)
        if existing is None:
            merged.append(
                {"pts": pts, "pts_time": pts_time, "selection_reasons": ["text_region_change"]}
            )
        elif "text_region_change" not in existing["selection_reasons"]:
            existing["selection_reasons"].append("text_region_change")

    merged.sort(key=lambda e: e["pts_time"])
    return merged


def apply_budget(merged_candidates, budget_cap):
    """
    Keep all scene_change / text_region_change candidates first (up to the
    cap), then fill remaining budget with uniform-coverage-only candidates
    spaced as evenly as possible.
    """
    priority = [
        c
        for c in merged_candidates
        if "scene_change" in c["selection_reasons"] or "text_region_change" in c["selection_reasons"]
    ]
    uniform_only = [
        c
        for c in merged_candidates
        if "scene_change" not in c["selection_reasons"]
        and "text_region_change" not in c["selection_reasons"]
    ]

    if len(priority) >= budget_cap:
        # Even priority candidates exceed the cap: keep them evenly spaced
        # across the timeline rather than truncating chronologically.
        priority_sorted = sorted(priority, key=lambda e: e["pts_time"])
        if len(priority_sorted) == budget_cap:
            selected = priority_sorted
        else:
            step = len(priority_sorted) / float(budget_cap)
            indices = sorted({int(i * step) for i in range(budget_cap)})
            selected = [priority_sorted[i] for i in indices][:budget_cap]
        return sorted(selected, key=lambda e: e["pts_time"])

    remaining_slots = budget_cap - len(priority)
    uniform_sorted = sorted(uniform_only, key=lambda e: e["pts_time"])

    if len(uniform_sorted) <= remaining_slots:
        selected = priority + uniform_sorted
    else:
        step = len(uniform_sorted) / float(remaining_slots)
        indices = sorted({int(i * step) for i in range(remaining_slots)})
        picked = [uniform_sorted[i] for i in indices][:remaining_slots]
        selected = priority + picked

    return sorted(selected, key=lambda e: e["pts_time"])


def flag_crop_candidates(retained_candidates, cap):
    """
    Mark up to `cap` text_region_change candidates as close-up crop
    candidates. Ordering is by original detection order (showinfo does not
    expose the raw scene score value to us via this parsing approach, so we
    fall back to timestamp order).
    """
    text_region_entries = [
        c for c in retained_candidates if "text_region_change" in c["selection_reasons"]
    ]
    crop_ids = set(id(c) for c in text_region_entries[:cap])
    for c in retained_candidates:
        if id(c) in crop_ids:
            c["is_crop_candidate"] = True
            c["suggested_crop"] = "lower_third"
        else:
            c["is_crop_candidate"] = False
            c["suggested_crop"] = None


def extract_frames(video_path, retained_candidates, frames_dir, errors):
    """
    Extract all retained PTS values as PNG files in one ffmpeg call using a
    batched select expression, matching the original PoC's frame_extraction
    pattern. Fatal on failure.
    """
    os.makedirs(frames_dir, exist_ok=True)

    if not retained_candidates:
        raise RuntimeError("no candidates retained; nothing to extract")

    select_terms = "+".join(
        "eq(pts\\,%d)" % c["pts"] for c in retained_candidates
    )
    select_expr = "select='%s',showinfo" % select_terms
    output_pattern = os.path.join(frames_dir, "hybrid_%03d.png")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        select_expr,
        "-vsync",
        "vfr",
        output_pattern,
    ]
    returncode, _, stderr = run_subprocess(cmd)
    if returncode != 0:
        raise RuntimeError("fatal: frame extraction failed: %s" % stderr.strip()[-1000:])

    extraction_events = parse_showinfo_events(stderr)
    return extraction_events


def extract_crops(video_path, retained_candidates, crops_dir):
    crop_candidates = [c for c in retained_candidates if c["is_crop_candidate"]]
    os.makedirs(crops_dir, exist_ok=True)
    if not crop_candidates:
        return []

    select_terms = "+".join("eq(pts\\,%d)" % c["pts"] for c in crop_candidates)
    filter_expr = "select='%s',crop=iw:ih*0.35:0:ih*0.65,showinfo" % select_terms
    output_pattern = os.path.join(crops_dir, "crop_%03d.png")
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-vf", filter_expr,
        "-vsync", "vfr", output_pattern,
    ]
    returncode, _, stderr = run_subprocess(cmd)
    if returncode != 0:
        raise RuntimeError("fatal: crop extraction failed: %s" % stderr.strip()[-1000:])
    return crop_candidates


def sha256_of_file(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Extract hybrid frame candidates via ffmpeg.")
    parser.add_argument("video_mp4_path")
    parser.add_argument("output_dir")
    parser.add_argument("--manifest", dest="manifest", default=None)
    args = parser.parse_args()

    video_path = args.video_mp4_path
    output_dir = args.output_dir
    manifest_ref_path = args.manifest

    os.makedirs(output_dir, exist_ok=True)
    errors = []

    try:
        time_base_str, time_base_float, r_frame_rate, probe_duration = probe_video_stream(
            video_path, errors
        )
    except Exception as exc:
        sys.stderr.write("FATAL: could not probe video stream: %s\n" % exc)
        sys.exit(1)

    reference_manifest = None
    if manifest_ref_path:
        try:
            with open(manifest_ref_path) as f:
                reference_manifest = json.load(f)
        except Exception as exc:
            errors.append({"step": "load_reference_manifest", "error": str(exc)})

    uniform_events = generate_uniform_candidates(video_path, UNIFORM_FPS, time_base_float, errors)
    total_2fps_candidates_generated = len(uniform_events)

    uniform_coverage = build_uniform_coverage_subset(uniform_events)
    scene_change_events = generate_scene_change_candidates(
        video_path, SCENE_CHANGE_THRESHOLD, time_base_float, errors
    )
    text_region_events = generate_text_region_candidates(
        video_path, TEXT_REGION_SCENE_THRESHOLD, time_base_float, errors
    )

    merged = merge_candidate_sets(uniform_coverage, scene_change_events, text_region_events)
    merged_before_budget_count = len(merged)

    retained = apply_budget(merged, BUDGET_CAP)
    final_retained_count = len(retained)

    flag_crop_candidates(retained, CROP_CANDIDATE_CAP)

    # Cross-check ffmpeg-reported pts_time against our own pts * time_base.
    for entry in retained:
        computed = entry["pts"] * time_base_float
        reported = entry["pts_time"]
        entry["timestamp_seconds_computed"] = computed
        entry["timestamp_seconds_ffmpeg_reported"] = reported
        entry["pts_time_mismatch_warning"] = abs(computed - reported) > 0.01

    frames_dir = os.path.join(output_dir, "frames", "hybrid")
    crops_dir = os.path.join(output_dir, "frames", "crops")
    try:
        extract_frames(video_path, retained, frames_dir, errors)
        crop_candidates = extract_crops(video_path, retained, crops_dir)
    except Exception as exc:
        sys.stderr.write("FATAL: %s\n" % exc)
        sys.exit(1)

    # Match extracted PNG files (in timestamp order) to retained candidates.
    png_files = sorted(
        f for f in os.listdir(frames_dir) if f.startswith("hybrid_") and f.endswith(".png")
    )
    crop_files = sorted(
        f for f in os.listdir(crops_dir) if f.startswith("crop_") and f.endswith(".png")
    )
    crop_by_pts = {}
    for candidate, filename in zip(crop_candidates, crop_files):
        crop_path = os.path.join(crops_dir, filename)
        crop_by_pts[candidate["pts"]] = {
            "filename": filename,
            "sha256": sha256_of_file(crop_path),
            "size_bytes": os.path.getsize(crop_path),
            "crop": "iw:ih*0.35:0:ih*0.65",
        }

    frames_output = []
    for index, (candidate, filename) in enumerate(zip(retained, png_files), start=1):
        file_path = os.path.join(frames_dir, filename)
        frames_output.append(
            {
                "index": index,
                "filename": filename,
                "pts": candidate["pts"],
                "timestamp_seconds_computed": candidate["timestamp_seconds_computed"],
                "timestamp_seconds_ffmpeg_reported": candidate["timestamp_seconds_ffmpeg_reported"],
                "pts_time_mismatch_warning": candidate["pts_time_mismatch_warning"],
                "selection_reasons": candidate["selection_reasons"],
                "is_crop_candidate": candidate["is_crop_candidate"],
                "suggested_crop": candidate["suggested_crop"],
                "crop_artifact": crop_by_pts.get(candidate["pts"]),
                "sha256": sha256_of_file(file_path),
                "size_bytes": os.path.getsize(file_path),
            }
        )

    if len(png_files) != len(retained):
        errors.append(
            {
                "step": "frame_count_mismatch",
                "detail": "expected %d extracted frames, found %d PNG files"
                % (len(retained), len(png_files)),
            }
        )
    if len(crop_files) != len(crop_candidates):
        errors.append(
            {
                "step": "crop_count_mismatch",
                "detail": "expected %d extracted crops, found %d PNG files"
                % (len(crop_candidates), len(crop_files)),
            }
        )

    manifest_out = {
        "video_path": video_path,
        "time_base": time_base_str,
        "total_2fps_candidates_generated": total_2fps_candidates_generated,
        "uniform_coverage_candidates_count": len(uniform_coverage),
        "scene_change_candidates_count": len(scene_change_events),
        "text_region_change_candidates_count": len(text_region_events),
        "merged_before_budget_count": merged_before_budget_count,
        "final_retained_count": final_retained_count,
        "budget_cap": BUDGET_CAP,
        "crop_candidate_cap": CROP_CANDIDATE_CAP,
        "crop_candidate_count": len(crop_candidates),
        "parameters": {
            "uniform_fps": UNIFORM_FPS,
            "uniform_min_gap_seconds": UNIFORM_MIN_GAP_SECONDS,
            "scene_change_threshold": SCENE_CHANGE_THRESHOLD,
            "text_region_scene_threshold": TEXT_REGION_SCENE_THRESHOLD,
            "dedup_match_window_seconds": DEDUP_MATCH_WINDOW_SECONDS,
            "pts_formula": "timestamp_seconds = pts * time_base",
        },
        "sub_0_5s_coverage_gap_note": SUB_0_5S_COVERAGE_GAP_NOTE,
        "text_region_crop_heuristic_note": TEXT_REGION_CROP_NOTE,
        "frames": frames_output,
        "errors": errors,
    }

    if reference_manifest is not None:
        manifest_out["reference_manifest_path"] = manifest_ref_path

    manifest_path = os.path.join(output_dir, "sampling-manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest_out, f, indent=2)

    print("Wrote manifest: %s (%d frames retained)" % (manifest_path, final_retained_count))


if __name__ == "__main__":
    main()
