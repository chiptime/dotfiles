"""Structural rules for the static SPA (T5: S6 + S7).

The frontend is plain HTML/CSS/JS served under a strict CSP
(``default-src 'self'``), so its safety properties are structural and
can be pinned without a JS runtime: no inline script/style/handlers,
no HTML-injection sinks in app.js, ONE POST helper carrying the
per-start token, a consent modal with only Authorize/Deny (no close
control), and every ``/api/...`` path literal used by the client
resolving against a real app wired exactly like ``run_web`` (a dummy
runner: launching stays disabled, nothing ever runs).

Scratch state stays in tempfile directories outside the worktree.
"""

from __future__ import annotations

import contextlib
import http.client
import re
import tempfile
import threading
import unittest
from pathlib import Path

from tiktok_ingest.state import StateRoot
from tiktok_ingest.web.review_api import register_review
from tiktok_ingest.web.runner import WebRunner, register_runs
from tiktok_ingest.web.server import create_server

STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"

# Every route the SPA is allowed to call (single source of truth in
# app.js PATHS). Each must resolve on a run_web-shaped app.
EXPECTED_PATHS = {
    "/api/health",
    "/api/collections",
    "/api/collections/{key}",
    "/api/runs",
    "/api/runs/{key}/{report}",
    "/api/runs/active",
    "/api/runs/active/consent",
    "/api/runs/active/ready",
    "/api/runs/active/cancel",
    "/api/runs/active/events",
    "/api/blocks",
    "/api/videos/{video_id}",
    "/api/videos/{video_id}/evidence/{name}",
    "/api/backlog",
    "/api/backlog/{video_id}",
    "/api/backlog/{video_id}/decision",
    "/api/backlog/plan",
    "/api/backlog/apply",
}

# Sinks that would turn server data into markup (or execute strings).
FORBIDDEN_IN_APP_JS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
)


def _idle_ingest(*args: object, **kwargs: object) -> tuple[int, dict[str, object]]:
    raise AssertionError("the dummy runner must never start a run")


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


def build_like_run_web(state_root: StateRoot):
    """A real loopback server with ALL routes run_web registers.

    The runner is the real WebRunner with a dummy ingest seam and
    ``allow_runs=False`` (the phase hold): POST /api/runs answers 409
    and no thread is ever started.
    """
    server = create_server(state_root, port=0)
    runner = WebRunner(state_root, allow_runs=False, ingest=_idle_ingest)
    register_runs(server.app, runner)
    register_review(server.app, runner)
    return server


