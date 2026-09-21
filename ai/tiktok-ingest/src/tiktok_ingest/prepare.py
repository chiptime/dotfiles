"""CPU preparation inside the pinned FFmpeg container (Milestone 2).

Boundaries implemented here (MVP-PRD sections 3.B and 5, PRD section 6.2):

- FFmpeg/ffprobe execute via podman from ``config.FFMPEG_IMAGE_REF``. The
  FULL immutable image ID is resolved READ-ONLY at runtime before first
  use and recorded in the manifest; resolution failure fails closed. The
  recorded benchmark ID prefix is a cross-check only — a digest is never
  invented from it.
- Containers run network-isolated (``--network none``), read-only root
  filesystem with a writable working mount and tmpfs ``/tmp``.
- Hybrid baseline frame selection follows ``sampling`` (methodology
  section 3.2 parameters); native-resolution frames keep source PTS,
  timestamps and selection reasons; uncovered intervals are exposed.
- Audio normalization produces 16 kHz mono signed 16-bit PCM WAV from the
  SAME media file (one media asset serves both modalities).
- Stage deadline (20 minutes) and derived-output cap (2 GiB per clip) are
  enforced between steps; both surface as explicit ``budget_exceeded``
  outcomes with reasons.
- All artifacts persist atomically through the state modules, and
  ``meta.json`` records stage outcomes, hashes, versions and timings.
  Reuse decisions go through the existing resume fingerprints: an
  unchanged prepare fingerprint with intact artifacts performs zero
  re-preparation.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import config
from .contracts import (
    JobManifest,
    StageOutcome,
    StageRecord,
    StageResult,
    utc_now_iso,
)
from .resume import build_fingerprint, fingerprints_equal
from .runtime import subprocess_runner
from .sampling import (
    Candidate,
    SelectedFrame,
    select_baseline_frames,
    snap_candidates_to_stream,
)
from .state import Blocklist, CacheStore, Processed, StateRoot, atomic_write_bytes
from .validation import sha256_of_file

Runner = Callable[..., tuple[int, str, str]]

_FULL_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_SHOWINFO_PTS_RE = re.compile(r"pts:\s*(\d+)\s+pts_time:\s*([\d.]+)")
_MEDIA_SUFFIX = ".mp4"
_AUDIO_NAME = "audio.wav"
_MANIFEST_NAME = "sampling-manifest.json"
_FRAMES_SUBDIR = "frames"
_SOURCE_MOUNT = "/media"
_WORK_MOUNT = "/work"


class PrepareError(RuntimeError):
    """Raised when preparation cannot continue; carries an auditable reason."""


class StageDeadlineExceeded(PrepareError):
    pass


class DerivedOutputBudgetExceeded(PrepareError):
    pass


# --------------------------------------------------------------------------
# Deadline and derived-output budgets
# --------------------------------------------------------------------------


class StageClock:
    """Deadline clock for a stage (injectable monotonic).

    ``label`` names the stage in the explicit budget-exceeded message so
    the milestone-3 vision/audio stages can reuse this clock verbatim.
    """

    def __init__(
        self,
        deadline_seconds: float,
        *,
        label: str = "CPU preparation",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.deadline_seconds = float(deadline_seconds)
        self.label = label
        self._monotonic = monotonic
        self._start = monotonic()

    def elapsed(self) -> float:
        return self._monotonic() - self._start

    def check(self) -> float:
        elapsed = self.elapsed()
        if elapsed > self.deadline_seconds:
            raise StageDeadlineExceeded(
                f"{self.label} deadline of {self.deadline_seconds:.0f}s "
                f"exceeded (elapsed {elapsed:.1f}s); stopping with an explicit "
                "budget outcome, never truncating silently"
            )
        return elapsed

    def remaining(self) -> float:
        return max(0.0, self.deadline_seconds - self.elapsed())


class DerivedOutputTracker:
    """Enforces the 2 GiB derived-output cap over the run directory."""

    def __init__(self, root: Path | str, max_bytes: int) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes

    def current_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def check(self) -> int:
        total = self.current_bytes()
        if total > self.max_bytes:
            raise DerivedOutputBudgetExceeded(
                f"derived outputs reached {total} bytes, above the "
                f"{self.max_bytes}-byte per-clip cap; stopping with an "
                "explicit budget outcome"
            )
        return total


# --------------------------------------------------------------------------
# Podman container boundary
# --------------------------------------------------------------------------


def resolve_image_id(
    image_ref: str, *, runner: Runner = subprocess_runner
) -> str:
    """Resolve and return the FULL immutable image ID (fail closed).

    Read-only resolution happens BEFORE any execution; anything other than
    a full 64-character ID fails closed so the pipeline never runs against
    a moving tag or an invented digest (MVP-PRD section 3.B).
    """
    returncode, out, err = runner(
        ["podman", "inspect", "--format", "{{.Id}}", image_ref]
    )
    if returncode != 0:
        raise PrepareError(
            f"failed to resolve container image {image_ref!r}: "
            f"{(err or '').strip()[-400:]}; failing closed before execution"
        )
    image_id = out.strip().lower()
    if not _FULL_ID_RE.match(image_id):
        raise PrepareError(
            f"resolved image identifier {image_id[:12]!r}… is not a full "
            "immutable ID; refusing to proceed on a moving tag or prefix"
        )
    return image_id


def container_argv(
    image_id: str,
    argv: list[str],
    *,
    read_only_mounts: tuple[tuple[Path | str, str], ...] = (),
    writable_mount: tuple[Path | str, str] | None = None,
) -> list[str]:
    """One network-isolated, read-only container invocation."""
    if not _FULL_ID_RE.match(image_id):
        raise PrepareError(
            "container commands require a resolved full immutable image ID"
        )
    command = [
        "podman",
        "run",
        "--rm",
        # The nvidia/cuda base image entrypoint echoes a CUDA banner on
        # STDOUT before exec'ing the command, which would corrupt every
        # stdout-parsed tool output (ffprobe JSON, ffmpeg -version). Our
        # invocations are CPU-only and need no NVIDIA entrypoint setup, so
        # the entrypoint is cleared and the command runs directly.
        "--entrypoint",
        "",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp",
    ]
    for source, target in read_only_mounts:
        command += ["-v", f"{source}:{target}:ro"]
    if writable_mount is not None:
        source, target = writable_mount
        command += ["-v", f"{source}:{target}"]
    command.append(image_id)
    command.extend(argv)
    return command


def parse_showinfo_events(stderr_text: str) -> list[tuple[int, float]]:
    """Extract ``(pts, pts_time)`` pairs from ffmpeg showinfo stderr."""
    events: list[tuple[int, float]] = []
    for match in _SHOWINFO_PTS_RE.finditer(stderr_text or ""):
        events.append((int(match.group(1)), float(match.group(2))))
    return events


def rebase_resampled_events(
    events: list[tuple[int, float]],
    source_fps: int,
    original_time_base: float,
) -> list[tuple[int, float]]:
    """Convert fps-scan events from the resampled time base to the original.

    The ``fps`` filter rewrites PTS onto a synthetic ``1/fps`` time base, so
    scanned PTS must be mapped back. The conversion uses the EXACT integer
    PTS showinfo reports — never the printed ``pts_time`` float, which
    ffmpeg truncates to ~6 significant digits and which drifts by ±1 tick
    here (first real run: 10 of 29 planned frames lost to that drift).
    Rebasing uniformly keeps one code path and guarantees every recorded
    PTS is valid on the original stream (methodology section 8, pitfall 5).
    """
    source_time_base = 1.0 / float(source_fps)
    return [
        (round(pts * source_time_base / original_time_base), pts_time)
        for pts, pts_time in events
    ]


def original_stream_events(
    events: list[tuple[int, float]],
) -> list[tuple[int, float]]:
    """Pass through events from scans that ran on the ORIGINAL stream.

    Scene, text-region and extraction passes decode the original video, so
    the integer PTS showinfo reports IS the original-stream PTS. Recomputing
    it from the low-precision printed ``pts_time`` would corrupt it (see
    :func:`rebase_resampled_events`); the exact integer is kept instead.
    """
    return list(events)


class ContainerFFmpeg:
    """FFmpeg/ffprobe executed inside the pinned, isolated container."""

    def __init__(
        self,
        image_id: str,
        *,
        runner: Runner,
        media_path: Path,
        run_dir: Path,
        clock: StageClock,
    ) -> None:
        self.image_id = image_id
        self._runner = runner
        self.media_path = Path(media_path)
        self.run_dir = Path(run_dir)
        self.clock = clock

    # -- invocation --------------------------------------------------------

    def _to_source(self, argv: list[str]) -> list[str]:
        """Replace host media paths with their read-only container mount."""
        source_host = str(self.media_path)
        source_mount = f"{_SOURCE_MOUNT}/{self.media_path.name}"
        return [source_mount if item == source_host else item for item in argv]

    def _to_work(self, argv: list[str]) -> list[str]:
        """Replace host output paths under run_dir with /work equivalents."""
        result = []
        for item in argv:
            try:
                relative = Path(item).relative_to(self.run_dir)
            except (ValueError, OSError):
                result.append(item)
            else:
                result.append(f"{_WORK_MOUNT}/{relative.as_posix()}")
        return result

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        self.clock.check()
        timeout = max(1.0, self.clock.remaining())
        argv = self._to_work(self._to_source(list(argv)))
        return self._runner(
            container_argv(
                self.image_id,
                argv,
                read_only_mounts=((self.media_path.parent, _SOURCE_MOUNT),),
                writable_mount=(self.run_dir, _WORK_MOUNT),
            ),
            timeout=timeout,
        )

    # -- stage steps --------------------------------------------------------

    def version(self) -> str:
        returncode, out, err = self.run(["ffmpeg", "-version"])
        if returncode != 0:
            raise PrepareError(
                f"ffmpeg -version failed inside the container: "
                f"{(err or out).strip()[-300:]}"
            )
        lines = (out or err).strip().splitlines()
        return lines[0].strip() if lines else "unknown"

    def integrity_pass(self) -> list[int]:
        """Full decode check that also records every original frame PTS.

        The decode already happens, so attaching ``showinfo`` costs ~nothing
        and yields the authoritative stream PTS set used to snap uniform
        sampling candidates onto REAL frames (a 59.94 fps stream has frame
        timestamps that almost never coincide with exact fps-grid seconds).
        A corrupt download must fail here, loudly.
        """
        returncode, _, err = self.run(
            [
                "ffmpeg",
                "-v",
                "info",
                "-i",
                str(self.media_path),
                "-vf",
                "showinfo",
                "-f",
                "null",
                "-",
            ]
        )
        events = parse_showinfo_events(err)
        if returncode != 0 and not events:
            raise PrepareError(
                f"media integrity pass failed: {(err or '').strip()[-400:]}"
            )
        if returncode != 0:
            raise PrepareError(
                "media integrity pass failed after decoding "
                f"{len(events)} frames: {(err or '').strip()[-300:]}"
            )
        return sorted({pts for pts, _ in events})

    def probe_streams(self) -> dict[str, Any]:
        returncode, out, err = self.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=time_base,r_frame_rate,duration",
                "-of",
                "json",
                str(self.media_path),
            ]
        )
        if returncode != 0:
            raise PrepareError(
                f"ffprobe stream probe failed: {(err or '').strip()[-400:]}"
            )
        try:
            document = json.loads(out)
        except json.JSONDecodeError as exc:
            raise PrepareError(f"ffprobe returned invalid JSON: {exc}") from exc
        streams = document.get("streams") or []
        if not streams:
            raise PrepareError("ffprobe reported no video stream")
        stream = streams[0]
        time_base_text = stream.get("time_base", "1/1")
        num, den = time_base_text.split("/")
        duration = stream.get("duration")
        return {
            "time_base": time_base_text,
            "time_base_float": float(num) / float(den),
            "r_frame_rate": stream.get("r_frame_rate"),
            "duration_seconds": float(duration) if duration is not None else None,
        }

    def _scan(self, filter_expression: str) -> tuple[int, list[tuple[int, float]]]:
        returncode, _, err = self.run(
            [
                "ffmpeg",
                "-i",
                str(self.media_path),
                "-vf",
                filter_expression,
                "-f",
                "null",
                "-",
            ]
        )
        events = parse_showinfo_events(err)
        # Tolerate non-zero exits that still emitted showinfo events (the
        # null muxer can surface benign warnings); a silent failure is fatal.
        if returncode != 0 and not events:
            raise PrepareError(
                f"scan failed without showinfo output: {(err or '').strip()[-400:]}"
            )
        return returncode, events

    def scan_uniform(self, fps: int, time_base: float) -> list[tuple[int, float]]:
        _, events = self._scan(f"fps={fps},showinfo")
        return rebase_resampled_events(events, fps, time_base)

    def scan_scene(self, threshold: float) -> list[tuple[int, float]]:
        _, events = self._scan(f"select='gt(scene,{threshold})',showinfo")
        return original_stream_events(events)

    def scan_text_region(self, threshold: float) -> list[tuple[int, float]]:
        expression = (
            f"crop=iw:ih*{config.SAMPLING_CROP_HEIGHT_FRACTION}:0:"
            f"ih*{config.SAMPLING_CROP_Y_OFFSET_FRACTION},"
            f"select='gt(scene\\,{threshold})',showinfo"
        )
        _, events = self._scan(expression)
        return original_stream_events(events)

    def extract_frames(
        self, frames: tuple[SelectedFrame, ...]
    ) -> list[tuple[int, float]]:
        """Extract all retained PTS values in ONE batched select pass."""
        frames_dir = self.run_dir / _FRAMES_SUBDIR
        frames_dir.mkdir(parents=True, exist_ok=True)
        # A recomputation after a partial failure must not count stale PNGs
        # from the previous attempt; clear the directory first.
        for stale in frames_dir.glob("hybrid_*.png"):
            stale.unlink()
        if not frames:
            raise PrepareError("no frames retained; nothing to extract")
        select_terms = "+".join(f"eq(pts\\,{frame.pts})" for frame in frames)
        target = str(frames_dir / "hybrid_%03d.png")
        returncode, _, err = self.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(self.media_path),
                "-vf",
                f"select='{select_terms}',showinfo",
                "-vsync",
                "vfr",
                target,
            ]
        )
        if returncode != 0:
            raise PrepareError(
                f"frame extraction failed: {(err or '').strip()[-500:]}"
            )
        return parse_showinfo_events(err)

    def extract_audio(self) -> Path:
        """Normalize audio from the SAME media file to 16 kHz mono s16 WAV."""
        target = self.run_dir / _AUDIO_NAME
        returncode, _, err = self.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(self.media_path),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(target),
            ]
        )
        if returncode != 0:
            raise PrepareError(
                f"audio normalization failed: {(err or '').strip()[-500:]}"
            )
        if not target.is_file():
            raise PrepareError("audio normalization produced no output file")
        with target.open("rb") as handle:
            header = handle.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise PrepareError(
                "audio output is not a RIFF/WAVE file; refusing to bind it"
            )
        return target


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PrepareOutcome:
    """Result of one prepare invocation, reusable by CLI and tests."""

    outcome: str
    reason: str
    reused: bool
    video_id: str
    run_dir: str | None = None
    frame_count: int | None = None
    uncovered_count: int | None = None


def prepare_fingerprint(media_sha256: str, image_id: str) -> dict[str, Any]:
    """Resume fingerprint for the prepare stage (input hash + versions)."""
    return build_fingerprint(
        "prepare",
        media_sha256,
        {
            "ffmpeg_image_id": image_id,
            "sampler_rules": config.SAMPLING_RULES_VERSION,
        },
    )


def _load_manifest(
    manifest_path: Path, video_id: str, media_sha256: str
) -> JobManifest:
    if manifest_path.exists():
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        return JobManifest.from_dict(document)
    now = utc_now_iso()
    return JobManifest(
        video_id=video_id,
        media_sha256=media_sha256,
        created_at=now,
        updated_at=now,
    )


def _write_manifest(manifest_path: Path, manifest: JobManifest) -> None:
    atomic_write_bytes(
        manifest_path,
        json.dumps(
            manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n",
    )


def _record_stage(
    manifest: JobManifest,
    manifest_path: Path,
    *,
    outcome: StageOutcome,
    reason: str,
    fingerprint: dict[str, Any] | None,
    input_sha256: str,
    output_sha256: str | None,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
) -> None:
    manifest.stages["prepare"] = StageRecord(
        stage="prepare",
        result=StageResult(outcome=outcome, reason=reason),
        fingerprint=fingerprint or {},
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(duration_seconds, 3),
    )
    manifest.updated_at = finished_at
    _write_manifest(manifest_path, manifest)


def _artifacts_intact(run_dir: Path) -> tuple[bool, int | None, str]:
    """Verify the recorded prepare artifacts are still present and counted."""
    manifest_file = run_dir / _MANIFEST_NAME
    audio = run_dir / _AUDIO_NAME
    frames = run_dir / _FRAMES_SUBDIR
    if not manifest_file.is_file():
        return False, None, "sampling manifest missing"
    if not audio.is_file() or audio.stat().st_size == 0:
        return False, None, "normalized audio missing or empty"
    png_files = list(frames.glob("hybrid_*.png")) if frames.is_dir() else []
    if not png_files:
        return False, None, "no retained frame files present"
    frame_count = _frame_count_from_manifest(run_dir)
    if frame_count is not None and len(png_files) != frame_count:
        return (
            False,
            frame_count,
            f"frame count drifted on disk ({len(png_files)} != {frame_count})",
        )
    return True, frame_count, "prepare artifacts intact"


def _frame_count_from_manifest(run_dir: Path) -> int | None:
    manifest_file = run_dir / _MANIFEST_NAME
    if not manifest_file.is_file():
        return None
    try:
        document = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    frames = document.get("frames")
    return len(frames) if isinstance(frames, list) else None


def prepare_video(
    video_id: str,
    *,
    state: StateRoot,
    runner: Runner = subprocess_runner,
    force: bool = False,
) -> PrepareOutcome:
    """Prepare one fetched video: frames + audio WAV with full provenance.

    Reuse follows the milestone-1 resume rules: a completed prepare stage
    whose fingerprint (media hash + image ID + sampler rules) still matches
    AND whose artifacts are intact is reused with zero container work.
    Anything else recomputes and updates ``meta.json`` atomically.
    """
    state.ensure_layout()
    blocklist = Blocklist(state)
    if blocklist.is_blocked(video_id):
        return PrepareOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="video ID is permanently blocklisted; no flag can "
            "override an operator rejection",
            reused=False,
            video_id=video_id,
        )

    processed = Processed(state)
    entry = processed.get(video_id)
    if entry is None:
        return PrepareOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="no fetch record for this ID; run fetch-url first",
            reused=False,
            video_id=video_id,
        )

    cache = CacheStore(state)
    media_path = cache.path_for(entry.media_sha256, _MEDIA_SUFFIX)
    if not media_path.is_file():
        return PrepareOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="media missing from the working cache; re-run fetch-url",
            reused=False,
            video_id=video_id,
        )
    actual_hash = sha256_of_file(media_path)
    if actual_hash != entry.media_sha256:
        return PrepareOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="cached media bytes no longer match the recorded hash; "
            "re-run fetch-url to re-acquire",
            reused=False,
            video_id=video_id,
        )

    if not entry.artifacts:
        return PrepareOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="fetch record has no run directory reference",
            reused=False,
            video_id=video_id,
        )
    run_dir = state.root / entry.artifacts[0]
    manifest_path = run_dir / "meta.json"
    manifest = _load_manifest(manifest_path, video_id, entry.media_sha256)

    # Read-only image resolution BEFORE any use; also part of the fingerprint.
    image_id = resolve_image_id(config.FFMPEG_IMAGE_REF, runner=runner)
    fingerprint = prepare_fingerprint(entry.media_sha256, image_id)

    reason = "recomputing prepare (fingerprint change or explicit force)"
    recorded = manifest.stages.get("prepare")
    if not force and recorded is not None and fingerprints_equal(
        recorded.fingerprint, fingerprint
    ):
        if recorded.result.outcome is StageOutcome.COMPLETE:
            intact, frame_count, intact_reason = _artifacts_intact(run_dir)
            if intact:
                return PrepareOutcome(
                    outcome=StageOutcome.COMPLETE.value,
                    reason="prepare fingerprint and artifacts match the "
                    "recorded run; zero re-preparation performed",
                    reused=True,
                    video_id=video_id,
                    run_dir=str(run_dir),
                    frame_count=frame_count,
                )
            reason = f"recorded prepare output unusable ({intact_reason}); recomputing"
        else:
            reason = "previous prepare did not complete; recomputing"

    started_at = utc_now_iso()
    clock = StageClock(config.BUDGETS.cpu_prep_deadline_seconds)
    tracker = DerivedOutputTracker(run_dir, config.BUDGETS.cpu_prep_max_bytes)
    ffmpeg = ContainerFFmpeg(
        image_id,
        runner=runner,
        media_path=media_path,
        run_dir=run_dir,
        clock=clock,
    )

    try:
        ffmpeg_version = ffmpeg.version()
        clock.check()
        stream_pts = ffmpeg.integrity_pass()
        tracker.check()
        probe = ffmpeg.probe_streams()
        time_base = probe["time_base_float"]
        duration = probe["duration_seconds"]
        if duration is None or duration <= 0:
            raise PrepareError("ffprobe reported no usable duration")

        uniform_events = ffmpeg.scan_uniform(config.SAMPLING_UNIFORM_FPS, time_base)
        scene_events = ffmpeg.scan_scene(config.SAMPLING_SCENE_THRESHOLD)
        text_events = ffmpeg.scan_text_region(
            config.SAMPLING_TEXT_REGION_THRESHOLD
        )
        clock.check()

        # fps-scan output timestamps are grid seconds, NOT stream frame PTS;
        # snap them onto real frames (rules version 2) before selection.
        uniform_candidates = snap_candidates_to_stream(
            [Candidate(pts=pts, pts_time=pts_time) for pts, pts_time in uniform_events],
            stream_pts,
            time_base,
        )

        selection = select_baseline_frames(
            uniform_candidates,
            [Candidate(pts=pts, pts_time=pts_time) for pts, pts_time in scene_events],
            [Candidate(pts=pts, pts_time=pts_time) for pts, pts_time in text_events],
            duration_seconds=duration,
            budget_cap=config.MAX_BASELINE_FRAMES,
            # Coverage tolerance = design spacing + one grid period: uniform
            # snapping and 0.5 s grid quantization make retained gaps up to
            # ~4.5 s NORMAL (not coverage holes); only larger gaps are
            # genuinely uncovered.
            max_coverage_gap_seconds=(
                config.SAMPLING_UNIFORM_MIN_GAP_SECONDS
                + 1.0 / config.SAMPLING_UNIFORM_FPS
            ),
        )
        if not selection.frames:
            raise PrepareError("hybrid selection retained no frames")

        extraction_events = ffmpeg.extract_frames(selection.frames)
        extracted = dict(original_stream_events(extraction_events))
        tracker.check()
        clock.check()

        frames_dir = run_dir / _FRAMES_SUBDIR
        png_files = sorted(frames_dir.glob("hybrid_*.png"))
        if len(png_files) != len(selection.frames):
            raise PrepareError(
                f"frame count mismatch: expected {len(selection.frames)}, "
                f"found {len(png_files)} on disk"
            )

        audio_path = ffmpeg.extract_audio()
        tracker.check()

        frame_records: list[dict[str, Any]] = []
        for index, (frame, png) in enumerate(zip(selection.frames, png_files), start=1):
            frame_records.append(
                {
                    "index": index,
                    "filename": png.name,
                    "pts": frame.pts,
                    "timestamp_seconds": frame.pts_time,
                    "timestamp_seconds_computed": frame.pts * time_base,
                    "ffmpeg_reported_seconds": extracted.get(frame.pts),
                    "selection_reasons": list(frame.selection_reasons),
                    "is_crop_candidate": frame.is_crop_candidate,
                    "crop_metadata_only": frame.crop,
                    "sha256": sha256_of_file(png),
                    "size_bytes": png.stat().st_size,
                }
            )

        manifest.tool_versions.update(
            {
                "ffmpeg": ffmpeg_version,
                "ffmpeg_container_image": config.FFMPEG_IMAGE_REF,
                "ffmpeg_container_image_id": image_id,
            }
        )
        sampling_manifest = {
            "video_id": video_id,
            "media_sha256": entry.media_sha256,
            "media_cache_path": str(media_path),
            "container_image": config.FFMPEG_IMAGE_REF,
            "container_image_id": image_id,
            "ffmpeg_version": ffmpeg_version,
            "time_base": probe["time_base"],
            "duration_seconds": duration,
            "source_resolution": "native (no scale filter applied)",
            "parameters": {
                "sampler_rules_version": config.SAMPLING_RULES_VERSION,
                "uniform_fps": config.SAMPLING_UNIFORM_FPS,
                "uniform_min_gap_seconds": config.SAMPLING_UNIFORM_MIN_GAP_SECONDS,
                "scene_threshold": config.SAMPLING_SCENE_THRESHOLD,
                "text_region_threshold": config.SAMPLING_TEXT_REGION_THRESHOLD,
                "dedup_window_seconds": config.SAMPLING_DEDUP_WINDOW_SECONDS,
                "budget_cap": config.MAX_BASELINE_FRAMES,
                "crop_candidate_cap": config.SAMPLING_CROP_CANDIDATE_CAP,
                "max_coverage_gap_seconds": config.SAMPLING_MAX_COVERAGE_GAP_SECONDS,
                "pts_formula": "timestamp_seconds = pts * time_base",
            },
            "counts": selection.counts,
            "uncovered_intervals": [
                {
                    "start_seconds": interval.start_seconds,
                    "end_seconds": interval.end_seconds,
                    "length_seconds": interval.length_seconds,
                }
                for interval in selection.uncovered
            ],
            "frames": frame_records,
            "audio": {
                "filename": _AUDIO_NAME,
                "format": "pcm_s16le",
                "channels": 1,
                "sample_rate_hz": 16000,
                "size_bytes": audio_path.stat().st_size,
            },
        }
        clock.check()
        atomic_write_bytes(
            run_dir / _MANIFEST_NAME,
            json.dumps(
                sampling_manifest, ensure_ascii=False, indent=2, sort_keys=True
            ).encode("utf-8")
            + b"\n",
        )
        tracker.check()
    except (StageDeadlineExceeded, DerivedOutputBudgetExceeded) as exc:
        reason = str(exc)
        _record_stage(
            manifest, manifest_path,
            outcome=StageOutcome.BUDGET_EXCEEDED, reason=reason,
            fingerprint=None, input_sha256=entry.media_sha256,
            output_sha256=None, started_at=started_at,
            finished_at=utc_now_iso(), duration_seconds=clock.elapsed(),
        )
        return PrepareOutcome(
            outcome=StageOutcome.BUDGET_EXCEEDED.value, reason=reason,
            reused=False, video_id=video_id, run_dir=str(run_dir),
        )
    except PrepareError as exc:
        reason = str(exc)
        _record_stage(
            manifest, manifest_path,
            outcome=StageOutcome.FAILED, reason=reason,
            fingerprint=None, input_sha256=entry.media_sha256,
            output_sha256=None, started_at=started_at,
            finished_at=utc_now_iso(), duration_seconds=clock.elapsed(),
        )
        return PrepareOutcome(
            outcome=StageOutcome.FAILED.value, reason=reason,
            reused=False, video_id=video_id, run_dir=str(run_dir),
        )

    output_sha256 = hashlib.sha256(
        (run_dir / _MANIFEST_NAME).read_bytes()
    ).hexdigest()
    finished_at = utc_now_iso()
    _record_stage(
        manifest, manifest_path,
        outcome=StageOutcome.COMPLETE,
        reason=f"hybrid selection retained {len(frame_records)} native-resolution "
        f"frames with {len(selection.uncovered)} uncovered interval(s); "
        "audio normalized to 16 kHz mono s16 WAV",
        fingerprint=fingerprint,
        input_sha256=entry.media_sha256,
        output_sha256=output_sha256,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=clock.elapsed(),
    )

    entry.stage_fingerprints["prepare"] = fingerprint
    manifest_ref = f"{entry.artifacts[0]}/{_MANIFEST_NAME}"
    if manifest_ref not in entry.artifacts:
        entry.artifacts.append(manifest_ref)
    processed.record(entry)

    return PrepareOutcome(
        outcome=StageOutcome.COMPLETE.value,
        reason=f"prepared {len(frame_records)} frames + audio WAV; "
        f"{len(selection.uncovered)} uncovered interval(s) exposed",
        reused=False,
        video_id=video_id,
        run_dir=str(run_dir),
        frame_count=len(frame_records),
        uncovered_count=len(selection.uncovered),
    )
