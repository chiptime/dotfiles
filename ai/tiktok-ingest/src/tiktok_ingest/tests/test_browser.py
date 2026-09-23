"""Browser boundary tests: fake controller + scripted consent, zero browser.

Covers the Phase 0 browser boundary: run-scoped isolated profile
(created and removed), explicit visibility confirmation before any
inventory, denial/EOF paths that capture nothing, no login/captcha
automation (the controller surface has no such capability), reuse of
the existing collection DOM rules (container scope, recommendations
excluded, DOM change = explicit failure), auditable HTML persistence,
and honest close/cleanup reporting on success, error and Ctrl-C.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import browser
from tiktok_ingest.collection import CollectionScanStore, parse_collection_url
from tiktok_ingest.guided import ConsentWindow
from tiktok_ingest.state import InventoryStore, StateRoot

COLLECTION_URL = "https://www.tiktok.com/@fixture_author/collection/live-col"
REF = parse_collection_url(COLLECTION_URL)


def page_html(*, container: bool = True, items: int = 3, outside: int = 2) -> str:
    inner = "".join(
        f'<a href="/@fixture_author/video/{7400000000000000000 + n}">v{n}</a>'
        for n in range(items)
    )
    container_div = (
        f'<div data-testid="collection-container">{inner}</div>'
        if container
        else "<div>nothing here</div>"
    )
    outside_links = "".join(
        f'<a href="https://www.tiktok.com/@fixture_author/video/{7500000000000000000 + n}">rec{n}</a>'
        for n in range(outside)
    )
    return (
        "<!DOCTYPE html><html><body>"
        + container_div
        + f'<div class="recommendations">{outside_links}</div>'
        + "</body></html>"
    )


class FakeController:
    """Browser double with EXACTLY the controller surface: no clicks."""

    def __init__(self, html: str, *, open_raises: BaseException | None = None) -> None:
        self.html = html
        self.open_raises = open_raises
        self.calls: list[tuple[str, ...]] = []
        self.profile_dirs: list[str] = []

    def open(self, url: str, profile_dir: Path) -> None:
        self.calls.append(("open", url, str(profile_dir)))
        self.profile_dirs.append(str(profile_dir))
        if self.open_raises is not None:
            raise self.open_raises

    def current_html(self) -> str:
        self.calls.append(("content",))
        return self.html

    def close(self) -> None:
        self.calls.append(("close",))


class CloseFailsController(FakeController):
    def close(self) -> None:
        self.calls.append(("close",))
        raise browser.BrowserInventoryError("context close failed: fixture")


class ScriptedConsent:
    def __init__(self, script: list[tuple[str, bool]]) -> None:
        self.script = list(script)
        self.windows: list[ConsentWindow] = []

    def ask(self, window: ConsentWindow) -> bool:
        self.windows.append(window)
        expected, granted = self.script.pop(0)
        assert window.kind == expected, f"expected {expected}, got {window.kind}"
        return granted


class BrowserTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state = StateRoot(Path(self.tmp.name) / "state")
        self.state.ensure_layout()
        self.profile_base = Path(self.tmp.name) / "profiles"
        self.profile_base.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_inventory(
        self,
        controller: FakeController,
        consent: ScriptedConsent,
        **kwargs: Any,
    ) -> browser.BrowserInventoryReport:
        return browser.run_browser_inventory(
            state=self.state,
            ref=REF,
            consent=consent,
            out=lambda _t: None,
            controller=controller,
            profile_base=self.profile_base,
            **kwargs,
        )


class TestHappyPath(BrowserTestCase):
    def test_capture_close_cleanup_and_state_persisted(self) -> None:
        controller = FakeController(page_html())
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        report = self.run_inventory(controller, consent)

        self.assertEqual(report.observed_items, 3)
        self.assertEqual(report.out_of_container_count, 2)
        self.assertTrue(report.profile_removed)
        self.assertFalse(list(self.profile_base.iterdir()), "profile dir removed")
        self.assertIn("close", [c[0] for c in controller.calls])
        self.assertTrue(Path(report.audit_html_path).is_file())

        scan = CollectionScanStore(self.state).load(REF.collection_key)
        assert scan is not None
        self.assertEqual(len(scan.items), 3)
        entries = InventoryStore(self.state).load()
        self.assertEqual(
            sorted(e.stable_id for e in entries),
            sorted(item.stable_id for item in scan.items),
        )
        # Inventory-only status honesty: no end evidence -> in_progress.
        self.assertEqual(scan.status, "in_progress")

    def test_profile_is_run_scoped_and_under_base(self) -> None:
        controller = FakeController(page_html())
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        self.run_inventory(controller, consent)
        profile = controller.profile_dirs[0]
        self.assertTrue(profile.startswith(str(self.profile_base)))
        self.assertIn(REF.collection_key, Path(profile).name)

    def test_declared_count_and_end_evidence_are_recorded(self) -> None:
        controller = FakeController(page_html(items=3))
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        self.run_inventory(
            controller, consent, declared_count=3, end_evidence="end of list"
        )
        scan = CollectionScanStore(self.state).load(REF.collection_key)
        assert scan is not None
        self.assertEqual(scan.declared_count, 3)
        self.assertEqual(scan.status, "complete")

    def test_capture_is_merge_only_across_runs(self) -> None:
        first = FakeController(page_html(items=2))
        self.run_inventory(
            first, ScriptedConsent([("browser", True), ("browser-confirm", True)])
        )
        second = FakeController(page_html(items=3))
        self.run_inventory(
            second, ScriptedConsent([("browser", True), ("browser-confirm", True)])
        )
        scan = CollectionScanStore(self.state).load(REF.collection_key)
        assert scan is not None
        self.assertEqual(len(scan.items), 3, "merge-only: items accumulate")


class TestDenials(BrowserTestCase):
    def test_browser_window_denied_opens_and_captures_nothing(self) -> None:
        controller = FakeController(page_html())
        consent = ScriptedConsent([("browser", False)])
        with self.assertRaises(browser.BrowserInventoryError) as ctx:
            self.run_inventory(controller, consent)
        self.assertIn("no browser was opened", str(ctx.exception))
        self.assertEqual(controller.calls, [])
        self.assertFalse(CollectionScanStore(self.state).load(REF.collection_key) is not None)
        self.assertFalse(list(self.profile_base.iterdir()))

    def test_confirmation_denied_captures_nothing_but_cleans_up(self) -> None:
        controller = FakeController(page_html())
        consent = ScriptedConsent([("browser", True), ("browser-confirm", False)])
        with self.assertRaises(browser.BrowserInventoryError) as ctx:
            self.run_inventory(controller, consent)
        self.assertIn("no inventory was captured", str(ctx.exception))
        self.assertIn("close", [c[0] for c in controller.calls])
        self.assertNotIn("content", [c[0] for c in controller.calls])
        self.assertFalse(list(self.profile_base.iterdir()))
        self.assertIsNone(CollectionScanStore(self.state).load(REF.collection_key))

    def test_eof_on_console_provider_never_grants(self) -> None:
        from tiktok_ingest.guided import ConsoleConsentProvider

        controller = FakeController(page_html())
        provider = ConsoleConsentProvider(
            stdin=io.StringIO(""), out=lambda *a, **k: None
        )
        with self.assertRaises(browser.BrowserInventoryError):
            browser.run_browser_inventory(
                state=self.state,
                ref=REF,
                consent=provider,
                out=lambda _t: None,
                controller=controller,
                profile_base=self.profile_base,
            )
        self.assertEqual(controller.calls, [])
        self.assertFalse(list(self.profile_base.iterdir()))


class TestNoAutomation(BrowserTestCase):
    def test_controller_surface_has_no_login_automation(self) -> None:
        # The production controller exposes only open/current_html/close;
        # there is no click/type/fill method to automate login/captcha.
        names = {
            name for name in dir(browser.PlaywrightController)
            if not name.startswith("_")
        }
        self.assertEqual(names, {"open", "current_html", "close"})

    def test_operator_instructions_mention_manual_login(self) -> None:
        controller = FakeController(page_html())
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        messages: list[str] = []
        browser.run_browser_inventory(
            state=self.state,
            ref=REF,
            consent=consent,
            out=messages.append,
            controller=controller,
            profile_base=self.profile_base,
        )
        self.assertTrue(any("login/captcha" in m.lower() for m in messages))


class TestDomAndAudit(BrowserTestCase):
    def test_dom_change_fails_explicitly_and_preserves_audit_html(self) -> None:
        controller = FakeController(page_html(container=False))
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        with self.assertRaises(browser.BrowserInventoryError) as ctx:
            self.run_inventory(controller, consent)
        message = str(ctx.exception)
        self.assertIn("DOM", message)
        self.assertIn("fake empty list", message)
        captures = list(
            (self.state.collections_dir / f"{REF.collection_key}-captures").glob("*.html")
        )
        self.assertEqual(len(captures), 1, "audit HTML preserved for diagnosis")
        self.assertFalse(list(self.profile_base.iterdir()))
        self.assertIsNone(CollectionScanStore(self.state).load(REF.collection_key))

    def test_audit_html_is_the_captured_content(self) -> None:
        html = page_html()
        controller = FakeController(html)
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        report = self.run_inventory(controller, consent)
        self.assertEqual(Path(report.audit_html_path).read_text(encoding="utf-8"), html)


class TestCleanupHonesty(BrowserTestCase):
    def test_close_failure_is_reported_not_hidden(self) -> None:
        controller = CloseFailsController(page_html())
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        report = self.run_inventory(controller, consent)
        self.assertTrue(
            any("context close failed" in note for note in report.cleanup_notes)
        )
        self.assertTrue(report.profile_removed)

    def test_interrupt_closes_and_cleans_up(self) -> None:
        controller = FakeController(
            page_html(), open_raises=KeyboardInterrupt()
        )
        consent = ScriptedConsent([("browser", True)])
        with self.assertRaises(browser.BrowserInventoryError) as ctx:
            self.run_inventory(controller, consent)
        self.assertIn("interrupted", str(ctx.exception))
        self.assertIn("close", [c[0] for c in controller.calls])
        self.assertFalse(list(self.profile_base.iterdir()))

    def test_profile_cleanup_failure_is_reported_not_faked(self) -> None:
        import shutil
        from unittest import mock

        controller = FakeController(page_html())
        consent = ScriptedConsent([("browser", True), ("browser-confirm", True)])
        with mock.patch.object(
            shutil, "rmtree", side_effect=OSError("fixture: disk on fire")
        ):
            report = self.run_inventory(controller, consent)
        self.assertFalse(report.profile_removed)
        self.assertTrue(
            any("disk on fire" in note for note in report.cleanup_notes),
            report.cleanup_notes,
        )
        self.assertTrue(
            any("remove it manually" in note for note in report.cleanup_notes)
        )


class TestPlaywrightPreflight(unittest.TestCase):
    def test_missing_playwright_is_reported_without_install(self) -> None:
        available, detail = browser.playwright_available()
        self.assertIsInstance(available, bool)
        if not available:
            self.assertIn("never installs", detail)

    def test_run_refuses_cleanly_when_playwright_missing(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            state = StateRoot(Path(tmp) / "state")
            state.ensure_layout()
            with mock.patch.object(
                browser,
                "playwright_available",
                return_value=(False, "playwright is not installed (fixture)"),
            ):
                with self.assertRaises(browser.PlaywrightMissingError) as ctx:
                    browser.run_browser_inventory(
                        state=state,
                        ref=REF,
                        consent=ScriptedConsent([]),
                        out=lambda _t: None,
                    )
            self.assertIn("playwright is not installed", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
