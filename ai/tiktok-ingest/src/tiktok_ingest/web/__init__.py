"""Loopback-only local web interface (Phase 1).

``server`` holds the whole stdlib foundation: threading server, router,
per-start token, Host/Origin guards and static serving. Later units add
JSON endpoints through ``server.App.route`` without touching internals.
"""
