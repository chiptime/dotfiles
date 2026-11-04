"""The web consent provider (S6): browser modal authorization prompts.

The provider adapts the product's consent boundary to the Phase 1 web
interface. It changes NO authority semantics — every rule of
:mod:`tiktok_ingest.guided` holds unchanged:

- ``ask`` blocks the run until the browser answers that ONE prompt id
  (Authorize / Deny); it returns True ONLY on an explicit grant.
- ``await_readiness`` is the browser-inventory press-Enter checkpoint,
  NOT an authorization: only the ``ready`` action resolves it.
- Answers are non-persistent and single-shot: they apply to exactly one
  prompt id of the current run. Unknown, stale and duplicate answers are
  rejected (:class:`PromptRejected`), and a consent answer can never
  resolve a readiness prompt (nor the reverse).
- Cancellation unblocks the operator fail-closed: the pending prompt is
  resolved WITHOUT ever granting, and the blocked worker sees
  ``KeyboardInterrupt`` — which the engine already treats as an
  interruption — instead of a quiet deny that would mislabel runs that
  already had effects as "nothing ran".

The prompt document carries the ``ConsentWindow`` fields EXACTLY as
``ConsentWindow.to_dict()`` / ``render_consent_window`` produce them, so
the modal shows byte-identical text to the CLI/TUI. No prompt state is
persisted: it lives only for the run that opened it.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from ..guided import ConsentProvider, ConsentWindow, render_consent_window

# The event published when a prompt opens/resolves; the payload shape is
# part of the SSE contract consumed by the frontend.
CONSENT_EVENT = "consent"
CONSENT_RESOLVED_EVENT = "consent-resolved"

PublishFn = Callable[[str, dict[str, Any]], None]


class PromptRejected(Exception):
    """An answer the current prompt state cannot accept (409 upstream).

    Carries an operator-readable reason; never a stack trace or internal
    state.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _Prompt:
    """One open prompt: the id, its view document and its resolution."""

    __slots__ = ("id", "kind", "view", "event", "outcome")

    def __init__(self, prompt_id: str, kind: str, view: dict[str, Any]) -> None:
        self.id = prompt_id
        self.kind = kind  # "consent" | "readiness"
        self.view = view
        self.event = threading.Event()
        self.outcome: str | None = None  # granted|denied|ready|cancelled


