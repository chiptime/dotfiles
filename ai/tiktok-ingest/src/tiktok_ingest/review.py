"""Safe, minimal backlog review interface (list, show, decide, plan, apply).

Operator-facing review lifecycle over the EXISTING backlog and blocklist
state (no new formats for state, no pipeline changes):

- ``list_entries``   backlog entries with per-line hashes and an optional
  status filter.
- ``show_entry``     one entry with its claims, provenance and the
  mechanical verification reasons from the recorded
  ``verification.json`` (read-only).
- ``record_decision`` one EXPLICIT operator decision (pending/accepted/
  rejected) appended to a versioned JSONL decisions document. Decisions
  are never inferred; a decision binds the current raw backlog line hash.
- ``build_plan``     dry run: IDs, status changes, expected per-entry and
  whole-file hashes, blocklist additions, conflicts.
- ``apply_decisions`` applies ONLY explicit, still-current decisions with
  verified exclusive backups, fail-safe write ordering, a receipt and
  post-write verification.

Honest concurrency model (NO invented guarantees):

- There is NO cooperative lock in the product. The existing writers
  (``Backlog.append`` during verify/emit, ``Blocklist.reject``) publish
  whole files through ``atomic_write_bytes`` and ignore any lock this
  module could take, so a lock used only here would protect nothing and
  is deliberately NOT added (a partial lock is a false guarantee).
- The concurrency control is therefore OPTIMISTIC and hash-based: a
  decision records ``entry_sha256`` (sha256 of the raw backlog line
  without the trailing newline); apply revalidates every rewritten
  entry's hash on the exact bytes that generate the new backlog BEFORE
  any publication, so an old decision is never applied to new content.
- The residual race window between that revalidation read and
  ``os.replace`` cannot be closed without coordination honored by ALL
  writers. It is NOT claimed to be closed. The post-write re-read
  verifies OUR OWN publication bytes only: it detects an external write
  that landed AFTER our replace, and it can NEVER detect a third-party
  change our replace already destroyed. Without a product-wide
  exclusive-writer window that loss remains possible; the backups +
  receipt make the state recoverable, not atomic.

Multi-file change model (NOT a transaction):

- ``blocklist.json`` is published BEFORE ``backlog.jsonl`` (fail-safe
  order: an interrupted reject leaves an over-blocked ID, never an
  incoherent "rejected backlog line without a permanent blocklist entry").
- Each file replacement is atomic (temp file + fsync + ``os.replace``);
  the multi-file sequence is NOT atomic. Once the first publication has
  happened, any detected conflict or failure is registered in a
  ``status: "partial"`` receipt naming exactly what was published.
  Recovery = re-run apply (idempotent, hash-guarded) or restore the
  exclusive backups recorded in the receipt.

Decisions document format (``tiktok-ingest/backlog-decision@1``): strict
JSONL, ONE compact JSON object per line, no pretty-printed/multi-line
JSON, no unknown fields. See OPERATIONS.md for the operator procedure.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .contracts import BACKLOG_STATUSES, utc_now_iso
from .state import Blocklist, BlocklistEntry, StateRoot, atomic_write_bytes

DECISIONS_SCHEMA = "tiktok-ingest/backlog-decision@1"
PLAN_SCHEMA = "tiktok-ingest/backlog-plan@1"
RECEIPT_SCHEMA = "tiktok-ingest/backlog-apply-receipt@1"

DECISION_REQUIRED_FIELDS = {"schema", "id", "decision", "decided_at", "entry_sha256"}
DECISION_OPTIONAL_FIELDS = {"reason"}
BACKLOG_NAME = "backlog.jsonl"
BLOCKLIST_NAME = "blocklist.json"
REVIEW_DIR_NAME = "reviews"
APPLY_PREFIX = "backlog-apply-"
RECEIPT_FILENAME = "apply-receipt.json"


class ReviewError(RuntimeError):
    """Raised when a review operation cannot be completed."""


class ReviewConflictError(ReviewError):
    """Raised when state changed under us or publication failed.

    Raises NEVER abort a run that already published something without
    registering it: once the first publication happened, the partial
    state is recorded in a ``status: "partial"`` receipt before the
    error propagates. Before any publication, conflicts raise with zero
    data writes (backups, if created, are preserved for recovery).
    """


# --------------------------------------------------------------------------
# Raw backlog access (bytes-level: hashes are computed on raw lines)
# --------------------------------------------------------------------------


class _RawBacklog:
    """The backlog file as raw byte lines plus its newline discipline."""

    def __init__(self, raw: bytes) -> None:
        self.trailing_newline = raw.endswith(b"\n")
        body = raw[:-1] if self.trailing_newline else raw
        self.lines: list[bytes] = body.split(b"\n") if body else []

    def payload(self) -> bytes:
        if not self.lines:
            return b""
        body = b"\n".join(self.lines)
        return body + b"\n" if self.trailing_newline else body


def _read_raw_backlog(state: StateRoot) -> _RawBacklog:
    path = state.backlog_path
    if not path.exists():
        raise ReviewError(f"no backlog at {path}; nothing to review")
    return _RawBacklog(path.read_bytes())


def _parse_raw_line(line: bytes, lineno: int) -> dict[str, Any]:
    try:
        parsed = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(
            f"backlog line {lineno} is not a single-line JSON object "
            f"(JSONL files hold ONE compact object per line): {exc}"
        ) from exc
    if not isinstance(parsed, dict) or "id" not in parsed:
        raise ReviewError(f"backlog line {lineno} has no 'id' field")
    return parsed


def _indexed_entries(raw: _RawBacklog) -> list[tuple[int, bytes, dict[str, Any]]]:
    result: list[tuple[int, bytes, dict[str, Any]]] = []
    seen: dict[str, int] = {}
    for index, line in enumerate(raw.lines):
        if not line.strip():
            continue
        parsed = _parse_raw_line(line, index + 1)
        video_id = str(parsed.get("id"))
        if video_id in seen:
            raise ReviewError(
                f"backlog has duplicate entries for id {video_id!r} "
                f"(lines {seen[video_id]} and {index + 1}); refusing to guess"
            )
        seen[video_id] = index + 1
        result.append((index, line, parsed))
    return result


def _find_entry(
    raw: _RawBacklog, video_id: str
) -> tuple[int, bytes, dict[str, Any]]:
    for index, line, parsed in _indexed_entries(raw):
        if str(parsed.get("id")) == video_id:
            return index, line, parsed
    raise ReviewError(f"unknown backlog id {video_id!r}")


def entry_sha256(raw_line: bytes) -> str:
    """sha256 of ONE raw backlog line, without the trailing newline."""
    return hashlib.sha256(raw_line).hexdigest()


def _canonical_line(entry: dict[str, Any]) -> bytes:
    """Canonical single-line serialization (matches Backlog.append)."""
    return json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8")


# --------------------------------------------------------------------------
# Decisions document (strict versioned JSONL)
# --------------------------------------------------------------------------


def _require_iso_z(value: Any, field: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{where}: {field} must be a non-empty string")
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReviewError(
            f"{where}: {field} must be an ISO-8601 timestamp, got {value!r}"
        ) from None
    return value


def _validate_decision(record: Any, where: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ReviewError(f"{where}: each decision must be a JSON object")
    keys = set(record)
    missing = DECISION_REQUIRED_FIELDS - keys
    if missing:
        raise ReviewError(f"{where}: missing required fields {sorted(missing)}")
    unknown = keys - DECISION_REQUIRED_FIELDS - DECISION_OPTIONAL_FIELDS
    if unknown:
        raise ReviewError(
            f"{where}: unknown fields {sorted(unknown)}; the format is "
            f"{DECISIONS_SCHEMA} and must not be extended in place"
        )
    if record["schema"] != DECISIONS_SCHEMA:
        raise ReviewError(
            f"{where}: schema must be {DECISIONS_SCHEMA!r}, "
            f"got {record['schema']!r}"
        )
    video_id = record["id"]
    if not isinstance(video_id, str) or not video_id.strip():
        raise ReviewError(f"{where}: id must be a non-empty string")
    decision = record["decision"]
    if decision not in BACKLOG_STATUSES:
        raise ReviewError(
            f"{where}: decision must be one of {list(BACKLOG_STATUSES)}, "
            f"got {decision!r}"
        )
    _require_iso_z(record["decided_at"], "decided_at", where)
    entry_hash = record["entry_sha256"]
    if (
        not isinstance(entry_hash, str)
        or len(entry_hash) != 64
        or any(c not in "0123456789abcdef" for c in entry_hash)
    ):
        raise ReviewError(
            f"{where}: entry_sha256 must be a lowercase 64-hex sha256"
        )
    reason = record.get("reason")
    if decision == "rejected":
        if not isinstance(reason, str) or not reason.strip():
            raise ReviewError(
                f"{where}: a 'rejected' decision requires a non-empty "
                "reason (it becomes the permanent blocklist reason)"
            )
    elif reason is not None and not isinstance(reason, str):
        raise ReviewError(f"{where}: reason must be a string or null")
    return record


def parse_decisions_file(path: Path | str) -> list[dict[str, Any]]:
    """Strictly parse a decisions document (schema-versioned JSONL)."""
    file_path = Path(path)
    if not file_path.exists():
        raise ReviewError(f"decisions file not found: {file_path}")
    raw = file_path.read_bytes()
    if not raw.strip():
        raise ReviewError(f"decisions file is empty: {file_path}")
    decisions: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for lineno, line in enumerate(raw.split(b"\n"), start=1):
        if not line.strip():
            continue
        where = f"decisions line {lineno}"
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewError(
                f"{where} is not a single-line JSON object (JSONL files "
                "hold ONE compact object per line; pretty-printed "
                f"multi-line JSON is refused): {exc}"
            ) from exc
        _validate_decision(record, where)
        video_id = record["id"]
        if video_id in seen:
            raise ReviewError(
                f"{where}: duplicate decision for id {video_id!r} "
                f"(first seen on line {seen[video_id]}); refusing an "
                "ambiguous decision set"
            )
        seen[video_id] = lineno
        decisions.append(record)
    if not decisions:
        raise ReviewError(f"decisions file has no decisions: {file_path}")
    return decisions


def record_decision(
    state: StateRoot,
    *,
    video_id: str,
    decision: str,
    decisions_file: Path | str,
    reason: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Record ONE explicit operator decision into the decisions document.

    The decision binds the CURRENT raw backlog line hash so a later apply
    can detect concurrent changes. Nothing in the pipeline state is
    modified here.
    """
    if decision not in BACKLOG_STATUSES:
        raise ReviewError(
            f"decision must be one of {list(BACKLOG_STATUSES)}, got {decision!r}"
        )
    if decision == "rejected" and not (isinstance(reason, str) and reason.strip()):
        raise ReviewError(
            "a 'rejected' decision requires --reason (it becomes the "
            "permanent blocklist reason)"
        )
    raw = _read_raw_backlog(state)
    index, line, _ = _find_entry(raw, video_id)
    current_hash = entry_sha256(line)

    file_path = Path(decisions_file)
    existing: list[dict[str, Any]] = []
    if file_path.exists():
        existing = parse_decisions_file(file_path)
        for prior in existing:
            if prior["id"] == video_id:
                raise ReviewError(
                    f"decisions file already records a decision for id "
                    f"{video_id!r}; refusing to duplicate or shadow it"
                )
    record = {
        "schema": DECISIONS_SCHEMA,
        "id": video_id,
        "decision": decision,
        "decided_at": now or utc_now_iso(),
        "entry_sha256": current_hash,
        "reason": reason,
    }
    all_records = existing + [record]
    payload = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in all_records
    )
    atomic_write_bytes(file_path, payload.encode("utf-8"))
    return record


