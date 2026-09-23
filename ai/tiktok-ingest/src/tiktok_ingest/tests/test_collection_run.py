"""collection-run tests: every external boundary is a double.

Covers the Phase 0 application layer end to end with fakes: the
browser controller double, stage doubles that seed REAL durable state
(so the product's own fingerprints drive resume), the scripted/recording
consent provider, the fake text-API transport and the fake podman
runner. Zero network, zero browser, zero GPU, zero containers.

The coordination contract proven here: fresh plan recomputation per
batch, batch cap 5, exclusion of completed/rejected ids, loop-guard on
no progress, whisper stop/restore ordering and failure blocking, the
synthesis API consent + resumable stops (timeout/refusal/denial/
missing config) without vision/audio repetition, exact-URL verify
consent from the VALIDATED document, pending-only delivery without
duplicates, and dry-run zero effects.
"""

from __future__ import annotations

import dataclasses
import json
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
from tiktok_ingest.guided import ConsentWindow, StageFunctions
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
from tiktok_ingest.whisper_lifecycle import WhisperServiceInfo

COLLECTION_URL = "https://www.tiktok.com/@fixture_author/collection/run-col"
REF = parse_collection_url(COLLECTION_URL)


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
    """Grants everything except the denied kinds; records every window."""

    def __init__(self, deny: set[str] | None = None) -> None:
        self.deny = deny or set()
        self.windows: list[ConsentWindow] = []

    def ask(self, window: ConsentWindow) -> bool:
        self.windows.append(window)
        return window.kind not in self.deny

    @property
    def kinds(self) -> list[str]:
        return [w.kind for w in self.windows]

    def of(self, kind: str) -> list[ConsentWindow]:
        return [w for w in self.windows if w.kind == kind]


KNOWN_INFO = WhisperServiceInfo(
    name="voice-assistant-whisper",
    image="localhost/voice-assistant_whisper:latest",
    state="running",
    running=True,
    env=(("COMPUTE_TYPE", "float16"), ("DEVICE", "cuda")),
)

OTHER_INFO = dataclasses.replace(KNOWN_INFO, name="some-other-whisper")


