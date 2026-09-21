"""Milestone-3 vision stage: payload, gates, rereads, markdown, CLI flows.

Fixture-only: no GPU, no podman, no network, no Ollama. Every side effect
(runnner, urlopen, gpu, vmstat, server log, POST transport) is faked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.contracts import JobManifest, ProcessedEntry, StageOutcome, StageRecord, StageResult, utc_now_iso
from tiktok_ingest.gates import GpuMemory, SwapSnapshot
from tiktok_ingest.prepare import resolve_image_id
from tiktok_ingest.state import StateRoot, atomic_write_bytes
from tiktok_ingest.tests.fixtures import FAKE_IMAGE_ID, MEDIA_SHA256
from tiktok_ingest.vision import (
    PROMPT,
    bounded_unload_cleanup,
    build_payload,
    flag_dense,
    flag_repetitive,
    flag_truncated,
    frame_flags,
    is_empty_response,
    parse_generate_response,
    query_gpu,
    redact_payload,
    run_vision_stage,
    vision_fingerprint,
)

VIDEO_ID = "7684788219510033665"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfixture-frame-bytes"


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


def gpu_value(used: int, total: int = 24564) -> GpuMemory:
    return GpuMemory(total_mib=total, used_mib=used, free_mib=total - used)


def swap_value(pswpin: int, pswpout: int = 0) -> SwapSnapshot:
    return SwapSnapshot(pswpin_pages=pswpin, pswpout_pages=pswpout, page_size_bytes=4096)


def generate_raw(text: str, done_reason: str = "stop") -> str:
    return json.dumps(
        {
            "response": text,
            "done_reason": done_reason,
            "load_duration": 1000,
            "prompt_eval_duration": 2000,
            "eval_duration": 3000,
            "eval_count": 10,
        }
    )


class FakeRunner:
    """podman ps / podman inspect / container ffmpeg — scripted."""

    def __init__(self, ps_names: str = "") -> None:
        self.ps_names = ps_names
        self.calls: list[list[str]] = []
        self.ffmpeg_should_fail = False

    def __call__(self, argv, timeout=None):
        self.calls.append(list(argv))
        if argv[:2] == ["podman", "ps"]:
            return 0, self.ps_names, ""
        if argv[:2] == ["podman", "inspect"]:
            return 0, FAKE_IMAGE_ID + "\n", ""
        if "ffmpeg" in argv and argv[0] == "podman":
            if self.ffmpeg_should_fail:
                return 1, "", "ffmpeg boom"
            # Map /work/<relative> outputs back to the host run_dir mount.
            for index, item in enumerate(argv):
                if item == "-v" and index + 1 < len(argv) and item_next_is_work_mount(argv, index):
                    mount = argv[index + 1]
                    host_root = Path(mount.split(":/work")[0])
                    for candidate in argv:
                        if candidate.startswith("/work/") and candidate.endswith(".png"):
                            target = host_root / candidate[len("/work/") :]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(PNG_BYTES)
            return 0, "", ""
        return 0, "", ""


def item_next_is_work_mount(argv: list[str], index: int) -> bool:
    return argv[index + 1].endswith(":/work")


class FakeOllama:
    """Dispatches /api/version, unload /api/generate and /api/ps."""

    def __init__(self, version: str | None = config.GATES.isolated_ollama_version,
                 resident: list[dict] | None = None, down: bool = False) -> None:
        self.version = version
        self.resident = resident if resident is not None else []
        self.down = down
        self.unload_requests = 0

    def __call__(self, req, timeout=None):
        if self.down:
            raise OSError("connection refused")
        url = req.full_url
        from tiktok_ingest.tests.fixtures import FakeResponse

        if url.endswith("/api/version"):
            if self.version is None:
                raise OSError("no version endpoint")
            return FakeResponse(json.dumps({"version": self.version}).encode())
        if url.endswith("/api/ps"):
            return FakeResponse(json.dumps({"models": self.resident}).encode())
        if url.endswith("/api/generate"):
            payload = json.loads(req.data.decode("utf-8"))
            if payload.get("keep_alive") == 0:
                self.unload_requests += 1
            return FakeResponse(b'{"done":true}')
        raise AssertionError(f"unexpected URL {url}")


class FakePoster:
    """The /api/generate transport for frame requests."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)
        self.payloads: list[dict] = []
        self.calls = 0

    def __call__(self, url, payload, timeout):
        self.calls += 1
        self.payloads.append(json.loads(json.dumps(payload)))
        if not self.responses:
            raise AssertionError("script ran out of responses")
        return self.responses.popleft(), 0.5


