"""Direct-URL pipeline orchestration (Milestone 2).

Implements the "authorized input to CPU artifacts" boundary for ONE
canonical TikTok video URL (MVP-PRD section 7, milestone row 2):

    fetch-url <url>   direct URL -> blocklist/cache checks -> gated pinned
                      extraction -> shared-media validation -> content-
                      addressed cache -> fetch record + manifest
    prepare <id>      validated media -> frames + audio WAV with full
                      provenance (see ``prepare``)
    status [id]       persisted outcomes for inspection

Rules encoded here:

- Direct-URL input is explicitly recorded with ``input_origin=
  "direct-url"`` and NEVER claims collection membership. Collection
  inventory persistence rides the milestone-1 contracts; the live browser
  scan is a later authorized run.
- The permanent blocklist is checked before ANY remote work; no flag can
  override it (``--force`` invalidates derived outputs only).
- An unchanged repeated run performs ZERO re-extraction and ZERO
  re-preparation through the processed cache and resume fingerprints.
- Extraction is never retried automatically; metadata TTL is 24 h.
- No browser automation happens in this module: it only persists the
  inventory/contract side.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from . import config
from .contracts import (
    InventoryEntry,
    JobManifest,
    ProcessedEntry,
    StageOutcome,
    utc_now_iso,
)
from .extractor import (
    ExtractionResult,
    OEmbedCache,
    fetch_oembed,
    run_extraction,
)
from .prepare import (
    Runner,
    container_argv,
    resolve_image_id,
    subprocess_runner,
)
from .runtime import default_urlopen
from .state import (
    Backlog,
    Blocklist,
    CacheStore,
    InventoryStore,
    Processed,
    StateRoot,
    atomic_write_bytes,
)
from .validation import MediaValidation, sha256_of_file, validate_media_file

_TIKTOK_URL_RE = re.compile(
    r"^https://www\.tiktok\.com/@(?P<author>[A-Za-z0-9_.]+)/video/(?P<video_id>\d+)/?$"
)


class PipelineError(RuntimeError):
    """Raised for pipeline input errors (unparsed URLs, invalid IDs)."""


FetchClock = Callable[[], datetime.datetime]


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_tiktok_url(url: str) -> tuple[str, str, str | None]:
    """Parse ONE canonical TikTok video URL.

    Returns ``(video_id, canonical_url, author)``. Only the canonical
    ``/@author/video/<id>`` form is accepted in this milestone; short links
    would require redirect following and are an explicit ``unsupported``
    input rather than a silent fetch.
    """
    if not isinstance(url, str) or not url.strip():
        raise PipelineError("a TikTok video URL is required")
    match = _TIKTOK_URL_RE.match(url.strip())
    if match is None:
        raise PipelineError(
            f"unsupported TikTok URL {url.strip()!r}: this input mode accepts "
            "only canonical https://www.tiktok.com/@<author>/video/<id> URLs"
        )
    video_id = match.group("video_id")
    canonical = (
        "https://www.tiktok.com/"
        f"@{match.group('author')}/video/{video_id}"
    )
    return video_id, canonical, match.group("author")


def fetch_fingerprint(canonical_url: str) -> dict[str, Any]:
    """Resume fingerprint for the extraction (fetch) step.

    ``fetch`` is upstream of the canonical stage order, so it uses the same
    fingerprint SHAPE but is stored under its own key in the processed
    entry and in ``manifest.extraction``.
    """
    return {
        "stage": "fetch",
        "input_sha256": hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
        "versions": {
            "extractor_pin": config.EXTRACTOR_PIN,
            "gate_rules": config.EXTRACTOR_GATE_RULES_VERSION,
        },
    }


@dataclasses.dataclass(frozen=True)
class FetchOutcome:
    """Result of one fetch-url invocation."""

    outcome: str
    reason: str
    reused: bool
    video_id: str
    canonical_url: str
    media_sha256: str | None = None
    oembed_outcome: str | None = None
    run_dir: str | None = None


class ContainerProbe:
    """ffprobe runner executing inside the pinned container (validation)."""

    def __init__(
        self,
        image_id: str,
        *,
        runner: Runner,
        media_path: Path,
        timeout_seconds: float,
    ) -> None:
        self.image_id = image_id
        self._runner = runner
        self.media_path = Path(media_path)
        self.timeout_seconds = timeout_seconds

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        mapped = [
            f"/media/{self.media_path.name}" if item == str(self.media_path) else item
            for item in argv
        ]
        return self._runner(
            container_argv(
                self.image_id,
                mapped,
                read_only_mounts=((self.media_path.parent, "/media"),),
            ),
            timeout=self.timeout_seconds,
        )


def _fetch_media_failure(
    video_id: str,
    canonical_url: str,
    run_dir: Path,
    result: ExtractionResult,
    *,
    oembed_outcome: str | None,
    started_at: str,
    deadline_seconds: float,
    max_bytes: int,
    validation: MediaValidation | None = None,
) -> dict[str, Any]:
    """Persist the fetch record for a non-reusable attempt and return it."""
    record: dict[str, Any] = {
        "schema": 1,
        "origin": "direct-url",
        "video_id": video_id,
        "canonical_url": canonical_url,
        "extractor": config.EXTRACTOR_REQUIREMENT,
        "gate_rules_version": config.EXTRACTOR_GATE_RULES_VERSION,
        "outcome": result.outcome,
        "reason": result.reason,
        "detail": result.detail,
        "media_sha256": None,
        "oembed_outcome": oembed_outcome,
        "validation": (
            {
                "outcome": validation.outcome,
                "reason": validation.reason,
                "size_bytes": validation.size_bytes,
                "duration_seconds": validation.duration_seconds,
            }
            if validation is not None
            else None
        ),
        "deadline_seconds": deadline_seconds,
        "max_bytes": max_bytes,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "retried": False,
    }
    atomic_write_bytes(
        run_dir / "fetch.json",
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n",
    )
    return record


def fetch_url(
    url: str,
    *,
    state: StateRoot,
    share_root: Path | str | None = None,
    venv_dir: Path | str | None = None,
    runner: Runner = subprocess_runner,
    urlopen: Callable[..., Any] = default_urlopen,
    now_fn: FetchClock = _utc_now,
    force: bool = False,
) -> FetchOutcome:
    """Process ONE direct URL end to end up to validated shared media.

    Zero re-extraction on an unchanged repeated run: a matching fetch
    fingerprint plus an intact, hash-verified media file short-circuits the
    whole remote path (the extractor is never invoked).
    """
    share_root = Path(share_root) if share_root is not None else config.SHARE_ROOT
    venv_dir = Path(venv_dir) if venv_dir is not None else config.EXTRACTOR_VENV_DIR

    video_id, canonical_url, author = parse_tiktok_url(url)
    state.ensure_layout()

    blocklist = Blocklist(state)
    if blocklist.is_blocked(video_id):
        return FetchOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="video ID is permanently blocklisted; no flag can override "
            "an operator rejection",
            reused=False,
            video_id=video_id,
            canonical_url=canonical_url,
        )

    now = now_fn()
    oembed_cache = OEmbedCache(state)
    oembed_record = oembed_cache.get_fresh(canonical_url, now)
    oembed_outcome: str | None
    if oembed_record is not None:
        oembed_outcome = oembed_record.outcome
    else:
        oembed_record = fetch_oembed(
            canonical_url,
            urlopen=urlopen,
            fetched_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
        oembed_cache.put(oembed_record)
        oembed_outcome = oembed_record.outcome

    processed = Processed(state)
    cache = CacheStore(state)
    current_fingerprint = fetch_fingerprint(canonical_url)

    if not force:
        entry = processed.get(video_id)
        recorded_fp = entry.stage_fingerprints.get("fetch") if entry else None
        if entry is not None and recorded_fp and recorded_fp == current_fingerprint:
            media_path = cache.path_for(entry.media_sha256, ".mp4")
            if media_path.is_file() and sha256_of_file(media_path) == entry.media_sha256:
                return FetchOutcome(
                    outcome=StageOutcome.COMPLETE.value,
                    reason="fetch fingerprint and cached media match the "
                    "recorded run; zero re-extraction performed",
                    reused=True,
                    video_id=video_id,
                    canonical_url=canonical_url,
                    media_sha256=entry.media_sha256,
                    oembed_outcome=oembed_outcome,
                    run_dir=str(state.root / entry.artifacts[0]) if entry.artifacts else None,
                )

    run_id = utc_now_iso().replace(":", "-")
    run_dir = state.run_dir(run_id, video_id)
    started_at = utc_now_iso()

    result = run_extraction(
        canonical_url,
        run_dir / "download",
        venv_dir=venv_dir,
        share_root=share_root,
        runner=runner,
        deadline_seconds=float(config.BUDGETS.fetch_deadline_seconds),
        max_bytes=config.BUDGETS.media_max_bytes,
    )

    def record_failure(
        outcome: str, reason: str, validation: MediaValidation | None = None
    ) -> FetchOutcome:
        _fetch_media_failure(
            video_id, canonical_url, run_dir, result,
            oembed_outcome=oembed_outcome, started_at=started_at,
            deadline_seconds=float(config.BUDGETS.fetch_deadline_seconds),
            max_bytes=config.BUDGETS.media_max_bytes,
            validation=validation,
        )
        _record_inventory(state, video_id, canonical_url, author, now)
        return FetchOutcome(
            outcome=outcome,
            reason=reason,
            reused=False,
            video_id=video_id,
            canonical_url=canonical_url,
            oembed_outcome=oembed_outcome,
            run_dir=str(run_dir),
        )

    if result.outcome != "complete" or result.media_path is None:
        return record_failure(result.outcome, result.reason)

    media_file = Path(result.media_path)

    image_id = resolve_image_id(config.FFMPEG_IMAGE_REF, runner=runner)
    probe = ContainerProbe(
        image_id,
        runner=runner,
        media_path=media_file,
        timeout_seconds=float(config.BUDGETS.fetch_deadline_seconds),
    )
    validation = validate_media_file(media_file, ffprobe_runner=probe)
    if validation.outcome != StageOutcome.COMPLETE.value:
        media_file.unlink(missing_ok=True)
        return record_failure(
            validation.outcome,
            f"media validation failed: {validation.reason}",
            validation=validation,
        )

    media_sha256 = validation.media_sha256
    assert media_sha256 is not None  # validation binds it on success
    cache_path = cache.put_file(media_file, media_sha256, ".mp4")

    finished_at = utc_now_iso()
    extraction_record: dict[str, Any] = {
        "schema": 1,
        "origin": "direct-url",
        "video_id": video_id,
        "canonical_url": canonical_url,
        "extractor": config.EXTRACTOR_REQUIREMENT,
        "gate_rules_version": config.EXTRACTOR_GATE_RULES_VERSION,
        "outcome": StageOutcome.COMPLETE.value,
        "reason": result.reason,
        "detail": {
            **result.detail,
            "media_sha256": media_sha256,
            "media_cache_path": str(cache_path),
        },
        "media_sha256": media_sha256,
        "oembed": {
            "outcome": oembed_record.outcome,
            "fetched_at": oembed_record.fetched_at,
        },
        "validation": {
            "outcome": validation.outcome,
            "reason": validation.reason,
            "size_bytes": validation.size_bytes,
            "duration_seconds": validation.duration_seconds,
            "sha256": media_sha256,
        },
        "deadline_seconds": float(config.BUDGETS.fetch_deadline_seconds),
        "max_bytes": config.BUDGETS.media_max_bytes,
        "started_at": started_at,
        "finished_at": finished_at,
        "retried": False,
    }
    atomic_write_bytes(
        run_dir / "fetch.json",
        json.dumps(
            extraction_record, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n",
    )

    manifest = JobManifest(
        video_id=video_id,
        media_sha256=media_sha256,
        created_at=started_at,
        updated_at=finished_at,
        tool_versions={
            "extractor": config.EXTRACTOR_REQUIREMENT,
            "ffmpeg_container_image": config.FFMPEG_IMAGE_REF,
            "ffmpeg_container_image_id": image_id,
        },
        budgets=dataclasses.asdict(config.BUDGETS),
        extraction=extraction_record,
    )
    atomic_write_bytes(
        run_dir / "meta.json",
        json.dumps(
            manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n",
    )

    entry = ProcessedEntry(
        video_id=video_id,
        media_sha256=media_sha256,
        completed_at=finished_at,
        stage_fingerprints={"fetch": current_fingerprint},
        artifacts=[f"runs/{run_id}/{video_id}"],
    )
    processed.record(entry)
    _record_inventory(state, video_id, canonical_url, author, now)

    return FetchOutcome(
        outcome=StageOutcome.COMPLETE.value,
        reason="gated extraction completed; media validated and bound to "
        f"sha256 {media_sha256}",
        reused=False,
        video_id=video_id,
        canonical_url=canonical_url,
        media_sha256=media_sha256,
        oembed_outcome=oembed_outcome,
        run_dir=str(run_dir),
    )


def _record_inventory(
    state: StateRoot,
    video_id: str,
    canonical_url: str,
    author: str | None,
    now: datetime.datetime,
) -> None:
    """Persist the direct-URL observation (never a collection claim).

    First observation wins (PRD section 4): when the video was already
    inventoried (for example from a collection enumeration), the existing
    entry keeps its ``input_origin``, ``discovered_at``, counts,
    ``completeness`` and collection associations. Only a missing ``author``
    is filled in. A fetch is processing provenance, not a new enumeration
    fact, so it never overwrites enumeration records.
    """
    store = InventoryStore(state)
    entries = store.load()
    for index, entry in enumerate(entries):
        if entry.key(entry.source, entry.stable_id) != ("tiktok", video_id):
            continue
        if entry.author is not None:
            return
        entries[index] = InventoryEntry(
            **{**entry.to_dict(), "author": author}
        )
        store.save(entries)
        return
    entries.append(
        InventoryEntry(
            source="tiktok",
            stable_id=video_id,
            canonical_url=canonical_url,
            input_origin="direct-url",
            discovered_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            collections=(),
            declared_count=None,
            observed_count=1,
            completeness="complete",
            author=author,
        )
    )
    store.save(entries)


def video_status(video_id: str | None, *, state: StateRoot) -> dict[str, Any]:
    """Read-only status of one video or of the whole local state.

    Includes the synthesis/verify stage outcomes (from ``meta.json``) and
    the backlog emission state for each item.
    """
    processed = Processed(state)
    blocklist = Blocklist(state)
    inventory = InventoryStore(state)
    backlog_entries = Backlog(state).entries()
    backlog_status_by_id = {
        str(item.get("id")): item.get("status") for item in backlog_entries
    }

    if video_id is not None:
        ids = [video_id]
    else:
        ids = sorted(processed.all().keys())

    items: list[dict[str, Any]] = []
    for item_id in ids:
        entry = processed.get(item_id)
        record: dict[str, Any] = {
            "video_id": item_id,
            "blocklisted": blocklist.is_blocked(item_id),
            "processed": entry is not None,
            "backlog": {
                "emitted": item_id in backlog_status_by_id,
                "status": backlog_status_by_id.get(item_id),
            },
        }
        if entry is not None:
            record["media_sha256"] = entry.media_sha256
            record["completed_at"] = entry.completed_at
            record["stages"] = sorted(entry.stage_fingerprints.keys())
            record["artifacts"] = list(entry.artifacts)
            manifest_path = state.root / entry.artifacts[0] / "meta.json" if entry.artifacts else None
            if manifest_path and manifest_path.is_file():
                try:
                    manifest = JobManifest.from_dict(
                        json.loads(manifest_path.read_text(encoding="utf-8"))
                    )
                    record["extraction_outcome"] = (
                        (manifest.extraction or {}).get("outcome")
                    )
                    record["stage_outcomes"] = {
                        stage: stage_record.result.outcome.value
                        for stage, stage_record in manifest.stages.items()
                    }
                except (json.JSONDecodeError, ValueError):
                    record["manifest_error"] = "meta.json is not a valid manifest"
        items.append(record)

    return {
        "state_root": str(state.root),
        "items": items,
        "inventory_size": len(inventory.load()),
    }
