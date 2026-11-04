"""Loopback web server rules (S2/S3/S4): binding, token, Host/Origin, static safety.

The REAL ThreadingHTTPServer runs on an ephemeral port in a thread and is
driven through http.client: one test per rule. No real browser
(webbrowser.open is patched); scratch state stays in tempfile directories
outside the worktree.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tiktok_ingest import __version__
from tiktok_ingest.state import StateRoot
from tiktok_ingest.web.server import (
    HTTPError,
    ServerState,
    WebServerError,
    create_app,
    create_server,
    json_response,
    run_web,
)


@contextlib.contextmanager
def serving(server):
    """Run a created server until the block exits, then always shut it down."""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


class ServerHarness(unittest.TestCase):
    """Real server on an ephemeral port + one-request helper."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = StateRoot(Path(self._tmp.name) / "state")
        self.server = self.enterContext(serving(create_server(self.state_root, port=0)))
        self.port = self.server.server_port
        self.token = self.server.app.state.token

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()


class CreateServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = StateRoot(Path(self._tmp.name) / "state")

    def test_binding_refuses_non_loopback_hosts(self) -> None:
        for host in ("0.0.0.0", "localhost", "::1", "192.168.1.5", "example.com"):
            with self.subTest(host=host):
                with self.assertRaises(WebServerError):
                    create_server(self.state_root, port=0, host=host)

    def test_busy_port_fails_loudly_without_fallback(self) -> None:
        first = create_server(self.state_root, port=0)
        self.addCleanup(first.server_close)
        with self.assertRaises(WebServerError) as ctx:
            create_server(self.state_root, port=first.server_port)
        message = str(ctx.exception)
        self.assertIn("cannot bind", message)
        self.assertIn(str(first.server_port), message)