def make_prepared_job(
    tmp: Path,
    *,
    video_id: str = VIDEO_ID,
    frame_count: int = 2,
    prepare_complete: bool = True,
) -> tuple[StateRoot, Path]:
    state = StateRoot(tmp / "state")
    state.ensure_layout()
    run_id = "run-1"
    run_dir = state.run_dir(run_id, video_id)
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_records = []
    for index in range(1, frame_count + 1):
        name = f"hybrid_{index:02d}.png"
        (frames_dir / name).write_bytes(PNG_BYTES + bytes([index]))
        frame_records.append(
            {
                "index": index,
                "filename": name,
                "pts": 12800 * index,
                "timestamp_seconds": 1.0 * index,
                "sha256": hashlib.sha256(PNG_BYTES + bytes([index])).hexdigest(),
                "size_bytes": len(PNG_BYTES) + 1,
                "selection_reasons": ["uniform_coverage"],
                "is_crop_candidate": False,
                "crop_metadata_only": None,
            }
        )
    (run_dir / "audio.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00")
    sampling_manifest = {
        "video_id": video_id,
        "media_sha256": MEDIA_SHA256,
        "frames": frame_records,
        "audio": {"filename": "audio.wav"},
    }
    atomic_write_bytes(
        run_dir / "sampling-manifest.json",
        json.dumps(sampling_manifest, indent=2).encode("utf-8") + b"\n",
    )
    now = utc_now_iso()
    manifest = JobManifest(
        video_id=video_id,
        media_sha256=MEDIA_SHA256,
        created_at=now,
        updated_at=now,
    )
    if prepare_complete:
        manifest.stages["prepare"] = StageRecord(
            stage="prepare",
            result=StageResult(outcome=StageOutcome.COMPLETE, reason="fixture"),
            fingerprint={"stage": "prepare", "input_sha256": MEDIA_SHA256, "versions": {}},
            input_sha256=MEDIA_SHA256,
        )
    atomic_write_bytes(
        run_dir / "meta.json",
        json.dumps(manifest.to_dict(), indent=2).encode("utf-8") + b"\n",
    )
    processed = ProcessedEntry(
        video_id=video_id,
        media_sha256=MEDIA_SHA256,
        completed_at=now,
        stage_fingerprints={},
        artifacts=[f"runs/{run_id}/{video_id}"],
    )
    from tiktok_ingest.state import Processed

    Processed(state).record(processed)
    return state, run_dir


class VisionHarness:
    """Wires a fully faked vision stage invocation.

    GPU behaviour is MODELLED, not scripted per call: the first query is
    the session baseline, every query while the model is resident reports
    ``loaded_used``, and once the fake Ollama served the unload request
    the reported usage drops to ``release_used``. Swap stays constant.
    """

    def __init__(
        self,
        state: StateRoot,
        *,
        responses: list[str],
        baseline_used: int = 2443,
        loaded_used: int = 9200,
        release_used: int = 2514,
        total_mib: int = 24564,
        swap_pages: int = 100,
        layer_report: str = "offloaded 34/34 layers to GPU",
        ps_names: str = "",
        ollama: FakeOllama | None = None,
        ffmpeg_should_fail: bool = False,
        monotonic=None,
    ) -> None:
        self.state = state
        self.runner = FakeRunner(ps_names=ps_names)
        self.runner.ffmpeg_should_fail = ffmpeg_should_fail
        self.ollama = ollama or FakeOllama()
        self.poster = FakePoster(responses)
        self.gpu_baseline = gpu_value(used=baseline_used, total=total_mib)
        self.gpu_loaded = gpu_value(used=loaded_used, total=total_mib)
        self.gpu_release = gpu_value(used=release_used, total=total_mib)
        self.swap = swap_value(swap_pages)
        self.layer_report = layer_report
        self._monotonic = monotonic or time.monotonic
        self._first_gpu_query = True

    def gpu_fn(self) -> GpuMemory:
        if self.ollama.unload_requests > 0:
            return self.gpu_release
        if self._first_gpu_query:
            self._first_gpu_query = False
            return self.gpu_baseline
        return self.gpu_loaded

    def swap_fn(self) -> SwapSnapshot:
        return self.swap

    def log_fn(self) -> str:
        return self.layer_report

    def run(self, *, force: bool = False):
        return run_vision_stage(
            VIDEO_ID,
            state=self.state,
            runner=self.runner,
            urlopen=self.ollama,
            force=force,
            gpu_fn=self.gpu_fn,
            vmstat_fn=self.swap_fn,
            read_log_fn=self.log_fn,
            post_json_fn=self.poster,
            monotonic=self._monotonic,
        )


# --------------------------------------------------------------------------
# Payload unit tests
# --------------------------------------------------------------------------


