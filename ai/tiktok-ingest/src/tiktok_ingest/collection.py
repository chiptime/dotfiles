"""Offline enumeration of ONE explicitly selected TikTok collection.

The browser is operator-driven and separately authorized; this module
never opens a browser, performs network I/O or fetches media — it
consumes page snapshots (HTML) captured by an authorized operator
session.

Honesty rules encoded here (MVP-PRD sections 3.A, 4 and 5):

- Container-scoped links only: recommendation links outside the
  declared container are counted (``out_of_container_count``) and NEVER
  become items.
- Declared count, observed count, end-of-list evidence, access markers
  and status are recorded separately and are never inferred from each
  other.
- Repeated-ID scroll caps, timeouts and an accidental
  ``declared == observed`` match never prove completeness; only
  recorded end-of-list evidence does. An honestly empty collection with
  end evidence is a valid complete empty result.
- "Collection isn't available" plus a "log in" marker is access-blocked
  evidence. It is NOT proof of deletion or privacy and is distinct from
  an honestly empty collection.
- A DOM change (container marker missing) fails explicitly with
  :class:`CollectionDOMError` instead of returning a fake empty list.
- State is MERGE-ONLY: a later partial observation never removes known
  items and never infers removals; ``first_seen_at`` is preserved.
- Enumeration never authorizes downloads or inference; a processing
  plan is a document and executes nothing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html.parser
import re
from pathlib import Path
from typing import Any

from .contracts import (
    ContractError,
    InventoryEntry,
    _optional_int,
    _optional_str,
    _require_dict,
    _require_iso,
    _require_one_of,
    _require_present,
    _require_str,
    to_jsonable,
    utc_now_iso,
)
from .state import (
    Blocklist,
    InventoryStore,
    Processed,
    StateRoot,
    atomic_write_json,
    read_json_file,
)


class CollectionError(RuntimeError):
    """Raised for collection inventory input, state or schema errors."""


class CollectionDOMError(CollectionError):
    """Raised when the captured page no longer matches the declared DOM.

    An absent container marker is an explicit failure, never a silent
    empty enumeration.
    """


# --------------------------------------------------------------------------
# Collection URL parsing
# --------------------------------------------------------------------------

_COLLECTION_URL_RE = re.compile(
    r"^https://www\.tiktok\.com/@(?P<author>[A-Za-z0-9_.]+)/(?P<kind>collection|playlist)/(?P<tail>[^/?#]+)(?:[/?#].*)?$"
)

_COLLECTION_KEY_RE = re.compile(r"^[0-9a-f]{16}$")

_ITEM_PATH_RE = re.compile(
    r"^(?:https://www\.tiktok\.com)?/@(?P<author>[A-Za-z0-9_.]+)/(?P<kind>video|photo)/(?P<item_id>\d+)(?:[/?#].*)?$"
)


@dataclasses.dataclass(frozen=True)
class CollectionRef:
    """Identity of one explicitly selected collection.

    ``collection_url`` is canonical (scheme + host + path, query and
    fragment stripped); ``collection_key`` is the first 16 hex chars of
    its sha256 and names the persisted scan state file.
    """

    collection_url: str
    author: str
    kind: str
    collection_key: str

    def __post_init__(self) -> None:
        _require_str(self.collection_url, "CollectionRef.collection_url")
        if not re.match(r"^[A-Za-z0-9_.]+$", self.author or ""):
            raise ContractError(
                "CollectionRef.author must match [A-Za-z0-9_.]+, "
                f"got {self.author!r}"
            )
        _require_one_of(self.kind, ("collection", "playlist"), "CollectionRef.kind")
        if not isinstance(self.collection_key, str) or not _COLLECTION_KEY_RE.match(
            self.collection_key
        ):
            raise ContractError(
                "CollectionRef.collection_key must be 16 lowercase hex chars, "
                f"got {self.collection_key!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "CollectionRef":
        record = dict(_require_dict(data, "CollectionRef"))
        _require_present(cls, record)
        return cls(**record)


def parse_collection_url(url: str) -> CollectionRef:
    """Parse ONE canonical TikTok collection/playlist URL.

    Only ``https://www.tiktok.com/@<author>/(collection|playlist)/<slug>``
    is accepted; anything else raises :class:`CollectionError` rather
    than guessing. Query and fragment are stripped from the canonical
    URL used for identity.
    """
    if not isinstance(url, str) or not url.strip():
        raise CollectionError("a TikTok collection URL is required")
    match = _COLLECTION_URL_RE.match(url.strip())
    if match is None:
        raise CollectionError(
            f"unsupported TikTok collection URL {url.strip()!r}: this input "
            "accepts only canonical https://www.tiktok.com/@<author>/"
            "collection/<slug> (or /playlist/<slug>) URLs"
        )
    canonical = (
        "https://www.tiktok.com/"
        f"@{match.group('author')}/{match.group('kind')}/{match.group('tail')}"
    )
    return CollectionRef(
        collection_url=canonical,
        author=match.group("author"),
        kind=match.group("kind"),
        collection_key=hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
    )


# --------------------------------------------------------------------------
# Page observation (pure HTML parsing over an operator-captured snapshot)
# --------------------------------------------------------------------------

DEFAULT_ACCESS_BLOCK_MARKERS: tuple[str, ...] = ("collection isn't available",)
LOGIN_EVIDENCE_MARKER = "log in"

_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


@dataclasses.dataclass(frozen=True)
class ObservedItem:
    """One container-scoped item link observed on a captured page."""

    stable_id: str
    kind: str
    canonical_url: str
    author: str
    raw_href: str

    def __post_init__(self) -> None:
        _require_str(self.stable_id, "ObservedItem.stable_id")
        _require_one_of(self.kind, ("video", "photo"), "ObservedItem.kind")
        _require_str(self.canonical_url, "ObservedItem.canonical_url")
        _require_str(self.author, "ObservedItem.author")
        _require_str(self.raw_href, "ObservedItem.raw_href")


@dataclasses.dataclass(frozen=True)
class PageObservation:
    """What ONE page snapshot honestly showed.

    ``items`` are deduplicated by stable ID in first-seen order.
    ``out_of_container_count`` counts item-shaped links found OUTSIDE
    the container — counted, never items. ``access_markers`` records
    matched access-block marker strings; they are access evidence and
    never imply deletion, privacy or emptiness.
    """

    items: tuple[ObservedItem, ...]
    out_of_container_count: int
    access_markers: tuple[str, ...]
    container_found: bool

    def __post_init__(self) -> None:
        if isinstance(self.items, (list, tuple)):
            object.__setattr__(self, "items", tuple(self.items))
        for item in self.items:
            if not isinstance(item, ObservedItem):
                raise ContractError("PageObservation.items must hold ObservedItem")
        if isinstance(self.out_of_container_count, bool) or not isinstance(
            self.out_of_container_count, int
        ):
            raise ContractError(
                "PageObservation.out_of_container_count must be an integer"
            )
        object.__setattr__(self, "access_markers", tuple(self.access_markers))


def _detect_access_markers(
    html: str, extra_blocked_markers: tuple[str, ...]
) -> tuple[str, ...]:
    """Case-insensitive substring scan of the raw page for access evidence.

    Detection is independent of the container scan: a blocked page is
    access evidence whether or not a container was found. When the
    default availability marker matches, the login marker is looked for
    too and the pair is recorded together.
    """
    lowered = html.lower()
    markers: list[str] = []
    for marker in (*DEFAULT_ACCESS_BLOCK_MARKERS, *extra_blocked_markers):
        if not isinstance(marker, str) or not marker.strip():
            raise CollectionError(
                "access-block markers must be non-empty strings, "
                f"got {marker!r}"
            )
        needle = marker.lower()
        if needle in lowered and not any(m.lower() == needle for m in markers):
            markers.append(marker)
    default_matched = any(
        m.lower() == DEFAULT_ACCESS_BLOCK_MARKERS[0] for m in markers
    )
    if default_matched and LOGIN_EVIDENCE_MARKER in lowered:
        if not any(
            m.lower() == LOGIN_EVIDENCE_MARKER for m in markers
        ):
            markers.append(LOGIN_EVIDENCE_MARKER)
    return tuple(markers)


class _CollectionPageParser(html.parser.HTMLParser):
    """Container-scoped link collector over one captured page.

    A start tag whose attribute ``container_attr`` equals
    ``container_value`` exactly opens the container; links are
    in-container until that tag closes (tracked by tag name + stack
    depth). Item-shaped links outside the container are only counted.
    """

    def __init__(self, container_attr: str, container_value: str) -> None:
        super().__init__(convert_charrefs=True)
        self._container_attr = container_attr.lower()
        self._container_value = container_value
        self._stack: list[str] = []
        self._container_tag: str | None = None
        self._container_depth: int | None = None
        self.container_found = False
        self.items: list[ObservedItem] = []
        self._seen_ids: set[str] = set()
        self.out_of_container_count = 0

    @property
    def in_container(self) -> bool:
        return self._container_tag is not None

    def _is_container_start(self, attrs: list[tuple[str, str | None]]) -> bool:
        return any(
            (key or "").lower() == self._container_attr
            and attr_value == self._container_value
            for key, attr_value in attrs
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if not self.container_found and self._is_container_start(attrs):
            self.container_found = True
            self._container_tag = lowered
            self._stack.append(lowered)
            self._container_depth = len(self._stack)
            return
        if lowered not in _VOID_ELEMENTS:
            self._stack.append(lowered)
        if lowered == "a":
            self._handle_anchor(attrs, in_container=self.in_container)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _VOID_ELEMENTS:
            return
        if lowered in self._stack:
            while self._stack:
                popped = self._stack.pop()
                if popped == lowered:
                    break
        if (
            self._container_tag is not None
            and self._container_depth is not None
            and len(self._stack) < self._container_depth
        ):
            self._container_tag = None
            self._container_depth = None

    def _handle_anchor(
        self, attrs: list[tuple[str, str | None]], *, in_container: bool
    ) -> None:
        href: str | None = None
        for key, value in attrs:
            if (key or "").lower() == "href":
                href = value
                break
        if not href:
            return
        match = _ITEM_PATH_RE.match(href.strip())
        if match is None:
            return
        item = ObservedItem(
            stable_id=match.group("item_id"),
            kind=match.group("kind"),
            canonical_url=(
                f"https://www.tiktok.com/@{match.group('author')}/"
                f"{match.group('kind')}/{match.group('item_id')}"
            ),
            author=match.group("author"),
            raw_href=href.strip(),
        )
        if in_container:
            if item.stable_id not in self._seen_ids:
                self._seen_ids.add(item.stable_id)
                self.items.append(item)
        else:
            self.out_of_container_count += 1


def extract_page_items(
    html: str,
    container_attr: str,
    container_value: str,
    *,
    extra_blocked_markers: tuple[str, ...] = (),
) -> PageObservation:
    """Enumerate container-scoped items from ONE captured page snapshot.

    Pure stdlib HTML parsing: no network, no browser, no media. A
    missing container marker raises :class:`CollectionDOMError` instead
    of a fake empty list; parser failures raise it too.
    """
    _require_str(container_attr, "container_attr")
    _require_str(container_value, "container_value")
    if not isinstance(html, str) or not html:
        raise CollectionDOMError(
            "page snapshot HTML is empty; refusing to invent an empty "
            "observation for a page that was not honestly read"
        )
    access_markers = _detect_access_markers(html, tuple(extra_blocked_markers))
    parser = _CollectionPageParser(container_attr, container_value)
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # html.parser failure: fail explicitly
        raise CollectionDOMError(
            f"page snapshot HTML could not be parsed: {exc}"
        ) from exc
    if not parser.container_found:
        raise CollectionDOMError(
            f"container marker {container_attr}={container_value!r} was not "
            "found in the page snapshot; the DOM appears to have changed — "
            "failing explicitly instead of returning a fake empty list"
        )
    return PageObservation(
        items=tuple(parser.items),
        out_of_container_count=parser.out_of_container_count,
        access_markers=access_markers,
        container_found=True,
    )


# --------------------------------------------------------------------------
# Honest scan status
# --------------------------------------------------------------------------

SCAN_SCHEMA_VERSION = 1

SCAN_STATUSES: tuple[str, ...] = ("in_progress", "complete", "partial", "blocked")


def compute_scan_status(
    *,
    observed_count: int,
    declared_count: int | None,
    end_evidence: str | None,
    stop_reason: str | None,
    access_markers: tuple[str, ...],
    had_items: bool,
) -> tuple[str, str]:
    """Recompute the honest scan status from SEPARATE evidence streams.

    Returns ``(status, reason)``. Access evidence dominates; ``complete``
    requires recorded end-of-list evidence with a reconciled (or absent)
    declared count; counts matching by accident without end evidence are
    never complete; a stopped scroll, timeout or repeated-ID cap is
    partial, never completeness proof.
    """
    if isinstance(observed_count, bool) or not isinstance(observed_count, int):
        raise ContractError("observed_count must be an integer")
    if observed_count < 0:
        raise ContractError("observed_count must be non-negative")
    if declared_count is not None:
        if isinstance(declared_count, bool) or not isinstance(declared_count, int):
            raise ContractError("declared_count must be an integer or null")
    _optional_str(end_evidence, "end_evidence")
    _optional_str(stop_reason, "stop_reason")
    markers = tuple(access_markers)

    if markers:
        return "blocked", (
            "blocked: page carried access-block evidence ("
            + ", ".join(repr(marker) for marker in markers)
            + "); these markers are ACCESS evidence only: they do NOT prove "
            "deletion or privacy and are distinct from an honestly empty "
            "collection"
        )

    if end_evidence is not None and (
        declared_count is None or observed_count == declared_count
    ):
        if declared_count is None:
            reason = (
                f"complete: end-of-list evidence present "
                f"({end_evidence!r}) with no declared count recorded; "
                f"observed {observed_count} items"
            )
        else:
            reason = (
                f"complete: end-of-list evidence present "
                f"({end_evidence!r}) and observed {observed_count} of "
                f"declared {declared_count} items"
            )
        if observed_count == 0 or not had_items:
            reason += (
                "; an honestly empty collection with end evidence is a "
                "complete empty result, not a failure"
            )
        return "complete", reason

    if end_evidence is not None:
        return "partial", (
            f"partial: end-of-list evidence present ({end_evidence!r}) but "
            f"observed {observed_count} does not reconcile with declared "
            f"{declared_count}; the count mismatch stays unresolved in "
            "either direction"
        )

    if stop_reason is not None:
        return "partial", (
            f"partial: scroll stopped without end-of-list evidence (stop "
            f"reason: {stop_reason!r}); a stopped scroll, timeout or "
            "repeated-ID cap does not prove completeness"
        )

    reason = (
        f"in_progress: no end-of-list evidence recorded; enumeration may "
        f"continue (observed {observed_count} items so far)"
    )
    if observed_count == 0 or not had_items:
        reason += (
            "; an incomplete page load cannot be ruled out with zero "
            "items observed"
        )
    return "in_progress", reason


# --------------------------------------------------------------------------
# Persisted scan state
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CollectionItemRecord:
    """Merged knowledge about one stable ID, accumulated merge-only."""

    stable_id: str
    kind: str
    canonical_url: str
    author: str | None
    first_seen_at: str
    last_seen_at: str
    raw_hrefs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_str(self.stable_id, "CollectionItemRecord.stable_id")
        _require_one_of(self.kind, ("video", "photo"), "CollectionItemRecord.kind")
        _require_str(self.canonical_url, "CollectionItemRecord.canonical_url")
        _optional_str(self.author, "CollectionItemRecord.author")
        _require_iso(self.first_seen_at, "CollectionItemRecord.first_seen_at")
        _require_iso(self.last_seen_at, "CollectionItemRecord.last_seen_at")
        object.__setattr__(
            self, "raw_hrefs", tuple(str(href) for href in self.raw_hrefs)
        )
        for href in self.raw_hrefs:
            _require_str(href, "CollectionItemRecord.raw_hrefs")
        if len(set(self.raw_hrefs)) != len(self.raw_hrefs):
            raise ContractError("CollectionItemRecord.raw_hrefs must be unique")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "CollectionItemRecord":
        record = dict(_require_dict(data, "CollectionItemRecord"))
        _require_present(cls, record)
        record["raw_hrefs"] = tuple(record.get("raw_hrefs") or ())
        return cls(**record)


@dataclasses.dataclass(frozen=True)
class CaptureRecord:
    """What one page-merge captured, kept as an append-only history."""

    captured_at: str
    container_attr: str
    container_value: str
    declared_count: int | None
    end_evidence: str | None
    stop_reason: str | None
    access_markers: tuple[str, ...]
    new_ids: tuple[str, ...]
    observed_total: int
    out_of_container_count: int
    status_after: str

    def __post_init__(self) -> None:
        _require_iso(self.captured_at, "CaptureRecord.captured_at")
        _require_str(self.container_attr, "CaptureRecord.container_attr")
        _require_str(self.container_value, "CaptureRecord.container_value")
        object.__setattr__(
            self,
            "declared_count",
            _optional_int(self.declared_count, "CaptureRecord.declared_count"),
        )
        _optional_str(self.end_evidence, "CaptureRecord.end_evidence")
        _optional_str(self.stop_reason, "CaptureRecord.stop_reason")
        object.__setattr__(
            self, "access_markers", tuple(self.access_markers)
        )
        object.__setattr__(self, "new_ids", tuple(self.new_ids))
        if isinstance(self.observed_total, bool) or not isinstance(
            self.observed_total, int
        ):
            raise ContractError("CaptureRecord.observed_total must be an integer")
        if isinstance(self.out_of_container_count, bool) or not isinstance(
            self.out_of_container_count, int
        ):
            raise ContractError(
                "CaptureRecord.out_of_container_count must be an integer"
            )
        _require_one_of(
            self.status_after, SCAN_STATUSES, "CaptureRecord.status_after"
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "CaptureRecord":
        record = dict(_require_dict(data, "CaptureRecord"))
        _require_present(cls, record)
        record["access_markers"] = tuple(record.get("access_markers") or ())
        record["new_ids"] = tuple(record.get("new_ids") or ())
        return cls(**record)


@dataclasses.dataclass(frozen=True)
class CollectionScanState:
    """Accumulated, merge-only scan state for one collection.

    Items are sorted by stable ID; captures are append-ordered.
    ``declared_count`` is the latest non-null declared count,
    ``end_of_list_evidence`` is sticky once set and ``stop_reason`` is
    the latest non-null stop reason. Status is always recomputed from
    the ACCUMULATED evidence, never asserted.
    """

    schema_version: int
    collection: CollectionRef
    declared_count: int | None
    end_of_list_evidence: str | None
    stop_reason: str | None
    status: str
    status_reason: str
    items: tuple[CollectionItemRecord, ...]
    captures: tuple[CaptureRecord, ...]
    started_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise ContractError(
                "CollectionScanState.schema_version must be an integer"
            )
        if self.schema_version != SCAN_SCHEMA_VERSION:
            raise CollectionError(
                f"collection scan schema version {self.schema_version!r} is "
                f"not supported (expected {SCAN_SCHEMA_VERSION})"
            )
        if not isinstance(self.collection, CollectionRef):
            object.__setattr__(
                self, "collection", CollectionRef.from_dict(self.collection)
            )
        object.__setattr__(
            self,
            "declared_count",
            _optional_int(
                self.declared_count, "CollectionScanState.declared_count"
            ),
        )
        _optional_str(
            self.end_of_list_evidence, "CollectionScanState.end_of_list_evidence"
        )
        _optional_str(self.stop_reason, "CollectionScanState.stop_reason")
        _require_one_of(self.status, SCAN_STATUSES, "CollectionScanState.status")
        _require_str(self.status_reason, "CollectionScanState.status_reason")
        if not isinstance(self.items, (list, tuple)):
            raise ContractError("CollectionScanState.items must be a list")
        items = tuple(
            item if isinstance(item, CollectionItemRecord)
            else CollectionItemRecord.from_dict(item)
            for item in self.items
        )
        object.__setattr__(
            self, "items", tuple(sorted(items, key=lambda record: record.stable_id))
        )
        if not isinstance(self.captures, (list, tuple)):
            raise ContractError("CollectionScanState.captures must be a list")
        object.__setattr__(
            self,
            "captures",
            tuple(
                capture if isinstance(capture, CaptureRecord)
                else CaptureRecord.from_dict(capture)
                for capture in self.captures
            ),
        )
        _require_iso(self.started_at, "CollectionScanState.started_at")
        _require_iso(self.updated_at, "CollectionScanState.updated_at")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "CollectionScanState":
        record = dict(_require_dict(data, "CollectionScanState"))
        _require_present(cls, record)
        record["items"] = list(record.get("items") or ())
        record["captures"] = list(record.get("captures") or ())
        return cls(**record)


def merge_observation(
    existing: CollectionScanState | None,
    ref: CollectionRef,
    observation: PageObservation,
    *,
    declared_count: int | None,
    end_evidence: str | None,
    stop_reason: str | None,
    captured_at: str,
    container_attr: str,
    container_value: str,
) -> CollectionScanState:
    """Merge ONE page observation into the scan state (MERGE-ONLY).

    Items only ever accumulate: union by stable ID, keeping the original
    ``first_seen_at`` and kind on conflict, appending new raw hrefs and
    filling a missing author. A later partial observation never removes
    known items and never infers removals. Status is recomputed from the
    ACCUMULATED evidence via :func:`compute_scan_status`.
    """
    _require_iso(captured_at, "captured_at")
    _require_str(container_attr, "container_attr")
    _require_str(container_value, "container_value")
    if not isinstance(ref, CollectionRef):
        raise ContractError("ref must be a CollectionRef")
    if not isinstance(observation, PageObservation):
        raise ContractError("observation must be a PageObservation")
    if (
        existing is not None
        and existing.collection.collection_key != ref.collection_key
    ):
        raise CollectionError(
            "scan state belongs to collection "
            f"{existing.collection.collection_url!r} but the observation is "
            f"for {ref.collection_url!r}; refusing to merge across collections"
        )

    prior_ids: set[str] = set()
    if existing is not None:
        prior_ids = {record.stable_id for record in existing.items}
    new_ids = tuple(
        item.stable_id
        for item in observation.items
        if item.stable_id not in prior_ids
    )

    merged: dict[str, CollectionItemRecord] = {}
    for record in existing.items if existing is not None else ():
        merged[record.stable_id] = record
    for item in observation.items:
        current = merged.get(item.stable_id)
        if current is None:
            merged[item.stable_id] = CollectionItemRecord(
                stable_id=item.stable_id,
                kind=item.kind,
                canonical_url=item.canonical_url,
                author=item.author,
                first_seen_at=captured_at,
                last_seen_at=captured_at,
                raw_hrefs=(item.raw_href,),
            )
            continue
        hrefs = list(current.raw_hrefs)
        if item.raw_href not in hrefs:
            hrefs.append(item.raw_href)
        merged[item.stable_id] = CollectionItemRecord(
            stable_id=current.stable_id,
            kind=current.kind,  # original kind is kept on conflict
            canonical_url=current.canonical_url,
            author=current.author if current.author is not None else item.author,
            first_seen_at=current.first_seen_at,
            last_seen_at=captured_at,
            raw_hrefs=tuple(hrefs),
        )

    accumulated_declared = existing.declared_count if existing is not None else None
    if declared_count is not None:
        if isinstance(declared_count, bool) or not isinstance(declared_count, int):
            raise ContractError("declared_count must be an integer or null")
        accumulated_declared = declared_count  # latest non-null wins
    accumulated_end = (
        existing.end_of_list_evidence if existing is not None else None
    )
    if end_evidence is not None:
        _require_str(end_evidence, "end_evidence")
        accumulated_end = (
            accumulated_end if accumulated_end is not None else end_evidence
        )  # sticky once set
    accumulated_stop = existing.stop_reason if existing is not None else None
    if stop_reason is not None:
        _require_str(stop_reason, "stop_reason")
        accumulated_stop = stop_reason  # latest non-null wins

    accumulated_markers: list[str] = []
    for capture in existing.captures if existing is not None else ():
        for marker in capture.access_markers:
            if marker not in accumulated_markers:
                accumulated_markers.append(marker)
    for marker in observation.access_markers:
        if marker not in accumulated_markers:
            accumulated_markers.append(marker)

    status, status_reason = compute_scan_status(
        observed_count=len(merged),
        declared_count=accumulated_declared,
        end_evidence=accumulated_end,
        stop_reason=accumulated_stop,
        access_markers=tuple(accumulated_markers),
        had_items=bool(merged),
    )

    capture = CaptureRecord(
        captured_at=captured_at,
        container_attr=container_attr,
        container_value=container_value,
        declared_count=declared_count,
        end_evidence=end_evidence,
        stop_reason=stop_reason,
        access_markers=observation.access_markers,
        new_ids=new_ids,
        observed_total=len(merged),
        out_of_container_count=observation.out_of_container_count,
        status_after=status,
    )
    captures = tuple(
        (existing.captures if existing is not None else ()) + (capture,)
    )
    return CollectionScanState(
        schema_version=SCAN_SCHEMA_VERSION,
        collection=ref,
        declared_count=accumulated_declared,
        end_of_list_evidence=accumulated_end,
        stop_reason=accumulated_stop,
        status=status,
        status_reason=status_reason,
        items=tuple(sorted(merged.values(), key=lambda record: record.stable_id)),
        captures=captures,
        started_at=existing.started_at if existing is not None else captured_at,
        updated_at=captured_at,
    )


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class CollectionScanStore:
    """Atomically persisted scan states under ``<state-root>/collections/``."""

    _KEY_RE = _COLLECTION_KEY_RE

    def __init__(self, state: StateRoot) -> None:
        self.state = state

    def path_for(self, key: str) -> Path:
        if not isinstance(key, str) or not self._KEY_RE.match(key):
            raise CollectionError(
                f"collection key must be 16 lowercase hex chars, got {key!r}"
            )
        return self.state.collections_dir / f"{key}.json"

    def load(self, key: str) -> CollectionScanState | None:
        """Load one scan state; missing file is ``None``, malformed fails."""
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            document = read_json_file(path)
        except Exception as exc:
            raise CollectionError(
                f"collection scan state {key!r} is not valid JSON: {exc}"
            ) from exc
        if document is None:  # raced with a removal; treat as absent
            return None
        if not isinstance(document, dict) or "scan" not in document:
            raise CollectionError(
                f"collection scan state {key!r} has an unexpected structure"
            )
        try:
            return CollectionScanState.from_dict(document["scan"])
        except ContractError as exc:
            raise CollectionError(
                f"collection scan state {key!r} is malformed: {exc}"
            ) from exc

    def save(self, scan: CollectionScanState) -> None:
        if not isinstance(scan, CollectionScanState):
            raise ContractError("save expects a CollectionScanState")
        path = self.path_for(scan.collection.collection_key)
        atomic_write_json(
            path, {"version": SCAN_SCHEMA_VERSION, "scan": scan.to_dict()}
        )

    def keys(self) -> list[str]:
        directory = self.state.collections_dir
        if not directory.exists():
            return []
        return sorted(
            path.stem
            for path in directory.glob("*.json")
            if self._KEY_RE.match(path.stem)
        )


def resolve_collection(
    state: StateRoot, arg: str
) -> tuple[CollectionRef, CollectionScanState | None]:
    """Resolve a CLI argument to a collection ref and its scan state.

    A 16-hex argument names a persisted scan key (unknown keys fail);
    anything else is parsed as a collection URL (scan state may be
    ``None`` when nothing has been captured yet).
    """
    if not isinstance(arg, str) or not arg.strip():
        raise CollectionError("a collection URL or 16-hex collection key is required")
    text = arg.strip()
    if _COLLECTION_KEY_RE.match(text):
        scan = CollectionScanStore(state).load(text)
        if scan is None:
            raise CollectionError(
                f"unknown collection key {text!r}: no persisted scan state"
            )
        return scan.collection, scan
    ref = parse_collection_url(text)
    return ref, CollectionScanStore(state).load(ref.collection_key)


# --------------------------------------------------------------------------
# Inventory sync (reuses the existing InventoryEntry contract)
# --------------------------------------------------------------------------

_STATUS_TO_COMPLETENESS = {
    "complete": "complete",
    "partial": "incomplete",
    "in_progress": "unknown",
    "blocked": "unknown",
}


def sync_inventory_from_scan(
    state: StateRoot, scan: CollectionScanState
) -> tuple[int, int]:
    """Upsert scan items into the existing inventory store.

    Existing entries only GAIN the collection association; their input
    origin, completeness, discovery time and counts are never touched.
    New entries are created with ``input_origin="collection"`` and a
    completeness mapped from the scan status. Returns ``(added, updated)``.
    """
    if not isinstance(scan, CollectionScanState):
        raise ContractError("sync expects a CollectionScanState")
    store = InventoryStore(state)
    entries = store.load()
    index = {(entry.source, entry.stable_id): position for position, entry in enumerate(entries)}
    collection_url = scan.collection.collection_url
    completeness = _STATUS_TO_COMPLETENESS[scan.status]
    added = 0
    updated = 0
    for item in scan.items:
        key = ("tiktok", item.stable_id)
        position = index.get(key)
        if position is not None:
            entry = entries[position]
            if collection_url in entry.collections:
                continue
            entries[position] = InventoryEntry(
                source=entry.source,
                stable_id=entry.stable_id,
                canonical_url=entry.canonical_url,
                input_origin=entry.input_origin,
                discovered_at=entry.discovered_at,
                collections=entry.collections + (collection_url,),
                declared_count=entry.declared_count,
                observed_count=entry.observed_count,
                completeness=entry.completeness,
                caption=entry.caption,
                author=entry.author,
                is_photo_post=entry.is_photo_post,
            )
            updated += 1
            continue
        entries.append(
            InventoryEntry(
                source="tiktok",
                stable_id=item.stable_id,
                canonical_url=item.canonical_url,
                input_origin="collection",
                discovered_at=item.first_seen_at,
                collections=(collection_url,),
                declared_count=scan.declared_count,
                observed_count=len(scan.items),
                completeness=completeness,
                author=item.author,
                is_photo_post=(item.kind == "photo"),
            )
        )
        added += 1
    store.save(entries)
    return added, updated


# --------------------------------------------------------------------------
# Processing plan (document only — executes nothing)
# --------------------------------------------------------------------------

PLAN_CLASSIFICATIONS: tuple[str, ...] = (
    "new_processable",
    "processed_complete",
    "partial_resumable",
    "rejected",
    "unsupported_photo",
)


@dataclasses.dataclass(frozen=True)
class PlanItem:
    """One observed item with its document-only classification."""

    stable_id: str
    kind: str
    canonical_url: str
    classification: str
    detail: str

    def __post_init__(self) -> None:
        _require_str(self.stable_id, "PlanItem.stable_id")
        _require_one_of(self.kind, ("video", "photo"), "PlanItem.kind")
        _require_str(self.canonical_url, "PlanItem.canonical_url")
        _require_one_of(
            self.classification, PLAN_CLASSIFICATIONS, "PlanItem.classification"
        )
        _require_str(self.detail, "PlanItem.detail")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "PlanItem":
        record = dict(_require_dict(data, "PlanItem"))
        _require_present(cls, record)
        return cls(**record)


@dataclasses.dataclass(frozen=True)
class CollectionPlan:
    """The processing plan document for one collection scan.

    This is an inspection document: it classifies observed items against
    the EXISTING blocklist and processed stores and executes nothing.
    "Observed" is never treated as "processed".
    """

    collection: CollectionRef
    scan_status: str
    scan_status_reason: str
    declared_count: int | None
    observed_count: int
    counts: dict[str, int]
    items: tuple[PlanItem, ...]
    generated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.collection, CollectionRef):
            object.__setattr__(
                self, "collection", CollectionRef.from_dict(self.collection)
            )
        _require_one_of(self.scan_status, SCAN_STATUSES, "CollectionPlan.scan_status")
        _require_str(
            self.scan_status_reason, "CollectionPlan.scan_status_reason"
        )
        object.__setattr__(
            self,
            "declared_count",
            _optional_int(self.declared_count, "CollectionPlan.declared_count"),
        )
        if isinstance(self.observed_count, bool) or not isinstance(
            self.observed_count, int
        ):
            raise ContractError("CollectionPlan.observed_count must be an integer")
        if not isinstance(self.counts, dict):
            raise ContractError("CollectionPlan.counts must be an object")
        if not isinstance(self.items, (list, tuple)):
            raise ContractError("CollectionPlan.items must be a list")
        object.__setattr__(self, "items", tuple(self.items))
        _require_iso(self.generated_at, "CollectionPlan.generated_at")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Any) -> "CollectionPlan":
        record = dict(_require_dict(data, "CollectionPlan"))
        _require_present(cls, record)
        record["items"] = list(record.get("items") or ())
        return cls(**record)


def build_collection_plan(
    state: StateRoot, scan: CollectionScanState
) -> CollectionPlan:
    """Classify each observed item against existing durable state.

    First match wins per item: permanent blocklist rejection, processed
    with an emit fingerprint (complete), processed without one
    (partial-resumable), photo posts (unsupported in this MVP), then new
    processable. Uses the EXISTING Blocklist and Processed stores ONLY —
    no second dedup system, and observation is never treated as
    processing.
    """
    if not isinstance(scan, CollectionScanState):
        raise ContractError("plan expects a CollectionScanState")
    blocklist = Blocklist(state)
    processed_store = Processed(state)
    counts: dict[str, int] = {name: 0 for name in PLAN_CLASSIFICATIONS}
    plan_items: list[PlanItem] = []
    for item in scan.items:
        if blocklist.is_blocked(item.stable_id):
            classification, detail = (
                "rejected",
                "permanently rejected by the operator blocklist; no flag can override",
            )
        else:
            processed = processed_store.get(item.stable_id)
            if processed is not None and "emit" in processed.stage_fingerprints:
                classification, detail = (
                    "processed_complete",
                    "emit fingerprint present; an unchanged rerun performs zero re-inference",
                )
            elif processed is not None:
                classification, detail = (
                    "partial_resumable",
                    "processed entry without an emit fingerprint; valid completed stages may resume",
                )
            elif item.kind == "photo":
                classification, detail = (
                    "unsupported_photo",
                    "photo posts are reported, not ingested in this MVP",
                )
            else:
                classification, detail = (
                    "new_processable",
                    "no processed or blocklist record; eligible for a separately authorized run",
                )
        counts[classification] += 1
        plan_items.append(
            PlanItem(
                stable_id=item.stable_id,
                kind=item.kind,
                canonical_url=item.canonical_url,
                classification=classification,
                detail=detail,
            )
        )
    return CollectionPlan(
        collection=scan.collection,
        scan_status=scan.status,
        scan_status_reason=scan.status_reason,
        declared_count=scan.declared_count,
        observed_count=len(scan.items),
        counts=counts,
        items=tuple(plan_items),
        generated_at=utc_now_iso(),
    )
