"""Contract tests: manifest round-trip and required-field validation."""

from __future__ import annotations

import dataclasses
import json
import unittest

from tiktok_ingest.tests import fixtures
from tiktok_ingest.contracts import (
    BacklogEntry,
    Classification,
    Claim,
    ContractError,
    JobManifest,
    ProvenanceHeader,
    StageOutcome,
    StageResult,
    ToolModelRef,
)


class ManifestRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self) -> None:
        manifest = fixtures.sample_manifest()
        # Force a real JSON boundary so serialization is actually exercised.
        payload = json.loads(json.dumps(manifest.to_dict()))
        restored = JobManifest.from_dict(payload)
        self.assertEqual(manifest.to_dict(), restored.to_dict())

    def test_round_trip_keeps_stage_outcomes_and_reasons(self) -> None:
        restored = JobManifest.from_dict(fixtures.sample_manifest().to_dict())
        for stage, record in restored.stages.items():
            self.assertIs(record.result.outcome, StageOutcome.COMPLETE)
            self.assertTrue(record.result.reason)
            self.assertEqual(
                record.fingerprint, fixtures.sample_fingerprints()[stage]
            )

    def test_missing_required_field_is_rejected(self) -> None:
        for removed in ("video_id", "media_sha256", "created_at", "updated_at"):
            with self.subTest(field=removed):
                data = fixtures.sample_manifest().to_dict()
                del data[removed]
                with self.assertRaises(ContractError):
                    JobManifest.from_dict(data)

    def test_invalid_media_hash_is_rejected(self) -> None:
        data = fixtures.sample_manifest().to_dict()
        data["media_sha256"] = "not-a-hash"
        with self.assertRaises(ContractError):
            JobManifest.from_dict(data)

    def test_unknown_stage_name_is_rejected(self) -> None:
        data = fixtures.sample_manifest().to_dict()
        data["stages"]["transmogrify"] = data["stages"]["vision"]
        data["stages"]["transmogrify"]["stage"] = "transmogrify"
        with self.assertRaises(ContractError):
            JobManifest.from_dict(data)

    def test_stage_result_requires_reason(self) -> None:
        with self.assertRaises(ContractError):
            StageResult(outcome=StageOutcome.COMPLETE, reason="")
        with self.assertRaises(ContractError):
            StageResult(outcome="made-up-outcome", reason="something")


class ClassificationAndBacklogTests(unittest.TestCase):
    def test_unknown_classification_is_null_with_reason(self) -> None:
        classification = Classification(root=None, unknown_reason="no usable evidence")
        self.assertIsNone(classification.root)
        self.assertEqual(classification.to_dict()["root"], None)

    def test_null_classification_without_reason_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            Classification(root=None)

    def test_sixth_taxonomy_root_is_never_allowed(self) -> None:
        with self.assertRaises(ContractError):
            Classification(root="tecnología-2", subgroup="nuevo")

    def test_backlog_entry_null_classification_requires_reason(self) -> None:
        base = fixtures.sample_backlog_entry().to_dict()
        entry = BacklogEntry.from_dict({**base, "classification": None})
        self.assertTrue(entry.unknown_classification_reason)
        with self.assertRaises(ContractError):
            BacklogEntry.from_dict(
                {**base, "classification": None, "unknown_classification_reason": None}
            )

    def test_backlog_entry_validates_status_and_claims(self) -> None:
        base = fixtures.sample_backlog_entry().to_dict()
        with self.assertRaises(ContractError):
            BacklogEntry.from_dict({**base, "status": "auto-accepted"})
        with self.assertRaises(ContractError):
            BacklogEntry.from_dict(
                {**base, "claims": [{"claim": "x", "source": "vibes"}]}
            )
        claim = Claim.from_dict({"claim": "90% faster", "source": "audio", "verdict": "overstated"})
        self.assertEqual(claim.verdict, "overstated")

    def test_backlog_round_trip(self) -> None:
        entry = fixtures.sample_backlog_entry()
        restored = BacklogEntry.from_dict(json.loads(json.dumps(entry.to_dict())))
        self.assertEqual(entry.to_dict(), restored.to_dict())


class ProvenanceHeaderTests(unittest.TestCase):
    def test_video_header_round_trip(self) -> None:
        header = ProvenanceHeader(
            artifact="video.md",
            video_id="7683273567433248022",
            media_sha256=fixtures.MEDIA_SHA256,
            generator=ToolModelRef(
                tool="ollama", version="0.34.1", model="qwen3.5:9b"
            ),
            generated_at=fixtures.FIXED_NOW,
            entries=[
                {
                    "kind": "frame",
                    "id": "frame_00",
                    "sha256": fixtures.content_hash("frame-0"),
                    "timestamp_seconds": 0.0,
                    "roi": None,
                    "resize": None,
                }
            ],
            notes=["fixture header"],
        )
        restored = ProvenanceHeader.from_dict(json.loads(json.dumps(header.to_dict())))
        self.assertEqual(header.to_dict(), restored.to_dict())

    def test_header_rejects_unknown_artifact_and_bad_entries(self) -> None:
        common = dict(
            video_id="v1",
            media_sha256=fixtures.MEDIA_SHA256,
            generator=ToolModelRef(tool="t", version="1"),
            generated_at=fixtures.FIXED_NOW,
        )
        with self.assertRaises(ContractError):
            ProvenanceHeader(artifact="frames.bin", **common)
        with self.assertRaises(ContractError):
            ProvenanceHeader(
                artifact="audio.md", entries=[{"id": "seg-0"}], **common
            )


class InventoryAndProcessedTests(unittest.TestCase):
    def test_inventory_entry_round_trip_and_validation(self) -> None:
        from tiktok_ingest.contracts import InventoryEntry

        entry = InventoryEntry(
            source="tiktok",
            stable_id="7683273567433248022",
            canonical_url="https://www.tiktok.com/@mralekai/video/7683273567433248022",
            input_origin="collection",
            discovered_at=fixtures.FIXED_NOW,
            collections=("favourites",),
            declared_count=28,
            observed_count=22,
            completeness="incomplete",
        )
        restored = InventoryEntry.from_dict(entry.to_dict())
        self.assertEqual(entry, restored)
        with self.assertRaises(ContractError):
            dataclasses.replace(entry, completeness="trust-me")

    def test_processed_entry_round_trip(self) -> None:
        from tiktok_ingest.contracts import ProcessedEntry

        entry = ProcessedEntry(
            video_id="7683273567433248022",
            media_sha256=fixtures.MEDIA_SHA256,
            completed_at=fixtures.FIXED_NOW,
            stage_fingerprints=fixtures.sample_fingerprints(),
            artifacts=["runs/r1/7683273567433248022/video.md"],
        )
        restored = ProcessedEntry.from_dict(entry.to_dict())
        self.assertEqual(entry.to_dict(), restored.to_dict())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
