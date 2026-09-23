"""Guided-run coordinator tests: fake stages, scripted consent, temp state.

Zero network, zero GPU, zero podman/ollama: every stage boundary is a
double and every consent answer is scripted. The REAL stage behavior
(gates, cleanup, fail-closed preconditions) is covered by the existing
per-stage suites; these tests prove the COORDINATION contract —
selection, consent windows, ordering, stop-on-failure, the synthesis
boundary and honest reporting.
"""

from __future__ import annotations

import dataclasses
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import config, guided
from tiktok_ingest.collection import (
    CollectionScanStore,
    extract_page_items,
    merge_observation,
    parse_collection_url,
    sync_inventory_from_scan,
)
from tiktok_ingest.contracts import (
    JobManifest,
    ProcessedEntry,
    StageOutcome,
    StageRecord,
    StageResult,
)
from tiktok_ingest.guided import (
    ConsentWindow,
    ConsoleConsentProvider,
    GuidedError,
    GUIDED_MAX_BATCH,
    parse_synthesis_args,
    run_guided,
)
from tiktok_ingest.state import (
    Backlog,
    Blocklist,
    BlocklistEntry,
    InventoryStore,
    Processed,
    StateRoot,
)
from tiktok_ingest.tests.fixtures import (
    FIXED_NOW,
    MEDIA_SHA256,
    sample_synthesis_document,
    write_sanitized_evidence,
)

COLLECTION_URL = "https://www.tiktok.com/@fixture_author/collection/guided-col"
REF = parse_collection_url(COLLECTION_URL)


def fake_id(n: int) -> str:
    return str(2_000_000_000_000_000_000 + n)


def seed_scan(
    state: StateRoot,
    video_ns: list[int],
    *,
    photo_ns: list[int] | None = None,
) -> None:
    """Persist a scan state through the REAL merge path (no shortcuts)."""
    photo_ns = photo_ns or []
    links = [
        f'<a href="/@fixture_author/video/{fake_id(n)}">item</a>'
        for n in video_ns
    ]
    links += [
        f'<a href="/@fixture_author/photo/{fake_id(n)}">item</a>'
        for n in photo_ns
    ]
    html = (
        "<!DOCTYPE html><html><body>"
        f'<div data-testid="collection-container">{"".join(links)}</div>'
        "</body></html>"
    )
    observation = extract_page_items(
        html, "data-testid", "collection-container"
    )
    merged = merge_observation(
        None,
        REF,
        observation,
        declared_count=len(video_ns) + len(photo_ns),
        end_evidence="fixture end-of-list marker observed",
        stop_reason=None,
        captured_at=FIXED_NOW,
        container_attr="data-testid",
        container_value="collection-container",
    )
    CollectionScanStore(state).save(merged)
    sync_inventory_from_scan(state, merged)


