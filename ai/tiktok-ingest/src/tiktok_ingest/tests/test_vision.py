"""Vision stage on llm-hub: OpenAI chat payload, readiness, rereads, markdown.

Fixture-only: no GPU, no podman, no network. Every side effect (runner,
urlopen, POST transport) is faked.
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
from tiktok_ingest.state import StateRoot, atomic_write_bytes
from tiktok_ingest.tests.fixtures import FAKE_IMAGE_ID, FakeResponse, MEDIA_SHA256
from tiktok_ingest.vision import (
    PROMPT,
    VisionError,
    build_payload,
    flag_dense,
    flag_repetitive,
    flag_truncated,
    frame_flags,
    is_empty_response,
    parse_chat_response,
    redact_payload,
    run_vision_stage,
    vision_fingerprint,
)

VIDEO_ID = "7684788219510033665"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfixture-frame-bytes"


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


def chat_raw(text: str, finish_reason: str = "stop") -> str:
    return json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": 346, "completion_tokens": 21},
            "timings": {"prompt_ms": 120.5, "predicted_ms": 900.25},
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


class FakeHub:
    """llm-hub ``GET /v1/models``; anything else is a test failure."""

    def __init__(self, models: list[str] | None = None, down: bool = False) -> None:
        self.models = [config.VISION_MODEL] if models is None else models
        self.down = down
        self.urls: list[str] = []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        if self.down:
            raise OSError("connection refused")
        if req.full_url == f"{config.LLM_HUB_CHAT_API_BASE}/models":
            return FakeResponse(
                json.dumps(
                    {"object": "list", "data": [{"id": name} for name in self.models]}
                ).encode()
            )
        raise AssertionError(f"unexpected URL {req.full_url}")


class FakePoster:
    """The chat completions transport for frame requests."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)
        self.payloads: list[dict] = []
        self.urls: list[str] = []
        self.calls = 0

    def __call__(self, url, payload, timeout):
        self.calls += 1
        self.urls.append(url)
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
    """Wires a fully faked vision stage invocation."""

    def __init__(
        self,
        state: StateRoot,
        *,
        responses: list[str],
        hub: FakeHub | None = None,
        ffmpeg_should_fail: bool = False,
        monotonic=None,
    ) -> None:
        self.state = state
        self.runner = FakeRunner()
        self.runner.ffmpeg_should_fail = ffmpeg_should_fail
        self.hub = hub or FakeHub()
        self.poster = FakePoster(responses)
        self._monotonic = monotonic or time.monotonic

    def run(self, *, video_id: str = VIDEO_ID, force: bool = False, poster=None):
        return run_vision_stage(
            video_id,
            state=self.state,
            runner=self.runner,
            urlopen=self.hub,
            force=force,
            post_json_fn=poster or self.poster,
            monotonic=self._monotonic,
        )


# --------------------------------------------------------------------------
# Payload unit tests
# --------------------------------------------------------------------------


