"""Loopback-only web foundation for the Phase 1 local interface (S2/S3/S4).

- ``LoopbackServer``: a ``ThreadingHTTPServer`` bound ONLY to 127.0.0.1;
  any other host value is refused and a busy port fails loudly (no silent
  fallback).
- Browser-origin protection: a random per-start token delivered to the
  page through a meta tag and required (constant-time compare) on every
  mutating request; ``Host``/``Origin`` checks on every request guard
  against DNS rebinding; static paths reject traversal with 404.
- A small (method, path-pattern) router plus JSON helpers so later units
  register endpoints without touching the HTTP plumbing: handlers take a
  ``Request`` and return ``(status, headers, body)`` or raise ``HTTPError``.
- Sanitized access logging: stderr only, query strings stripped, tokens
  never logged or returned in error bodies.
"""

from __future__ import annotations

import json
import secrets
import sys
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from .. import __version__
from ..state import StateRoot

DEFAULT_PORT = 8484
LOOPBACK_HOST = "127.0.0.1"
TOKEN_HEADER = "X-TI-Token"
TOKEN_PLACEHOLDER = "__TI_TOKEN__"
MAX_BODY_BYTES = 1_048_576

STATIC_DIR = Path(__file__).resolve().parent / "static"

SECURITY_HEADERS: Mapping[str, str] = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # default-src 'self' blocks inline scripts, so the token is injected
    # via meta tag and read by the external app.js.
    "Content-Security-Policy": (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "no-referrer",
}

CONTENT_TYPES: Mapping[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}


class WebServerError(Exception):
    """Server startup failure (non-loopback host or busy port)."""


