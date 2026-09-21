"""Shared fixtures for the Milestone 1 test suite (fixture-only, no I/O).

Nothing here touches the network, GPU, services or the real state root.
Test modules combine these builders with ``tempfile.TemporaryDirectory``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from tiktok_ingest import config
from tiktok_ingest.contracts import (
    BacklogEntry,
    JobManifest,
    ProcessedEntry,
    StageOutcome,
    StageRecord,
    StageResult,
)
from tiktok_ingest.resume import build_fingerprint
from tiktok_ingest.state import Processed, StateRoot, atomic_write_bytes

FIXED_NOW = "2026-09-18T12:00:00Z"

MEDIA_SHA256 = hashlib.sha256(b"fixture-media-bytes").hexdigest()


def content_hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def sample_versions() -> dict[str, dict[str, str]]:
    """Tool/model/config versions per stage, as a fixture would record them."""
    return {
        "prepare": {"ffmpeg": "4.4.2-0ubuntu0.22.04.1", "extractor": "yt-dlp==2026.08.19"},
        "vision": {"vision_model": config.VISION_MODEL, "prompt_pack": "1"},
        "audio": {"whisper_model": config.WHISPER_MODEL, "language": config.WHISPER_LANGUAGE},
        "synthesis": {"text_model": "glm-5.3-flash", "prompt_pack": "1"},
        "verify": {"verifier": "glm-5.3-flash", "prompt_pack": "1"},
        "emit": {"manifest_schema": "1"},
    }


def sample_input_hashes() -> dict[str, str]:
    return {
        "prepare": MEDIA_SHA256,
        "vision": content_hash("prepare-output"),
        "audio": content_hash("vision-output"),
        "synthesis": content_hash("audio-output"),
        "verify": content_hash("synthesis-output"),
        "emit": content_hash("verify-output"),
    }


def sample_fingerprints() -> dict[str, dict[str, Any]]:
    """Complete, matching fingerprints for all six canonical stages."""
    versions = sample_versions()
    inputs = sample_input_hashes()
    return {
        stage: build_fingerprint(stage, inputs[stage], versions[stage])
        for stage in (
            "prepare",
            "vision",
            "audio",
            "synthesis",
            "verify",
            "emit",
        )
    }


def sample_manifest() -> JobManifest:
    """A fully completed job manifest with per-stage records."""
    fingerprints = sample_fingerprints()
    inputs = sample_input_hashes()
    stages = {
        stage: StageRecord(
            stage=stage,
            result=StageResult(
                outcome=StageOutcome.COMPLETE,
                reason=f"fixture: {stage} completed",
            ),
            fingerprint=fingerprints[stage],
            attempts=1,
            input_sha256=inputs[stage],
            output_sha256=content_hash(f"{stage}-output"),
            started_at=FIXED_NOW,
            finished_at=FIXED_NOW,
            duration_seconds=1.0,
        )
        for stage in fingerprints
    }
    return JobManifest(
        video_id="7683273567433248022",
        media_sha256=MEDIA_SHA256,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        tool_versions={"ffmpeg": "4.4.2-0ubuntu0.22.04.1"},
        model_versions={
            "vision": config.VISION_MODEL,
            "whisper": config.WHISPER_MODEL,
        },
        config_versions={"prompt_pack": "1", "transcript_policy": config.TRANSCRIPT_POLICY},
        budgets=dataclasses.asdict(config.BUDGETS),
        stages=stages,
        resource_gates={"free_vram_gate_mib": config.GATES.free_vram_gate_mib},
    )


def sample_backlog_entry() -> BacklogEntry:
    return BacklogEntry(
        id="7683273567433248022",
        url="https://www.tiktok.com/@mralekai/video/7683273567433248022",
        ingested_at=FIXED_NOW,
        author="mralekai",
        status="pending",
        classification=None,
        unknown_classification_reason="fixture: synthesis not run yet",
        entities=["fixture-tool"],
        claims=[],
        fit=None,
        actionable=None,
        artifacts="runs/2026-09-18T12-00Z/7683273567433248022/",
    )


def make_state_root(parent: str) -> StateRoot:
    """A state root confined to a test temp directory."""
    root = Path(tempfile.mkdtemp(dir=parent, prefix="state-"))
    return StateRoot(root)


# --------------------------------------------------------------------------
# Milestone 2 fixtures: media bytes, ffprobe documents, gate reports
# --------------------------------------------------------------------------

# A full immutable image ID as `podman inspect` would report it.
FAKE_IMAGE_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"

MP4_HEADER = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


def mp4_bytes(payload: bytes = b"0" * 256) -> bytes:
    """Minimal MP4-looking bytes: valid ftyp signature plus payload."""
    return MP4_HEADER + payload


def html_bytes() -> bytes:
    """An HTML access page served as 'video' — a validation failure."""
    return b"<!DOCTYPE html><html><head><title>Oops</title></head></html>"


def webm_bytes(payload: bytes = b"1" * 128) -> bytes:
    return b"\x1a\x45\xdf\xa3" + payload


def ffprobe_document(
    *,
    duration: str = "12.5",
    time_base: str = "1/12800",
    video: bool = True,
    audio: bool = True,
    attached_pic_video: bool = False,
) -> str:
    """An ffprobe -show_streams -show_format JSON document."""
    streams: list[dict[str, Any]] = []
    if video:
        stream = {
            "codec_type": "video",
            "codec_name": "h264",
            "time_base": time_base,
            "r_frame_rate": "30/1",
            "duration": duration,
            "disposition": {"attached_pic": 1 if attached_pic_video else 0},
        }
        streams.append(stream)
    if attached_pic_video:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": "mjpeg",
                "disposition": {"attached_pic": 1},
            }
        )
    if audio:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "disposition": {},
            }
        )
    return json.dumps({"streams": streams, "format": {"duration": duration}})


def showinfo_stderr(events: list[tuple[float, float]]) -> str:
    """ffmpeg showinfo stderr text for (pts, pts_time) events."""
    lines = ["ffmpeg version 4.4.2 ..."]
    for pts, pts_time in events:
        lines.append(
            f"[Parsed_showinfo_1 @ 0x0] n:0 pts:{pts} pts_time:{pts_time}"
        )
    return "\n".join(lines) + "\n"


def gate_report(
    *,
    version: str = "2026.08.19",
    app_api_found: tuple[str, ...] = ("_call_api",),
    challenge_found: tuple[str, ...] = ("_solve_challenge",),
    ambient_cookies_blocked: bool = True,
    cookie_export_blocked: bool = True,
) -> dict[str, Any]:
    """A gate probe report shape as the in-venv probe would emit."""
    return {
        "schema": 1,
        "version": version,
        "areas": {
            "app_api": {
                "candidates": ["_call_api"],
                "found": list(app_api_found),
                "missing": [c for c in ["_call_api"] if c not in app_api_found],
            },
            "challenge": {
                "candidates": ["_solve_challenge"],
                "found": list(challenge_found),
                "missing": [c for c in ["_solve_challenge"] if c not in challenge_found],
            },
        },
        "ambient_cookies_blocked": ambient_cookies_blocked,
        "cookie_export_blocked": cookie_export_blocked,
    }


JSON_MARKER = "<<JSON>>"


class FakeResponse:
    """Minimal urlopen()-returned body: context manager + read()."""

    def __init__(self, body: bytes) -> None:
        self._buffer = io.BytesIO(body)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


# --------------------------------------------------------------------------
# Milestone 3 fixtures: prepared jobs and WS transport fakes
# --------------------------------------------------------------------------

VIDEO_ID = "7684788219510033665"

PNG_BYTES = b"\x89PNG\r\n\x1a\nfixture-frame-bytes"


def make_prepared_job(
    tmp: Path,
    *,
    video_id: str = VIDEO_ID,
    frame_count: int = 2,
    prepare_complete: bool = True,
    include_audio: bool = True,
    run_id: str = "run-1",
) -> tuple[StateRoot, Path]:
    """A state root with one fetched + prepared job (frames + audio)."""
    state = StateRoot(Path(tmp) / "state")
    state.ensure_layout()
    run_dir = state.run_dir(run_id, video_id)
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_records = []
    for index in range(1, frame_count + 1):
        name = f"hybrid_{index:02d}.png"
        payload = PNG_BYTES + bytes([index])
        (frames_dir / name).write_bytes(payload)
        frame_records.append(
            {
                "index": index,
                "filename": name,
                "pts": 12800 * index,
                "timestamp_seconds": 1.0 * index,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "selection_reasons": ["uniform_coverage"],
                "is_crop_candidate": False,
                "crop_metadata_only": None,
            }
        )
    if include_audio:
        (run_dir / "audio.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00")
    sampling_manifest = {
        "video_id": video_id,
        "media_sha256": MEDIA_SHA256,
        "frames": frame_records,
        "audio": {"filename": "audio.wav"},
    }
    atomic_write_bytes(
        run_dir / "sampling-manifest.json",
        json.dumps(sampling_manifest, indent=2).encode("utf-8") + b"\n",
    )
    now = FIXED_NOW
    manifest = JobManifest(
        video_id=video_id,
        media_sha256=MEDIA_SHA256,
        created_at=now,
        updated_at=now,
    )
    if prepare_complete:
        manifest.stages["prepare"] = StageRecord(
            stage="prepare",
            result=StageResult(outcome=StageOutcome.COMPLETE, reason="fixture"),
            fingerprint={
                "stage": "prepare",
                "input_sha256": MEDIA_SHA256,
                "versions": {},
            },
            input_sha256=MEDIA_SHA256,
        )
    atomic_write_bytes(
        run_dir / "meta.json",
        json.dumps(manifest.to_dict(), indent=2).encode("utf-8") + b"\n",
    )
    processed = ProcessedEntry(
        video_id=video_id,
        media_sha256=MEDIA_SHA256,
        completed_at=now,
        stage_fingerprints={},
        artifacts=[f"runs/{run_id}/{video_id}"],
    )
    Processed(state).record(processed)
    return state, run_dir


def runner_json(payload: dict[str, Any]) -> str:
    """stdout line as the in-venv scripts emit JSON documents."""
    return JSON_MARKER + json.dumps(payload) + "\n"


def sample_oembed_payload(title: str = "Fixture video") -> dict[str, Any]:
    return {
        "title": title,
        "author_name": "fixture-author",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "embed_html": "<blockquote></blockquote>",
    }


# --------------------------------------------------------------------------
# Milestone 4 fixtures: sanitized evidence, synthesis documents, fake HTTP
# --------------------------------------------------------------------------

GITHUB_REPO_URL = "https://github.com/fixture/tool"


def write_sanitized_evidence(run_dir: Path, *, include_audio: bool = True) -> None:
    """Write sanitized video.md (+ optional audio.md) into a run directory."""
    (run_dir / "video.md").write_text(
        "# video.md — fixture\n\n- media_sha256: " + MEDIA_SHA256 + "\n"
        "## Frame 001\n\n> observed: FixtureTool repository UI\n",
        encoding="utf-8",
    )
    if include_audio:
        (run_dir / "audio.md").write_text(
            "# audio.md — fixture\n\n- method: fixture-asr\n"
            "> FixtureTool tiene 12000 estrellas\n",
            encoding="utf-8",
        )


def sample_synthesis_document() -> dict[str, Any]:
    """A valid operator-supplied synthesis document."""
    return {
        "classification": {
            "root": "tecnología",
            "subgroup": "Cli Agentes",
            "confidence": 0.9,
        },
        "entities": [
            {"name": "FixtureTool", "candidate_urls": [GITHUB_REPO_URL]},
            "Plain Entity",
        ],
        "claims": [
            {
                "claim": "FixtureTool has 12000 stars",
                "source": "audio",
                "verdict": None,
                "evidence": ["frame_001", "chunk_02"],
                "note": None,
                "candidate_urls": [GITHUB_REPO_URL],
            },
        ],
        "actionables": ["Evaluate FixtureTool for CLI work"],
        "fit": "adjacent",
        "model": "operator-curated-fixture",
        "generated_at": FIXED_NOW,
    }


def record_inventory(
    state: StateRoot,
    *,
    video_id: str = VIDEO_ID,
    canonical_url: str | None = None,
    author: str | None = "fixture-author",
) -> None:
    """Persist the direct-URL inventory entry the verify stage requires."""
    from tiktok_ingest.contracts import InventoryEntry
    from tiktok_ingest.state import InventoryStore

    InventoryStore(state).save(
        [
            InventoryEntry(
                source="tiktok",
                stable_id=video_id,
                canonical_url=canonical_url
                or f"https://www.tiktok.com/@fixture-author/video/{video_id}",
                input_origin="direct-url",
                discovered_at=FIXED_NOW,
                author=author,
            )
        ]
    )


class FakeVerifyResponse:
    """Fake urlopen() result: status, headers, capped read, geturl."""

    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        final_url: str | None = None,
    ) -> None:
        self._buffer = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}
        self._final_url = final_url

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._final_url or "https://fixture.example/final"


def github_star_page(stars: int | str) -> bytes:
    """A GitHub-looking page with an embedded star count."""
    return json.dumps({"stargazers_count": stars}).encode("utf-8")


def make_synthesized_job(
    tmp: Path,
    *,
    video_id: str = VIDEO_ID,
    document: dict[str, Any] | None = None,
    include_audio: bool = True,
) -> tuple[StateRoot, Path]:
    """A prepared job with sanitized evidence, a COMPLETED synthesis stage
    and an inventory record — the full precondition set for ``verify``."""
    from tiktok_ingest.synthesis import run_synthesis_stage

    state, run_dir = make_prepared_job(tmp, video_id=video_id, include_audio=include_audio)
    write_sanitized_evidence(run_dir, include_audio=include_audio)
    source = Path(tmp) / f"synthesis-{video_id}.json"
    source.write_text(
        json.dumps(document or sample_synthesis_document(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    outcome = run_synthesis_stage(video_id, state=state, from_file=source)
    if outcome.outcome != "complete":  # pragma: no cover - fixture assertion
        raise AssertionError(f"fixture synthesis did not complete: {outcome.reason}")
    record_inventory(state, video_id=video_id)
    return state, run_dir
