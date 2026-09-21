"""Sanitized synthesis ingestion stage (MVP-PRD section 3.C, PRD section 6.5).

The pipeline stays CREDENTIAL-FREE: this module contains no API keys, no
model endpoints and performs no network calls. Synthesis is a
VALIDATED-DOCUMENT ingestion: the sanitized ``video.md`` + ``audio.md``
evidence leaves the machine only through an explicitly operator-supplied
synthesis document, which this stage validates against a strict schema,
scrubs for credential-looking strings, and persists as ``synthesis.json``
in the run directory with a resume fingerprint.

Fail-closed rules encoded here:

- A missing ``video.md`` or ``audio.md`` is an explicit failure with the
  missing modality named; it is NEVER an empty success (MVP-PRD section 4).
- With neither ``--from-file`` nor an injected document callable the stage
  is BLOCKED ("no text-model credential configured; supply --from-file"),
  never a silent empty.
- Unknown taxonomy roots, unknown claim sources or verdicts, out-of-range
  confidence and malformed fields are rejected with explicit reasons.
- When the credential scrub detects anything credential-looking in the
  document, NOTHING is persisted (no synthesis.json, no stage record) and
  the failure reason names the pattern kind, never the secret itself.

Where audio, visual, caption and metadata evidence disagree, every claim
keeps its own source label; disagreement is signal, not noise.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import (
    CLAIM_SOURCES,
    CLAIM_VERDICTS,
    TAXONOMY_ROOTS,
    ContractError,
    JobManifest,
    StageOutcome,
    StageRecord,
    StageResult,
    to_jsonable,
    utc_now_iso,
)
from .resume import build_fingerprint
from .state import (
    Blocklist,
    Processed,
    StateRoot,
    atomic_write_bytes,
)

# Bump when the synthesis document schema changes so recorded stage
# fingerprints distinguish schema generations.
SYNTHESIS_SCHEMA_VERSION = "1"

# Stage artifact name inside the run directory.
SYNTHESIS_FILENAME = "synthesis.json"


class SynthesisError(RuntimeError):
    """Raised for synthesis input errors that surface as outcome ``failed``."""


# --------------------------------------------------------------------------
# Strict document schema (dataclass-validated, fail closed)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SynthesisClassification:
    """Classification against the five FIXED taxonomy roots (PRD 6.7).

    Unknown classification is NOT representable here: a synthesis document
    must commit to one of the five roots. Unknown roots are rejected.
    """

    root: str
    subgroup: str
    confidence: float

    def __post_init__(self) -> None:
        if self.root not in TAXONOMY_ROOTS:
            raise ContractError(
                f"synthesis classification root {self.root!r} is not one of "
                f"the fixed roots {list(TAXONOMY_ROOTS)}; unknown roots are "
                "rejected, not invented"
            )
        if not isinstance(self.subgroup, str) or not self.subgroup.strip():
            raise ContractError("synthesis classification subgroup must be a non-empty string")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ContractError("synthesis classification confidence must be a number")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ContractError(
                "synthesis classification confidence must be between 0.0 and 1.0, "
                f"got {self.confidence!r}"
            )
        object.__setattr__(self, "confidence", float(self.confidence))

    @classmethod
    def from_dict(cls, data: Any) -> "SynthesisClassification":
        if not isinstance(data, Mapping):
            raise ContractError("synthesis classification must be an object")
        return cls(
            root=data.get("root"),
            subgroup=data.get("subgroup"),
            confidence=data.get("confidence"),
        )


@dataclasses.dataclass(frozen=True)
class SynthesisClaim:
    """One factual assertion (PRD section 6.5) with its evidence source.

    ``verdict`` is one of the five PRD section 6.6 verdicts or ``None``
    when the claim has not been verified yet. ``evidence`` entries are
    pointers to frame/audio refs in the sanitized artifacts.
    ``candidate_urls`` are the ONLY URLs the verification stage may fetch:
    they are operator/model-curated first-party candidates, never URLs
    extracted from media text.
    """

    claim: str
    source: str
    evidence: tuple[str, ...] = ()
    verdict: str | None = None
    note: str | None = None
    candidate_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.claim, str) or not self.claim.strip():
            raise ContractError("synthesis claim text must be a non-empty string")
        if self.source not in CLAIM_SOURCES:
            raise ContractError(
                f"synthesis claim source {self.source!r} must be one of "
                f"{list(CLAIM_SOURCES)}"
            )
        if self.verdict is not None and self.verdict not in CLAIM_VERDICTS:
            raise ContractError(
                f"synthesis claim verdict {self.verdict!r} must be one of "
                f"{list(CLAIM_VERDICTS)} or null (unverified yet)"
            )
        if not isinstance(self.evidence, (list, tuple)):
            raise ContractError("synthesis claim evidence must be a list of strings")
        object.__setattr__(self, "evidence", tuple(str(item) for item in self.evidence))
        if self.note is not None and not isinstance(self.note, str):
            raise ContractError("synthesis claim note must be a string or null")
        if not isinstance(self.candidate_urls, (list, tuple)):
            raise ContractError(
                "synthesis claim candidate_urls must be a list of URL strings"
            )
        object.__setattr__(
            self,
            "candidate_urls",
            tuple(str(item) for item in self.candidate_urls),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "SynthesisClaim":
        if not isinstance(data, Mapping):
            raise ContractError("synthesis claim must be an object")
        return cls(
            claim=data.get("claim"),
            source=data.get("source"),
            evidence=data.get("evidence") or (),
            verdict=data.get("verdict"),
            note=data.get("note"),
            candidate_urls=data.get("candidate_urls") or (),
        )


@dataclasses.dataclass(frozen=True)
class SynthesisEntity:
    """One named tool, repository, model or product (PRD section 6.5).

    Either a bare name or an object with ``name`` plus optional
    operator/model-curated first-party ``candidate_urls``.
    """

    name: str
    candidate_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ContractError("synthesis entity name must be a non-empty string")
        if not isinstance(self.candidate_urls, (list, tuple)):
            raise ContractError(
                "synthesis entity candidate_urls must be a list of URL strings"
            )
        object.__setattr__(
            self,
            "candidate_urls",
            tuple(str(item) for item in self.candidate_urls),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "SynthesisEntity":
        if isinstance(data, str):
            return cls(name=data)
        if isinstance(data, Mapping):
            return cls(name=data.get("name"), candidate_urls=data.get("candidate_urls") or ())
        raise ContractError("synthesis entity must be a string or an object with a name")


@dataclasses.dataclass(frozen=True)
class SynthesisDocument:
    """The complete, validated synthesis document."""

    classification: SynthesisClassification
    entities: tuple[SynthesisEntity, ...]
    claims: tuple[SynthesisClaim, ...]
    actionables: tuple[str, ...]
    fit: str
    model: str
    generated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.classification, SynthesisClassification):
            raise ContractError("synthesis classification is invalid")
        if not isinstance(self.entities, (list, tuple)):
            raise ContractError("synthesis entities must be a list")
        object.__setattr__(
            self, "entities", tuple(
                item if isinstance(item, SynthesisEntity) else SynthesisEntity.from_dict(item)
                for item in self.entities
            )
        )
        if not isinstance(self.claims, (list, tuple)):
            raise ContractError("synthesis claims must be a list")
        object.__setattr__(
            self, "claims", tuple(
                item if isinstance(item, SynthesisClaim) else SynthesisClaim.from_dict(item)
                for item in self.claims
            )
        )
        if not isinstance(self.actionables, (list, tuple)):
            raise ContractError("synthesis actionables must be a list")
        object.__setattr__(
            self, "actionables", tuple(str(item) for item in self.actionables)
        )
        if not isinstance(self.fit, str) or not self.fit.strip():
            raise ContractError("synthesis fit must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ContractError("synthesis model label must be a non-empty string")
        if not isinstance(self.generated_at, str) or not self.generated_at.strip():
            raise ContractError("synthesis generated_at must be an ISO-8601 timestamp")
        try:
            datetime.datetime.fromisoformat(
                self.generated_at.replace("Z", "+00:00")
            )
        except ValueError:
            raise ContractError(
                "synthesis generated_at must be an ISO-8601 timestamp"
            ) from None

    @classmethod
    def from_dict(cls, data: Any) -> "SynthesisDocument":
        if not isinstance(data, Mapping):
            raise ContractError("synthesis document must be a JSON object")
        classification = SynthesisClassification.from_dict(data.get("classification"))
        document = cls(
            classification=classification,
            entities=data.get("entities") or (),
            claims=data.get("claims") or (),
            actionables=data.get("actionables") or (),
            fit=data.get("fit"),
            model=data.get("model"),
            generated_at=data.get("generated_at"),
        )
        return document

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


# --------------------------------------------------------------------------
# Credential scrub (MVP-PRD section 3.D: scrub credentials and signed tokens
# from durable reports). Pattern KINDS are reported, never the matched text.
# --------------------------------------------------------------------------

_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)),
    ("password_assignment", re.compile(r"\bpass(?:word|wd)\s*[=:]\s*\S+", re.IGNORECASE)),
    ("api_key_assignment", re.compile(r"\bapi[_-]?key\s*[=:]\s*\S+", re.IGNORECASE)),
    ("secret_assignment", re.compile(r"\b(?:client[_-]?secret|secret|token)\s*[=:]\s*\S+", re.IGNORECASE)),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("authorization_header", re.compile(r"\bauthorization\s*:\s*\S+", re.IGNORECASE)),
)


def find_credential_leaks(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the KINDS of credential-looking strings in the document.

    The document is serialized through canonical JSON first so nested
    objects are covered. Only pattern names are returned: the reason
    surfaces to the operator without echoing any secret material.
    """
    text = json.dumps(to_jsonable(document), ensure_ascii=False)
    return tuple(name for name, pattern in _CREDENTIAL_PATTERNS if pattern.search(text))