def seed_job(
    state: StateRoot,
    video_id: str,
    *,
    complete: tuple[str, ...] = ("fetch", "prepare"),
    fingerprints: tuple[str, ...] | None = None,
    evidence: bool = False,
    canonical_url: str | None = None,
    author: str | None = "fixture-author",
) -> Path:
    """Seed one processed job whose manifest records the given stages."""
    run_id = f"run-{video_id[-6:]}"
    run_dir = state.run_dir(run_id, video_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = JobManifest(
        video_id=video_id,
        media_sha256=MEDIA_SHA256,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )
    for stage in complete:
        if stage == "fetch":
            manifest.extraction = {"outcome": "complete", "reason": "fixture"}
            continue
        manifest.stages[stage] = StageRecord(
            stage=stage,
            result=StageResult(
                outcome=StageOutcome.COMPLETE, reason="fixture"
            ),
            fingerprint={
                "stage": stage,
                "input_sha256": MEDIA_SHA256,
                "versions": {},
            },
            input_sha256=MEDIA_SHA256,
        )
    (run_dir / "meta.json").write_text(
        json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
    )
    if evidence:
        write_sanitized_evidence(run_dir)
    if fingerprints is None:
        fingerprints = tuple(complete)
    Processed(state).record(
        ProcessedEntry(
            video_id=video_id,
            media_sha256=MEDIA_SHA256,
            completed_at=FIXED_NOW,
            stage_fingerprints={
                name: {
                    "stage": name,
                    "input_sha256": MEDIA_SHA256,
                    "versions": {},
                }
                for name in fingerprints
            },
            artifacts=[f"runs/{run_id}/{video_id}"],
        )
    )
    from tiktok_ingest.contracts import InventoryEntry

    entries = InventoryStore(state).load()
    if all(entry.stable_id != video_id for entry in entries):
        entries.append(
            InventoryEntry(
                source="tiktok",
                stable_id=video_id,
                canonical_url=canonical_url
                or f"https://www.tiktok.com/@fixture-author/video/{video_id}",
                input_origin="direct-url",
                discovered_at=FIXED_NOW,
                author=author,
            )
        )
        InventoryStore(state).save(entries)
    return run_dir


@dataclasses.dataclass(frozen=True)
class FakeOutcome:
    outcome: str
    reason: str = "fixture"
    reused: bool = False


class FakeStages:
    """Stage doubles that record order and return scripted outcomes.

    ``synthesize`` defaults to the REAL ingestion stage (pure local
    validation) so the synthesis boundary is tested against the true
    contract; everything else is a double. Call :meth:`functions` to
    build the production-shaped :class:`StageFunctions` from them.
    """

    def __init__(
        self,
        *,
        fetch_outcome: FakeOutcome | None = None,
        prepare_outcome: FakeOutcome | None = None,
        vision_outcomes: dict[str, FakeOutcome] | None = None,
        audio_outcome: FakeOutcome | None = None,
        verify_outcome: FakeOutcome | None = None,
        whisper_running: bool = False,
        whisper_healthy: bool = True,
        ollama_already_running: bool = False,
        stop_raises: bool = False,
        vision_raises: BaseException | None = None,
        real_synthesis: bool = True,
        verify_emits_pending: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.fetch_outcome = fetch_outcome or FakeOutcome("complete")
        self.prepare_outcome = prepare_outcome or FakeOutcome("complete")
        self.vision_outcomes = vision_outcomes or {}
        self.audio_outcome = audio_outcome or FakeOutcome("complete")
        self.verify_outcome = verify_outcome or FakeOutcome("complete")
        self.whisper_running = whisper_running
        self.whisper_healthy = whisper_healthy
        self.ollama_already_running = ollama_already_running
        self.stop_raises = stop_raises
        self.vision_raises = vision_raises
        self.real_synthesis = real_synthesis
        self.verify_emits_pending = verify_emits_pending
        self.server_started = 0
        self.server_stopped = 0

    def functions(self) -> guided.StageFunctions:
        return guided.StageFunctions(
            fetch=self.do_fetch,
            prepare=self.do_prepare,
            vision=self.do_vision,
            audio=self.do_audio,
            synthesize=self.do_synthesize,
            verify=self.do_verify,
            whisper_stopped=self.do_whisper_stopped,
            whisper_health=self.do_whisper_health,
            ollama_reachable=self.do_ollama_reachable,
            ollama_start=self.do_ollama_start,
            ollama_wait_ready=self.do_ollama_wait_ready,
            ollama_stop=self.do_ollama_stop,
        )

    def do_fetch(self, url: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"fetch:{url.rsplit('/', 1)[-1]}")
        return self.fetch_outcome

    def do_prepare(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"prepare:{video_id}")
        return self.prepare_outcome

    def do_vision(self, video_id: str, state: StateRoot) -> FakeOutcome:
        if self.vision_raises is not None:
            self.calls.append(f"vision-raise:{video_id}")
            raise self.vision_raises
        self.calls.append(f"vision:{video_id}")
        return self.vision_outcomes.get(video_id, FakeOutcome("complete"))

    def do_audio(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"audio:{video_id}")
        return self.audio_outcome

    def do_synthesize(
        self, video_id: str, state: StateRoot, from_file: Path | None
    ) -> Any:
        self.calls.append(f"synthesize:{video_id}")
        if self.real_synthesis:
            from tiktok_ingest.synthesis import run_synthesis_stage

            return run_synthesis_stage(
                video_id, state=state, from_file=from_file
            )
        return FakeOutcome("complete")

    def do_verify(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"verify:{video_id}")
        if self.verify_emits_pending:
            from tiktok_ingest.contracts import BacklogEntry
            from tiktok_ingest.resume import emit_backlog_entry

            entry = BacklogEntry(
                id=video_id,
                url=f"https://www.tiktok.com/@fixture-author/video/{video_id}",
                ingested_at=FIXED_NOW,
                status="pending",
                classification=None,
                unknown_classification_reason="fixture: guided test emission",
                entities=[],
                claims=[],
            )
            emit_backlog_entry(Backlog(state), entry)
        return self.verify_outcome

    def do_whisper_stopped(self) -> None:
        self.calls.append("whisper-stopped-check")
        if self.whisper_running:
            raise RuntimeError(
                "stop the whisper service first (operator-authorized action): "
                "still running: ['voice-assistant-whisper']"
            )

    def do_whisper_health(self) -> tuple[bool, str]:
        self.calls.append("whisper-health-check")
        if self.whisper_healthy:
            return True, "healthy"
        return False, "whisper health endpoint unreachable: fixture"

    def do_ollama_reachable(self) -> bool:
        self.calls.append("ollama-reachable-check")
        return self.ollama_already_running

    def do_ollama_start(self) -> object:
        self.calls.append("ollama-start")
        self.server_started += 1
        return object()

    def do_ollama_wait_ready(self, handle: object) -> str:
        self.calls.append("ollama-wait-ready")
        return config.GATES.isolated_ollama_version

    def do_ollama_stop(self, handle: object) -> None:
        self.calls.append("ollama-stop")
        self.server_stopped += 1
        if self.stop_raises:
            raise RuntimeError("fixture: SIGTERM did not stop the server")


class ScriptedConsent(guided.ConsentProvider):
    """Consent double: a fixed script of (expected kind, granted) answers."""

    def __init__(self, script: list[tuple[str, bool]]) -> None:
        self.script = list(script)
        self.windows: list[ConsentWindow] = []

    def ask(self, window: ConsentWindow) -> bool:
        self.windows.append(window)
        if not self.script:
            raise AssertionError(
                f"unexpected consent window {window.kind!r} with no scripted "
                "answer (the test must script every window it expects)"
            )
        expected_kind, granted = self.script.pop(0)
        if window.kind != expected_kind:
            raise AssertionError(
                f"consent window order mismatch: expected {expected_kind!r}, "
                f"got {window.kind!r}"
            )
        return granted

    @property
    def asked_kinds(self) -> list[str]:
        return [window.kind for window in self.windows]


GRANT_ALL = [
    ("tanda", True),
    ("fetch", True),
    ("prepare", True),
    ("gpu", True),
    ("verify", True),
]


class GuidedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_guided_with(
        self,
        stages: FakeStages,
        consent: ScriptedConsent,
        *,
        collection: str | None = COLLECTION_URL,
        ids: list[str] | None = None,
        synthesis: dict[str, Path] | None = None,
        dry_run: bool = False,
        limit: int | None = None,
    ) -> tuple[int, dict[str, Any], list[str]]:
        messages: list[str] = []
        exit_code, report = run_guided(
            state=self.state,
            collection=collection,
            ids=ids,
            limit=limit,
            synthesis=synthesis,
            dry_run=dry_run,
            stages=stages.functions(),
            consent=consent,
            out=messages.append,
        )
        return exit_code, report, messages


# --------------------------------------------------------------------------
# Selection and planning
# --------------------------------------------------------------------------


class TestSelection(GuidedTestCase):
    def test_plan_selection_caps_at_five_and_notes_remainder(self) -> None:
        seed_scan(self.state, list(range(7)))
        stages = FakeStages()
        code, report, _ = self.run_guided_with(stages, ScriptedConsent([]), dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(report["selected"]), 5)
        self.assertTrue(
            any("2 further new-processable" in note for note in report["notes"])
        )

    def test_plan_selection_excludes_photo_blocked_and_complete(self) -> None:
        seed_scan(self.state, [1, 2, 3], photo_ns=[9])
        seed_job(self.state, fake_id(2), complete=("fetch",), fingerprints=("fetch",))
        # Make id 2 fully complete (emit fingerprint) and block id 3.
        entry = Processed(self.state).get(fake_id(2))
        entry.stage_fingerprints["emit"] = {"stage": "emit"}
        Processed(self.state).record(entry)
        Blocklist(self.state).reject(
            BlocklistEntry(video_id=fake_id(3), reason="fixture rejection", rejected_at=FIXED_NOW)
        )
        code, report, _ = self.run_guided_with(
            FakeStages(), ScriptedConsent([]), dry_run=True
        )
        self.assertEqual(code, 0)
        selected_ids = [item["id"] for item in report["selected"]]
        self.assertEqual(selected_ids, [fake_id(1)])
        excluded = {item["id"]: item["classification"] for item in report["excluded"]}
        self.assertEqual(excluded[fake_id(2)], "processed_complete")
        self.assertEqual(excluded[fake_id(3)], "rejected")
        self.assertEqual(excluded[fake_id(9)], "unsupported_photo")

    def test_explicit_ids_select_new_and_partial(self) -> None:
        seed_scan(self.state, [1, 2])
        seed_job(self.state, fake_id(2), complete=("fetch", "prepare"))
        code, report, _ = self.run_guided_with(
            FakeStages(),
            ScriptedConsent([]),
            ids=[fake_id(1), fake_id(2)],
            dry_run=True,
        )
        self.assertEqual(code, 0)
        by_id = {item["id"]: item for item in report["selected"]}
        self.assertEqual(by_id[fake_id(1)]["classification"], "new_processable")
        self.assertEqual(by_id[fake_id(2)]["classification"], "partial_resumable")
        self.assertIn("prepare", by_id[fake_id(2)]["stages"])
        self.assertEqual(by_id[fake_id(2)]["stages"]["prepare"], "complete")

    def test_unknown_id_is_a_hard_error(self) -> None:
        seed_scan(self.state, [1])
        with self.assertRaises(GuidedError) as ctx:
            self.run_guided_with(
                FakeStages(),
                ScriptedConsent([]),
                ids=[fake_id(1), "9999999999999999999"],
                dry_run=True,
            )
        self.assertIn("unknown video id(s): 9999999999999999999", str(ctx.exception))

    def test_duplicate_id_is_ambiguous(self) -> None:
        seed_scan(self.state, [1])
        with self.assertRaises(GuidedError) as ctx:
            self.run_guided_with(
                FakeStages(),
                ScriptedConsent([]),
                ids=[fake_id(1), fake_id(1)],
                dry_run=True,
            )
        self.assertIn("appears more than once", str(ctx.exception))

    def test_explicit_selection_over_cap_is_an_error(self) -> None:
        seed_scan(self.state, list(range(6)))
        with self.assertRaises(GuidedError) as ctx:
            self.run_guided_with(
                FakeStages(),
                ScriptedConsent([]),
                ids=[fake_id(n) for n in range(6)],
                dry_run=True,
            )
        self.assertIn("batch cap", str(ctx.exception))

    def test_limit_bounds_are_enforced(self) -> None:
        seed_scan(self.state, [1])
        for bad in (0, GUIDED_MAX_BATCH + 1):
            with self.assertRaises(GuidedError):
                self.run_guided_with(
                    FakeStages(),
                    ScriptedConsent([]),
                    ids=[fake_id(1)],
                    dry_run=True,
                    limit=bad,
                )

    def test_stale_plan_is_not_authority_current_state_wins(self) -> None:
        # A plan printed earlier classified id 1 as new_processable; the
        # operator rejected it afterwards. guided-run recomputes against
        # the CURRENT blocklist and excludes it.
        seed_scan(self.state, [1, 2])
        Blocklist(self.state).reject(
            BlocklistEntry(video_id=fake_id(1), reason="late rejection", rejected_at=FIXED_NOW)
        )
        code, report, _ = self.run_guided_with(
            FakeStages(), ScriptedConsent([]), dry_run=True
        )
        self.assertEqual([item["id"] for item in report["selected"]], [fake_id(2)])
        self.assertEqual(
            report["excluded"][0]["classification"], "rejected"
        )

    def test_limit_caps_explicit_selection(self) -> None:
        seed_scan(self.state, [1, 2, 3, 4])
        # Four explicit processable ids above limit 3 is an error, never
        # a silent truncation of the operator's list.
        with self.assertRaises(GuidedError):
            self.run_guided_with(
                FakeStages(),
                ScriptedConsent([]),
                ids=[fake_id(n) for n in (1, 2, 3, 4)],
                dry_run=True,
                limit=3,
            )
        code, report, _ = self.run_guided_with(
            FakeStages(),
            ScriptedConsent([]),
            ids=[fake_id(n) for n in (1, 2, 3)],
            dry_run=True,
            limit=3,
        )
        self.assertEqual(len(report["selected"]), 3)

    def test_dry_run_shows_stage_states_per_video(self) -> None:
        seed_scan(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch",))
        code, report, _ = self.run_guided_with(
            FakeStages(), ScriptedConsent([]), ids=[fake_id(1)], dry_run=True
        )
        stages = report["selected"][0]["stages"]
        self.assertEqual(stages["fetch"], "complete")
        self.assertEqual(stages["prepare"], "pending")
        self.assertEqual(stages["verify"], "pending")


# --------------------------------------------------------------------------
# Consent discipline
# --------------------------------------------------------------------------


class TestConsentDiscipline(GuidedTestCase):
    def test_dry_run_prompts_nothing_and_calls_nothing(self) -> None:
        seed_scan(self.state, [1])
        stages = FakeStages()
        consent = ScriptedConsent([])  # any window raises the test
        code, report, _ = self.run_guided_with(stages, consent, dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(stages.calls, [])
        self.assertTrue(
            any("dry run: zero consent prompts" in n for n in report["notes"])
        )

    def test_tanda_denial_runs_no_stage(self) -> None:
        seed_scan(self.state, [1])
        stages = FakeStages()
        code, report, _ = self.run_guided_with(
            stages, ScriptedConsent([("tanda", False)])
        )
        self.assertEqual(code, 0)
        self.assertEqual(stages.calls, [])
        self.assertTrue(any("tanda confirmation denied" in n for n in report["notes"]))

    def test_fetch_denial_runs_no_stage(self) -> None:
        seed_scan(self.state, [1])
        stages = FakeStages()
        code, report, _ = self.run_guided_with(
            stages, ScriptedConsent([("tanda", True), ("fetch", False)])
        )
        self.assertEqual(code, 0)
        self.assertEqual(stages.calls, [])
        self.assertTrue(any("fetch window denied" in n for n in report["notes"]))

    def test_prepare_denial_stops_before_container(self) -> None:
        seed_scan(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch",))
        stages = FakeStages()
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("prepare", False)]),
            ids=[fake_id(1)],
        )
        self.assertEqual(code, 0)
        self.assertEqual(stages.calls, [])
        self.assertTrue(any("prepare window denied" in n for n in report["notes"]))

    def test_windows_name_exact_ids_and_destinations(self) -> None:
        seed_scan(self.state, [1, 2])
        stages = FakeStages()
        consent = ScriptedConsent(
            [("tanda", True), ("fetch", True), ("prepare", False)]
        )
        self.run_guided_with(stages, consent)
        fetch_window = consent.windows[1]
        self.assertEqual(fetch_window.kind, "fetch")
        self.assertEqual(
            set(fetch_window.ids), {fake_id(1), fake_id(2)}
        )
        self.assertEqual(
            set(fetch_window.destinations),
            {
                f"https://www.tiktok.com/@fixture_author/video/{fake_id(1)}",
                f"https://www.tiktok.com/@fixture_author/video/{fake_id(2)}",
            },
        )
        # Authorization is scoped to THIS invocation's ids: an id outside
        # the selection never appears in any window.
        for window in consent.windows:
            self.assertNotIn("9999999999999999999", window.ids)
            self.assertNotIn("9999999999999999999", window.destinations)

    def test_gpu_window_names_vision_and_audio_possible_ids(self) -> None:
        seed_scan(self.state, [1, 2])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare"))
        # id 1: vision pending (audio may follow); id 2: fetched, needs
        # fetch+prepare first so it must NOT appear in the gpu window.
        stages = FakeStages()
        consent = ScriptedConsent(
            [("tanda", True), ("fetch", True), ("prepare", False)]
        )
        self.run_guided_with(stages, consent)
        gpu_windows = [w for w in consent.windows if w.kind == "gpu"]
        self.assertEqual(gpu_windows, [])

    def test_authorization_does_not_persist_across_invocations(self) -> None:
        seed_scan(self.state, [1])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare", "vision", "audio"), evidence=True)
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(sample_synthesis_document()), encoding="utf-8")
        first = ScriptedConsent([("tanda", True), ("verify", True)])
        code1, _, _ = self.run_guided_with(
            FakeStages(verify_emits_pending=True),
            first,
            synthesis={fake_id(1): doc},
            ids=[fake_id(1)],
        )
        self.assertEqual(code1, 0)
        # Second invocation must ask again — no remembered grant.
        second = ScriptedConsent([("tanda", False)])
        code2, report2, _ = self.run_guided_with(
            FakeStages(), second, synthesis={fake_id(1): doc}, ids=[fake_id(1)]
        )
        self.assertEqual(code2, 0)
        self.assertEqual(second.asked_kinds, ["tanda"])
        self.assertTrue(any("denied" in n for n in report2["notes"]))


class TestConsoleConsent(unittest.TestCase):
    def _window(self) -> ConsentWindow:
        return ConsentWindow(
            kind="fetch",
            title="fixture window",
            ids=("1",),
            destinations=("https://fixture.example/video/1",),
            operations=("fixture operation",),
            limits=("fixture limit",),
            effects=("fixture effect",),
        )

    def test_eof_denies(self) -> None:
        provider = ConsoleConsentProvider(
            stdin=io.StringIO(""), out=lambda *a, **k: None
        )
        self.assertFalse(provider.ask(self._window()))

    def test_explicit_no_denies(self) -> None:
        provider = ConsoleConsentProvider(
            stdin=io.StringIO("no\n"), out=lambda *a, **k: None
        )
        self.assertFalse(provider.ask(self._window()))

    def test_only_explicit_yes_grants(self) -> None:
        for answer in ("yes", "y", "YES"):
            provider = ConsoleConsentProvider(
                stdin=io.StringIO(answer + "\n"), out=lambda *a, **k: None
            )
            self.assertTrue(provider.ask(self._window()), answer)
        for answer in ("ok", "sure", "1", ""):
            provider = ConsoleConsentProvider(
                stdin=io.StringIO(answer + "\n"), out=lambda *a, **k: None
            )
            self.assertFalse(provider.ask(self._window()), answer)

    def test_non_interactive_environment_never_grants(self) -> None:
        class NonTTY:
            def isatty(self) -> bool:
                return False

            def readline(self) -> str:  # would grant if asked
                return "yes\n"

        import sys as _sys

        original = _sys.stdin
        _sys.stdin = NonTTY()
        try:
            provider = ConsoleConsentProvider(out=lambda *a, **k: None)
            self.assertFalse(provider.ask(self._window()))
        finally:
            _sys.stdin = original


# --------------------------------------------------------------------------
# GPU window: order, lifecycle and stop-on-failure
# --------------------------------------------------------------------------


class TestGpuWindow(GuidedTestCase):
    def _seed_two_prepared(self) -> None:
        seed_scan(self.state, [1, 2])
        seed_job(self.state, fake_id(1), complete=("fetch", "prepare"))
        seed_job(self.state, fake_id(2), complete=("fetch", "prepare"))

    def test_vision_all_then_verified_stop_then_audio(self) -> None:
        self._seed_two_prepared()
        stages = FakeStages()
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 0)
        calls = stages.calls
        self.assertLess(calls.index("ollama-start"), calls.index(f"vision:{fake_id(1)}"))
        self.assertLess(calls.index(f"vision:{fake_id(1)}"), calls.index(f"vision:{fake_id(2)}"))
        self.assertLess(calls.index(f"vision:{fake_id(2)}"), calls.index("ollama-stop"))
        self.assertLess(calls.index("ollama-stop"), calls.index("whisper-health-check"))
        self.assertLess(calls.index("whisper-health-check"), calls.index(f"audio:{fake_id(1)}"))
        self.assertLess(calls.index(f"audio:{fake_id(1)}"), calls.index(f"audio:{fake_id(2)}"))
        self.assertEqual(stages.server_started, 1)
        self.assertEqual(stages.server_stopped, 1)

    def test_whisper_running_blocks_vision_before_any_load(self) -> None:
        self._seed_two_prepared()
        stages = FakeStages(whisper_running=True)
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 1)
        self.assertNotIn("ollama-start", stages.calls)
        self.assertNotIn(f"vision:{fake_id(1)}", stages.calls)
        vision_failures = [f for f in report["failures"] if f["stage"] == "vision"]
        self.assertEqual(len(vision_failures), 2)
        self.assertIn("stop the whisper service first", vision_failures[0]["reason"])

    def test_whisper_running_does_not_block_audio_of_vision_complete_ids(self) -> None:
        seed_scan(self.state, [1])
        seed_job(
            self.state, fake_id(1), complete=("fetch", "prepare", "vision")
        )
        stages = FakeStages(whisper_running=True)
        code, _, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1)],
        )
        self.assertEqual(code, 0)
        self.assertIn(f"audio:{fake_id(1)}", stages.calls)
        self.assertNotIn("ollama-start", stages.calls)

    def test_fired_gate_ends_window_no_further_gpu_load(self) -> None:
        self._seed_two_prepared()
        stages = FakeStages(
            vision_outcomes={
                fake_id(1): FakeOutcome(
                    "failed", "swap I/O delta exceeds the limit; window closed"
                )
            }
        )
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 1)
        self.assertNotIn(f"vision:{fake_id(2)}", stages.calls)
        self.assertNotIn(f"audio:{fake_id(1)}", stages.calls)
        self.assertNotIn(f"audio:{fake_id(2)}", stages.calls)
        # The server this run started is still stopped (cleanup runs).
        self.assertEqual(stages.server_stopped, 1)

    def test_preexisting_ollama_is_never_adopted(self) -> None:
        self._seed_two_prepared()
        stages = FakeStages(ollama_already_running=True)
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 1)
        self.assertNotIn("ollama-start", stages.calls)
        reason = report["failures"][0]["reason"]
        self.assertIn("never adopts a preexisting process", reason)

    def test_interrupt_runs_cleanup_and_preserves_partials(self) -> None:
        self._seed_two_prepared()
        stages = FakeStages(vision_raises=KeyboardInterrupt())
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])
        self.assertEqual(stages.server_stopped, 1)
        self.assertNotIn("audio", " ".join(stages.calls))

    def test_cleanup_failure_blocks_audio_explicitly(self) -> None:
        self._seed_two_prepared()
        stages = FakeStages(stop_raises=True)
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 1)
        self.assertIn(f"vision:{fake_id(1)}", stages.calls)
        self.assertNotIn(f"audio:{fake_id(1)}", stages.calls)
        self.assertTrue(
            any("ISOLATED OLLAMA STOP FAILED" in n for n in report["cleanup_notes"])
        )
        self.assertTrue(
            any("audio NOT attempted" in n for n in report["notes"])
        )

    def test_gpu_window_not_asked_when_nothing_pending(self) -> None:
        seed_scan(self.state, [1])
        seed_job(
            self.state,
            fake_id(1),
            complete=("fetch", "prepare", "vision", "audio"),
            evidence=True,
        )
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(sample_synthesis_document()), encoding="utf-8")
        consent = ScriptedConsent([("tanda", True), ("verify", False)])
        code, _, _ = self.run_guided_with(
            FakeStages(), consent, synthesis={fake_id(1): doc}, ids=[fake_id(1)]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("gpu", consent.asked_kinds)

    def test_gpu_denial_starts_nothing(self) -> None:
        self._seed_two_prepared()
        stages = FakeStages()
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", False)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 0)
        self.assertEqual(stages.calls, [])
        self.assertTrue(any("gpu window denied" in n for n in report["notes"]))


# --------------------------------------------------------------------------
# Synthesis boundary and verification window
# --------------------------------------------------------------------------


class TestSynthesisBoundary(GuidedTestCase):
    def _seed_ready_for_synthesis(self, video_id: str) -> Path:
        seed_scan(self.state, [1])
        return seed_job(
            self.state,
            video_id,
            complete=("fetch", "prepare", "vision", "audio"),
            evidence=True,
        )

    def test_missing_document_is_a_waiting_state_with_paths(self) -> None:
        run_dir = self._seed_ready_for_synthesis(fake_id(1))
        code, report, messages = self.run_guided_with(
            FakeStages(),
            ScriptedConsent([("tanda", True)]),
            ids=[fake_id(1)],
        )
        self.assertEqual(code, 0)
        self.assertEqual(report["waiting_for_synthesis"], [fake_id(1)])
        joined = "\n".join(messages)
        self.assertIn(f"{run_dir}/video.md", joined)
        self.assertIn(f"{run_dir}/audio.md", joined)
        self.assertIn(f"--synthesis {fake_id(1)}=<path>", joined)
        self.assertIn("no model calls", joined)

    def test_malformed_document_fails_and_verify_window_not_asked(self) -> None:
        self._seed_ready_for_synthesis(fake_id(1))
        doc = Path(self.tmp.name) / "broken.json"
        doc.write_text("{not-json", encoding="utf-8")
        consent = ScriptedConsent([("tanda", True), ("verify", True)])
        code, report, _ = self.run_guided_with(
            FakeStages(), consent, synthesis={fake_id(1): doc}, ids=[fake_id(1)]
        )
        self.assertEqual(code, 1)
        synthesis_failure = [
            f for f in report["failures"] if f["stage"] == "synthesis"
        ]
        self.assertTrue(synthesis_failure)
        # The scripted verify grant was never consumed: the window is
        # asked ONLY after a valid document with known URLs exists.
        self.assertEqual(consent.script, [("verify", True)])

    def test_valid_document_leads_to_verify_with_exact_urls(self) -> None:
        self._seed_ready_for_synthesis(fake_id(1))
        document = sample_synthesis_document()
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(document), encoding="utf-8")
        stages = FakeStages(verify_emits_pending=True)
        consent = ScriptedConsent([("tanda", True), ("verify", True)])
        code, report, _ = self.run_guided_with(
            stages, consent, synthesis={fake_id(1): doc}, ids=[fake_id(1)]
        )
        self.assertEqual(code, 0)
        verify_window = [w for w in consent.windows if w.kind == "verify"][0]
        self.assertEqual(
            list(verify_window.destinations),
            ["https://github.com/fixture/tool"],
        )
        self.assertIn(f"verify:{fake_id(1)}", stages.calls)

    def test_verify_denial_emits_nothing(self) -> None:
        self._seed_ready_for_synthesis(fake_id(1))
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(sample_synthesis_document()), encoding="utf-8")
        stages = FakeStages(verify_emits_pending=True)
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("verify", False)]),
            synthesis={fake_id(1): doc},
            ids=[fake_id(1)],
        )
        self.assertEqual(code, 0)
        self.assertNotIn(f"verify:{fake_id(1)}", stages.calls)
        self.assertFalse(Backlog(self.state).has(fake_id(1)))
        self.assertTrue(any("verify window denied" in n for n in report["notes"]))

    def test_verify_window_without_candidate_urls_says_so(self) -> None:
        self._seed_ready_for_synthesis(fake_id(1))
        document = sample_synthesis_document()
        document["entities"] = [{"name": "NoURL Entity"}]
        document["claims"][0]["candidate_urls"] = []
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(document), encoding="utf-8")
        stages = FakeStages(verify_emits_pending=True)
        consent = ScriptedConsent([("tanda", True), ("verify", True)])
        code, _report, _ = self.run_guided_with(
            stages, consent, synthesis={fake_id(1): doc}, ids=[fake_id(1)]
        )
        self.assertEqual(code, 0)
        verify_window = [w for w in consent.windows if w.kind == "verify"][0]
        self.assertIn("no candidate URLs", verify_window.destinations[0])
        self.assertIn(f"verify:{fake_id(1)}", stages.calls)

    def test_pending_entries_stay_pending_and_review_is_referred(self) -> None:
        self._seed_ready_for_synthesis(fake_id(1))
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(sample_synthesis_document()), encoding="utf-8")
        code, report, _ = self.run_guided_with(
            FakeStages(verify_emits_pending=True),
            ScriptedConsent([("tanda", True), ("verify", True)]),
            synthesis={fake_id(1): doc},
            ids=[fake_id(1)],
        )
        self.assertEqual(code, 0)
        entries = Backlog(self.state).entries()
        self.assertEqual(entries[0]["status"], "pending")
        self.assertFalse((self.state.root / "blocklist.json").exists())
        self.assertIn("backlog-list", report["review_next"])
        self.assertIn("backlog-apply", report["review_next"])

    def test_complete_is_not_confirmed_is_kept_separate(self) -> None:
        self._seed_ready_for_synthesis(fake_id(1))
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(sample_synthesis_document()), encoding="utf-8")
        code, report, _ = self.run_guided_with(
            FakeStages(verify_emits_pending=True),
            ScriptedConsent([("tanda", True), ("verify", True)]),
            synthesis={fake_id(1): doc},
            ids=[fake_id(1)],
        )
        self.assertEqual(code, 0)
        note = report["complete_is_not_confirmed"]
        self.assertIn("'complete' means the machinery finished", note)
        self.assertIn("'confirmed'", note)

    def test_synthesis_arg_parsing(self) -> None:
        parsed = parse_synthesis_args(["111=/a.json", "222=/b.json"])
        self.assertEqual(list(parsed), ["111", "222"])
        self.assertEqual(parsed["111"], Path("/a.json"))
        for bad in (["nodelimiter"], ["=path"], ["id="]):
            with self.assertRaises(GuidedError):
                parse_synthesis_args(bad)
        with self.assertRaises(GuidedError):  # duplicate id
            parse_synthesis_args(["111=/a.json", "111=/b.json"])

    def test_synthesis_for_unselected_id_is_rejected(self) -> None:
        seed_scan(self.state, [1])
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text("{}", encoding="utf-8")
        with self.assertRaises(GuidedError) as ctx:
            self.run_guided_with(
                FakeStages(),
                ScriptedConsent([]),
                ids=[fake_id(1)],
                synthesis={"9999999999999999999": doc},
                dry_run=True,
            )
        self.assertIn("not part of the selected tanda", str(ctx.exception))


