"""Data contracts for the TikTok ingest MVP (MVP-PRD section 4, PRD section 8).

Design rules encoded here:

- Every stage outcome carries a non-empty reason string.
- Unknown classification is ``null`` plus an explanation; it is never a
  sixth taxonomy root. The five roots are fixed.
- ``meta.json`` binds every stage to the media hash plus tool, model and
  configuration versions, per-stage status/reason, budgets, attempts and
  timings.
- All objects validate on construction and provide ``to_dict``/``from_dict``
  JSON (de)serialization helpers.

Stage pipeline order (canonical, used by resume invalidation):

    prepare -> vision -> audio -> synthesis -> verify -> emit
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import json
import re
from typing import Any, Mapping

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ContractError(ValueError):
    """Raised when a contract object fails validation."""


# --------------------------------------------------------------------------
# Enumerations and fixed vocabularies
# --------------------------------------------------------------------------

# Canonical stage pipeline order. Resume invalidation derives from this.
STAGE_ORDER: tuple[str, ...] = (
    "prepare",
    "vision",
    "audio",
    "synthesis",
    "verify",
    "emit",
)

# Fixed taxonomy roots (PRD section 6.7). Never extended at runtime; unknown
# classification is null with an explanation instead of a new root.
TAXONOMY_ROOTS: tuple[str, ...] = (
    "tecnología",
    "recurso",
    "educativo",
    "consumo",
    "ruido",
)

BACKLOG_STATUSES: tuple[str, ...] = ("pending", "accepted", "rejected")

# Claim evidence sources (PRD section 6.5 plus the MVP-PRD section 3.C
# metadata evidence: labelled caption/oEmbed metadata is a valid claim
# source alongside visual and audio evidence).
CLAIM_SOURCES: tuple[str, ...] = ("caption", "audio", "visual", "metadata")

CLAIM_VERDICTS: tuple[str, ...] = (
    "confirmed",
    "overstated",
    "unverifiable",
    "contradicted",
    "unnamed",
)

INVENTORY_COMPLETENESS: tuple[str, ...] = ("complete", "incomplete", "unknown")

PROVENANCE_ARTIFACTS: tuple[str, ...] = ("video.md", "audio.md")

MANIFEST_SCHEMA_VERSION = "2"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StageOutcome(enum.Enum):
    """Extraction-stage outcomes (PRD section 8). Every outcome has a reason."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    NOT_APPLICABLE = "not_applicable"

    @classmethod
    def parse(cls, value: Any) -> "StageOutcome":
        if isinstance(value, StageOutcome):
            return value
        try:
            return cls(str(value))
        except ValueError:
            raise ContractError(f"unknown stage outcome: {value!r}") from None


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_present(cls: type, record: Mapping[str, Any]) -> None:
    """Raise ContractError when required (no-default) fields are missing."""
    missing = [
        field.name
        for field in dataclasses.fields(cls)  # type: ignore[arg-type]
        if field.name not in record
        and field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    ]
    if missing:
        raise ContractError(
            f"{cls.__name__} is missing required fields: {missing}"
        )


def _require_dict(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field_name} must be an object")
    return value


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    return value


def _require_sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise ContractError(
            f"{field_name} must be a lowercase 64-character sha256 hex digest"
        )
    return value


def _require_one_of(value: Any, allowed: tuple[str, ...], field_name: str) -> str:
    _require_str(value, field_name)
    if value not in allowed:
        raise ContractError(
            f"{field_name} must be one of {list(allowed)}, got {value!r}"
        )
    return value


def _require_iso(value: Any, field_name: str) -> str:
    _require_str(value, field_name)
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError(f"{field_name} must be an ISO-8601 timestamp") from None
    return value


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field_name)


def _optional_sha256(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name)


