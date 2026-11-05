"""collection-run tests: every external boundary is a double.

Covers the Phase 0 application layer end to end with fakes: the
browser controller double, stage doubles that seed REAL durable state
(so the product's fingerprints drive resume), the scripted/recording
consent provider and the fake text-API transport. Zero network, zero
browser, zero GPU, zero containers.

The TWO-decision authorization contract proven here (user-approved
2026-09-30): ONE browser grant naming the readiness checkpoint and the
snapshot/audit writes; a press-Enter readiness checkpoint that is NOT
a yes/no authorization; ONE whole-current-run plan grant over the
FROZEN inventory (exact ids/URLs/reuse states, batches of <= 5, each
id attempted exactly once, synthesis endpoint+model named upfront,
verification deferred); and ONE grouped
verification authorization showing every exact validated candidate
URL. Stage windows covered by the plan are discharged by the run
authorization wrapper and audited as covered — never fabricated human
yeses. Genuinely new scope (unknown kinds, excess ids, unapproved
destinations) is forwarded to the human and never auto-granted.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import collection_run
from tiktok_ingest.collection import (
    CollectionScanStore,
    extract_page_items,
    merge_observation,
    parse_collection_url,
    sync_inventory_from_scan,
)
from tiktok_ingest.contracts import (
    JobManifest,
    StageOutcome,
    StageRecord,
    StageResult,
)
from tiktok_ingest.guided import (
    ConsentWindow,
    ConsoleConsentProvider,
    StageFunctions,
)
from tiktok_ingest.state import Backlog, StateRoot
from tiktok_ingest.synthesis_api import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
)
from tiktok_ingest.tests.fixtures import (
    FIXED_NOW,
    MEDIA_SHA256,
    sample_synthesis_document,
    write_sanitized_evidence,
)
from tiktok_ingest.tests.test_guided import fake_id, seed_job

COLLECTION_URL = "https://www.tiktok.com/@fixture_author/collection/run-col"
REF = parse_collection_url(COLLECTION_URL)

# The only yes/no windows a HUMAN sees in one invocation of the new
# contract (the grouped verification window only appears when some id
# reached a validated synthesis document).
HUMAN_KINDS = ["browser", "run-plan", "verify"]


def seed_scan_for_run(state: StateRoot, video_ns: list[int]) -> None:
    """Persist scan state for THIS collection through the real merge path."""
    links = "".join(
        f'<a href="/@fixture_author/video/{fake_id(n)}">item</a>'
        for n in video_ns
    )
    html = (
        "<!DOCTYPE html><html><body>"
        f'<div data-testid="collection-container">{links}</div>'
        "</body></html>"
    )
    observation = extract_page_items(html, "data-testid", "collection-container")
    merged = merge_observation(
        None,
        REF,
        observation,
        declared_count=len(video_ns),
        end_evidence="fixture end-of-list marker observed",
        stop_reason=None,
        captured_at=FIXED_NOW,
        container_attr="data-testid",
        container_value="collection-container",
    )
    CollectionScanStore(state).save(merged)
    sync_inventory_from_scan(state, merged)


API_ENV = {
    ENV_BASE_URL: "https://api.example.com/v1",
    ENV_API_KEY: "sk-fixture-key",
    ENV_MODEL: "glm-5.3-flash",
}


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FakeOutcome:
    outcome: str
    reason: str = "fixture"
    reused: bool = False


class FakeBrowserController:
    """Browser double: captures a fixture page, records calls."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.calls: list[str] = []

    def open(self, url: str, profile_dir: Path) -> None:
        self.calls.append(f"open:{url}")

    def current_html(self) -> str:
        self.calls.append("content")
        return self.html

    def close(self) -> None:
        self.calls.append("close")


