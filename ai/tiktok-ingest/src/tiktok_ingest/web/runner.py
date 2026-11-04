"""Run orchestration for the web interface (T4: S5 + S6 + S8).

``WebRunner`` owns at most ONE active ingest run in a worker thread
(S8: the web is a single writer; conflicts are reported, never queued)
and exposes it through the endpoints registered by ``register_runs``:

- ``POST /api/runs``                     start (``allow_runs`` gated)
- ``GET  /api/runs/active``              state + pending prompt
- ``POST /api/runs/active/consent``     answer the pending consent prompt
- ``POST /api/runs/active/ready``       signal browser readiness
- ``POST /api/runs/active/cancel``      cooperative cancellation
- ``GET  /api/runs/active/events``      SSE narration stream (S5)

Honesty rules carried into this module:

- The run itself is the EXISTING engine: ``ingest.run_ingest`` is called
  exactly as the CLI calls it (same URL parsers, same stage assembly,
  same report); this module adds no pipeline behavior of its own.
- The SSE stream is DERIVED narration, never an authority: the report
  stays on disk (the saved run report), and a reload rebuilds state from
  the product's own stores. The in-memory buffer is bounded.
- The GPU lease stays authoritative: a live foreign lease marker is a
  hard refusal (the marker itself is never touched here).
- Signals only reach the main thread, so cancellation is COOPERATIVE:
  ``cancel()`` resolves any pending prompt fail-closed and the worker
  raises ``KeyboardInterrupt`` at the next safe point (a narration call
  or a consent ask — both are engine-owned interruption points). A long
  blocking stage call (e.g. a model request) finishes before
  cancellation takes effect; the existing per-stage timeouts bound that
  wait. No ctypes async exceptions.
- Process exit: ``shutdown()`` cancels and bounded-joins an active run
  so the engine's GPU lease restore/marker logic runs; a hard kill is
  covered by the existing startup reconcile, never silently assumed.

Streaming seam: the SSE endpoint returns an iterable body. Until
``server.WebRequestHandler._send`` grows iterable-body support (a
one-function change proposed to the parent), that route only streams
with a seam-enabled handler; everything else is plain JSON.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections import deque
from typing import Any, Callable, Iterable, Iterator, Mapping

from .. import gpu_lease
from ..collection import CollectionError, parse_collection_url
from ..contracts import utc_now_iso
from ..fetch import PipelineError, parse_tiktok_url
from ..ingest import IngestError, run_ingest
from ..state import StateError, StateRoot
from .consent import PromptRejected, WebConsentProvider
from .server import (
    App,
    HTTPError,
    Request,
    Response,
    json_response,
    parse_json_body,
)

# The L3 hold: launching runs from the web waits for a user decision on
# the Phase 0 acceptance checklist. The message tells the operator what
# to do instead — verbatim contract with the tests and the frontend.
RUNS_DISABLED_MESSAGE = (
    "run launching from the web is disabled until the Phase 0 acceptance "
    "checklist is closed; use `ti <url>` in a terminal"
)
REVIEW_WRITING_MESSAGE = (
    "a backlog review write is in progress; retry when it finishes"
)

# The exact option names of the ingest CLI (`ti ingest --help`); the web
# payload mirrors them by name and nothing else is accepted.
ALLOWED_OPTIONS: tuple[str, ...] = ("url", "limit", "dry_run", "no_auto_verify", "rescan")
BOOL_OPTIONS: tuple[str, ...] = ("dry_run", "no_auto_verify", "rescan")

# Bounded narration history for late subscribers (S5). The buffer is a
# convenience for page reloads mid-run, never an authority.
EVENT_BUFFER_CAPACITY = 2000

# Bounded joins: cancel waits briefly for the worker to reach a safe
# point; shutdown (process exit) may wait longer. Both are upper bounds,
# not guarantees — a worker inside a long blocking stage call finishes
# that call first (per-stage timeouts bound it).
CANCEL_JOIN_SECONDS = 5.0
SHUTDOWN_JOIN_SECONDS = 30.0

# The product's own stable narration line that names the saved report
# (collection-run). The path for the finished event is derived from it;
# nothing is re-derived or guessed here.
REPORT_SAVED_PREFIX = "collection-run report saved: "

TERMINAL_EVENT = "finished"


class EventLog:
    """Bounded ring buffer of run events + per-subscriber delivery queues.

    The ring buffer only bounds the REPLAY a late subscriber receives. A
    connected subscriber has its own queue filled at publish time, so a
    burst faster than the client reads never drops its events.
    """

    def __init__(self, capacity: int = EVENT_BUFFER_CAPACITY) -> None:
        self._capacity = capacity
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._cond = threading.Condition()
        self._seq = 0
        self._subscribers: list[deque[dict[str, Any]]] = []

    def publish(self, type_: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._cond:
            self._seq += 1
            event = {"seq": self._seq, "type": type_, **dict(payload)}
            self._events.append(event)
            for queue in self._subscribers:
                queue.append(event)
            self._cond.notify_all()
        return event

    def subscribe(self) -> deque[dict[str, Any]]:
        """A delivery queue seeded with the current replay buffer."""
        with self._cond:
            queue: deque[dict[str, Any]] = deque(self._events)
            self._subscribers.append(queue)
            return queue

    def unsubscribe(self, queue: deque[dict[str, Any]]) -> None:
        with self._cond:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def take(self, queue: deque[dict[str, Any]], timeout: float) -> list[dict[str, Any]]:
        """Drain a subscriber queue, waiting up to ``timeout`` when empty."""
        with self._cond:
            if not queue:
                self._cond.wait(timeout)
            drained = [dict(event) for event in queue]
            queue.clear()
            return drained


class _ActiveRun:
    """One in-flight run: its event log, provider, worker and state."""

    def __init__(self, url: str, kind: str, options: dict[str, Any]) -> None:
        self.url = url
        self.kind = kind  # "video" | "collection" (from URL validation)
        self.options = options
        self.started_at = utc_now_iso()
        self.events = EventLog()
        self.cancel_event = threading.Event()
        self.provider = WebConsentProvider(self.events.publish)
        self.thread: threading.Thread | None = None
        self.report_path: str | None = None

    # -- view documents -----------------------------------------------------

    def view(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "kind": self.kind,
            "started_at": self.started_at,
            "options": dict(self.options),
            "cancelled": self.cancel_event.is_set(),
        }

    # -- cooperative cancellation -------------------------------------------

    def mark_cancelled(self) -> None:
        """Fail-closed unblock: set the flag FIRST so a worker waking
        from a cancelled prompt sees it at its next safe point."""
        self.cancel_event.set()
        self.provider.cancel_pending()


def _summarize_report(report: Any) -> dict[str, Any] | None:
    """A bounded, frontend-facing subset of the run report; the report
    itself stays on disk. Never raises on an unexpected report shape."""
    if not isinstance(report, Mapping):
        return None
    summary: dict[str, Any] = {}
    for key in (
        "mode",
        "interrupted",
        "hard_stop_reason",
        "collection_key",
        "collection_url",
        "final_plan_counts",
        "backlog_pending_view",
    ):
        if key in report:
            summary[key] = report[key]
    failures = report.get("failures")
    if isinstance(failures, list):
        summary["failures_count"] = len(failures)
    return summary or None


def _sse_chunk(event: Mapping[str, Any]) -> bytes:
    return "id: {}\ndata: {}\n\n".format(
        event["seq"], json.dumps(event, ensure_ascii=False)
    ).encode("utf-8")


class WebRunner:
    """Single-writer (S8) orchestrator of web-launched ingest runs."""

    def __init__(
        self,
        state_root: StateRoot,
        *,
        allow_runs: bool = False,
        ingest: Callable[..., tuple[int, dict[str, Any]]] = run_ingest,
        cancel_join_seconds: float = CANCEL_JOIN_SECONDS,
        shutdown_join_seconds: float = SHUTDOWN_JOIN_SECONDS,
    ) -> None:
        self.state_root = state_root
        self.allow_runs = allow_runs
        # The ingest seam: production runs the real one-command engine;
        # tests inject a scripted double that drives the real provider.
        self._ingest = ingest
        self._cancel_join_seconds = cancel_join_seconds
        self._shutdown_join_seconds = shutdown_join_seconds
        self._lock = threading.Lock()
        self._active: _ActiveRun | None = None
        # S8 single-writer slot shared with review writes: a run and a
        # review write never overlap, checked under the same lock.
        self._review_writing = False

    # -- start ---------------------------------------------------------------

    def start(self, url: object, options: object) -> dict[str, Any]:
        """Validate and launch ONE run in a worker thread.

        Guard order: the L3 hold, the single-writer lock (S8), input
        validation (400), then the GPU lease refusal (S8) — the lease
        itself stays authoritative and is never touched.
        """
        if not isinstance(options, dict):
            raise HTTPError(400, "a JSON object body is required")
        with self._lock:
            if not self.allow_runs:
                raise HTTPError(409, RUNS_DISABLED_MESSAGE)
            if self._active is not None:
                raise HTTPError(
                    409,
                    "a run is already active; wait for it to finish or "
                    "cancel it before starting another",
                )
            if self._review_writing:
                raise HTTPError(409, REVIEW_WRITING_MESSAGE)
            payload = self._validate_options(options)
            kind = self._validate_url(payload["url"])
            self._refuse_foreign_gpu_lease()
            run = _ActiveRun(payload["url"], kind, payload)
            run.thread = threading.Thread(
                target=self._worker,
                args=(run,),
                name="ti-web-run",
                daemon=True,  # shutdown() joins bounded; a hard exit is
                # still covered by the startup reconcile.
            )
            self._active = run
            run.thread.start()
        return run.view()

    def _validate_options(self, options: dict[str, Any]) -> dict[str, Any]:
        payload = dict(options)
        unknown = sorted(set(payload) - set(ALLOWED_OPTIONS))
        if unknown:
            raise HTTPError(
                400,
                "unknown option(s) "
                + ", ".join(unknown)
                + "; allowed: " + ", ".join(ALLOWED_OPTIONS),
            )
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HTTPError(400, "url is required")
        if payload.get("limit") is not None:
            limit = payload["limit"]
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise HTTPError(400, "limit must be an integer")
        for name in BOOL_OPTIONS:
            if payload.get(name) is not None and not isinstance(payload[name], bool):
                raise HTTPError(400, f"{name} must be a boolean")
        return payload

    def _validate_url(self, url: str) -> str:
        """The product's own URL dispatch, mirrored from ingest.run_ingest:
        collection first, then canonical video URL, else the same error."""
        url = url.strip()
        try:
            parse_collection_url(url)
            return "collection"
        except CollectionError:
            pass
        try:
            parse_tiktok_url(url)
            return "video"
        except PipelineError as exc:
            raise HTTPError(
                400,
                str(
                    IngestError(
                        f"unsupported TikTok URL: {exc}. Must be a collection "
                        "URL (@author/collection/<name>) or a video URL "
                        "(@author/video/<id>)."
                    )
                ),
            ) from exc

    def _refuse_foreign_gpu_lease(self) -> None:
        marker = gpu_lease.LeaseMarkerFile(self.state_root)
        try:
            owner = marker.live_foreign_owner()
        except StateError as exc:  # unreadable marker: ownership is never guessed
            raise HTTPError(409, f"gpu lease marker unreadable: {exc}") from exc
        if owner is not None:
            raise HTTPError(409, gpu_lease._busy_message(owner))

    # -- worker ---------------------------------------------------------------

    def _worker(self, run: _ActiveRun) -> None:
        assert run.thread is not None
        try:
            exit_code, report = self._ingest(
                run.url,
                state=self.state_root,
                dry_run=bool(run.options.get("dry_run", False)),
                limit=run.options.get("limit"),
                consent=run.provider,
                out=lambda text: self._out(run, text),
                auto_verify=not bool(run.options.get("no_auto_verify", False)),
                rescan=bool(run.options.get("rescan", False)),
            )
        except KeyboardInterrupt:
            # The engine usually converts KeyboardInterrupt into an
            # interrupted report itself; one escaping to here still ends
            # the run honestly.
            run.events.publish(
                TERMINAL_EVENT,
                {
                    "exit_code": 1,
                    "interrupted": True,
                    "error": "interrupted: cancelled from the web interface",
                    "report_path": run.report_path,
                    "summary": None,
                },
            )
        except BaseException as exc:  # IngestError, GuidedError, ... honest 500s
            message = f"{type(exc).__name__}: {exc}"
            run.events.publish("error", {"message": message})
            run.events.publish(
                TERMINAL_EVENT,
                {
                    "exit_code": 2,
                    "interrupted": False,
                    "error": message,
                    "report_path": run.report_path,
                    "summary": None,
                },
            )
        else:
            run.events.publish(
                TERMINAL_EVENT,
                {
                    "exit_code": exit_code,
                    "interrupted": bool(
                        isinstance(report, Mapping)
                        and report.get("interrupted")
                    ),
                    "error": None,
                    "report_path": run.report_path,
                    "summary": _summarize_report(report),
                },
            )
        finally:
            with self._lock:
                if self._active is run:
                    self._active = None

    def _out(self, run: _ActiveRun, text: str) -> None:
        """The run's narration adapter: the exact CLI lines, forwarded to
        the event log; the next call after cancellation raises
        KeyboardInterrupt (cooperative cancel at an engine safe point)."""
        if run.cancel_event.is_set():
            raise KeyboardInterrupt
        if text.startswith(REPORT_SAVED_PREFIX):
            # The product's own stable line naming the saved report.
            run.report_path = text[len(REPORT_SAVED_PREFIX):].strip()
        run.events.publish("line", {"text": text})

    # -- introspection ---------------------------------------------------------

    @contextlib.contextmanager
    def review_write(self, busy_message: str) -> Iterator[None]:
        """Hold the single-writer slot for one review write (S8).

        Refuses with 409 ``busy_message`` while a run is active; while
        held, ``start`` refuses. Review writes serialize among themselves
        through their own lock; this slot only excludes runs.
        """
        with self._lock:
            if self._active is not None:
                raise HTTPError(409, busy_message)
            self._review_writing = True
        try:
            yield
        finally:
            with self._lock:
                self._review_writing = False

    def active_view(self) -> dict[str, Any]:
        with self._lock:
            run = self._active
            return {
                "state": "running" if run is not None else "idle",
                "allow_runs": self.allow_runs,
                "run": run.view() if run is not None else None,
                "pending_prompt": (
                    run.provider.pending_prompt() if run is not None else None
                ),
            }

    def _current_run(self, *, missing_status: int = 409) -> _ActiveRun:
        with self._lock:
            run = self._active
        if run is None:
            # POST endpoints conflict (409); only the events stream uses
            # 404 because there is nothing to stream.
            raise HTTPError(missing_status, "no active run")
        return run

    # -- consent / readiness / cancel -------------------------------------------

    def _with_run(self, action: Callable[[_ActiveRun], Any]) -> Any:
        run = self._current_run()
        try:
            return action(run)
        except PromptRejected as exc:
            raise HTTPError(409, exc.message) from exc

    def answer_consent(self, prompt_id: object, granted: object) -> dict[str, Any]:
        self._require_prompt_id(prompt_id)
        if not isinstance(granted, bool):
            raise HTTPError(400, "granted must be a boolean")
        outcome = self._with_run(
            lambda run: run.provider.answer(prompt_id, granted)
        )
        return {"ok": True, "prompt_id": prompt_id, "outcome": outcome}

    def signal_ready(self, prompt_id: object) -> dict[str, Any]:
        self._require_prompt_id(prompt_id)
        outcome = self._with_run(lambda run: run.provider.signal_ready(prompt_id))
        return {"ok": True, "prompt_id": prompt_id, "outcome": outcome}

    @staticmethod
    def _require_prompt_id(prompt_id: object) -> None:
        if not isinstance(prompt_id, str) or not prompt_id:
            raise HTTPError(400, "prompt_id is required")

    def cancel(self) -> dict[str, Any]:
        run = self._current_run()
        run.mark_cancelled()
        joined = False
        if run.thread is not None:
            run.thread.join(self._cancel_join_seconds)
            joined = not run.thread.is_alive()
        return {"cancelled": True, "joined": joined}

    def shutdown(self) -> None:
        """Process-exit rule: cancel any active run and bounded-join the
        worker so the engine's GPU lease restore/marker logic runs."""
        with self._lock:
            run = self._active
        if run is None:
            return
        run.mark_cancelled()
        if run.thread is not None:
            run.thread.join(self._shutdown_join_seconds)