# --------------------------------------------------------------------------
# Plan (dry run)
# --------------------------------------------------------------------------


def build_plan(
    state: StateRoot,
    decisions: list[dict[str, Any]],
    *,
    decisions_file: str | None = None,
) -> dict[str, Any]:
    """Compute the full dry-run document WITHOUT writing anything."""
    raw = _read_raw_backlog(state)
    entries = {str(entry[2]["id"]): entry for entry in _indexed_entries(raw)}
    blocked = Blocklist(state).entries()

    planned: list[dict[str, Any]] = []
    rewrites: dict[int, bytes] = {}
    blocklist_additions: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    applied_ids: list[str] = []

    for decision in decisions:
        video_id = decision["id"]
        target = decision["decision"]
        if video_id not in entries:
            raise ReviewError(
                f"decision for unknown backlog id {video_id!r}; refusing "
                "to plan a partial application"
            )
        index, line, parsed = entries[video_id]
        current_hash = entry_sha256(line)
        current_status = parsed.get("status")
        is_blocked = video_id in blocked

        item: dict[str, Any] = {
            "id": video_id,
            "decision": target,
            "current_status": current_status,
            "decision_entry_sha256": decision["entry_sha256"],
            "current_entry_sha256": current_hash,
            "blocklisted": is_blocked,
            "rewrite": False,
            "append_blocklist": False,
            "already_applied": False,
            "stale": False,
            "post_entry_sha256": None,
            "blocklist_reason": None,
        }

        if is_blocked and target in ("pending", "accepted"):
            conflicts.append(
                {
                    "id": video_id,
                    "reason": (
                        f"id is permanently blocklisted; only 'rejected' "
                        f"is coherent (got decision {target!r})"
                    ),
                }
            )
            item["stale"] = True

        if current_status == target:
            if target == "rejected" and not is_blocked:
                item["append_blocklist"] = True
                item["blocklist_reason"] = decision.get("reason")
                blocklist_additions.append(
                    {"id": video_id, "reason": str(decision.get("reason"))}
                )
            else:
                item["already_applied"] = True
                applied_ids.append(video_id)
        else:
            if current_hash != decision["entry_sha256"]:
                item["stale"] = True
                conflicts.append(
                    {
                        "id": video_id,
                        "reason": (
                            "backlog line changed since the decision was "
                            "recorded (entry hash mismatch): concurrent "
                            "modification or obsolete decision"
                        ),
                    }
                )
            else:
                item["rewrite"] = True
                updated = dict(parsed)
                updated["status"] = target
                new_line = _canonical_line(updated)
                item["post_entry_sha256"] = entry_sha256(new_line)
                rewrites[index] = new_line
                if target == "rejected" and not is_blocked:
                    item["append_blocklist"] = True
                    item["blocklist_reason"] = decision.get("reason")
                    blocklist_additions.append(
                        {"id": video_id, "reason": str(decision.get("reason"))}
                    )
        planned.append(item)

    after_raw = _RawBacklog(b"")
    after_raw.trailing_newline = raw.trailing_newline
    after_raw.lines = [
        rewrites.get(index, line) for index, line, _ in _indexed_entries(raw)
    ]
    after_payload = after_raw.payload()
    before_payload = raw.payload()
    files_to_write: list[str] = []
    if rewrites:
        files_to_write.append(BACKLOG_NAME)
    if blocklist_additions:
        files_to_write.append(BLOCKLIST_NAME)
    files_to_write.sort()

    return {
        "schema": PLAN_SCHEMA,
        "decisions_file": decisions_file,
        "decisions": planned,
        "applied_count": len(applied_ids),
        "conflicts": conflicts,
        "blocklist_additions": blocklist_additions,
        "files_to_write": files_to_write,
        "backlog_sha256_before": hashlib.sha256(before_payload).hexdigest(),
        "backlog_sha256_after": hashlib.sha256(after_payload).hexdigest(),
    }


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------


