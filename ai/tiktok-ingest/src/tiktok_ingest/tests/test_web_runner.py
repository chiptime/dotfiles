"""Run-launch endpoints (T4): worker thread, web consent, SSE, single writer.

RED state: ``web/consent.py`` and ``web/runner.py`` do not exist yet, so
importing this module fails — that import failure IS the observed RED for
every rule below. GREEN requires those modules.

One test per rule (S5/S6/S8, P1-FR-09/10/13/14/17):

- allow_runs False -> 409 with the exact hold message
- start -> consent prompt with the EXACT ConsentWindow fields -> grant ->
  run proceeds -> finished event with exit code
- deny -> the run stops exactly like a CLI denial (fake mirrors the CLI
  denial report; no stage work after denial)
- stale / duplicate / unknown prompt answers rejected; consent and ready
  answers cannot be swapped between prompt kinds
- second start while active -> 409 (S8 single writer)
- live foreign GPU lease owner -> 409 (lease stays authoritative)
- cancel while waiting on consent -> interrupted outcome, worker joined
- cancel during narration -> KeyboardInterrupt path (report interrupted)
- late SSE subscriber gets the buffer replay (S5)
- SSE over a REAL server on an ephemeral port, read incrementally
- readiness prompt answered via the ready action (S6)
- every POST without the token -> 403 (inherited server rule)
- options forwarded to run_ingest with the exact CLI names
- URL validated with the product's own parsers (400, not a run)
- shutdown() cancels and joins an active run (process-exit rule)

Nothing real ever runs: ``run_ingest`` is injected as a scripted double
driving the REAL :class:`tiktok_ingest.web.consent.WebConsentProvider`.
Scratch state lives in tempfile directories outside the worktree.

SSE goes through the real ``WebRequestHandler._send`` streaming path
(iterable body, ``Connection: close``, no ``Content-Length``).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from tiktok_ingest import gpu_lease, guided
from tiktok_ingest.engine import ConsentWindow
from tiktok_ingest.state import StateRoot
from tiktok_ingest.web.server import (
    LOOPBACK_HOST,
    LoopbackServer,
    ServerState,
    WebRequestHandler,
    create_app,
)
from tiktok_ingest.web.consent import WebConsentProvider

TOKEN_HEADER = "X-TI-Token"

VIDEO_URL = "https://www.tiktok.com/@fixture_author/video/7123456789012345678"
COLLECTION_URL = (
    "https://www.tiktok.com/@fixture_author/collection/guided-col"
)
HOLD_MESSAGE = (
    "run launching from the web is disabled until the Phase 0 acceptance "
    "checklist is closed; use `ti <url>` in a terminal"
)

WINDOW = ConsentWindow(
    kind="tanda",
    title="confirm this tanda (nothing has run yet)",
    ids=("7123456789012345678",),
    operations=(
        "process exactly these ids through fetch -> prepare -> vision "
        "-> audio -> synthesis ingestion -> verify, with a SEPARATE "
        "consent window before each effectful phase",
    ),
    limits=("batch cap 5; nothing outside this list is touched",),
    effects=(
        "each later window asks again before its own effects; denial "
        "of any window stops before that window's operations",
    ),
)
DENIAL_REPORT = {
    "mode": "execute",
    "interrupted": False,
    "failures": [],
    "notes": [
        "run-plan confirmation denied: no stage ran and nothing was "
        "written; re-invoke to be asked again"
    ],
}
# The window document EXACTLY as it crosses JSON (tuples become lists on
# the wire; the fields themselves are ConsentWindow.to_dict() verbatim).
WINDOW_JSON = json.loads(json.dumps(WINDOW.to_dict()))
REPORT_PATH = "/tmp/state/reports/guided-col/2026-11-04T01-02-03Z.json"


@contextlib.contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


class Harness(unittest.TestCase):
    """Real loopback server + runs routes on an ephemeral port."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = StateRoot(Path(self._tmp.name) / "state")
        self.state_root.ensure_layout()
        self.ingest = _ScriptedIngest()
        from tiktok_ingest.web.runner import WebRunner, register_runs

        self.runner = WebRunner(
            self.state_root, allow_runs=True, ingest=self.ingest
        )
        state = ServerState(state_root=self.state_root, token="test-token")
        self.app = create_app(state)
        register_runs(self.app, self.runner)
        self.server = self.enterContext(
            serving(LoopbackServer(("127.0.0.1", 0), WebRequestHandler, app=self.app))
        )
        self.port = self.server.server_port
        self.token = "test-token"

    # -- HTTP helpers ------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        token: str | None = "test-token",
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"X-TI-Token": token} if token else {}
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def post_json(
        self, method: str, path: str, payload: object | None = None, **kwargs: Any
    ) -> tuple[int, Any]:
        status, _, body = self.request(method, path, payload, **kwargs)
        return status, json.loads(body) if body else None

    def wait_until(
        self, predicate: Callable[[], Any], timeout: float = 5.0
    ) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.fail("condition not met within %.1fs" % timeout)

    def active(self) -> dict[str, Any]:
        status, _, body = self.request("GET", "/api/runs/active")
        self.assertEqual(status, 200)
        return json.loads(body)

    def wait_pending_prompt(self) -> dict[str, Any]:
        return self.wait_until(
            lambda: self.active().get("pending_prompt") or None
        )

    def sse_tail(self) -> "_SseTail":
        tail = _SseTail(self.port, self.token)
        self.addCleanup(tail.close)
        return tail


