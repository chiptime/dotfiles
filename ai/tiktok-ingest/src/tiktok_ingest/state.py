"""Local state management for the TikTok ingest MVP (PRD section 8).

State root layout (configurable; default ``~/.local/state/tiktok-ingest/``)::

    backlog.jsonl          operator-edited queue, one JSON object per line
    blocklist.json         permanent reject set (append-only)
    taxonomy.json          accumulated normalized subgroups
    processed.json         stable ID -> media hash, fingerprints, completion
    inventory.json         collection observations and completeness
    cache/                 bounded working cache keyed by content + stage
    runs/<run-id>/<video-id>/   per-job artifacts (video.md, audio.md, meta.json)

Durability rules:

- Every durable write is atomic: data goes to a temporary file in the
  destination directory, is flushed and fsynced, then moved into place with
  ``os.replace``. A crash mid-write leaves the previous file intact.
- The blocklist is append-only and permanent. ``--force`` and every other
  flag can NEVER override a rejection (PRD section 8).
- Tests always construct a ``StateRoot`` inside a temporary directory and
  never read or write the real default state root.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    TAXONOMY_ROOTS,
    BacklogEntry,
    BlocklistEntry,
    ContractError,
    InventoryEntry,
    ProcessedEntry,
    TaxonomyEntry,
    to_jsonable,
    utc_now_iso,
)

DEFAULT_STATE_ROOT = Path.home() / ".local" / "state" / "tiktok-ingest"

_STATE_FORMAT_VERSION = 1

_KEY_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


class StateError(RuntimeError):
    """Raised when a state operation cannot be completed."""


class BlockedVideoError(StateError):
    """Raised when an operation targets a permanently rejected video ID."""


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync so the rename itself is durable."""
    try:
        dir_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically.

    The payload is written to a temporary file in the destination directory,
    flushed, fsynced, and moved into place with ``os.replace``. On any
    failure the temporary file is removed and the previous content of
    ``path`` is left intact.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def atomic_write_json(path: Path | str, payload: Any) -> None:
    """Atomically serialize ``payload`` (contract objects allowed) as JSON."""
    text = json.dumps(
        to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True
    )
    atomic_write_bytes(path, (text + "\n").encode("utf-8"))


def read_json_file(path: Path) -> Any:
    """Read a JSON document, returning ``None`` when the file is absent."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"{path} is not valid JSON: {exc}") from exc


# --------------------------------------------------------------------------
# State root
# --------------------------------------------------------------------------


class StateRoot:
    """Filesystem layout for one state root (configurable, never implicit)."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = (
            Path(root).expanduser() if root is not None else DEFAULT_STATE_ROOT
        )

    # -- paths ------------------------------------------------------------

    @property
    def backlog_path(self) -> Path:
        return self.root / "backlog.jsonl"

    @property
    def blocklist_path(self) -> Path:
        return self.root / "blocklist.json"

    @property
    def taxonomy_path(self) -> Path:
        return self.root / "taxonomy.json"

    @property
    def processed_path(self) -> Path:
        return self.root / "processed.json"

    @property
    def inventory_path(self) -> Path:
        return self.root / "inventory.json"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def collections_dir(self) -> Path:
        return self.root / "collections"

    # -- layout -----------------------------------------------------------

    def ensure_layout(self) -> None:
        """Create the directory skeleton (idempotent)."""
        for directory in (self.root, self.cache_dir, self.runs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str, video_id: str) -> Path:
        """Return (and create) ``runs/<run-id>/<video-id>/``."""
        for name, value in (("run_id", run_id), ("video_id", video_id)):
            if not value or not _KEY_RE.match(value):
                raise StateError(
                    f"{name} must match {_KEY_RE.pattern}, got {value!r}"
                )
        path = self.runs_dir / run_id / video_id
        path.mkdir(parents=True, exist_ok=True)
        return path


# --------------------------------------------------------------------------
# Backlog (backlog.jsonl)
# --------------------------------------------------------------------------