def page_html(items: int) -> str:
    links = "".join(
        f'<a href="/@fixture_author/video/{fake_id(n)}">item</a>'
        for n in range(items)
    )
    return (
        "<!DOCTYPE html><html><body>"
        f'<div data-testid="collection-container">{links}</div>'
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
        whisper_running: bool = False,
        whisper_info: WhisperServiceInfo | None = KNOWN_INFO,
        whisper_stop_fails: bool = False,
        whisper_stop_raises_ki: bool = False,
        whisper_restore_fails: bool = False,
        whisper_restore_raises_ki: bool = False,
        whisper_healthy: bool = True,
        ollama_already_running: bool = False,
        verify_emits_pending: bool = True,
        fetch_fails: bool = False,
        vision_raises: BaseException | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.whisper_running = whisper_running
        self.whisper_info = whisper_info
        self.whisper_stop_fails = whisper_stop_fails
        self.whisper_stop_raises_ki = whisper_stop_raises_ki
        self.whisper_restore_fails = whisper_restore_fails
        self.whisper_restore_raises_ki = whisper_restore_raises_ki
        self.whisper_healthy = whisper_healthy
        self.ollama_already_running = ollama_already_running
        self.verify_emits_pending = verify_emits_pending
        self.fetch_fails = fetch_fails
        self.vision_raises = vision_raises
        self.whisper_stops = 0
        self.whisper_restores = 0
        self.server_stops = 0

    def functions(self) -> StageFunctions:
        return StageFunctions(
            fetch=self.do_fetch,
            prepare=self.do_prepare,
            vision=self.do_vision,
            audio=self.do_audio,
            synthesize=self.do_synthesize_unreachable,
            verify=self.do_verify,
            whisper_stopped=self.do_whisper_stopped,
            whisper_health=self.do_whisper_health,
            ollama_reachable=lambda: self.ollama_already_running,
            ollama_start=self.do_ollama_start,
            ollama_wait_ready=lambda handle: "0.34.1",
            ollama_stop=self.do_ollama_stop,
            whisper_inspect=self.do_whisper_inspect,
            whisper_stop=self.do_whisper_stop,
            whisper_restore=self.do_whisper_restore,
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
        return FakeOutcome("complete")

    # -- whisper lifecycle hooks (doubles) ------------------------------

    def do_whisper_stopped(self) -> None:
        self.calls.append("whisper-stopped-check")
        if self.whisper_running:
            raise RuntimeError(
                "stop the whisper service first: still running: "
                "['voice-assistant-whisper']"
            )

    def do_whisper_health(self) -> tuple[bool, str]:
        self.calls.append("whisper-health-check")
        if self.whisper_healthy:
            return True, "healthy"
        return False, "whisper health endpoint unreachable: fixture"

    def do_whisper_inspect(self) -> WhisperServiceInfo | None:
        self.calls.append("whisper-inspect")
        if not self.whisper_running:
            return None
        return self.whisper_info

    def do_whisper_stop(self, info: WhisperServiceInfo) -> WhisperServiceInfo:
        self.calls.append("whisper-stop-op")
        if self.whisper_stop_raises_ki:
            # The interruption lands INSIDE the stop: its result is
            # unknown, so the completed-stop counter must not move.
            raise KeyboardInterrupt()
        self.whisper_stops += 1
        if self.whisper_stop_fails:
            raise RuntimeError("fixture: stop did not verify")
        # A verified stop changes the world: later stopped-checks pass.
        self.whisper_running = False
        return dataclasses.replace(info, running=False, state="exited")

    def do_whisper_restore(self, info: WhisperServiceInfo) -> WhisperServiceInfo:
        self.calls.append("whisper-restore-op")
        self.whisper_restores += 1
        if self.whisper_restore_raises_ki:
            raise KeyboardInterrupt()
        if self.whisper_restore_fails:
            raise RuntimeError("fixture: restore failed")
        self.whisper_running = True
        return dataclasses.replace(info, running=True, state="running")

    # -- ollama lifecycle -----------------------------------------------

    def do_ollama_start(self) -> object:
        self.calls.append("ollama-start")
        return object()

    def do_ollama_stop(self, handle: object) -> None:
        self.calls.append("ollama-stop")
        self.server_stops += 1


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
    ) -> tuple[int, dict[str, Any], FakeApiTransport | None]:
        controller = FakeBrowserController(
            browser_html if browser_html else page_html(3)
        )
        transport = FakeApiTransport(
            transport_results
            if transport_results is not None
            else [FakeApiResponse(sample_synthesis_document()) for _ in range(50)]
        )
        code, report = collection_run.run_collection(
            collection_url=COLLECTION_URL,
            state=self.state,
            consent=consent,
            out=self.messages.append,
            dry_run=dry_run,
            limit=limit,
            browser_controller=controller,
            browser_profile_base=self.profile_base,
            api_transport=transport,
            env=env if env is not None else API_ENV,
            podman_runner=lambda argv, timeout=None: (0, "", ""),
            stages=stages.functions(),
        )
        return code, report, transport


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
# Happy path and batching
# --------------------------------------------------------------------------


class TestHappyPath(CollectionRunTestCase):
    def test_full_journey_to_pending_entries(self) -> None:
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
        # Window order: browser -> confirm -> tanda -> fetch -> prepare
        # -> gpu -> synthesis-api(x3) -> verify.
        self.assertEqual(
            consent.kinds,
            [
                "browser",
                "browser-confirm",
                "tanda",
                "fetch",
                "prepare",
                "gpu",
                "synthesis-api",
                "synthesis-api",
                "synthesis-api",
                "verify",
            ],
        )
        self.assertIn("review_next", report)
        self.assertEqual(report["backlog_pending_view"], 3)

    def test_second_run_reuses_everything_and_duplicates_nothing(self) -> None:
        stages = CollectionStages()
        code1, report1, _ = self.run_collection(stages, RecordingConsent())
        self.assertEqual(code1, 0)
        first_stage_calls = list(stages.calls)
        stages2 = CollectionStages()
        consent2 = RecordingConsent()
        code2, report2, _ = self.run_collection(stages2, consent2)
        self.assertEqual(code2, 0, report2.get("hard_stop_reason"))
        self.assertEqual(report2["batches"], [], "nothing eligible remains")
        self.assertEqual(
            [c for c in stages2.calls if c.startswith(("fetch:", "vision:", "audio:"))],
            [],
            "no download or audiovisual inference on rerun",
        )
        self.assertEqual(len(Backlog(self.state).entries()), 3, "no duplicates")

    def test_batches_cap_at_five(self) -> None:
        controller_html = page_html(7)
        stages = CollectionStages()
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent, browser_html=controller_html)
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        self.assertEqual(len(report["batches"]), 2)
        self.assertEqual(len(report["batches"][0]["ids"]), 5)
        self.assertEqual(len(report["batches"][1]["ids"]), 2)
        self.assertEqual(consent.kinds.count("tanda"), 2)
        self.assertEqual(len(Backlog(self.state).entries()), 7)

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
        self.assertEqual(len(Backlog(self.state).entries()), 1)

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
        self.assertEqual(Backlog(self.state).entries(), [])

    def test_confirmation_denial_stops_everything(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent(deny={"browser-confirm"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertEqual(stages.calls, [])
        self.assertEqual(Backlog(self.state).entries(), [])

    def test_tanda_denial_runs_no_stage_and_loop_guards(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent(deny={"tanda"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertIn("loop guard", report["hard_stop_reason"])
        self.assertEqual(stages.calls, [], "no stage before permission")
        self.assertEqual(Backlog(self.state).entries(), [])
        # Exactly ONE tanda ask: the loop guard fires before a second
        # identical no-progress batch is even attempted (no re-asking).
        self.assertEqual(consent.kinds.count("tanda"), 1)

    def test_verify_window_lists_exact_validated_candidate_urls(self) -> None:
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

    def test_verify_denial_emits_nothing(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent(deny={"verify"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertIn("loop guard", report["hard_stop_reason"])
        self.assertEqual(Backlog(self.state).entries(), [], "no entry without verify consent")


# --------------------------------------------------------------------------
# Whisper coordination
# --------------------------------------------------------------------------


class TestWhisperCoordination(CollectionRunTestCase):
    def test_stop_before_vision_and_restore_before_audio(self) -> None:
        stages = CollectionStages(whisper_running=True)
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 0, report.get("hard_stop_reason"))
        calls = stages.calls
        self.assertLess(
            calls.index("whisper-stop-op"),
            calls.index("ollama-start"),
            "verified whisper stop precedes any model load",
        )
        self.assertLess(
            calls.index("ollama-stop"),
            calls.index("whisper-restore-op"),
            "restore happens after the isolated server stops",
        )
        self.assertLess(
            calls.index("whisper-restore-op"),
            calls.index(f"audio:{fake_id(1)}"),
            "audio only after restore",
        )
        self.assertLess(
            calls.index(f"vision:{fake_id(1)}"),
            calls.index("whisper-restore-op"),
        )
        self.assertEqual(stages.whisper_stops, 1)
        self.assertEqual(stages.whisper_restores, 1)
        self.assertIn("whisper-stop", consent.kinds)
        self.assertIn("whisper-restore", consent.kinds)
        # GPU window consent comes before the whisper-stop window.
        self.assertLess(consent.kinds.index("gpu"), consent.kinds.index("whisper-stop"))

    def test_stop_denial_blocks_vision_without_mutation(self) -> None:
        stages = CollectionStages(whisper_running=True)
        consent = RecordingConsent(deny={"whisper-stop"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertEqual(stages.whisper_stops, 0, "denied consent stops before mutation")
        self.assertEqual(
            [c for c in stages.calls if c.startswith("vision:")],
            [],
            "no vision without the whisper precondition",
        )
        self.assertFalse(any("ollama-start" == c for c in stages.calls))
        self.assertEqual(Backlog(self.state).entries(), [])

    def test_unidentified_service_is_refused_before_consent(self) -> None:
        stages = CollectionStages(
            whisper_running=True, whisper_info=OTHER_INFO
        )
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertNotIn("whisper-stop", consent.kinds, "no consent for an unidentified service")
        self.assertEqual(stages.whisper_stops, 0)
        self.assertEqual([c for c in stages.calls if c.startswith("vision:")], [])

    def test_restore_failure_blocks_all_progress_and_later_batches(self) -> None:
        controller_html = page_html(7)  # two batches worth
        stages = CollectionStages(
            whisper_running=True, whisper_restore_fails=True
        )
        consent = RecordingConsent()
        code, report, _ = self.run_collection(
            stages, consent, browser_html=controller_html
        )
        self.assertEqual(code, 1)
        self.assertIn("whisper restoration failed", report["hard_stop_reason"])
        self.assertEqual(len(report["batches"]), 1, "no further batches after a failed restore")
        self.assertEqual(
            [c for c in stages.calls if c.startswith("audio:")],
            [],
            "audio blocked after failed restore",
        )
        self.assertTrue(
            any("no rollback" in note for note in report["batches"][0]["cleanup_notes"]),
            report["batches"][0]["cleanup_notes"],
        )

    def test_restore_authorization_denied_blocks_before_any_mutation(self) -> None:
        stages = CollectionStages(whisper_running=True)
        consent = RecordingConsent(deny={"whisper-restore"})
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        # Denial of the standing restore authorization happens BEFORE the
        # stop: no mutation at all, vision blocked.
        self.assertEqual(stages.whisper_stops, 0, "no stop without restore authorization")
        self.assertEqual(stages.whisper_restores, 0)
        self.assertEqual(
            [c for c in stages.calls if c.startswith("vision:")], [],
            "vision blocked when recovery cannot be pre-authorized",
        )
        self.assertFalse(any(c == "ollama-start" for c in stages.calls))
        failures = report["batches"][0]["failures"]
        self.assertTrue(
            any(
                "standing authorization denied" in f["reason"]
                and "no mutation" in f["reason"]
                for f in failures
            ),
            failures,
        )
        self.assertEqual(Backlog(self.state).entries(), [])

    def test_ctrl_c_during_stop_itself_reports_unknown_no_restore(self) -> None:
        """W-1r: KI inside the whisper stop after BOTH authorizations.

        The stop result is UNKNOWN: no restore is attempted (it cannot
        be verified that the stop was ours/completed), no vision/audio
        run, the run is interrupted (exit 1), and the generic
        "applicable cleanup ran" claim is replaced by the honest
        cleanup-uncertain message.
        """
        stages = CollectionStages(
            whisper_running=True, whisper_stop_raises_ki=True
        )
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])
        # Both exact authorizations were asked and granted BEFORE the
        # stop attempt — the standing consent is not weakened.
        kinds = consent.kinds
        self.assertIn("whisper-stop", kinds)
        self.assertIn("whisper-restore", kinds)
        self.assertLess(kinds.index("whisper-stop"), kinds.index("whisper-restore"))
        # The interrupted stop is never counted as completed...
        self.assertEqual(stages.whisper_stops, 0)
        # ...and NO restore, vision, audio or Ollama lifecycle ran.
        self.assertEqual(stages.whisper_restores, 0)
        self.assertNotIn("whisper-restore-op", stages.calls)
        self.assertEqual(
            [c for c in stages.calls if c.startswith(("vision:", "audio:"))], []
        )
        self.assertNotIn("ollama-start", stages.calls)
        self.assertNotIn("ollama-stop", stages.calls)
        self.assertEqual(Backlog(self.state).entries(), [])
        # Explicit UNKNOWN note without a false promise.
        batch = report["batches"][0]
        notes = " ".join(batch["cleanup_notes"])
        self.assertIn("stop result is", notes)
        self.assertIn("UNKNOWN", notes)
        self.assertIn("NO restore was attempted", notes)
        self.assertIn("no rollback", notes)
        self.assertTrue(
            any(
                f["stage"] == "whisper-stop" and "UNKNOWN" in f["reason"]
                for f in batch["failures"]
            ),
            batch["failures"],
        )
        # The generic overclaim never appears on this path; the honest
        # variant does.
        self.assertFalse(
            any("applicable cleanup ran" in m for m in self.messages),
            self.messages,
        )
        self.assertTrue(
            any("cleanup state is NOT fully known" in m for m in self.messages),
            self.messages,
        )

    def test_ctrl_c_during_vision_after_stop_restores_under_standing_auth(
        self,
    ) -> None:
        stages = CollectionStages(
            whisper_running=True, vision_raises=KeyboardInterrupt()
        )
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])
        # BOTH exact authorizations were asked BEFORE the stop, in order.
        kinds = consent.kinds
        self.assertLess(kinds.index("gpu"), kinds.index("whisper-stop"))
        self.assertLess(kinds.index("whisper-stop"), kinds.index("whisper-restore"))
        # Ollama cleanup (finally) still ran; whisper was stopped once and
        # restored once under the standing authorization — with NO window
        # asked after the interrupt.
        self.assertIn("ollama-stop", stages.calls)
        self.assertEqual(stages.whisper_stops, 1)
        self.assertEqual(stages.whisper_restores, 1)
        windows_after_interrupt = kinds[kinds.index("whisper-restore") + 1:]
        self.assertEqual(
            windows_after_interrupt, [],
            "an interrupt never triggers a new consent window",
        )
        self.assertEqual(
            [c for c in stages.calls if c.startswith("audio:")], [],
            "no audio after an interrupt in the GPU window",
        )
        notes = " ".join(report["batches"][0]["cleanup_notes"])
        self.assertIn("restored and healthy", notes)

    def test_ctrl_c_during_vision_with_restore_failure_blocks_everything(
        self,
    ) -> None:
        stages = CollectionStages(
            whisper_running=True,
            vision_raises=KeyboardInterrupt(),
            whisper_restore_fails=True,
        )
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])
        self.assertIn("ollama-stop", stages.calls, "Ollama finally cleanup kept")
        self.assertEqual(stages.whisper_restores, 1, "one attempt, no retry")
        self.assertEqual(
            [c for c in stages.calls if c.startswith("audio:")], [],
            "audio blocked when recovery fails after an interrupt",
        )
        notes = " ".join(report["batches"][0]["cleanup_notes"])
        self.assertIn("whisper restore failed", notes)
        self.assertIn("no rollback", notes)

    def test_ctrl_c_during_vision_with_health_failure_blocks(self) -> None:
        stages = CollectionStages(
            whisper_running=True,
            vision_raises=KeyboardInterrupt(),
            whisper_healthy=False,
        )
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])
        self.assertEqual(stages.whisper_restores, 1)
        self.assertEqual(
            [c for c in stages.calls if c.startswith("audio:")], []
        )
        notes = " ".join(report["batches"][0]["cleanup_notes"])
        self.assertIn("NOT healthy", notes)
        self.assertIn("no rollback", notes)

    def test_ctrl_c_during_restore_itself_reports_unknown_state(self) -> None:
        stages = CollectionStages(
            whisper_running=True,
            vision_raises=KeyboardInterrupt(),
            whisper_restore_raises_ki=True,
        )
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])
        self.assertEqual(
            stages.whisper_restores, 1, "the interrupted restore is never retried"
        )
        self.assertEqual(
            [c for c in stages.calls if c.startswith("audio:")], []
        )
        notes = " ".join(report["batches"][0]["cleanup_notes"])
        self.assertIn("UNKNOWN", notes)
        self.assertIn("no rollback", notes)