class IndexTests(ServerHarness):
    def test_get_index_serves_token_meta_and_security_headers(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        lower = {key.lower(): value for key, value in headers.items()}
        self.assertIn("text/html", lower["content-type"])
        # The token reaches the page ONLY through the meta tag (CSP forbids
        # inline scripts, so JS reads it from there).
        self.assertIn(f'name="ti-token" content="{self.token}"', body.decode())
        self.assertEqual(lower["cache-control"], "no-store")
        self.assertEqual(lower["x-content-type-options"], "nosniff")
        self.assertEqual(lower["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", lower["content-security-policy"])
        self.assertEqual(lower["referrer-policy"], "no-referrer")

    def test_head_index_returns_headers_without_body(self) -> None:
        status, headers, body = self.request("HEAD", "/")
        self.assertEqual(status, 200)
        lower = {key.lower(): value for key, value in headers.items()}
        self.assertGreater(int(lower["content-length"]), 0)
        self.assertEqual(body, b"")


class TokenTests(ServerHarness):
    def test_post_without_token_is_forbidden(self) -> None:
        status, _, body = self.request("POST", "/api/health")
        self.assertEqual(status, 403)
        self.assertNotIn(self.token.encode(), body)

    def test_post_with_wrong_token_is_forbidden(self) -> None:
        status, _, body = self.request(
            "POST", "/api/health", headers={"X-TI-Token": "bogus-token"}
        )
        self.assertEqual(status, 403)
        self.assertNotIn(self.token.encode(), body)

    def test_post_with_valid_token_reaches_handler(self) -> None:
        self.server.app.route(
            "POST",
            "/api/__echo",
            lambda request: json_response(200, {"authenticated": True}),
        )
        status, _, body = self.request(
            "POST", "/api/__echo", headers={"X-TI-Token": self.token}
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["authenticated"])


class HostOriginTests(ServerHarness):
    def test_bad_host_is_rejected(self) -> None:
        status, _, _ = self.request(
            "GET", "/", headers={"Host": f"evil.example:{self.port}"}
        )
        self.assertEqual(status, 421)

    def test_localhost_host_is_accepted(self) -> None:
        status, _, _ = self.request(
            "GET", "/", headers={"Host": f"localhost:{self.port}"}
        )
        self.assertEqual(status, 200)

    def test_foreign_origin_is_rejected(self) -> None:
        status, _, _ = self.request(
            "GET", "/", headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(status, 403)

    def test_same_origin_header_is_accepted(self) -> None:
        status, _, _ = self.request(
            "GET", "/", headers={"Origin": f"http://127.0.0.1:{self.port}"}
        )
        self.assertEqual(status, 200)


class StaticSafetyTests(ServerHarness):
    def test_static_app_js_is_served_as_javascript(self) -> None:
        status, headers, body = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        lower = {key.lower(): value for key, value in headers.items()}
        self.assertIn("text/javascript", lower["content-type"])
        self.assertIn(b"/api/health", body)  # placeholder client really polls

    def test_traversal_variants_are_404(self) -> None:
        variants = [
            "/static/../server.py",
            "/static/..%2Fserver.py",
            "/static/%2e%2e/server.py",
            "/static/%2E%2E%2F%2E%2E%2Fsrc%2Ftiktok_ingest%2Fcli.py",
            "/static/./app.js",
            "/static//app.js",
            "/static",
            "/static/",
            "/static/missing.js",
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                status, _, body = self.request("GET", variant)
                self.assertEqual(status, 404, variant)
                self.assertNotIn(self.token.encode(), body)

    def test_symlink_escape_is_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            static = Path(tmp) / "static"
            static.mkdir()
            secret = Path(tmp) / "secret.txt"
            secret.write_text("secret", encoding="utf-8")
            (static / "index.html").write_text(
                '<meta name="ti-token" content="__TI_TOKEN__">', encoding="utf-8"
            )
            (static / "leak.txt").symlink_to(secret)
            state_root = StateRoot(Path(tmp) / "state")
            server = self.enterContext(
                serving(create_server(state_root, port=0, static_dir=static))
            )
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                conn.request("GET", "/static/leak.txt")
                response = conn.getresponse()
                status, body = response.status, response.read()
            finally:
                conn.close()
        self.assertEqual(status, 404)
        self.assertNotIn(b"secret", body)


class ApiTests(ServerHarness):
    def test_api_health_returns_ok_and_version(self) -> None:
        status, _, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIs(data["ok"], True)
        self.assertEqual(data["version"], __version__)


class SafetyTests(ServerHarness):
    def test_error_bodies_never_contain_the_token(self) -> None:
        def boom(request) -> object:
            raise RuntimeError("boom " + request.state.token)

        self.server.app.route("GET", "/api/__boom", boom)
        cases = [
            ("POST", "/api/health", {"X-TI-Token": "wrong"}),
            ("GET", "/", {"Origin": "http://evil.example"}),
            ("GET", "/static/missing.js", None),
            ("GET", "/api/__boom", None),
        ]
        for method, path, headers in cases:
            with self.subTest(path=path):
                status, _, body = self.request(method, path, headers=headers)
                self.assertGreaterEqual(status, 400)
                self.assertNotIn(self.token.encode(), body)

    def test_access_log_strips_query_strings(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status, _, _ = self.request("GET", "/api/health?leak=SECRETVALUE")
            self.assertEqual(status, 200)
        logged = stderr.getvalue()
        self.assertIn("/api/health", logged)
        self.assertNotIn("SECRETVALUE", logged)


class RunWebWiringTests(unittest.TestCase):
    def test_run_web_serves_views_and_holds_run_launching(self) -> None:
        from unittest import mock

        from tiktok_ingest.web import server as server_module

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen: dict[str, object] = {}

        def fake_serve_forever(self_server, *args, **kwargs):
            app = self_server.app
            seen["collections"] = app.resolve("GET", "/api/collections") is not None
            seen["plan"] = app.resolve("GET", "/api/backlog/plan") is not None
            seen["events"] = app.resolve("GET", "/api/runs/active/events")[0]
            handler, _params = app.resolve("POST", "/api/runs")
            request = server_module.Request(
                method="POST",
                path="/api/runs",
                query={},
                headers={},
                body=b'{"url": "https://www.tiktok.com/@a/video/1"}',
                params={},
                state=app.state,
            )
            try:
                seen["start"] = handler(request)
            except server_module.HTTPError as exc:
                seen["start"] = exc
            raise KeyboardInterrupt

        with mock.patch.object(
            server_module.LoopbackServer, "serve_forever", fake_serve_forever
        ), mock.patch("builtins.print"):
            code = server_module.run_web(
                StateRoot(Path(tmp.name) / "state"), port=0, open_browser=False
            )
        self.assertEqual(code, 0)
        self.assertTrue(seen["collections"])
        self.assertTrue(seen["plan"])
        self.assertIn("runner", seen["events"].__module__ + repr(seen["events"]))
        start = seen["start"]
        status = start.status if isinstance(start, server_module.HTTPError) else start[0]
        self.assertEqual(status, 409)


class RouterApiTests(unittest.TestCase):
    """Pin the exact router contract later units register endpoints with."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = ServerState(
            state_root=StateRoot(Path(self._tmp.name) / "state"), token="t"
        )
        self.app = create_app(self.state)

    def test_route_pattern_captures_params(self) -> None:
        captured: dict[str, str] = {}

        def handler(request) -> tuple[int, dict[str, str], bytes]:
            captured.update(request.params)
            return json_response(200, {"ok": True})

        self.app.route("GET", "/api/collections/{key}/items", handler)
        _, params = self.app.resolve("GET", "/api/collections/abc123/items")
        self.assertEqual(params, {"key": "abc123"})
        handler(params and type("R", (), {"params": params, "state": self.state})())
        self.assertEqual(captured, {"key": "abc123"})

    def test_literal_route_wins_over_a_parameter_route_registered_first(self) -> None:
        def generic(request):
            return json_response(200, {"route": "generic"})

        def literal(request):
            return json_response(200, {"route": "literal"})

        self.app.route("GET", "/api/x/{key}/{name}", generic)
        self.app.route("GET", "/api/x/active/events", literal)
        handler, params = self.app.resolve("GET", "/api/x/active/events")
        self.assertIs(handler, literal)
        self.assertEqual(params, {})
        handler, params = self.app.resolve("GET", "/api/x/k1/r1")
        self.assertIs(handler, generic)
        self.assertEqual(params, {"key": "k1", "name": "r1"})

    def test_http_error_maps_to_status_and_message(self) -> None:
        from tiktok_ingest.cli import main  # noqa: F401  (router import stays lazy)

        def busy(request) -> tuple[int, dict[str, str], bytes]:
            raise HTTPError(409, "another writer is active")

        self.app.route("POST", "/api/__busy", busy)
        with serving(create_server(self.state.state_root, port=0, app=self.app)) as server:
            conn = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=10
            )
            try:
                conn.request(
                    "POST",
                    "/api/__busy",
                    headers={"X-TI-Token": server.app.state.token},
                )
                response = conn.getresponse()
                status, body = response.status, response.read()
            finally:
                conn.close()
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"], "another writer is active")


class WebCliTests(unittest.TestCase):
    def test_web_help_text_and_options(self) -> None:
        from tiktok_ingest.cli import build_parser

        parser = build_parser()
        # Collapse argparse line-wrapping before matching the exact text.
        main_help = " ".join(parser.format_help().split())
        self.assertIn(
            "Serve the local web interface on 127.0.0.1 (loopback only).",
            main_help,
        )
        web_help = parser._subparsers._group_actions[0].choices["web"].format_help()
        for token in ("--port", "8484", "--no-browser", "--state-root"):
            self.assertIn(token, web_help)

    def test_web_defaults_and_explicit_options(self) -> None:
        from tiktok_ingest.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["web"])
        self.assertEqual(args.command, "web")
        self.assertEqual(args.port, 8484)
        self.assertFalse(args.no_browser)
        self.assertIsNone(args.state_root)
        args = parser.parse_args(
            ["web", "--port", "9000", "--no-browser", "--state-root", "/tmp/x"]
        )
        self.assertEqual(args.port, 9000)
        self.assertTrue(args.no_browser)
        self.assertEqual(args.state_root, Path("/tmp/x"))


class RunWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = StateRoot(Path(self._tmp.name) / "state")
        self.fake_server = mock.Mock()
        self.fake_server.server_port = 8484
        # run_web registers the run and review routes on the server's app,
        # so the fake carries a real one.
        self.fake_server.app = create_app(
            ServerState(state_root=self.state_root, token="t")
        )

    def _run(self, **kwargs) -> tuple[int, str]:
        stdout = io.StringIO()
        with mock.patch(
            "tiktok_ingest.web.server.create_server", return_value=self.fake_server
        ), mock.patch("tiktok_ingest.web.server.webbrowser.open") as opened:
            with contextlib.redirect_stdout(stdout):
                code = run_web(state_root=self.state_root, **kwargs)
        return code, stdout.getvalue(), opened

    def test_prints_url_once_and_opens_browser(self) -> None:
        code, printed, opened = self._run(port=8484)
        self.assertEqual(code, 0)
        self.assertEqual(printed.strip(), "http://127.0.0.1:8484/")
        opened.assert_called_once_with("http://127.0.0.1:8484/")
        self.fake_server.serve_forever.assert_called_once()
        self.fake_server.server_close.assert_called_once()

    def test_no_browser_flag_skips_webbrowser(self) -> None:
        code, _, opened = self._run(port=8484, open_browser=False)
        self.assertEqual(code, 0)
        opened.assert_not_called()

    def test_keyboard_interrupt_exits_zero_after_clean_shutdown(self) -> None:
        self.fake_server.serve_forever.side_effect = KeyboardInterrupt
        code, _, _ = self._run(port=8484)
        self.assertEqual(code, 0)
        self.fake_server.server_close.assert_called_once()


class WebDispatchTests(unittest.TestCase):
    def test_cli_web_dispatch_wires_state_port_and_browser(self) -> None:
        from tiktok_ingest.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            state_root = str(Path(tmp) / "state")
            with mock.patch(
                "tiktok_ingest.cli.run_web", autospec=True, return_value=0
            ) as run, contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "web",
                        "--no-browser",
                        "--port",
                        "9001",
                        "--state-root",
                        state_root,
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(run.call_args.kwargs["port"], 9001)
            self.assertFalse(run.call_args.kwargs["open_browser"])
            self.assertEqual(
                run.call_args.kwargs["state_root"].root, Path(state_root)
            )

    def test_cli_web_starts_the_real_server_and_stops_on_ctrl_c(self) -> None:
        from tiktok_ingest.cli import main
        from tiktok_ingest.web import server as server_module

        def interrupt(self_server, *args, **kwargs):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.object(
                server_module.LoopbackServer, "serve_forever", interrupt
            ), contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "web",
                        "--no-browser",
                        "--port",
                        "0",
                        "--state-root",
                        str(Path(tmp) / "state"),
                    ]
                )
        self.assertEqual(code, 0)
        self.assertRegex(stdout.getvalue().strip(), r"^http://127\.0\.0\.1:\d+/$")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