class _SseTail:
    """Incremental SSE reader thread over a real connection."""

    def __init__(self, port: int, token: str) -> None:
        self._conn = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=10
        )
        self._conn.request(
            "GET",
            "/api/runs/active/events",
            headers={"X-TI-Token": token, "Accept": "text/event-stream"},
        )
        self._response = self._conn.getresponse()
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    @property
    def status(self) -> int:
        return self._response.status

    @property
    def headers(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self._response.getheaders()}

    def _read(self) -> None:
        try:
            while True:
                line = self._response.readline()
                if not line:
                    return
                text = line.decode("utf-8").strip()
                if not text.startswith("data: "):
                    continue
                event = json.loads(text[len("data: "):])
                with self._lock:
                    self._events.append(event)
        except BaseException as exc:  # socket close ends the loop normally
            self._error = exc

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 5.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events():
                if predicate(event):
                    return event
            if self._error is not None and not self._thread.is_alive():
                break
            time.sleep(0.01)
        raise AssertionError(
            "event not observed within %.1fs; got %r" % (timeout, self.events())
        )

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()
        self._thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Scripted run_ingest doubles (drive the REAL WebConsentProvider)
# ---------------------------------------------------------------------------


class _ScriptedIngest:
    """Replaceable ``run_ingest`` double; the scenario drives the real
    provider, so consent/readiness/cancel semantics are exercised for real."""

    def __init__(self) -> None:
        self.scenario: Callable[..., tuple[int, dict[str, Any]]] = (
            self.ask_then_finish
        )
        self.calls: list[dict[str, Any]] = []
        self.granted: list[bool] = []
        self.readiness: list[bool] = []
        self.cancel_errors: list[BaseException] = []
        self.work_after_consent = 0

    def __call__(self, url: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.calls.append({"url": url, **kwargs})
        return self.scenario(self, url, kwargs)

    # -- scenarios ---------------------------------------------------------

    def ask_then_finish(
        self, fake: "_ScriptedIngest", url: str, kwargs: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        out = kwargs["out"]
        out("narration before consent")
        granted = kwargs["consent"].ask(WINDOW)
        fake.granted.append(granted)
        if not granted:
            return 1, DENIAL_REPORT
        out("narration after consent")
        fake.work_after_consent += 1
        out(f"collection-run report saved: {REPORT_PATH}")
        return 0, {"mode": "execute", "interrupted": False, "failures": []}

    def readiness_then_finish(
        self, fake: "_ScriptedIngest", url: str, kwargs: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        ready = kwargs["consent"].await_readiness(
            "readiness checkpoint (NOT an authorization): press Enter "
            "once you have logged in and the collection items are "
            "visible in the browser window"
        )
        fake.readiness.append(ready)
        if not ready:
            return 1, DENIAL_REPORT
        return 0, {"mode": "execute", "interrupted": False, "failures": []}

    def lines_forever(
        self, fake: "_ScriptedIngest", url: str, kwargs: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        counter = 0
        try:
            while True:
                kwargs["out"](f"working line {counter}")
                counter += 1
                time.sleep(0.005)
        except KeyboardInterrupt as exc:
            fake.cancel_errors.append(exc)
            raise
        # pragma: no cover - the loop only exits via KeyboardInterrupt


def _start_payload(url: str = VIDEO_URL, **options: Any) -> dict[str, Any]:
    return {"url": url, **options}


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class AllowRunsSwitchTests(Harness):
    def test_post_runs_refused_with_exact_hold_message(self) -> None:
        from tiktok_ingest.web.runner import WebRunner, register_runs

        runner = WebRunner(self.state_root, allow_runs=False, ingest=self.ingest)
        state = ServerState(state_root=self.state_root, token="t2")
        app = create_app(state)
        register_runs(app, runner)
        server = self.enterContext(
            serving(LoopbackServer(("127.0.0.1", 0), WebRequestHandler, app=app))
        )
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            conn.request(
                "POST",
                "/api/runs",
                body=json.dumps(_start_payload()).encode(),
                headers={"X-TI-Token": "t2"},
            )
            response = conn.getresponse()
            status, body = response.status, response.read()
        finally:
            conn.close()
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"], HOLD_MESSAGE)
        self.assertFalse(self.ingest.calls)


class HappyPathTests(Harness):
    def test_grant_flow_prompt_fields_and_finished_event(self) -> None:
        status, payload = self.post_json("POST", "/api/runs", _start_payload())
        self.assertEqual(status, 202)
        prompt = self.wait_pending_prompt()
        # EXACT ConsentWindow fields, byte-identical to the CLI rendering.
        self.assertEqual(prompt["kind"], "consent")
        self.assertEqual(prompt["window"], WINDOW_JSON)
        self.assertEqual(prompt["rendered"], guided.render_consent_window(WINDOW))

        tail = self.sse_tail()
        status, answer = self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        self.assertEqual(status, 200)

        finished = tail.wait_for(lambda event: event["type"] == "finished")
        self.assertEqual(finished["exit_code"], 0)
        self.assertFalse(finished["interrupted"])
        self.assertEqual(finished["report_path"], REPORT_PATH)
        self.assertEqual(finished["summary"]["mode"], "execute")
        resolved = tail.wait_for(
            lambda event: event["type"] == "consent-resolved"
        )
        self.assertEqual(resolved["prompt_id"], prompt["id"])
        self.assertTrue(resolved["granted"])
        lines = [
            event["text"] for event in tail.events() if event["type"] == "line"
        ]
        self.assertIn("narration before consent", lines)
        self.assertIn("narration after consent", lines)
        self.assertTrue(self.ingest.granted)
        self.wait_until(lambda: self.active()["state"] == "idle")

    def test_get_active_reports_state_and_pending_prompt(self) -> None:
        self.assertEqual(
            self.active(),
            {"state": "idle", "allow_runs": True, "run": None, "pending_prompt": None},
        )
        self.post_json("POST", "/api/runs", _start_payload())
        prompt = self.wait_pending_prompt()
        view = self.active()
        self.assertEqual(view["state"], "running")
        self.assertEqual(view["run"]["url"], VIDEO_URL)
        self.assertEqual(view["run"]["kind"], "video")
        self.assertEqual(view["run"]["options"], {"url": VIDEO_URL})
        self.assertFalse(view["run"]["cancelled"])
        self.assertEqual(view["pending_prompt"]["id"], prompt["id"])
        self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        self.wait_until(lambda: self.active()["state"] == "idle")


class DenyTests(Harness):
    def test_deny_stops_run_like_cli_denial(self) -> None:
        self.post_json("POST", "/api/runs", _start_payload())
        tail = self.sse_tail()
        prompt = self.wait_pending_prompt()
        status, _ = self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": False},
        )
        self.assertEqual(status, 200)
        finished = tail.wait_for(lambda event: event["type"] == "finished")
        # Same denial outcome as the CLI: exit 1 from the denial report,
        # and no stage work after the denial.
        self.assertEqual(finished["exit_code"], 1)
        self.assertEqual(self.ingest.granted, [False])
        self.assertEqual(self.ingest.work_after_consent, 0)
        resolved = tail.wait_for(
            lambda event: event["type"] == "consent-resolved"
        )
        self.assertFalse(resolved["granted"])


class AnswerRejectionTests(Harness):
    def test_unknown_stale_duplicate_and_wrong_kind_answers_rejected(self) -> None:
        self.ingest.scenario = self._consent_then_readiness
        self.post_json("POST", "/api/runs", _start_payload())
        prompt = self.wait_pending_prompt()

        # Unknown id while a DIFFERENT prompt is pending.
        status, body = self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": "never-issued", "granted": True},
        )
        self.assertEqual(status, 409)
        self.assertIn("unknown prompt id", body["error"])

        # A consent prompt cannot be answered through the ready action.
        status, body = self.post_json(
            "POST", "/api/runs/active/ready", {"prompt_id": prompt["id"]}
        )
        self.assertEqual(status, 409)
        self.assertIn(
            "is a consent prompt; answer it with an authorize/deny answer",
            body["error"],
        )

        # Grant it; the same id again is a stale/duplicate answer.
        status, _ = self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        self.assertEqual(status, 200)
        status, body = self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        self.assertEqual(status, 409)
        self.assertIn("already answered", body["error"])

        # Readiness prompts are answered ONLY via the ready action.
        readiness = self.wait_pending_prompt()
        self.assertEqual(readiness["kind"], "readiness")
        status, body = self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": readiness["id"], "granted": True},
        )
        self.assertEqual(status, 409)
        self.assertIn("readiness prompt", body["error"])
        status, _ = self.post_json(
            "POST", "/api/runs/active/ready", {"prompt_id": readiness["id"]}
        )
        self.assertEqual(status, 200)
        self.wait_until(lambda: self.active()["state"] == "idle")

    def test_answers_after_run_ends_are_rejected(self) -> None:
        self.post_json("POST", "/api/runs", _start_payload())
        prompt = self.wait_pending_prompt()
        self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        self.wait_until(lambda: self.active()["state"] == "idle")
        status, body = self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        self.assertEqual(status, 409)
        self.assertIn("no active run", body["error"])

    def _consent_then_readiness(
        self, fake: "_ScriptedIngest", url: str, kwargs: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        if not kwargs["consent"].ask(WINDOW):
            return 1, DENIAL_REPORT
        if not kwargs["consent"].await_readiness("readiness checkpoint"):
            return 1, DENIAL_REPORT
        return 0, {"mode": "execute", "interrupted": False}


class SingleWriterTests(Harness):
    def test_second_start_while_active_is_409(self) -> None:
        self.post_json("POST", "/api/runs", _start_payload())
        self.wait_pending_prompt()
        status, body = self.post_json("POST", "/api/runs", _start_payload())
        self.assertEqual(status, 409)
        self.assertIn("already active", body["error"])
        self.assertEqual(len(self.ingest.calls), 1)


class GpuLeaseGuardTests(Harness):
    def test_live_foreign_gpu_lease_owner_is_409(self) -> None:
        marker = gpu_lease.LeaseMarkerFile(self.state_root, pid=1)
        marker.write(
            SimpleNamespace(name="voice-assistant-whisper", image="img", env=[])
        )
        status, body = self.post_json("POST", "/api/runs", _start_payload())
        self.assertEqual(status, 409)
        self.assertIn("holds the GPU window", body["error"])
        self.assertIn("pid 1", body["error"])
        self.assertFalse(self.ingest.calls)

    def test_own_process_marker_does_not_block(self) -> None:
        marker = gpu_lease.LeaseMarkerFile(self.state_root)  # our own pid
        marker.write(
            SimpleNamespace(name="voice-assistant-whisper", image="img", env=[])
        )
        status, _ = self.post_json("POST", "/api/runs", _start_payload())
        self.assertEqual(status, 202)


class CancelTests(Harness):
    def test_cancel_while_waiting_on_consent_interrupts_and_joins(self) -> None:
        self.post_json("POST", "/api/runs", _start_payload())
        tail = self.sse_tail()
        prompt = self.wait_pending_prompt()
        with self.runner._lock:  # type: ignore[attr-defined]
            thread = self.runner._active.thread  # type: ignore[attr-defined]
        status, body = self.post_json("POST", "/api/runs/active/cancel", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["cancelled"])
        self.assertTrue(body["joined"])
        self.assertFalse(thread.is_alive())
        finished = tail.wait_for(lambda event: event["type"] == "finished")
        self.assertEqual(finished["exit_code"], 1)
        self.assertTrue(finished["interrupted"])
        resolved = tail.wait_for(
            lambda event: event["type"] == "consent-resolved"
        )
        self.assertEqual(resolved["outcome"], "cancelled")
        self.wait_until(lambda: self.active()["state"] == "idle")

    def test_cancel_during_narration_raises_keyboard_interrupt(self) -> None:
        self.ingest.scenario = self.ingest.lines_forever
        self.post_json("POST", "/api/runs", _start_payload())
        tail = self.sse_tail()
        tail.wait_for(lambda event: event["type"] == "line")
        status, body = self.post_json("POST", "/api/runs/active/cancel", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["joined"])
        self.assertEqual(len(self.ingest.cancel_errors), 1)
        self.assertIsInstance(self.ingest.cancel_errors[0], KeyboardInterrupt)
        finished = tail.wait_for(lambda event: event["type"] == "finished")
        self.assertTrue(finished["interrupted"])
        self.assertEqual(finished["exit_code"], 1)

    def test_cancel_without_active_run_is_409(self) -> None:
        status, body = self.post_json("POST", "/api/runs/active/cancel", {})
        self.assertEqual(status, 409)
        self.assertIn("no active run", body["error"])


class SseTests(Harness):
    def test_late_subscriber_gets_buffer_replay(self) -> None:
        self.ingest.scenario = self._lines_then_block
        self.post_json("POST", "/api/runs", _start_payload())
        tail = self.sse_tail()  # subscribes AFTER the lines were published
        first = tail.wait_for(lambda event: event["type"] == "consent")
        prompt = first["prompt"]
        replayed = [e for e in tail.events() if e["type"] == "line"]
        self.assertEqual(
            [event["text"] for event in replayed],
            ["line one", "line two", "line three"],
        )
        self.assertEqual(prompt["window"], WINDOW_JSON)
        self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        tail.wait_for(lambda event: event["type"] == "finished")

    def test_sse_stream_has_event_stream_headers_and_seq_ids(self) -> None:
        self.post_json("POST", "/api/runs", _start_payload())
        tail = self.sse_tail()
        event = tail.wait_for(lambda e: e["type"] == "consent")
        headers = tail.headers
        self.assertEqual(tail.status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        self.assertIn("no-store", headers["cache-control"])
        seqs = [e["seq"] for e in tail.events()]
        self.assertEqual(seqs, sorted(seqs))
        self.assertGreater(event["seq"], 0)

    def test_events_endpoint_is_404_without_active_run(self) -> None:
        status, _, body = self.request("GET", "/api/runs/active/events")
        self.assertEqual(status, 404)
        self.assertIn("no active run", json.loads(body)["error"])

    def _lines_then_block(
        self, fake: "_ScriptedIngest", url: str, kwargs: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        out = kwargs["out"]
        out("line one")
        out("line two")
        out("line three")
        granted = kwargs["consent"].ask(WINDOW)
        if not granted:
            return 1, DENIAL_REPORT
        return 0, {"mode": "execute", "interrupted": False}


class ReadinessTests(Harness):
    def test_readiness_prompt_answered_via_ready(self) -> None:
        self.ingest.scenario = self.ingest.readiness_then_finish
        self.post_json("POST", "/api/runs", _start_payload())
        tail = self.sse_tail()
        prompt = self.wait_pending_prompt()
        self.assertEqual(prompt["kind"], "readiness")
        self.assertIn("readiness checkpoint", prompt["prompt"])
        self.assertEqual(prompt["rendered"], prompt["prompt"])
        status, _ = self.post_json(
            "POST", "/api/runs/active/ready", {"prompt_id": prompt["id"]}
        )
        self.assertEqual(status, 200)
        finished = tail.wait_for(lambda event: event["type"] == "finished")
        self.assertEqual(finished["exit_code"], 0)
        self.assertEqual(self.ingest.readiness, [True])


class ValidationTests(Harness):
    def test_invalid_url_is_400_with_the_product_message(self) -> None:
        status, body = self.post_json(
            "POST", "/api/runs", _start_payload("https://google.com")
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported TikTok URL", body["error"])
        self.assertIn("Must be a collection URL", body["error"])
        self.assertFalse(self.ingest.calls)

    def test_unknown_option_key_is_400(self) -> None:
        status, body = self.post_json(
            "POST", "/api/runs", _start_payload(retry_failed=3)
        )
        self.assertEqual(status, 400)
        self.assertIn("unknown option", body["error"])
        status, body = self.post_json("POST", "/api/runs", {"limit": 2})
        self.assertEqual(status, 400)
        self.assertIn("url is required", body["error"])

    def test_options_forwarded_with_exact_cli_names(self) -> None:
        status, _ = self.post_json(
            "POST", "/api/runs",
            _start_payload(limit=2, dry_run=True, no_auto_verify=True, rescan=True),
        )
        self.assertEqual(status, 202)
        call = self.wait_until(lambda: self.ingest.calls and self.ingest.calls[0])
        self.assertEqual(call["url"], VIDEO_URL)
        self.assertIs(call["state"].root, self.state_root.root)
        self.assertTrue(call["dry_run"])
        self.assertEqual(call["limit"], 2)
        self.assertTrue(call["rescan"])
        self.assertFalse(call["auto_verify"])
        self.assertIsInstance(call["consent"], WebConsentProvider)
        self.assertTrue(callable(call["out"]))
        prompt = self.wait_pending_prompt()
        self.post_json(
            "POST", "/api/runs/active/consent",
            {"prompt_id": prompt["id"], "granted": True},
        )
        self.wait_until(lambda: self.active()["state"] == "idle")


class TokenTests(Harness):
    def test_every_post_without_token_is_403(self) -> None:
        paths = [
            ("/api/runs", _start_payload()),
            ("/api/runs/active/consent", {"prompt_id": "x", "granted": True}),
            ("/api/runs/active/ready", {"prompt_id": "x"}),
            ("/api/runs/active/cancel", {}),
        ]
        for path, payload in paths:
            with self.subTest(path=path):
                status, _, body = self.request(
                    "POST", path, payload, token=None
                )
                self.assertEqual(status, 403)
                self.assertNotIn(b"test-token", body)


class ShutdownTests(Harness):
    def test_shutdown_cancels_and_joins_active_run(self) -> None:
        self.post_json("POST", "/api/runs", _start_payload())
        self.wait_pending_prompt()
        with self.runner._lock:  # type: ignore[attr-defined]
            thread = self.runner._active.thread  # type: ignore[attr-defined]
        self.runner.shutdown()  # type: ignore[attr-defined]
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.active()["state"], "idle")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
