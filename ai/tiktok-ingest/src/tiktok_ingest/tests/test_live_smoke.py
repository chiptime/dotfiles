"""Opt-in live smoke test for the real local stack (S7, Decision 3).

Run it explicitly with (one command, one line)::

    TIKTOK_INGEST_LIVE=1 TIKTOK_INGEST_LIVE_URL=<short video> TIKTOK_INGEST_LIVE_COLLECTION=<collection url> PYTHONPATH=ai/tiktok-ingest/src python3 -m unittest tiktok_ingest.tests.test_live_smoke -v

The module NEVER runs in the default suite: every class is gated on
``TIKTOK_INGEST_LIVE=1`` and importing it has zero side effects. Each part
is a separate test and skips with a clear reason when its own input is
missing:

- (a) environment: podman reachable, the known Whisper container exists and
  its health endpoint answers, the isolated Ollama binary and the
  configured vision model are present, the pinned yt-dlp extractor gate
  passes. Read-only; reuses the package's own probes.
- (b) ``TIKTOK_INGEST_LIVE_URL`` (one short real video): the exact
  ``ingest`` CLI code path (``ingest.run_ingest``, which builds the real
  ``whisper_coordinated_stage_functions`` stages) against an isolated
  temporary state root. This REALLY stops and restores the user's shared
  Whisper container behind its two consent windows (explicitly approved
  for this test); a granting ConsentProvider records every window asked.
- (c) ``TIKTOK_INGEST_LIVE_COLLECTION``: the real browser inventory
  (``browser.run_browser_inventory``) against a temp state root. This may
  need an already logged-in / visible browser because the granting
  provider auto-signals readiness; it is the TikTok DOM canary — zero
  observed items means the collection selector probably broke.

The text-API env keys are stripped in (b) so the deterministic end state
is the one the CLI reports for a video without text API: fetch -> prepare
-> vision -> audio complete and the item waiting for the synthesis
document (exit code 0).
"""

from __future__ import annotations

import contextlib
import os
import signal
import tempfile
import unittest
from typing import Any, Iterator

from tiktok_ingest import config
from tiktok_ingest.browser import run_browser_inventory
from tiktok_ingest.collection import parse_collection_url
from tiktok_ingest.extractor import evaluate_gate_report, probe_gate
from tiktok_ingest.gpu_lease import lease_marker_path
from tiktok_ingest.guided import ConsentProvider, ConsentWindow
from tiktok_ingest.ingest import run_ingest
from tiktok_ingest.ollama_runtime import parse_model_name
from tiktok_ingest.state import StateRoot
from tiktok_ingest.synthesis_api import ENV_API_KEY, ENV_BASE_URL, ENV_MODEL
from tiktok_ingest.whisper_client import health_precheck
from tiktok_ingest.whisper_lifecycle import (
    WHISPER_CONTAINER_NAME,
    inspect_whisper,
    verify_known_service,
)

# The one gate that opts the whole module into touching anything real.
_live_gate = unittest.skipUnless(
    os.environ.get("TIKTOK_INGEST_LIVE") == "1",
    "live smoke: set TIKTOK_INGEST_LIVE=1",
)

# Overall per-test bounds so a hung live dependency cannot wedge the run
# forever (cheap signal.alarm guard, always restored). Generous on (b):
# real stop/restore + Ollama + vision + audio on one video legitimately
# takes minutes.
_ENV_PROBE_TIMEOUT_SECONDS = 300
_VIDEO_TIMEOUT_SECONDS = 3600
_COLLECTION_TIMEOUT_SECONDS = 900

# Stripping these makes (b) deterministic: no auto text synthesizer, so the
# item's final state is the CLI's honest "waiting for synthesis" report.
_TEXT_API_ENV_KEYS = (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL)