def _require_object_body(request: Request) -> dict[str, Any]:
    payload = parse_json_body(request)
    if not isinstance(payload, dict):
        raise HTTPError(400, "a JSON object body is required")
    return payload


def register_runs(app: App, runner: WebRunner) -> None:
    """Register the run endpoints on an existing app (no server edits)."""

    def post_run(request: Request) -> Response:
        payload = _require_object_body(request)
        url = payload.get("url")
        view = runner.start(url, payload)
        return json_response(202, {"started": True, "run": view})

    def get_active(request: Request) -> Response:
        return json_response(200, runner.active_view())

    def post_consent(request: Request) -> Response:
        payload = _require_object_body(request)
        return json_response(
            200,
            runner.answer_consent(
                payload.get("prompt_id"), payload.get("granted")
            ),
        )

    def post_ready(request: Request) -> Response:
        payload = _require_object_body(request)
        return json_response(200, runner.signal_ready(payload.get("prompt_id")))

    def post_cancel(request: Request) -> Response:
        _require_object_body(request)
        return json_response(200, runner.cancel())

    def get_events(request: Request) -> Response:
        run = runner._current_run(missing_status=404)  # noqa: SLF001 - route seam
        log = run.events
        # Subscribe NOW (not on first iteration) so nothing published
        # between this request and the first chunk is lost.
        queue = log.subscribe()

        def body() -> Iterable[bytes]:
            try:
                while True:
                    events = log.take(queue, timeout=0.5)
                    for event in events:
                        yield _sse_chunk(event)
                        if event["type"] == TERMINAL_EVENT:
                            return
                    if events:
                        continue
                    # Safety valve: a worker that exited WITHOUT publishing
                    # a terminal event must not hold the stream open
                    # forever. A dead thread has published everything it
                    # ever will, so an empty queue proves there is no tail.
                    if (
                        run.thread is not None
                        and not run.thread.is_alive()
                        and not queue
                    ):
                        return
            finally:
                log.unsubscribe(queue)

        return (
            200,
            {
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-store",
            },
            body(),
        )

    app.route("POST", "/api/runs", post_run)
    app.route("GET", "/api/runs/active", get_active)
    app.route("POST", "/api/runs/active/consent", post_consent)
    app.route("POST", "/api/runs/active/ready", post_ready)
    app.route("POST", "/api/runs/active/cancel", post_cancel)
    app.route("GET", "/api/runs/active/events", get_events)


__all__ = [
    "RUNS_DISABLED_MESSAGE",
    "EventLog",
    "WebRunner",
    "register_runs",
]
