"""Stage engine tests: the S2 execution seam (no TUI, no consent logic).

Focused proofs through the PUBLIC engine interface, per the S2/S4 task:

(a) the engine module imports no TUI and no ``ConsentProvider`` — it is
    terminal-free and decides nothing; the injected adapters do;
(b) a denied ``approve`` adapter stops the phase and produces the same
    report the ``GuidedRun`` driver produces for the same denial;
(c) the ``notify`` adapter receives exactly the narration lines
    ``GuidedRun.out`` received for the same representative run.

The full behaviour pin (windows, ordering, reports, state) remains the
existing guided/collection suites (S4); these tests pin the extraction
seam itself.
"""

from __future__ import annotations

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import engine, guided
from tiktok_ingest.state import StateRoot
from tiktok_ingest.tests.test_guided import (
    FakeStages,
    ScriptedConsent,
    fake_id,
    seed_job,
    seed_scan,
)


def _seed_prepared(state: StateRoot) -> None:
    seed_scan(state, [1])
    seed_job(state, fake_id(1), complete=("fetch", "prepare"))


def _normalized(report: dict[str, Any]) -> str:
    """Report JSON with the run-varying consent timestamps blanked."""
    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    return re.sub(r'"asked_at": "[^"]*"', '"asked_at": "<TS>"', text)


class EngineSeamTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()

    def tearDown(self) -> None:
        self.tmp.cleanup()


class TestEnginePurity(unittest.TestCase):
    def test_engine_module_imports_no_tui_and_no_consent_provider(self) -> None:
        source = Path(engine.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name, "tui",
                        "engine must not import the TUI directly",
                    )
                    self.assertFalse(
                        alias.name.endswith(".tui"),
                        f"engine must not import {alias.name!r}",
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                self.assertNotEqual(
                    module, "tui", "engine must not import the TUI directly"
                )
                self.assertFalse(
                    module.endswith(".tui"),
                    f"engine must not import from {module!r}",
                )
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name,
                        "ConsentProvider",
                        "engine must not import a consent provider",
                    )
        self.assertFalse(hasattr(engine, "ConsentProvider"))
        self.assertFalse(hasattr(engine, "render_consent_window"))


class TestApproveDenial(EngineSeamTestCase):
    def test_denied_approve_stops_execute_with_the_guided_report(self) -> None:
        _seed_prepared(self.state)
        # Driver path: today's public GuidedRun surface.
        runner = guided.GuidedRun(
            self.state,
            stages=FakeStages().functions(),
            consent=ScriptedConsent([("tanda", False)]),
        )
        driver_report = runner.execute(
            guided.select_ids(self.state, [fake_id(1)], limit=5)
        )
        # Engine path: the same denial through the approve adapter.
        approve = ScriptedConsent([("tanda", False)])
        core = engine.StageEngine(
            self.state,
            stages=FakeStages().functions(),
            approve=approve.ask,
            notify=lambda _text: None,
        )
        engine_report = core.execute(
            guided.select_ids(self.state, [fake_id(1)], limit=5)
        )
        self.assertEqual(_normalized(driver_report), _normalized(engine_report))
        self.assertEqual(approve.asked_kinds, ["tanda"])
        self.assertEqual(engine_report["stage_calls"], [])
        self.assertIn(
            "tanda confirmation denied: no stage ran, nothing was written",
            engine_report["notes"],
        )

    def test_denied_approve_stops_collection_phase_with_the_guided_report(
        self,
    ) -> None:
        _seed_prepared(self.state)
        runner = guided.GuidedRun(
            self.state,
            stages=FakeStages().functions(),
            consent=ScriptedConsent([("tanda", False)]),
            out=lambda _text: None,
        )
        driver_report = runner._execute_collection_phase(
            guided.select_ids(self.state, [fake_id(1)], limit=5), "preparation"
        )
        approve = ScriptedConsent([("tanda", False)])
        core = engine.StageEngine(
            self.state,
            stages=FakeStages().functions(),
            approve=approve.ask,
            notify=lambda _text: None,
        )
        engine_report = core.execute_collection_phase(
            guided.select_ids(self.state, [fake_id(1)], limit=5), "preparation"
        )
        self.assertEqual(_normalized(driver_report), _normalized(engine_report))
        self.assertIn(
            "resource slice denied: preparation did not run",
            engine_report["notes"],
        )


class TestNotifyNarration(EngineSeamTestCase):
    def test_notify_receives_the_guided_out_lines_for_one_run(self) -> None:
        _seed_prepared(self.state)
        script = [
            ("tanda", True),
            ("gpu", True),
        ]
        driver_lines: list[str] = []
        runner = guided.GuidedRun(
            self.state,
            stages=FakeStages().functions(),
            consent=ScriptedConsent(list(script)),
            out=driver_lines.append,
        )
        driver_report = runner.execute(
            guided.select_ids(self.state, [fake_id(1)], limit=5)
        )
        engine_lines: list[str] = []
        core = engine.StageEngine(
            self.state,
            stages=FakeStages().functions(),
            approve=ScriptedConsent(list(script)).ask,
            notify=engine_lines.append,
        )
        engine_report = core.execute(
            guided.select_ids(self.state, [fake_id(1)], limit=5)
        )
        self.assertEqual(driver_lines, engine_lines)
        self.assertTrue(engine_lines)  # the representative run narrated
        self.assertEqual(_normalized(driver_report), _normalized(engine_report))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