def _optional_iso(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_iso(value, field_name)


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field_name} must be an integer or null")
    return value


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, enums and containers to JSON types."""
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_jsonable(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return value


def dumps_json(value: Any, *, indent: int | None = 2) -> str:
    """Deterministic JSON serialization for contract objects."""
    return json.dumps(
        to_jsonable(value), ensure_ascii=False, indent=indent, sort_keys=True
    )


# --------------------------------------------------------------------------
# Stage results and job manifest (meta.json)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StageResult:
    """A stage outcome. Every outcome carries a non-empty reason string."""

    outcome: StageOutcome
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", StageOutcome.parse(self.outcome))
        _require_str(self.reason, "StageResult.reason")

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome.value, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Any) -> "StageResult":
        record = _require_dict(data, "StageResult")
        return cls(
            outcome=StageOutcome.parse(record.get("outcome")),
            reason=record.get("reason"),
        )


@dataclasses.dataclass
class StageRecord:
    """Per-stage manifest record: status, reason, fingerprint, attempts, timings."""

    stage: str
    result: StageResult
    fingerprint: dict[str, Any] = dataclasses.field(default_factory=dict)
    attempts: int = 1
    input_sha256: str | None = None
    output_sha256: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        _require_one_of(self.stage, STAGE_ORDER, "StageRecord.stage")
        if not isinstance(self.result, StageResult):
            object.__setattr__(  # coerce plain dicts for convenience
                self, "result", StageResult.from_dict(self.result)
            )
        if not isinstance(self.fingerprint, dict):
            self.fingerprint = dict(_require_dict(self.fingerprint, "fingerprint"))
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise ContractError("StageRecord.attempts must be an integer")
        if self.attempts < 1:
            raise ContractError("StageRecord.attempts must be at least 1")
        self.input_sha256 = _optional_sha256(
            self.input_sha256, "StageRecord.input_sha256"
        )
        self.output_sha256 = _optional_sha256(
            self.output_sha256, "StageRecord.output_sha256"
        )
        self.started_at = _optional_iso(self.started_at, "StageRecord.started_at")
        self.finished_at = _optional_iso(self.finished_at, "StageRecord.finished_at")
        if self.duration_seconds is not None:
            if isinstance(self.duration_seconds, bool) or not isinstance(
                self.duration_seconds, (int, float)
            ):
                raise ContractError("StageRecord.duration_seconds must be a number")
            self.duration_seconds = float(self.duration_seconds)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "StageRecord":
        record = dict(_require_dict(data, "StageRecord"))
        record["result"] = StageResult.from_dict(record.get("result"))
        return cls(**record)


@dataclasses.dataclass
class JobManifest:
    """Job manifest persisted as ``runs/<run-id>/<video-id>/meta.json``.

    Binds the media hash and the tool/model/config versions to per-stage
    status, reason, fingerprints, budgets, attempts and timings.
    """

    video_id: str
    media_sha256: str
    created_at: str
    updated_at: str
    tool_versions: dict[str, str] = dataclasses.field(default_factory=dict)
    model_versions: dict[str, str] = dataclasses.field(default_factory=dict)
    config_versions: dict[str, str] = dataclasses.field(default_factory=dict)
    budgets: dict[str, Any] = dataclasses.field(default_factory=dict)
    stages: dict[str, StageRecord] = dataclasses.field(default_factory=dict)
    resource_gates: dict[str, Any] = dataclasses.field(default_factory=dict)
    # Fetch/extraction record (Milestone 2): outcome, reason, gate evidence,
    # extractor and oEmbed versions/timings. Extraction happens upstream of
    # the canonical stage order, so it is recorded here rather than as a
    # pseudo-stage. ``None`` on manifests created before a fetch existed.
    extraction: dict[str, Any] | None = None
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_str(self.video_id, "JobManifest.video_id")
        _require_sha256(self.media_sha256, "JobManifest.media_sha256")
        _require_iso(self.created_at, "JobManifest.created_at")
        _require_iso(self.updated_at, "JobManifest.updated_at")
        for name in ("tool_versions", "model_versions", "config_versions"):
            value = getattr(self, name)
            if not isinstance(value, dict):
                raise ContractError(f"JobManifest.{name} must be an object")
        if not isinstance(self.budgets, dict):
            raise ContractError("JobManifest.budgets must be an object")
        if not isinstance(self.resource_gates, dict):
            raise ContractError("JobManifest.resource_gates must be an object")
        if self.extraction is not None and not isinstance(self.extraction, dict):
            raise ContractError("JobManifest.extraction must be an object or null")
        if not isinstance(self.stages, dict):
            raise ContractError("JobManifest.stages must be an object")
        normalized: dict[str, StageRecord] = {}
        for stage, record in self.stages.items():
            if stage not in STAGE_ORDER:
                raise ContractError(
                    f"JobManifest.stages has unknown stage {stage!r}; "
                    f"valid stages: {list(STAGE_ORDER)}"
                )
            normalized[stage] = (
                record if isinstance(record, StageRecord) else StageRecord.from_dict(record)
            )
        self.stages = normalized

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "JobManifest":
        record = dict(_require_dict(data, "JobManifest"))
        _require_present(cls, record)
        record["stages"] = {
            stage: StageRecord.from_dict(value)
            for stage, value in (record.get("stages") or {}).items()
        }
        return cls(**record)


# --------------------------------------------------------------------------
# Provenance headers (video.md / audio.md)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ToolModelRef:
    """Tool plus optional model and version that generated an artifact."""

    tool: str
    version: str
    model: str | None = None

    def __post_init__(self) -> None:
        _require_str(self.tool, "ToolModelRef.tool")
        _require_str(self.version, "ToolModelRef.version")
        _optional_str(self.model, "ToolModelRef.model")

    @classmethod
    def from_dict(cls, data: Any) -> "ToolModelRef":
        record = _require_dict(data, "ToolModelRef")
        return cls(
            tool=record.get("tool"),
            version=record.get("version"),
            model=record.get("model"),
        )


@dataclasses.dataclass(frozen=True)
class FrameEvidence:
    """Per-frame provenance for ``video.md`` (MVP-PRD section 4)."""

    frame_id: str
    sha256: str
    timestamp_seconds: float
    roi: dict[str, Any] | None = None
    resize: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_str(self.frame_id, "FrameEvidence.frame_id")
        _require_sha256(self.sha256, "FrameEvidence.sha256")
        if isinstance(self.timestamp_seconds, bool) or not isinstance(
            self.timestamp_seconds, (int, float)
        ):
            raise ContractError("FrameEvidence.timestamp_seconds must be a number")
        object.__setattr__(self, "timestamp_seconds", float(self.timestamp_seconds))
        if self.roi is not None and not isinstance(self.roi, dict):
            raise ContractError("FrameEvidence.roi must be an object or null")
        if self.resize is not None and not isinstance(self.resize, dict):
            raise ContractError("FrameEvidence.resize must be an object or null")

    def to_entry(self) -> dict[str, Any]:
        return {
            "kind": "frame",
            "id": self.frame_id,
            "sha256": self.sha256,
            "timestamp_seconds": self.timestamp_seconds,
            "roi": self.roi,
            "resize": self.resize,
        }


@dataclasses.dataclass
class ProvenanceHeader:
    """Provenance header schema for ``video.md`` and ``audio.md``.

    Entries are provenance records: frame evidence for ``video.md``, and
    ASR/subtitle method, timing and origin records for ``audio.md``. Empty
    entries are valid only alongside an explicit outcome reason (for
    example, analysed no-speech audio is a finding, not an error).
    """

    artifact: str
    video_id: str
    media_sha256: str
    generator: ToolModelRef
    generated_at: str
    entries: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        _require_one_of(self.artifact, PROVENANCE_ARTIFACTS, "ProvenanceHeader.artifact")
        _require_str(self.video_id, "ProvenanceHeader.video_id")
        _require_sha256(self.media_sha256, "ProvenanceHeader.media_sha256")
        if not isinstance(self.generator, ToolModelRef):
            self.generator = ToolModelRef.from_dict(self.generator)
        _require_iso(self.generated_at, "ProvenanceHeader.generated_at")
        if not isinstance(self.entries, list):
            raise ContractError("ProvenanceHeader.entries must be a list")
        for index, entry in enumerate(self.entries):
            _require_dict(entry, f"ProvenanceHeader.entries[{index}]")
            _require_str(entry.get("kind"), f"entries[{index}].kind")
            _require_str(entry.get("id"), f"entries[{index}].id")
        if not isinstance(self.notes, list):
            raise ContractError("ProvenanceHeader.notes must be a list")
        for index, note in enumerate(self.notes):
            _require_str(note, f"ProvenanceHeader.notes[{index}]")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ProvenanceHeader":
        record = dict(_require_dict(data, "ProvenanceHeader"))
        record["generator"] = ToolModelRef.from_dict(record.get("generator"))
        return cls(**record)


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class InventoryEntry:
    """One inventoried item (MVP-PRD section 4).

    Deduplicated by ``(source, stable_id)``, retaining collection
    associations. ``declared_count``, ``observed_count`` and
    ``completeness`` are recorded separately and never invented.
    """

    source: str
    stable_id: str
    canonical_url: str
    input_origin: str
    discovered_at: str
    collections: tuple[str, ...] = ()
    declared_count: int | None = None
    observed_count: int | None = None
    completeness: str = "unknown"
    caption: str | None = None
    author: str | None = None
    is_photo_post: bool | None = None

    def __post_init__(self) -> None:
        _require_str(self.source, "InventoryEntry.source")
        _require_str(self.stable_id, "InventoryEntry.stable_id")
        _require_str(self.canonical_url, "InventoryEntry.canonical_url")
        _require_str(self.input_origin, "InventoryEntry.input_origin")
        _require_iso(self.discovered_at, "InventoryEntry.discovered_at")
        object.__setattr__(
            self, "collections", tuple(str(item) for item in self.collections)
        )
        object.__setattr__(
            self,
            "declared_count",
            _optional_int(self.declared_count, "InventoryEntry.declared_count"),
        )
        object.__setattr__(
            self,
            "observed_count",
            _optional_int(self.observed_count, "InventoryEntry.observed_count"),
        )
        _require_one_of(
            self.completeness, INVENTORY_COMPLETENESS, "InventoryEntry.completeness"
        )
        _optional_str(self.caption, "InventoryEntry.caption")
        _optional_str(self.author, "InventoryEntry.author")
        if self.is_photo_post is not None and not isinstance(self.is_photo_post, bool):
            raise ContractError("InventoryEntry.is_photo_post must be boolean or null")

    @classmethod
    def key(cls, source: str, stable_id: str) -> tuple[str, str]:
        """Deduplication key: source + stable video ID."""
        return (_require_str(source, "source"), _require_str(stable_id, "stable_id"))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "InventoryEntry":
        record = dict(_require_dict(data, "InventoryEntry"))
        record["collections"] = tuple(record.get("collections") or ())
        return cls(**record)


# --------------------------------------------------------------------------
# Classification, claims and backlog (PRD section 8.1)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Classification:
    """Taxonomy classification against the five fixed roots.

    Unknown classification is ``root=None`` plus a mandatory
    ``unknown_reason``; it is never a sixth taxonomy root.
    """

    root: str | None = None
    subgroup: str | None = None
    confidence: float | None = None
    unknown_reason: str | None = None

    def __post_init__(self) -> None:
        if self.root is None:
            _require_str(
                self.unknown_reason,
                "Classification.unknown_reason (required when root is null)",
            )
        else:
            if self.root not in TAXONOMY_ROOTS:
                raise ContractError(
                    f"Classification.root {self.root!r} is not one of the fixed "
                    f"roots {list(TAXONOMY_ROOTS)}; unknown classification must "
                    "be null with an explanation, never a new root"
                )
            _optional_str(self.subgroup, "Classification.subgroup")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(
                self.confidence, (int, float)
            ):
                raise ContractError("Classification.confidence must be a number")
            if not 0.0 <= float(self.confidence) <= 1.0:
                raise ContractError(
                    "Classification.confidence must be between 0.0 and 1.0"
                )
            object.__setattr__(self, "confidence", float(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "Classification":
        record = dict(_require_dict(data, "Classification"))
        return cls(
            root=record.get("root"),
            subgroup=record.get("subgroup"),
            confidence=record.get("confidence"),
            unknown_reason=record.get("unknown_reason"),
        )


@dataclasses.dataclass(frozen=True)
class Claim:
    """One factual assertion with its evidence source and verdict."""

    claim: str
    source: str
    verdict: str | None = None
    evidence: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _require_str(self.claim, "Claim.claim")
        _require_one_of(self.source, CLAIM_SOURCES, "Claim.source")
        if self.verdict is not None:
            _require_one_of(self.verdict, CLAIM_VERDICTS, "Claim.verdict")
        _optional_str(self.evidence, "Claim.evidence")
        _optional_str(self.note, "Claim.note")

    @classmethod
    def from_dict(cls, data: Any) -> "Claim":
        record = dict(_require_dict(data, "Claim"))
        return cls(**record)


@dataclasses.dataclass
class BacklogEntry:
    """Local backlog entry (PRD section 8.1).

    ``status`` is operator-edited: pending -> accepted | rejected. When
    ``classification`` is null, ``unknown_classification_reason`` must
    explain why; insufficient evidence never produces a fabricated class.
    """

    id: str
    url: str
    ingested_at: str
    author: str | None = None
    status: str = "pending"
    classification: Classification | None = None
    unknown_classification_reason: str | None = None
    entities: list[str] = dataclasses.field(default_factory=list)
    claims: list[Claim] = dataclasses.field(default_factory=list)
    fit: str | None = None
    actionable: str | None = None
    artifacts: str | None = None

    def __post_init__(self) -> None:
        _require_str(self.id, "BacklogEntry.id")
        _require_str(self.url, "BacklogEntry.url")
        _require_iso(self.ingested_at, "BacklogEntry.ingested_at")
        _optional_str(self.author, "BacklogEntry.author")
        _require_one_of(self.status, BACKLOG_STATUSES, "BacklogEntry.status")
        if self.classification is not None and not isinstance(
            self.classification, Classification
        ):
            self.classification = Classification.from_dict(self.classification)
        if self.classification is None:
            _require_str(
                self.unknown_classification_reason,
                "BacklogEntry.unknown_classification_reason "
                "(required when classification is null)",
            )
        if not isinstance(self.entities, list):
            raise ContractError("BacklogEntry.entities must be a list")
        for index, entity in enumerate(self.entities):
            _require_str(entity, f"BacklogEntry.entities[{index}]")
        if not isinstance(self.claims, list):
            raise ContractError("BacklogEntry.claims must be a list")
        normalized_claims: list[Claim] = []
        for index, claim in enumerate(self.claims):
            normalized_claims.append(
                claim if isinstance(claim, Claim) else Claim.from_dict(claim)
            )
        self.claims = normalized_claims
        _optional_str(self.fit, "BacklogEntry.fit")
        _optional_str(self.actionable, "BacklogEntry.actionable")
        _optional_str(self.artifacts, "BacklogEntry.artifacts")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "BacklogEntry":
        record = dict(_require_dict(data, "BacklogEntry"))
        if record.get("classification") is not None:
            record["classification"] = Classification.from_dict(
                record["classification"]
            )
        record["claims"] = [
            Claim.from_dict(claim) for claim in (record.get("claims") or [])
        ]
        return cls(**record)


# --------------------------------------------------------------------------
# Blocklist, taxonomy and processed entries
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BlocklistEntry:
    """Permanent reject record. Appended once; never removed."""

    video_id: str
    rejected_at: str
    reason: str
    source: str | None = None

    def __post_init__(self) -> None:
        _require_str(self.video_id, "BlocklistEntry.video_id")
        _require_iso(self.rejected_at, "BlocklistEntry.rejected_at")
        _require_str(self.reason, "BlocklistEntry.reason")
        _optional_str(self.source, "BlocklistEntry.source")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "BlocklistEntry":
        record = dict(_require_dict(data, "BlocklistEntry"))
        return cls(**record)


@dataclasses.dataclass(frozen=True)
class TaxonomyEntry:
    """One accumulated, normalized subgroup under a fixed root."""

    root: str
    subgroup: str
    added_at: str
    origin_video_id: str | None = None

    def __post_init__(self) -> None:
        if self.root not in TAXONOMY_ROOTS:
            raise ContractError(
                f"TaxonomyEntry.root {self.root!r} is not one of the fixed "
                f"roots {list(TAXONOMY_ROOTS)}"
            )
        _require_str(self.subgroup, "TaxonomyEntry.subgroup")
        _require_iso(self.added_at, "TaxonomyEntry.added_at")
        _optional_str(self.origin_video_id, "TaxonomyEntry.origin_video_id")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "TaxonomyEntry":
        record = dict(_require_dict(data, "TaxonomyEntry"))
        return cls(**record)


@dataclasses.dataclass
class ProcessedEntry:
    """Processed-video record: stable ID bound to media hash and fingerprints."""

    video_id: str
    media_sha256: str
    completed_at: str
    stage_fingerprints: dict[str, dict[str, Any]] = dataclasses.field(
        default_factory=dict
    )
    artifacts: list[str] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        _require_str(self.video_id, "ProcessedEntry.video_id")
        _require_sha256(self.media_sha256, "ProcessedEntry.media_sha256")
        _require_iso(self.completed_at, "ProcessedEntry.completed_at")
        if not isinstance(self.stage_fingerprints, dict):
            raise ContractError("ProcessedEntry.stage_fingerprints must be an object")
        if not isinstance(self.artifacts, list):
            raise ContractError("ProcessedEntry.artifacts must be a list")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ProcessedEntry":
        record = dict(_require_dict(data, "ProcessedEntry"))
        return cls(**record)
