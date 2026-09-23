"""Bounded, identity-checked lifecycle for the KNOWN shared Whisper service.

Phase 0 (``collection-run``) offers to stop the shared Whisper service
for the vision window and restore it for audio, so the normal path needs
no operator service commands. Every rule here comes from the Phase 0
PRD (P0-FR-11) and the MVP-PRD section 5 runtime gates:

- Capability is bounded to the ONE known shared service
  (``voice-assistant-whisper``). Stopping arbitrary or unidentified
  workloads is forbidden: identity and configuration are positively
  verified against the known service BEFORE any mutation, and a failed
  or ambiguous verification blocks BEFORE any mutation happens.
- A stop is a bounded ``podman stop`` (SIGTERM, bounded grace, SIGKILL
  escalation handled by podman itself) followed by POSITIVE
  verification that the service is no longer running.
- A restore starts the SAME container (same image, same configuration)
  and verifies identity and configuration afterwards; the caller
  separately validates health before any audio work.
- Failure handling never promises rollback: it reports the actual
  partial service state with recovery instructions and blocks progress.

This module performs NO consent logic: callers (guided-run under
collection-run) ask the explicit bounded consent window naming the
service and operation BEFORE calling any mutation here. All podman
calls go through the injectable ``runner`` (tests use fakes; nothing
real runs in the fixture suite).
"""

from __future__ import annotations

import dataclasses
import json

from .runtime import Runner, subprocess_runner

# The one known shared Whisper service this pipeline may ever touch
# (MVP-PRD section 5: stopping ``voice-assistant-whisper`` requires
# explicit authorization naming the service/operation).
WHISPER_CONTAINER_NAME = "voice-assistant-whisper"

# Environment keys that define the service's identity/configuration for
# verification purposes. Only these are read: the container environment
# may carry unrelated secrets which are NEVER captured or reported.
WHISPER_IDENTITY_ENV_KEYS: tuple[str, ...] = (
    "DEVICE",
    "COMPUTE_TYPE",
    "LANGUAGE",
    "VAD_FILTER",
    "WHISPER_MODEL",
    "MODEL",
)

# Bounded SIGTERM grace before podman escalates to SIGKILL. Historical
# stops required escalating termination (MVP-PRD section 5); podman's
# stop timeout implements that escalation boundedly.
STOP_GRACE_SECONDS = 30


class WhisperLifecycleError(RuntimeError):
    """Operator-readable lifecycle failure; never promises rollback."""


@dataclasses.dataclass(frozen=True)
class WhisperServiceInfo:
    """Positively observed identity/configuration of the service."""

    name: str
    image: str
    state: str
    running: bool
    env: tuple[tuple[str, str], ...]

    def describe(self) -> str:
        env = ", ".join(f"{key}={value}" for key, value in self.env)
        return (
            f"{self.name} (image {self.image}, state {self.state}"
            + (f", config {env}" if env else "")
            + ")"
        )


def _parse_inspect(raw: str) -> WhisperServiceInfo:
    """Parse ``podman inspect`` output for the known container."""
    try:
        document = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise WhisperLifecycleError(
            "cannot verify the whisper service identity: podman inspect "
            f"returned unparseable output ({exc}); refusing to touch any "
            "service (fail closed before mutation)"
        ) from exc
    if not isinstance(document, list) or not document:
        raise WhisperLifecycleError(
            "cannot verify the whisper service identity: podman inspect "
            "returned no record for the known container; refusing to touch "
            "any service (fail closed before mutation)"
        )
    record = document[0]
    if not isinstance(record, dict):
        raise WhisperLifecycleError(
            "cannot verify the whisper service identity: podman inspect "
            "record has an unexpected shape; refusing to touch any service"
        )
    config = record.get("Config") or {}
    state = record.get("State") or {}
    env_pairs: list[tuple[str, str]] = []
    for entry in config.get("Env") or ():
        key, _, value = str(entry).partition("=")
        if key in WHISPER_IDENTITY_ENV_KEYS:
            env_pairs.append((key, value))
    image = str(config.get("Image") or record.get("ImageName") or "")
    return WhisperServiceInfo(
        name=str(record.get("Name") or "").lstrip("/") or WHISPER_CONTAINER_NAME,
        image=image,
        state=str(state.get("Status") or state.get("State") or "unknown"),
        running=bool(state.get("Running", False)),
        env=tuple(sorted(env_pairs)),
    )


def inspect_whisper(
    runner: Runner = subprocess_runner,
) -> WhisperServiceInfo | None:
    """Read-only identity/configuration inspection of the known service.

    Returns ``None`` when the known container does not exist. Any podman
    failure raises :class:`WhisperLifecycleError` — an unidentifiable
    service is never mutated.
    """
    returncode, out, err = runner(
        ["podman", "inspect", WHISPER_CONTAINER_NAME]
    )
    if returncode != 0:
        lowered = (err or "").lower()
        if "no such" in lowered or "not found" in lowered:
            return None
        raise WhisperLifecycleError(
            "cannot verify the whisper service identity (podman inspect "
            f"failed: {(err or '').strip()[-200:]}); refusing to touch any "
            "service (fail closed before mutation)"
        )
    return _parse_inspect(out or "")


