"""Shared-media validation before any CPU preparation (Milestone 2).

Checks, in order, each with an explicit outcome and reason:

1. Size budget from the filesystem (over budget is ``budget_exceeded``).
2. Signature magic bytes. An HTML response served as ``video`` is an
   explicit ``unsupported`` outcome, never a decode error downstream.
3. Stream inspection via ffprobe (video stream plus a REAL audio track;
   attached cover-art pictures do not count). Missing streams are
   ``unsupported`` capabilities, not successful empty videos.
4. Duration/size budgets (explicit ``budget_exceeded``, never silent
   truncation).
5. sha256 of the accepted bytes; every derived artifact binds to it.

Outcomes reuse the milestone-1 ``StageOutcome`` vocabulary. The ffprobe
runner is injectable: production runs it inside the pinned container
(``prepare.ContainerFFmpeg``), unit tests pass fakes. No network, no
binaries at test time.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Callable

from . import config
from .contracts import StageOutcome

Runner = Callable[..., tuple[int, str, str]]

_HTML_MARKERS: tuple[bytes, ...] = (
    b"<!doctype html",
    b"<html",
    b"<head",
    b"<body",
)


@dataclasses.dataclass(frozen=True)
class CheckOutcome:
    """One validation check. ``ok=False`` carries an explicit outcome."""

    ok: bool
    outcome: str
    reason: str


def _fail(outcome: StageOutcome, reason: str) -> CheckOutcome:
    return CheckOutcome(ok=False, outcome=outcome.value, reason=reason)


def check_size_budget(size_bytes: int, budgets: config.Budgets) -> CheckOutcome:
    if size_bytes > budgets.media_max_bytes:
        return _fail(
            StageOutcome.BUDGET_EXCEEDED,
            f"media is {size_bytes} bytes, above the "
            f"{budgets.media_max_bytes}-byte budget; rejected explicitly",
        )
    return CheckOutcome(ok=True, outcome=StageOutcome.COMPLETE.value, reason="size within budget")


def check_media_signature(head: bytes) -> CheckOutcome:
    """Signature magic-bytes check on the first bytes of the file."""
    if not head:
        return _fail(StageOutcome.FAILED, "media file is empty")
    lowered = head[:512].lstrip().lower()
    if any(lowered.startswith(marker) for marker in _HTML_MARKERS) or lowered[:1] == b"<":
        return _fail(
            StageOutcome.UNSUPPORTED,
            "HTML response served as video media; treating as a signature "
            "failure, not as decodable media",
        )
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return CheckOutcome(
            ok=True,
            outcome=StageOutcome.COMPLETE.value,
            reason=f"MP4/MOV signature (brand {head[8:12].decode('ascii', 'replace')!r})",
        )
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return CheckOutcome(
            ok=True, outcome=StageOutcome.COMPLETE.value, reason="WebM/Matroska (EBML) signature"
        )
    return _fail(
        StageOutcome.UNSUPPORTED,
        f"unrecognized media signature {head[:16]!r}; not a supported container",
    )


@dataclasses.dataclass(frozen=True)
class StreamSummary:
    """Stream facts extracted from one ffprobe JSON document."""

    has_video: bool
    has_audio: bool
    duration_seconds: float | None
    video_codec: str | None
    audio_codec: str | None


def parse_ffprobe_document(text: str) -> dict:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe output is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("ffprobe output must be a JSON object")
    return document


def _stream_is_attached_pic(stream: dict) -> bool:
    disposition = stream.get("disposition")
    if isinstance(disposition, dict) and disposition.get("attached_pic") == 1:
        return True
    # Some muxers mark cover art via codec tag instead of the disposition.
    return "attached_pic" in str(stream.get("codec_tag_string", "")).lower()


def inspect_streams(document: dict) -> StreamSummary:
    """Require a real video stream and a real audio track.

    Cover-art thumbnails are attached-picture VIDEO streams and never
    satisfy the audio requirement (PRD section 6.2: the chosen format must
    carry the video's audio, not merely an associated track).
    """
    streams = document.get("streams")
    streams = streams if isinstance(streams, list) else []

    video_codec: str | None = None
    audio_codec: str | None = None
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        codec_name = stream.get("codec_name")
        if codec_type == "video" and video_codec is None and not _stream_is_attached_pic(stream):
            video_codec = codec_name
        elif codec_type == "audio" and audio_codec is None and not _stream_is_attached_pic(stream):
            audio_codec = codec_name

    duration: float | None = None
    fmt = document.get("format")
    if isinstance(fmt, dict) and fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None
    if duration is None:
        for stream in streams:
            if isinstance(stream, dict) and stream.get("duration") is not None:
                try:
                    duration = float(stream["duration"])
                    break
                except (TypeError, ValueError):
                    continue

    return StreamSummary(
        has_video=video_codec is not None,
        has_audio=audio_codec is not None,
        duration_seconds=duration,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )


def check_stream_requirements(summary: StreamSummary) -> CheckOutcome:
    if not summary.has_video and not summary.has_audio:
        return _fail(
            StageOutcome.UNSUPPORTED,
            "no decodable video or audio stream found by ffprobe",
        )
    if not summary.has_video:
        return _fail(
            StageOutcome.UNSUPPORTED,
            f"no real video stream (audio codec {summary.audio_codec!r} only)",
        )
    if not summary.has_audio:
        return _fail(
            StageOutcome.UNSUPPORTED,
            f"no real audio track (video codec {summary.video_codec!r} only); "
            "a music-only or cover-art asset is an explicit outcome",
        )
    return CheckOutcome(
        ok=True,
        outcome=StageOutcome.COMPLETE.value,
        reason=f"video ({summary.video_codec}) and audio ({summary.audio_codec}) present",
    )


def check_duration_budget(
    duration_seconds: float | None, budgets: config.Budgets
) -> CheckOutcome:
    if duration_seconds is None:
        return _fail(
            StageOutcome.FAILED,
            "ffprobe reported no duration; cannot verify the media budget",
        )
    if duration_seconds > budgets.media_max_duration_seconds:
        return _fail(
            StageOutcome.BUDGET_EXCEEDED,
            f"media duration {duration_seconds:.1f}s exceeds the "
            f"{budgets.media_max_duration_seconds}s budget; rejected explicitly, "
            "never silently truncated",
        )
    return CheckOutcome(
        ok=True,
        outcome=StageOutcome.COMPLETE.value,
        reason=f"duration {duration_seconds:.1f}s within budget",
    )


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Streaming sha256 of a file on disk."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclasses.dataclass(frozen=True)
class MediaValidation:
    """Result of validating one media file. Failed checks stay visible."""

    outcome: str
    reason: str
    media_sha256: str | None
    size_bytes: int | None
    duration_seconds: float | None
    streams: StreamSummary | None
    checks: tuple[CheckOutcome, ...]


def validate_media_file(
    path: Path | str,
    *,
    ffprobe_runner: Runner,
    budgets: config.Budgets = config.BUDGETS,
) -> MediaValidation:
    """Validate one downloaded media file; bind accepted bytes to a sha256."""
    path = Path(path)
    checks: list[CheckOutcome] = []

    if not path.is_file():
        return MediaValidation(
            outcome=StageOutcome.FAILED.value,
            reason=f"media file does not exist: {path}",
            media_sha256=None,
            size_bytes=None,
            duration_seconds=None,
            streams=None,
            checks=checks,
        )

    size_bytes = path.stat().st_size
    size_check = check_size_budget(size_bytes, budgets)
    checks.append(size_check)
    if not size_check.ok:
        return MediaValidation(
            outcome=size_check.outcome,
            reason=size_check.reason,
            media_sha256=None,
            size_bytes=size_bytes,
            duration_seconds=None,
            streams=None,
            checks=tuple(checks),
        )

    with path.open("rb") as handle:
        head = handle.read(64)
    signature_check = check_media_signature(head)
    checks.append(signature_check)
    if not signature_check.ok:
        return MediaValidation(
            outcome=signature_check.outcome,
            reason=signature_check.reason,
            media_sha256=None,
            size_bytes=size_bytes,
            duration_seconds=None,
            streams=None,
            checks=tuple(checks),
        )

    returncode, out, err = ffprobe_runner(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    if returncode != 0:
        reason = f"ffprobe failed with exit code {returncode}: {(err or '').strip()[-400:]}"
        checks.append(_fail(StageOutcome.FAILED, reason))
        return MediaValidation(
            outcome=StageOutcome.FAILED.value,
            reason=reason,
            media_sha256=None,
            size_bytes=size_bytes,
            duration_seconds=None,
            streams=None,
            checks=tuple(checks),
        )

    try:
        document = parse_ffprobe_document(out)
    except ValueError as exc:
        checks.append(_fail(StageOutcome.FAILED, str(exc)))
        return MediaValidation(
            outcome=StageOutcome.FAILED.value,
            reason=str(exc),
            media_sha256=None,
            size_bytes=size_bytes,
            duration_seconds=None,
            streams=None,
            checks=tuple(checks),
        )

    summary = inspect_streams(document)
    stream_check = check_stream_requirements(summary)
    checks.append(stream_check)
    if not stream_check.ok:
        return MediaValidation(
            outcome=stream_check.outcome,
            reason=stream_check.reason,
            media_sha256=None,
            size_bytes=size_bytes,
            duration_seconds=summary.duration_seconds,
            streams=summary,
            checks=tuple(checks),
        )

    duration_check = check_duration_budget(summary.duration_seconds, budgets)
    checks.append(duration_check)
    if not duration_check.ok:
        return MediaValidation(
            outcome=duration_check.outcome,
            reason=duration_check.reason,
            media_sha256=None,
            size_bytes=size_bytes,
            duration_seconds=summary.duration_seconds,
            streams=summary,
            checks=tuple(checks),
        )

    media_sha256 = sha256_of_file(path)
    return MediaValidation(
        outcome=StageOutcome.COMPLETE.value,
        reason="; ".join(
            check.reason for check in checks if check.outcome == StageOutcome.COMPLETE.value
        )
        + f"; bound to sha256 {media_sha256}",
        media_sha256=media_sha256,
        size_bytes=size_bytes,
        duration_seconds=summary.duration_seconds,
        streams=summary,
        checks=tuple(checks),
    )