class PayloadTests(unittest.TestCase):
    def test_payload_matches_benchmark_harmonized_payload(self) -> None:
        payload = build_payload("QkFTRTY0")
        self.assertEqual(
            payload,
            {
                "model": config.VISION_MODEL,
                "prompt": PROMPT,
                "images": ["QkFTRTY0"],
                "stream": False,
                "keep_alive": "15m",
                "think": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 1024,
                    "num_ctx": 4096,
                },
            },
        )
        # Exactly ONE image per request (methodology section 5).
        self.assertEqual(len(payload["images"]), 1)
        # The prompt is byte-identical to the benchmark script's constant.
        self.assertIn("NEVER guess, infer, or complete URLs", PROMPT)

    def test_redact_replaces_image_with_sha256_marker(self) -> None:
        payload = build_payload("QkFTRTY0")
        digest = hashlib.sha256(b"BASE64").hexdigest()
        redacted = redact_payload(payload, digest)
        self.assertEqual(redacted["images"], ["base64:sha256:" + digest])
        self.assertEqual(payload["images"], ["QkFTRTY0"], "original untouched")

    def test_generate_response_parsing(self) -> None:
        parsed = parse_generate_response(generate_raw("hello", "length"))
        self.assertEqual(parsed["response_text"], "hello")
        self.assertEqual(parsed["done_reason"], "length")
        self.assertEqual(parsed["eval_duration_ns"], 3000)

    def test_generate_response_rejects_garbage(self) -> None:
        import tiktok_ingest.vision as vision

        with self.assertRaises(vision.VisionError):
            parse_generate_response("not json")

    def test_flags(self) -> None:
        self.assertTrue(flag_truncated("length"))
        self.assertFalse(flag_truncated("stop"))
        self.assertTrue(is_empty_response("   \n"))
        repeated = "\n".join(["uv venv .venv"] * 6)
        self.assertTrue(flag_repetitive(repeated))
        self.assertFalse(flag_repetitive("\n".join(["a", "b", "a", "b", "a"])))
        self.assertTrue(flag_dense("\n".join(["line"] * 30)))
        self.assertTrue(flag_dense("x" * 2400))
        self.assertFalse(flag_dense("short"))
        flags = frame_flags("", "length")
        self.assertEqual(
            flags, {"truncated": True, "empty": True, "repetitive": False, "dense": False}
        )


class QueryGpuTests(unittest.TestCase):
    def test_parses_through_injected_runner(self) -> None:
        captured = {}

        def runner(argv, timeout=None):
            captured["argv"] = argv
            return 0, "24564, 1432, 23132\n", ""

        gpu = query_gpu(runner)
        self.assertEqual(gpu.free_mib, 23132)
        self.assertIn("--format=csv,noheader,nounits", captured["argv"])

    def test_runner_failure_raises(self) -> None:
        with self.assertRaises(Exception) as ctx:
            query_gpu(lambda argv, timeout=None: (1, "", "no nvidia-smi"))
        self.assertIn("nvidia-smi", str(ctx.exception))


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_binds_media_manifest_and_payload_pack(self) -> None:
        fp = vision_fingerprint(MEDIA_SHA256, "a" * 64)
        self.assertEqual(fp["stage"], "vision")
        self.assertEqual(fp["input_sha256"], "a" * 64)
        self.assertEqual(fp["versions"]["media_sha256"], MEDIA_SHA256)
        self.assertEqual(fp["versions"]["vision_model"], config.VISION_MODEL)
        self.assertIn("num_ctx=4096", fp["versions"]["payload_options"])


# --------------------------------------------------------------------------
# Stage flows
# --------------------------------------------------------------------------


class VisionStageBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class VisionPreconditionTests(VisionStageBase):
    def test_whisper_container_running_blocks_with_operator_message(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(
            state, responses=[generate_raw("x")], ps_names="voice-assistant-whisper\n"
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("stop the whisper service first", outcome.reason)
        self.assertIn("never stops", outcome.reason)

    def test_podman_ps_failure_blocks(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(state, responses=[generate_raw("x")])

        def broken_runner(argv, timeout=None):
            if argv[:2] == ["podman", "ps"]:
                return 1, "", "podmand down"
            return harness.runner(argv, timeout=timeout)

        outcome = run_vision_stage(
            VIDEO_ID,
            state=state,
            runner=broken_runner,
            urlopen=harness.ollama,
            gpu_fn=harness.gpu_fn,
            vmstat_fn=harness.swap_fn,
            read_log_fn=harness.log_fn,
            post_json_fn=harness.poster,
        )
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("cannot verify", outcome.reason)

    def test_ollama_down_blocks(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(
            state, responses=[generate_raw("x")], ollama=FakeOllama(down=True)
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("not reachable", outcome.reason)

    def test_global_ollama_version_blocks(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(
            state, responses=[generate_raw("x")], ollama=FakeOllama(version="0.14.2")
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("0.34.1", outcome.reason)

    def test_blocklisted_video_blocks(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        from tiktok_ingest.contracts import BlocklistEntry
        from tiktok_ingest.state import Blocklist

        Blocklist(state).reject(
            BlocklistEntry(video_id=VIDEO_ID, rejected_at=utc_now_iso(), reason="operator")
        )
        harness = VisionHarness(state, responses=[generate_raw("x")])
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)

    def test_prepare_incomplete_fails_closed(self) -> None:
        state, _ = make_prepared_job(self.tmp, prepare_complete=False)
        harness = VisionHarness(state, responses=[generate_raw("x")])
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("prepare", outcome.reason)

    def test_unknown_video_fails(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(state, responses=[generate_raw("x")])
        outcome = run_vision_stage(
            "9999999999999999999",
            state=state,
            runner=harness.runner,
            urlopen=harness.ollama,
            gpu_fn=harness.gpu_fn,
            vmstat_fn=harness.swap_fn,
            read_log_fn=harness.log_fn,
            post_json_fn=harness.poster,
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)


class VisionHappyPathTests(VisionStageBase):
    def test_complete_run_writes_video_md_and_records(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=3)
        responses = [
            generate_raw("frame one text"),
            generate_raw("frame two text"),
            generate_raw("frame three text"),
        ]
        harness = VisionHarness(state, responses=responses)
        outcome = harness.run()
        self.assertEqual(outcome.outcome, "complete", outcome.reason)
        self.assertEqual(outcome.frames_processed, 3)
        self.assertEqual(outcome.rereads_used, 0)

        video_md = (run_dir / "video.md").read_text()
        self.assertIn("# video.md — " + VIDEO_ID, video_md)
        self.assertIn("hybrid_01.png", video_md)
        self.assertIn("never instructions", video_md)
        self.assertIn("evidence/hypothesis", video_md)
        self.assertIn("frame two text", video_md)

        # Per-frame payload records carry NO base64 image bytes.
        vision_dir = run_dir / "vision"
        payload_files = sorted(vision_dir.glob("*-payload.json"))
        self.assertEqual(len(payload_files), 3)
        for index, payload_file in enumerate(payload_files, start=1):
            document = json.loads(payload_file.read_text())
            frame_bytes = PNG_BYTES + bytes([index])
            expected = "base64:sha256:" + hashlib.sha256(frame_bytes).hexdigest()
            self.assertEqual(document["images"], [expected])
            self.assertNotIn(base64.b64encode(frame_bytes).decode("ascii"), json.dumps(document))
        # Raw responses are preserved next to the payloads.
        self.assertEqual(len(list(vision_dir.glob("*-response.json"))), 3)

        # Manifest records the stage + gates; processed entry records artifact.
        manifest = JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text())
        )
        self.assertEqual(
            manifest.stages["vision"].result.outcome, StageOutcome.COMPLETE
        )
        self.assertIn("vision", manifest.resource_gates)
        gate_records = manifest.resource_gates["vision"]["gates"]
        passed = {record["gate"] for record in gate_records}
        self.assertIn("free_vram", passed)
        self.assertIn("vram_delta", passed)
        self.assertIn("swap_delta", passed)
        self.assertIn("offload", passed)
        self.assertIn("unload_verification", passed)

        from tiktok_ingest.state import Processed

        entry = Processed(state).get(VIDEO_ID)
        self.assertIn("vision", entry.stage_fingerprints)
        self.assertTrue(any(a.endswith("video.md") for a in entry.artifacts))

        # Unload was actually requested with keep_alive=0 and /api/ps checked.
        self.assertEqual(harness.ollama.unload_requests, 1)

    def test_reuse_performs_zero_inference(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(state, responses=[generate_raw("only frame")])
        first = harness.run()
        self.assertEqual(first.outcome, "complete")

        second_poster = FakePoster([])
        second = run_vision_stage(
            VIDEO_ID,
            state=state,
            runner=harness.runner,
            urlopen=harness.ollama,
            gpu_fn=harness.gpu_fn,
            vmstat_fn=harness.swap_fn,
            read_log_fn=harness.log_fn,
            post_json_fn=second_poster,
        )
        self.assertEqual(second.outcome, "complete")
        self.assertTrue(second.reused)
        self.assertEqual(second_poster.calls, 0, "reuse must NOT re-run inference")


class VisionGateTests(VisionStageBase):
    def test_free_vram_gate_blocks_before_load(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state, responses=[generate_raw("x")], baseline_used=24464  # free = 100 MiB
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("before every Qwen load", outcome.reason)
        self.assertEqual(harness.poster.calls, 0, "blocked BEFORE the load: no request")

    def test_vram_delta_gate_fails_mid_run_without_retry(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=3)
        harness = VisionHarness(
            state,
            responses=[generate_raw("1"), generate_raw("2"), generate_raw("3")],
            loaded_used=20000,  # delta 17557 > 12288 after frame 1
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("12,288 MiB".replace(",", ""), outcome.reason)
        self.assertIn("immediate phase baseline", outcome.reason)
        self.assertEqual(harness.poster.calls, 1, "NO retries after the gate fires")
        records = json.loads(
            (run_dir / "vision" / "vision-records.json").read_text()
        )
        self.assertEqual(records["status"], "failed")

    def test_swap_gate_fails_from_immediate_baseline(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=2)
        harness = VisionHarness(state, responses=[generate_raw("1")])
        # Ambient swap growth happens DURING the phase: the first vmstat
        # read is the immediate baseline; afterwards the counters jump by
        # 600 MiB. (Growth BEFORE the baseline is attributed away by
        # design — methodology section 8.4.)
        baseline = harness.swap
        reads = {"count": 0}

        def growing_swap() -> SwapSnapshot:
            reads["count"] += 1
            if reads["count"] == 1:
                return baseline
            return swap_value(baseline.pswpin_pages + 600 * 1024 * 1024 // 4096)

        outcome = run_vision_stage(
            VIDEO_ID,
            state=state,
            runner=harness.runner,
            urlopen=harness.ollama,
            gpu_fn=harness.gpu_fn,
            vmstat_fn=growing_swap,
            read_log_fn=harness.log_fn,
            post_json_fn=harness.poster,
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("512", outcome.reason)
        self.assertEqual(harness.poster.calls, 1, "gate stop is final: no retries")

    def test_swap_growth_before_the_baseline_is_attributed_away(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(state, responses=[generate_raw("1")])
        harness.swap = swap_value(10 + 600 * 1024 * 1024 // 4096)  # ambient, pre-baseline
        outcome = harness.run()
        self.assertEqual(outcome.outcome, "complete", outcome.reason)

    def test_cpu_offload_fails_the_run(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state,
            responses=[generate_raw("1")],
            layer_report="offloaded 30/34 layers to GPU",
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("failed run, not a slow run", outcome.reason)

    def test_missing_layer_report_fails_closed(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state, responses=[generate_raw("1")], layer_report="no layer info here"
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("could not be verified", outcome.reason)

    def test_unload_verification_failure_fails(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state,
            responses=[generate_raw("1")],
            ollama=FakeOllama(resident=[{"name": config.VISION_MODEL}]),
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("residency is not empty", outcome.reason)

    def test_vram_not_released_after_unload_fails(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state,
            responses=[generate_raw("1")],
            release_used=2443 + 4000,  # +4000 MiB above baseline at unload
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("GPU release not verified", outcome.reason)

    def test_deadline_exceeded_is_budget_exceeded(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=3)
        ticks = iter([0.0, 0.1, 0.2, 10_000.0])
        harness = VisionHarness(
            state,
            responses=[generate_raw("1"), generate_raw("2"), generate_raw("3")],
            monotonic=lambda: next(ticks, 10_001.0),
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BUDGET_EXCEEDED.value)
        self.assertIn("Vision stage deadline", outcome.reason)

    def test_request_failure_records_frame_and_fails_without_retry(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=2)
        harness = VisionHarness(state, responses=[])

        def failing_post(url, payload, timeout):
            raise OSError("ollama vanished")

        outcome = run_vision_stage(
            VIDEO_ID,
            state=state,
            runner=harness.runner,
            urlopen=harness.ollama,
            gpu_fn=harness.gpu_fn,
            vmstat_fn=harness.swap_fn,
            read_log_fn=harness.log_fn,
            post_json_fn=failing_post,
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("automatic retry", outcome.reason)
        records = json.loads((run_dir / "vision" / "vision-records.json").read_text())
        self.assertIn("generate request failed", str(records["frames"][0]["error"]))

    def test_missing_frame_file_fails(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=1)
        (run_dir / "frames" / "hybrid_01.png").unlink()
        harness = VisionHarness(state, responses=[generate_raw("1")])
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("frame file missing", outcome.reason)


class VisionRereadTests(VisionStageBase):
    def test_truncation_triggers_bounded_crop_rereads(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=12)
        # Consumption is interleaved: frame N, then its reread (when the
        # budget allows). The first 8 frames each get a reread; frames 9-12
        # hit the MAX_TARGETED_REREADS budget and get none.
        responses = []
        for i in range(1, 13):
            responses.append(generate_raw(f"frame {i}", "length"))
            if i <= config.MAX_TARGETED_REREADS:
                responses.append(generate_raw(f"crop reread {i}", "stop"))
        harness = VisionHarness(state, responses=responses)
        outcome = harness.run()
        self.assertEqual(outcome.outcome, "complete", outcome.reason)
        self.assertEqual(
            outcome.rereads_used, config.MAX_TARGETED_REREADS, "budget caps at 8"
        )
        self.assertEqual(harness.poster.calls, 12 + 8)
        video_md = (run_dir / "video.md").read_text()
        self.assertIn("targeted rereads: 1", video_md)
        self.assertIn("Targeted reread 1", video_md)
        crops = list((run_dir / "vision" / "crops").glob("*-crop-*.png"))
        self.assertEqual(len(crops), config.MAX_TARGETED_REREADS)
        records = json.loads((run_dir / "vision" / "vision-records.json").read_text())
        kinds = {
            reread["kind"]
            for frame in records["frames"]
            for reread in frame["targeted_rereads"]
        }
        self.assertEqual(kinds, {"targeted_reread"})
        # Frames beyond the budget keep NO rereads but stay visible.
        late_frames = [f for f in records["frames"] if f["index"] > 8]
        self.assertTrue(late_frames and all(
            not frame["targeted_rereads"] for frame in late_frames
        ))

    def test_empty_response_triggers_reread(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state, responses=[generate_raw("", "stop"), generate_raw("recovered", "stop")]
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, "complete")
        self.assertEqual(outcome.rereads_used, 1)

    def test_crop_render_failure_skips_reread_without_retry(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state,
            responses=[generate_raw("truncated", "length")],
            ffmpeg_should_fail=True,
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, "complete")
        self.assertEqual(outcome.rereads_used, 0)
        self.assertEqual(harness.poster.calls, 1, "failed crop render is NOT retried")
        records = json.loads(
            (state.root / "runs" / "run-1" / VIDEO_ID / "vision" / "vision-records.json").read_text()
        )
        self.assertIn("reread_skips", records["frames"][0])


# --------------------------------------------------------------------------
# Gate-failure cleanup regression (unload-on-failure contract)
# --------------------------------------------------------------------------


class CleanupOllama:
    """Standalone /api/version + unload + /api/ps double with an operation log.

    Frame inference POSTs travel through the poster double, never through
    this object; it serves the precondition version check, the unload
    request (``keep_alive=0``) and the residency query, recording the
    exact order of every handled operation so tests can assert the
    CLEANUP SEQUENCE, not merely that some cleanup function was called.
    """

    def __init__(
        self,
        *,
        version: str | None = config.GATES.isolated_ollama_version,
        resident: list[dict] | None = None,
        fail_unload: bool = False,
        fail_ps: bool = False,
    ) -> None:
        self.version = version
        self.resident = resident if resident is not None else []
        self.fail_unload = fail_unload
        self.fail_ps = fail_ps
        self.unload_requests = 0
        self.ps_queries = 0
        self.events: list[str] = []

    def __call__(self, req, timeout=None):
        from tiktok_ingest.tests.fixtures import FakeResponse

        url = req.full_url
        if url.endswith("/api/version"):
            if self.version is None:
                self.events.append("version:error")
                raise OSError("no version endpoint")
            self.events.append("version")
            return FakeResponse(json.dumps({"version": self.version}).encode())
        if url.endswith("/api/generate"):
            payload = json.loads(req.data.decode("utf-8"))
            if payload.get("keep_alive") != 0:
                raise AssertionError("frame inference must use the poster transport")
            self.events.append("unload")
            if self.fail_unload:
                raise OSError("unload transport refused")
            self.unload_requests += 1
            return FakeResponse(b'{"done":true}')
        if url.endswith("/api/ps"):
            self.events.append("ps")
            self.ps_queries += 1
            if self.fail_ps:
                raise OSError("ps transport refused")
            return FakeResponse(json.dumps({"models": self.resident}).encode())
        raise AssertionError(f"unexpected URL {url}")


def swap_gate_after_read(baseline: SwapSnapshot, fail_from_read: int):
    """vmstat double: the immediate baseline, then +600 MiB swap growth."""

    reads = {"count": 0}

    def _fn() -> SwapSnapshot:
        reads["count"] += 1
        if reads["count"] < fail_from_read:
            return baseline
        return swap_value(baseline.pswpin_pages + 600 * 1024 * 1024 // 4096)

    return _fn


def run_gated_failure(state, harness, ollama, *, vmstat_fn=None):
    """Run the stage wiring every transport to the supplied doubles."""
    return run_vision_stage(
        VIDEO_ID,
        state=state,
        runner=harness.runner,
        urlopen=ollama,
        gpu_fn=harness.gpu_fn,
        vmstat_fn=vmstat_fn or harness.swap_fn,
        read_log_fn=harness.log_fn,
        post_json_fn=harness.poster,
    )


class GateFailureCleanupTests(VisionStageBase):
    """Regression: a fired gate (or any handled failure) must request the
    model unload, verify residency and release through the existing
    mechanisms, and report BOTH the original failure and any cleanup
    failure. Fixture-only: no GPU, no network, no services."""

    def test_gate_during_inference_unloads_and_verifies_release_in_order(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=2)
        ollama = CleanupOllama()
        harness = VisionHarness(
            state, responses=[generate_raw("frame one")], ollama=ollama
        )
        outcome = run_gated_failure(
            state, harness, ollama, vmstat_fn=swap_gate_after_read(harness.swap, 2)
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        # No new inference after the gate: exactly one frame request ran.
        self.assertEqual(harness.poster.calls, 1)
        # Cleanup ORDER: unload requested first, residency verified after.
        self.assertEqual(ollama.events, ["version", "unload", "ps"])
        self.assertEqual(ollama.unload_requests, 1)
        # Release claimed only with positive evidence, recorded durably.
        self.assertTrue(outcome.cleanup["release_verified"], outcome.cleanup)
        self.assertTrue(outcome.cleanup["unload_requested"])
        records = json.loads((run_dir / "vision" / "vision-records.json").read_text())
        self.assertEqual(records["status"], "failed")
        self.assertTrue(records["gates"]["cleanup"]["release_verified"])
        manifest = JobManifest.from_dict(json.loads((run_dir / "meta.json").read_text()))
        self.assertEqual(
            manifest.stages["vision"].result.outcome, StageOutcome.FAILED
        )
        self.assertEqual(
            manifest.stages["vision"].fingerprint, {},
            "an interrupted job must never be recorded as complete",
        )

    def test_gate_before_load_requests_no_unload_but_verifies_residency(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        ollama = CleanupOllama()
        harness = VisionHarness(
            state, responses=[generate_raw("x")], baseline_used=24464, ollama=ollama
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertEqual(harness.poster.calls, 0, "blocked BEFORE the load")
        self.assertNotIn("unload", ollama.events, "nothing was loaded: no unload request")
        self.assertIn("ps", ollama.events, "residency is still verified defensively")
        self.assertTrue(outcome.cleanup["release_verified"])
        self.assertFalse(outcome.cleanup["unload_requested"])

    def test_inference_exception_requests_unload_for_partial_load(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=2)
        ollama = CleanupOllama()
        harness = VisionHarness(state, responses=[], ollama=ollama)

        def failing_post(url, payload, timeout):
            raise OSError("ollama vanished mid-request")

        outcome = run_vision_stage(
            VIDEO_ID,
            state=state,
            runner=harness.runner,
            urlopen=ollama,
            gpu_fn=harness.gpu_fn,
            vmstat_fn=harness.swap_fn,
            read_log_fn=harness.log_fn,
            post_json_fn=failing_post,
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("automatic retry", outcome.reason)
        # An ATTEMPTED request may have partially loaded the model: the
        # unload must be requested even though no response ever arrived.
        self.assertIn("unload", ollama.events)
        self.assertTrue(outcome.cleanup["unload_requested"])
        self.assertTrue(outcome.cleanup["release_verified"], outcome.cleanup)

    def test_unload_failure_preserves_original_and_cleanup_errors(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=1)
        ollama = CleanupOllama(fail_unload=True, fail_ps=True)
        harness = VisionHarness(
            state, responses=[generate_raw("frame one")], ollama=ollama
        )
        outcome = run_gated_failure(
            state, harness, ollama, vmstat_fn=swap_gate_after_read(harness.swap, 2)
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        # BOTH facts survive in the durable reason: the gate failure AND
        # the cleanup failures — neither hides the other.
        self.assertIn("swap I/O delta", outcome.reason)
        self.assertIn("unload transport refused", outcome.reason)
        self.assertIn("ps transport refused", outcome.reason)
        self.assertIn("model release NOT verified", outcome.reason)
        self.assertFalse(outcome.cleanup["release_verified"])
        self.assertFalse(outcome.cleanup["unload_ok"])
        self.assertIsNone(outcome.cleanup["resident_models"])
        manifest = JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text())
        )
        self.assertIn(
            "unload transport refused", manifest.stages["vision"].result.reason
        )
        cleanup = manifest.resource_gates["vision"]["cleanup"]
        self.assertFalse(cleanup["release_verified"])
        self.assertEqual(len(cleanup["errors"]), 2)

    def test_model_still_resident_after_cleanup_is_never_claimed_released(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        ollama = CleanupOllama(resident=[{"name": config.VISION_MODEL}])
        harness = VisionHarness(
            state, responses=[generate_raw("frame one")], ollama=ollama
        )
        outcome = run_gated_failure(
            state, harness, ollama, vmstat_fn=swap_gate_after_read(harness.swap, 2)
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertEqual(ollama.unload_requests, 1)
        self.assertEqual(outcome.cleanup["resident_models"], [config.VISION_MODEL])
        self.assertFalse(outcome.cleanup["release_verified"])
        self.assertIn("model release NOT verified", outcome.reason)
        self.assertNotIn("release verified]", outcome.reason)

    def test_success_path_unload_transport_error_fails_with_cleanup_evidence(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=1)
        ollama = CleanupOllama(fail_unload=True)
        harness = VisionHarness(
            state, responses=[generate_raw("frame one")], ollama=ollama
        )
        outcome = harness.run()  # must not raise a bare transport error
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("unload verification transport failed", outcome.reason)
        self.assertFalse(outcome.cleanup["release_verified"])
        manifest = JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text())
        )
        self.assertEqual(
            manifest.stages["vision"].result.outcome, StageOutcome.FAILED
        )

    def test_cleanup_never_stops_or_starts_services(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        ollama = CleanupOllama()
        harness = VisionHarness(
            state, responses=[generate_raw("frame one")], ollama=ollama
        )
        outcome = run_gated_failure(
            state, harness, ollama, vmstat_fn=swap_gate_after_read(harness.swap, 2)
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        for argv in harness.runner.calls:
            self.assertEqual(
                argv[:2],
                ["podman", "ps"],
                f"cleanup must not run processes: {argv}",
            )
        joined = " ".join(" ".join(argv) for argv in harness.runner.calls)
        for forbidden in ("stop", "kill", "systemctl", "start", "whisper"):
            self.assertNotIn(forbidden, joined)

    def test_partial_results_preserved_and_no_new_inference_after_gate(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=3)
        ollama = CleanupOllama()
        harness = VisionHarness(
            state,
            responses=[generate_raw("frame one text"), generate_raw("frame two text")],
            ollama=ollama,
        )
        outcome = run_gated_failure(
            state, harness, ollama, vmstat_fn=swap_gate_after_read(harness.swap, 3)
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertEqual(harness.poster.calls, 2, "gate stop is final: no retries")
        # Frame 1 completed and stays recorded; frame 2's request ran but
        # its gate check fired before the entry was recorded, so it is
        # NOT counted as processed (its raw response remains on disk).
        self.assertEqual(outcome.frames_processed, 1)
        records = json.loads((run_dir / "vision" / "vision-records.json").read_text())
        self.assertEqual(len(records["frames"]), 1)
        self.assertEqual(records["frames"][0]["response_text"], "frame one text")
        manifest = JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text())
        )
        self.assertNotEqual(
            manifest.stages["vision"].result.outcome, StageOutcome.COMPLETE
        )

    def test_operator_interrupt_runs_cleanup_and_stays_incomplete(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=2)
        ollama = CleanupOllama()
        harness = VisionHarness(state, responses=[], ollama=ollama)

        def interrupting_post(url, payload, timeout):
            raise KeyboardInterrupt()

        outcome = run_vision_stage(
            VIDEO_ID,
            state=state,
            runner=harness.runner,
            urlopen=ollama,
            gpu_fn=harness.gpu_fn,
            vmstat_fn=harness.swap_fn,
            read_log_fn=harness.log_fn,
            post_json_fn=interrupting_post,
        )
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("interrupted", outcome.reason)
        self.assertIn("unload", ollama.events, "cleanup runs after an interrupt")
        manifest = JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text())
        )
        self.assertEqual(
            manifest.stages["vision"].result.outcome, StageOutcome.FAILED
        )
        self.assertEqual(manifest.stages["vision"].fingerprint, {})


class BoundedUnloadCleanupTests(VisionStageBase):
    """Direct unit tests for the bounded, idempotent cleanup helper."""

    def _run(self, ollama, *, may_be_resident=True, gpu=None, baseline=2443):
        return bounded_unload_cleanup(
            model=config.VISION_MODEL,
            urlopen=ollama,
            gpu_fn=gpu or (lambda: gpu_value(2514)),
            session_baseline_used_mib=baseline,
            may_be_resident=may_be_resident,
        )

    def test_release_verified_requires_empty_residency_and_baseline_vram(self) -> None:
        report = self._run(CleanupOllama())
        self.assertTrue(report["release_verified"])
        self.assertTrue(report["unload_ok"])
        self.assertEqual(report["resident_models"], [])
        self.assertEqual(report["overshoot_mib"], 71)

    def test_unload_failure_still_checks_residency_and_reports_both_errors(self) -> None:
        report = self._run(CleanupOllama(fail_unload=True, fail_ps=True))
        self.assertFalse(report["unload_ok"])
        self.assertIsNone(report["resident_models"])
        self.assertFalse(report["release_verified"])
        self.assertEqual(len(report["errors"]), 2)

    def test_no_unload_request_when_nothing_was_attempted(self) -> None:
        ollama = CleanupOllama()
        report = self._run(ollama, may_be_resident=False)
        self.assertEqual(ollama.unload_requests, 0)
        self.assertFalse(report["unload_requested"])
        self.assertTrue(report["release_verified"])
        self.assertEqual(ollama.events, ["ps"])

    def test_resident_model_blocks_release_claim(self) -> None:
        report = self._run(CleanupOllama(resident=[{"name": config.VISION_MODEL}]))
        self.assertFalse(report["release_verified"])
        self.assertEqual(report["resident_models"], [config.VISION_MODEL])

    def test_gpu_failure_never_claims_release(self) -> None:
        def broken_gpu():
            raise OSError("nvidia-smi vanished")

        report = self._run(CleanupOllama(), gpu=broken_gpu)
        self.assertFalse(report["release_verified"])
        self.assertTrue(any("GPU query failed" in e for e in report["errors"]))

    def test_cleanup_is_idempotent(self) -> None:
        ollama = CleanupOllama()
        first = self._run(ollama)
        second = self._run(ollama)
        self.assertTrue(first["release_verified"])
        self.assertTrue(second["release_verified"])
        self.assertEqual(ollama.unload_requests, 2)