# --------------------------------------------------------------------------
# Resume behavior
# --------------------------------------------------------------------------


class TestResume(GuidedTestCase):
    def test_resume_reuses_complete_stages_without_repetition(self) -> None:
        seed_scan(self.state, [1])
        seed_job(
            self.state,
            fake_id(1),
            complete=("fetch", "prepare", "vision", "audio"),
            evidence=True,
        )
        doc = Path(self.tmp.name) / "doc.json"
        doc.write_text(json.dumps(sample_synthesis_document()), encoding="utf-8")
        stages = FakeStages(verify_emits_pending=True)
        consent = ScriptedConsent([("tanda", True), ("verify", True)])
        code, _, _ = self.run_guided_with(
            stages, consent, synthesis={fake_id(1): doc}, ids=[fake_id(1)]
        )
        self.assertEqual(code, 0)
        # No fetch/prepare/gpu windows were asked and no such stage ran.
        self.assertEqual(consent.asked_kinds, ["tanda", "verify"])
        for call in stages.calls:
            self.assertTrue(
                call.startswith(("synthesize:", "verify:")),
                f"unexpected stage call on resume: {call}",
            )

    def test_audio_blocked_when_whisper_not_ready_shows_resume(self) -> None:
        seed_scan(self.state, [1])
        seed_job(
            self.state, fake_id(1), complete=("fetch", "prepare", "vision")
        )
        stages = FakeStages(whisper_healthy=False)
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("gpu", True)]),
            ids=[fake_id(1)],
        )
        self.assertEqual(code, 1)
        audio_failure = [f for f in report["failures"] if f["stage"] == "audio"][0]
        self.assertEqual(audio_failure["outcome"], "blocked")
        self.assertIn("start it explicitly", audio_failure["reason"])
        self.assertIn("zero re-inference", audio_failure["reason"])
        self.assertNotIn(f"audio:{fake_id(1)}", stages.calls)


