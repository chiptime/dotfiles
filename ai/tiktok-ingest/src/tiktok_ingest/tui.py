"""Interactive curses terminal UI for ``guided-run`` and ``collection-run``.

Presentation layer ONLY (stdlib ``curses``, no third-party dependency).
It adds a live dashboard (per-id stage states, current phase/activity and
elapsed time) and renders the EXISTING consent windows as in-place
modals, but it changes no stage semantics, gate, fingerprint, store or
consent rule:

- The provider implements the existing :class:`guided.ConsentProvider`
  contract (``ask`` / ``await_readiness``) plus an ``out`` sink, so it
  drops in exactly where the console provider and narration are used.
- Consent semantics are preserved EXACTLY: only the literal words
  ``y``/``yes`` grant a window; EOF, a read error or a non-interactive
  stdin deny; the WHOLE window text (title, ids, destinations,
  operations, limits, effects) is composed and shown (with scrolling on
  small terminals).
- The TUI draws on the terminal screen (curses). stdout stays reserved
  for the parseable JSON report; the report is printed only after the
  curses session ends.
- Automatic, mandatory fallback: when curses cannot start, or stdin or
  stdout is not a TTY (CI, pipes, ``TERM=dumb``), the caller keeps the
  EXISTING line-based behavior byte-for-byte by receiving a fallback
  session whose ``consent``/``out`` are ``None`` (defaults unchanged).

Nothing here is imported eagerly by non-interactive code paths; the
fallback is decided before any curses call.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import sys
import time
from typing import Any, Callable, Iterable, Iterator

try:  # POSIX: available; guarded so importing the module never explodes.
    import curses
except ImportError:  # pragma: no cover - non-POSIX host
    curses = None  # type: ignore[assignment]

from .guided import GUIDED_STAGES, ConsentProvider, render_consent_window

STAGE_ORDER: tuple[str, ...] = GUIDED_STAGES

_STAGE_SHORT = {
    "fetch": "fetch",
    "prepare": "prep",
    "vision": "vision",
    "audio": "audio",
    "synthesis": "synth",
    "verify": "verify",
}
_STAGE_PHASE = {
    "fetch": "fetching",
    "prepare": "preparing",
    "vision": "vision",
    "audio": "audio",
    "synthesis": "synthesis",
    "verify": "verify",
}

_ENTER = (10, 13)
_BACKSPACE = (8, 127)
_CTRL_C = 3
_EOF = None

_START_RE = re.compile(r"^stage (?P<name>\S+) start: (?P<label>.+)$")
_FINISH_RE = re.compile(
    r"^stage (?P<name>\S+) finish: (?P<label>\S+) (?P<value>\S+) "
    r"\(reused=(?P<reused>True|False|true|false), (?P<seconds>[0-9.]+)s\)$"
)
_RAISED_RE = re.compile(
    r"^stage (?P<name>\S+) finish: (?P<label>\S+) raised (?P<exc>\S+) "
    r"after (?P<seconds>[0-9.]+)s$"
)

_MAX_LOG_LINES = 500


def _normalize_stage(name: str) -> str:
    # The Phase 0 automatic-synthesis narration is tagged ``synthesis-api``
    # but is the ``synthesis`` stage of the canonical order.
    return "synthesis" if name == "synthesis-api" else name


def _fit(text: str, width: int) -> str:
    return text if width <= 0 else text[:width]


@dataclasses.dataclass
class TuiState:
    """Small live state model fed ONLY by the stage narration.

    It is a VIEW: every value is derived from the narration the run
    already emits, never from a second durable ledger. ``started`` is a
    monotonic timestamp used to render the elapsed clock.
    """

    ids: list[str] = dataclasses.field(default_factory=list)
    stages: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    running: dict[str, str] = dataclasses.field(default_factory=dict)
    phase: str = "starting"
    activity: str = ""
    started: float = 0.0
    log: list[str] = dataclasses.field(default_factory=list)

    def ensure_id(self, video_id: str) -> None:
        if video_id not in self.stages:
            self.stages[video_id] = {name: "pending" for name in STAGE_ORDER}
            self.ids.append(video_id)

    def seed_items(self, items: Iterable[tuple[str, dict[str, str]]]) -> None:
        for video_id, stages in items:
            self.ensure_id(video_id)
            for name, value in (stages or {}).items():
                if name in self.stages[video_id]:
                    self.stages[video_id][name] = value

    def start_stage(self, name: str, label: str) -> None:
        self.ensure_id(label)
        self.running[label] = name
        self.phase = _STAGE_PHASE.get(name, name)
        self.activity = f"{name} {label}"

    def finish_stage(
        self, name: str, label: str, value: str, reused: bool, seconds: float
    ) -> None:
        self.ensure_id(label)
        if name in self.stages[label]:
            self.stages[label][name] = value
        if self.running.get(label) == name:
            self.running.pop(label, None)
        self.activity = f"{name} {label} {value}"
        self.note(
            f"{name} {label} -> {value} (reused={reused}, {seconds:.1f}s)"
        )

    def note(self, text: str) -> None:
        for line in str(text).splitlines():
            if line:
                self.log.append(line)
        if len(self.log) > _MAX_LOG_LINES:
            del self.log[: len(self.log) - _MAX_LOG_LINES]

    def elapsed(self, now: float) -> float:
        return max(0.0, now - self.started)


def apply_line(state: TuiState, line: str) -> bool:
    """Fold ONE narration line into the state; return True if it was one.

    Recognized lines are the stage ``start``/``finish``/``raised`` pairs
    emitted by :func:`guided.narrate_stages`. Anything else is a plain
    log line for the caller to note.
    """
    match = _START_RE.match(line)
    if match:
        state.start_stage(_normalize_stage(match.group("name")), match.group("label"))
        return True
    match = _FINISH_RE.match(line)
    if match:
        state.finish_stage(
            _normalize_stage(match.group("name")),
            match.group("label"),
            match.group("value"),
            match.group("reused").lower() == "true",
            float(match.group("seconds")),
        )
        return True
    match = _RAISED_RE.match(line)
    if match:
        state.finish_stage(
            _normalize_stage(match.group("name")),
            match.group("label"),
            "raised",
            False,
            float(match.group("seconds")),
        )
        return True
    return False


def _stage_cell(state: TuiState, video_id: str, stage: str, width: int) -> str:
    if state.running.get(video_id) == stage:
        return "RUN".ljust(width)
    value = state.stages.get(video_id, {}).get(stage, "pending")
    if value == "pending":
        return ".".ljust(width)
    if value == "complete":
        return "ok".ljust(width)
    return value[:width].ljust(width)


def render_lines(
    state: TuiState, *, now: float, width: int = 80, height: int = 24
) -> list[str]:
    """Pure dashboard renderer: a list of lines (testable without curses)."""
    elapsed = state.elapsed(now)
    lines: list[str] = [
        f"TikTok ingest — live run    elapsed {elapsed:5.1f}s",
        f"phase: {state.phase}    activity: {state.activity or '(idle)'}",
        "",
    ]
    id_width = max([len("id")] + [len(video_id) for video_id in state.ids])
    header = "id".ljust(id_width) + "  " + " ".join(
        _STAGE_SHORT[name].ljust(6) for name in STAGE_ORDER
    )
    lines.append(header)
    for video_id in state.ids:
        cells = " ".join(
            _stage_cell(state, video_id, name, 6) for name in STAGE_ORDER
        )
        lines.append(f"{video_id.ljust(id_width)}  {cells}")
    lines.append("")
    lines.append("log:")
    remaining = max(0, height - len(lines))
    for entry in state.log[-remaining:] if remaining else []:
        lines.append(entry)
    if height > 0:
        lines = lines[:height]
    return [_fit(line, width) for line in lines]


def compose_modal_lines(window: Any) -> list[str]:
    """The FULL consent window text, exactly as the console renders it.

    A pure function of the window, so the whole decision text (title,
    ids, destinations, operations, limits, effects) is deterministic and
    testable independently of the screen.
    """
    return render_consent_window(window).splitlines()


class _ReadDenied(Exception):
    """EOF or a read error while reading a consent/readiness answer."""


def _safe_addstr(window: Any, y: int, x: int, text: str) -> None:
    try:
        window.addstr(y, x, text)
    except Exception:  # noqa: BLE001 - drawing must never crash the run
        pass


def _key_to_char(key: Any) -> str | None:
    if isinstance(key, str):
        return key if key.isprintable() else None
    if isinstance(key, int) and 0 <= key < 0x110000:
        char = chr(key)
        return char if char.isprintable() else None
    return None


class CursesConsentProvider(ConsentProvider):
    """Curses-backed ``ConsentProvider`` with a live dashboard and sink.

    ``keys`` is injectable for deterministic tests (an iterator of key
    codes; exhaustion is EOF). When it is ``None`` the real window's
    ``getch`` is used. ``now`` is injectable for a deterministic clock.
    """

    active = True

    def __init__(
        self,
        *,
        window: Any,
        keys: Iterator[Any] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._window = window
        self._keys = keys
        self._now = now or time.monotonic
        self.state = TuiState(started=self._now())
        self._modal_body: list[str] | None = None
        self._modal_prompt = ""
        self._modal_buffer = ""
        self._modal_offset = 0
        self._draw()

    # -- session surface ---------------------------------------------------

    @property
    def consent(self) -> "CursesConsentProvider":
        return self

    @classmethod
    def start(
        cls, *, now: Callable[[], float] | None = None
    ) -> "CursesConsentProvider | None":
        """Initialize curses; ``None`` when it cannot start (-> fallback)."""
        if curses is None:  # pragma: no cover - non-POSIX host
            return None
        try:
            stdscr = curses.initscr()
            try:
                curses.noecho()
                curses.cbreak()
            except Exception:  # noqa: BLE001 - best-effort setup
                pass
            stdscr.keypad(True)
            try:
                curses.curs_set(0)
            except Exception:  # noqa: BLE001 - curs_set is optional
                pass
        except Exception:  # noqa: BLE001 - ANY failure means fallback
            _safe_endwin()
            return None
        return cls(window=stdscr, now=now)

    def close(self) -> None:
        try:
            self._window.keypad(False)
        except Exception:  # noqa: BLE001
            pass
        if curses is not None:
            try:
                curses.nocbreak()
                curses.echo()
            except Exception:  # noqa: BLE001
                pass
        _safe_endwin()

    # -- out sink ----------------------------------------------------------

    def out(self, text: str) -> None:
        """Narration sink: fold stage lines into state, log the rest."""
        for line in str(text).splitlines() or [""]:
            if not apply_line(self.state, line):
                self.state.note(line)
        self._draw()

    # -- ConsentProvider ---------------------------------------------------

    def ask(self, window: Any) -> bool:
        for video_id in getattr(window, "ids", ()) or ():
            self.state.ensure_id(video_id)
        self.state.note(f"consent requested: {window.kind}")
        self._modal_body = compose_modal_lines(window)
        self._modal_prompt = "authorize this window? [yes/no] "
        self._modal_buffer = ""
        self._modal_offset = 0
        self._draw()
        try:
            answer = self._read_answer(scrollable=True)
        except _ReadDenied as exc:
            self._close_modal()
            self.state.note(
                f"refused: could not read authorization ({exc}); nothing "
                "ran and consent is never assumed"
            )
            self._draw()
            return False
        self._close_modal()
        granted = answer.strip().lower() in ("yes", "y")
        if granted:
            self.state.note("granted")
        else:
            self.state.note(
                f'denied: nothing ran for "{window.title}" — stopping '
                "before this window"
            )
        self._draw()
        return granted

    def await_readiness(self, prompt: str) -> bool:
        """Press-Enter readiness checkpoint; NOT a yes/no authorization.

        Any input line (typically just Enter) signals the operator
        finished the manual steps. EOF or a read error refuses: readiness
        is never assumed and carries no authority of its own.
        """
        self.state.note(prompt)
        self._modal_body = [
            prompt,
            "",
            "press Enter to signal readiness (not an authorization)",
        ]
        self._modal_prompt = "[enter] "
        self._modal_buffer = ""
        self._modal_offset = 0
        self._draw()
        try:
            self._read_answer(scrollable=False)
        except _ReadDenied as exc:
            self._close_modal()
            self.state.note(f"refused: could not read readiness ({exc})")
            self._draw()
            return False
        self._close_modal()
        self.state.note("readiness signaled")
        self._draw()
        return True

    # -- input -------------------------------------------------------------

    def _next_key(self) -> Any:
        if self._keys is not None:
            try:
                return next(self._keys)
            except StopIteration:
                return _EOF
            except OSError as exc:
                raise _ReadDenied(str(exc)) from exc
        try:
            key = self._window.getch()
        except Exception as exc:  # noqa: BLE001 - any getch error denies
            raise _ReadDenied(str(exc)) from exc
        return _EOF if key == -1 else key

    def _read_answer(self, *, scrollable: bool) -> str:
        buffer: list[str] = []
        while True:
            key = self._next_key()
            if key is _EOF:
                raise _ReadDenied("end of input")
            if key == _CTRL_C:
                raise KeyboardInterrupt
            if key in _ENTER:
                return "".join(buffer)
            if key in _BACKSPACE:
                if buffer:
                    buffer.pop()
                self._modal_buffer = "".join(buffer)
                self._draw()
                continue
            if scrollable and curses is not None and key in (
                curses.KEY_DOWN,
                curses.KEY_NPAGE,
            ):
                self._scroll(1)
                continue
            if scrollable and curses is not None and key in (
                curses.KEY_UP,
                curses.KEY_PPAGE,
            ):
                self._scroll(-1)
                continue
            char = _key_to_char(key)
            if char is not None:
                buffer.append(char)
                self._modal_buffer = "".join(buffer)
                self._draw()

    def _close_modal(self) -> None:
        self._modal_body = None
        self._modal_prompt = ""
        self._modal_buffer = ""
        self._modal_offset = 0

    def _scroll(self, delta: int) -> None:
        if self._modal_body is None:
            return
        height, _width = self._window.getmaxyx()
        body = self._modal_body + ["", self._modal_prompt + self._modal_buffer]
        max_offset = max(0, len(body) - max(1, height - 1))
        self._modal_offset = min(max(0, self._modal_offset + delta), max_offset)
        self._draw()

    # -- drawing -----------------------------------------------------------

    def _draw(self) -> None:
        window = self._window
        try:
            height, width = window.getmaxyx()
        except Exception:  # noqa: BLE001 - a dead window disables drawing
            return
        try:
            window.erase()
        except Exception:  # noqa: BLE001
            pass
        if self._modal_body is not None:
            lines = self._modal_frame(height, width)
        else:
            lines = render_lines(
                self.state, now=self._now(), width=width, height=height
            )
        for y, line in enumerate(lines[:height]):
            _safe_addstr(window, y, 0, _fit(line, width))
        try:
            window.refresh()
        except Exception:  # noqa: BLE001
            pass

    def _modal_frame(self, height: int, width: int) -> list[str]:
        assert self._modal_body is not None  # narrowing
        body = list(self._modal_body) + [
            "",
            self._modal_prompt + self._modal_buffer,
        ]
        view = max(1, height - 1)
        if len(body) <= height:
            self._modal_offset = 0
            visible = body
            hint = None
        else:
            offset = min(self._modal_offset, max(0, len(body) - view))
            self._modal_offset = offset
            visible = body[offset : offset + view]
            hint = (
                f"-- lines {offset + 1}-{offset + len(visible)}/{len(body)} "
                "(up/down scroll) --"
            )
        result = [_fit(line, width) for line in visible]
        if hint is not None:
            result.append(_fit(hint, width))
        return result


class FallbackSession:
    """The mandatory non-curses fallback: caller keeps its defaults.

    ``consent``/``out`` are ``None`` so the caller passes nothing and the
    EXISTING :class:`guided.ConsoleConsentProvider` and stderr narration
    run unchanged (byte-identical behavior).
    """

    active = False
    consent: ConsentProvider | None = None
    out: Callable[[str], None] | None = None


def ui_supported(
    *,
    stream_in: Any | None = None,
    stream_out: Any | None = None,
    env: Any | None = None,
) -> bool:
    """True only when curses can draw on an interactive terminal."""
    if curses is None:
        return False
    stream_in = stream_in if stream_in is not None else sys.stdin
    stream_out = stream_out if stream_out is not None else sys.stdout
    try:
        if not stream_in.isatty() or not stream_out.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    env = env if env is not None else os.environ
    if env.get("TERM", "") in ("", "dumb"):
        return False
    return True


def _safe_endwin() -> None:
    if curses is None:  # pragma: no cover - non-POSIX host
        return
    try:
        curses.endwin()
    except Exception:  # noqa: BLE001
        pass


@contextlib.contextmanager
def terminal_ui(
    *,
    stream_in: Any | None = None,
    stream_out: Any | None = None,
    env: Any | None = None,
) -> Iterator[Any]:
    """Yield an active TUI session, or the mandatory line-based fallback.

    Detection happens BEFORE any curses call, so a non-TTY or an
    unusable terminal never even attempts to start curses.
    """
    if not ui_supported(stream_in=stream_in, stream_out=stream_out, env=env):
        yield FallbackSession()
        return
    session = CursesConsentProvider.start()
    if session is None:
        yield FallbackSession()
        return
    try:
        yield session
    finally:
        session.close()


__all__ = [
    "CursesConsentProvider",
    "FallbackSession",
    "STAGE_ORDER",
    "TuiState",
    "apply_line",
    "compose_modal_lines",
    "render_lines",
    "terminal_ui",
    "ui_supported",
]
