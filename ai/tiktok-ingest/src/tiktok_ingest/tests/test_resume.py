"""Fixture-only resume: reuse, downstream invalidation, no duplicate emits."""

from __future__ import annotations

import copy
import tempfile
import unittest
from typing import Any

from tiktok_ingest.tests import fixtures
from tiktok_ingest.contracts import STAGE_ORDER
from tiktok_ingest.resume import (
    downstream_of,
    emit_backlog_entry,
    plan_resume,
    plan_resume_for_manifest,
)
from tiktok_ingest.state import Backlog


class ResumePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.recorded = fixtures.sample_fingerprints()

    def test_unchanged_fingerprints_reuse_every_completed_stage(self) -> None:
        plan = plan_resume(self.recorded, copy.deepcopy(self.recorded))
        self.assertEqual(plan.reusable, STAGE_ORDER)
        self.assertEqual(plan.invalidated, ())
        self.assertTrue(all(d.action == "reuse" for d in plan.decisions))

    def test_model_version_change_invalidates_only_downstream(self) -> None:
        current = copy.deepcopy(self.recorded)
        # Change the vision model version: vision and everything after it
        # must recompute; prepare is untouched.
        current["vision"]["versions"]["vision_model"] = "qwen3.8:27b"
        plan = plan_resume(self.recorded, current)
        self.assertEqual(plan.reusable, ("prepare",))
        self.assertEqual(
            plan.invalidated, ("vision", "audio", "synthesis", "verify", "emit")
        )
        self.assertIn("fingerprint changed", plan.decisions[1].reason)

    def test_input_hash_change_at_prepare_invalidates_everything(self) -> None:
        current = copy.deepcopy(self.recorded)
        current["prepare"]["input_sha256"] = fixtures.content_hash("re-fetched media")
        plan = plan_resume(self.recorded, current)
        self.assertEqual(plan.reusable, ())
        self.assertEqual(plan.invalidated, STAGE_ORDER)

    def test_missing_current_fingerprint_forces_recompute(self) -> None:
        current = {stage: fp for stage, fp in self.recorded.items() if stage != "audio"}
        plan = plan_resume(self.recorded, current)
        self.assertEqual(plan.reusable, ("prepare", "vision"))
        self.assertEqual(
            plan.invalidated, ("audio", "synthesis", "verify", "emit")
        )
        self.assertIn(
            "no fingerprint", next(d.reason for d in plan.decisions if d.stage == "audio")
        )

    def test_incomplete_upstream_stage_blocks_downstream_reuse(self) -> None:
        # audio never completed: only prepare and vision can be reused even
        # though later fingerprints match.
        recorded = {
            stage: fp for stage, fp in self.recorded.items() if stage != "audio"
        }
        plan = plan_resume(recorded, copy.deepcopy(self.recorded))
        self.assertEqual(plan.reusable, ("prepare", "vision"))
        self.assertEqual(
            plan.invalidated, ("audio", "synthesis", "verify", "emit")
        )

    def test_downstream_of_matches_canonical_order(self) -> None:
        self.assertEqual(downstream_of("prepare"), STAGE_ORDER[1:])
        self.assertEqual(downstream_of("verify"), ("emit",))
        self.assertEqual(downstream_of("emit"), ())
        with self.assertRaises(ValueError):
            downstream_of("not-a-stage")

    def test_plan_resume_for_manifest_uses_completed_stages_only(self) -> None:
        manifest = fixtures.sample_manifest()
        plan = plan_resume_for_manifest(manifest, fixtures.sample_fingerprints())
        self.assertEqual(plan.reusable, STAGE_ORDER)

        current = fixtures.sample_fingerprints()
        current["verify"]["versions"]["verifier"] = "opus-5"
        plan = plan_resume_for_manifest(manifest, current)
        self.assertEqual(plan.reusable, ("prepare", "vision", "audio", "synthesis"))
        self.assertEqual(plan.invalidated, ("verify", "emit"))


class ResumeEmitIdempotenceTests(unittest.TestCase):
    """A resumed run can never create duplicate backlog entries."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = fixtures.make_state_root(self._tmp.name)
        self.backlog = Backlog(self.state)

    def test_resume_emit_is_idempotent_by_stable_video_id(self) -> None:
        entry = fixtures.sample_backlog_entry()
        first = emit_backlog_entry(self.backlog, entry)
        second = emit_backlog_entry(self.backlog, entry)
        self.assertEqual(first, "appended")
        self.assertEqual(second, "skipped_duplicate")
        self.assertEqual(len(self.backlog.entries()), 1)
        self.assertEqual(self.backlog.ids(), {entry.id})

    def test_full_fixture_resume_cycle(self) -> None:
        """Fixture state on disk: reuse valid stages, then re-emit safely."""
        entry = fixtures.sample_backlog_entry()
        emit_backlog_entry(self.backlog, entry)

        manifest = fixtures.sample_manifest()
        # Persist processed state and manifest into the fixture state root.
        from tiktok_ingest.contracts import ProcessedEntry
        from tiktok_ingest.state import Processed

        processed = Processed(self.state)
        processed.record(
            ProcessedEntry(
                video_id=manifest.video_id,
                media_sha256=manifest.media_sha256,
                completed_at=fixtures.FIXED_NOW,
                stage_fingerprints=fixtures.sample_fingerprints(),
            )
        )
        run_dir = self.state.run_dir("2026-09-18T12-00Z", manifest.video_id)
        manifest_path = run_dir / "meta.json"
        from tiktok_ingest.state import atomic_write_json

        atomic_write_json(manifest_path, manifest)

        # Second unchanged run: everything reused, zero recompute.
        current: dict[str, Any] = fixtures.sample_fingerprints()
        plan = plan_resume_for_manifest(manifest, current)
        self.assertEqual(plan.invalidated, ())

        # Re-emitting after resume must not duplicate the backlog entry.
        emit_backlog_entry(self.backlog, entry)
        self.assertEqual(len(self.backlog.entries()), 1)
        self.assertTrue(manifest_path.exists())

        # A changed upstream fingerprint invalidates only downstream stages.
        current["vision"]["versions"]["vision_model"] = "qwen3.8:27b"
        plan = plan_resume_for_manifest(manifest, current)
        self.assertEqual(plan.reusable, ("prepare",))
        self.assertEqual(
            plan.invalidated, ("vision", "audio", "synthesis", "verify", "emit")
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
