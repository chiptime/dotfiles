"""Backlog review CLI tests (fixture-only, zero network/GPU/services).

Covers the mandated review scenarios: listing and detail, valid decision
format with malformed-input refusal, duplicate/unknown IDs, concurrent
change detection, stale decisions, accept preserving everything except
status, reject/blocklist coherence, failure between writes and recovery,
collision-free exclusive backups, idempotent re-application and the
multi-line-JSON-as-JSONL incident. All state lives in temp directories.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tiktok_ingest.cli import build_parser, main
from tiktok_ingest.contracts import BlocklistEntry
from tiktok_ingest.review import (
    DECISIONS_SCHEMA,
    REVIEW_DIR_NAME,
    ReviewConflictError,
    ReviewError,
    apply_decisions,
    build_plan,
    entry_sha256,
    list_entries,
    parse_decisions_file,
    record_decision,
    show_entry,
)
from tiktok_ingest.state import Blocklist, StateRoot

FIXED_NOW = "2026-09-18T12:00:00Z"


def entry_dict(video_id: str, status: str = "pending", **overrides) -> dict:
    entry = {
        "id": video_id,
        "url": f"https://www.tiktok.com/@author/video/{video_id}",
        "ingested_at": FIXED_NOW,
        "author": "author",
        "status": status,
        "classification": None,
        "unknown_classification_reason": "fixture: none",
        "entities": ["FixtureTool"],
        "claims": [
            {
                "claim": "FixtureTool has 12000 stars",
                "source": "audio",
                "verdict": "confirmed",
                "evidence": "frame_001",
                "note": None,
            }
        ],
        "fit": "adjacent",
        "actionable": "evaluate",
        "artifacts": f"runs/run-1/{video_id}/",
    }
    entry.update(overrides)
    return entry


def canonical_line(entry: dict) -> bytes:
    return json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8")


def write_backlog(state: StateRoot, entries: list[dict]) -> bytes:
    payload = b"".join(canonical_line(entry) + b"\n" for entry in entries)
    state.ensure_layout()
    state.backlog_path.write_bytes(payload)
    return payload


def make_state(tmp: Path) -> StateRoot:
    root = Path(tempfile.mkdtemp(dir=tmp, prefix="review-state-"))
    return StateRoot(root)


def decision_line(
    video_id: str,
    decision: str,
    entry_hash: str,
    *,
    reason: str | None = None,
    **overrides,
) -> str:
    record = {
        "schema": DECISIONS_SCHEMA,
        "id": video_id,
        "decision": decision,
        "decided_at": FIXED_NOW,
        "entry_sha256": entry_hash,
        "reason": reason,
    }
    record.update(overrides)
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def write_decisions(path: Path, lines: list[str]) -> Path:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


class ReviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = make_state(self.tmp)


class ListTests(ReviewTestCase):
    def test_lists_entries_with_hashes_and_filters_by_status(self) -> None:
        write_backlog(
            self.state,
            [
                entry_dict("111", status="pending"),
                entry_dict("222", status="accepted"),
                entry_dict("333", status="rejected"),
            ],
        )
        listed = list_entries(self.state)
        self.assertEqual([item["id"] for item in listed], ["111", "222", "333"])
        raw_lines = self.state.backlog_path.read_bytes().splitlines()
        self.assertEqual(listed[1]["entry_sha256"], entry_sha256(raw_lines[1]))

        pending = list_entries(self.state, status="pending")
        self.assertEqual([item["id"] for item in pending], ["111"])
        rejected = list_entries(self.state, status="rejected")
        self.assertEqual([item["id"] for item in rejected], ["333"])

    def test_unknown_status_filter_is_refused(self) -> None:
        with self.assertRaisesRegex(ReviewError, "unknown status filter"):
            list_entries(self.state, status="confirmed")

    def test_missing_backlog_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ReviewError, "no backlog at"):
            list_entries(self.state)
        with self.assertRaisesRegex(ReviewError, "no backlog at"):
            show_entry(self.state, "111")
        with self.assertRaisesRegex(ReviewError, "no backlog at"):
            record_decision(
                self.state,
                video_id="111",
                decision="accepted",
                decisions_file=self.tmp / "d.jsonl",
            )


class ShowTests(ReviewTestCase):
    def test_show_includes_claims_and_verification_reasons(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        run_dir = self.state.root / "runs" / "run-1" / "111"
        run_dir.mkdir(parents=True)
        (run_dir / "verification.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "video_id": "111",
                    "generated_at": FIXED_NOW,
                    "rules_version": "2",
                    "claims": [
                        {
                            "claim": "FixtureTool has 12000 stars",
                            "verdict": "confirmed",
                            "reason": "fetched 1 candidate source(s); "
                            "mechanical passage matched",
                            "evidence": "frame_001",
                            "note": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        document = show_entry(self.state, "111")
        self.assertEqual(document["id"], "111")
        self.assertEqual(document["entry"]["status"], "pending")
        self.assertEqual(document["verification"]["rules_version"], "2")
        self.assertEqual(
            document["verification"]["claims"][0]["verdict"], "confirmed"
        )
        self.assertIn("mechanical passage matched",
                      document["verification"]["claims"][0]["reason"])

    def test_show_without_verification_records_a_note(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        document = show_entry(self.state, "111")
        self.assertIsNone(document["verification"])
        self.assertIn("no verification.json", document["verification_note"])

    def test_show_unknown_id_is_refused(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        with self.assertRaisesRegex(ReviewError, "unknown backlog id"):
            show_entry(self.state, "999")

    def test_show_refuses_artifacts_path_traversal(self) -> None:
        write_backlog(
            self.state, [entry_dict("111", artifacts="../../etc/passwd/")]
        )
        with self.assertRaisesRegex(ReviewError, "relative to the state root"):
            show_entry(self.state, "111")


class DecideTests(ReviewTestCase):
    def test_records_decision_bound_to_current_line_hash(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        decisions_file = self.tmp / "decisions.jsonl"
        record = record_decision(
            self.state,
            video_id="111",
            decision="accepted",
            decisions_file=decisions_file,
            reason="looks solid",
            now=FIXED_NOW,
        )
        raw_line = self.state.backlog_path.read_bytes().splitlines()[0]
        self.assertEqual(record["entry_sha256"], entry_sha256(raw_line))
        self.assertEqual(record["schema"], DECISIONS_SCHEMA)
        self.assertEqual(record["decision"], "accepted")
        parsed = json.loads(decisions_file.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(parsed, record)

    def test_rejected_requires_reason(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        with self.assertRaisesRegex(ReviewError, "requires --reason"):
            record_decision(
                self.state,
                video_id="111",
                decision="rejected",
                decisions_file=self.tmp / "d.jsonl",
            )
        with self.assertRaisesRegex(ReviewError, "requires --reason"):
            record_decision(
                self.state,
                video_id="111",
                decision="rejected",
                decisions_file=self.tmp / "d.jsonl",
                reason="   ",
            )

    def test_unknown_id_is_refused(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        with self.assertRaisesRegex(ReviewError, "unknown backlog id"):
            record_decision(
                self.state,
                video_id="999",
                decision="accepted",
                decisions_file=self.tmp / "d.jsonl",
            )

    def test_duplicate_id_in_file_is_refused(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        decisions_file = self.tmp / "d.jsonl"
        record_decision(
            self.state,
            video_id="111",
            decision="accepted",
            decisions_file=decisions_file,
            now=FIXED_NOW,
        )
        with self.assertRaisesRegex(ReviewError, "already records a decision"):
            record_decision(
                self.state,
                video_id="111",
                decision="rejected",
                decisions_file=decisions_file,
                reason="changed my mind",
            )

    def test_empty_existing_decisions_file_is_refused(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        empty = self.tmp / "empty.jsonl"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(ReviewError, "decisions file is empty"):
            record_decision(
                self.state,
                video_id="111",
                decision="accepted",
                decisions_file=empty,
            )


class DecisionsFormatTests(ReviewTestCase):
    """The decisions document is strict versioned JSONL."""

    def _valid(self, **overrides) -> str:
        return decision_line("111", "accepted", "a" * 64, **overrides)

    def test_valid_document_parses(self) -> None:
        path = write_decisions(
            self.tmp / "d.jsonl",
            [
                self._valid(),
                decision_line("222", "rejected", "b" * 64, reason="spam"),
            ],
        )
        parsed = parse_decisions_file(path)
        self.assertEqual([item["id"] for item in parsed], ["111", "222"])

    def test_multiline_pretty_json_is_refused(self) -> None:
        # The incident: a file NAMED .jsonl holding pretty-printed
        # multi-line JSON objects concatenated together.
        path = self.tmp / "pretty.jsonl"
        path.write_text(
            json.dumps({"schema": DECISIONS_SCHEMA}, indent=2)
            + "\n"
            + json.dumps({"schema": DECISIONS_SCHEMA}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReviewError, "single-line JSON object"):
            parse_decisions_file(path)

    def test_malformed_decisions_are_refused(self) -> None:
        base = json.loads(self._valid())
        cases = {
            "bad schema": json.dumps({**base, "schema": "tiktok-ingest/backlog-decision@2"}),
            "unknown field": json.dumps({**base, "extra": "nope"}),
            "bad decision": json.dumps({**base, "decision": "confirmed"}),
            "bad hash": json.dumps({**base, "entry_sha256": "XYZ"}),
            "short hash": json.dumps({**base, "entry_sha256": "a" * 63}),
            "bad date": json.dumps({**base, "decided_at": "18/09/2026"}),
            "missing id": json.dumps(
                {
                    "schema": DECISIONS_SCHEMA,
                    "decision": "accepted",
                    "decided_at": FIXED_NOW,
                    "entry_sha256": "a" * 64,
                }
            ),
            "non-object line": json.dumps(["111"]),
            "rejected without reason": decision_line(
                "111", "rejected", "a" * 64, reason=None
            ),
        }
        for label, line in cases.items():
            with self.subTest(case=label):
                path = write_decisions(self.tmp / "d.jsonl", [line])
                with self.assertRaises(ReviewError):
                    parse_decisions_file(path)

    def test_duplicate_ids_across_lines_are_refused(self) -> None:
        path = write_decisions(
            self.tmp / "d.jsonl",
            [self._valid(), self._valid()],
        )
        with self.assertRaisesRegex(ReviewError, "duplicate decision"):
            parse_decisions_file(path)

    def test_missing_file_and_empty_file_are_refused(self) -> None:
        with self.assertRaisesRegex(ReviewError, "not found"):
            parse_decisions_file(self.tmp / "absent.jsonl")
        empty = self.tmp / "empty.jsonl"
        empty.write_bytes(b"\n\n")
        with self.assertRaisesRegex(ReviewError, "decisions file is empty"):
            parse_decisions_file(empty)


class PlanTests(ReviewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.entries = [
            entry_dict("111", status="pending"),
            entry_dict("222", status="pending"),
            entry_dict("333", status="accepted"),
        ]
        self.payload = write_backlog(self.state, self.entries)
        raw_lines = self.payload.splitlines()
        self.hashes = {i: entry_sha256(line) for i, line in enumerate(raw_lines)}

    def test_plan_reports_changes_and_expected_hashes(self) -> None:
        decisions = parse_decisions_file(
            write_decisions(
                self.tmp / "d.jsonl",
                [
                    decision_line("111", "accepted", self.hashes[0]),
                    decision_line(
                        "222", "rejected", self.hashes[1], reason="spam"
                    ),
                ],
            )
        )
        plan = build_plan(self.state, decisions)
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual(plan["files_to_write"], ["backlog.jsonl", "blocklist.json"])
        self.assertEqual(
            plan["blocklist_additions"], [{"id": "222", "reason": "spam"}]
        )
        by_id = {item["id"]: item for item in plan["decisions"]}
        self.assertFalse(by_id["111"]["already_applied"])
        self.assertTrue(by_id["111"]["rewrite"])
        self.assertTrue(by_id["222"]["append_blocklist"])

        # Expected post-entry hash equals the canonical rewrite we predict.
        updated = dict(self.entries[0])
        updated["status"] = "accepted"
        self.assertEqual(
            by_id["111"]["post_entry_sha256"],
            entry_sha256(canonical_line(updated)),
        )
        # Expected whole-file hash: both rewrites applied.
        updated222 = dict(self.entries[1])
        updated222["status"] = "rejected"
        expected_after = b"".join(
            [
                canonical_line(updated) + b"\n",
                canonical_line(updated222) + b"\n",
                canonical_line(self.entries[2]) + b"\n",
            ]
        )
        self.assertEqual(
            plan["backlog_sha256_before"], entry_sha256(self.payload)
        )
        self.assertEqual(
            plan["backlog_sha256_after"], hashlib.sha256(expected_after).hexdigest()
        )

    def test_plan_flags_stale_decision(self) -> None:
        decisions = parse_decisions_file(
            write_decisions(
                self.tmp / "d.jsonl",
                [decision_line("111", "accepted", "c" * 64)],
            )
        )
        plan = build_plan(self.state, decisions)
        self.assertEqual(len(plan["conflicts"]), 1)
        self.assertIn("hash mismatch", plan["conflicts"][0]["reason"])

    def test_plan_refuses_unknown_id(self) -> None:
        decisions = parse_decisions_file(
            write_decisions(
                self.tmp / "d.jsonl",
                [decision_line("999", "accepted", "c" * 64)],
            )
        )
        with self.assertRaisesRegex(ReviewError, "unknown backlog id"):
            build_plan(self.state, decisions)

    def test_plan_refuses_accepting_a_blocklisted_id(self) -> None:
        Blocklist(self.state).reject(
            BlocklistEntry(
                video_id="111",
                rejected_at=FIXED_NOW,
                reason="spam",
                source="fixture",
            )
        )
        decisions = parse_decisions_file(
            write_decisions(
                self.tmp / "d.jsonl",
                [decision_line("111", "accepted", self.hashes[0])],
            )
        )
        plan = build_plan(self.state, decisions)
        self.assertEqual(len(plan["conflicts"]), 1)
        self.assertIn("permanently blocklisted", plan["conflicts"][0]["reason"])

    def test_plan_marks_already_applied(self) -> None:
        decisions = parse_decisions_file(
            write_decisions(
                self.tmp / "d.jsonl",
                [decision_line("333", "accepted", self.hashes[2])],
            )
        )
        plan = build_plan(self.state, decisions)
        self.assertEqual(plan["files_to_write"], [])
        self.assertTrue(plan["decisions"][0]["already_applied"])
        self.assertEqual(
            plan["backlog_sha256_after"], plan["backlog_sha256_before"]
        )


class ApplyTests(ReviewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.entries = [
            entry_dict("111", status="pending"),
            entry_dict("222", status="pending"),
        ]
        self.payload = write_backlog(self.state, self.entries)
        raw_lines = self.payload.splitlines()
        self.hashes = {i: entry_sha256(line) for i, line in enumerate(raw_lines)}

    def _decisions(self, lines: list[str]) -> Path:
        return write_decisions(self.tmp / "d.jsonl", lines)

    def test_accept_preserves_everything_except_status(self) -> None:
        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("111", "accepted", self.hashes[0])]
            )
        )
        summary = apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertEqual(summary["applied"], ["111"])
        before = self.entries[0]
        after = json.loads(
            self.state.backlog_path.read_bytes().splitlines()[0].decode("utf-8")
        )
        self.assertEqual(after["status"], "accepted")
        for key, value in before.items():
            if key == "status":
                continue
            self.assertEqual(after[key], value, f"field {key} must be preserved")
        # Acceptance is NOT confirmation: the claim verdict is untouched.
        self.assertEqual(after["claims"][0]["verdict"], "confirmed")
        self.assertEqual(summary["changed"], True)

    def test_reject_appends_blocklist_and_is_coherent(self) -> None:
        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("222", "rejected", self.hashes[1], reason="spam")]
            )
        )
        apply_decisions(self.state, decisions, now=FIXED_NOW)
        line = json.loads(
            self.state.backlog_path.read_bytes().splitlines()[1].decode("utf-8")
        )
        self.assertEqual(line["status"], "rejected")
        blocked = Blocklist(self.state)
        self.assertTrue(blocked.is_blocked("222"))
        entry = blocked.entries()["222"]
        self.assertEqual(entry.reason, "spam")
        self.assertEqual(entry.source, "backlog-review-cli")

    def test_reject_publishes_blocklist_before_backlog(self) -> None:
        order: list[str] = []
        import tiktok_ingest.review as review_module
        import tiktok_ingest.state as state_module

        real_state_write = state_module.atomic_write_bytes
        real_review_write = review_module.atomic_write_bytes

        def recording(path, data, real):
            order.append(Path(path).name)
            return real(path, data)

        with mock.patch.object(
            state_module,
            "atomic_write_bytes",
            lambda p, d: recording(p, d, real_state_write),
        ), mock.patch.object(
            review_module,
            "atomic_write_bytes",
            lambda p, d: recording(p, d, real_review_write),
        ):
            decisions = parse_decisions_file(
                self._decisions(
                    [
                        decision_line(
                            "222", "rejected", self.hashes[1], reason="spam"
                        )
                    ]
                )
            )
            apply_decisions(self.state, decisions, now=FIXED_NOW)
        # Fail-safe publication order: the permanent blocklist entry lands
        # BEFORE the backlog status change.
        self.assertEqual(order[0], "blocklist.json")
        self.assertEqual(order[1], "backlog.jsonl")

    def test_apply_is_idempotent(self) -> None:
        decisions = parse_decisions_file(
            self._decisions(
                [
                    decision_line("111", "accepted", self.hashes[0]),
                    decision_line(
                        "222", "rejected", self.hashes[1], reason="spam"
                    ),
                ]
            )
        )
        apply_decisions(self.state, decisions, now=FIXED_NOW)
        backlog_after_first = self.state.backlog_path.read_bytes()
        blocklist_after_first = (
            self.state.root / "blocklist.json"
        ).read_bytes()

        second = apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertEqual(second["changed"], False)
        self.assertEqual(sorted(second["already_applied"]), ["111", "222"])
        self.assertEqual(second["applied"], [])
        self.assertEqual(second["files_written"], [])
        self.assertEqual(second["backups"], [])
        self.assertEqual(self.state.backlog_path.read_bytes(), backlog_after_first)
        self.assertEqual(
            (self.state.root / "blocklist.json").read_bytes(),
            blocklist_after_first,
        )

    def test_concurrent_change_is_detected_with_zero_writes(self) -> None:
        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("111", "accepted", self.hashes[0])]
            )
        )
        # A concurrent writer touches entry 111 between decide and apply.
        touched = dict(self.entries[0])
        touched["fit"] = "core"
        write_backlog(
            self.state, [touched, self.entries[1]]
        )
        with self.assertRaises(ReviewConflictError) as ctx:
            apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertIn("hash mismatch", str(ctx.exception))
        # Nothing was written and no audit dir was created.
        self.assertEqual(
            self.state.backlog_path.read_bytes(),
            b"".join(canonical_line(e) + b"\n" for e in [touched, self.entries[1]]),
        )
        self.assertFalse(
            (self.state.root / REVIEW_DIR_NAME).exists(),
            "no audit directory may be created on a refused apply",
        )

    def test_stale_decision_after_status_change_is_a_noop_conflict_free(self) -> None:
        # Entry already accepted by someone else: decision satisfied,
        # even though the line hash moved on (claims sync etc.).
        accepted = dict(self.entries[0])
        accepted["status"] = "accepted"
        accepted["fit"] = "core"
        write_backlog(self.state, [accepted, self.entries[1]])
        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("111", "accepted", self.hashes[0])]
            )
        )
        summary = apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertEqual(summary["already_applied"], ["111"])
        self.assertEqual(summary["changed"], False)

    def test_change_between_plan_and_publish_is_not_applied_to_new_content(
        self,
    ) -> None:
        """Regression (deterministic interleave): a foreign writer changes
        a reviewed entry BETWEEN build_plan and the publish-source read;
        the old decision must NOT be applied to the new content."""
        import tiktok_ingest.review as review_module

        real_read = review_module._read_raw_backlog
        calls = {"n": 0}

        def racing_read(state):
            calls["n"] += 1
            if calls["n"] == 2:
                # Concurrent writer lands right before the publish read.
                foreign = dict(self.entries[0])
                foreign["fit"] = "core"  # foreign content change
                write_backlog(self.state, [foreign, self.entries[1]])
            return real_read(state)

        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("111", "accepted", self.hashes[0])]
            )
        )
        with mock.patch.object(review_module, "_read_raw_backlog", racing_read):
            with self.assertRaisesRegex(
                ReviewConflictError,
                "changed between planning and publication",
            ) as ctx:
                apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertIn(self.hashes[0], str(ctx.exception))
        # The OLD decision was not applied: status is still pending and
        # the FOREIGN change is preserved (no blind rollback).
        line = json.loads(
            self.state.backlog_path.read_bytes().splitlines()[0].decode("utf-8")
        )
        self.assertEqual(line["status"], "pending")
        self.assertEqual(line["fit"], "core")
        # Nothing was published: no blocklist, no receipt.
        self.assertFalse((self.state.root / "blocklist.json").exists())
        stamp = FIXED_NOW.replace(":", "-")
        self.assertFalse(
            (
                self.state.root
                / REVIEW_DIR_NAME
                / f"backlog-apply-{stamp}"
                / "apply-receipt.json"
            ).exists()
        )

    def test_change_between_plan_and_publish_rejected_keeps_blocklist_unpublished(
        self,
    ) -> None:
        """Same interleave with a rejected decision: hash revalidation on
        the publish-source bytes happens BEFORE any publication, so the
        permanent blocklist entry is NOT written for stale content."""
        import tiktok_ingest.review as review_module

        real_read = review_module._read_raw_backlog
        calls = {"n": 0}

        def racing_read(state):
            calls["n"] += 1
            if calls["n"] == 2:
                foreign = dict(self.entries[1])
                foreign["entities"] = ["SomeoneElse"]  # foreign claims sync
                write_backlog(self.state, [self.entries[0], foreign])
            return real_read(state)

        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("222", "rejected", self.hashes[1], reason="spam")]
            )
        )
        with mock.patch.object(review_module, "_read_raw_backlog", racing_read):
            with self.assertRaisesRegex(
                ReviewConflictError, "changed between planning and publication"
            ):
                apply_decisions(self.state, decisions, now=FIXED_NOW)
        # No publication at all: the blocklist was never written.
        self.assertFalse((self.state.root / "blocklist.json").exists())
        line = json.loads(
            self.state.backlog_path.read_bytes().splitlines()[1].decode("utf-8")
        )
        self.assertEqual(line["status"], "pending")
        self.assertEqual(line["entities"], ["SomeoneElse"])  # foreign change kept

    def test_partial_publication_is_registered_and_recoverable(self) -> None:
        """A rejected decision whose backlog publication is detected as
        corrupted AFTER both files were published: the partial state is
        registered in a status:"partial" receipt and a re-run recovers."""
        import tiktok_ingest.review as review_module

        real_write = review_module.atomic_write_bytes

        def tampering_write(path, data):
            if Path(path).name == "backlog.jsonl":
                # Simulate a writer racing right after our replace.
                return real_write(path, data.replace(b'"pending"', b'"accepted"', 1))
            return real_write(path, data)

        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("222", "rejected", self.hashes[1], reason="spam")]
            )
        )
        with mock.patch.object(
            review_module, "atomic_write_bytes", tampering_write
        ):
            with self.assertRaisesRegex(
                ReviewConflictError, "PARTIAL publication"
            ):
                apply_decisions(self.state, decisions, now=FIXED_NOW)

        # The blocklist WAS published: the state is partial, not clean.
        self.assertTrue(Blocklist(self.state).is_blocked("222"))
        stamp = FIXED_NOW.replace(":", "-")
        receipt_path = (
            self.state.root
            / REVIEW_DIR_NAME
            / f"backlog-apply-{stamp}"
            / "apply-receipt.json"
        )
        self.assertTrue(receipt_path.is_file())
        partial = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["backlog_published"], True)
        self.assertEqual(partial["blocklist_published"], [{"id": "222", "result": "appended"}])
        self.assertIn("re-run backlog-apply", partial["recovery"])

        # Recovery: a later-stamp re-run converges. Entry 222 is already
        # rejected on disk (the simulated racer corrupted line 111, not
        # ours), so the re-run is a registered NOOP for 222; the foreign
        # 111 state is preserved (no blind rollback).
        summary = apply_decisions(
            self.state, decisions, now="2026-09-18T12:00:01Z"
        )
        self.assertEqual(summary["status"], "noop")
        self.assertEqual(summary["already_applied"], ["222"])
        self.assertEqual(summary["changed"], False)
        line = json.loads(
            self.state.backlog_path.read_bytes().splitlines()[1].decode("utf-8")
        )
        self.assertEqual(line["status"], "rejected")
        blocklist_document = json.loads(
            (self.state.root / "blocklist.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(blocklist_document["entries"]), 1)

    def test_failure_between_writes_recovers_on_rerun(self) -> None:
        import tiktok_ingest.review as review_module

        real_write = review_module.atomic_write_bytes

        def failing_backlog_write(path, data):
            if Path(path).name == "backlog.jsonl":
                raise OSError("simulated crash between writes")
            return real_write(path, data)

        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("222", "rejected", self.hashes[1], reason="spam")]
            )
        )
        with mock.patch.object(
            review_module, "atomic_write_bytes", failing_backlog_write
        ):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                apply_decisions(self.state, decisions, now=FIXED_NOW)
        # Crash state: blocklist published, backlog untouched. The
        # partial publication IS registered, not silently swallowed.
        self.assertTrue(Blocklist(self.state).is_blocked("222"))
        self.assertEqual(
            json.loads(
                self.state.backlog_path.read_bytes().splitlines()[1].decode("utf-8")
            )["status"],
            "pending",
        )
        stamp = FIXED_NOW.replace(":", "-")
        partial = json.loads(
            (
                self.state.root
                / REVIEW_DIR_NAME
                / f"backlog-apply-{stamp}"
                / "apply-receipt.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["backlog_published"], False)
        self.assertEqual(
            partial["blocklist_published"], [{"id": "222", "result": "appended"}]
        )

        # Recovery: re-run apply with a LATER timestamp (a same-stamp
        # re-run would collide with the first run's exclusive backups);
        # the blocklist reject is a duplicate skip and the backlog line
        # is now updated exactly once.
        summary = apply_decisions(
            self.state, decisions, now="2026-09-18T12:00:01Z"
        )
        self.assertEqual(summary["applied"], ["222"])
        blocklist_document = json.loads(
            (self.state.root / "blocklist.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(blocklist_document["entries"]), 1)
        line = json.loads(
            self.state.backlog_path.read_bytes().splitlines()[1].decode("utf-8")
        )
        self.assertEqual(line["status"], "rejected")

    def test_backup_collision_aborts_with_zero_writes(self) -> None:
        stamp = FIXED_NOW.replace(":", "-")
        backup_path = (
            self.state.root
            / REVIEW_DIR_NAME
            / f"backlog-apply-{stamp}"
            / "backup"
            / "backlog.jsonl"
        )
        backup_path.parent.mkdir(parents=True)
        backup_path.write_bytes(b"pre-existing garbage")
        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("111", "accepted", self.hashes[0])]
            )
        )
        with self.assertRaisesRegex(ReviewConflictError, "refusing to overwrite"):
            apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertEqual(self.state.backlog_path.read_bytes(), self.payload)

    def test_backup_hash_is_verified_before_any_write(self) -> None:
        import tiktok_ingest.review as review_module

        def tampered_backup(destination, data):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"tampered")

        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("111", "accepted", self.hashes[0])]
            )
        )
        with mock.patch.object(
            review_module, "_write_backup_exclusive", tampered_backup
        ):
            with self.assertRaisesRegex(
                ReviewConflictError, "backup verification failed"
            ):
                apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertEqual(self.state.backlog_path.read_bytes(), self.payload)
        self.assertFalse((self.state.root / "blocklist.json").exists())

    def test_post_write_verification_catches_tampered_publication(self) -> None:
        import tiktok_ingest.review as review_module

        real_write = review_module.atomic_write_bytes

        def tampering_write(path, data):
            if Path(path).name == "backlog.jsonl":
                tampered = data.replace(b'"pending"', b'"accepted"', 1)
                return real_write(path, tampered)
            return real_write(path, data)

        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("222", "accepted", self.hashes[1])]
            )
        )
        with mock.patch.object(
            review_module, "atomic_write_bytes", tampering_write
        ):
            with self.assertRaisesRegex(
                ReviewConflictError, "post-write verification FAILED"
            ):
                apply_decisions(self.state, decisions, now=FIXED_NOW)
        # The verified backup exists for recovery, and the partial
        # publication is registered (backlog published but unverified).
        stamp = FIXED_NOW.replace(":", "-")
        backup = (
            self.state.root
            / REVIEW_DIR_NAME
            / f"backlog-apply-{stamp}"
            / "backup"
            / "backlog.jsonl"
        )
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), self.payload)
        partial = json.loads(
            (
                self.state.root
                / REVIEW_DIR_NAME
                / f"backlog-apply-{stamp}"
                / "apply-receipt.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["backlog_published"], True)
        self.assertEqual(partial["blocklist_published"], [])

    def test_receipt_records_backups_and_hashes(self) -> None:
        decisions = parse_decisions_file(
            self._decisions(
                [decision_line("111", "accepted", self.hashes[0])]
            )
        )
        summary = apply_decisions(self.state, decisions, now=FIXED_NOW)
        self.assertEqual(len(summary["backups"]), 1)
        self.assertEqual(summary["backups"][0]["path"].endswith("backlog.jsonl"), True)
        self.assertEqual(
            summary["backups"][0]["sha256"], entry_sha256(self.payload)
        )
        receipt_files = list(
            (self.state.root / REVIEW_DIR_NAME).rglob("apply-receipt.json")
        )
        self.assertEqual(len(receipt_files), 1)
        receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], "tiktok-ingest/backlog-apply-receipt@1")
        self.assertIn("recovery", receipt)


class MixedEndToEndTests(ReviewTestCase):
    def test_decide_plan_apply_flow_mixed_decisions(self) -> None:
        entries = [
            entry_dict("A1", status="pending"),
            entry_dict("B2", status="pending"),
            entry_dict("C3", status="accepted"),
        ]
        payload = write_backlog(self.state, entries)
        raw_lines = payload.splitlines()
        hashes = [entry_sha256(line) for line in raw_lines]

        decisions_file = self.tmp / "flow.jsonl"
        record_decision(
            self.state,
            video_id="A1",
            decision="accepted",
            decisions_file=decisions_file,
            now=FIXED_NOW,
        )
        record_decision(
            self.state,
            video_id="B2",
            decision="rejected",
            decisions_file=decisions_file,
            reason="not a tool",
            now=FIXED_NOW,
        )
        decisions = parse_decisions_file(decisions_file)
        plan = build_plan(self.state, decisions, decisions_file=str(decisions_file))
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual(
            plan["files_to_write"], ["backlog.jsonl", "blocklist.json"]
        )
        summary = apply_decisions(
            self.state, decisions, decisions_file=str(decisions_file), now=FIXED_NOW
        )
        self.assertEqual(summary["applied"], ["A1", "B2"])
        statuses = [
            json.loads(line.decode("utf-8"))["status"]
            for line in self.state.backlog_path.read_bytes().splitlines()
        ]
        self.assertEqual(statuses, ["accepted", "rejected", "accepted"])
        self.assertTrue(Blocklist(self.state).is_blocked("B2"))
        self.assertFalse(Blocklist(self.state).is_blocked("A1"))


class MultilineBacklogIncidentTests(ReviewTestCase):
    def test_backlog_file_with_multiline_json_is_refused(self) -> None:
        # The incident: a file NAMED backlog.jsonl holding multi-line
        # JSON objects concatenated together must fail loudly.
        self.state.ensure_layout()
        self.state.backlog_path.write_bytes(
            b'{\n  "id": "111"\n}\n{\n  "id": "222"\n}\n'
        )
        with self.assertRaisesRegex(ReviewError, "single-line JSON object"):
            list_entries(self.state)


class CliTests(ReviewTestCase):
    def _main(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main(list(argv))
            except SystemExit as exc:  # argparse-level rejections
                code = int(exc.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_list_show_decide_plan_apply(self) -> None:
        payload = write_backlog(
            self.state, [entry_dict("111"), entry_dict("222")]
        )

        code, out, _ = self._main(
            "backlog-list", "--status", "pending", "--state-root", str(self.state.root)
        )
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(document["count"], 2)

        code, out, _ = self._main(
            "backlog-show", "111", "--state-root", str(self.state.root)
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["id"], "111")

        decisions_file = self.tmp / "cli-decisions.jsonl"
        code, out, _ = self._main(
            "backlog-decide",
            "111",
            "--decision",
            "accepted",
            "--decisions-file",
            str(decisions_file),
            "--state-root",
            str(self.state.root),
        )
        self.assertEqual(code, 0)
        self.assertTrue(decisions_file.is_file())

        code, out, _ = self._main(
            "backlog-plan",
            "--decisions-file",
            str(decisions_file),
            "--state-root",
            str(self.state.root),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["conflicts"], [])

        code, out, _ = self._main(
            "backlog-apply",
            "--decisions-file",
            str(decisions_file),
            "--state-root",
            str(self.state.root),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["applied"], ["111"])
        status = json.loads(
            self.state.backlog_path.read_bytes().splitlines()[0].decode("utf-8")
        )["status"]
        self.assertEqual(status, "accepted")
        self.assertNotEqual(
            self.state.backlog_path.read_bytes(), payload  # content changed
        )

    def test_cli_plan_returns_1_on_conflicts(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        decisions_file = write_decisions(
            self.tmp / "stale.jsonl",
            [decision_line("111", "accepted", "d" * 64)],
        )
        code, out, _ = self._main(
            "backlog-plan",
            "--decisions-file",
            str(decisions_file),
            "--state-root",
            str(self.state.root),
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(out)["conflicts"]), 1)

    def test_cli_errors_exit_2_with_message(self) -> None:
        write_backlog(self.state, [entry_dict("111")])
        code, _, err = self._main(
            "backlog-show", "999", "--state-root", str(self.state.root)
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown backlog id", err)

        code, _, err = self._main(
            "backlog-list", "--status", "confirmed", "--state-root", str(self.state.root)
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_new_subcommands_are_declared(self) -> None:
        parser = build_parser()
        commands = set(parser._subparsers._group_actions[0].choices)
        for expected in (
            "backlog-list",
            "backlog-show",
            "backlog-decide",
            "backlog-plan",
            "backlog-apply",
        ):
            self.assertIn(expected, commands)
            # --help smoke: argparse renders help and exits 0 via SystemExit.
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as ctx:
                    parser.parse_args([expected, "--help"])
            self.assertEqual(ctx.exception.code, 0)
            self.assertIn(expected, stdout.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
