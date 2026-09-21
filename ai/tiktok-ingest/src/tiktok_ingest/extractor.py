"""Gated pinned extractor and oEmbed metadata cache (Milestone 2).

Policy implemented here (PRD section 6.2, MVP-PRD section 3.A, benchmark
methodology section 2):

- The public extractor is pinned to ``config.EXTRACTOR_PIN`` and resolved
  from a local virtual environment OUTSIDE Git (default under
  ``~/.local/share/tiktok-ingest/``, see ``config.SHARE_ROOT``).
- Verification happens BEFORE any execution: the reported version must
  match the pin exactly, and every protected extractor area (App-API
  paths, automatic challenge solving) must have at least one located and
  hard-blocked enforcement point in the pinned source. Ambient cookies
  and cookie-export options are refused at the argument level.
- ANY gate failure yields a ``blocked`` outcome with a reason. Execution
  is never degraded, never bypassed, and extraction is never retried
  automatically.
- Extraction outcomes use the PRD section 6.2 vocabulary: ``denied``,
  ``unsupported``, ``challenge`` and ``blocked`` are recorded without
  bypass (plus ``complete`` / ``failed`` / ``budget_exceeded``).
- oEmbed metadata is fetched over stdlib HTTP with a 24-hour TTL cache;
  payload, canonical URL, fetch time and outcome are persisted through
  the existing state/cache modules.

The two runtime scripts in this module (gate probe and download runner)
execute INSIDE the extractor virtual environment, after being written
atomically to the local share root. They only use the venv's stdlib and
the pinned yt-dlp. Unit tests never execute them: every boundary is
injectable and the scripts are tested as data (pure helpers).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from . import config
from .contracts import utc_now_iso
from .runtime import default_urlopen, subprocess_runner
from .state import CacheStore, StateRoot, stage_cache_key

# --------------------------------------------------------------------------
# Extraction outcome vocabulary (PRD section 6.2)
# --------------------------------------------------------------------------

EXTRACTION_OUTCOMES: tuple[str, ...] = (
    "complete",
    "denied",
    "unsupported",
    "challenge",
    "blocked",
    "failed",
    "budget_exceeded",
)


class ExtractorError(RuntimeError):
    """Raised when an extractor operation cannot be completed."""


@dataclasses.dataclass(frozen=True)
class ExtractionResult:
    """Outcome of one gated extraction attempt. Every outcome has a reason."""

    outcome: str
    reason: str
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)
    media_path: str | None = None
    media_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in EXTRACTION_OUTCOMES:
            raise ExtractorError(
                f"extraction outcome must be one of {list(EXTRACTION_OUTCOMES)}, "
                f"got {self.outcome!r}"
            )
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ExtractorError("ExtractionResult.reason must be non-empty")
        if not isinstance(self.detail, dict):
            raise ExtractorError("ExtractionResult.detail must be an object")


def tail(text: str, limit: int = 800) -> str:
    """Last ``limit`` characters of observed output, for durable records."""
    text = (text or "").strip()
    return text[-limit:]


# --------------------------------------------------------------------------
# Gate: version pin and protected-area verification (fail closed)
# --------------------------------------------------------------------------

# Gate conditions that must ALL hold before execution (MVP-PRD section 3.A).
GATE_CONDITIONS: tuple[str, ...] = (
    "version_matches_pin",
    "app_api_paths_blocked",
    "challenge_solving_blocked",
    "ambient_cookies_blocked",
    "cookie_export_blocked",
)

# Gate report schema produced by the in-venv probe script.
GATE_REPORT_SCHEMA_VERSION = 1


@dataclasses.dataclass(frozen=True)
class GateVerdict:
    """Result of gate evaluation. ``passed=False`` means NO execution."""

    passed: bool
    reason: str
    checks: tuple[tuple[str, bool, str], ...] = ()

    def failed_conditions(self) -> tuple[str, ...]:
        return tuple(name for name, ok, _ in self.checks if not ok)


def check_version(version_output: str) -> tuple[bool, str]:
    """Exact-pin version check. Returns ``(ok, reason)``."""
    version = (version_output or "").strip()
    if version == config.EXTRACTOR_PIN:
        return True, f"extractor version matches pin {config.EXTRACTOR_PIN}"
    return False, (
        f"extractor version {version!r} does not match required pin "
        f"{config.EXTRACTOR_PIN!r}; refusing to execute"
    )


def evaluate_gate_report(report: Mapping[str, Any] | None) -> GateVerdict:
    """Validate a runtime gate probe report. Anything absent fails closed.

    An unmodified stock install is NOT proof of compliance: the report must
    show located (and therefore patched) enforcement points for BOTH the
    App-API and challenge-solving areas, plus hard blocking of ambient
    cookies and cookie export.
    """
    checks: list[tuple[str, bool, str]] = []

    def require(condition: str, ok: bool, reason: str) -> None:
        checks.append((condition, bool(ok), reason))

    if report is None:
        return GateVerdict(
            passed=False,
            reason="gate probe produced no report; failing closed",
            checks=tuple(
                (condition, False, "no gate report") for condition in GATE_CONDITIONS
            ),
        )

    version = report.get("version")
    ok, reason = check_version(version if isinstance(version, str) else "")
    require("version_matches_pin", ok, reason)

    areas = report.get("areas")
    if not isinstance(areas, dict):
        areas = {}
    for condition, area_key, label in (
        ("app_api_paths_blocked", "app_api", "App-API"),
        ("challenge_solving_blocked", "challenge", "challenge-solving"),
    ):
        area = areas.get(area_key)
        found = area.get("found") if isinstance(area, dict) else None
        if isinstance(found, list) and found:
            require(
                condition,
                True,
                f"{label} enforcement points located and hard-blocked: "
                f"{sorted(str(item) for item in found)}",
            )
        else:
            require(
                condition,
                False,
                f"no {label} enforcement point located in the pinned extractor; "
                "the protected paths cannot be proven blocked (fail closed)",
            )

    for condition, key, label in (
        ("ambient_cookies_blocked", "ambient_cookies_blocked", "ambient cookies"),
        ("cookie_export_blocked", "cookie_export_blocked", "cookie export"),
    ):
        value = report.get(key)
        require(
            condition,
            value is True,
            f"{label} blocked by the gate probe"
            if value is True
            else f"gate report does not prove {label} is blocked; failing closed",
        )

    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        return GateVerdict(
            passed=False,
            reason="extractor gate failed ("
            + "; ".join(failed)
            + "); extraction blocked before execution",
            checks=tuple(checks),
        )
    return GateVerdict(
        passed=True,
        reason="extractor gate passed: pin verified and protected paths blocked",
        checks=tuple(checks),
    )


def check_forbidden_options(argv: list[str]) -> tuple[bool, str]:
    """Refuse ambient-cookie / cookie-export options at the argument level.

    Matching covers the exact option, its ``=VALUE`` form, and prefix
    collisions (``--cookie`` against ``--cookies``), so no spelling of a
    credential-transfer flag slips through.
    """
    for item in argv:
        name = str(item).lower().split("=", 1)[0]
        for option in config.EXTRACTOR_FORBIDDEN_OPTIONS:
            if name == option or name.startswith(option) or option.startswith(name):
                return False, (
                    f"option {str(item)!r} may transfer browser credentials to "
                    "the extractor; refusing (no ambient cookies, no cookie export)"
                )
    return True, "no credential-transfer options present"


# --------------------------------------------------------------------------
# Runtime scripts (executed inside the extractor venv only)
# --------------------------------------------------------------------------

# Tokens injected into the embedded sources. Token REPLACEMENT is used
# instead of %-formatting on purpose: the embedded code itself contains %
# operators that must survive verbatim.
_APP_API_TOKEN = "__GATE_APP_API_SYMBOLS__"
_CHALLENGE_TOKEN = "__GATE_CHALLENGE_SYMBOLS__"
_FORBIDDEN_TOKEN = "__GATE_FORBIDDEN_OPTIONS__"
_JSON_MARKER_TOKEN = "__GATE_JSON_MARKER__"
_JSON_MARKER = "<<JSON>>"

# Shared helper code embedded in both in-venv scripts: locates protected
# symbols on the TikTok extractor classes and hard-blocks them by raising
# before any network action can go through those paths.
_GATE_HELPERS_SOURCE = (
    r'''
import inspect
import json
import sys


class PolicyBlocked(Exception):
    """Raised when a protected extractor path is invoked after patching."""


def _blocker(area, name):
    def _blocked(*args, **kwargs):
        raise PolicyBlocked(
            "policy gate: " + area + "." + name + " is hard-blocked before execution"
        )
    return _blocked


def patch_gate_symbols(candidates_by_area):
    """Locate and hard-block protected symbols on TikTok extractor classes.

    Returns a report mapping each area to the symbols found (and patched)
    versus missing. Located attributes are replaced on the object that
    exposes them, so any later call raises ``PolicyBlocked`` instead of
    reaching the network.
    """
    import yt_dlp.extractor.tiktok as tiktok_module

    targets = [tiktok_module]
    for _, obj in sorted(vars(tiktok_module).items()):
        if inspect.isclass(obj) and obj.__name__.startswith("TikTok"):
            targets.append(obj)

    areas = {}
    patched = set()
    for area, candidates in candidates_by_area.items():
        found, missing = [], []
        for name in candidates:
            located = False
            for target in targets:
                key = (id(target), name)
                if key in patched:
                    located = True
                    continue
                try:
                    has = hasattr(target, name)
                except AttributeError:
                    has = False
                if has:
                    try:
                        setattr(target, name, _blocker(area, name))
                        patched.add(key)
                        located = True
                    except (AttributeError, TypeError):
                        located = False
            if located and name not in found:
                found.append(name)
            if not located and name not in missing:
                missing.append(name)
        areas[area] = {"candidates": list(candidates), "found": found,
                       "missing": missing}
    return areas


def gate_candidates():
    return {
        "app_api": list(__GATE_APP_API_SYMBOLS__),
        "challenge": list(__GATE_CHALLENGE_SYMBOLS__),
    }


def emit(payload):
    sys.stdout.write("__GATE_JSON_MARKER__" + json.dumps(payload) + "\n")
    sys.stdout.flush()
'''
    .replace("__GATE_APP_API_SYMBOLS__", repr(tuple(config.EXTRACTOR_GATE_APP_API_SYMBOLS)))
    .replace("__GATE_CHALLENGE_SYMBOLS__", repr(tuple(config.EXTRACTOR_GATE_CHALLENGE_SYMBOLS)))
    .replace(_JSON_MARKER_TOKEN, _JSON_MARKER)
)

# In-venv gate probe: patches the protected paths and reports WITHOUT any
# network activity. Missing/absent report fields make the outer evaluator
# fail closed.
GATE_PROBE_SOURCE = (
    _GATE_HELPERS_SOURCE
    + r'''

def main():
    try:
        import yt_dlp
        areas = patch_gate_symbols(gate_candidates())
        emit({
            "schema": __GATE_SCHEMA__,
            "version": yt_dlp.version.__version__,
            "areas": areas,
            "ambient_cookies_blocked": True,
            "cookie_export_blocked": True,
        })
    except Exception as exc:  # report everything; the evaluator fails closed
        emit({"schema": __GATE_SCHEMA__,
              "probe_error": type(exc).__name__ + ": " + str(exc)})

main()
'''.replace("__GATE_SCHEMA__", str(GATE_REPORT_SCHEMA_VERSION))
)

# In-venv download runner: re-applies the hard-block patches, refuses
# cookie options, then performs exactly ONE bounded download with retries
# disabled. Emits a JSON outcome document on stdout.
EXTRACTOR_RUNNER_SOURCE = (
    _GATE_HELPERS_SOURCE
    + r'''

def classify_error(message):
    text = (message or "").lower()
    if "captcha" in text or "challenge" in text or "login" in text:
        return ("challenge",
                "access requires human interaction (captcha/login); "
                "pausing, never bypassing")
    if "403" in text or "forbidden" in text or "denied" in text:
        return ("denied",
                "access denied by the server (observed response recorded)")
    if ("unsupported url" in text or "unable to extract" in text
            or "not a video" in text):
        return "unsupported", "the URL is not supported by the pinned extractor"
    if "larger than" in text or "max-filesize" in text or "file is larger" in text:
        return "budget_exceeded", "media exceeded the configured size budget"
    return "failed", "extraction failed (observed error recorded)"


def main():
    import os

    import yt_dlp

    argv = sys.argv[1:]
    mode, url, dest_dir, options_json = argv[0], argv[1], argv[2], argv[3]
    options = json.loads(options_json)

    for option in argv:
        lowered = option.lower()
        for forbidden in __GATE_FORBIDDEN_OPTIONS__:
            if lowered == forbidden or lowered.startswith(forbidden + "="):
                emit({"outcome": "blocked",
                      "reason": "credential-transfer option refused",
                      "detail": {"option": forbidden}})
                return 0

    try:
        areas = patch_gate_symbols(gate_candidates())
        for area, info in areas.items():
            if not info["found"]:
                emit({"outcome": "blocked",
                      "reason": ("gate: no " + area +
                                 " enforcement point could be patched"),
                      "detail": {"area": area}})
                return 0
    except Exception as exc:  # noqa: BLE001 - fail closed before execution
        emit({"outcome": "blocked",
              "reason": "gate patching failed before execution",
              "detail": {"error": type(exc).__name__ + ": " + str(exc)}})
        return 0

    ydl_opts = {
        "outtmpl": os.path.join(dest_dir, "%(id)s.%(ext)s"),
        "retries": 0,
        "fragment_retries": 0,
        "socket_timeout": options.get("socket_timeout", 30),
        "max_filesize": options.get("max_filesize"),
        "noplaylist": True,
        "nooverwrites": True,
        "quiet": True,
        "skip_download": False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = []
        if info:
            for item in info.get("requested_downloads") or []:
                path = item.get("filepath") or item.get("filename")
                if path:
                    files.append(path)
        emit({"outcome": "complete",
              "reason": "one media asset downloaded under the configured budget",
              "detail": {"files": files,
                         "title": (info or {}).get("title"),
                         "id": (info or {}).get("id")}})
        return 0
    except PolicyBlocked as exc:
        emit({"outcome": "blocked", "reason": str(exc), "detail": {}})
        return 0
    except yt_dlp.utils.DownloadError as exc:
        outcome, reason = classify_error(str(exc))
        emit({"outcome": outcome, "reason": reason,
              "detail": {"observed": str(exc)[:500]}})
        return 0
    except Exception as exc:  # noqa: BLE001
        emit({"outcome": "failed",
              "reason": "extraction failed with an unexpected error",
              "detail": {"error": type(exc).__name__ + ": " + str(exc)[:500]}})
        return 1


sys.exit(main())
'''.replace(_FORBIDDEN_TOKEN, json.dumps(list(config.EXTRACTOR_FORBIDDEN_OPTIONS)))
)


def venv_python(venv_dir: Path | str) -> Path:
    """Python interpreter of the extractor virtual environment."""
    return Path(venv_dir) / "bin" / "python"


def _write_script(path: Path, source: str) -> Path:
    from .state import atomic_write_bytes

    atomic_write_bytes(path, source.encode("utf-8"))
    return path


def probe_gate(
    venv_dir: Path | str,
    share_root: Path | str,
    *,
    runner: Callable[..., tuple[int, str, str]] = subprocess_runner,
    timeout: float = 60.0,
) -> dict[str, Any] | None:
    """Run the in-venv gate probe and return its JSON report (or None).

    No execution decision is made here; :func:`evaluate_gate_report` fails
    closed on any missing evidence.
    """
    share_root = Path(share_root)
    script = _write_script(share_root / config.EXTRACTOR_GATE_PROBE_PATH.name,
                           GATE_PROBE_SOURCE)
    rc, out, err = runner(
        [str(venv_python(venv_dir)), str(script)], timeout=timeout
    )
    if rc != 0:
        return None
    marker = "<<JSON>>"
    for line in out.splitlines():
        if line.startswith(marker):
            try:
                report = json.loads(line[len(marker):])
            except json.JSONDecodeError:
                return None
            return report if isinstance(report, dict) else None
    return None


def verify_extractor_gate(
    venv_dir: Path | str,
    share_root: Path | str,
    *,
    runner: Callable[..., tuple[int, str, str]] = subprocess_runner,
) -> GateVerdict:
    """Full pre-execution gate: probe + evaluation. Fail closed on anything."""
    report = probe_gate(venv_dir, share_root, runner=runner)
    if report is not None and "probe_error" in report:
        return GateVerdict(
            passed=False,
            reason=(
                "gate probe failed before reporting: "
                f"{report.get('probe_error')!r}; extraction blocked"
            ),
        )
    return evaluate_gate_report(report)


def _result_from_runner_output(
    outcome_json: str, stderr: str
) -> ExtractionResult:
    marker = "<<JSON>>"
    for line in (outcome_json or "").splitlines():
        if line.startswith(marker):
            try:
                payload = json.loads(line[len(marker):])
            except json.JSONDecodeError:
                break
            if isinstance(payload, dict) and "outcome" in payload:
                outcome = str(payload.get("outcome"))
                if outcome not in EXTRACTION_OUTCOMES:
                    return ExtractionResult(
                        outcome="failed",
                        reason=f"runner reported unknown outcome {outcome!r}",
                        detail=payload,
                    )
                detail = payload.get("detail")
                detail = detail if isinstance(detail, dict) else {}
                media_path = None
                if outcome == "complete":
                    files = [str(item) for item in detail.get("files", [])]
                    media_path = files[0] if files else None
                return ExtractionResult(
                    outcome=outcome,
                    reason=str(payload.get("reason") or "runner reported outcome"),
                    detail=detail,
                    media_path=media_path,
                )
    return ExtractionResult(
        outcome="failed",
        reason="runner produced no parsable outcome document",
        detail={"stderr": tail(stderr)},
    )


def run_extraction(
    canonical_url: str,
    dest_dir: Path | str,
    *,
    venv_dir: Path | str,
    share_root: Path | str,
    runner: Callable[..., tuple[int, str, str]] = subprocess_runner,
    deadline_seconds: float | None = None,
    max_bytes: int | None = None,
    socket_timeout: int = 30,
) -> ExtractionResult:
    """Run one gated, bounded extraction attempt. NEVER retries.

    Order: gate first (version + protected paths + cookie refusal), then a
    single download with retries disabled. Any gate failure returns a
    ``blocked`` result without executing the downloader.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    verdict = verify_extractor_gate(venv_dir, share_root, runner=runner)
    if not verdict.passed:
        return ExtractionResult(
            outcome="blocked",
            reason=verdict.reason,
            detail={
                "gate_conditions": [
                    {"condition": name, "ok": ok, "reason": reason}
                    for name, ok, reason in verdict.checks
                ]
            },
        )

    script = _write_script(
        Path(share_root) / config.EXTRACTOR_RUNNER_PATH.name,
        EXTRACTOR_RUNNER_SOURCE,
    )
    options = {
        "socket_timeout": socket_timeout,
        "max_filesize": max_bytes,
    }
    argv = [
        str(venv_python(venv_dir)),
        str(script),
        "download",
        canonical_url,
        str(dest_dir),
        json.dumps(options),
    ]
    forbidden_ok, forbidden_reason = check_forbidden_options(argv)
    if not forbidden_ok:
        return ExtractionResult(outcome="blocked", reason=forbidden_reason)

    try:
        rc, out, err = runner(argv, timeout=deadline_seconds)
    except subprocess.TimeoutExpired:
        return ExtractionResult(
            outcome="budget_exceeded",
            reason=f"fetch deadline of {deadline_seconds}s exceeded",
            detail={"deadline_seconds": deadline_seconds},
        )
    except Exception as exc:
        return ExtractionResult(
            outcome="failed",
            reason=f"runner failed before completing ({exc.__class__.__name__})",
            detail={"deadline_seconds": deadline_seconds},
        )

    if rc == 124:
        return ExtractionResult(
            outcome="budget_exceeded",
            reason=f"fetch deadline of {deadline_seconds}s exceeded",
            detail={"stderr": tail(err)},
        )

    result = _result_from_runner_output(out, err)
    if rc != 0 and result.outcome == "complete":
        return ExtractionResult(
            outcome="failed",
            reason="runner reported complete but exited non-zero",
            detail={"returncode": rc, "stderr": tail(err)},
        )
    if result.outcome == "complete" and result.media_path is None:
        return ExtractionResult(
            outcome="failed",
            reason="runner reported complete but named no media file",
            detail=result.detail,
        )
    return result