def verify_known_service(info: WhisperServiceInfo) -> None:
    """Positive identity check against the known shared service.

    A name mismatch means an unidentified workload is answering under a
    whisper-like name: mutation is refused BEFORE anything runs.
    """
    if info.name != WHISPER_CONTAINER_NAME:
        raise WhisperLifecycleError(
            f"refusing to stop {info.describe()}: only the known shared "
            f"service {WHISPER_CONTAINER_NAME!r} may ever be managed by "
            "this pipeline; no arbitrary or unidentified workload is "
            "stopped"
        )
    if not info.image:
        raise WhisperLifecycleError(
            "cannot verify the whisper service identity: podman inspect "
            "reported no image for the known container; refusing to "
            "mutate an unverifiable service (fail closed)"
        )


def same_service_config(
    before: WhisperServiceInfo, after: WhisperServiceInfo
) -> bool:
    """Same name, image and identity-relevant configuration."""
    return (
        before.name == after.name
        and before.image == after.image
        and before.env == after.env
    )


def _assert_not_running(runner: Runner) -> None:
    returncode, out, err = runner(["podman", "ps", "--format", "{{.Names}}"])
    if returncode != 0:
        raise WhisperLifecycleError(
            "cannot verify the whisper service stopped (podman ps failed: "
            f"{(err or '').strip()[-200:]}); the stop may or may not have "
            "taken effect — check the service state explicitly; no "
            "rollback is performed or implied"
        )
    names = [line.strip() for line in (out or "").splitlines() if line.strip()]
    still = [n for n in names if WHISPER_CONTAINER_NAME == n]
    if still:
        raise WhisperLifecycleError(
            "the whisper service stop did not take effect: "
            f"{WHISPER_CONTAINER_NAME} still appears in podman ps; the "
            "service state is unchanged and vision cannot proceed"
        )


def stop_whisper(
    info: WhisperServiceInfo,
    *,
    runner: Runner = subprocess_runner,
    grace_seconds: int = STOP_GRACE_SECONDS,
) -> WhisperServiceInfo:
    """Bounded stop of the VERIFIED known service, positively checked.

    ``podman stop`` sends SIGTERM and escalates to SIGKILL after the
    bounded grace. The caller MUST have collected explicit consent and
    passed :func:`verify_known_service` first. Raises (never pretends)
    when the stop cannot be positively verified; the error reports the
    real state without promising rollback.
    """
    verify_known_service(info)
    returncode, _, err = runner(
        [
            "podman",
            "stop",
            "--timeout",
            str(grace_seconds),
            WHISPER_CONTAINER_NAME,
        ]
    )
    if returncode != 0:
        raise WhisperLifecycleError(
            "the whisper service stop command failed ("
            f"{(err or '').strip()[-200:]}); the service may or may not "
            "have stopped — verify its state explicitly before any "
            "further GPU work; no rollback is performed or implied"
        )
    _assert_not_running(runner)
    after = inspect_whisper(runner)
    if after is not None and after.running:
        raise WhisperLifecycleError(
            "the whisper service still reports a running state after the "
            "stop; refusing to proceed — verify the service state "
            "explicitly; no rollback is performed or implied"
        )
    return after if after is not None else info


def restore_whisper(
    before: WhisperServiceInfo,
    *,
    runner: Runner = subprocess_runner,
) -> WhisperServiceInfo:
    """Start the SAME container and verify identity/configuration.

    Restoration starts the identical container (same image and
    configuration by construction); success additionally requires the
    restored service to positively match the recorded name, image and
    identity-relevant environment. Health validation is the caller's
    separate step — this returns as soon as identity matches.
    """
    verify_known_service(before)
    returncode, _, err = runner(["podman", "start", WHISPER_CONTAINER_NAME])
    if returncode != 0:
        raise WhisperLifecycleError(
            "the whisper service restore command failed ("
            f"{(err or '').strip()[-200:]}); the service is NOT restored — "
            "report below states what is actually known; no rollback is "
            "performed or implied"
        )
    after = inspect_whisper(runner)
    if after is None:
        raise WhisperLifecycleError(
            "the whisper service restore cannot be verified: podman "
            "inspect no longer finds the known container; the service is "
            "NOT restored — no rollback is performed or implied"
        )
    if not after.running:
        raise WhisperLifecycleError(
            "the whisper service was started but does not report a "
            "running state; the service is NOT restored — no rollback is "
            "performed or implied"
        )
    if not same_service_config(before, after):
        raise WhisperLifecycleError(
            "the restored whisper service does not match the recorded "
            f"service configuration (before {before.describe()}; after "
            f"{after.describe()}); the SAME service/config was not "
            "restored — no rollback is performed or implied"
        )
    return after


def describe_service_or_none(
    runner: Runner = subprocess_runner,
) -> str:
    """Read-only display helper for preflight/consent windows."""
    try:
        info = inspect_whisper(runner)
    except WhisperLifecycleError as exc:
        return f"(identity check failed: {exc})"
    if info is None:
        return f"(no container named {WHISPER_CONTAINER_NAME} exists)"
    return info.describe()


__all__ = [
    "STOP_GRACE_SECONDS",
    "WHISPER_CONTAINER_NAME",
    "WHISPER_IDENTITY_ENV_KEYS",
    "WhisperLifecycleError",
    "WhisperServiceInfo",
    "describe_service_or_none",
    "inspect_whisper",
    "restore_whisper",
    "same_service_config",
    "stop_whisper",
    "verify_known_service",
]
