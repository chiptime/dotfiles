"""Swap-quiet-retry tests: V1 matrix + V2 mocked-clock FSM harness.

Phase 0 addendum (``prds/PHASE-0-SWAP-QUIET-RETRY-PRD.md``). Every
external boundary is a double: a scripted fake ``/proc/vmstat``
sequence, a fake monotonic clock (sleep advances time instantly), fake
whisper/Ollama lifecycle hooks and stage doubles that seed REAL durable
state so the product's own manifests drive eligibility and evidence.
Zero network, zero browser, zero GPU, zero containers, zero real
sleeping.

The V2 harness drives the full FSM of PRD section 5.2 (T1–T16) end to
end through the real ``_run_swap_retry_flow`` coordinator, asserting
terminal-state messages and durable records. V3 (one real GPU trial) is
EXPLICITLY DEFERRED to a separate operator authorization (OQ-05) and is
never claimed here.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import collection_run as cr
from tiktok_ingest import config
from tiktok_ingest.contracts import (
    JobManifest,
    StageOutcome,
    StageRecord,
    StageResult,
)
from tiktok_ingest.gates import (
    GATE_SWAP_DELTA,
    GateLedger,
    GateParseError,
    SwapSnapshot,
    check_swap_delta,
)
from tiktok_ingest.guided import StageFunctions
from tiktok_ingest.state import StateRoot
from tiktok_ingest.swap_cooldown import (
    SwapCooldownMeasurementError,
    SwapQuietWatcher,
)
from tiktok_ingest.tests.fixtures import (
    FIXED_NOW,
    MEDIA_SHA256,
    write_sanitized_evidence,
)
from tiktok_ingest.tests.test_collection_run import (
    API_ENV,
    COLLECTION_URL,
    CollectionStages,
    FakeApiTransport,
    FakeApiResponse,
    FakeBrowserController,
    FakeOutcome,
    KNOWN_INFO,
    DRIFTED_INFO,
    RecordingConsent,
    mark_stage,
    page_html,
    seed_scan_for_run,
)
from tiktok_ingest.tests.test_guided import fake_id, seed_job
from tiktok_ingest.whisper_lifecycle import WhisperServiceInfo

SAMPLING_SHA256 = "1" * 64
MIB = 1024 * 1024
# A measured failing delta recorded by the authorized session's console
# evidence: 556,335,104 B over the 512 MiB budget.
FAILING_DELTA_BYTES = 556_335_104
SWAP_LIMIT_BYTES = config.GATES.swap_delta_limit_mib * MIB


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


class FakeClock:
    """Monotonic double: sleep(positive) just advances the clock."""

    def __init__(self) -> None:
        self.t = 10_000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        advanced = max(float(seconds), 0.0)
        if advanced <= 0:
            # The controller only sleeps positive bounded intervals; a
            # zero advance would make the rate math degenerate.
            self.t += 0.001
            return
        self.t += advanced


class ScriptedVmstat:
    """Fake ``/proc/vmstat``: per-call cumulative byte deltas.

    Each script entry is the BYTES DELTA for one sampling interval
    (``None`` raises a parse error — the unreadable-counter branch).
    When the script runs dry, ``fill`` is reused forever.
    """

    def __init__(
        self, deltas: list[float | None] | None = None, fill: float = 0.4 * MIB
    ) -> None:
        self.deltas = list(deltas or [])
        self.fill = fill
        self.calls = 0
        self._cum = 0

    def __call__(self) -> SwapSnapshot:
        self.calls += 1
        value = self.deltas.pop(0) if self.deltas else self.fill
        if value is None:
            raise GateParseError("fixture: vmstat unreadable")
        self._cum += int(value)
        # page_size 1 byte keeps swap_io_bytes == cumulative pages.
        return SwapSnapshot(
            pswpin_pages=self._cum, pswpout_pages=0, page_size_bytes=1
        )


def _run_dir_of(state: StateRoot, video_id: str) -> Path:
    from tiktok_ingest.state import Processed

    entry = Processed(state).get(video_id)
    assert entry is not None and entry.artifacts
    return state.root / entry.artifacts[0]


def mark_vision_swap_failed(
    state: StateRoot,
    video_id: str,
    *,
    gate: str = GATE_SWAP_DELTA,
    cleanup_verified: bool = True,
    outcome: StageOutcome = StageOutcome.FAILED,
    detail: dict[str, Any] | None = None,
) -> None:
    """Seed the DURABLE failed-vision evidence the flow reads (FR-01)."""
    run_dir = _run_dir_of(state, video_id)
    manifest_path = run_dir / "meta.json"
    manifest = JobManifest.from_dict(json.loads(manifest_path.read_text()))
    if detail is None:
        detail = {
            "delta_bytes": FAILING_DELTA_BYTES,
            "limit_bytes": SWAP_LIMIT_BYTES,
            "current_bytes": FAILING_DELTA_BYTES,
            "baseline_bytes": 0,
        }
    manifest.resource_gates["vision"] = {
        "stage": "vision",
        "model": "fixture-model",
        "gates": [
            {
                "gate": gate,
                "passed": False,
                "reason": "fixture gate fired",
                "detail": detail,
            }
        ],
        "cleanup": {
            "release_verified": cleanup_verified,
            "unload_requested": True,
            "errors": [],
        },
    }
    manifest.stages["vision"] = StageRecord(
        stage="vision",
        result=StageResult(
            outcome=outcome, reason=f"{gate} gate fired: fixture"
        ),
        fingerprint={},
        input_sha256=SAMPLING_SHA256,
        attempts=1,
    )
    manifest.updated_at = FIXED_NOW
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))
    vision_dir = run_dir / "vision"
    vision_dir.mkdir(parents=True, exist_ok=True)
    (vision_dir / "records.json").write_text(
        json.dumps({"status": outcome.value, "fixture": True})
    )


class SwapRetryStages:
    """Stage/whisper/Ollama doubles for the retry coordinator."""

    def __init__(
        self,
        *,
        vision_outcomes: list[str] | None = None,
        gpu_free_sequence: list[int] | None = None,
        whisper_info: WhisperServiceInfo | None = KNOWN_INFO,
        whisper_inspect_sequence: list[WhisperServiceInfo | None]
        | None = None,
        whisper_stop_fails: bool = False,
        whisper_stop_raises_ki: bool = False,
        whisper_restore_fails: bool = False,
        whisper_health_fails: bool = False,
        whisper_inspect_raises: bool = False,
        whisper_inspect_raises_ki: bool = False,
        ollama_preexisting: bool = False,
        ollama_stop_fails: bool = False,
        vision_raises: BaseException | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.vision_outcomes = list(vision_outcomes or ["complete"])
        self.gpu_free_sequence = list(gpu_free_sequence or [])
        self.whisper_info = whisper_info
        self.whisper_inspect_sequence = list(
            whisper_inspect_sequence or []
        )
        self.whisper_stop_fails = whisper_stop_fails
        self.whisper_stop_raises_ki = whisper_stop_raises_ki
        self.whisper_restore_fails = whisper_restore_fails
        self.whisper_health_fails = whisper_health_fails
        self.whisper_inspect_raises = whisper_inspect_raises
        self.whisper_inspect_raises_ki = whisper_inspect_raises_ki
        self.ollama_preexisting = ollama_preexisting
        self.ollama_stop_fails = ollama_stop_fails
        self.vision_raises = vision_raises
        self.whisper_running = True

    def functions(self) -> StageFunctions:
        return StageFunctions(
            fetch=lambda url, state: FakeOutcome("complete"),
            prepare=lambda video_id, state: FakeOutcome("complete"),
            vision=self.do_vision,
            audio=lambda video_id, state: FakeOutcome("complete"),
            synthesize=lambda video_id, state, from_file: FakeOutcome(
                "complete"
            ),
            verify=lambda video_id, state: FakeOutcome("complete"),
            whisper_stopped=lambda: None,
            whisper_health=self.do_whisper_health,
            ollama_reachable=lambda: self.ollama_preexisting,
            ollama_start=self.do_ollama_start,
            ollama_wait_ready=lambda handle: "0.34.1",
            ollama_stop=self.do_ollama_stop,
            whisper_inspect=self.do_whisper_inspect,
            whisper_stop=self.do_whisper_stop,
            whisper_restore=self.do_whisper_restore,
            gpu_free_mib=self.do_gpu_free,
        )

    def do_vision(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"vision:{video_id}")
        if self.vision_raises is not None:
            raise self.vision_raises
        outcome = (
            self.vision_outcomes.pop(0)
            if self.vision_outcomes
            else "complete"
        )
        if outcome == "complete":
            run_dir = _run_dir_of(state, video_id)
            manifest_path = run_dir / "meta.json"
            manifest = JobManifest.from_dict(
                json.loads(manifest_path.read_text())
            )
            manifest.stages["vision"] = StageRecord(
                stage="vision",
                result=StageResult(
                    outcome=StageOutcome.COMPLETE,
                    reason="fixture retry success",
                ),
                fingerprint={"stage": "vision"},
                input_sha256=SAMPLING_SHA256,
                attempts=1,
            )
            manifest.updated_at = FIXED_NOW
            manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))
            write_sanitized_evidence(run_dir, include_audio=False)
            return FakeOutcome("complete", "fixture retry success")
        mark_vision_swap_failed(state, video_id)
        return FakeOutcome("failed", "swap_delta gate fired: fixture")

    def do_whisper_health(self) -> tuple[bool, str]:
        self.calls.append("whisper-health-check")
        if self.whisper_health_fails:
            return False, "fixture: health endpoint unreachable"
        return True, "healthy"

    def do_whisper_inspect(self) -> WhisperServiceInfo | None:
        self.calls.append("whisper-inspect")
        if self.whisper_inspect_raises_ki:
            raise KeyboardInterrupt()
        if self.whisper_inspect_sequence:
            info: WhisperServiceInfo | None = (
                self.whisper_inspect_sequence.pop(0)
            )
            if info is not None:
                self.whisper_running = info.running
            return info
        if self.whisper_inspect_raises:
            raise RuntimeError("fixture: podman inspect failed")
        if not self.whisper_running:
            return None
        return self.whisper_info

    def do_whisper_stop(self, info: WhisperServiceInfo) -> WhisperServiceInfo:
        self.calls.append("whisper-stop-op")
        if self.whisper_stop_raises_ki:
            raise KeyboardInterrupt()
        if self.whisper_stop_fails:
            raise RuntimeError("fixture: stop did not verify")
        self.whisper_running = False
        return dataclasses.replace(info, running=False, state="exited")

    def do_whisper_restore(
        self, info: WhisperServiceInfo
    ) -> WhisperServiceInfo:
        self.calls.append("whisper-restore-op")
        if self.whisper_restore_fails:
            raise RuntimeError("fixture: restore failed")
        self.whisper_running = True
        return dataclasses.replace(info, running=True, state="running")

    def do_ollama_start(self) -> object:
        self.calls.append("ollama-start")
        return object()

    def do_ollama_stop(self, handle: object) -> None:
        self.calls.append("ollama-stop")
        if self.ollama_stop_fails:
            raise RuntimeError("fixture: ollama stop failed")

    def do_gpu_free(self) -> int:
        self.calls.append("gpu-free-probe")
        if self.gpu_free_sequence:
            return self.gpu_free_sequence.pop(0)
        return config.GATES.free_vram_gate_mib + 4096


def build_plan(
    *, whisper_info: WhisperServiceInfo | None = KNOWN_INFO
) -> cr.RunPlan:
    return cr.RunPlan(
        collection_url=COLLECTION_URL,
        frozen_ids=(fake_id(1),),
        canonical_urls={fake_id(1): "https://www.tiktok.com/x"},
        stage_states={fake_id(1): {}},
        batch_cap=5,
        batch_count=1,
        plan_counts={},
        whisper_bound=(
            whisper_info.describe() if whisper_info is not None else None
        ),
        whisper_note="fixture",
        text_api_origin=None,
        text_api_model=None,
        whisper_info=whisper_info,
    )


# --------------------------------------------------------------------------
# Shared harness
# --------------------------------------------------------------------------


class SwapRetryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()
        self.messages: list[str] = []
        self.clock = FakeClock()
        self.vmstat = ScriptedVmstat()
        self.decisions: list[dict[str, Any]] = []
        self.report: dict[str, Any] = {"swap_retries": [], "hard_stop_reason": None}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def out(self, text: str) -> None:
        self.messages.append(text)

    def seed_failed_job(self, video_n: int = 1, **kw: Any) -> str:
        video_id = fake_id(video_n)
        seed_job(self.state, video_id, complete=("fetch", "prepare"))
        mark_vision_swap_failed(self.state, video_id, **kw)
        return video_id

    def run_flow(
        self,
        stages: SwapRetryStages,
        consent: RecordingConsent | None = None,
        *,
        batch_ids: list[str] | None = None,
        plan: cr.RunPlan | None = None,
        offered: set[str] | None = None,
        vmstat: ScriptedVmstat | None = None,
    ):
        consent = consent or RecordingConsent()
        cr._run_swap_retry_flow(
            state=self.state,
            stages=stages.functions(),
            narrated=stages.functions(),
            human=consent,
            human_decisions=self.decisions,
            plan=plan or build_plan(),
            batch_ids=batch_ids or [fake_id(1)],
            offered=offered if offered is not None else set(),
            out=self.out,
            report=self.report,
            monotonic=self.clock,
            sleep=self.clock.sleep,
            vmstat_fn=(vmstat or self.vmstat),
        )
        return consent

    def last_retry_record(self) -> dict[str, Any]:
        records = self.report["swap_retries"]
        assert records, "no swap_retries record was appended"
        return records[-1]

    def manifest_of(self, video_id: str) -> JobManifest:
        run_dir = _run_dir_of(self.state, video_id)
        return JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text())
        )


# --------------------------------------------------------------------------
# V1: pure watcher bounds (FR-07/FR-08/FR-09/FR-12)
# --------------------------------------------------------------------------


class TestSwapQuietWatcher(SwapRetryTestCase):
    def _watcher(self, vmstat: ScriptedVmstat | None = None) -> SwapQuietWatcher:
        return SwapQuietWatcher(
            vmstat_fn=vmstat or self.vmstat, monotonic=self.clock
        )

    def test_admission_needs_three_stable_intervals_and_floor(self) -> None:
        watcher = self._watcher()
        watcher.start()
        for expected_elapsed in range(5, 65, 5):
            self.clock.sleep(5.0)
            sample = watcher.sample()
            self.assertEqual(sample.elapsed_seconds, expected_elapsed)
            if expected_elapsed < 60:
                # Stability accumulates from the first interval, but the
                # 60 s floor rules admission (windows overlap, FR-09).
                self.assertFalse(watcher.admitted)
        self.assertGreaterEqual(watcher.stable_count, 3)
        self.assertTrue(watcher.admitted)
        self.assertFalse(watcher.expired)

    def test_above_threshold_resets_count_never_cap(self) -> None:
        vmstat = ScriptedVmstat(
            deltas=[0.4 * MIB] * 13 + [20 * MIB] + [0.4 * MIB] * 30
        )
        watcher = self._watcher(vmstat)
        watcher.start()
        for _ in range(12):
            self.clock.sleep(5.0)
            watcher.sample()
        self.assertTrue(watcher.admitted)
        self.clock.sleep(5.0)
        noisy = watcher.sample()
        self.assertEqual(watcher.stable_count, 0)
        self.assertFalse(watcher.admitted)
        # The cap was NOT restarted: elapsed keeps counting from t0.
        self.assertEqual(noisy.elapsed_seconds, 65.0)
        for _ in range(3):
            self.clock.sleep(5.0)
            watcher.sample()
        self.assertTrue(watcher.admitted)
        self.assertLess(watcher.elapsed_seconds, 180.0)

    def test_expiry_at_cap_without_stability(self) -> None:
        vmstat = ScriptedVmstat(fill=20 * MIB)
        watcher = self._watcher(vmstat)
        watcher.start()
        while not watcher.expired:
            self.clock.sleep(5.0)
            watcher.sample()
        self.assertEqual(watcher.elapsed_seconds, 180.0)
        self.assertFalse(watcher.admitted)

    def test_unreadable_counters_fail_closed_distinctly(self) -> None:
        vmstat = ScriptedVmstat(deltas=[0.4 * MIB, None])
        watcher = self._watcher(vmstat)
        watcher.start()
        self.clock.sleep(5.0)
        with self.assertRaises(SwapCooldownMeasurementError):
            watcher.sample()

    def test_decreasing_cumulative_counters_fail_closed(self) -> None:
        vmstat = ScriptedVmstat(deltas=[100 * MIB, 50 * MIB, -5])
        watcher = self._watcher(vmstat)
        watcher.start()
        self.clock.sleep(5.0)
        watcher.sample()
        self.clock.sleep(5.0)
        with self.assertRaises(SwapCooldownMeasurementError) as ctx:
            watcher.sample()
        self.assertIn("decreased", str(ctx.exception))

    def test_reset_stability_keeps_single_t0(self) -> None:
        watcher = self._watcher()
        watcher.start()
        for _ in range(12):
            self.clock.sleep(5.0)
            watcher.sample()
        elapsed_before = watcher.elapsed_seconds
        watcher.reset_stability()
        self.assertEqual(watcher.stable_count, 0)
        self.assertEqual(watcher.elapsed_seconds, elapsed_before)
        self.assertEqual(watcher.remaining_seconds, 180.0 - elapsed_before)


# --------------------------------------------------------------------------
# V1: eligibility matrix from DURABLE typed evidence (FR-01/FR-16)
# --------------------------------------------------------------------------


class TestEligibility(SwapRetryTestCase):
    def test_failed_swap_delta_with_verified_cleanup_is_eligible(self) -> None:
        video_id = self.seed_failed_job()
        elig = cr._swap_retry_eligibility(self.state, video_id)
        self.assertTrue(elig.eligible)
        self.assertAlmostEqual(elig.overshoot_mib, 18.56, places=2)
        self.assertEqual(
            elig.scope["sampling_manifest_sha256"], SAMPLING_SHA256
        )
        self.assertEqual(elig.scope["media_sha256"], MEDIA_SHA256)

    def test_offload_gate_never_qualifies(self) -> None:
        video_id = self.seed_failed_job(gate="offload")
        elig = cr._swap_retry_eligibility(self.state, video_id)
        self.assertFalse(elig.eligible)
        self.assertIn("no failed swap_delta gate", elig.reason)

    def test_unverified_cleanup_never_qualifies(self) -> None:
        video_id = self.seed_failed_job(cleanup_verified=False)
        elig = cr._swap_retry_eligibility(self.state, video_id)
        self.assertFalse(elig.eligible)
        self.assertIn("cleanup", elig.reason)

    def test_blocked_outcome_never_qualifies(self) -> None:
        video_id = self.seed_failed_job(outcome=StageOutcome.BLOCKED)
        elig = cr._swap_retry_eligibility(self.state, video_id)
        self.assertFalse(elig.eligible)
        self.assertIn("not a failed run", elig.reason)

    def test_deadline_exhaustion_never_qualifies(self) -> None:
        video_id = self.seed_failed_job(
            outcome=StageOutcome.BUDGET_EXCEEDED
        )
        elig = cr._swap_retry_eligibility(self.state, video_id)
        self.assertFalse(elig.eligible)

    def test_missing_detail_yields_no_overshoot_but_still_eligible(self) -> None:
        video_id = self.seed_failed_job(detail={})
        elig = cr._swap_retry_eligibility(self.state, video_id)
        self.assertTrue(elig.eligible)
        self.assertIsNone(elig.overshoot_mib)


# --------------------------------------------------------------------------
# V1: additive ledger detail + manifest provenance (FR-15/FR-16/FR-17)
# --------------------------------------------------------------------------


class TestDurableEvidence(SwapRetryTestCase):
    def test_gate_ledger_persists_raw_detail_additively(self) -> None:
        from tiktok_ingest.gates import GateViolation

        ledger = GateLedger()
        with self.assertRaises(GateViolation):
            ledger.record(
                check_swap_delta(FAILING_DELTA_BYTES, 0, SWAP_LIMIT_BYTES)
            )
        entries = ledger.to_jsonable()
        self.assertEqual(entries[0]["gate"], GATE_SWAP_DELTA)
        self.assertFalse(entries[0]["passed"])
        self.assertEqual(entries[0]["detail"]["delta_bytes"], FAILING_DELTA_BYTES)

    def test_manifest_swap_retry_round_trips(self) -> None:
        video_id = self.seed_failed_job()
        manifest = self.manifest_of(video_id)
        manifest.swap_retry = {"original": {"archive_dir": "vision-attempt-1"}}
        path = _run_dir_of(self.state, video_id) / "meta.json"
        path.write_text(json.dumps(manifest.to_dict(), indent=2))
        reloaded = self.manifest_of(video_id)
        self.assertEqual(
            reloaded.swap_retry, {"original": {"archive_dir": "vision-attempt-1"}}
        )

    def test_old_manifest_without_swap_retry_stays_readable(self) -> None:
        video_id = self.seed_failed_job()
        manifest = self.manifest_of(video_id)
        document = manifest.to_dict()
        document.pop("swap_retry", None)
        reloaded = JobManifest.from_dict(document)
        self.assertIsNone(reloaded.swap_retry)


# --------------------------------------------------------------------------
# V1: the dedicated consent is NEVER auto-covered (FR-02/FR-03/FR-04/FR-24)
# --------------------------------------------------------------------------


class TestConsentAuthority(SwapRetryTestCase):
    def test_run_scoped_authorization_never_covers_vision_swap_retry(self) -> None:
        video_id = self.seed_failed_job()
        elig = cr._swap_retry_eligibility(self.state, video_id)
        plan = build_plan()
        authorization = cr.RunScopedAuthorization(
            human=RecordingConsent(), plan=plan
        )
        window = cr._build_swap_retry_window(video_id, elig, plan)
        granted = authorization.ask(window)
        self.assertTrue(granted, "granted by the recording double's human")
        decisions = authorization.authorization_log
        self.assertEqual(decisions[-1]["decision"], "human_granted")
        self.assertNotEqual(decisions[-1]["decision"], "covered_by_run_plan")
        # The denial variant also stays a human decision, never covered.
        denying = cr.RunScopedAuthorization(
            human=RecordingConsent(deny={"vision-swap-retry"}), plan=plan
        )
        self.assertFalse(denying.ask(window))
        self.assertEqual(
            denying.authorization_log[-1]["decision"], "human_denied"
        )

    def test_window_names_id_gate_overshoot_budget_and_lifecycle(self) -> None:
        video_id = self.seed_failed_job()
        elig = cr._swap_retry_eligibility(self.state, video_id)
        window = cr._build_swap_retry_window(video_id, elig, build_plan())
        text = " ".join(
            [window.title, *window.operations, *window.limits, *window.effects]
        )
        self.assertIn(video_id, window.ids)
        self.assertIn("swap_delta", text)
        self.assertIn("+18.56 MiB", text)
        self.assertIn("180s", text)
        self.assertIn("60s", text)
        self.assertIn("whisper", text.lower())
        self.assertIn("ONE extra vision attempt", " ".join(window.limits))
        self.assertIn("media", " ".join(window.limits))


# --------------------------------------------------------------------------
# V2 FSM harness: T1–T16 end to end through the REAL coordinator
# --------------------------------------------------------------------------


class TestV2FsmHarness(SwapRetryTestCase):
    """Full-FSM drives with mocked clock/services (PRD section 10 V2)."""

    def messages_text(self) -> str:
        return "\n".join(self.messages)

    # T1 -> T4 -> T5(verified) -> T7/T8 -> T12 -> T14
    def test_t14_retry_complete_with_archive_and_reuse(self) -> None:
        video_id = self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])
        original_records = (
            _run_dir_of(self.state, video_id) / "vision" / "records.json"
        ).read_text()
        consent = self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        # Exactly ONE dedicated consent, granted, recorded as human.
        self.assertEqual(
            [w.kind for w in consent.windows], ["vision-swap-retry"]
        )
        self.assertEqual(
            self.decisions[-1]["basis"],
            "human decision: ONE bounded vision swap-retry (new scope, "
            "never covered by the run-plan grant)",
        )
        # Truthful narration: pause, 15s cadence, admission, stop, restore.
        text = self.messages_text()
        self.assertIn("vision swap-retry pause:", text)
        self.assertIn("[15s] swap", text)
        self.assertIn("at 60s floor: admitting retry", text)
        self.assertIn(
            "whisper stop (retry authority): verified identical config", text
        )
        self.assertIn("whisper restored healthy after the retry window", text)
        # The retry ran vision exactly once (vision-only; others reused).
        self.assertEqual(stages.calls.count(f"vision:{video_id}"), 1)
        self.assertNotIn(f"audio:{video_id}", stages.calls)
        # Isolated Ollama lifecycle around the single attempt.
        self.assertEqual(stages.calls.count("ollama-start"), 1)
        self.assertEqual(stages.calls.count("ollama-stop"), 1)
        # Durable evidence: archive + original preserved + attempt recorded.
        run_dir = _run_dir_of(self.state, video_id)
        archive = run_dir / "vision-attempt-1"
        self.assertTrue(archive.is_dir())
        self.assertEqual(
            (archive / "records.json").read_text(), original_records
        )
        manifest = self.manifest_of(video_id)
        assert manifest.swap_retry is not None
        self.assertEqual(
            manifest.swap_retry["original"]["stage_record"]["result"][
                "outcome"
            ],
            "failed",
        )
        self.assertEqual(
            manifest.swap_retry["attempt"]["outcome"], "complete"
        )
        self.assertTrue(manifest.swap_retry["attempt"]["cooldown_samples"])
        # The live stage record now reflects the successful retry.
        self.assertEqual(
            manifest.stages["vision"].result.outcome, StageOutcome.COMPLETE
        )

    # T2: other failure kinds end terminal per existing rules (no offer)
    def test_t2_non_swap_failures_get_no_offer(self) -> None:
        video_id = self.seed_failed_job(gate="offload")
        consent = self.run_flow(SwapRetryStages())
        self.assertEqual(consent.windows, [])
        self.assertEqual(self.report["swap_retries"], [])

    # T3: consent denial fails closed, cooldown never starts
    def test_t3_denial_leaves_video_pending_without_sampling(self) -> None:
        video_id = self.seed_failed_job()
        vmstat = ScriptedVmstat()
        consent = RecordingConsent(deny={"vision-swap-retry"})
        self.run_flow(SwapRetryStages(), consent, vmstat=vmstat)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_declined_pending")
        self.assertEqual(record["samples"], [])
        self.assertEqual(vmstat.calls, 0, "cooldown never samples on denial")
        self.assertIn(
            "swap-retry declined: video stays pending and resumable",
            self.messages_text(),
        )

    # T5/T6: restoration verification failure blocks globally BEFORE consent
    def test_t6_config_mismatch_before_consent_is_hard_stop(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            whisper_inspect_sequence=[DRIFTED_INFO]
        )
        consent = self.run_flow(stages)
        self.assertEqual(consent.windows, [], "no consent after hard stop")
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIsNotNone(self.report["hard_stop_reason"])
        self.assertIn(
            "does not match the identity", self.report["hard_stop_reason"]
        )

    def test_t6_unhealthy_restore_before_consent_is_hard_stop(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(whisper_health_fails=True)
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn("NOT healthy", record["message"])

    # T9: above-threshold interval resets the count, cap keeps running
    def test_t9_noisy_then_quiet_recovers_within_same_cap(self) -> None:
        self.seed_failed_job()
        vmstat = ScriptedVmstat(
            deltas=[20 * MIB] * 9 + [0.4 * MIB] * 40
        )
        stages = SwapRetryStages(vision_outcomes=["complete"])
        self.run_flow(stages, vmstat=vmstat)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        text = self.messages_text()
        self.assertIn("above 1.00 MiB/s", text)
        self.assertIn("NOT recovered", text)
        # Admission happened strictly after 3 consecutive quiet intervals
        # AND the 60 s floor (deltas[0] is the t0 anchor sample).
        admitted_sample = [
            s for s in record["samples"] if s["stable_intervals"] >= 3
        ][-1]
        self.assertGreaterEqual(admitted_sample["elapsed_seconds"], 60.0)

    # T10: cap exhaustion -> retry NOT executed, video pending
    def test_t10_expiry_message_verbatim_and_whisper_healthy(self) -> None:
        self.seed_failed_job()
        vmstat = ScriptedVmstat(fill=20 * MIB)
        stages = SwapRetryStages(vision_outcomes=["complete"])
        self.run_flow(stages, vmstat=vmstat)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "cooldown_expired")
        self.assertEqual(
            record["message"],
            "cap reached without 3 stable intervals: retry not executed, "
            "video pending (resumable by re-invoking). Whisper remains "
            "healthy.",
        )
        self.assertEqual(
            stages.calls.count("vision:" + fake_id(1)),
            0,
            "the retry is NOT executed on expiry",
        )
        self.assertNotIn("whisper-stop-op", stages.calls)
        self.assertLessEqual(record["samples"][-1]["elapsed_seconds"], 180.0)

    # T11: unreadable counters fail closed, distinct from expiry
    def test_t11_measurement_unavailable_is_distinct_from_expiry(self) -> None:
        self.seed_failed_job()
        vmstat = ScriptedVmstat(deltas=[0.4 * MIB] * 3 + [None])
        stages = SwapRetryStages(vision_outcomes=["complete"])
        self.run_flow(stages, vmstat=vmstat)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "measurement_unavailable")
        self.assertIn("swap measurement unavailable", record["message"])
        self.assertIn("retry not executed", record["message"])
        self.assertEqual(stages.calls.count("vision:" + fake_id(1)), 0)

    # T13: drift resets the count and resumes sampling in the SAME cap
    def test_t13_vram_drift_resamples_within_same_cap_then_completes(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            gpu_free_sequence=[15000, config.GATES.free_vram_gate_mib + 1],
        )
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        # One recorded drift reset with its reason.
        self.assertEqual(len(record["stable_resets"]), 1)
        self.assertIn(
            "only 15000 MiB free VRAM", record["stable_resets"][0]["reason"]
        )
        # Post-drift samples are explicitly accounted as resample phase.
        phases = {sample["phase"] for sample in record["samples"]}
        self.assertIn("resample", phases)
        # Whisper was stopped twice (drift cycle + final) and restored
        # after the drift BEFORE the second stop.
        calls = stages.calls
        first_stop = calls.index("whisper-stop-op")
        drift_restore = calls.index("whisper-restore-op", first_stop)
        second_stop = calls.index("whisper-stop-op", drift_restore)
        self.assertLess(first_stop, drift_restore)
        self.assertLess(drift_restore, second_stop)
        # No fresh 180s: the resample continued past 60s from the SAME t0.
        resample_elapsed = [
            s["elapsed_seconds"]
            for s in record["samples"]
            if s["phase"] == "resample"
        ]
        self.assertGreater(min(resample_elapsed), 60.0)
        self.assertLess(max(resample_elapsed), 180.0)

    # T15: second failure ends the matter; no third attempt, no re-prompt
    def test_t15_second_failure_exhausts_retry(self) -> None:
        video_id = self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["failed"])
        consent = self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_exhausted")
        self.assertEqual(
            record["message"],
            "retry exhausted: no third attempt this invocation; video "
            "stays pending (resumable by re-invoking)",
        )
        self.assertEqual(stages.calls.count(f"vision:{video_id}"), 1)
        self.assertEqual(
            [w.kind for w in consent.windows],
            ["vision-swap-retry"],
            "no re-prompt after the failed second attempt",
        )
        # The retry's own failure is durable append-only evidence.
        manifest = self.manifest_of(video_id)
        assert manifest.swap_retry is not None
        self.assertEqual(manifest.swap_retry["attempt"]["outcome"], "failed")

    # T16: interrupt during the read-only cooldown keeps UNKNOWN semantics
    def test_t16_interrupt_during_cooldown_reports_unknown(self) -> None:
        self.seed_failed_job()

        class InterruptingVmstat(ScriptedVmstat):
            def __call__(self) -> SwapSnapshot:
                if self.calls >= 3:
                    raise KeyboardInterrupt()
                return super().__call__()

        stages = SwapRetryStages(vision_outcomes=["complete"])
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(stages, vmstat=InterruptingVmstat())
        record = self.last_retry_record()
        self.assertEqual(record["state"], "interrupted_unknown")
        self.assertIn("UNKNOWN", record["message"])
        # Nothing was mutated during the read-only cooldown.
        self.assertNotIn("whisper-stop-op", stages.calls)

    # T16: interrupt inside the retry window restores whisper first
    def test_t16_interrupt_during_attempt_restores_whisper(self) -> None:
        video_id = self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"], vision_raises=KeyboardInterrupt()
        )
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "interrupted_unknown")
        self.assertIn("whisper-restore-op", stages.calls)
        self.assertIn("ollama-stop", stages.calls)

    # T16 (corrected scope): interrupt during the PRE-CONSENT read-only
    # verification leaves a durable interrupted_unknown record.
    def test_t16_interrupt_during_preconsent_verification(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            whisper_inspect_raises_ki=True,
        )
        consent = RecordingConsent()
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(stages, consent)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "interrupted_unknown")
        self.assertIn("before any mutation", record["message"])
        self.assertEqual(
            consent.windows, [], "no consent was asked yet"
        )
        self.assertEqual(self.vmstat.calls, 0, "cooldown never started")
        self.assertNotIn("whisper-stop-op", stages.calls)

    # T16 (corrected scope): interrupt during the consent ask itself.
    def test_t16_interrupt_during_consent_ask(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])

        class KIOnRetryAsk(RecordingConsent):
            def ask(self, window: Any) -> bool:
                if window.kind == "vision-swap-retry":
                    raise KeyboardInterrupt()
                return super().ask(window)

        consent = KIOnRetryAsk()
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(stages, consent)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "interrupted_unknown")
        self.assertIn("before any mutation", record["message"])
        self.assertEqual(self.vmstat.calls, 0, "cooldown never started")
        self.assertNotIn("whisper-stop-op", stages.calls)

    # T16: an interrupt inside the readmission stop leaves the stop
    # result UNKNOWN — NEVER an unsafe restore (existing semantics).
    def test_t16_interrupt_during_readmission_stop_never_restores(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"], whisper_stop_raises_ki=True
        )
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "interrupted_unknown")
        self.assertIn("UNKNOWN", record["message"])
        self.assertNotIn(
            "whisper-restore-op",
            stages.calls,
            "the stop result is UNKNOWN: restoring would be an unsafe "
            "rollback guess",
        )

    # Identity-level config mismatch at re-admission: global fail-closed
    # with the actual state (never resample on an assumed healthy known
    # service; the consent never authorized this foreign configuration).
    def test_readmission_config_mismatch_is_global_failclosed(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            whisper_inspect_sequence=[KNOWN_INFO, DRIFTED_INFO],
        )
        consent = self.run_flow(stages)
        self.assertEqual(
            [w.kind for w in consent.windows], ["vision-swap-retry"]
        )
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn(
            "differs from the plan binding", record["message"]
        )
        self.assertIn("now ", record["message"])
        self.assertIn(DRIFTED_INFO.describe(), record["message"])
        self.assertNotIn(
            "whisper-stop-op", stages.calls, "no mutation on mismatch"
        )
        self.assertIsNotNone(self.report["hard_stop_reason"])

    # Volatile not-running drift: restore FIRST (granted authority),
    # then resample — never hold the shared service down while waiting.
    def test_readmission_not_running_restores_then_resamples(self) -> None:
        self.seed_failed_job()
        not_running = dataclasses.replace(
            KNOWN_INFO, running=False, state="exited"
        )
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            whisper_inspect_sequence=[KNOWN_INFO, not_running],
        )
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        self.assertEqual(len(record["stable_resets"]), 1)
        self.assertIn(
            "not running at re-admission",
            record["stable_resets"][0]["reason"],
        )
        # The restore happened BEFORE any (re)stop of the service.
        self.assertIn("whisper-restore-op", stages.calls)
        self.assertIn("whisper-stop-op", stages.calls)
        self.assertLess(
            stages.calls.index("whisper-restore-op"),
            stages.calls.index("whisper-stop-op"),
        )
        phases = {sample["phase"] for sample in record["samples"]}
        self.assertIn("resample", phases)
        self.assertIn("post_stop", phases)

    # The known service disappearing entirely: cannot restore — global
    # fail-closed with the actual state.
    def test_readmission_inspect_none_is_global_failclosed(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            whisper_inspect_sequence=[KNOWN_INFO, None],
        )
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn("cannot be found at re-admission", record["message"])
        self.assertNotIn("whisper-stop-op", stages.calls)

    # A raising free-VRAM probe after a verified stop: restore under the
    # granted authority (state known), then honest fail-closed.
    def test_gpu_probe_raising_restores_whisper_then_blocks(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])

        def raising_probe() -> int:
            raise RuntimeError("fixture: nvidia-smi failed")

        stages.do_gpu_free = raising_probe
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn("free-VRAM probe at re-admission failed", record["message"])
        self.assertIn(
            "whisper-restore-op",
            stages.calls,
            "known stopped state must be restored before failing closed",
        )
        self.assertNotIn(f"vision:{fake_id(1)}", stages.calls)

    # The 180 s TOTAL cap gates the LAUNCH after slow re-admission
    # transitions: past the ORIGINAL deadline the attempt never starts.
    def test_slow_transitions_past_cap_do_not_launch_retry(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(vision_outcomes=["complete"])
        original_stop = stages.do_whisper_stop

        def slow_stop(info: WhisperServiceInfo) -> WhisperServiceInfo:
            self.clock.sleep(130.0)  # bounded stop grace consuming time
            return original_stop(info)

        stages.do_whisper_stop = slow_stop
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "cooldown_expired")
        self.assertIn(
            "cap exhausted during re-admission transitions",
            record["message"],
        )
        self.assertIn("Whisper restored healthy", record["message"])
        self.assertEqual(
            stages.calls.count(f"vision:{fake_id(1)}"),
            0,
            "no retry launch past the original 180 s deadline",
        )
        self.assertIn("whisper-restore-op", stages.calls)
        # The original t0 was never reset: the post-stop sample's
        # elapsed already exceeds the cap.
        post_stop = [
            s for s in record["samples"] if s["phase"] == "post_stop"
        ]
        self.assertTrue(post_stop)
        self.assertGreaterEqual(post_stop[0]["elapsed_seconds"], 190.0)

    # Post-stop fresh swap measurement (FR-11/FR-12): an above-threshold
    # rate right after the service mutation IS drift — restore, reset the
    # count, resample inside the SAME cap, then complete.
    def test_post_stop_swap_drift_resets_and_resamples(self) -> None:
        self.seed_failed_job()
        vmstat = ScriptedVmstat(
            deltas=[0.4 * MIB] * 13 + [20 * MIB] + [0.4 * MIB] * 60
        )
        stages = SwapRetryStages(vision_outcomes=["complete"])
        original_stop = stages.do_whisper_stop

        def slow_stop(info: WhisperServiceInfo) -> WhisperServiceInfo:
            self.clock.sleep(5.0)
            return original_stop(info)

        stages.do_whisper_stop = slow_stop
        self.run_flow(stages, vmstat=vmstat)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_complete")
        # The raw post-mutation sample participated in the accounting.
        post_stop = [
            s for s in record["samples"] if s["phase"] == "post_stop"
        ]
        self.assertTrue(post_stop)
        self.assertGreater(
            post_stop[0]["rate_bytes_per_s"] / MIB, 1.0
        )
        self.assertEqual(len(record["stable_resets"]), 1)
        self.assertIn(
            "after the whisper stop", record["stable_resets"][0]["reason"]
        )
        # Stop -> restore (drift) -> stop (final) order, and no fresh
        # 180 s: the resample continued past 60 s from the SAME t0.
        calls = stages.calls
        first_stop = calls.index("whisper-stop-op")
        restore = calls.index("whisper-restore-op", first_stop)
        second_stop = calls.index("whisper-stop-op", restore)
        self.assertLess(first_stop, restore)
        self.assertLess(restore, second_stop)
        resample_elapsed = [
            s["elapsed_seconds"]
            for s in record["samples"]
            if s["phase"] == "resample"
        ]
        self.assertGreater(min(resample_elapsed), 60.0)
        self.assertLess(max(resample_elapsed), 180.0)

    # FR-02: exactly ONE ask per video per invocation (denial included)
    def test_one_ask_per_video_per_invocation(self) -> None:
        video1 = self.seed_failed_job(1)
        video2 = self.seed_failed_job(2)
        offered: set[str] = set()
        consent = RecordingConsent(deny={"vision-swap-retry"})
        self.run_flow(
            SwapRetryStages(),
            consent,
            batch_ids=[video1, video2],
            offered=offered,
        )
        self.assertEqual(
            [w.kind for w in consent.windows],
            ["vision-swap-retry", "vision-swap-retry"],
            "one dedicated ask per eligible video (no pooling)",
        )
        # A second pass over the same ids never re-asks.
        self.run_flow(
            SwapRetryStages(),
            consent,
            batch_ids=[video1, video2],
            offered=offered,
        )
        self.assertEqual(len(consent.windows), 2)
        self.assertEqual(offered, {video1, video2})

    # FR-20: a preexisting isolated server is never adopted (honest pending)
    def test_preexisting_isolated_server_blocks_retry_without_adoption(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"], ollama_preexisting=True
        )
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_not_executed_pending")
        self.assertIn("never adopts a preexisting server", record["message"])
        self.assertNotIn("vision:" + fake_id(1), stages.calls)
        self.assertIn("whisper-restore-op", stages.calls)
        self.assertIsNone(self.report["hard_stop_reason"])

    # FR-24: scope drift after consent is a NEW authorization question
    def test_scope_drift_after_consent_needs_new_authorization(self) -> None:
        video_id = self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"],
            gpu_free_sequence=[15000],
        )
        plan = build_plan()
        # The first gpu probe (readmission #1) reports low VRAM AND
        # rewrites the manifest's media hash: the drift path restores
        # whisper, and readmission #2's scope check must then refuse the
        # OLD grant as changed scope.
        original_gpu = stages.do_gpu_free
        probed = False

        def drift_scope_then_probe() -> int:
            nonlocal probed
            if not probed:
                probed = True
                run_dir = _run_dir_of(self.state, video_id)
                path = run_dir / "meta.json"
                manifest = JobManifest.from_dict(json.loads(path.read_text()))
                manifest.media_sha256 = "9" * 64
                path.write_text(json.dumps(manifest.to_dict(), indent=2))
            return original_gpu()

        stages.do_gpu_free = drift_scope_then_probe
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "retry_not_executed_pending")
        self.assertIn(
            "NEW authorization question", record["message"]
        )
        self.assertNotIn("vision:" + video_id, stages.calls)
        self.assertIn(
            "whisper-restore-op",
            stages.calls,
            "the drift path restored whisper before resampling",
        )

    # FR-23: stop failure under the retry authority is the honest hard stop
    def test_readmission_stop_failure_is_service_hard_stop(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"], whisper_stop_fails=True
        )
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn(
            "stop under the retry authority failed",
            self.report["hard_stop_reason"],
        )
        self.assertNotIn("vision:" + fake_id(1), stages.calls)

    # FR-25: restore failure after the retry window blocks everything
    def test_restore_failure_after_attempt_is_global_hard_stop(self) -> None:
        self.seed_failed_job()
        stages = SwapRetryStages(
            vision_outcomes=["complete"], whisper_restore_fails=True
        )
        self.run_flow(stages)
        record = self.last_retry_record()
        self.assertEqual(record["state"], "blocked_global")
        self.assertIn(
            "ALL further progress is blocked",
            self.report["hard_stop_reason"],
        )
        # The attempt DID run and its evidence is durable.
        manifest = self.manifest_of(fake_id(1))
        assert manifest.swap_retry is not None
        self.assertEqual(manifest.swap_retry["attempt"]["outcome"], "complete")


# --------------------------------------------------------------------------
# End-to-end journey through run_collection (driver reconciliation)
# --------------------------------------------------------------------------


class RetryCollectionStages(CollectionStages):
    """The real journey stage double with a scripted vision outcome.

    The first "failed" seeds the DURABLE swap_delta evidence exactly the
    way the real stage records it; later entries complete normally.
    """

    def __init__(
        self,
        *,
        vision_outcomes: list[str] | None = None,
        gpu_free_sequence: list[int] | None = None,
        **kw: Any,
    ) -> None:
        self.vision_outcomes = list(vision_outcomes or ["complete"])
        self.gpu_free_sequence = list(gpu_free_sequence or [])
        super().__init__(**kw)

    def do_vision(self, video_id: str, state: StateRoot) -> FakeOutcome:
        self.calls.append(f"vision:{video_id}")
        if self.vision_raises is not None:
            raise self.vision_raises
        outcome = (
            self.vision_outcomes.pop(0)
            if self.vision_outcomes
            else "complete"
        )
        if outcome == "complete":
            mark_stage(state, video_id, "vision")
            write_sanitized_evidence(
                _run_dir_of(state, video_id), include_audio=False
            )
            return FakeOutcome("complete")
        mark_vision_swap_failed(state, video_id)
        return FakeOutcome("failed", "swap_delta gate fired: fixture")

    def do_gpu_free(self) -> int:
        self.calls.append("gpu-free-probe")
        if self.gpu_free_sequence:
            return self.gpu_free_sequence.pop(0)
        return config.GATES.free_vram_gate_mib + 4096

    def functions(self) -> StageFunctions:
        return dataclasses.replace(
            super().functions(), gpu_free_mib=self.do_gpu_free
        )


class KIOnRetryConsent(RecordingConsent):
    """Grants every window EXCEPT the retry ask, which it interrupts."""

    def ask(self, window: Any) -> bool:
        if window.kind == "vision-swap-retry":
            raise KeyboardInterrupt()
        return super().ask(window)


class TestRunCollectionJourney(SwapRetryTestCase):
    def run_collection(
        self,
        stages: SwapRetryStages,
        consent: RecordingConsent,
        *,
        video_ns: list[int],
        limit: int | None = None,
        vmstat: ScriptedVmstat | None = None,
    ) -> tuple[int, dict[str, Any]]:
        from tiktok_ingest.tests.fixtures import sample_synthesis_document

        seed_scan_for_run(self.state, video_ns)
        controller = FakeBrowserController(page_html(len(video_ns)))
        transport = FakeApiTransport(
            [FakeApiResponse(sample_synthesis_document()) for _ in range(50)]
        )
        code, report = cr.run_collection(
            collection_url=COLLECTION_URL,
            state=self.state,
            consent=consent,
            out=self.out,
            limit=limit,
            browser_controller=controller,
            browser_profile_base=Path(self.tmp.name) / "profiles",
            api_transport=transport,
            env=dict(API_ENV),
            podman_runner=lambda argv, timeout=None: (0, "", ""),
            stages=stages.functions(),
            swap_retry_monotonic=self.clock,
            swap_retry_sleep=self.clock.sleep,
            swap_retry_vmstat_fn=vmstat or self.vmstat,
        )
        return code, report

    def test_journey_retry_success_keeps_other_window_ids_pending(self) -> None:
        # fake_id(0) fails vision under swap_delta in the batch; the
        # other same-window ids stay pending per D-08/OQ-01 even though
        # the retry of fake_id(0) succeeds (browser/scan union freezes
        # fake_id(0..2) here).
        stages = RetryCollectionStages(
            whisper_running=True, vision_outcomes=["failed", "complete"]
        )
        consent = RecordingConsent()
        code, report = self.run_collection(stages, consent, video_ns=[1, 2])
        self.assertEqual(code, 1, "honest incompleteness: ids remain pending")
        self.assertEqual(len(report["batches"]), 1)
        retries = report["swap_retries"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["id"], fake_id(0))
        self.assertEqual(retries[0]["state"], "retry_complete")
        # Exactly one NEW human window kind appears, between plan and
        # grouped verify; stage windows stay covered by the plan.
        self.assertIn("vision-swap-retry", consent.kinds)
        self.assertEqual(
            consent.kinds[0], "browser", "browser grant first"
        )
        # Same-window ids' vision never ran (window closed by the gate).
        self.assertNotIn(f"vision:{fake_id(1)}", stages.calls)
        self.assertNotIn(f"vision:{fake_id(2)}", stages.calls)
        # The failed id's vision ran exactly twice (original + ONE retry).
        self.assertEqual(stages.calls.count(f"vision:{fake_id(0)}"), 2)
        # The final report stays parseable JSON with additive retry data.
        json.loads(json.dumps(report))
        # No relabel: a gate failure is not a service hard stop by itself
        # and retry success does not clear it.
        self.assertIsNone(report["hard_stop_reason"])

    def test_journey_subsequent_frozen_batches_reconcile_unchanged(self) -> None:
        # OQ-03 reconciliation: after fake_id(0)'s failed batch + retry,
        # the driver continues with the remaining frozen batches EXACTLY
        # as before — the retry adds no batch, no window, no extra ask
        # for ids that did not fail (frozen: fake_id(0..2), limit 1).
        stages = RetryCollectionStages(
            whisper_running=True, vision_outcomes=["failed", "complete"]
        )
        consent = RecordingConsent()
        code, report = self.run_collection(
            stages, consent, video_ns=[1, 2], limit=1
        )
        self.assertEqual(len(report["batches"]), 3)
        self.assertEqual(
            [b["ids"] for b in report["batches"]],
            [[fake_id(0)], [fake_id(1)], [fake_id(2)]],
        )
        self.assertEqual(len(report["swap_retries"]), 1)
        self.assertEqual(
            consent.kinds.count("vision-swap-retry"), 1,
            "no retry ask for ids whose vision did not fail",
        )
        # Batches 2 and 3 ran their own normal windows (driver unchanged).
        self.assertIn(f"vision:{fake_id(1)}", stages.calls)
        self.assertIn(f"vision:{fake_id(2)}", stages.calls)
        self.assertEqual(code, 1)

    def test_journey_hard_stop_skips_grouped_verification(self) -> None:
        stages = RetryCollectionStages(
            whisper_running=True,
            vision_outcomes=["failed", "complete"],
            whisper_infos=[KNOWN_INFO, KNOWN_INFO, DRIFTED_INFO],
        )
        consent = RecordingConsent()
        code, report = self.run_collection(stages, consent, video_ns=[1, 2])
        self.assertEqual(code, 1)
        self.assertIsNotNone(report["hard_stop_reason"])
        self.assertIn(
            "does not match the identity", report["hard_stop_reason"]
        )
        self.assertIsNone(
            report["verification"],
            "grouped verification must not proceed after a restoration "
            "hard stop",
        )
        self.assertEqual(len(report["batches"]), 1, "no further batches")
        self.assertEqual(
            report["swap_retries"][0]["state"], "blocked_global"
        )

    # T16 end-to-end: a consent-phase interrupt marks the video
    # interrupted_unknown durably and skips the grouped verification
    # through the existing run-level interrupt guard.
    def test_journey_consent_interrupt_skips_grouped_verification(self) -> None:
        stages = RetryCollectionStages(
            whisper_running=True, vision_outcomes=["failed", "complete"]
        )
        consent = KIOnRetryConsent()
        code, report = self.run_collection(stages, consent, video_ns=[1, 2])
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])
        self.assertIsNone(
            report["verification"],
            "grouped verification must not proceed after the interrupt",
        )
        retries = report["swap_retries"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["state"], "interrupted_unknown")
        self.assertIn("before any mutation", retries[0]["message"])
        # The retry ask was interrupted BEFORE the window was recorded;
        # only browser + run-plan were asked.
        self.assertEqual(consent.kinds, ["browser", "run-plan"])
        # Only the ORIGINAL batch vision ran — no retry attempt.
        self.assertEqual(stages.calls.count(f"vision:{fake_id(0)}"), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