# --------------------------------------------------------------------------
# Stage orchestration
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SynthesisOutcome:
    """Result of one synthesis invocation, reusable by CLI and tests."""

    outcome: str
    reason: str
    reused: bool
    video_id: str
    run_dir: str | None = None
    synthesis_sha256: str | None = None
    claims: int | None = None


def synthesis_fingerprint(media_sha256: str, source_sha256: str) -> dict[str, Any]:
    """Resume fingerprint: media sha + schema version + synthesis source hash."""
    return build_fingerprint(
        "synthesis",
        media_sha256,
        {
            "schema_version": SYNTHESIS_SCHEMA_VERSION,
            "source_sha256": source_sha256,
        },
    )


def _load_manifest(manifest_path: Path, video_id: str, media_sha256: str) -> JobManifest:
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


def _failure_record(
    manifest: JobManifest,
    manifest_path: Path,
    *,
    outcome: StageOutcome,
    reason: str,
    started_at: str,
) -> None:
    """Persist a non-complete stage record (fingerprint empty, no artifact)."""
    finished_at = utc_now_iso()
    manifest.stages["synthesis"] = StageRecord(
        stage="synthesis",
        result=StageResult(outcome=outcome, reason=reason),
        fingerprint={},
        input_sha256=None,
        output_sha256=None,
        started_at=started_at,
        finished_at=finished_at,
    )
    manifest.updated_at = finished_at
    _write_manifest(manifest_path, manifest)