class Backlog:
    """Append-only JSONL queue with a duplicate-ID guard.

    Appends are atomic: the full file is rewritten through
    ``atomic_write_bytes`` after checking for duplicate stable IDs, so an
    interrupted append can never leave a torn line or a duplicate entry.
    """

    def __init__(self, state: StateRoot) -> None:
        self.state = state

    def entries(self) -> list[dict[str, Any]]:
        path = self.state.backlog_path
        if not path.exists():
            return []
        result: list[dict[str, Any]] = []
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StateError(f"backlog line {lineno} is not valid JSON: {exc}")
            if not isinstance(parsed, dict) or "id" not in parsed:
                raise StateError(f"backlog line {lineno} has no 'id' field")
            result.append(parsed)
        return result

    def ids(self) -> set[str]:
        return {str(entry.get("id")) for entry in self.entries()}

    def has(self, video_id: str) -> bool:
        return video_id in self.ids()

    def append(self, entry: BacklogEntry) -> str:
        """Append one entry; duplicate IDs are refused.

        Returns ``"appended"`` or ``"skipped_duplicate"``. The duplicate
        guard is keyed by the stable video ID, which is what makes resume
        emission idempotent.
        """
        existing = self.entries()
        if any(item.get("id") == entry.id for item in existing):
            return "skipped_duplicate"
        existing.append(to_jsonable(entry))
        payload = "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in existing
        )
        atomic_write_bytes(self.state.backlog_path, payload.encode("utf-8"))
        return "appended"


# --------------------------------------------------------------------------
# Blocklist (blocklist.json) — permanent, never overridden by --force
# --------------------------------------------------------------------------


class Blocklist:
    """Append-only permanent reject set.

    Rejection is permanent by contract (PRD section 8): once a video ID is
    rejected by the operator it is skipped by every future run. ``--force``
    invalidates selected derived outputs but can NEVER remove a blocklist
    entry or authorize re-ingestion. ``unblock`` exists to make that rule
    explicit and testable; it always raises, with or without ``force``.
    """

    def __init__(self, state: StateRoot) -> None:
        self.state = state

    def _load(self) -> list[dict[str, Any]]:
        document = read_json_file(self.state.blocklist_path)
        if document is None:
            return []
        if not isinstance(document, dict) or not isinstance(
            document.get("entries"), list
        ):
            raise StateError("blocklist.json has an unexpected structure")
        return document["entries"]

    def _save(self, entries: list[dict[str, Any]]) -> None:
        atomic_write_json(
            self.state.blocklist_path, {"version": _STATE_FORMAT_VERSION, "entries": entries}
        )

    def entries(self) -> dict[str, BlocklistEntry]:
        result: dict[str, BlocklistEntry] = {}
        for item in self._load():
            entry = BlocklistEntry.from_dict(item)
            result.setdefault(entry.video_id, entry)  # first rejection wins
        return result

    def is_blocked(self, video_id: str) -> bool:
        return video_id in self.entries()

    def ensure_not_blocked(self, video_id: str) -> None:
        if self.is_blocked(video_id):
            raise BlockedVideoError(
                f"video {video_id!r} is permanently blocklisted; no flag can "
                "override this rejection"
            )

    def reject(self, entry: BlocklistEntry) -> str:
        """Append a rejection. Idempotent per ID: the first entry wins.

        Returns ``"appended"`` or ``"skipped_duplicate"``.
        """
        entries = self._load()
        if any(
            isinstance(item, dict) and item.get("video_id") == entry.video_id
            for item in entries
        ):
            return "skipped_duplicate"
        entries.append(entry.to_dict())
        self._save(entries)
        return "appended"

    def unblock(self, video_id: str, force: bool = False) -> None:
        """Refuse removal unconditionally: the blocklist is permanent."""
        raise StateError(
            f"refusing to unblock {video_id!r}: the blocklist is permanent and "
            "--force can never override an operator rejection"
        )


# --------------------------------------------------------------------------
# Taxonomy (taxonomy.json)
# --------------------------------------------------------------------------