# --------------------------------------------------------------------------
# oEmbed metadata (stdlib HTTP, TTL cache through the state modules)
# --------------------------------------------------------------------------

OEMBED_CACHE_SCHEMA = 1
_OEMBED_TIMEOUT_SECONDS = 15


def build_oembed_url(canonical_url: str) -> str:
    return f"{config.OEMBED_ENDPOINT}?url={urllib.parse.quote(canonical_url, safe='')}"


@dataclasses.dataclass(frozen=True)
class OEmbedRecord:
    """One oEmbed fetch: payload, canonical URL, fetch time and outcome."""

    canonical_url: str
    fetched_at: str
    outcome: str
    reason: str
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    ttl_seconds: int = config.BUDGETS.metadata_ttl_seconds

    def __post_init__(self) -> None:
        if self.outcome not in EXTRACTION_OUTCOMES:
            raise ExtractorError(
                f"oEmbed outcome must be one of {list(EXTRACTION_OUTCOMES)}"
            )
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ExtractorError("OEmbedRecord.reason must be non-empty")

    @property
    def fresh_within_ttl(self) -> bool:
        return self.outcome == "complete"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OEmbedRecord":
        return cls(
            canonical_url=str(data.get("canonical_url")),
            fetched_at=str(data.get("fetched_at")),
            outcome=str(data.get("outcome")),
            reason=str(data.get("reason")),
            payload=dict(data.get("payload") or {}),
            ttl_seconds=int(data.get("ttl_seconds") or config.BUDGETS.metadata_ttl_seconds),
        )