class WebConsentProvider(ConsentProvider):
    """One run's consent prompts, answered through the web endpoints."""

    def __init__(self, publish: PublishFn) -> None:
        self._publish = publish
        self._lock = threading.Lock()
        self._pending: _Prompt | None = None
        self._answered: dict[str, str] = {}
        self._cancelled = False

    # -- ConsentProvider interface -----------------------------------------

    def ask(self, window: ConsentWindow) -> bool:
        view = {
            "id": uuid.uuid4().hex[:12],
            "kind": "consent",
            "window": window.to_dict(),
            "rendered": render_consent_window(window),
        }
        prompt = self._open(view)
        return self._await(prompt) == "granted"

    def await_readiness(self, prompt: str) -> bool:
        view = {
            "id": uuid.uuid4().hex[:12],
            "kind": "readiness",
            "prompt": prompt,
            "rendered": prompt,
        }
        prompt_record = self._open(view)
        return self._await(prompt_record) == "ready"

    # -- Web-facing surface (driven by the runner endpoints) ----------------

    def pending_prompt(self) -> dict[str, Any] | None:
        """The view document of the one open prompt, if any."""
        with self._lock:
            return dict(self._pending.view) if self._pending else None

    def answer(self, prompt_id: str, granted: bool) -> str:
        """Resolve the pending CONSENT prompt with an explicit answer."""
        outcome = "granted" if granted else "denied"
        self._settle(prompt_id, expected_kind="consent", outcome=outcome)
        return outcome

    def signal_ready(self, prompt_id: str) -> str:
        """Resolve the pending READINESS prompt (not an authorization)."""
        self._settle(prompt_id, expected_kind="readiness", outcome="ready")
        return "ready"

    def cancel_pending(self) -> None:
        """Fail-closed unblock for cancellation: never grants, wakes the
        worker with ``KeyboardInterrupt`` semantics."""
        with self._lock:
            self._cancelled = True
            prompt = self._pending
        if prompt is not None:
            self._finish(prompt, "cancelled")

    # -- internals ----------------------------------------------------------

    def _open(self, view: dict[str, Any]) -> _Prompt:
        prompt = _Prompt(view["id"], view["kind"], view)
        with self._lock:
            if self._cancelled:
                # The run was cancelled before this window opened: fail
                # closed without ever showing a new prompt.
                raise KeyboardInterrupt
            if self._pending is not None:  # single worker thread: impossible
                raise RuntimeError(
                    "consent provider asked a second window while one "
                    "prompt is pending"
                )
            self._pending = prompt
        self._publish(CONSENT_EVENT, {"prompt": dict(view)})
        return prompt

    def _await(self, prompt: _Prompt) -> str:
        # Unbounded by design: a human decision has no timeout. The only
        # exits are an answer through the endpoints or cancellation.
        prompt.event.wait()
        with self._lock:
            if self._pending is prompt:
                self._pending = None
            outcome = prompt.outcome or "cancelled"
            self._answered[prompt.id] = outcome
        if outcome == "cancelled":
            raise KeyboardInterrupt
        return outcome

    def _settle(self, prompt_id: str, *, expected_kind: str, outcome: str) -> None:
        """Validate and resolve one prompt ATOMICALLY (single lock scope),
        so a duplicate answer racing the first one is always rejected."""
        if not isinstance(prompt_id, str) or not prompt_id:
            raise PromptRejected("prompt_id is required")
        with self._lock:
            already = self._answered.get(prompt_id)
            pending = self._pending
            if already is not None or (
                pending is not None and pending.outcome is not None
            ):
                raise PromptRejected(
                    f"prompt {prompt_id} was already answered ({already or pending.outcome}); "
                    "answers are not persistent and apply to one prompt only"
                )
            if pending is None:
                raise PromptRejected("no pending prompt to answer")
            if pending.id != prompt_id:
                raise PromptRejected(
                    f"unknown prompt id {prompt_id!r}: it is not the pending "
                    "prompt of the current run (stale answers are rejected)"
                )
            if pending.kind != expected_kind:
                # Point the operator at the action that FITS THE PROMPT
                # (the one just tried is the mismatched one).
                action = (
                    "an authorize/deny answer"
                    if pending.kind == "consent"
                    else "the ready action"
                )
                raise PromptRejected(
                    f"prompt {prompt_id} is a {pending.kind} prompt; answer it "
                    f"with {action} instead"
                )
            pending.outcome = outcome
            self._answered[prompt_id] = outcome
            self._pending = None
        payload: dict[str, Any] = {
            "prompt_id": prompt_id,
            "outcome": outcome,
        }
        if pending.kind == "consent":
            payload["granted"] = outcome == "granted"
        self._publish(CONSENT_RESOLVED_EVENT, payload)
        pending.event.set()

    def _finish(self, prompt: _Prompt, outcome: str) -> None:
        with self._lock:
            if prompt.outcome is not None:
                return  # an explicit answer won the race; keep it
            if self._pending is prompt:
                self._pending = None
            prompt.outcome = outcome
            self._answered[prompt.id] = outcome
        payload: dict[str, Any] = {
            "prompt_id": prompt.id,
            "outcome": outcome,
        }
        if prompt.kind == "consent":
            payload["granted"] = outcome == "granted"
        self._publish(CONSENT_RESOLVED_EVENT, payload)
        prompt.event.set()


__all__ = [
    "CONSENT_EVENT",
    "CONSENT_RESOLVED_EVENT",
    "PromptRejected",
    "WebConsentProvider",
]