def run_synthesis_stage(
    video_id: str,
    *,
    state: StateRoot,
    from_file: Path | str | None = None,
    document_fn: Callable[[], Mapping[str, Any]] | None = None,
    force: bool = False,
) -> SynthesisOutcome:
    """Validate and persist the synthesis document for one video.

    ``from_file`` is the explicit operator-supplied synthesis JSON.
    ``document_fn`` is an injectable callable seam for tests. Exactly one
    source may be supplied; neither is a BLOCKED outcome (the pipeline
    itself performs no text-model calls), never a silent empty.
    """
    state.ensure_layout()
    if Blocklist(state).is_blocked(video_id):
        return SynthesisOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="video ID is permanently blocklisted; no flag can override "
            "an operator rejection",
            reused=False,
            video_id=video_id,
        )

    entry = Processed(state).get(video_id)
    if entry is None:
        return SynthesisOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="no fetch record for this ID; run fetch-url first",
            reused=False,
            video_id=video_id,
        )
    if not entry.artifacts:
        return SynthesisOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="fetch record has no run directory reference",
            reused=False,
            video_id=video_id,
        )
    run_dir = state.root / entry.artifacts[0]
    manifest_path = run_dir / "meta.json"
    manifest = _load_manifest(manifest_path, video_id, entry.media_sha256)

    # Sanitized evidence must exist for BOTH modalities: a missing modality
    # is an explicit reason, never an empty success (MVP-PRD section 4).
    missing = [
        name
        for name in ("video.md", "audio.md")
        if not (run_dir / name).is_file()
    ]
    if missing:
        reason = (
            f"sanitized evidence incomplete: {', '.join(missing)} missing from "
            f"the run directory; a missing modality is an explicit reason, "
            "never an empty success"
        )
        started_at = utc_now_iso()
        _failure_record(
            manifest, manifest_path,
            outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
        )
        return SynthesisOutcome(
            outcome=StageOutcome.FAILED.value,
            reason=reason,
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    if from_file is not None and document_fn is not None:
        return SynthesisOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="supply exactly one synthesis source: --from-file or an "
            "injected document, never both",
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )
    if from_file is None and document_fn is None:
        return SynthesisOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="no text-model credential configured; supply --from-file "
            "(synthesis is a validated-document ingestion; this pipeline "
            "performs no model calls itself)",
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    started_at = utc_now_iso()
    raw_bytes: bytes
    if from_file is not None:
        source_path = Path(from_file)
        if not source_path.is_file():
            reason = f"synthesis source file does not exist: {source_path}"
            _failure_record(
                manifest, manifest_path,
                outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
            )
            return SynthesisOutcome(
                outcome=StageOutcome.FAILED.value,
                reason=reason,
                reused=False,
                video_id=video_id,
                run_dir=str(run_dir),
            )
        raw_bytes = source_path.read_bytes()
    else:
        assert document_fn is not None  # narrowing: neither/both handled above
        try:
            raw_document = document_fn()
        except Exception as exc:  # noqa: BLE001 - surfaced as explicit outcome
            reason = f"synthesis document callable failed: {exc}"
            _failure_record(
                manifest, manifest_path,
                outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
            )
            return SynthesisOutcome(
                outcome=StageOutcome.FAILED.value,
                reason=reason,
                reused=False,
                video_id=video_id,
                run_dir=str(run_dir),
            )
        raw_bytes = json.dumps(
            to_jsonable(raw_document), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")

    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    fingerprint = synthesis_fingerprint(entry.media_sha256, source_sha256)
    synthesis_path = run_dir / SYNTHESIS_FILENAME

    # Resume: a matching complete stage plus the persisted artifact is
    # reused with zero revalidation work. A corrupted artifact is never
    # fatal on reuse: it falls through to full revalidation instead.
    recorded = manifest.stages.get("synthesis")
    if (
        not force
        and recorded is not None
        and recorded.result.outcome is StageOutcome.COMPLETE
        and recorded.fingerprint == fingerprint
        and synthesis_path.is_file()
    ):
        try:
            reused_document = SynthesisDocument.from_dict(
                json.loads(synthesis_path.read_text(encoding="utf-8"))
            )
            return SynthesisOutcome(
                outcome=StageOutcome.COMPLETE.value,
                reason="synthesis fingerprint and synthesis.json match the "
                "recorded run; zero re-validation performed",
                reused=True,
                video_id=video_id,
                run_dir=str(run_dir),
                synthesis_sha256=source_sha256,
                claims=len(reused_document.claims),
            )
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            pass  # corrupted artifact: recompute below

    # Parse and validate strictly. Any schema rejection is an explicit
    # failed outcome BEFORE anything is persisted.
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        reason = f"synthesis source is not valid UTF-8 JSON: {exc}"
        _failure_record(
            manifest, manifest_path,
            outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
        )
        return SynthesisOutcome(
            outcome=StageOutcome.FAILED.value,
            reason=reason,
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )
    try:
        document = SynthesisDocument.from_dict(parsed)
    except ContractError as exc:
        reason = f"synthesis document rejected by schema validation: {exc}"
        _failure_record(
            manifest, manifest_path,
            outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
        )
        return SynthesisOutcome(
            outcome=StageOutcome.FAILED.value,
            reason=reason,
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    # Credential scrub: on detection persist NOTHING (no synthesis.json and
    # no stage record) and fail with an explicit sanitization reason. Both
    # the RAW source (including fields the schema ignores) and the validated
    # document that would be persisted are scanned.
    leaks = set(find_credential_leaks(parsed))
    leaks.update(find_credential_leaks(document.to_dict()))
    if leaks:
        return SynthesisOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="synthesis document failed the credential scrub "
            f"({', '.join(sorted(leaks))}); nothing was persisted and the "
            "run directory is unchanged",
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    atomic_write_bytes(
        synthesis_path,
        json.dumps(
            document.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n",
    )
    output_sha256 = hashlib.sha256(synthesis_path.read_bytes()).hexdigest()

    finished_at = utc_now_iso()
    manifest.stages["synthesis"] = StageRecord(
        stage="synthesis",
        result=StageResult(
            outcome=StageOutcome.COMPLETE,
            reason=f"validated synthesis document ingested: "
            f"{len(document.claims)} claim(s), {len(document.entities)} "
            f"entity(ies), root {document.classification.root!r}",
        ),
        fingerprint=fingerprint,
        input_sha256=entry.media_sha256,
        output_sha256=output_sha256,
        started_at=started_at,
        finished_at=finished_at,
    )
    manifest.updated_at = finished_at
    _write_manifest(manifest_path, manifest)

    entry.stage_fingerprints["synthesis"] = fingerprint
    synthesis_ref = f"{entry.artifacts[0]}/{SYNTHESIS_FILENAME}"
    if synthesis_ref not in entry.artifacts:
        entry.artifacts.append(synthesis_ref)
    Processed(state).record(entry)

    return SynthesisOutcome(
        outcome=StageOutcome.COMPLETE.value,
        reason="synthesis completed: "
        + manifest.stages["synthesis"].result.reason,
        reused=False,
        video_id=video_id,
        run_dir=str(run_dir),
        synthesis_sha256=output_sha256,
        claims=len(document.claims),
    )
