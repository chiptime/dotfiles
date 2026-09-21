"""Bootstrap: explicit install steps, no root, no sudo, pinned requirement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.bootstrap import (
    BootstrapError,
    build_install_steps,
    guard_install_argv,
    install_extractor,
    verify_installation,
)
from tiktok_ingest.tests import fixtures


class InstallStepTests(unittest.TestCase):
    def test_two_minimal_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = Path(tmp) / "extractor-venv"
            steps = build_install_steps(venv_dir, python_executable="/usr/bin/python3")
            self.assertEqual([step.name for step in steps], ["create-venv", "install-extractor"])
            create, install = steps
            self.assertEqual(
                create.argv,
                ("/usr/bin/python3", "-m", "venv", str(venv_dir)),
            )
            self.assertIn(config.EXTRACTOR_REQUIREMENT, install.argv)
            self.assertIn("--no-deps", install.argv)
            self.assertIn("--disable-pip-version-check", install.argv)

    def test_pip_targets_the_venv_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = Path(tmp) / "venv"
            _, install = build_install_steps(venv_dir)
            self.assertEqual(
                install.argv[0], str(venv_dir / "bin" / "python")
            )


class GuardTests(unittest.TestCase):
    def test_privilege_escalation_is_refused(self) -> None:
        for argv in (["sudo", "-m", "venv", "/opt"], ["/usr/bin/su", "-c", "x"]):
            with self.assertRaises(BootstrapError):
                guard_install_argv(argv)

    def test_plain_steps_pass(self) -> None:
        guard_install_argv(["/usr/bin/python3", "-m", "venv", "/home/u/.local/x"])


class InstallTests(unittest.TestCase):
    def test_refuses_to_run_as_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BootstrapError) as ctx:
                install_extractor(
                    Path(tmp) / "venv", Path(tmp), runner=lambda argv, timeout=None: (0, "", ""),
                    euid=0,
                )
            self.assertIn("root", str(ctx.exception))

    def test_runs_steps_in_order_and_reports_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[list[str]] = []

            def runner(argv, timeout=None):
                calls.append(list(argv))
                return 0, "", ""

            results = install_extractor(
                Path(tmp) / "venv", Path(tmp), runner=runner, euid=1000
            )
            self.assertEqual(len(results), 2)
            self.assertEqual(calls[0][1:3], ["-m", "venv"])
            self.assertEqual(calls[0][-1], str(Path(tmp) / "venv"))

    def test_failed_step_stops_the_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[list[str]] = []

            def runner(argv, timeout=None):
                calls.append(list(argv))
                return 1 if "pip" in argv else 0, "", "network unreachable"

            with self.assertRaises(BootstrapError) as ctx:
                install_extractor(
                    Path(tmp) / "venv", Path(tmp), runner=runner, euid=1000
                )
            self.assertEqual(len(calls), 2)
            self.assertIn("network unreachable", str(ctx.exception))

    def test_install_never_appears_in_processing_paths(self) -> None:
        # The processing modules must not import the bootstrap: fetching and
        # preparing can never create environments or install packages.
        import tiktok_ingest.extractor as extractor_module
        import tiktok_ingest.prepare as prepare_module
        import tiktok_ingest.pipeline as pipeline_module

        for module in (extractor_module, prepare_module, pipeline_module):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("bootstrap", source)


class VerifyInstallationTests(unittest.TestCase):
    def test_matching_pin_verifies(self) -> None:
        ok, reason = verify_installation(
            Path("/venv"),
            runner=lambda argv, timeout=None: (0, "2026.08.19\n", ""),
        )
        self.assertTrue(ok)

    def test_mismatched_pin_fails(self) -> None:
        ok, reason = verify_installation(
            Path("/venv"),
            runner=lambda argv, timeout=None: (0, "2026.09.01\n", ""),
        )
        self.assertFalse(ok)
        self.assertIn("refusing", reason)

    def test_broken_install_fails(self) -> None:
        ok, reason = verify_installation(
            Path("/venv"),
            runner=lambda argv, timeout=None: (1, "", "No module named yt_dlp"),
        )
        self.assertFalse(ok)
        self.assertIn("No module", reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
