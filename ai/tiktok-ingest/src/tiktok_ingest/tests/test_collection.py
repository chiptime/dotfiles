"""Offline collection-inventory tests: honest enumeration, merge-only state.

Every fixture is sanitized inline HTML with clearly fake authors and IDs
(``@fixture_author``, 19-digit synthetic IDs). No network, no browser, no
media downloads, no inference (MVP-PRD sections 3.A and 5): every state
root is a scratch temp directory.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Sequence
from unittest import mock

from tiktok_ingest.cli import main
from tiktok_ingest.collection import (
    DEFAULT_ACCESS_BLOCK_MARKERS,
    CollectionDOMError,
    CollectionError,
    CollectionScanState,
    CollectionScanStore,
    PLAN_CLASSIFICATIONS,
    SCAN_STATUSES,
    build_collection_plan,
    compute_scan_status,
    extract_page_items,
    merge_observation,
    parse_collection_url,
    resolve_collection,
    sync_inventory_from_scan,
)
from tiktok_ingest.contracts import ContractError, InventoryEntry, ProcessedEntry
from tiktok_ingest.state import (
    Blocklist,
    BlocklistEntry,
    InventoryStore,
    Processed,
    StateRoot,
)
from tiktok_ingest.tests.fixtures import FIXED_NOW, make_state_root

CONTAINER_ATTR = "data-testid"
CONTAINER_VALUE = "collection-container"
FAKE_AUTHOR = "fixture_author"

COLLECTION_URL = "https://www.tiktok.com/@fixture_author/collection/fixture-col"
REF = parse_collection_url(COLLECTION_URL)

T1 = FIXED_NOW
T2 = "2026-09-18T12:01:00Z"
T3 = "2026-09-18T12:02:00Z"

ACCESS_TEXT = "Collection isn't available. Log in to continue."


def fake_id(n: int) -> str:
    """A clearly fake, distinct, numeric 19-digit stable ID."""
    return str(1_000_000_000_000_000_000 + n)


def item_href(
    n: int,
    *,
    kind: str = "video",
    author: str = FAKE_AUTHOR,
    absolute: bool = False,
    query: str = "",
) -> str:
    path = f"/@{author}/{kind}/{fake_id(n)}{query}"
    return f"https://www.tiktok.com{path}" if absolute else path


def _anchor(href: str) -> str:
    return f'<a href="{href}">fixture item</a>'


def collection_page(
    *links: str,
    outside: Sequence[str] = (),
    access_text: str | None = None,
) -> str:
    """Wrap item links (plus optional outside links / access text) in the
    declared container element."""
    inner = "".join(_anchor(link) for link in links)
    outside_html = "".join(_anchor(link) for link in outside)
    marker = f"<p>{access_text}</p>" if access_text else ""
    return (
        "<!DOCTYPE html><html><body>"
        f"{outside_html}"
        f'<div {CONTAINER_ATTR}="{CONTAINER_VALUE}">{inner}</div>'
        f"{marker}"
        "</body></html>"
    )


def make_observation(
    *,
    video_ids: Sequence[int] = (),
    photo_ids: Sequence[int] = (),
    outside_ids: Sequence[int] = (),
    access_text: str | None = None,
    extra_markers: Sequence[str] = (),
):
    links = [item_href(n) for n in video_ids]
    links += [item_href(n, kind="photo") for n in photo_ids]
    outside = [
        item_href(n, absolute=True, query="?is_from_webapp=1&send_by_post=1")
        for n in outside_ids
    ]
    return extract_page_items(
        collection_page(*links, outside=outside, access_text=access_text),
        CONTAINER_ATTR,
        CONTAINER_VALUE,
        extra_blocked_markers=tuple(extra_markers),
    )


def merge_page(
    existing: CollectionScanState | None,
    *,
    video_ids: Sequence[int] = (),
    photo_ids: Sequence[int] = (),
    outside_ids: Sequence[int] = (),
    declared: int | None = None,
    end_evidence: str | None = None,
    stop_reason: str | None = None,
    captured_at: str = FIXED_NOW,
    access_text: str | None = None,
    extra_markers: Sequence[str] = (),
) -> CollectionScanState:
    return merge_observation(
        existing,
        REF,
        make_observation(
            video_ids=video_ids,
            photo_ids=photo_ids,
            outside_ids=outside_ids,
            access_text=access_text,
            extra_markers=extra_markers,
        ),
        declared_count=declared,
        end_evidence=end_evidence,
        stop_reason=stop_reason,
        captured_at=captured_at,
        container_attr=CONTAINER_ATTR,
        container_value=CONTAINER_VALUE,
    )


# --------------------------------------------------------------------------
# 1) Collection URL parsing
# --------------------------------------------------------------------------


class CollectionUrlParsingTests(unittest.TestCase):
    def test_parses_collection_url_and_strips_query(self) -> None:
        ref = parse_collection_url(COLLECTION_URL + "?lang=en&foo=bar")
        self.assertEqual(ref.collection_url, COLLECTION_URL)
        self.assertEqual(ref.author, FAKE_AUTHOR)
        self.assertEqual(ref.kind, "collection")
        expected_key = hashlib.sha256(COLLECTION_URL.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(ref.collection_key, expected_key)
        self.assertRegex(ref.collection_key, r"^[0-9a-f]{16}$")

    def test_parses_playlist_kind(self) -> None:
        ref = parse_collection_url(
            "https://www.tiktok.com/@fixture_author/playlist/fixture-pl"
        )
        self.assertEqual(ref.kind, "playlist")
        self.assertEqual(ref.author, FAKE_AUTHOR)

    def test_rejects_non_collection_urls(self) -> None:
        for url in (
            "",
            "   ",
            "https://www.tiktok.com/@fixture_author/video/123",
            "https://vm.tiktok.com/abcdef/",
            "https://www.tiktok.com/@fixture_author/collection",
            "https://example.com/collection/x",
            "not-a-url",
        ):
            with self.assertRaises(CollectionError):
                parse_collection_url(url)


# --------------------------------------------------------------------------
# 2) Page observation (pure HTML parsing)
# --------------------------------------------------------------------------


class ExtractPageItemsTests(unittest.TestCase):
    def test_container_scoped_items_deduped_first_seen(self) -> None:
        page = collection_page(
            item_href(1),
            item_href(2),
            item_href(1),  # duplicate across the same page
            item_href(3, absolute=True),  # absolute form also accepted
        )
        observation = extract_page_items(page, CONTAINER_ATTR, CONTAINER_VALUE)
        self.assertTrue(observation.container_found)
        self.assertEqual(
            [item.stable_id for item in observation.items],
            [fake_id(1), fake_id(2), fake_id(3)],
        )
        self.assertEqual(observation.out_of_container_count, 0)
        self.assertEqual(observation.access_markers, ())
        first = observation.items[0]
        self.assertEqual(first.kind, "video")
        self.assertEqual(first.author, FAKE_AUTHOR)
        self.assertEqual(
            first.canonical_url,
            f"https://www.tiktok.com/@{FAKE_AUTHOR}/video/{fake_id(1)}",
        )

    def test_outside_container_recommendations_counted_never_items(self) -> None:
        page = collection_page(
            item_href(1),
            outside=[
                item_href(2, absolute=True, query="?is_from_webapp=1"),
                item_href(3),
            ],
        )
        observation = extract_page_items(page, CONTAINER_ATTR, CONTAINER_VALUE)
        self.assertEqual(
            [item.stable_id for item in observation.items], [fake_id(1)]
        )
        self.assertEqual(observation.out_of_container_count, 2)

    def test_photo_kind_is_preserved(self) -> None:
        observation = make_observation(photo_ids=(7,))
        self.assertEqual(len(observation.items), 1)
        self.assertEqual(observation.items[0].kind, "photo")
        self.assertIn("/photo/", observation.items[0].canonical_url)

    def test_access_markers_are_recorded_as_evidence(self) -> None:
        observation = make_observation(access_text=ACCESS_TEXT)
        self.assertEqual(observation.items, ())  # nothing is claimed as items
        lowered = [marker.lower() for marker in observation.access_markers]
        self.assertIn(DEFAULT_ACCESS_BLOCK_MARKERS[0], lowered)
        self.assertIn("log in", lowered)

    def test_extra_blocked_markers_are_scanned(self) -> None:
        page = collection_page(
            item_href(1),
            access_text="Please verify to continue.",
        )
        observation = extract_page_items(
            page,
            CONTAINER_ATTR,
            CONTAINER_VALUE,
            extra_blocked_markers=("verify to continue",),
        )
        self.assertEqual(len(observation.items), 1)
        self.assertEqual(observation.access_markers, ("verify to continue",))


# --------------------------------------------------------------------------
# 3) Honest scan status
# --------------------------------------------------------------------------


class ComputeScanStatusTests(unittest.TestCase):
    def test_end_evidence_with_matching_declared_is_complete(self) -> None:
        status, reason = compute_scan_status(
            observed_count=3,
            declared_count=3,
            end_evidence="footer reached",
            stop_reason=None,
            access_markers=(),
            had_items=True,
        )
        self.assertEqual(status, "complete")
        self.assertIn("footer reached", reason)

        status, _ = compute_scan_status(
            observed_count=3,
            declared_count=None,
            end_evidence="footer reached",
            stop_reason=None,
            access_markers=(),
            had_items=True,
        )
        self.assertEqual(status, "complete")

    def test_empty_collection_with_end_evidence_is_complete(self) -> None:
        status, reason = compute_scan_status(
            observed_count=0,
            declared_count=0,
            end_evidence="no items listed",
            stop_reason=None,
            access_markers=(),
            had_items=False,
        )
        self.assertEqual(status, "complete")
        self.assertIn("empty", reason.lower())

    def test_end_evidence_with_declared_mismatch_is_partial(self) -> None:
        for observed, declared in ((3, 5), (5, 3)):
            status, reason = compute_scan_status(
                observed_count=observed,
                declared_count=declared,
                end_evidence="footer reached",
                stop_reason=None,
                access_markers=(),
                had_items=True,
            )
            self.assertEqual(status, "partial")
            self.assertIn(str(observed), reason)
            self.assertIn(str(declared), reason)
            self.assertIn("mismatch", reason.lower())

    def test_count_match_without_end_evidence_is_never_complete(self) -> None:
        # The honesty test: an accidental declared == observed match proves
        # nothing without end evidence.
        for stop_reason in (None, "timeout"):
            status, _ = compute_scan_status(
                observed_count=3,
                declared_count=3,
                end_evidence=None,
                stop_reason=stop_reason,
                access_markers=(),
                had_items=True,
            )
            self.assertNotEqual(status, "complete")
            self.assertIn(status, ("partial", "in_progress"))
        status, _ = compute_scan_status(
            observed_count=2,
            declared_count=3,
            end_evidence=None,
            stop_reason=None,
            access_markers=(),
            had_items=True,
        )
        self.assertEqual(status, "in_progress")

    def test_stop_reason_is_quoted_and_never_proves_completeness(self) -> None:
        status, reason = compute_scan_status(
            observed_count=5,
            declared_count=None,
            end_evidence=None,
            stop_reason="3 scroll cycles produced no new IDs",
            access_markers=(),
            had_items=True,
        )
        self.assertEqual(status, "partial")
        self.assertIn("3 scroll cycles produced no new IDs", reason)
        self.assertIn("does not prove completeness", reason)

    def test_zero_items_without_evidence_is_in_progress_with_caveat(self) -> None:
        status, reason = compute_scan_status(
            observed_count=0,
            declared_count=None,
            end_evidence=None,
            stop_reason=None,
            access_markers=(),
            had_items=False,
        )
        self.assertEqual(status, "in_progress")
        self.assertIn("incomplete", reason.lower())

    def test_zero_items_with_stop_reason_is_partial(self) -> None:
        status, reason = compute_scan_status(
            observed_count=0,
            declared_count=0,
            end_evidence=None,
            stop_reason="operator paused the session",
            access_markers=(),
            had_items=False,
        )
        self.assertEqual(status, "partial")
        self.assertIn("operator paused the session", reason)

    def test_access_markers_block_and_do_not_imply_deletion(self) -> None:
        status, reason = compute_scan_status(
            observed_count=0,
            declared_count=0,
            end_evidence=None,
            stop_reason=None,
            access_markers=("collection isn't available", "log in"),
            had_items=False,
        )
        self.assertEqual(status, "blocked")
        self.assertIn("access", reason.lower())
        self.assertIn("do not prove deletion or privacy", reason.lower())
        self.assertIn("empty", reason.lower())


# --------------------------------------------------------------------------
# 4) Merge-only scan state and inventory sync
# --------------------------------------------------------------------------


class MergeTests(unittest.TestCase):
    def test_small_complete_collection_merges_and_syncs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            scan = merge_page(None, video_ids=(1, 2, 3), declared=3, end_evidence="footer reached")
            self.assertEqual(scan.status, "complete")
            self.assertEqual(len(scan.items), 3)
            added, updated = sync_inventory_from_scan(state, scan)
            self.assertEqual((added, updated), (3, 0))
            entries = InventoryStore(state).load()
            self.assertEqual(len(entries), 3)
            for entry in entries:
                self.assertEqual(entry.input_origin, "collection")
                self.assertEqual(entry.completeness, "complete")
                self.assertEqual(entry.collections, (COLLECTION_URL,))
                self.assertEqual(entry.declared_count, 3)
                self.assertEqual(entry.observed_count, 3)

    def test_incremental_pages_keep_first_seen_and_accumulate_hrefs(self) -> None:
        page1 = make_observation(video_ids=(1, 2, 3))
        scan1 = merge_observation(
            None, REF, page1,
            declared_count=None, end_evidence=None, stop_reason=None,
            captured_at=T1, container_attr=CONTAINER_ATTR,
            container_value=CONTAINER_VALUE,
        )
        page2 = make_observation(video_ids=(3, 4))  # 3 re-observed, absolute vs relative hrefs differ only if built so
        scan2 = merge_observation(
            scan1, REF, page2,
            declared_count=None, end_evidence=None, stop_reason=None,
            captured_at=T2, container_attr=CONTAINER_ATTR,
            container_value=CONTAINER_VALUE,
        )
        self.assertEqual(len(scan2.items), 4)
        by_id = {record.stable_id: record for record in scan2.items}
        self.assertEqual(by_id[fake_id(3)].first_seen_at, T1)
        self.assertEqual(by_id[fake_id(3)].last_seen_at, T2)
        self.assertEqual(by_id[fake_id(4)].first_seen_at, T2)
        self.assertEqual(scan2.captures[-1].new_ids, (fake_id(4),))

    def test_duplicate_href_forms_accumulate_raw_hrefs(self) -> None:
        scan1 = merge_page(None, video_ids=(3,), captured_at=T1)
        page2 = extract_page_items(
            collection_page(item_href(3, absolute=True)),
            CONTAINER_ATTR,
            CONTAINER_VALUE,
        )
        scan2 = merge_observation(
            scan1, REF, page2,
            declared_count=None, end_evidence=None, stop_reason=None,
            captured_at=T2, container_attr=CONTAINER_ATTR,
            container_value=CONTAINER_VALUE,
        )
        by_id = {record.stable_id: record for record in scan2.items}
        self.assertEqual(len(scan2.items), 1)
        self.assertEqual(
            by_id[fake_id(3)].raw_hrefs,
            (
                f"/@{FAKE_AUTHOR}/video/{fake_id(3)}",
                f"https://www.tiktok.com/@{FAKE_AUTHOR}/video/{fake_id(3)}",
            ),
        )

    def test_declared_latest_non_null_end_sticky_stop_latest(self) -> None:
        scan1 = merge_page(None, video_ids=(1,), declared=3, stop_reason="operator pause", captured_at=T1)
        self.assertEqual(scan1.status, "partial")
        scan2 = merge_page(scan1, video_ids=(2,), end_evidence="footer reached", captured_at=T2)
        self.assertEqual(scan2.declared_count, 3)  # None does not clear it
        self.assertEqual(scan2.end_of_list_evidence, "footer reached")
        self.assertEqual(scan2.stop_reason, "operator pause")  # None does not clear it
        scan3 = merge_page(scan2, video_ids=(3,), stop_reason="timeout after 5 minutes", captured_at=T3)
        self.assertEqual(scan3.stop_reason, "timeout after 5 minutes")
        self.assertEqual(scan3.end_of_list_evidence, "footer reached")  # sticky

    def test_access_markers_accumulate_and_block_the_scan(self) -> None:
        scan1 = merge_page(None, video_ids=(1,), captured_at=T1)
        self.assertNotEqual(scan1.status, "blocked")
        scan2 = merge_page(scan1, video_ids=(1,), captured_at=T2, access_text=ACCESS_TEXT)
        self.assertEqual(scan2.status, "blocked")
        lowered = [marker.lower() for marker in scan2.captures[-1].access_markers]
        self.assertIn(DEFAULT_ACCESS_BLOCK_MARKERS[0], lowered)
        self.assertIn("log in", lowered)
        self.assertIn("do not prove deletion or privacy", scan2.status_reason.lower())

    def test_status_matches_accumulated_evidence(self) -> None:
        scan = merge_page(None, video_ids=(1, 2), declared=2, end_evidence="footer reached")
        self.assertEqual(scan.status, "complete")
        self.assertEqual(scan.captures[-1].status_after, scan.status)
        self.assertIn(scan.status, SCAN_STATUSES)


class InventorySyncTests(unittest.TestCase):
    def test_preexisting_direct_url_entry_keeps_origin_gains_association(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            store = InventoryStore(state)
            preexisting = InventoryEntry(
                source="tiktok",
                stable_id=fake_id(1),
                canonical_url=f"https://www.tiktok.com/@other_author/video/{fake_id(1)}",
                input_origin="direct-url",
                discovered_at=T1,
                author="other_author",
            )
            store.save([preexisting])
            scan = merge_page(None, video_ids=(1, 2), declared=2, end_evidence="footer reached")
            added, updated = sync_inventory_from_scan(state, scan)
            self.assertEqual((added, updated), (1, 1))
            entries = {entry.stable_id: entry for entry in store.load()}
            kept = entries[fake_id(1)]
            self.assertEqual(kept.input_origin, "direct-url")  # untouched
            self.assertEqual(kept.completeness, "unknown")  # untouched
            self.assertEqual(kept.discovered_at, T1)  # untouched
            self.assertIsNone(kept.declared_count)  # untouched
            self.assertIsNone(kept.observed_count)  # untouched
            self.assertEqual(kept.collections, (COLLECTION_URL,))  # appended
            fresh = entries[fake_id(2)]
            self.assertEqual(fresh.input_origin, "collection")

    def test_second_sync_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            scan = merge_page(None, video_ids=(1, 2), declared=2, end_evidence="footer reached")
            self.assertEqual(sync_inventory_from_scan(state, scan), (2, 0))
            self.assertEqual(sync_inventory_from_scan(state, scan), (0, 0))

    def test_status_maps_to_inventory_completeness(self) -> None:
        cases = [
            (dict(video_ids=(1, 2, 3), declared=3, end_evidence="footer"), "complete"),
            (dict(video_ids=(1, 2), stop_reason="timeout"), "incomplete"),
            (dict(video_ids=(1,)), "unknown"),
            (dict(access_text=ACCESS_TEXT), "unknown"),
        ]
        for index, (kwargs, expected) in enumerate(cases):
            with tempfile.TemporaryDirectory() as tmp:
                state = make_state_root(tmp)
                scan = merge_page(None, **kwargs)
                self.assertIn(scan.status, SCAN_STATUSES)
                sync_inventory_from_scan(state, scan)
                for entry in InventoryStore(state).load():
                    self.assertEqual(entry.completeness, expected, msg=f"case {index}")
                    self.assertEqual(entry.input_origin, "collection")


# --------------------------------------------------------------------------
# 5) Persistence, DOM change, store and resolution
# --------------------------------------------------------------------------


class PersistenceTests(unittest.TestCase):
    def test_interruption_with_new_store_instance_merges_without_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            store = CollectionScanStore(state)
            scan1 = merge_page(None, video_ids=(1, 2), captured_at=T1)
            store.save(scan1)
            # Simulate a fresh process: a brand-new store over the same root.
            reloaded = CollectionScanStore(state).load(scan1.collection.collection_key)
            self.assertIsNotNone(reloaded)
            scan2 = merge_page(reloaded, video_ids=(2, 3), captured_at=T2)
            CollectionScanStore(state).save(scan2)
            final = CollectionScanStore(state).load(scan2.collection.collection_key)
            self.assertIsNotNone(final)
            self.assertEqual(
                {record.stable_id for record in final.items},
                {fake_id(1), fake_id(2), fake_id(3)},
            )
            self.assertEqual(len(final.captures), 2)  # no torn history
            self.assertEqual(final.captures[0].captured_at, T1)

    def test_reenumeration_is_idempotent_merge_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            scan1 = merge_page(None, video_ids=(1, 2, 3), declared=3, end_evidence="footer reached", captured_at=T1)
            scan2 = merge_page(scan1, video_ids=(1, 2, 3), declared=3, end_evidence="footer reached", captured_at=T2)
            self.assertEqual(len(scan2.items), 3)  # zero duplicates
            by_id = {record.stable_id: record for record in scan2.items}
            self.assertTrue(all(record.first_seen_at == T1 for record in by_id.values()))
            self.assertEqual(len(scan2.captures), 2)
            store = CollectionScanStore(state)
            store.save(scan2)
            self.assertEqual(store.keys(), [scan2.collection.collection_key])
            self.assertEqual(store.load(scan2.collection.collection_key), scan2)

    def test_dom_change_fails_explicitly_and_leaves_state_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            store = CollectionScanStore(state)
            scan1 = merge_page(None, video_ids=(1,), captured_at=T1)
            store.save(scan1)
            path = store.path_for(scan1.collection.collection_key)
            before = path.read_text(encoding="utf-8")
            with self.assertRaises(CollectionDOMError):
                extract_page_items(
                    "<html><body><div class='something-else'>no marker</div></body></html>",
                    CONTAINER_ATTR,
                    CONTAINER_VALUE,
                )
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_dom_error_is_a_collection_error(self) -> None:
        self.assertTrue(issubclass(CollectionDOMError, CollectionError))

    def test_empty_html_fails_instead_of_fake_empty_list(self) -> None:
        with self.assertRaises(CollectionDOMError):
            extract_page_items("", CONTAINER_ATTR, CONTAINER_VALUE)


class StoreTests(unittest.TestCase):
    def test_path_for_validates_hex16_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CollectionScanStore(make_state_root(tmp))
            with self.assertRaises(CollectionError):
                store.path_for("NOT-A-KEY")
            with self.assertRaises(CollectionError):
                store.path_for("abc123")  # too short
            with self.assertRaises(CollectionError):
                store.path_for("0123456789ABCDEF")  # uppercase refused

    def test_load_missing_is_none_and_malformed_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            store = CollectionScanStore(state)
            self.assertIsNone(store.load("0123456789abcdef"))
            path = state.collections_dir / "0123456789abcdef.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(CollectionError):
                store.load("0123456789abcdef")
            path.write_text(
                json.dumps({"version": 1, "scan": {"schema_version": 99}}),
                encoding="utf-8",
            )
            with self.assertRaises(CollectionError):
                store.load("0123456789abcdef")
            # keys() lists by filename; content validity is load()'s job.
            self.assertEqual(store.keys(), ["0123456789abcdef"])

    def test_resolve_by_key_and_by_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            scan = merge_page(None, video_ids=(1,), captured_at=T1)
            CollectionScanStore(state).save(scan)
            ref, loaded = resolve_collection(state, scan.collection.collection_key)
            self.assertEqual(ref.collection_key, scan.collection.collection_key)
            self.assertEqual(loaded, scan)
            ref2, scan2 = resolve_collection(state, COLLECTION_URL)
            self.assertEqual(ref2.collection_key, scan.collection.collection_key)
            self.assertEqual(scan2, scan)
            with self.assertRaises(CollectionError):
                resolve_collection(state, "ffffffffffffffff")  # unknown key
            with self.assertRaises(CollectionError):
                resolve_collection(state, "https://example.com/nope")
            with self.assertRaises(CollectionError):
                resolve_collection(state, "  ")


# --------------------------------------------------------------------------
# 6) Contract roundtrip
# --------------------------------------------------------------------------


class ContractRoundtripTests(unittest.TestCase):
    def test_scan_state_roundtrip_equality(self) -> None:
        scan1 = merge_page(None, video_ids=(1, 2), declared=5, stop_reason="pause", captured_at=T1)
        scan2 = merge_page(scan1, video_ids=(3,), declared=5, end_evidence="footer reached", captured_at=T2)
        restored = CollectionScanState.from_dict(scan2.to_dict())
        self.assertEqual(restored, scan2)

    def test_bad_version_raises_collection_error(self) -> None:
        scan = merge_page(None, video_ids=(1,), captured_at=T1)
        document = json.loads(json.dumps(scan.to_dict()))
        document["schema_version"] = 99
        with self.assertRaises(CollectionError):
            CollectionScanState.from_dict(document)

    def test_bad_shapes_raise_contract_error(self) -> None:
        scan = merge_page(None, video_ids=(1,), captured_at=T1)
        document = scan.to_dict()
        document["status"] = "made_up_status"
        with self.assertRaises(ContractError):
            CollectionScanState.from_dict(document)
        document = scan.to_dict()
        document["status"] = "complete"  # valid again
        document["declared_count"] = "three"
        with self.assertRaises(ContractError):
            CollectionScanState.from_dict(document)


# --------------------------------------------------------------------------
# 7) Processing plan (document only)
# --------------------------------------------------------------------------


class PlanTests(unittest.TestCase):
    def _state_with_prior_work(self, tmp: str) -> StateRoot:
        state = make_state_root(tmp)
        Blocklist(state).reject(
            BlocklistEntry(
                video_id=fake_id(1),
                rejected_at=T1,
                reason="operator rejected permanently",
                source="tiktok",
            )
        )
        media = hashlib.sha256(b"fixture-media").hexdigest()
        Processed(state).record(
            ProcessedEntry(
                video_id=fake_id(2),
                media_sha256=media,
                completed_at=T1,
                stage_fingerprints={"emit": {"stage": "emit", "versions": {}}},
            )
        )
        Processed(state).record(
            ProcessedEntry(
                video_id=fake_id(3),
                media_sha256=media,
                completed_at=T1,
                stage_fingerprints={"prepare": {"stage": "prepare", "versions": {}}},
            )
        )
        return state

    def test_five_classifications_and_counts_sum_to_observed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state_with_prior_work(tmp)
            scan = merge_page(None, video_ids=(1, 2, 3, 4), photo_ids=(5,))
            plan = build_collection_plan(state, scan)
            by_id = {item.stable_id: item.classification for item in plan.items}
            self.assertEqual(by_id[fake_id(1)], "rejected")
            self.assertEqual(by_id[fake_id(2)], "processed_complete")
            self.assertEqual(by_id[fake_id(3)], "partial_resumable")
            self.assertEqual(by_id[fake_id(4)], "new_processable")
            self.assertEqual(by_id[fake_id(5)], "unsupported_photo")
            self.assertEqual(sum(plan.counts.values()), len(scan.items))
            for name in PLAN_CLASSIFICATIONS:
                self.assertIn(name, plan.counts)
            self.assertEqual(plan.counts["rejected"], 1)
            self.assertEqual(plan.counts["processed_complete"], 1)
            self.assertEqual(plan.counts["partial_resumable"], 1)
            self.assertEqual(plan.counts["new_processable"], 1)
            self.assertEqual(plan.counts["unsupported_photo"], 1)
            self.assertEqual(plan.scan_status, scan.status)
            self.assertEqual(plan.observed_count, len(scan.items))

    def test_photo_posts_are_never_processable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            scan = merge_page(None, photo_ids=(5,))
            self.assertEqual(scan.items[0].kind, "photo")
            added, _ = sync_inventory_from_scan(state, scan)
            self.assertEqual(added, 1)
            entry = InventoryStore(state).load()[0]
            self.assertTrue(entry.is_photo_post)
            plan = build_collection_plan(state, scan)
            classifications = {item.classification for item in plan.items}
            self.assertEqual(classifications, {"unsupported_photo"})
            self.assertNotIn("new_processable", classifications)

    def test_observed_is_never_treated_as_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(tmp)
            scan = merge_page(None, video_ids=(1,), declared=1, end_evidence="footer reached")
            self.assertEqual(scan.status, "complete")  # enumeration is done...
            plan = build_collection_plan(state, scan)
            self.assertEqual(plan.items[0].classification, "new_processable")  # ...not processing


# --------------------------------------------------------------------------
# 8) CLI wiring
# --------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_root = self.tmp / "state"

    def _main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _write_html(self, page: str, name: str = "page.html") -> Path:
        path = self.tmp / name
        path.write_text(page, encoding="utf-8")
        return path

    def _collect_argv(self, html_value: str, *extra: str) -> list[str]:
        return [
            "inventory-collect",
            COLLECTION_URL,
            "--html",
            html_value,
            "--container-attr",
            CONTAINER_ATTR,
            "--container-value",
            CONTAINER_VALUE,
            "--state-root",
            str(self.state_root),
            *extra,
        ]

    def test_collect_happy_path_then_status_and_plan(self) -> None:
        html_path = self._write_html(collection_page(item_href(1), item_href(2), item_href(3)))
        code, out, err = self._main(
            self._collect_argv(
                str(html_path),
                "--declared-count",
                "3",
                "--end-evidence",
                "footer reached",
            )
        )
        self.assertEqual(code, 0, msg=err)
        document = json.loads(out)
        self.assertEqual(document["status"], "complete")
        self.assertEqual(document["observed_count"], 3)
        self.assertEqual(document["declared_count"], 3)
        self.assertEqual(document["end_of_list_evidence"], "footer reached")
        self.assertEqual(document["new_ids"], [fake_id(1), fake_id(2), fake_id(3)])
        self.assertEqual(document["out_of_container_count"], 0)
        self.assertEqual(document["inventory_sync"], {"added": 3, "updated": 0})
        key = document["collection_key"]

        code, out, err = self._main(
            ["inventory-status", key, "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(json.loads(out)["status"], "complete")

        code, out, err = self._main(
            ["inventory-status", COLLECTION_URL, "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(json.loads(out)["declared_count"], 3)

        code, out, err = self._main(
            ["inventory-plan", key, "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 0, msg=err)
        plan = json.loads(out)
        self.assertEqual(plan["scan_status"], "complete")
        self.assertEqual(plan["observed_count"], 3)
        self.assertEqual(sum(plan["counts"].values()), 3)
        self.assertEqual(plan["counts"]["new_processable"], 3)

    def test_collect_reads_stdin_with_dash(self) -> None:
        page = collection_page(item_href(1))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(page)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(self._collect_argv("-"))
        self.assertEqual(code, 0, msg=err.getvalue())
        document = json.loads(out.getvalue())
        self.assertEqual(document["observed_count"], 1)

    def test_collect_blocked_exits_one_but_persists(self) -> None:
        html_path = self._write_html(
            collection_page(item_href(1), access_text=ACCESS_TEXT)
        )
        code, out, err = self._main(self._collect_argv(str(html_path)))
        self.assertEqual(code, 1)
        document = json.loads(out)
        self.assertEqual(document["status"], "blocked")
        self.assertIn("do not prove deletion or privacy", document["status_reason"].lower())
        self.assertEqual(document["inventory_sync"]["added"], 1)  # sync still ran
        code, out, err = self._main(
            ["inventory-status", document["collection_key"], "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(json.loads(out)["status"], "blocked")

    def test_collect_dom_error_exits_two_and_writes_no_state(self) -> None:
        html_path = self._write_html(
            "<html><body><div class='renamed'>marker moved</div></body></html>",
            name="changed.html",
        )
        code, out, err = self._main(self._collect_argv(str(html_path)))
        self.assertEqual(code, 2)
        self.assertIn("collection error", err)
        self.assertFalse((self.state_root / "collections").exists())

    def test_collect_invalid_url_exits_two(self) -> None:
        html_path = self._write_html(collection_page(item_href(1)), name="ok.html")
        code, out, err = self._main(
            ["inventory-collect", "https://example.com/collection/x", "--html", str(html_path),
             "--container-attr", CONTAINER_ATTR, "--container-value", CONTAINER_VALUE,
             "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 2)
        self.assertIn("collection error", err)

    def test_status_unknown_key_exits_two(self) -> None:
        code, out, err = self._main(
            ["inventory-status", "ffffffffffffffff", "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 2)
        self.assertIn("collection error", err)

    def test_plan_requires_existing_scan(self) -> None:
        code, out, err = self._main(
            ["inventory-plan", COLLECTION_URL, "--state-root", str(self.state_root)]
        )
        self.assertEqual(code, 2)
        self.assertIn("no persisted scan state", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