# --------------------------------------------------------------------------
# Synthesis API coordination
# --------------------------------------------------------------------------


class TestSynthesisApi(CollectionRunTestCase):
    def api_window(self, consent: RecordingConsent) -> ConsentWindow:
        windows = consent.of("synthesis-api")
        self.assertTrue(windows, "synthesis API consent window must be asked")
        return windows[0]

    def test_window_shows_origin_model_and_limits_never_the_key(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, _, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 0)
        window = self.api_window(consent)
        self.assertIn("https://api.example.com/v1", " ".join(window.destinations))
        self.assertIn("glm-5.3-flash", " ".join(window.operations))
        blob = json.dumps(window.to_dict())
        self.assertNotIn(API_ENV[ENV_API_KEY], blob)

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

    def test_denial_is_blocked_and_resumable_without_rerunning_media(self) -> None:
        seed_scan_for_run(self.state, [0])
        seed_job(
            self.state,
            fake_id(0),
            complete=("fetch", "prepare", "vision", "audio"),
            evidence=True,
        )
        stages = CollectionStages()
        consent = RecordingConsent(deny={"synthesis-api"})
        code, report, _ = self.run_collection(
            stages, consent, browser_html=page_html(1)
        )
        self.assertEqual(code, 1)
        self.assertEqual(
            [c for c in stages.calls if c.startswith(("vision:", "audio:"))],
            [],
            "completed media stages are not repeated on a synthesis stop",
        )
        self.assertEqual(Backlog(self.state).entries(), [])
        # Resume with consent granted: synthesis and verify run; media
        # stages stay untouched.
        stages2 = CollectionStages()
        consent2 = RecordingConsent()
        code2, report2, _ = self.run_collection(
            stages2, consent2, browser_html=page_html(1)
        )
        self.assertEqual(code2, 0, report2.get("hard_stop_reason"))
        self.assertEqual(
            [c for c in stages2.calls if c.startswith(("vision:", "audio:"))], []
        )
        self.assertIn("verify:" + fake_id(0), stages2.calls)
        self.assertEqual(len(Backlog(self.state).entries()), 1)

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
        self.assertNotIn("synthesis-api", consent.kinds, "no API window without config")
        waiting = report["batches"][0]["waiting_for_synthesis"]
        self.assertEqual(waiting, [fake_id(0), fake_id(1), fake_id(2)])
        self.assertEqual(Backlog(self.state).entries(), [])
        self.assertEqual(report["preflight"]["text_api_configured"], False)


# --------------------------------------------------------------------------
# Ollama discipline through collection-run
# --------------------------------------------------------------------------


class TestOllama(CollectionRunTestCase):
    def test_preexisting_isolated_server_is_never_adopted(self) -> None:
        stages = CollectionStages(ollama_already_running=True)
        consent = RecordingConsent()
        code, report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 1)
        self.assertFalse(any(c == "ollama-start" for c in stages.calls))
        failures = report["batches"][0]["failures"]
        self.assertTrue(
            any("never adopts" in f["reason"] for f in failures), failures
        )

    def test_server_started_is_stopped_at_window_end(self) -> None:
        stages = CollectionStages()
        consent = RecordingConsent()
        code, _report, _ = self.run_collection(stages, consent)
        self.assertEqual(code, 0)
        self.assertIn("ollama-start", stages.calls)
        self.assertIn("ollama-stop", stages.calls)


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
        self.assertTrue(any("text API" in d for d in report["planned_destinations"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
