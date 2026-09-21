"""Extractor environment bootstrap (Milestone 2).

The pinned extractor lives in a virtual environment OUTSIDE Git under the
local share root (default ``~/.local/share/tiktok-ingest/extractor-venv``),
following the dotfiles doctrine: the repository pins the version, machine
local installs stay out of Git.

Rules encoded here:

- ``install-extractor`` is an EXPLICITLY invoked CLI subcommand. Nothing in
  the processing path imports or executes this module; a fetch must never
  create environments or install packages implicitly.
- No sudo, no global installs: the bootstrap refuses to run as root and
  only ever creates a local venv and installs the pinned requirement into
  it with ``--no-deps`` (benchmark methodology section 2).
- Binaries are never committed; the version pin lives in ``config``.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Callable

from . import config
from .extractor import check_version, subprocess_runner, venv_python


class BootstrapError(RuntimeError):
    """Raised when the extractor environment cannot be prepared safely."""


@dataclasses.dataclass(frozen=True)
class InstallStep:
    """One explicit, auditable bootstrap command."""

    name: str
    argv: tuple[str, ...]
    rationale: str


def build_install_steps(
    venv_dir: Path | str,
    *,
    python_executable: str | None = None,
) -> tuple[InstallStep, ...]:
    """Deterministic, minimal installation steps for the pinned extractor."""
    venv_dir = Path(venv_dir)
    python = python_executable or sys.executable
    return (
        InstallStep(
            name="create-venv",
            argv=(python, "-m", "venv", str(venv_dir)),
            rationale=(
                "isolated virtual environment under the local share root, "
                "outside Git; no system packages are touched"
            ),
        ),
        InstallStep(
            name="install-extractor",
            argv=(
                str(venv_python(venv_dir)),
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--disable-pip-version-check",
                config.EXTRACTOR_REQUIREMENT,
            ),
            rationale=(
                f"exact pinned requirement {config.EXTRACTOR_REQUIREMENT} "
                "with no dependency tree, per benchmark methodology section 2"
            ),
        ),
    )


def guard_install_argv(argv: list[str] | tuple[str, ...]) -> None:
    """Refuse privilege escalation or global targets in bootstrap commands."""
    forbidden = {"sudo", "doas", "su"}
    for item in argv:
        name = Path(str(item)).name.lower()
        if name in forbidden:
            raise BootstrapError(
                f"bootstrap step contains {str(item)!r}: no privilege "
                "escalation is allowed; installs stay inside the local "
                "user-owned venv"
            )


def install_extractor(
    venv_dir: Path | str,
    share_root: Path | str,
    *,
    runner: Callable[..., tuple[int, str, str]] = subprocess_runner,
    python_executable: str | None = None,
    euid: int | None = None,
) -> list[tuple[InstallStep, tuple[int, str, str]]]:
    """Run the explicit install steps. NEVER called by processing paths."""
    euid = os.geteuid() if euid is None else euid
    if euid == 0:
        raise BootstrapError(
            "refusing to install as root: no sudo, no global installs; "
            "run this command as the owning user"
        )
    steps = build_install_steps(venv_dir, python_executable=python_executable)
    results: list[tuple[InstallStep, tuple[int, str, str]]] = []
    for step in steps:
        argv = list(step.argv)
        guard_install_argv(argv)
        outcome = runner(argv)
        results.append((step, outcome))
        returncode, _, stderr = outcome
        if returncode != 0:
            raise BootstrapError(
                f"bootstrap step {step.name!r} failed with exit code "
                f"{returncode}: {(stderr or '').strip()[-500:]}"
            )
    return results


def verify_installation(
    venv_dir: Path | str,
    *,
    runner: Callable[..., tuple[int, str, str]] = subprocess_runner,
) -> tuple[bool, str]:
    """Post-install verification: reported version must match the pin."""
    returncode, out, err = runner(
        [str(venv_python(venv_dir)), "-m", "yt_dlp", "--version"]
    )
    if returncode != 0:
        return False, (
            f"installed extractor did not report a version (exit {returncode}): "
            f"{(err or '').strip()[-300:]}"
        )
    return check_version(out)