class HTTPError(Exception):
    """Handler-authored HTTP response (status + safe, non-secret message)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class ServerState:
    """Per-start context handed to every handler: state root + token."""

    state_root: StateRoot
    token: str
    version: str = __version__


@dataclass
class Request:
    """Parsed request handed to route handlers."""

    method: str
    path: str  # percent-decoded, query-stripped
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)  # lower-case keys
    body: bytes = b""
    params: dict[str, str] = field(default_factory=dict)  # pattern captures
    state: ServerState | None = None


Response = tuple[int, dict[str, str], bytes]
Handler = Callable[[Request], Response]


def json_response(status: int, payload: object) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8"}, body


def parse_json_body(request: Request) -> object:
    """Decode the request body as UTF-8 JSON (HTTPError 400 on garbage)."""
    try:
        return json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPError(400, "invalid JSON body") from exc


def _split_pattern(pattern: str) -> list[str]:
    return [segment for segment in pattern.strip("/").split("/") if segment]


def _split_path(path: str) -> list[str]:
    return [segment for segment in path.strip("/").split("/") if segment]


class _Route:
    def __init__(self, method: str, pattern: str, handler: Handler) -> None:
        self.method = method.upper()
        self.segments = _split_pattern(pattern)
        self.handler = handler

    @staticmethod
    def _capture(params: dict[str, str], literal: str, segment: str) -> bool:
        """Record one segment match; False when the literal differs."""
        if literal.startswith("{") and literal.endswith("}"):
            params[literal[1:-1]] = segment
            return True
        return literal == segment

    def match(self, segments: list[str]) -> dict[str, str] | None:
        if self.segments and self.segments[-1].endswith("...}"):
            head = self.segments[:-1]
            name = self.segments[-1][1:-4]
            if len(segments) < len(head) + 1:  # remainder must be non-empty
                return None
            params: dict[str, str] = {}
            for literal, segment in zip(head, segments):
                if not self._capture(params, literal, segment):
                    return None
            params[name] = "/".join(segments[len(head):])
            return params
        if len(segments) != len(self.segments):
            return None
        params = {}
        for literal, segment in zip(self.segments, segments):
            if not self._capture(params, literal, segment):
                return None
        return params


class App:
    """Route table + per-start state; handlers never touch HTTP plumbing."""

    def __init__(self, state: ServerState, static_dir: Path | None = None) -> None:
        self.state = state
        self.static_dir = static_dir if static_dir is not None else STATIC_DIR
        self._routes: list[_Route] = []

    def route(self, method: str, pattern: str, handler: Handler) -> None:
        """Register a handler; ``{name}`` captures one path segment,
        ``{name...}`` (final segment) captures the remainder."""
        self._routes.append(_Route(method, pattern, handler))

    def resolve(self, method: str, path: str) -> tuple[Handler, dict[str, str]] | None:
        segments = _split_path(path)
        for route in self._routes:
            if route.method != method:
                continue
            params = route.match(segments)
            if params is not None:
                return route.handler, params
        return None


def _resolve_static(static_dir: Path, relative: str) -> Path | None:
    """Resolve a static path inside static_dir; None on any escape attempt."""
    if relative.startswith("/") or "\x00" in relative:
        return None
    if any(segment in ("", ".", "..") for segment in relative.split("/")):
        return None
    root = static_dir.resolve()
    candidate = (root / relative).resolve()  # follows symlinks out of the dir
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _health_handler(request: Request) -> Response:
    return json_response(200, {"ok": True, "version": request.state.version})


def create_app(state: ServerState, static_dir: Path | None = None) -> App:
    """Build the app with the foundation routes (later units add more)."""
    app = App(state, static_dir=static_dir)
    directory = app.static_dir

    def index(request: Request) -> Response:
        source = _resolve_static(directory, "index.html")
        if source is None:
            return json_response(404, {"error": "not found"})
        html = source.read_text(encoding="utf-8").replace(
            TOKEN_PLACEHOLDER, state.token
        )
        return 200, {"Content-Type": "text/html; charset=utf-8"}, html.encode("utf-8")

    def static(request: Request) -> Response:
        source = _resolve_static(directory, request.params["path"])
        if source is None:
            return json_response(404, {"error": "not found"})
        content_type = CONTENT_TYPES.get(
            source.suffix.lower(), "application/octet-stream"
        )
        return 200, {"Content-Type": content_type}, source.read_bytes()

    app.route("GET", "/", index)
    app.route("GET", "/api/health", _health_handler)
    app.route("GET", "/static/{path...}", static)
    return app


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        app: App,
    ) -> None:
        self.app = app
        super().__init__(address, handler)


class WebRequestHandler(BaseHTTPRequestHandler):
    server: LoopbackServer
    protocol_version = "HTTP/1.1"
    server_version = "tiktok-ingest-web"
    sys_version = ""
    timeout = 30

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        try:
            status, headers, body = self._handle(method)
        except HTTPError as exc:
            status, headers, body = json_response(exc.status, {"error": exc.message})
        except Exception as exc:  # generic 500: never leak handler internals
            self.log_message("internal error (%s) %s", type(exc).__name__, path)
            status, headers, body = json_response(500, {"error": "internal error"})
        self._send(status, headers, body, head_only=method == "HEAD")

    def _handle(self, method: str) -> Response:
        app = self.server.app
        port = self.server.server_port
        split = urlsplit(self.path)
        path = unquote(split.path)
        if not path.startswith("/") or "\x00" in path:
            return json_response(400, {"error": "bad request"})
        if path != "/" and any(
            segment == "" for segment in path.strip("/").split("/")
        ):
            return json_response(404, {"error": "not found"})

        # DNS-rebinding guard: Host must be loopback with our exact port.
        host = self.headers.get("Host", "")
        if host.lower() not in (f"{LOOPBACK_HOST}:{port}", f"localhost:{port}"):
            return json_response(421, {"error": "misdirected request"})

        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in (
            f"http://{LOOPBACK_HOST}:{port}",
            f"http://localhost:{port}",
        ):
            return json_response(403, {"error": "origin not allowed"})

        # Mutating requests must carry the per-start token (constant-time).
        if method not in ("GET", "HEAD"):
            provided = self.headers.get(TOKEN_HEADER, "")
            expected = app.state.token
            if not secrets.compare_digest(
                provided.encode("utf-8"), expected.encode("utf-8")
            ):
                return json_response(403, {"error": "missing or invalid token"})

        if self.headers.get("Transfer-Encoding"):
            return json_response(400, {"error": "unsupported transfer encoding"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return json_response(400, {"error": "bad content length"})
        if length > MAX_BODY_BYTES:
            return json_response(413, {"error": "body too large"})
        body = self.rfile.read(length) if length > 0 else b""

        resolved = app.resolve("GET" if method == "HEAD" else method, path)
        if resolved is None:
            return json_response(404, {"error": "not found"})
        handler, params = resolved
        request = Request(
            method=method,
            path=path,
            query=parse_qs(split.query),
            headers={key.lower(): value for key, value in self.headers.items()},
            body=body,
            params=params,
            state=app.state,
        )
        return handler(request)

    def _send(
        self,
        status: int,
        headers: Mapping[str, str],
        body: bytes,
        head_only: bool = False,
    ) -> None:
        self.send_response(status)
        for name, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        if status >= 400:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        if body and not head_only:
            self.wfile.write(body)

    # -- sanitized logging (stderr, no query strings, no secrets) --------
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        path = self.path.split("?", 1)[0]
        self.log_message("%s %s %s", self.command, path, code)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        safe = tuple(
            arg.split("?", 1)[0] if isinstance(arg, str) else arg for arg in args
        )
        sys.stderr.write(
            "ti-web %s %s\n" % (self.address_string(), format % safe)
        )


def create_server(
    state_root: StateRoot,
    port: int = DEFAULT_PORT,
    host: str = LOOPBACK_HOST,
    app: App | None = None,
    static_dir: Path | None = None,
) -> LoopbackServer:
    """Bind the loopback server; raise WebServerError on any bad input."""
    if host != LOOPBACK_HOST:
        raise WebServerError(
            f"refusing to bind {host!r}: the web interface is loopback-only "
            f"({LOOPBACK_HOST})"
        )
    state = ServerState(state_root=state_root, token=secrets.token_urlsafe(32))
    if app is None:
        app = create_app(state, static_dir=static_dir)
    try:
        return LoopbackServer((host, port), WebRequestHandler, app=app)
    except OSError as exc:
        raise WebServerError(
            f"cannot bind {LOOPBACK_HOST}:{port}: {exc} "
            "(is the port already in use?)"
        ) from exc


def run_web(
    state_root: StateRoot,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> int:
    """Serve until Ctrl+C; print the URL once; exit 0 on KeyboardInterrupt."""
    server = create_server(state_root, port=port)
    url = f"http://{LOOPBACK_HOST}:{server.server_port}/"
    print(url)
    try:
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
