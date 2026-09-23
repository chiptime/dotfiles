"""Launcher tests for run-collection.sh (zero-effect paths only).

The launcher is exercised through its REAL script file, but only on
paths that perform no browser, network, container, GPU or API work and
write nothing: the usage check, the argparse help path and the
URL-validation rejection (which happens before any effect inside the
Python command). Argument and exit-code forwarding are proven by
observing the Python command's own outputs and codes through the
launcher.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "run-collection.sh"
)


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.state_root = str(Path(self.tmp.name) / "state")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd or self.tmp.name,  # robustness: run from elsewhere
        )

    def test_script_exists_and_is_executable(self) -> None:
        self.assertTrue(SCRIPT.is_file(), SCRIPT)
        self.assertTrue(
            SCRIPT.stat().st_mode & 0o111, "the launcher must be executable"
        )

    def test_missing_url_is_usage_error_exit_two(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr.lower())

    def test_help_is_forwarded_and_exits_zero(self) -> None:
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("collection-run", result.stdout)
        self.assertIn("COLLECTION_URL", result.stdout)

    def test_invalid_url_is_forwarded_as_exit_two_with_python_error(
        self,
    ) -> None:
        result = self._run(
            "not-a-url", "--state-root", self.state_root
        )
        self.assertEqual(result.returncode, 2)
        # The Python command's own rejection message proves forwarding.
        self.assertIn("collection error", result.stderr)
        self.assertIn("unsupported TikTok collection URL", result.stderr)
        # URL validation happens before any effect: no state is created.
        self.assertFalse(Path(self.state_root).exists())

    def test_dry_run_is_forwarded_and_exits_zero(self) -> None:
        result = self._run(
            "https://www.tiktok.com/@user/collection/slug-1",
            "--dry-run",
            "--state-root",
            self.state_root,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn('"mode": "dry-run"', result.stdout)
        self.assertFalse(Path(self.state_root).exists())

    def test_launcher_holds_no_secrets_and_installs_nothing(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for banned in ("curl ", "wget ", "pip install", "apt ", "sudo ", "podman"):
            self.assertNotIn(banned, text)
        self.assertIn("exec python3 -m tiktok_ingest collection-run", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