_HYPHEN_RUN_RE = re.compile(r"-{2,}")
_SEPARATOR_RE = re.compile(r"[\s_]+")


def normalize_subgroup(name: str) -> str:
    """Basic subgroup normalization: lowercase, hyphens normalized.

    Whitespace and underscores collapse to single hyphens, hyphen runs
    collapse, and leading/trailing hyphens are stripped. Normalization is
    idempotent: ``normalize_subgroup(normalize_subgroup(x)) ==
    normalize_subgroup(x)``.
    """
    if not isinstance(name, str) or not name.strip():
        raise ContractError("subgroup name must be a non-empty string")
    text = unicodedata.normalize("NFC", name).strip().lower()
    text = _SEPARATOR_RE.sub("-", text)
    text = _HYPHEN_RUN_RE.sub("-", text).strip("-")
    if not text:
        raise ContractError(f"subgroup name {name!r} normalizes to empty")
    return text


class Taxonomy:
    """Accumulating normalized subgroup registry (PRD section 6.7).

    Subgroups are generated per video from its content, then normalized
    against this registry. Re-adding an existing normalized subgroup is
    idempotent and returns the original entry unchanged.
    """

    def __init__(self, state: StateRoot) -> None:
        self.state = state

    def _load(self) -> list[dict[str, Any]]:
        document = read_json_file(self.state.taxonomy_path)
        if document is None:
            return []
        if not isinstance(document, dict) or not isinstance(
            document.get("subgroups"), list
        ):
            raise StateError("taxonomy.json has an unexpected structure")
        return document["subgroups"]

    def _save(self, subgroups: list[dict[str, Any]]) -> None:
        atomic_write_json(
            self.state.taxonomy_path,
            {"version": _STATE_FORMAT_VERSION, "subgroups": subgroups},
        )

    def add(
        self,
        root: str,
        subgroup: str,
        origin_video_id: str | None = None,
        added_at: str | None = None,
    ) -> tuple[TaxonomyEntry, str]:
        """Add a subgroup under a fixed root; idempotent on re-add.

        Returns ``(entry, "added" | "existing")``. Existing entries keep
        their original ``added_at`` and origin.
        """
        if root not in TAXONOMY_ROOTS:
            raise ContractError(
                f"taxonomy root {root!r} is not one of the fixed roots "
                f"{list(TAXONOMY_ROOTS)}"
            )
        normalized = normalize_subgroup(subgroup)
        records = self._load()
        for record in records:
            if record.get("subgroup") == normalized:
                return TaxonomyEntry.from_dict(record), "existing"
        entry = TaxonomyEntry(
            root=root,
            subgroup=normalized,
            added_at=added_at or utc_now_iso(),
            origin_video_id=origin_video_id,
        )
        records.append(entry.to_dict())
        self._save(records)
        return entry, "added"

    def lookup(self, subgroup: str) -> TaxonomyEntry | None:
        """Find a subgroup by any spelling that normalizes to its key."""
        normalized = normalize_subgroup(subgroup)
        for record in self._load():
            if record.get("subgroup") == normalized:
                return TaxonomyEntry.from_dict(record)
        return None

    def entries(self) -> list[TaxonomyEntry]:
        return [TaxonomyEntry.from_dict(record) for record in self._load()]


# --------------------------------------------------------------------------
# Processed (processed.json) and inventory (inventory.json)
# --------------------------------------------------------------------------


