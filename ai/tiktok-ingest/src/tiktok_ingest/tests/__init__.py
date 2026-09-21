"""Fixture-only test suite for the TikTok ingest MVP (Milestone 1).

Run from ``ai/tiktok-ingest``::

    python3 -m unittest discover -s src -v

Every test runs inside a temporary directory; the real state root under
``~/.local/state/tiktok-ingest/`` is never read or written.
"""
