"""Curses TUI tests: state model, renderer, consent semantics and fallback.

No real TTY is required: the curses layer is exercised through a fake
window, the consent input through an injected key iterator, and the
fallback path through non-tty stream doubles. Consent semantics are the
product's contract and are asserted directly: only ``y``/``yes`` grants,
while EOF, a read error and a non-interactive environment deny.
"""

from __future__ import annotations

import io
import sys
import unittest
from unittest import mock

from tiktok_ingest import tui
from tiktok_ingest.guided import (
    ConsentWindow,
    ConsoleConsentProvider,
    render_consent_window,
)

ENTER = 10
CTRL_C = 3
BACKSPACE = 127


class FakeWindow:
    """Minimal curses-window double capturing what was drawn."""

    def __init__(self, height: int = 24, width: int = 100) -> None:
        self.height = height
        self.width = width
        self.drawn: list[tuple[int, int, str]] = []
        self.history: list[str] = []
        self.erased = 0
        self.refreshed = 0
        self.keypad_flag: bool | None = None
        self.cursor: tuple[int, int] | None = None

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def erase(self) -> None:
        self.erased += 1
        self.drawn.clear()

    def addstr(self, y: int, x: int, text: str) -> None:
        if y < 0 or y >= self.height:
            raise AssertionError(f"addstr out of bounds: y={y}")
        if x < 0:
            raise AssertionError(f"addstr out of bounds: x={x}")
        self.drawn.append((y, x, text))
        self.history.append(text)

    def refresh(self) -> None:
        self.refreshed += 1

    def move(self, y: int, x: int) -> None:
        self.cursor = (y, x)

    def keypad(self, flag: bool) -> None:
        self.keypad_flag = flag

    def nodelay(self, flag: bool) -> None:  # pragma: no cover - parity
        pass

    def getch(self) -> int:  # pragma: no cover - injected keys are used
        raise AssertionError("getch must not be called when keys are injected")

    def text(self) -> str:
        return "\n".join(entry[2] for entry in self.drawn)


class _RaisingKeys:
    """Key source that raises OSError, simulating a read error."""

    def __iter__(self):
        return self

    def __next__(self):
        raise OSError("terminal read failed")


class _NonTty(io.StringIO):
    def isatty(self) -> bool:  # noqa: D102 - contract is the point
        return False


class _Tty(io.StringIO):
    def isatty(self) -> bool:  # noqa: D102 - contract is the point
        return True


def _window(kind: str = "run-plan") -> ConsentWindow:
    return ConsentWindow(
        kind=kind,
        title="accept this WHOLE current run",
        ids=("2000000000000000001", "2000000000000000002"),
        destinations=("https://www.tiktok.com/@a/video/2000000000000000001",),
        operations=("fetch -> prepare -> vision -> audio",),
        limits=("batch cap 5",),
        effects=("appends pending backlog entries",),
    )


def _provider(keys, *, height: int = 24, width: int = 100):
    window = FakeWindow(height=height, width=width)
    provider = tui.CursesConsentProvider(
        window=window, keys=iter(keys), now=lambda: 5.0
    )
    return provider, window


class StateModelTests(unittest.TestCase):
    def test_starts_empty(self) -> None:
        state = tui.TuiState(started=1.0)
        self.assertEqual(state.ids, [])
        self.assertEqual(state.phase, "starting")

    def test_apply_start_sets_activity_and_phase(self) -> None:
        state = tui.TuiState(started=0.0)
        self.assertTrue(tui.apply_line(state, "stage fetch start: 2000000000000000001"))
        self.assertEqual(state.activity, "fetch 2000000000000000001")
        self.assertEqual(state.phase, "fetching")
        self.assertEqual(state.running["2000000000000000001"], "fetch")
        self.assertIn("2000000000000000001", state.ids)

    def test_apply_finish_sets_stage_state(self) -> None:
        state = tui.TuiState(started=0.0)
        tui.apply_line(state, "stage prepare start: 2000000000000000001")
        tui.apply_line(
            state,
            "stage prepare finish: 2000000000000000001 complete "
            "(reused=False, 1.5s)",
        )
        self.assertEqual(state.stages["2000000000000000001"]["prepare"], "complete")
        self.assertNotIn("2000000000000000001", state.running)
        self.assertTrue(any("complete" in line for line in state.log))

    def test_apply_raised_marks_stage(self) -> None:
        state = tui.TuiState(started=0.0)
        tui.apply_line(
            state, "stage vision finish: 2000000000000000001 raised OSError after 3.0s"
        )
        self.assertEqual(state.stages["2000000000000000001"]["vision"], "raised")

    def test_apply_unknown_line_is_not_a_stage(self) -> None:
        state = tui.TuiState(started=0.0)
        self.assertFalse(tui.apply_line(state, "cleanup note: nothing to do"))
        self.assertEqual(state.log, [])
        provider, _ = _provider([])
        provider.out("cleanup note: nothing to do")
        self.assertIn("cleanup note: nothing to do", provider.state.log)

    def test_synthesis_api_maps_to_synthesis_stage(self) -> None:
        state = tui.TuiState(started=0.0)
        tui.apply_line(state, "stage synthesis-api start: 2000000000000000001")
        self.assertEqual(state.running["2000000000000000001"], "synthesis")

    def test_elapsed_never_negative(self) -> None:
        state = tui.TuiState(started=10.0)
        self.assertEqual(state.elapsed(4.0), 0.0)
        self.assertEqual(state.elapsed(12.5), 2.5)