def _write_backup_exclusive(destination: Path, data: bytes) -> None:
    """Create the backup with EXCLUSIVE creation; never overwrite."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise ReviewConflictError(
            f"backup already exists (refusing to overwrite): {destination}"
        ) from None
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _stamp_from_iso(iso: str) -> str:
    return iso.replace(":", "-")


def _revalidate_publish_source(
    fresh: "_RawBacklog",
    decisions: list[dict[str, Any]],
    plan: dict[str, Any],
) -> None:
    """Re-check every rewritten entry's hash on the bytes about to be
    used to generate the new backlog, BEFORE any publication.

    A fresh re-read without this comparison is not protection: without
    it, a decision recorded against old content would be silently
    applied to content a concurrent writer already changed. Raises
    :class:`ReviewConflictError` with zero data writes (backups may
    already exist; nothing was published).
    """
    by_id = {str(entry[2]["id"]): entry for entry in _indexed_entries(fresh)}
    for decision, item in zip(decisions, plan["decisions"]):
        if not item["rewrite"]:
            continue
        video_id = decision["id"]
        if video_id not in by_id:
            raise ReviewConflictError(
                f"entry {video_id!r} disappeared between planning and "
                "publication; refusing to apply the old decision. No "
                "data files were published; verified backups exist in "
                "the audit directory."
            )
        _, line, _ = by_id[video_id]
        current = entry_sha256(line)
        if current != decision["entry_sha256"]:
            raise ReviewConflictError(
                f"entry {video_id!r} changed between planning and "
                f"publication (entry_sha256 {item['current_entry_sha256']} "
                f"-> {current}); refusing to apply the old decision to "
                "new content. No data files were published; verified "
                "backups exist in the audit directory."
            )


def _register_partial_publication(
    *,
    state: StateRoot,
    audit_dir: Path,
    moment: str,
    decisions_file: str | None,
    plan: dict[str, Any],
    backups: list[dict[str, Any]],
    blocklist_results: list[dict[str, str]],
    backlog_published: bool,
    backlog_sha256_before: str | None,
) -> None:
    """Best-effort receipt recording a PARTIAL publication: exactly what
    was published when a conflict or failure was detected. This makes
    the interrupted state explicit and recoverable instead of silent."""
    published_files: list[str] = []
    if blocklist_results:
        published_files.append(BLOCKLIST_NAME)
    if backlog_published:
        published_files.append(BACKLOG_NAME)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "partial",
        "applied_at": moment,
        "decisions_file": decisions_file,
        "published_files": published_files,
        "backlog_published": backlog_published,
        "blocklist_published": blocklist_results,
        "backlog_sha256_before": backlog_sha256_before,
        "applied_ids_planned": [d["id"] for d in plan["decisions"] if d["rewrite"]],
        "already_applied": [d["id"] for d in plan["decisions"] if d["already_applied"]],
        "backups": backups,
        "recovery": (
            "publication was interrupted mid-way; re-run backlog-apply "
            "(idempotent, hash-guarded) or restore the exclusive backups "
            "listed above into the state root; see OPERATIONS.md"
        ),
    }
    try:
        atomic_write_bytes(
            audit_dir / RECEIPT_FILENAME,
            json.dumps(
                receipt, ensure_ascii=False, indent=2, sort_keys=True
            ).encode("utf-8")
            + b"\n",
        )
    except OSError:
        # Never mask the original publication failure.
        pass


def apply_decisions(
    state: StateRoot,
    decisions: list[dict[str, Any]],
    *,
    decisions_file: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Apply explicit, still-current decisions with verified backups.

    Guarantee phases, precisely:

    - Plan conflicts (stale hash, unknown ID, blocklist incoherence)
      abort with ZERO data writes.
    - Backups are created exclusively and hash-verified before any
      publication.
    - EVERY rewritten entry's hash is REVALIDATED on the exact bytes
      that will generate the new backlog BEFORE any publication; a
      change between planning and publication aborts with zero data
      writes (the old decision is never applied to new content).
    - Publication order: blocklist first (fail-safe), then backlog.
    - AFTER the first publication, a detected conflict or write failure
      is a PARTIAL publication: a ``status: "partial"`` receipt is
      written naming exactly what was published, and the error says so
      (never "zero writes").
    - The post-write re-read verifies OUR OWN publication bytes only.
      It CANNOT detect a third-party change that our ``os.replace``
      already overwrote; that residual window requires coordination
      among ALL writers, which the product does not have.
    """
    moment = now or utc_now_iso()
    plan = build_plan(state, decisions, decisions_file=decisions_file)

    if plan["conflicts"]:
        names = ", ".join(c["id"] for c in plan["conflicts"])
        raise ReviewConflictError(
            "refusing to apply: "
            + "; ".join(f"{c['id']}: {c['reason']}" for c in plan["conflicts"])
            + f" (ids: {names}). No files were written."
        )

    if not plan["files_to_write"]:
        before_payload = _read_raw_backlog(state).payload()
        audit_dir = (
            state.root
            / REVIEW_DIR_NAME
            / f"{APPLY_PREFIX}{_stamp_from_iso(moment)}"
        )
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "noop",
            "applied_at": moment,
            "decisions_file": decisions_file,
            "applied": [],
            "already_applied": [d["id"] for d in plan["decisions"]],
            "blocklist": [],
            "files_written": [],
            "backups": [],
            "backlog_sha256_before": hashlib.sha256(before_payload).hexdigest(),
            "backlog_sha256_after": hashlib.sha256(before_payload).hexdigest(),
            "changed": False,
            "recovery": "no data files were written; this run is a no-op",
        }
        audit_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = audit_dir / RECEIPT_FILENAME
        receipt_note = None
        try:
            _write_backup_exclusive(
                receipt_path,
                json.dumps(
                    receipt, ensure_ascii=False, indent=2, sort_keys=True
                ).encode("utf-8")
                + b"\n",
            )
        except ReviewConflictError:
            # A prior receipt lives here; it is NEVER overwritten.
            receipt_note = (
                "prior receipt preserved at this path; no new receipt written"
            )
        if receipt_note:
            receipt["receipt_note"] = receipt_note
        return receipt

    audit_dir = (
        state.root / REVIEW_DIR_NAME / f"{APPLY_PREFIX}{_stamp_from_iso(moment)}"
    )

    # ---- backup phase: exclusive creation + hash verification BEFORE writes
    # A file in files_to_write may not exist yet (e.g. the first-ever
    # blocklist.json): there is nothing to back up, and the receipt
    # records that the file is newly created by this run.
    backups: list[dict[str, Any]] = []
    source_bytes: dict[str, bytes] = {}
    for name in plan["files_to_write"]:
        source = state.root / name
        backup_rel = f"{REVIEW_DIR_NAME}/{APPLY_PREFIX}{_stamp_from_iso(moment)}/backup/{name}"
        backup_path = state.root / backup_rel
        if not source.exists():
            backups.append(
                {"path": backup_rel, "sha256": None, "note": "new file"}
            )
            continue
        data = source.read_bytes()
        source_bytes[name] = data
        _write_backup_exclusive(backup_path, data)
        copied = backup_path.read_bytes()
        if hashlib.sha256(copied).hexdigest() != hashlib.sha256(data).hexdigest():
            raise ReviewConflictError(
                f"backup verification failed for {name} "
                f"({backup_rel}); aborting before any data write"
            )
        backups.append({"path": backup_rel, "sha256": hashlib.sha256(data).hexdigest()})

    # ---- pre-publication revalidation on the PUBLISH-SOURCE bytes.
    # The fresh read below supplies the bytes that generate the new
    # backlog; every rewritten entry's hash is re-checked against the
    # decision BEFORE anything is published. A fresh re-read WITHOUT
    # this comparison would happily apply the old decision to new
    # content. On mismatch: zero data writes (verified backups exist).
    fresh = _read_raw_backlog(state)
    _revalidate_publish_source(fresh, decisions, plan)

    # Render the new payload from THOSE revalidated bytes (rewrite lines
    # by id, keep everything else byte-exact). When no line needs
    # rewriting the backlog is NOT written again.
    rewrite_by_id: dict[str, bytes] = {}
    for decision, item in zip(decisions, plan["decisions"]):
        if item["rewrite"]:
            _, _, parsed = next(
                e for e in _indexed_entries(fresh) if str(e[2]["id"]) == decision["id"]
            )
            updated = dict(parsed)
            updated["status"] = decision["decision"]
            rewrite_by_id[decision["id"]] = _canonical_line(updated)

    new_payload: bytes | None = None
    after_hash: str | None = None
    if rewrite_by_id:
        new_lines: list[bytes] = []
        for _, line, parsed in _indexed_entries(fresh):
            new_lines.append(rewrite_by_id.get(str(parsed["id"]), line))
        new_raw = _RawBacklog(b"")
        new_raw.trailing_newline = fresh.trailing_newline
        new_raw.lines = new_lines
        new_payload = new_raw.payload()
        after_hash = hashlib.sha256(new_payload).hexdigest()

    # ---- publication: blocklist FIRST (fail-safe), then backlog.
    # From the first publication onward any detected failure is a
    # PARTIAL publication and must be registered as such.
    blocklist_results: list[dict[str, str]] = []
    backlog_published = False
    try:
        for decision, item in zip(decisions, plan["decisions"]):
            if item["append_blocklist"]:
                result = Blocklist(state).reject(
                    BlocklistEntry(
                        video_id=item["id"],
                        rejected_at=moment,
                        reason=str(item["blocklist_reason"]),
                        source="backlog-review-cli",
                    )
                )
                blocklist_results.append({"id": item["id"], "result": result})

        if new_payload is not None:
            atomic_write_bytes(state.backlog_path, new_payload)
            backlog_published = True

        # Post-write verification: proves OUR publication bytes are what
        # is on disk NOW. It detects an external write that landed AFTER
        # our replace; it can NEVER detect a third-party change our
        # replace already destroyed (that loss is silent by design of
        # whole-file replace without a shared writer coordination).
        if backlog_published:
            published = state.backlog_path.read_bytes()
            if hashlib.sha256(published).hexdigest() != after_hash:
                raise ReviewConflictError(
                    "post-write verification FAILED for backlog.jsonl: "
                    "the bytes on disk differ from our published payload "
                    "(a writer raced AFTER our replace). This is a "
                    "PARTIAL publication."
                )
        for entry in blocklist_results:
            if not Blocklist(state).is_blocked(entry["id"]):
                raise ReviewConflictError(
                    f"post-write verification FAILED for blocklist.jsonl: "
                    f"id {entry['id']!r} is not blocked after publication. "
                    "This is a PARTIAL publication."
                )
    except (ReviewConflictError, OSError):
        _register_partial_publication(
            state=state,
            audit_dir=audit_dir,
            moment=moment,
            decisions_file=decisions_file,
            plan=plan,
            backups=backups,
            blocklist_results=blocklist_results,
            backlog_published=backlog_published,
            backlog_sha256_before=(
                hashlib.sha256(source_bytes[BACKLOG_NAME]).hexdigest()
                if BACKLOG_NAME in source_bytes
                else None
            ),
        )
        raise

    written: list[dict[str, Any]] = []
    if blocklist_results:
        written.append(
            {
                "path": BLOCKLIST_NAME,
                "sha256_before": (
                    hashlib.sha256(source_bytes[BLOCKLIST_NAME]).hexdigest()
                    if BLOCKLIST_NAME in source_bytes
                    else None
                ),
                "sha256_after": hashlib.sha256(
                    (state.root / BLOCKLIST_NAME).read_bytes()
                ).hexdigest(),
            }
        )
    if after_hash is not None:
        written.append(
            {
                "path": BACKLOG_NAME,
                "sha256_before": hashlib.sha256(
                    source_bytes[BACKLOG_NAME]
                ).hexdigest(),
                "sha256_after": after_hash,
            }
        )

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "complete",
        "applied_at": moment,
        "decisions_file": decisions_file,
        "applied": [d["id"] for d in plan["decisions"] if d["rewrite"]],
        "already_applied": [d["id"] for d in plan["decisions"] if d["already_applied"]],
        "blocklist": blocklist_results,
        "files_written": written,
        "backups": backups,
        "backlog_sha256_before": hashlib.sha256(
            source_bytes[BACKLOG_NAME]
        ).hexdigest() if BACKLOG_NAME in source_bytes else None,
        "backlog_sha256_after": after_hash,
        "changed": bool(rewrite_by_id),
        "recovery": (
            "restore the exclusive backups listed above into the state "
            "root, then re-run apply; see OPERATIONS.md"
        ),
    }
    atomic_write_bytes(
        audit_dir / RECEIPT_FILENAME,
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n",
    )
    return receipt