class FakeApiTransport:
    """Text-API double: scripted results, records outbound requests."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.requests: list[Any] = []

    def __call__(self, request: Any, *, timeout: float):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeApiResponse:
    def __init__(self, document: dict[str, Any]) -> None:
        self.status = 200
        self._body = json.dumps(
            {"choices": [{"message": {"content": json.dumps(document)}}]}
        ).encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "FakeApiResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class RecordingConsent:
    """Human-side provider double for the TWO-decision contract.

    Records every yes/no window it is asked (only browser, run-plan,
    verify and genuinely-extra-scope questions should ever arrive) and
    every readiness checkpoint prompt (which is NOT a window).
    """

    def __init__(self, deny: set[str] | None = None, *, ready: bool = True) -> None:
        self.deny = deny or set()
        self.ready = ready
        self.windows: list[ConsentWindow] = []
        self.readiness_prompts: list[str] = []

    def ask(self, window: ConsentWindow) -> bool:
        self.windows.append(window)
        return window.kind not in self.deny

    def await_readiness(self, prompt: str) -> bool:
        self.readiness_prompts.append(prompt)
        return self.ready

    @property
    def kinds(self) -> list[str]:
        return [w.kind for w in self.windows]

    def of(self, kind: str) -> list[ConsentWindow]:
        return [w for w in self.windows if w.kind == kind]


class SeedingConsent(RecordingConsent):
    """RecordingConsent double that also accepts dashboard seeding.

    Mirrors the optional ``seed_prior_state`` hook of the curses
    provider so the resume path can be asserted without a TTY.
    """

    def __init__(self, deny: set[str] | None = None, *, ready: bool = True) -> None:
        super().__init__(deny, ready=ready)
        self.seeded: dict[str, dict[str, str]] = {}

    def seed_prior_state(self, items) -> None:
        for video_id, stages in items:
            self.seeded[video_id] = dict(stages)


def page_html(items: int) -> str:
    # Verified live shape: the collection container is marked with
    # data-e2e=collection-item-list (matches the browser defaults).
    links = "".join(
        f'<a href="/@fixture_author/video/{fake_id(n)}">item</a>'
        for n in range(items)
    )
    return (
        "<!DOCTYPE html><html><body>"
        f'<div data-e2e="collection-item-list">{links}</div>'
        "</body></html>"
    )


def mark_stage(state: StateRoot, video_id: str, stage: str) -> None:
    """Append a complete stage record + fingerprint to REAL state."""
    entry = state.processed_path
    assert entry is not None
    from tiktok_ingest.state import Processed

    processed = Processed(state).get(video_id)
    assert processed is not None
    run_dir = state.root / processed.artifacts[0]
    manifest_path = run_dir / "meta.json"
    manifest = JobManifest.from_dict(json.loads(manifest_path.read_text()))
    if stage == "fetch":
        manifest.extraction = {"outcome": "complete", "reason": "fixture"}
    else:
        manifest.stages[stage] = StageRecord(
            stage=stage,
            result=StageResult(outcome=StageOutcome.COMPLETE, reason="fixture"),
            fingerprint={"stage": stage, "input_sha256": MEDIA_SHA256},
            input_sha256=MEDIA_SHA256,
        )
    manifest.updated_at = FIXED_NOW
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))
    processed.stage_fingerprints[stage] = {
        "stage": stage,
        "input_sha256": MEDIA_SHA256,
    }
    Processed(state).record(processed)


class CollectionStages:
    """Stage doubles that SEED REAL durable state per completed stage.

    fetch/prepare/vision/audio create the state the product's own
    resume machinery reads; synthesize is NOT doubled here (the
    automatic API hook routes through the REAL ingestion stage inside
    guided); verify optionally emits a REAL pending backlog entry.
    """

    def __init__(
        self,
        *,
        whisper_healthy: bool = True,
        verify_emits_pending: bool = True,
        fetch_fails: bool = False,
        vision_raises: BaseException | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.whisper_healthy = whisper_healthy
        self.verify_emits_pending = verify_emits_pending
        self.fetch_fails = fetch_fails
        self.vision_raises = vision_raises

    def functions(self) -> StageFunctions:
        return StageFunctions(
            fetch=self.do_fetch,
            prepare=self.do_prepare,
            vision=self.do_vision,
            audio=self.do_audio,
            synthesize=self.do_synthesize_unreachable,
            verify=self.do_verify,
            whisper_health=self.do_whisper_health,
        )

    # -- stage doubles (seed REAL state) --------------------------------

    def do_fetch(self, url: str, state: StateRoot) -> FakeOutcome:
        video_id = url.rsplit("/", 1)[-1]
        self.calls.append(f"fetch:{video_id}")
        if self.fetch_fails:
            return FakeOutcome("failed", "fixture fetch failure")
        seed_job(
            state,
            video_id,
            complete=(),
            fingerprints=(),
            canonical_url=url,
        )
        entry_dir = state.run_dir(f"run-{video_id[-6:]}", video_id)
        mark_stage(state, video_id, "fetch")
        assert entry_dir is not None
        return FakeOutcome("complete")

    def do_prepare(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"prepare:{video_id}")
        mark_stage(state, video_id, "prepare")
        return FakeOutcome("complete")

    def do_vision(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"vision:{video_id}")
        if self.vision_raises is not None:
            raise self.vision_raises
        mark_stage(state, video_id, "vision")
        write_sanitized_evidence(
            _run_dir_of(state, video_id), include_audio=False
        )
        return FakeOutcome("complete")

    def do_audio(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"audio:{video_id}")
        mark_stage(state, video_id, "audio")
        run_dir = _run_dir_of(state, video_id)
        if not (run_dir / "audio.md").exists():
            write_sanitized_evidence(run_dir)
        return FakeOutcome("complete")

    def do_synthesize_unreachable(
        self, video_id: str, state: StateRoot, from_file: Path | None
    ) -> Any:
        # collection-run always uses the automatic API hook; the plain
        # synthesize seam must never be reached (no documents supplied).
        raise AssertionError(
            "StageFunctions.synthesize must not be called by collection-run "
            "(the automatic synthesis hook owns this phase)"
        )

    def do_verify(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"verify:{video_id}")
        from tiktok_ingest.contracts import BacklogEntry
        from tiktok_ingest.resume import emit_backlog_entry
        from tiktok_ingest.state import Processed

        if self.verify_emits_pending:
            emit_backlog_entry(
                Backlog(state),
                BacklogEntry(
                    id=video_id,
                    url=f"https://www.tiktok.com/@fixture-author/video/{video_id}",
                    ingested_at=FIXED_NOW,
                    status="pending",
                    classification=None,
                    unknown_classification_reason="fixture: collection-run emission",
                    entities=[],
                    claims=[],
                ),
            )
        # Record the verify/emit durable state exactly like the real
        # stage would, so classification flips to processed_complete.
        processed = Processed(state).get(video_id)
        assert processed is not None
        processed.stage_fingerprints["verify"] = {
            "stage": "verify",
            "input_sha256": MEDIA_SHA256,
        }
        processed.stage_fingerprints["emit"] = {"stage": "emit"}
        Processed(state).record(processed)
        mark_stage(state, video_id, "verify")
        return FakeOutcome("complete")

    # -- service health double -------------------------------------------

    def do_whisper_health(self) -> tuple[bool, str]:
        self.calls.append("whisper-health-check")
        if self.whisper_healthy:
            return True, "healthy"
        return False, "llm-hub-whisper health endpoint unreachable: fixture"


def _run_dir_of(state: StateRoot, video_id: str) -> Path:
    from tiktok_ingest.state import Processed

    entry = Processed(state).get(video_id)
    assert entry is not None and entry.artifacts
    return state.root / entry.artifacts[0]


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


class CollectionRunTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()
        self.profile_base = Path(self.tmp.name) / "profiles"
        self.profile_base.mkdir()
        self.messages: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_collection(
        self,
        stages: CollectionStages,
        consent: RecordingConsent,
        *,
        browser_html: str | None = "",
        transport_results: list[Any] | None = None,
        env: dict[str, str] | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        shared_trace: bool = False,
        rescan: bool = True,
        retry_failed: int = 0,
    ) -> tuple[int, dict[str, Any], FakeApiTransport | None]:
        controller = FakeBrowserController(
            browser_html if browser_html else page_html(3)
        )
        transport = FakeApiTransport(
            transport_results
            if transport_results is not None
            else [FakeApiResponse(sample_synthesis_document()) for _ in range(50)]
        )
        if shared_trace:
            original_transport = transport

            def traced_transport(request: Any, *, timeout: float) -> Any:
                stages.calls.append("synthesis-api")
                return original_transport(request, timeout=timeout)

            api_transport = traced_transport
        else:
            api_transport = transport
        code, report = collection_run.run_collection(
            collection_url=COLLECTION_URL,
            state=self.state,
            consent=consent,
            out=self.messages.append,
            dry_run=dry_run,
            limit=limit,
            browser_controller=controller,
            browser_profile_base=self.profile_base,
            api_transport=api_transport,
            env=env if env is not None else API_ENV,
            stages=stages.functions(),
            rescan=rescan,
            retry_failed=retry_failed,
        )
        return code, report, transport

    # -- report shorthands ------------------------------------------------

    def stage_windows(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        return report["run_authorization"]["stage_windows"]

    def covered_kinds(self, report: dict[str, Any]) -> list[str]:
        return [
            entry["window"]["kind"]
            for entry in self.stage_windows(report)
            if entry["decision"] == "covered_by_run_plan"
        ]

    def human_decisions(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        return report["run_authorization"]["human_decisions"]


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


class TestDryRun(CollectionRunTestCase):
    def test_dry_run_is_zero_effect(self) -> None:
        seed_scan_for_run(self.state, [1, 2])
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, transport = self.run_collection(
            stages, consent, dry_run=True
        )
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(consent.kinds, [], "dry run never asks")
        self.assertEqual(consent.readiness_prompts, [], "dry run never waits")
        self.assertEqual(stages.calls, [], "dry run never stages")
        self.assertEqual(transport.requests, [], "dry run never calls the API")
        self.assertFalse(
            (self.state.root / "backlog.jsonl").exists(),
            "dry run writes nothing",
        )

    def test_dry_run_shows_selection_and_actions(self) -> None:
        seed_scan_for_run(self.state, [1, 2, 3])
        code, report, _ = self.run_collection(
            CollectionStages(), RecordingConsent(), dry_run=True
        )
        self.assertEqual(code, 0)
        plan = report["plan"]
        self.assertTrue(plan["scan_state_present"])
        self.assertEqual(
            plan["first_batch_would_be"], [fake_id(1), fake_id(2), fake_id(3)]
        )
        self.assertEqual(report["preflight"]["text_api_configured"], True)
        self.assertIn("zero browser", report["notes"][0])


# --------------------------------------------------------------------------
# Happy path: the TWO-decision contract end to end
# --------------------------------------------------------------------------


class TestHappyPath(CollectionRunTestCase):
    def test_full_journey_asks_exactly_two_decisions_plus_grouped_verify(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, transport = self.run_collection(stages, consent)
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        self.assertEqual(len(report["batches"]), 1)
        self.assertEqual(report["browser"]["observed_items"], 3)
        backlog = Backlog(self.state).entries()
        self.assertEqual(len(backlog), 3)
        self.assertTrue(all(entry["status"] == "pending" for entry in backlog))
        # One outbound API request per id, none extra.
        self.assertEqual(len(transport.requests), 3)
        # The ONLY human yes/no windows: browser -> run-plan -> verify.
        self.assertEqual(consent.kinds, HUMAN_KINDS)
        # The readiness checkpoint is a separate, non-authorizing signal.
        self.assertEqual(len(consent.readiness_prompts), 1)
        self.assertIn("readiness", consent.readiness_prompts[0].lower())
        self.assertNotIn(
            "browser-confirm",
            consent.kinds + self.covered_kinds(report),
            "readiness must not be a consent window",
        )
        # Stage windows were discharged BY THE PLAN, not by a human.
        self.assertEqual(
            self.covered_kinds(report),
            [
                "tanda",
                "fetch",
                "prepare",
                "gpu",
                "gpu",
                "synthesis-api",
                "synthesis-api",
                "synthesis-api",
            ],
        )
        self.assertIn("review_next", report)
        self.assertEqual(report["backlog_pending_view"], 3)
        # The grouped verification phase verified every id once.
        self.assertEqual(report["verification"]["ids"], [fake_id(n) for n in range(3)])
        self.assertTrue(report["verification"]["granted"])
        self.assertTrue(
            all(
                rec["outcome"] == "complete"
                for rec in report["verification"]["outcomes"].values()
            )
        )

    def test_second_run_reuses_everything_and_asks_no_empty_plan(self) -> None:
        stages = CollectionStages()
        code1, report1, _ = self.run_collection(stages, RecordingConsent())
        self.assertEqual(code1, 0)
        first_stage_calls = list(stages.calls)
        stages2 = CollectionStages()
        consent2 = RecordingConsent()
        code2, report2, _ = self.run_collection(stages2, consent2)
        self.assertEqual(code2, 0, report2.get("hard_stop_reason"))
        self.assertEqual(report2["batches"], [], "nothing eligible remains")
        # Nothing to authorize: only the browser grant is asked again —
        # the first run's plan grant NEVER carries into this invocation.
        self.assertEqual(consent2.kinds, ["browser"])
        self.assertIsNone(report2["run_plan"], "no plan window when nothing is eligible")
        self.assertEqual(
            [c for c in stages2.calls if c.startswith(("fetch:", "vision:", "audio:"))],
            [],
            "no download or audiovisual inference on rerun",
        )
        self.assertEqual(len(Backlog(self.state).entries()), 3, "no duplicates")

    def test_batches_cap_at_five_with_one_grouped_verify(self) -> None:
        controller_html = page_html(7)
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent, browser_html=controller_html)
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        self.assertEqual(len(report["batches"]), 2)
        self.assertEqual(len(report["batches"][0]["ids"]), 5)
        self.assertEqual(len(report["batches"][1]["ids"]), 2)
        # Still exactly the same three human decisions across TWO batches.
        self.assertEqual(consent.kinds, HUMAN_KINDS)
        self.assertEqual(consent.kinds.count("verify"), 1, "one grouped verify")
        # Every covered tanda/fetch/prepare/gpu window discharged by plan.
        self.assertEqual(self.covered_kinds(report).count("tanda"), 2)
        self.assertEqual(self.covered_kinds(report).count("fetch"), 2)
        self.assertEqual(len(Backlog(self.state).entries()), 7)
        # Batch numbering is stated, never faked.
        self.assertTrue(any("collection batch 1/2" in m for m in self.messages))
        self.assertTrue(any("collection batch 2/2" in m for m in self.messages))

    def test_completed_and_rejected_ids_are_excluded(self) -> None:
        seed_scan_for_run(self.state, [0, 1, 2])
        # id 1 fully complete (emit fingerprint); id 2 rejected.
        seed_job(self.state, fake_id(1), complete=("fetch",))
        from tiktok_ingest.state import Blocklist, BlocklistEntry

        entry = self.state
        Processed = None  # noqa: N806 - shadow guard
        from tiktok_ingest.state import Processed as ProcessedStore

        processed = ProcessedStore(self.state).get(fake_id(1))
        processed.stage_fingerprints["emit"] = {"stage": "emit"}
        ProcessedStore(self.state).record(processed)
        Blocklist(self.state).reject(
            BlocklistEntry(
                video_id=fake_id(2), reason="fixture", rejected_at=FIXED_NOW
            )
        )
        stages = CollectionStages()
        code, report, _ = self.run_collection(
            stages, RecordingConsent(), browser_html=page_html(3)
        )
        self.assertEqual(code, 0)
        batch_ids = report["batches"][0]["ids"]
        self.assertEqual(batch_ids, [fake_id(0)])
        self.assertEqual(report["run_plan"]["frozen_ids"], [fake_id(0)])
        self.assertEqual(len(Backlog(self.state).entries()), 1)

    def test_resume_seeds_dashboard_with_prior_stage_states(self) -> None:
        seed_scan_for_run(self.state, [0])
        seed_job(
            self.state,
            fake_id(0),
            complete=("fetch", "prepare", "vision", "audio"),
            evidence=True,
        )
        stages = CollectionStages()
        consent = SeedingConsent()
        code, report, _ = self.run_collection(
            stages, consent, browser_html=page_html(1)
        )
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        self.assertEqual(
            consent.seeded.get(fake_id(0), {}).get("fetch"), "complete"
        )
        self.assertEqual(
            consent.seeded.get(fake_id(0), {}).get("verify"), "pending"
        )

    def test_resume_reuses_valid_completed_stages(self) -> None:
        seed_scan_for_run(self.state, [0])
        seed_job(
            self.state,
            fake_id(0),
            complete=("fetch", "prepare", "vision", "audio"),
            evidence=True,
        )
        stages = CollectionStages()
        code, report, _ = self.run_collection(
            stages, RecordingConsent(), browser_html=page_html(1)
        )
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        self.assertEqual(
            [c for c in stages.calls if c.startswith(("fetch:", "prepare:", "vision:", "audio:"))],
            [],
            "valid completed stages are reused, never repeated",
        )
        self.assertIn("verify:" + fake_id(0), stages.calls)
        self.assertEqual(len(Backlog(self.state).entries()), 1)
        # The plan named the reuse state of the frozen id.
        self.assertEqual(
            report["run_plan"]["stage_states"][fake_id(0)]["fetch"],
            "complete",
        )


class TestCollectionPhaseBarriers(CollectionRunTestCase):
    def test_global_order_across_seven_ids_and_two_resource_windows(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, transport = self.run_collection(
            stages, consent, browser_html=page_html(7), shared_trace=True
        )
        self.assertEqual(code, 0, report)
        calls = stages.calls
        positions = {
            stage: [i for i, call in enumerate(calls) if call.startswith(stage)]
            for stage in ("prepare:", "audio:", "vision:", "synthesis-api", "verify:")
        }
        for stage, indices in positions.items():
            self.assertEqual(len(indices), 7, stage)
        for before, after in zip(list(positions), list(positions)[1:]):
            self.assertLess(max(positions[before]), min(positions[after]), calls)
        self.assertEqual(consent.kinds, HUMAN_KINDS)
        self.assertEqual(len(transport.requests), 7)
        for batch in report["batches"]:
            self.assertLessEqual(len(batch["ids"]), 5)
            self.assertTrue(all(
                item["stages"]["verify"] == "complete" for item in batch["selected"]
            ), "selected must be a final state view, not the frozen plan snapshot")

    def test_audio_failure_precedes_vision_and_only_blocks_own_synthesis(self) -> None:
        class OneAudioFails(CollectionStages):
            def do_audio(self, video_id: str, state: StateRoot) -> FakeOutcome:
                if video_id == fake_id(0):
                    self.calls.append(f"audio:{video_id}")
                    return FakeOutcome("failed", "fixture audio failure")
                return super().do_audio(video_id, state)

        stages = OneAudioFails()
        code, report, transport = self.run_collection(
            stages, RecordingConsent(), browser_html=page_html(7), shared_trace=True
        )
        self.assertEqual(code, 1)
        self.assertLess(
            max(i for i, call in enumerate(stages.calls) if call.startswith("audio:")),
            min(i for i, call in enumerate(stages.calls) if call.startswith("vision:")),
        )
        self.assertEqual(len(transport.requests), 6)
        self.assertNotIn(fake_id(0), report["verification"]["ids"])
        self.assertEqual(len(Backlog(self.state).entries()), 6)

# --------------------------------------------------------------------------
# The run-plan grant (decision 2)
# --------------------------------------------------------------------------


class TestRunPlan(CollectionRunTestCase):
    def test_plan_window_names_frozen_scope_policies_and_consequences(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        windows = consent.of("run-plan")
        self.assertEqual(len(windows), 1)
        window = windows[0]
        self.assertEqual(
            list(window.ids),
            [fake_id(n) for n in range(3)],
            "the plan names the exact frozen ids",
        )
        blob = " ".join(window.destinations)
        for n in range(3):
            self.assertIn(f"https://www.tiktok.com/@fixture_author/video/{fake_id(n)}", blob)
        self.assertIn("https://api.example.com/v1", blob, "synthesis endpoint named upfront")
        operations = " ".join(window.operations)
        self.assertIn("glm-5.3-flash", operations, "synthesis model named upfront")
        self.assertIn("RESIDENT llm-hub chat", operations, "model backend named upfront")
        self.assertNotIn("whisper coordination", operations)
        self.assertIn("one additional grouped", operations.lower())
        limits = " ".join(window.limits)
        self.assertIn("exactly ONE batch", limits)
        self.assertIn("never reattempted", limits)
        effects = " ".join(window.effects)
        self.assertIn("PENDING backlog", effects)
        self.assertNotIn(API_ENV[ENV_API_KEY], json.dumps(window.to_dict()))
        # The report mirrors the frozen scope for audit.
        run_plan = report["run_plan"]
        self.assertTrue(run_plan["granted"])
        self.assertEqual(run_plan["frozen_ids"], [fake_id(n) for n in range(3)])
        self.assertEqual(run_plan["batch_cap"], 5)
        self.assertEqual(run_plan["batch_count"], 1)
        self.assertEqual(run_plan["text_api"]["origin"], "https://api.example.com/v1")
        self.assertEqual(run_plan["text_api"]["model"], "glm-5.3-flash")

    def test_plan_denial_runs_no_stage(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent(deny={"run-plan"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertIn("run plan denied", report["hard_stop_reason"])
        self.assertEqual(report["batches"], [])
        self.assertEqual(stages.calls, [], "nothing ran")
        self.assertEqual(Backlog(self.state).entries(), [])
        self.assertEqual(consent.kinds, ["browser", "run-plan"], "no later asks")

    def test_expired_run_grant_never_carries_into_the_next_invocation(self) -> None:
        seed_scan_for_run(self.state, [0])
        seed_job(
            self.state,
            fake_id(0),
            complete=("fetch", "prepare", "vision", "audio"),
            evidence=True,
        )
        # First invocation: the human DENIES the plan — nothing runs.
        stages = CollectionStages()
        consent1 = RecordingConsent(deny={"run-plan"})
        code1, _, _ = self.run_collection(stages, consent1, browser_html=page_html(1))
        self.assertEqual(code1, 1)
        self.assertEqual(consent1.kinds, ["browser", "run-plan"])
        self.assertEqual(Backlog(self.state).entries(), [])
        # Second invocation: the plan must be asked AGAIN (no remembered
        # grant), and the resume completes without repeating media work.
        stages2 = CollectionStages()
        consent2 = RecordingConsent()
        code2, report2, _ = self.run_collection(
            stages2, consent2, browser_html=page_html(1)
        )
        self.assertEqual(code2, 0, report2.get("hard_stop_reason"))
        self.assertEqual(consent2.kinds, HUMAN_KINDS, "a new invocation asks again")
        self.assertEqual(
            [c for c in stages2.calls if c.startswith(("vision:", "audio:"))], []
        )
        self.assertIn("verify:" + fake_id(0), stages2.calls)
        self.assertEqual(len(Backlog(self.state).entries()), 1)


# --------------------------------------------------------------------------
# Consent discipline
# --------------------------------------------------------------------------


class TestConsent(CollectionRunTestCase):
    def test_browser_denial_stops_everything(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent(deny={"browser"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertIn("browser inventory did not run", report["hard_stop_reason"])
        self.assertEqual(report["batches"], [])
        self.assertEqual(stages.calls, [])
        self.assertEqual(consent.kinds, ["browser"])
        self.assertEqual(consent.readiness_prompts, [], "never waits after denial")
        self.assertEqual(Backlog(self.state).entries(), [])

    def test_readiness_not_signaled_captures_nothing_and_stops(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent(ready=False)
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertIn("readiness", report["hard_stop_reason"])
        self.assertEqual(report["batches"], [])
        self.assertEqual(stages.calls, [])
        self.assertEqual(consent.kinds, ["browser"], "no plan window without readiness")
        self.assertEqual(Backlog(self.state).entries(), [])

    def test_grouped_verify_window_lists_exact_validated_candidate_urls(self) -> None:
        document = sample_synthesis_document()
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            stages, consent, transport_results=[FakeApiResponse(document)] * 3
        )
        self.assertEqual(code, 0)
        verify_windows = consent.of("verify")
        self.assertEqual(len(verify_windows), 1)
        expected = sorted(
            {
                url
                for claim in document["claims"]
                for url in claim.get("candidate_urls", [])
            }
            | {
                url
                for entity in document["entities"]
                if isinstance(entity, dict)
                for url in entity.get("candidate_urls", [])
            }
        )
        self.assertEqual(sorted(verify_windows[0].destinations), expected)
        self.assertEqual(
            list(verify_windows[0].ids),
            [fake_id(n) for n in range(3)],
            "one window groups every ready id",
        )
        self.assertEqual(report["verification"]["candidate_urls"], expected)

    def test_grouped_verify_denial_emits_nothing_and_stays_resumable(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent(deny={"verify"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertEqual(Backlog(self.state).entries(), [], "no entry without verify consent")
        self.assertFalse(report["verification"]["granted"])
        self.assertEqual(report["verification"]["outcomes"], {})
        # Batches themselves succeeded; a resumable re-invocation with
        # the grouped grant verifies without repeating media work.
        stages2 = CollectionStages()
        consent2 = RecordingConsent()
        code2, report2, _ = self.run_collection(stages2, consent2)
        self.assertEqual(code2, 0, report2.get("hard_stop_reason"))
        self.assertEqual(
            [c for c in stages2.calls if c.startswith(("vision:", "audio:"))], []
        )
        self.assertEqual(len(Backlog(self.state).entries()), 3)


# --------------------------------------------------------------------------
# Authorization wrapper: coverage, audit, deferral
# --------------------------------------------------------------------------


class TestAuthorizationWrapper(CollectionRunTestCase):
    def test_audit_distinguishes_human_grants_from_covered_operations(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 0)
        humans = self.human_decisions(report)
        self.assertEqual([h["window"]["kind"] for h in humans], HUMAN_KINDS)
        self.assertTrue(all(h["basis"].startswith("human") for h in humans))
        for entry in self.stage_windows(report):
            if entry["decision"] == "covered_by_run_plan":
                self.assertTrue(
                    entry["window"]["kind"]
                    in ("tanda", "fetch", "prepare", "gpu", "synthesis-api"),
                    entry,
                )
            else:
                self.assertIn(
                    entry["decision"],
                    ("deferred", "human_granted", "human_denied"),
                    entry,
                )

    def test_unknown_kind_is_forwarded_never_auto_granted(self) -> None:
        plan = collection_run.RunPlan(
            collection_url=COLLECTION_URL,
            frozen_ids=(fake_id(1),),
            canonical_urls={fake_id(1): "https://www.tiktok.com/@fixture_author/video/1"},
            stage_states={},
            batch_cap=5,
            batch_count=1,
            plan_counts={},
            text_api_origin=None,
            text_api_model=None,
        )
        human = RecordingConsent(deny={"mystery"})
        wrapper = collection_run.RunScopedAuthorization(human=human, plan=plan)
        window = ConsentWindow(kind="mystery", title="unknown future window")
        self.assertFalse(wrapper.ask(window))
        self.assertEqual(human.kinds, ["mystery"], "unknown kinds reach the human")
        self.assertEqual(wrapper.authorization_log[0]["decision"], "human_denied")
        # Authority for unknown kinds comes ONLY from the human: a
        # granting human is recorded as the grant, never the plan.
        granter = RecordingConsent()
        granter_wrapper = collection_run.RunScopedAuthorization(
            human=granter, plan=plan
        )
        self.assertTrue(granter_wrapper.ask(window))
        self.assertEqual(
            granter_wrapper.authorization_log[-1]["decision"], "human_granted"
        )

    def test_excess_ids_and_unknown_destinations_are_not_covered(self) -> None:
        plan = collection_run.RunPlan(
            collection_url=COLLECTION_URL,
            frozen_ids=(fake_id(1),),
            canonical_urls={
                fake_id(1): f"https://www.tiktok.com/@fixture_author/video/{fake_id(1)}"
            },
            stage_states={},
            batch_cap=5,
            batch_count=1,
            plan_counts={},
            text_api_origin="https://api.example.com/v1",
            text_api_model="glm-5.3-flash",
        )
        human = RecordingConsent()
        wrapper = collection_run.RunScopedAuthorization(human=human, plan=plan)
        # A fetch window naming an id OUTSIDE the frozen inventory.
        excess = ConsentWindow(
            kind="fetch",
            title="x",
            ids=(fake_id(9),),
            destinations=("https://www.tiktok.com/@fixture_author/video/9",),
        )
        self.assertTrue(wrapper.ask(excess), "forwarded to the human, who grants")
        self.assertEqual(human.kinds, ["fetch"])
        self.assertEqual(wrapper.authorization_log[0]["decision"], "human_granted")
        self.assertIn("not covered", wrapper.authorization_log[0]["basis"])
        # A fetch window with an UNAPPROVED destination URL.
        stranger = ConsentWindow(
            kind="fetch",
            title="x",
            ids=(fake_id(1),),
            destinations=("https://evil.example/video/1",),
        )
        human2 = RecordingConsent()
        wrapper2 = collection_run.RunScopedAuthorization(human=human2, plan=plan)
        wrapper2.ask(stranger)
        self.assertEqual(human2.kinds, ["fetch"], "unapproved destination is asked")
        self.assertEqual(
            [e["decision"] for e in wrapper2.authorization_log],
            ["human_granted"],
            "an unapproved destination is never covered, only asked",
        )
        # A synthesis window to a DIFFERENT endpoint is never covered.
        other_api = ConsentWindow(
            kind="synthesis-api",
            title="x",
            ids=(fake_id(1),),
            destinations=("https://evil.example/v1",),
        )
        human3 = RecordingConsent()
        wrapper3 = collection_run.RunScopedAuthorization(human=human3, plan=plan)
        wrapper3.ask(other_api)
        self.assertEqual(human3.kinds, ["synthesis-api"])

    def _api_plan(self) -> collection_run.RunPlan:
        return collection_run.RunPlan(
            collection_url=COLLECTION_URL,
            frozen_ids=(fake_id(1),),
            canonical_urls={
                fake_id(1): f"https://www.tiktok.com/@fixture_author/video/{fake_id(1)}"
            },
            stage_states={},
            batch_cap=5,
            batch_count=1,
            plan_counts={},
            text_api_origin="https://api.example.com/v1",
            text_api_model="glm-5.3",
        )

    def test_synthesis_model_matches_exactly_never_by_substring(self) -> None:
        plan = self._api_plan()
        # Same endpoint, a model whose name merely CONTAINS the plan
        # model as a substring: an impersonator, never covered.
        sneaky = ConsentWindow(
            kind="synthesis-api",
            title="x",
            ids=(fake_id(1),),
            destinations=("https://api.example.com/v1",),
            operations=(
                "ONE request using model glm-5.3-quantized-evil "
                "(NOT the approved model)",
            ),
        )
        human = RecordingConsent()
        wrapper = collection_run.RunScopedAuthorization(human=human, plan=plan)
        self.assertTrue(wrapper.ask(sneaky), "impersonator forwarded to the human")
        self.assertEqual(human.kinds, ["synthesis-api"])
        self.assertNotEqual(
            wrapper.authorization_log[0]["decision"],
            "covered_by_run_plan",
            "substring model match must never discharge a window",
        )
        # Structured binding: the exact model attribute discharges.
        exact_structured = ConsentWindow(
            kind="synthesis-api",
            title="x",
            ids=(fake_id(1),),
            destinations=("https://api.example.com/v1",),
            operations=("fixture operation",),
            model="glm-5.3",
        )
        human2 = RecordingConsent()
        wrapper2 = collection_run.RunScopedAuthorization(human=human2, plan=plan)
        self.assertTrue(wrapper2.ask(exact_structured))
        self.assertEqual(
            wrapper2.authorization_log[0]["decision"], "covered_by_run_plan"
        )
        # Structured prefix/suffix impersonation is rejected exactly.
        for evil in ("glm-5.3-quantized-evil", "evil-glm-5.3", "glm-5.3 ", "GLM-5.3"):
            impersonator = ConsentWindow(
                kind="synthesis-api",
                title="x",
                ids=(fake_id(1),),
                destinations=("https://api.example.com/v1",),
                operations=("fixture operation",),
                model=evil,
            )
            human3 = RecordingConsent()
            wrapper3 = collection_run.RunScopedAuthorization(human=human3, plan=plan)
            wrapper3.ask(impersonator)
            self.assertEqual(
                human3.kinds,
                ["synthesis-api"],
                f"model {evil!r} must be asked as new scope, never covered",
            )
        # Legacy window without the attribute: the EXACT token parsed from
        # the stable generated "(model <token>)" pattern still discharges.
        legacy_exact = ConsentWindow(
            kind="synthesis-api",
            title="x",
            ids=(fake_id(1),),
            destinations=("https://api.example.com/v1",),
            operations=(
                "ONE request to https://api.example.com/v1 (model glm-5.3) "
                "carrying ONLY sanitized evidence text",
            ),
        )
        human4 = RecordingConsent()
        wrapper4 = collection_run.RunScopedAuthorization(human=human4, plan=plan)
        self.assertTrue(wrapper4.ask(legacy_exact))
        self.assertEqual(
            wrapper4.authorization_log[0]["decision"], "covered_by_run_plan"
        )
        # A legacy window with no parseable exact token is never covered.
        legacy_vague = ConsentWindow(
            kind="synthesis-api",
            title="x",
            ids=(fake_id(1),),
            destinations=("https://api.example.com/v1",),
            operations=("request using some model somewhere"),
        )
        human5 = RecordingConsent()
        wrapper5 = collection_run.RunScopedAuthorization(human=human5, plan=plan)
        wrapper5.ask(legacy_vague)
        self.assertEqual(human5.kinds, ["synthesis-api"])


# --------------------------------------------------------------------------


class TestSynthesisApi(CollectionRunTestCase):
    def test_automatic_cleanup_does_not_hide_credentials_in_discarded_action(self) -> None:
        from tiktok_ingest.synthesis_api import load_text_api_config

        video_id = fake_id(1)
        run_dir = seed_job(self.state, video_id, complete=("fetch", "prepare", "audio", "vision"), evidence=True)
        document = sample_synthesis_document()
        document["actionables"] = ["Seguir a @ghp_" + "x" * 30 + " para más consejos."]
        config, _ = load_text_api_config(API_ENV)
        transport = FakeApiTransport([FakeApiResponse(document)])
        synthesize = collection_run.make_auto_synthesizer(config, transport=transport)
        def state_hashes() -> dict[str, str]:
            return {str(path.relative_to(self.state.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in self.state.root.rglob("*") if path.is_file()}
        before = state_hashes()
        result = synthesize(video_id, self.state, lambda window: True)
        self.assertEqual(result.outcome, "failed")
        self.assertFalse((run_dir / "synthesis.json").exists())
        self.assertEqual(state_hashes(), before)

    def test_manual_file_verdict_is_still_preserved_without_fetches(self) -> None:
        from tiktok_ingest.synthesis import run_synthesis_stage
        from tiktok_ingest.verify import run_verify_stage

        video_id = fake_id(1)
        seed_scan_for_run(self.state, [1])
        run_dir = seed_job(self.state, video_id, complete=("fetch", "prepare", "audio", "vision"), evidence=True)
        document = sample_synthesis_document()
        for claim in document["claims"]:
            claim["verdict"] = "confirmed"
            claim["candidate_urls"] = []
        for entity in document["entities"]:
            if isinstance(entity, dict):
                entity["candidate_urls"] = []
        source = Path(self.tmp.name) / "operator.json"
        source.write_text(json.dumps(document), encoding="utf-8")
        outcome = run_synthesis_stage(video_id, state=self.state, from_file=source)
        self.assertEqual(outcome.outcome, "complete")
        outcome = run_verify_stage(video_id, state=self.state,
                                   urlopen=lambda *a, **k: self.fail("unexpected fetch"))
        self.assertEqual(outcome.outcome, "complete")
        verified = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(verified["claims"][0]["verdict"], "confirmed")
        self.assertIn("kept operator-supplied verdict", verified["claims"][0]["reason"])
        self.assertEqual(verified["claims"][0]["fetches"], [])

    def test_model_verdicts_are_unverified_and_engagement_only_is_excluded(self) -> None:
        from tiktok_ingest.synthesis_api import load_text_api_config
        from tiktok_ingest.synthesis import SynthesisDocument
        from tiktok_ingest.verify import evaluate_claim, run_verify_stage

        video_id = fake_id(1)
        seed_scan_for_run(self.state, [1])
        run_dir = seed_job(self.state, video_id, complete=("fetch", "prepare", "audio", "vision"), evidence=True)
        document = sample_synthesis_document()
        for claim in document["claims"]:
            claim["verdict"] = "confirmed"
            claim["candidate_urls"] = []
        for entity in document["entities"]:
            if isinstance(entity, dict):
                entity["candidate_urls"] = []
        document["actionables"] = [
            "Seguir a @canal para más consejos.",
            "Preparar pollo al horno", "Consultar la receta original",
        ]
        config, _ = load_text_api_config(API_ENV)
        self.assertIsNotNone(config)
        transport = FakeApiTransport([FakeApiResponse(document)])
        synthesize = collection_run.make_auto_synthesizer(config, transport=transport)
        result = synthesize(video_id, self.state, lambda window: True)
        self.assertEqual(result.outcome, "complete")
        saved = json.loads((run_dir / "synthesis.json").read_text(encoding="utf-8"))
        self.assertIsNone(saved["claims"][0]["verdict"])
        self.assertEqual(saved["actionables"], ["Preparar pollo al horno", "Consultar la receta original"])
        parsed = SynthesisDocument.from_dict(saved)
        self.assertNotEqual(evaluate_claim(parsed.claims[0], {}, parsed.entities).verdict, "confirmed")
        def no_fetch(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("no candidate URLs should be fetched")
        verified = run_verify_stage(video_id, state=self.state, urlopen=no_fetch)
        self.assertEqual(verified.outcome, "complete")
        verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
        self.assertTrue(all(claim["verdict"] != "confirmed" for claim in verification["claims"]))
        self.assertTrue(all(not claim["fetches"] for claim in verification["claims"]))
        self.assertEqual(Backlog(self.state).entries()[0]["actionable"],
                         "Preparar pollo al horno; Consultar la receta original")
        self.assertEqual(len(transport.requests), 1)

    def api_windows(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        windows = [
            entry["window"]
            for entry in self.stage_windows(report)
            if entry["window"]["kind"] == "synthesis-api"
        ]
        self.assertTrue(windows, "synthesis API window must be asked (covered)")
        return windows

    def test_covered_windows_show_origin_model_and_limits_never_the_key(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 0)
        window = self.api_windows(report)[0]
        self.assertIn("https://api.example.com/v1", " ".join(window["destinations"]))
        self.assertIn("glm-5.3-flash", " ".join(window["operations"]))
        blob = json.dumps(window)
        self.assertNotIn(API_ENV[ENV_API_KEY], blob)
        # Multiple synthesis ids discharge under the plan with no extra
        # human prompts.
        self.assertEqual(len(self.api_windows(report)), 3)
        self.assertEqual(consent.kinds, HUMAN_KINDS)

    def test_outbound_payload_is_sanitized_evidence_only(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, _, transport = self.run_collection(stages, consent)
        self.assertEqual(code, 0)
        self.assertEqual(len(transport.requests), 3)
        for request in transport.requests:
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["tool_choice"], "none")
            self.assertEqual(body["tools"], [])
            self.assertEqual(body["response_format"], {"type": "json_object"})
            user_text = body["messages"][1]["content"]
            self.assertNotIn("/state/", user_text)
            self.assertNotIn(str(self.state.root), user_text)
            self.assertIn("video.md evidence", user_text)
            self.assertIn("audio.md evidence", user_text)

    def test_timeout_is_resumable_and_never_verifies(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            stages,
            consent,
            transport_results=[TimeoutError("fixture timeout")],
        )
        self.assertEqual(code, 1)
        self.assertNotIn("verify", consent.kinds, "no verify without validated synthesis")
        self.assertIsNone(report["verification"], "no grouped verify without synthesis")
        self.assertEqual(Backlog(self.state).entries(), [])
        failures = report["batches"][0]["failures"]
        self.assertTrue(
            any(f["stage"] == "synthesis" and "resumable" in f["reason"] for f in failures),
            failures,
        )

    def test_malformed_document_is_rejected_by_the_real_contract(self) -> None:
        bad_document = dict(sample_synthesis_document())
        bad_document["classification"] = {"root": "unknown-root", "subgroup": "x", "confidence": 1}
        stages = CollectionStages()
        consent = RecordingConsent()
        code, _report, _ = self.run_collection(
            stages,
            consent,
            transport_results=[FakeApiResponse(bad_document)],
        )
        self.assertEqual(code, 1)
        self.assertNotIn("verify", consent.kinds)
        self.assertEqual(Backlog(self.state).entries(), [])

    def test_missing_config_blocks_at_synthesis_without_model_calls(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent, env={})
        self.assertEqual(code, 1)
        self.assertEqual(
            [],
            [
                entry
                for entry in self.stage_windows(report)
                if entry["window"]["kind"] == "synthesis-api"
            ],
            "no API window without config",
        )
        waiting = report["batches"][0]["waiting_for_synthesis"]
        self.assertEqual(waiting, [fake_id(0), fake_id(1), fake_id(2)])
        self.assertEqual(Backlog(self.state).entries(), [])
        self.assertEqual(report["preflight"]["text_api_configured"], False)
        self.assertIsNone(report["run_plan"]["text_api"])


# --------------------------------------------------------------------------
# The frozen driver discipline
# --------------------------------------------------------------------------


class TestFrozenDriver(CollectionRunTestCase):
    def test_failed_ids_are_never_reattempted_in_later_batches(self) -> None:
        stages = CollectionStages(fetch_fails=True)
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            stages, consent, browser_html=page_html(7)
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(report["batches"]), 2, "unrelated ids still get their batch")
        fetches = [c for c in stages.calls if c.startswith("fetch:")]
        self.assertEqual(len(fetches), 7, "every frozen id attempted exactly once")
        self.assertEqual(len(set(fetches)), 7, "no id is ever repeated")
        self.assertEqual(Backlog(self.state).entries(), [])
        self.assertEqual(consent.kinds, ["browser", "run-plan"], "no re-asking on failure")

    def test_no_absorption_outside_the_frozen_inventory(self) -> None:
        # limit below the item count: batches are fixed slices of the
        # frozen inventory — every frozen id attempted exactly once and
        # NOTHING outside it is ever attempted, in any batch.
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent, limit=2)
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        frozen = report["run_plan"]["frozen_ids"]
        self.assertEqual(frozen, [fake_id(n) for n in range(3)])
        attempted = [i for b in report["batches"] for i in b["ids"]]
        self.assertEqual(attempted, list(frozen), "fixed slices, no absorption")
        self.assertEqual(len(report["batches"]), 2)


# --------------------------------------------------------------------------
# Hard stops block the grouped verification phase too
# --------------------------------------------------------------------------


class TestHardStopBlocksGroupedVerification(CollectionRunTestCase):
    def test_boundary_failure_after_ready_batch_skips_grouped_verify(self) -> None:
        """Validator Check B regression: a hard stop must include the
        grouped verification phase.

        One id already has validated synthesis (ready for verification);
        batch 2's vision stage raises an unexpected boundary error ->
        the run hard-stops. No grouped
        verification prompt, no verify stage, no backlog write may happen
        afterwards; the ready results stay resumable in a NEW authorized
        run with zero re-inference.
        """

        class SecondBatchVisionBreaks(CollectionStages):
            def do_vision(self, video_id: str, state: StateRoot) -> FakeOutcome:
                if video_id in (fake_id(5), fake_id(6)):
                    raise RuntimeError("fixture vision boundary failure")
                return super().do_vision(video_id, state)

        seed_scan_for_run(self.state, list(range(7)))
        seed_job(
            self.state, fake_id(0),
            complete=("fetch", "prepare", "vision", "audio"), evidence=True,
        )
        outcome = collection_run.run_synthesis_stage(
            fake_id(0), state=self.state, document_fn=sample_synthesis_document
        )
        self.assertEqual(outcome.outcome, "complete")
        stages = SecondBatchVisionBreaks()
        consent = RecordingConsent()
        code, report, transport = self.run_collection(
            stages, consent, browser_html=page_html(7)
        )
        self.assertEqual(code, 1)
        self.assertIn("boundary failed", report["hard_stop_reason"])
        self.assertEqual(len(report["batches"]), 2)
        # Prior synthesis remains ready, but no synthesis request crossed
        # the barrier before the vision boundary failure.
        self.assertEqual(transport.requests, [])
        self.assertEqual(transport_ready(report), [])
        self.assertEqual(
            collection_run.derive_item_stages(self.state, fake_id(0))["synthesis"],
            "complete",
        )
        # ...and yet NOTHING verifies after the hard stop:
        self.assertNotIn(
            "verify", consent.kinds, "no grouped prompt after a hard stop"
        )
        self.assertIsNone(report["verification"])
        self.assertEqual([c for c in stages.calls if c.startswith("verify:")], [])
        self.assertEqual(Backlog(self.state).entries(), [])
        # Resumable: a NEW authorized run completes the journey without
        # repeating any completed media stage.
        stages2 = CollectionStages()
        consent2 = RecordingConsent()
        code2, report2, _ = self.run_collection(stages2, consent2)
        self.assertEqual(code2, 0, report2.get("hard_stop_reason"))
        self.assertEqual(consent2.kinds, HUMAN_KINDS, "a new run asks again")
        self.assertEqual(
            sorted(c for c in stages2.calls if c.startswith("vision:")),
            [f"vision:{fake_id(5)}", f"vision:{fake_id(6)}"],
            "only the two ids whose vision never completed re-run; the "
            "ready results resume with zero re-inference",
        )
        self.assertEqual(
            sorted(c for c in stages2.calls if c.startswith("audio:")),
            [],
            "all seven audio results survive and need no retranscription",
        )
        self.assertEqual(
            len([c for c in stages2.calls if c.startswith("verify:")]), 7
        )
        self.assertEqual(len(Backlog(self.state).entries()), 7)


def transport_ready(report: dict[str, Any]) -> list[str]:
    """Ids whose synthesis completed (would be verify candidates)."""
    ready: list[str] = []
    for batch in report["batches"]:
        for video_id, records in (batch.get("stage_outcomes_this_run") or {}).items():
            if isinstance(records, dict) and records.get("synthesis", {}).get(
                "outcome"
            ) == "complete":
                ready.append(video_id)
    return ready


# --------------------------------------------------------------------------
# The REAL default consent provider, end to end (validator Check A)
# --------------------------------------------------------------------------


class TestRealConsoleConsentJourney(CollectionRunTestCase):
    def test_two_batches_need_exactly_three_yes_and_one_enter(
        self,
    ) -> None:
        stages = CollectionStages()

        class CountingStdin(io.StringIO):
            lines_read = 0

            def readline(self, *args: Any) -> str:
                CountingStdin.lines_read += 1
                return super().readline(*args)

        stdin = CountingStdin("yes\n\nyes\nyes\n")
        provider_lines: list[str] = []
        real_consent = ConsoleConsentProvider(
            stdin=stdin, out=lambda text, end="\n": provider_lines.append(text)
        )
        transport = FakeApiTransport(
            [FakeApiResponse(sample_synthesis_document()) for _ in range(50)]
        )
        controller = FakeBrowserController(page_html(7))
        code, report = collection_run.run_collection(
            collection_url=COLLECTION_URL,
            state=self.state,
            consent=real_consent,
            out=self.messages.append,
            browser_controller=controller,
            browser_profile_base=self.profile_base,
            api_transport=transport,
            env=API_ENV,
            stages=stages.functions(),
        )
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        self.assertEqual(len(report["batches"]), 2)
        # EXACTLY three yes/no decisions and ONE readiness Enter across
        # the whole two-batch run. (The browser
        # window's DISPLAY text also mentions the checkpoint; count only
        # actual prompt lines, which start with the checkpoint wording.)
        asks = [l for l in provider_lines if "authorize this window?" in l]
        readiness = [
            l
            for l in provider_lines
            if l.strip().startswith("readiness checkpoint (NOT")
        ]
        self.assertEqual(len(asks), 3, asks)
        self.assertEqual(len(readiness), 1, readiness)
        self.assertEqual(
            [h["window"]["kind"] for h in report["run_authorization"]["human_decisions"]],
            HUMAN_KINDS,
        )
        self.assertTrue(
            all(h["granted"] for h in report["run_authorization"]["human_decisions"])
        )
        self.assertFalse(
            [
                e
                for e in report["run_authorization"]["stage_windows"]
                if e["decision"] not in ("covered_by_run_plan", "deferred")
            ],
            "zero routine prompts: every stage window covered or deferred",
        )
        # Exactly four input lines consumed (yes, Enter, yes, yes) — the
        # stdin is exhausted afterwards.
        self.assertEqual(CountingStdin.lines_read, 4)
        self.assertEqual(stdin.read(), "")
        # All seven ids processed, synthesized and verified.
        self.assertEqual(len(transport.requests), 7)
        self.assertEqual(len([c for c in stages.calls if c.startswith("verify:")]), 7)
        self.assertEqual(len(Backlog(self.state).entries()), 7)


# --------------------------------------------------------------------------
# Progress narration honesty
# --------------------------------------------------------------------------


class TestProgress(CollectionRunTestCase):
    def test_stage_start_finish_and_batch_lines_are_truthful(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 0)
        joined = "\n".join(self.messages)
        self.assertIn("collection batch 1/1", joined)
        self.assertIn(f"stage fetch start: {fake_id(0)}", joined)
        self.assertIn(
            f"stage fetch finish: {fake_id(0)} complete (reused=False,", joined
        )
        self.assertIn(f"stage vision start: {fake_id(0)}", joined)
        self.assertIn(
            f"stage audio finish: {fake_id(0)} complete (reused=False,", joined
        )
        self.assertIn(f"stage verify start: {fake_id(0)}", joined)
        self.assertIn(
            f"stage verify finish: {fake_id(0)} complete (reused=False,", joined
        )
        # The synthesis stage is narrated like every other stage: start
        # line plus finish line with the actual outcome and duration,
        # with no intermediate fake progress and no evidence dumping.
        self.assertIn(f"stage synthesis-api start: {fake_id(0)}", joined)
        self.assertIn(
            f"stage synthesis-api finish: {fake_id(0)} complete (reused=False,",
            joined,
        )
        self.assertFalse(
            any("evidence" in m and "start" in m for m in self.messages),
            "no evidence text is ever narrated",
        )
        # No fabricated percentage claims.
        self.assertFalse(any("%" in m for m in self.messages), self.messages)

    def test_final_report_remains_parseable_json(self) -> None:
        stages = CollectionStages()
        code, report, _ = self.run_collection(stages, RecordingConsent())
        self.assertEqual(code, 0)
        parsed = json.loads(json.dumps(report, default=str))
        self.assertEqual(parsed["mode"], "execute")
        self.assertIn("run_authorization", parsed)


# --------------------------------------------------------------------------
# Report persistence
# --------------------------------------------------------------------------


class TestReportPersistence(CollectionRunTestCase):
    """A finished collection-run persists its report under the state root."""

    REPORT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.json$")

    def report_dir(self) -> Path:
        return self.state.root / "reports" / REF.collection_key

    def test_finished_run_persists_report_and_narrates_path(self) -> None:
        seed_scan_for_run(self.state, [1, 2])
        code, report, _ = self.run_collection(CollectionStages(), RecordingConsent())
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        directory = self.report_dir()
        self.assertTrue(directory.is_dir(), "reports folder must exist")
        files = sorted(directory.glob("*.json"))
        self.assertEqual(len(files), 1, files)
        saved = files[0]
        self.assertRegex(saved.name, self.REPORT_NAME_RE.pattern)
        # The saved file is the atomic JSON of the returned report.
        on_disk = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["collection_key"], REF.collection_key)
        self.assertEqual(on_disk["mode"], "execute")
        self.assertEqual(
            on_disk["batches"][0]["selected"][0]["id"],
            report["batches"][0]["selected"][0]["id"],
        )
        # The saved path is narrated on the out channel (stderr in prod).
        self.assertTrue(
            any(str(saved) in message for message in self.messages), self.messages
        )

    def test_report_is_never_written_into_the_repository(self) -> None:
        seed_scan_for_run(self.state, [1])
        self.run_collection(CollectionStages(), RecordingConsent())
        saved = next(self.report_dir().glob("*.json"))
        self.assertTrue(str(saved).startswith(str(self.state.root)))
        self.assertNotIn("/.dotfiles/", str(saved))

    def test_dry_run_writes_no_report(self) -> None:
        seed_scan_for_run(self.state, [1])
        self.run_collection(CollectionStages(), RecordingConsent(), dry_run=True)
        self.assertFalse((self.state.root / "reports").exists())


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


class TestPreflight(CollectionRunTestCase):
    def test_preflight_names_missing_env_without_values(self) -> None:
        report = collection_run.preflight(
            state=self.state, collection_url=COLLECTION_URL, env={}
        )
        self.assertFalse(report["text_api_configured"])
        self.assertEqual(
            report["text_api_missing_env"],
            [ENV_BASE_URL, ENV_API_KEY, ENV_MODEL],
        )
        self.assertIn("exclusive-writer", report["exclusive_writer"])
# --------------------------------------------------------------------------
# Collection-level resume and idempotency
# --------------------------------------------------------------------------


class TestCollectionResumeAndIdempotency(CollectionRunTestCase):
    def test_existing_scan_resumes_without_opening_browser(self) -> None:
        seed_scan_for_run(self.state, [1, 2])
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            CollectionStages(),
            consent,
            rescan=False,
        )
        self.assertEqual(code, 0)
        # Browser grant was skipped; run plan was asked directly
        self.assertNotIn("browser", consent.kinds)
        self.assertIn("run-plan", consent.kinds)
        self.assertTrue(report["browser"]["reused"])
        self.assertTrue(
            any("resuming collection" in m for m in self.messages)
        )

    def test_existing_scan_fully_complete_is_idempotent_noop(self) -> None:
        seed_scan_for_run(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch",))
        from tiktok_ingest.state import Processed as ProcessedStore

        processed = ProcessedStore(self.state).get(fake_id(1))
        processed.stage_fingerprints["emit"] = {"stage": "emit"}
        ProcessedStore(self.state).record(processed)

        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            CollectionStages(),
            consent,
            rescan=False,
        )
        self.assertEqual(code, 0)
        # ZERO consent prompts (no browser, no run-plan)
        self.assertEqual(consent.kinds, [])
        self.assertTrue(report["browser"]["reused"])
        self.assertTrue(
            any("already fully ingested" in m for m in self.messages)
        )

    def test_rescan_flag_forces_fresh_browser_inventory(self) -> None:
        seed_scan_for_run(self.state, [1, 2])
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            CollectionStages(),
            consent,
            rescan=True,
        )
        self.assertEqual(code, 0)
        # Browser WAS prompted and run
        self.assertIn("browser", consent.kinds)
        self.assertFalse(report["browser"].get("reused", False))

    def test_no_existing_scan_always_runs_browser(self) -> None:
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            CollectionStages(),
            consent,
            rescan=False,
        )
        self.assertEqual(code, 0)
        # Browser WAS prompted and run
        self.assertIn("browser", consent.kinds)
        self.assertFalse(report["browser"].get("reused", False))


# --------------------------------------------------------------------------
# Intra-phase retries within resource windows
# --------------------------------------------------------------------------


class RetryingStages(CollectionStages):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stage_attempt_counts: dict[str, int] = {}

    def do_fetch(self, url: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"fetch:{url}")
        count = self.stage_attempt_counts.get("fetch", 0) + 1
        self.stage_attempt_counts["fetch"] = count
        if count == 1:
            return FakeOutcome("failed", "transient network glitch: fixture")
        return super().do_fetch(url, state)


class TestIntraPhaseRetries(CollectionRunTestCase):
    def test_failed_item_retried_and_succeeds_in_same_phase(self) -> None:
        seed_scan_for_run(self.state, [1])
        stages = RetryingStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            stages,
            consent,
            rescan=False,
            retry_failed=1,
        )
        self.assertEqual(stages.stage_attempt_counts.get("fetch"), 2)
        self.assertTrue(
            any("retrying 1 failed item(s)" in m for m in self.messages)
        )
        # Preparation phase completed successfully on retry
        prep_phase = next(p for p in report["phases"] if p["phase"] == "preparation")
        self.assertEqual(prep_phase["state"], "complete")
        self.assertEqual(prep_phase["pending_ids"], [])

    def test_retry_failed_zero_disables_intra_phase_retries(self) -> None:
        seed_scan_for_run(self.state, [1])
        stages = RetryingStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            stages,
            consent,
            rescan=False,
            retry_failed=0,
        )
        self.assertEqual(code, 1)
        self.assertEqual(stages.stage_attempt_counts.get("fetch"), 1)
        self.assertFalse(
            any("retrying" in m for m in self.messages)
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