def parse_oembed_payload(body: bytes | str) -> dict[str, Any]:
    """Parse an oEmbed response body. Raises ``ExtractorError`` on garbage."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExtractorError(f"oEmbed response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExtractorError("oEmbed response must be a JSON object")
    return payload


def fetch_oembed(
    canonical_url: str,
    *,
    urlopen: Callable[..., Any] = default_urlopen,
    timeout: float = _OEMBED_TIMEOUT_SECONDS,
    fetched_at: str | None = None,
) -> OEmbedRecord:
    """Fetch oEmbed metadata once. Failures are recorded, never raised past
    the pipeline: missing metadata does not prevent processing media."""
    request = urllib.request.Request(
        build_oembed_url(canonical_url),
        headers={"User-Agent": "tiktok-ingest/+local", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read()
    except urllib.error.HTTPError as exc:
        outcome = "denied" if exc.code in (401, 403) else "failed"
        return OEmbedRecord(
            canonical_url=canonical_url,
            fetched_at=fetched_at or utc_now_iso(),
            outcome=outcome,
            reason=f"oEmbed HTTP {exc.code}; outcome recorded without retry",
            payload={},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return OEmbedRecord(
            canonical_url=canonical_url,
            fetched_at=fetched_at or utc_now_iso(),
            outcome="failed",
            reason=f"oEmbed request failed: {exc.__class__.__name__}",
            payload={},
        )
    if status != 200:
        return OEmbedRecord(
            canonical_url=canonical_url,
            fetched_at=fetched_at or utc_now_iso(),
            outcome="failed",
            reason=f"oEmbed returned HTTP {status}",
        )
    try:
        payload = parse_oembed_payload(body)
    except ExtractorError as exc:
        return OEmbedRecord(
            canonical_url=canonical_url,
            fetched_at=fetched_at or utc_now_iso(),
            outcome="failed",
            reason=str(exc),
        )
    return OEmbedRecord(
        canonical_url=canonical_url,
        fetched_at=fetched_at or utc_now_iso(),
        outcome="complete",
        reason="oEmbed metadata fetched",
        payload=payload,
    )


def oembed_cache_key(canonical_url: str) -> str:
    """Deterministic cache key for one canonical URL's oEmbed record."""
    return stage_cache_key(canonical_url, {"kind": "oembed", "schema": OEMBED_CACHE_SCHEMA})