@contextlib.contextmanager
def _alarm_guard(seconds: int, what: str) -> Iterator[None]:
    """Raise ``TimeoutError`` after ``seconds``; always restore the alarm."""

    def _fire(signum: int, frame: Any) -> None:  # pragma: no cover - live only
        raise TimeoutError(
            f"live smoke: {what} exceeded its overall {seconds}s bound"
        )

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class _GrantingConsent(ConsentProvider):
    """Grants every window (this test IS the operator) and records them."""

    def __init__(self) -> None:
        self.windows: list[str] = []

    def ask(self, window: ConsentWindow) -> bool:
        self.windows.append(window.kind)
        return True

    def await_readiness(self, prompt: str) -> bool:
        return True


@_live_gate
class LiveEnvironmentSmoke(unittest.TestCase):
    """Part (a): the real local stack answers (read-only probes)."""

    def test_podman_whisper_container_and_health(self) -> None:
        with _alarm_guard(_ENV_PROBE_TIMEOUT_SECONDS, "environment probe"):
            info = inspect_whisper()
            self.assertIsNotNone(
                info,
                f"no container named {WHISPER_CONTAINER_NAME!r} exists "
                "(podman itself answered); create the service first",
            )
            verify_known_service(info)
            ok, detail = health_precheck()
            self.assertTrue(
                ok,
                f"whisper health endpoint {config.WHISPER_HEALTH_URL} did "
                f"not answer ({detail}); the service is {info.describe()} — "
                "it must be running and healthy before the live smoke "
                "(part b stops and restores it)",
            )

    def test_isolated_ollama_and_vision_model(self) -> None:
        self.assertTrue(
            config.OLLAMA_BIN_PATH.is_file(),
            f"isolated Ollama binary missing at {config.OLLAMA_BIN_PATH}; "
            "run the explicit install-ollama command first",
        )
        name, tag = parse_model_name(config.VISION_MODEL)
        manifest = (
            config.OLLAMA_MODELS_DIR
            / "manifests"
            / "registry.ollama.ai"
            / "library"
            / name
            / tag
        )
        self.assertTrue(
            manifest.is_file(),
            f"vision model {config.VISION_MODEL!r} is not imported into "
            f"the isolated store (manifest {manifest} missing); run "
            f"import-model {config.VISION_MODEL}",
        )

    def test_pinned_extractor_gate(self) -> None:
        self.assertTrue(
            config.EXTRACTOR_VENV_DIR.is_dir(),
            f"pinned extractor venv missing at {config.EXTRACTOR_VENV_DIR} "
            f"({config.EXTRACTOR_REQUIREMENT}); run install-extractor first",
        )
        with _alarm_guard(_ENV_PROBE_TIMEOUT_SECONDS, "extractor gate probe"):
            verdict = evaluate_gate_report(
                probe_gate(config.EXTRACTOR_VENV_DIR, config.SHARE_ROOT)
            )
        self.assertTrue(verdict.passed, f"extractor gate: {verdict.reason}")