class Processed:
    """Stable ID -> media hash, stage fingerprints and completion time."""

    def __init__(self, state: StateRoot) -> None:
        self.state = state

    def _load(self) -> dict[str, dict[str, Any]]:
        document = read_json_file(self.state.processed_path)
        if document is None:
            return {}
        if not isinstance(document, dict) or not isinstance(
            document.get("entries"), dict
        ):
            raise StateError("processed.json has an unexpected structure")
        return document["entries"]

    def _save(self, entries: dict[str, dict[str, Any]]) -> None:
        atomic_write_json(
            self.state.processed_path,
            {"version": _STATE_FORMAT_VERSION, "entries": entries},
        )

    def all(self) -> dict[str, ProcessedEntry]:
        return {
            video_id: ProcessedEntry.from_dict(record)
            for video_id, record in self._load().items()
        }

    def get(self, video_id: str) -> ProcessedEntry | None:
        record = self._load().get(video_id)
        return ProcessedEntry.from_dict(record) if record else None

    def record(self, entry: ProcessedEntry) -> None:
        """Upsert one processed entry (atomic)."""
        entries = self._load()
        entries[entry.video_id] = entry.to_dict()
        self._save(entries)


class InventoryStore:
    """Persisted collection observations and completeness."""

    def __init__(self, state: StateRoot) -> None:
        self.state = state

    def load(self) -> list[InventoryEntry]:
        document = read_json_file(self.state.inventory_path)
        if document is None:
            return []
        if not isinstance(document, dict) or not isinstance(
            document.get("entries"), list
        ):
            raise StateError("inventory.json has an unexpected structure")
        return [InventoryEntry.from_dict(item) for item in document["entries"]]

    def save(self, entries: Iterable[InventoryEntry]) -> None:
        atomic_write_json(
            self.state.inventory_path,
            {
                "version": _STATE_FORMAT_VERSION,
                "entries": [entry.to_dict() for entry in entries],
            },
        )


# --------------------------------------------------------------------------
# Working cache keyed by content hash + stage fingerprint
# --------------------------------------------------------------------------


def stage_cache_key(media_sha256: str, fingerprint: Mapping[str, Any]) -> str:
    """Deterministic cache key from content hash plus stage fingerprint.

    Both inputs are folded through canonical JSON (sorted keys, compact
    separators) before hashing, so key ordering never changes the key while
    any version, input hash or stage name change always does.
    """
    payload = json.dumps(
        {"media": media_sha256, "fingerprint": to_jsonable(fingerprint)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CacheStore:
    """Byte-level working cache under ``<state-root>/cache/``.

    Keys come from :func:`stage_cache_key`. Every put is atomic. Eviction of
    completed-job working data is a later-milestone concern; this store only
    reads and writes keyed blobs.
    """

    def __init__(self, state: StateRoot) -> None:
        self.state = state

    def path_for(self, key: str, suffix: str = "") -> Path:
        if not re.match(r"^[0-9a-f]{64}$", key):
            raise StateError(f"cache key must be sha256 hex, got {key!r}")
        if suffix and not suffix.startswith("."):
            suffix = "." + suffix
        return self.state.cache_dir / f"{key}{suffix}"

    def has(self, key: str, suffix: str = "") -> bool:
        return self.path_for(key, suffix).exists()

    def put_bytes(self, key: str, data: bytes, suffix: str = "") -> Path:
        path = self.path_for(key, suffix)
        atomic_write_bytes(path, data)
        return path

    def put_file(self, src_path: Path | str, key: str, suffix: str = "") -> Path:
        """Move an existing file into the cache under ``key`` atomically.

        The source is renamed into place when it already sits on the same
        filesystem (the normal case: both live under the state root). On a
        cross-filesystem rename the content is copied through a temporary
        file and atomically moved, so the destination is never partial.
        """
        path = self.path_for(key, suffix)
        src_path = Path(src_path)
        if not src_path.is_file():
            raise StateError(f"cache source file does not exist: {src_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src_path, path)
        except OSError:  # cross-device: copy through a temporary file
            fd, tmp_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
            tmp_path = Path(tmp_name)
            try:
                with (
                    os.fdopen(fd, "wb") as dst,
                    src_path.open("rb") as src,
                ):
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
                os.replace(tmp_path, path)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
        _fsync_directory(path.parent)
        return path

    def get_bytes(self, key: str, suffix: str = "") -> bytes:
        path = self.path_for(key, suffix)
        if not path.exists():
            raise StateError(f"cache miss for key {key!r} (suffix {suffix!r})")
        return path.read_bytes()