def is_record_fresh(record: OEmbedRecord, now: datetime.datetime) -> bool:
    """True when the record is a successful fetch within the metadata TTL."""
    if record.outcome != "complete":
        return False
    fetched = datetime.datetime.fromisoformat(record.fetched_at.replace("Z", "+00:00"))
    age = (now - fetched).total_seconds()
    return 0 <= age < record.ttl_seconds


class OEmbedCache:
    """oEmbed records persisted under the state cache, keyed by URL."""

    def __init__(self, state: StateRoot) -> None:
        self._cache = CacheStore(state)

    def get(self, canonical_url: str) -> OEmbedRecord | None:
        path = self._cache.path_for(oembed_cache_key(canonical_url), ".json")
        if not path.exists():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(document, dict) or "record" not in document:
            return None
        try:
            return OEmbedRecord.from_dict(document["record"])
        except (ExtractorError, TypeError, ValueError):
            return None

    def get_fresh(
        self, canonical_url: str, now: datetime.datetime
    ) -> OEmbedRecord | None:
        record = self.get(canonical_url)
        if record is not None and is_record_fresh(record, now):
            return record
        return None

    def put(self, record: OEmbedRecord) -> Path:
        envelope = {"schema": OEMBED_CACHE_SCHEMA, "record": record.to_dict()}
        return self._cache.put_bytes(
            oembed_cache_key(record.canonical_url),
            json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            + b"\n",
            ".json",
        )