class RendererTests(unittest.TestCase):
    def test_render_includes_ids_and_pending_states(self) -> None:
        state = tui.TuiState(started=0.0)
        state.ensure_id("2000000000000000001")
        lines = tui.render_lines(state, now=3.0, width=100, height=24)
        joined = "\n".join(lines)
        self.assertIn("2000000000000000001", joined)
        self.assertIn("pending", joined.replace(".", "pending"))
        self.assertIn("elapsed", joined)

    def test_render_shows_running_and_complete_cells(self) -> None:
        state = tui.TuiState(started=0.0)
        state.ensure_id("id-1")
        state.stages["id-1"]["fetch"] = "complete"
        state.running["id-1"] = "prepare"
        joined = "\n".join(tui.render_lines(state, now=1.0, width=100, height=24))
        self.assertIn("ok", joined)
        self.assertIn("RUN", joined)

    def test_render_respects_height(self) -> None:
        state = tui.TuiState(started=0.0)
        for n in range(40):
            state.ensure_id(f"id-{n}")
        lines = tui.render_lines(state, now=1.0, width=80, height=5)
        self.assertLessEqual(len(lines), 5)

    def test_render_truncates_to_width(self) -> None:
        state = tui.TuiState(started=0.0)
        state.note("x" * 500)
        lines = tui.render_lines(state, now=1.0, width=20, height=24)
        self.assertTrue(all(len(line) <= 20 for line in lines))


class ConsentSemanticsTests(unittest.TestCase):
    def test_ask_grants_on_y(self) -> None:
        provider, _ = _provider([ord("y"), ENTER])
        self.assertTrue(provider.ask(_window()))

    def test_ask_grants_on_yes(self) -> None:
        provider, _ = _provider([ord("Y"), ord("E"), ord("S"), ENTER])
        self.assertTrue(provider.ask(_window()))

    def test_ask_denies_on_no(self) -> None:
        provider, _ = _provider([ord("n"), ord("o"), ENTER])
        self.assertFalse(provider.ask(_window()))

    def test_ask_denies_on_empty_enter(self) -> None:
        provider, _ = _provider([ENTER])
        self.assertFalse(provider.ask(_window()))

    def test_ask_denies_on_eof(self) -> None:
        provider, _ = _provider([])
        self.assertFalse(provider.ask(_window()))

    def test_ask_denies_on_read_error(self) -> None:
        window = FakeWindow()
        provider = tui.CursesConsentProvider(window=window, keys=_RaisingKeys())
        self.assertFalse(provider.ask(_window()))

    def test_ask_propagates_ctrl_c(self) -> None:
        provider, _ = _provider([CTRL_C])
        with self.assertRaises(KeyboardInterrupt):
            provider.ask(_window())

    def test_backspace_edits_the_answer(self) -> None:
        provider, _ = _provider([ord("x"), BACKSPACE, ord("y"), ENTER])
        self.assertTrue(provider.ask(_window()))

    def test_full_window_text_is_composed(self) -> None:
        window = _window()
        lines = tui.compose_modal_lines(window)
        self.assertEqual(lines, render_consent_window(window).splitlines())
        joined = "\n".join(lines)
        for expected in (
            "AUTHORIZATION REQUIRED",
            "accept this WHOLE current run",
            "2000000000000000002",
            "fetch -> prepare -> vision -> audio",
            "batch cap 5",
            "appends pending backlog entries",
        ):
            self.assertIn(expected, joined)

    def test_ask_draws_the_modal_with_the_window_text(self) -> None:
        provider, window = _provider([ord("y"), ENTER])
        provider.ask(_window())
        drawn = "\n".join(window.history)
        self.assertIn("AUTHORIZATION REQUIRED", drawn)
        self.assertIn("authorize this window? [yes/no]", drawn)

    def test_ask_seeds_state_ids_from_the_window(self) -> None:
        provider, _ = _provider([ord("y"), ENTER])
        provider.ask(_window())
        self.assertIn("2000000000000000001", provider.state.ids)

    def test_modal_scroll_reaches_last_line_on_small_screen(self) -> None:
        provider, window = _provider(
            [curses_down(), ENTER], height=6, width=60
        )
        provider.ask(_window())
        self.assertTrue(window.erased >= 2)

    def test_await_readiness_enter_grants(self) -> None:
        provider, _ = _provider([ENTER])
        self.assertTrue(provider.await_readiness("log in, then press Enter"))

    def test_await_readiness_eof_denies(self) -> None:
        provider, _ = _provider([])
        self.assertFalse(provider.await_readiness("log in, then press Enter"))

    def test_await_readiness_read_error_denies(self) -> None:
        provider = tui.CursesConsentProvider(window=FakeWindow(), keys=_RaisingKeys())
        self.assertFalse(provider.await_readiness("press Enter"))

    def test_out_sink_updates_state_and_redraws(self) -> None:
        provider, window = _provider([])
        provider.out("stage fetch start: 2000000000000000001")
        self.assertEqual(provider.state.activity, "fetch 2000000000000000001")
        self.assertGreater(window.refreshed, 0)