@_live_gate
@unittest.skipUnless(
    os.environ.get("TIKTOK_INGEST_LIVE_URL"),
    "live smoke: set TIKTOK_INGEST_LIVE_URL to one short TikTok video URL",
)
class LiveDirectVideoSmoke(unittest.TestCase):
    """Part (b): the real direct-URL flow end to end (``ingest`` CLI path).

    REALLY stops and restores the shared Whisper container behind its two
    consent windows (approved for this test). State goes to a temp root
    outside the repo; the user's real state root is never touched.
    """

    def test_direct_video_end_to_end(self) -> None:
        url = os.environ["TIKTOK_INGEST_LIVE_URL"].strip()
        before = inspect_whisper()
        self.assertIsNotNone(
            before,
            f"no container named {WHISPER_CONTAINER_NAME!r} exists; the "
            "live video flow needs the real service to stop and restore",
        )
        self.assertTrue(
            before.running,
            f"the whisper service is not running ({before.describe()}); "
            "start it healthy first",
        )
        consent = _GrantingConsent()
        out_lines: list[str] = []
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in _TEXT_API_ENV_KEYS
        }
        with tempfile.TemporaryDirectory(
            prefix="tiktok-live-smoke-"
        ) as state_root:
            state = StateRoot(state_root)
            with _alarm_guard(_VIDEO_TIMEOUT_SECONDS, "direct-video flow"):
                exit_code, report = run_ingest(
                    url,
                    state=state,
                    consent=consent,
                    out=out_lines.append,
                    env=env,
                )
            tail = "\n".join(out_lines[-5:])
            self.assertEqual(exit_code, 0, f"narration tail:\n{tail}")
            self.assertEqual(report["failures"], [], f"narration tail:\n{tail}")
            self.assertFalse(report["interrupted"])
            self.assertFalse(report["whisper_restore_failed"])
            self.assertTrue(
                report.get("run_plan", {}).get("granted"),
                "the upfront run-plan window was not granted",
            )
            self.assertIn(
                "whisper-stop",
                consent.windows,
                "the whisper stop window was never asked — the GPU swap "
                "path did not run",
            )
            self.assertIn("whisper-restore", consent.windows)
            selected = report["selected"]
            self.assertEqual(len(selected), 1, f"selected: {selected}")
            stages = selected[0]["stages"]
            for stage in ("fetch", "prepare", "vision", "audio"):
                self.assertEqual(
                    stages.get(stage),
                    "complete",
                    f"stage {stage} state: {stages}",
                )
            # Deterministic final state without text API keys: the CLI's
            # report shows the item waiting for the synthesis document.
            self.assertIn(
                selected[0]["id"],
                report["waiting_for_synthesis"],
                f"item not reported as waiting for synthesis: {report}",
            )
            after = inspect_whisper()
            self.assertIsNotNone(after)
            self.assertTrue(
                after is not None and after.running,
                "whisper was not left running after the flow",
            )
            ok, detail = health_precheck()
            self.assertTrue(
                ok, f"restored whisper is not healthy ({detail})"
            )
            self.assertFalse(
                lease_marker_path(state).exists(),
                "gpu lease marker active_gpu_lease.json still present in "
                "the temp state root after the flow",
            )


@_live_gate
@unittest.skipUnless(
    os.environ.get("TIKTOK_INGEST_LIVE_COLLECTION"),
    "live smoke: set TIKTOK_INGEST_LIVE_COLLECTION to a TikTok collection URL",
)
class LiveCollectionSelectorSmoke(unittest.TestCase):
    """Part (c): the real browser inventory — the TikTok DOM canary.

    May need an already logged-in / visible browser: the granting provider
    auto-signals the visibility checkpoint, so complete any login in the
    headed window quickly or be logged in beforehand. Zero observed items
    means the collection selector probably broke (TikTok DOM change).
    """

    def test_browser_inventory_observes_items(self) -> None:
        url = os.environ["TIKTOK_INGEST_LIVE_COLLECTION"].strip()
        ref = parse_collection_url(url)
        consent = _GrantingConsent()
        out_lines: list[str] = []
        with tempfile.TemporaryDirectory(
            prefix="tiktok-live-smoke-collection-"
        ) as state_root:
            state = StateRoot(state_root)
            with _alarm_guard(_COLLECTION_TIMEOUT_SECONDS, "browser inventory"):
                report = run_browser_inventory(
                    state=state,
                    ref=ref,
                    consent=consent,
                    out=out_lines.append,
                )
            self.assertFalse(report.interrupted, f"report: {report.to_dict()}")
            self.assertGreater(
                report.observed_items,
                0,
                "the collection selector observed zero items — TikTok may "
                f"have changed its page layout; narration tail:\n"
                f"{''.join(out_lines[-5:])}",
            )
            warned = [
                line
                for line in out_lines
                if "returned 0 items but previously had" in line
            ]
            self.assertEqual(
                warned,
                [],
                "the zero-items warning (S6) fired during the live capture",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