# --------------------------------------------------------------------------
# Reporting and defaults
# --------------------------------------------------------------------------


class TestReportingAndDefaults(GuidedTestCase):
    def test_report_has_no_second_state_authority(self) -> None:
        seed_scan(self.state, [1])
        code, report, _ = self.run_guided_with(
            FakeStages(),
            ScriptedConsent([("tanda", True), ("fetch", False)]),
        )
        self.assertEqual(code, 0)
        # The report is a plain in-memory dict; nothing guided-specific
        # was persisted into the state root.
        persisted = sorted(
            path.name for path in self.state.root.rglob("*") if path.is_file()
        )
        for name in persisted:
            self.assertNotIn("guided", name)

    def test_default_stage_functions_wrap_real_stages(self) -> None:
        defaults = guided.default_stage_functions()
        for name in (
            "fetch",
            "prepare",
            "vision",
            "audio",
            "synthesize",
            "verify",
            "whisper_stopped",
            "whisper_health",
            "ollama_reachable",
            "ollama_start",
            "ollama_wait_ready",
            "ollama_stop",
        ):
            self.assertTrue(callable(getattr(defaults, name)), name)

    def test_ollama_reachable_false_when_port_closed(self) -> None:
        original = config.OLLAMA_API_BASE
        config.OLLAMA_API_BASE = "http://127.0.0.1:1"
        try:
            self.assertFalse(guided._ollama_reachable())  # noqa: SLF001
        finally:
            config.OLLAMA_API_BASE = original

    def test_fetch_failure_recorded_and_other_ids_continue(self) -> None:
        # id 1 still needs its fetch; id 2 already has fetch+prepare and
        # resumes at vision. A failed fetch for id 1 must not stop id 2.
        seed_scan(self.state, [1, 2])
        seed_job(self.state, fake_id(2), complete=("fetch", "prepare"))

        class OneFails(FakeStages):
            def do_fetch(self, url: str, state: StateRoot) -> FakeOutcome:
                self.calls.append(f"fetch:{url.rsplit('/', 1)[-1]}")
                return FakeOutcome("blocked", "denied by site; gate final")

        stages = OneFails()
        code, report, _ = self.run_guided_with(
            stages,
            ScriptedConsent([("tanda", True), ("fetch", True), ("gpu", True)]),
            ids=[fake_id(1), fake_id(2)],
        )
        self.assertEqual(code, 1)
        self.assertIn(f"fetch:{fake_id(1)}", stages.calls)
        self.assertNotIn(f"fetch:{fake_id(2)}", stages.calls)  # reused, not re-fetched
        self.assertIn(f"vision:{fake_id(2)}", stages.calls)
        self.assertNotIn(f"vision:{fake_id(1)}", stages.calls)
        fetch_failure = [f for f in report["failures"] if f["stage"] == "fetch"][0]
        self.assertEqual(fetch_failure["id"], fake_id(1))


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