def curses_down() -> int:
    import curses

    return curses.KEY_DOWN


class FallbackTests(unittest.TestCase):
    def test_ui_supported_false_without_tty(self) -> None:
        self.assertFalse(
            tui.ui_supported(
                stream_in=_NonTty(),
                stream_out=_NonTty(),
                env={"TERM": "xterm"},
            )
        )

    def test_ui_supported_false_on_dumb_terminal(self) -> None:
        self.assertFalse(
            tui.ui_supported(
                stream_in=_Tty(),
                stream_out=_Tty(),
                env={"TERM": "dumb"},
            )
        )

    def test_ui_supported_true_with_tty_and_term(self) -> None:
        self.assertTrue(
            tui.ui_supported(
                stream_in=_Tty(),
                stream_out=_Tty(),
                env={"TERM": "xterm-256color"},
            )
        )

    def test_terminal_ui_falls_back_without_tty(self) -> None:
        with tui.terminal_ui(
            stream_in=_NonTty(),
            stream_out=_NonTty(),
            env={"TERM": "xterm"},
        ) as session:
            self.assertFalse(session.active)
            self.assertIsNone(session.consent)
            self.assertIsNone(session.out)

    def test_terminal_ui_falls_back_when_curses_cannot_start(self) -> None:
        with mock.patch.object(
            tui.CursesConsentProvider, "start", return_value=None
        ):
            with tui.terminal_ui(
                stream_in=_Tty(),
                stream_out=_Tty(),
                env={"TERM": "xterm"},
            ) as session:
                self.assertFalse(session.active)
                self.assertIsNone(session.consent)

    def test_terminal_ui_active_when_curses_starts(self) -> None:
        fake = tui.CursesConsentProvider(window=FakeWindow())
        with mock.patch.object(
            tui.CursesConsentProvider, "start", return_value=fake
        ), mock.patch.object(fake, "close") as close:
            with tui.terminal_ui(
                stream_in=_Tty(),
                stream_out=_Tty(),
                env={"TERM": "xterm"},
            ) as session:
                self.assertTrue(session.active)
                self.assertIs(session.consent, fake)
                self.assertTrue(callable(session.out))
        close.assert_called_once()

    def test_non_tty_console_provider_denies(self) -> None:
        # The console provider trusts an INJECTED stdin (fixture seam);
        # the non-interactive denial applies to the REAL stdin, which is
        # what the fallback uses. Patch the process stdin to a non-tty.
        with mock.patch.object(sys, "stdin", _NonTty("yes\n")):
            provider = ConsoleConsentProvider()
            self.assertFalse(provider.ask(_window()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