# --------------------------------------------------------------------------
# Read-only listing and detail views
# --------------------------------------------------------------------------


def list_entries(
    state: StateRoot, status: str | None = None
) -> list[dict[str, Any]]:
    """List backlog entries (optionally filtered by status) with hashes."""
    if status is not None and status not in BACKLOG_STATUSES:
        raise ReviewError(
            f"unknown status filter {status!r}; valid: {list(BACKLOG_STATUSES)}"
        )
    raw = _read_raw_backlog(state)
    result: list[dict[str, Any]] = []
    for _, line, parsed in _indexed_entries(raw):
        if status is not None and parsed.get("status") != status:
            continue
        result.append(
            {
                "id": parsed.get("id"),
                "status": parsed.get("status"),
                "url": parsed.get("url"),
                "author": parsed.get("author"),
                "ingested_at": parsed.get("ingested_at"),
                "entry_sha256": entry_sha256(line),
            }
        )
    return result


def _resolve_artifacts_dir(state: StateRoot, artifacts: str) -> Path:
    candidate = Path(artifacts)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReviewError(
            f"artifacts path must be relative to the state root, got {artifacts!r}"
        )
    return state.root / candidate


def show_entry(state: StateRoot, video_id: str) -> dict[str, Any]:
    """One entry with claims, provenance and verification reasons."""
    raw = _read_raw_backlog(state)
    _, line, parsed = _find_entry(raw, video_id)
    document: dict[str, Any] = {
        "id": video_id,
        "entry_sha256": entry_sha256(line),
        "entry": parsed,
        "verification": None,
    }
    artifacts = parsed.get("artifacts")
    if not isinstance(artifacts, str) or not artifacts.strip():
        document["verification_note"] = "entry records no artifacts path"
        return document
    run_dir = _resolve_artifacts_dir(state, artifacts)
    verification_path = run_dir / "verification.json"
    if not verification_path.is_file():
        document["verification_note"] = (
            f"no verification.json under {artifacts!r}"
        )
        return document
    try:
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        document["verification_note"] = f"unreadable verification.json: {exc}"
        return document
    if not isinstance(verification, dict):
        document["verification_note"] = "verification.json is not an object"
        return document
    document["verification"] = {
        "rules_version": verification.get("rules_version"),
        "generated_at": verification.get("generated_at"),
        "claims": [
            {
                "claim": claim.get("claim"),
                "verdict": claim.get("verdict"),
                "reason": claim.get("reason"),
                "evidence": claim.get("evidence"),
                "note": claim.get("note"),
            }
            for claim in verification.get("claims", [])
            if isinstance(claim, dict)
        ],
    }
    return document