class StaticHarness(unittest.TestCase):
    """Real server on an ephemeral port serving the real static files."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = StateRoot(Path(self._tmp.name) / "state")
        self.server = self.enterContext(serving(build_like_run_web(self.state_root)))
        self.port = self.server.server_port

    def request(
        self, method: str, path: str
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()


class IndexStructureTests(StaticHarness):
    """CSP-shaped index.html: external assets only, token via meta."""

    def test_index_serves_token_meta_and_external_assets(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        lower = {key.lower(): value for key, value in headers.items()}
        self.assertIn("default-src 'self'", lower["content-security-policy"])
        text = body.decode("utf-8")
        token = self.server.app.state.token
        self.assertIn(f'name="ti-token" content="{token}"', text)
        self.assertIn('href="/static/app.css"', text)
        self.assertIn('src="/static/app.js"', text)

    def test_index_has_no_inline_script_style_or_event_handlers(self) -> None:
        _, _, body = self.request("GET", "/")
        text = body.decode("utf-8")
        for tag in re.findall(r"<script\b[^>]*>", text, re.IGNORECASE):
            self.assertIn("src=", tag, f"inline script is blocked by CSP: {tag}")
        self.assertNotRegex(text, r"<style\b")
        self.assertNotRegex(text, r"\sstyle\s*=", msg="inline style attributes")
        self.assertNotRegex(
            text,
            r"\son[a-z]+\s*=",
            msg="on* handler attributes are blocked by CSP",
        )


class AppJsSafetyTests(unittest.TestCase):
    """app.js source rules: text-only rendering, one token POST helper."""

    def setUp(self) -> None:
        self.source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_no_html_injection_or_string_execution_sinks(self) -> None:
        for token in FORBIDDEN_IN_APP_JS:
            self.assertNotIn(token, self.source, f"forbidden sink: {token}")

    def test_every_post_goes_through_the_single_token_helper(self) -> None:
        # One fetch wrapper; the ONE method:"POST" occurrence sits in the
        # ONE helper that also sets the token header.
        self.assertEqual(self.source.count("fetch("), 1)
        self.assertEqual(self.source.count('"POST"'), 1)
        self.assertEqual(self.source.count("X-TI-Token"), 1)
        self.assertNotIn("XMLHttpRequest", self.source)
        self.assertNotIn("sendBeacon", self.source)
        post_at = self.source.index('"POST"')
        token_at = self.source.index("X-TI-Token")
        helper_start = self.source.rindex("function postJSON", 0, post_at)
        self.assertLess(helper_start, post_at, "POST must live inside postJSON")
        self.assertGreater(
            token_at, helper_start, "the token header must be set by postJSON"
        )

    def test_one_sse_stream_constructor(self) -> None:
        self.assertEqual(self.source.count("EventSource("), 1)

    def test_token_is_never_logged(self) -> None:
        self.assertNotIn("console.", self.source)


class ConsentModalTests(StaticHarness):
    """The S6 modal: exactly Authorize/Deny (plus Ready for readiness),
    no close control of any kind."""

    def test_modal_has_authorize_deny_ready_and_no_close_control(self) -> None:
        _, _, body = self.request("GET", "/")
        text = body.decode("utf-8")
        self.assertIn('id="consent-modal"', text)
        self.assertIn('role="dialog"', text)
        self.assertIn('aria-modal="true"', text)
        self.assertIn('id="consent-authorize"', text)
        self.assertIn(">Authorize</button>", text)
        self.assertIn('id="consent-deny"', text)
        self.assertIn(">Deny</button>", text)
        self.assertIn('id="consent-ready"', text)
        self.assertIn(">Ready</button>", text)
        # No close affordance: no glyph, no close-named control.
        self.assertNotIn("×", text)
        self.assertNotIn("&times;", text)
        self.assertNotIn("close", text.lower())

    def test_status_regions_are_live_regions(self) -> None:
        _, _, body = self.request("GET", "/")
        text = body.decode("utf-8")
        narration = re.search(r"<[^>]*id=\"narration\"[^>]*>", text)
        self.assertIsNotNone(narration, "narration log must exist")
        self.assertIn("aria-live", narration.group(0))
        self.assertIn('role="status"', text)


class EndpointCoverageTests(StaticHarness):
    """Every /api path literal in app.js exists on the run_web app."""

    def setUp(self) -> None:
        super().setUp()
        self.app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def _paths_in_app_js(self) -> set[str]:
        return set(re.findall(r'"(/api/[^"]*)"', self.app_source))

    def test_all_declared_paths_cover_the_contract(self) -> None:
        found = self._paths_in_app_js()
        missing = EXPECTED_PATHS - found
        self.assertEqual(missing, set(), f"paths missing from app.js: {missing}")

    def test_every_path_literal_resolves_on_a_real_app(self) -> None:
        app = self.server.app
        for template in sorted(self._paths_in_app_js()):
            with self.subTest(path=template):
                path = re.sub(r"\{[a-z_]+\}", "x", template)
                resolved = app.resolve("GET", path) or app.resolve("POST", path)
                self.assertIsNotNone(
                    resolved, f"app.js calls a route that does not exist: {path}"
                )

    def test_views_are_reachable_on_the_built_app(self) -> None:
        status, _, body = self.request("GET", "/api/collections")
        self.assertEqual(status, 200)
        self.assertEqual(__import__("json").loads(body), {"collections": []})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