class PayloadTests(unittest.TestCase):
    def test_payload_is_one_image_openai_chat_request(self) -> None:
        payload = build_payload("QkFTRTY0")
        self.assertEqual(
            payload,
            {
                "model": "qwen-3.8-27b",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,QkFTRTY0"},
                            },
                        ],
                    }
                ],
                "stream": False,
                "temperature": 0.1,
                "max_tokens": 1024,
            },
        )
        self.assertIn("NEVER guess, infer, or complete URLs", PROMPT)

    def test_redact_replaces_image_with_sha256_marker(self) -> None:
        payload = build_payload("QkFTRTY0")
        digest = hashlib.sha256(b"BASE64").hexdigest()
        redacted = redact_payload(payload, digest)
        image_part = redacted["messages"][0]["content"][1]
        self.assertEqual(image_part["image_url"], {"url": "base64:sha256:" + digest})
        self.assertNotIn("QkFTRTY0", json.dumps(redacted))
        self.assertEqual(
            payload["messages"][0]["content"][1]["image_url"]["url"],
            "data:image/png;base64,QkFTRTY0",
            "original untouched",
        )

    def test_chat_response_parsing(self) -> None:
        parsed = parse_chat_response(chat_raw("hello", "length"))
        self.assertEqual(parsed["response_text"], "hello")
        self.assertEqual(parsed["done_reason"], "length")
        self.assertEqual(parsed["prompt_tokens"], 346)
        self.assertEqual(parsed["completion_tokens"], 21)
        self.assertEqual(parsed["predicted_ms"], 900.25)

    def test_chat_response_rejects_garbage(self) -> None:
        for raw in ("not json", "[]", json.dumps({"choices": []})):
            with self.subTest(raw=raw), self.assertRaises(VisionError):
                parse_chat_response(raw)

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


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_binds_media_manifest_model_and_backend(self) -> None:
        fp = vision_fingerprint(MEDIA_SHA256, "a" * 64)
        self.assertEqual(fp["stage"], "vision")
        self.assertEqual(fp["input_sha256"], "a" * 64)
        self.assertEqual(fp["versions"]["media_sha256"], MEDIA_SHA256)
        self.assertEqual(fp["versions"]["vision_model"], "qwen-3.8-27b")
        self.assertIn("llm-hub", fp["versions"]["backend"])
        self.assertEqual(
            fp["versions"]["payload_options"], "temperature=0.1,max_tokens=1024"
        )
        self.assertNotIn("ollama_version", fp["versions"])


# --------------------------------------------------------------------------
# Stage flows
# --------------------------------------------------------------------------


class VisionStageBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class VisionPreconditionTests(VisionStageBase):
    def test_llm_hub_down_blocks_with_start_hint(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(state, responses=[], hub=FakeHub(down=True))
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("llm-hub chat is not reachable", outcome.reason)
        self.assertIn("systemctl --user start llm-hub-chat", outcome.reason)
        self.assertEqual(harness.poster.calls, 0)

    def test_model_not_served_blocks(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(state, responses=[], hub=FakeHub(models=["other"]))
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("does not serve model 'qwen-3.8-27b'", outcome.reason)
        self.assertEqual(harness.poster.calls, 0)

    def test_blocklisted_video_blocks(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        from tiktok_ingest.contracts import BlocklistEntry
        from tiktok_ingest.state import Blocklist

        Blocklist(state).reject(
            BlocklistEntry(video_id=VIDEO_ID, rejected_at=utc_now_iso(), reason="operator")
        )
        harness = VisionHarness(state, responses=[])
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)

    def test_prepare_incomplete_fails_closed(self) -> None:
        state, _ = make_prepared_job(self.tmp, prepare_complete=False)
        harness = VisionHarness(state, responses=[])
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("prepare", outcome.reason)

    def test_unknown_video_fails(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        harness = VisionHarness(state, responses=[])
        outcome = harness.run(video_id="9999999999999999999")
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)


class VisionHappyPathTests(VisionStageBase):
    def test_complete_run_writes_video_md_and_records(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=3)
        responses = [
            chat_raw("frame one text"),
            chat_raw("frame two text"),
            chat_raw("frame three text"),
        ]
        harness = VisionHarness(state, responses=responses)
        outcome = harness.run()
        self.assertEqual(outcome.outcome, "complete", outcome.reason)
        self.assertEqual(outcome.frames_processed, 3)
        self.assertEqual(outcome.rereads_used, 0)

        # Every request goes to llm-hub chat completions, one image each.
        self.assertEqual(
            set(harness.poster.urls),
            {"http://127.0.0.1:8081/v1/chat/completions"},
        )
        for index, payload in enumerate(harness.poster.payloads, start=1):
            frame_bytes = PNG_BYTES + bytes([index])
            self.assertEqual(
                payload["messages"][0]["content"][1]["image_url"]["url"],
                "data:image/png;base64," + base64.b64encode(frame_bytes).decode("ascii"),
            )

        video_md = (run_dir / "video.md").read_text()
        self.assertIn("# video.md — " + VIDEO_ID, video_md)
        self.assertIn("generator: llm-hub llama-chat", video_md)
        self.assertIn("model qwen-3.8-27b", video_md)
        self.assertNotIn("ollama", video_md.lower())
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
            self.assertEqual(
                document["messages"][0]["content"][1]["image_url"], {"url": expected}
            )
            self.assertNotIn(base64.b64encode(frame_bytes).decode("ascii"), json.dumps(document))
        self.assertEqual(len(list(vision_dir.glob("*-response.json"))), 3)
        records = json.loads((vision_dir / "vision-records.json").read_text())
        self.assertEqual(records["frames"][0]["prompt_tokens"], 346)

        manifest = JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text())
        )
        self.assertEqual(
            manifest.stages["vision"].result.outcome, StageOutcome.COMPLETE
        )
        self.assertEqual(manifest.resource_gates["vision"]["model"], "qwen-3.8-27b")
        self.assertEqual(
            manifest.resource_gates["vision"]["endpoint"],
            "http://127.0.0.1:8081/v1/chat/completions",
        )

        from tiktok_ingest.state import Processed

        entry = Processed(state).get(VIDEO_ID)
        self.assertIn("vision", entry.stage_fingerprints)
        self.assertTrue(any(a.endswith("video.md") for a in entry.artifacts))

    def test_never_queries_or_touches_containers(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(state, responses=[chat_raw("x")])
        self.assertEqual(harness.run().outcome, "complete")
        for argv in harness.runner.calls:
            self.assertNotIn(argv[:2], (["podman", "ps"], ["podman", "stop"], ["podman", "start"]))
            self.assertNotEqual(argv[0], "nvidia-smi")

    def test_reuse_performs_zero_inference(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(state, responses=[chat_raw("only frame")])
        first = harness.run()
        self.assertEqual(first.outcome, "complete")

        second_poster = FakePoster([])
        second = harness.run(poster=second_poster)
        self.assertEqual(second.outcome, "complete")
        self.assertTrue(second.reused)
        self.assertEqual(second_poster.calls, 0, "reuse must NOT re-run inference")


class VisionFailureTests(VisionStageBase):
    def test_deadline_exceeded_is_budget_exceeded(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=3)
        ticks = iter([0.0, 0.1, 0.2, 10_000.0])
        harness = VisionHarness(
            state,
            responses=[chat_raw("1"), chat_raw("2"), chat_raw("3")],
            monotonic=lambda: next(ticks, 10_001.0),
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, StageOutcome.BUDGET_EXCEEDED.value)
        self.assertIn("Vision stage deadline", outcome.reason)

    def test_request_failure_records_frame_and_fails_without_retry(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=2)
        harness = VisionHarness(state, responses=[])

        calls = []

        def failing_post(url, payload, timeout):
            calls.append(url)
            raise OSError("llm-hub vanished")

        outcome = harness.run(poster=failing_post)
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("automatic retry", outcome.reason)
        self.assertEqual(len(calls), 1)
        records = json.loads((run_dir / "vision" / "vision-records.json").read_text())
        self.assertEqual(records["status"], "failed")
        self.assertIn("chat request failed", str(records["frames"][0]["error"]))
        manifest = JobManifest.from_dict(json.loads((run_dir / "meta.json").read_text()))
        self.assertEqual(manifest.stages["vision"].result.outcome, StageOutcome.FAILED)
        self.assertFalse((run_dir / "video.md").exists())

    def test_missing_frame_file_fails(self) -> None:
        state, run_dir = make_prepared_job(self.tmp, frame_count=1)
        (run_dir / "frames" / "hybrid_01.png").unlink()
        harness = VisionHarness(state, responses=[chat_raw("1")])
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
            responses.append(chat_raw(f"frame {i}", "length"))
            if i <= config.MAX_TARGETED_REREADS:
                responses.append(chat_raw(f"crop reread {i}", "stop"))
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
            state, responses=[chat_raw("", "stop"), chat_raw("recovered", "stop")]
        )
        outcome = harness.run()
        self.assertEqual(outcome.outcome, "complete")
        self.assertEqual(outcome.rereads_used, 1)

    def test_crop_render_failure_skips_reread_without_retry(self) -> None:
        state, _ = make_prepared_job(self.tmp, frame_count=1)
        harness = VisionHarness(
            state,
            responses=[chat_raw("truncated", "length")],
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


if __name__ == "__main__":
    unittest.main()
