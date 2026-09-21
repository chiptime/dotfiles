"""Milestone 4: synthesis ingestion stage (fixture-only, zero network)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tiktok_ingest.contracts import ContractError, JobManifest, StageOutcome
from tiktok_ingest.state import Processed
from tiktok_ingest.synthesis import (
    SYNTHESIS_FILENAME,
    SYNTHESIS_SCHEMA_VERSION,
    SynthesisClassification,
    SynthesisClaim,
    SynthesisDocument,
    SynthesisEntity,
    find_credential_leaks,
    run_synthesis_stage,
    synthesis_fingerprint,
)
from tiktok_ingest.tests.fixtures import (
    FIXED_NOW,
    MEDIA_SHA256,
    VIDEO_ID,
    make_prepared_job,
    sample_synthesis_document,
    write_sanitized_evidence,
)


def _write_json(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


class SynthesisSchemaTests(unittest.TestCase):
    def test_happy_path_document(self) -> None:
        document = SynthesisDocument.from_dict(sample_synthesis_document())
        self.assertEqual(document.classification.root, "tecnología")
        self.assertEqual(document.classification.subgroup, "Cli Agentes")
        self.assertEqual(document.classification.confidence, 0.9)
        self.assertEqual(len(document.claims), 1)
        self.assertEqual(len(document.entities), 2)
        self.assertIsInstance(document.entities[0], SynthesisEntity)
        self.assertEqual(document.entities[1].name, "Plain Entity")
        self.assertEqual(document.claims[0].source, "audio")
        self.assertIsNone(document.claims[0].verdict)
        self.assertIn("frame_001", document.claims[0].evidence)

    def test_metadata_claim_source_is_valid(self) -> None:
        data = sample_synthesis_document()
        data["claims"][0]["source"] = "metadata"
        document = SynthesisDocument.from_dict(data)
        self.assertEqual(document.claims[0].source, "metadata")

    def test_unknown_root_rejected(self) -> None:
        data = sample_synthesis_document()
        data["classification"]["root"] = "herramientas"
        with self.assertRaises(ContractError) as ctx:
            SynthesisDocument.from_dict(data)
        self.assertIn("fixed roots", str(ctx.exception))

    def test_bad_verdict_rejected(self) -> None:
        data = sample_synthesis_document()
        data["claims"][0]["verdict"] = "probably-true"
        with self.assertRaises(ContractError) as ctx:
            SynthesisDocument.from_dict(data)
        self.assertIn("verdict", str(ctx.exception))

    def test_all_five_verdicts_accepted(self) -> None:
        for verdict in (
            "confirmed",
            "overstated",
            "unverifiable",
            "contradicted",
            "unnamed",
        ):
            SynthesisClaim(claim="x", source="visual", verdict=verdict)

    def test_confidence_out_of_range_rejected(self) -> None:
        for bad in (1.5, -0.1, True, "0.9"):
            with self.assertRaises(ContractError):
                SynthesisClassification(
                    root="tecnología", subgroup="x", confidence=bad
                )

    def test_confidence_bounds_accepted(self) -> None:
        SynthesisClassification(root="ruido", subgroup="x", confidence=0)
        SynthesisClassification(root="ruido", subgroup="x", confidence=1)

    def test_missing_required_fields_rejected(self) -> None:
        with self.assertRaises(ContractError):
            SynthesisDocument.from_dict({"classification": None})
        data = sample_synthesis_document()
        del data["fit"]
        with self.assertRaises(ContractError):
            SynthesisDocument.from_dict(data)

    def test_bad_generated_at_rejected(self) -> None:
        data = sample_synthesis_document()
        data["generated_at"] = "not-a-timestamp"
        with self.assertRaises(ContractError):
            SynthesisDocument.from_dict(data)

    def test_bad_claim_source_rejected(self) -> None:
        data = sample_synthesis_document()
        data["claims"][0]["source"] = "vibes"
        with self.assertRaises(ContractError):
            SynthesisDocument.from_dict(data)

    def test_non_string_entity_rejected(self) -> None:
        with self.assertRaises(ContractError):
            SynthesisEntity.from_dict(42)


class CredentialScrubTests(unittest.TestCase):
    def test_clean_document_has_no_leaks(self) -> None:
        self.assertEqual(find_credential_leaks(sample_synthesis_document()), ())

    def test_aws_access_key_detected(self) -> None:
        leaks = find_credential_leaks({"note": "id AKIAIOSFODNN7EXAMPLE"})
        self.assertEqual(leaks, ("aws_access_key_id",))

    def test_bearer_token_detected(self) -> None:
        leaks = find_credential_leaks({"note": "auth Bearer abc.def-ghi"})
        self.assertEqual(leaks, ("bearer_token",))

    def test_password_assignment_detected(self) -> None:
        leaks = find_credential_leaks({"note": "password=hunter2"})
        self.assertEqual(leaks, ("password_assignment",))

    def test_api_key_and_secret_detected(self) -> None:
        self.assertEqual(
            find_credential_leaks({"a": "api_key=xyz"}),
            ("api_key_assignment",),
        )
        self.assertEqual(
            find_credential_leaks({"a": "client_secret: xyz"}),
            ("secret_assignment",),
        )

    def test_github_and_slack_tokens_detected(self) -> None:
        self.assertIn(
            "github_token",
            find_credential_leaks({"t": "ghp_" + "a" * 36}),
        )
        self.assertIn(
            "slack_token", find_credential_leaks({"t": "xoxb-123456-abcdef"})
        )

    def test_nested_leak_detected(self) -> None:
        leaks = find_credential_leaks(
            {"claims": [{"note": "token=super-secret-value"}]}
        )
        self.assertIn("secret_assignment", leaks)


class SynthesisStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state, self.run_dir = make_prepared_job(self.tmp)
        write_sanitized_evidence(self.run_dir)
        self.source = _write_json(
            self.tmp / "synthesis.json", sample_synthesis_document()
        )

    def _manifest(self) -> JobManifest:
        return JobManifest.from_dict(
            json.loads((self.run_dir / "meta.json").read_text(encoding="utf-8"))
        )

    def test_blocked_without_any_source(self) -> None:
        outcome = run_synthesis_stage(VIDEO_ID, state=self.state)
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("supply --from-file", outcome.reason)
        self.assertIn("no text-model credential configured", outcome.reason)
        self.assertFalse((self.run_dir / SYNTHESIS_FILENAME).exists())

    def test_missing_audio_md_is_explicit_failure(self) -> None:
        (self.run_dir / "audio.md").unlink()
        outcome = run_synthesis_stage(
            VIDEO_ID, state=self.state, from_file=self.source
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("audio.md", outcome.reason)
        self.assertFalse((self.run_dir / SYNTHESIS_FILENAME).exists())

    def test_missing_video_md_is_explicit_failure(self) -> None:
        write_sanitized_evidence(self.run_dir)  # both present
        (self.run_dir / "video.md").unlink()
        outcome = run_synthesis_stage(
            VIDEO_ID, state=self.state, from_file=self.source
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("video.md", outcome.reason)

    def test_both_sources_rejected(self) -> None:
        outcome = run_synthesis_stage(
            VIDEO_ID,
            state=self.state,
            from_file=self.source,
            document_fn=sample_synthesis_document,
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("exactly one", outcome.reason)

    def test_missing_source_file_is_failure(self) -> None:
        outcome = run_synthesis_stage(
            VIDEO_ID, state=self.state, from_file=self.tmp / "nope.json"
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("does not exist", outcome.reason)

    def test_happy_path_persists_stage_and_artifact(self) -> None:
        outcome = run_synthesis_stage(VIDEO_ID, state=self.state, from_file=self.source)
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        self.assertFalse(outcome.reused)
        self.assertEqual(outcome.claims, 1)

        persisted = json.loads(
            (self.run_dir / SYNTHESIS_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["fit"], "adjacent")
        self.assertEqual(persisted["model"], "operator-curated-fixture")

        manifest = self._manifest()
        record = manifest.stages["synthesis"]
        self.assertEqual(record.result.outcome, StageOutcome.COMPLETE)

        expected_fp = synthesis_fingerprint(
            MEDIA_SHA256,
            hashlib.sha256(self.source.read_bytes()).hexdigest(),
        )
        self.assertEqual(record.fingerprint, expected_fp)
        self.assertEqual(
            record.fingerprint["versions"]["schema_version"],
            SYNTHESIS_SCHEMA_VERSION,
        )
        self.assertEqual(record.fingerprint["input_sha256"], MEDIA_SHA256)

        entry = Processed(self.state).get(VIDEO_ID)
        assert entry is not None
        self.assertEqual(entry.stage_fingerprints["synthesis"], expected_fp)
        self.assertIn(
            f"runs/run-1/{VIDEO_ID}/{SYNTHESIS_FILENAME}", entry.artifacts
        )

    def test_document_fn_injection(self) -> None:
        outcome = run_synthesis_stage(
            VIDEO_ID, state=self.state, document_fn=sample_synthesis_document
        )
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)

    def test_schema_rejection_is_failed_outcome_with_record(self) -> None:
        data = sample_synthesis_document()
        data["classification"]["root"] = "herramientas"
        bad = _write_json(self.tmp / "bad.json", data)
        outcome = run_synthesis_stage(VIDEO_ID, state=self.state, from_file=bad)
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("schema validation", outcome.reason)
        self.assertIn("fixed roots", outcome.reason)
        self.assertFalse((self.run_dir / SYNTHESIS_FILENAME).exists())
        self.assertEqual(
            self._manifest().stages["synthesis"].result.outcome,
            StageOutcome.FAILED,
        )

    def test_scrub_failure_persists_nothing(self) -> None:
        data = sample_synthesis_document()
        data["notes_extra"] = "password=hunter2"
        leaky = _write_json(self.tmp / "leaky.json", data)
        before = (self.run_dir / "meta.json").read_text(encoding="utf-8")
        outcome = run_synthesis_stage(VIDEO_ID, state=self.state, from_file=leaky)
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("credential scrub", outcome.reason)
        self.assertIn("password_assignment", outcome.reason)
        self.assertNotIn("hunter2", outcome.reason)
        # Nothing persisted: no artifact, no stage record, processed entry
        # unchanged.
        self.assertFalse((self.run_dir / SYNTHESIS_FILENAME).exists())
        self.assertEqual(
            (self.run_dir / "meta.json").read_text(encoding="utf-8"), before
        )
        entry = Processed(self.state).get(VIDEO_ID)
        assert entry is not None
        self.assertNotIn("synthesis", entry.stage_fingerprints)

    def test_resume_reuses_matching_stage(self) -> None:
        first = run_synthesis_stage(VIDEO_ID, state=self.state, from_file=self.source)
        self.assertEqual(first.outcome, StageOutcome.COMPLETE.value)
        second = run_synthesis_stage(VIDEO_ID, state=self.state, from_file=self.source)
        self.assertEqual(second.outcome, StageOutcome.COMPLETE.value)
        self.assertTrue(second.reused)

    def test_changed_source_invalidates_stage(self) -> None:
        run_synthesis_stage(VIDEO_ID, state=self.state, from_file=self.source)
        data = sample_synthesis_document()
        data["fit"] = "core"
        changed = _write_json(self.tmp / "changed.json", data)
        outcome = run_synthesis_stage(VIDEO_ID, state=self.state, from_file=changed)
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        self.assertFalse(outcome.reused)

    def test_force_recomputes(self) -> None:
        run_synthesis_stage(VIDEO_ID, state=self.state, from_file=self.source)
        forced = run_synthesis_stage(
            VIDEO_ID, state=self.state, from_file=self.source, force=True
        )
        self.assertFalse(forced.reused)

    def test_blocklist_blocks_stage(self) -> None:
        from tiktok_ingest.contracts import BlocklistEntry
        from tiktok_ingest.state import Blocklist

        Blocklist(self.state).reject(
            BlocklistEntry(
                video_id=VIDEO_ID,
                rejected_at=FIXED_NOW,
                reason="operator rejected",
            )
        )
        outcome = run_synthesis_stage(
            VIDEO_ID, state=self.state, from_file=self.source
        )
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)

    def test_unknown_video_id_fails(self) -> None:
        outcome = run_synthesis_stage(
            "4242424242424242424", state=self.state, from_file=self.source
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("no fetch record", outcome.reason)

    def test_invalid_json_is_failure(self) -> None:
        bad = self.tmp / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        outcome = run_synthesis_stage(VIDEO_ID, state=self.state, from_file=bad)
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("not valid UTF-8 JSON", outcome.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