class TestCliWiring(GuidedTestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        import contextlib
        import sys

        from tiktok_ingest.cli import main

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main(argv)
            except SystemExit as exc:  # argparse --help / usage errors
                code = int(exc.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_help_lists_guided_run(self) -> None:
        code, out, _ = self._run_cli(["guided-run", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--synthesis", out)
        self.assertIn("--dry-run", out)

    def test_requires_collection_or_ids(self) -> None:
        code, _, err = self._run_cli(["guided-run", "--state-root", str(self.state.root)])
        self.assertEqual(code, 2)
        self.assertIn("--collection and/or --ids", err)

    def test_unknown_id_exits_two_with_clear_message(self) -> None:
        seed_scan(self.state, [1])
        code, _, err = self._run_cli(
            [
                "guided-run",
                "--state-root",
                str(self.state.root),
                "--ids",
                "9999999999999999999",
                "--dry-run",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown video id(s)", err)

    def test_limit_over_cap_exits_two(self) -> None:
        seed_scan(self.state, [1])
        code, _, err = self._run_cli(
            [
                "guided-run",
                "--state-root",
                str(self.state.root),
                "--ids",
                fake_id(1),
                "--limit",
                "6",
                "--dry-run",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("batch cap", err)

    def test_dry_run_over_cli_prints_report_and_writes_nothing(self) -> None:
        seed_scan(self.state, [1])
        code, out, _ = self._run_cli(
            [
                "guided-run",
                "--state-root",
                str(self.state.root),
                "--collection",
                COLLECTION_URL,
                "--dry-run",
            ]
        )
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual([item["id"] for item in report["selected"]], [fake_id(1)])
        self.assertFalse(
            (self.state.root / "backlog.jsonl").exists()
        )

    def test_non_interactive_cli_stops_without_consent(self) -> None:
        import sys

        seed_scan(self.state, [1])

        class NonTTY:
            def isatty(self) -> bool:
                return False

            def readline(self) -> str:
                raise AssertionError("non-interactive stdin must not be read")

        original = sys.stdin
        sys.stdin = NonTTY()
        try:
            code, out, _ = self._run_cli(
                [
                    "guided-run",
                    "--state-root",
                    str(self.state.root),
                    "--collection",
                    COLLECTION_URL,
                ]
            )
        finally:
            sys.stdin = original
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["stage_calls"], [])
        self.assertTrue(
            any("tanda confirmation denied" in n for n in report["notes"])
        )
        # No fetch ran and no processed entry appeared.
        self.assertIsNone(Processed(self.state).get(fake_id(1)))

    def test_malformed_synthesis_arg_exits_two(self) -> None:
        seed_scan(self.state, [1])
        code, _, err = self._run_cli(
            [
                "guided-run",
                "--state-root",
                str(self.state.root),
                "--ids",
                fake_id(1),
                "--synthesis",
                "nodelimiter",
                "--dry-run",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("expected ID=PATH", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
