"""One OpenAI-compatible synthesis provider (Phase 0, P0-FR-13..17).

A small provider-neutral adapter speaking the OpenAI-compatible
``POST {base_url}/chat/completions`` protocol. It exists ONLY to turn
already-sanitized ``video.md``/``audio.md`` evidence text into a
candidate synthesis document; the EXISTING strict
``SynthesisDocument`` validation in :mod:`tiktok_ingest.synthesis`
remains the only path to persistence (the adapter result is passed
through ``run_synthesis_stage(document_fn=...)``).

Hard rules encoded here (Phase 0 PRD section 8 / section 9):

- Configuration is explicit and runtime-only: base URL, API key and
  model come from the documented environment variables. Values are
  never persisted, never printed and scrubbed from every error string.
- The request sends ONLY sanitized evidence text derived from
  ``video.md`` and ``audio.md``. Media, frames, local filesystem
  paths, browser state and credentials are stripped by
  :func:`sanitize_evidence_text` BEFORE anything is put on the wire.
- Tool use, provider-side URL retrieval and command execution are
  disabled in the request (``tools: []``, ``tool_choice: "none"``).
- Structured JSON is requested (``response_format: json_object``) and
  the response content must parse to a JSON object; full schema
  validation happens downstream in the existing stage.
- Exactly ONE request attempt: timeout, HTTP error, provider refusal
  and malformed output raise :class:`SynthesisApiError` (scrubbed).
  There is no retry, and a failure is a RESUMABLE synthesis stop —
  never a manufactured or auto-filled classification.
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

# Documented configuration convention (Phase 0). Values never enter Git;
# reports show only the endpoint ORIGIN (scheme://host) and the model.
ENV_BASE_URL = "TIKTOK_INGEST_TEXT_API_BASE_URL"
ENV_API_KEY = "TIKTOK_INGEST_TEXT_API_KEY"
ENV_MODEL = "TIKTOK_INGEST_TEXT_MODEL"
ENV_TIMEOUT_SECONDS = "TIKTOK_INGEST_TEXT_API_TIMEOUT_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 120.0

# Bounded evidence text per modality (characters). Larger evidence is a
# bounded truncation that is REPORTED in the payload header, never a
# silent cut.
MAX_EVIDENCE_CHARS_PER_MODALITY = 100_000


class SynthesisApiError(RuntimeError):
    """Scrubbed provider failure; a resumable synthesis stop."""


@dataclasses.dataclass(frozen=True)
class TextApiConfig:
    """Explicit runtime configuration; never persisted, never printed."""

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise SynthesisApiError("text API base URL is required")
        match = re.match(r"^https?://", self.base_url.strip())
        if match is None:
            raise SynthesisApiError(
                "text API base URL must be an http(s) URL, got an "
                "unacceptable value (value suppressed)"
            )
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise SynthesisApiError("text API key is required (value suppressed)")
        if not isinstance(self.model, str) or not self.model.strip():
            raise SynthesisApiError("text API model is required (value suppressed)")
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    def origin(self) -> str:
        """Display origin WITHOUT any credential material.

        scheme://host[/path] of the base URL — the operator sees exactly
        which endpoint will receive evidence text, never the key.
        """
        parts = urllib.parse.urlsplit(self.base_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if parts.path and parts.path != "/":
            origin += parts.path
        return origin

    def describe(self) -> str:
        return f"{self.origin()} (model {self.model})"


def load_text_api_config(
    env: Mapping[str, str],
) -> tuple[TextApiConfig | None, list[str]]:
    """Load configuration from the environment.

    Returns ``(config, missing_env_names)``. Missing names are echoed as
    NAMES ONLY — values are never logged. An empty/partial environment
    yields ``(None, names)`` so callers can treat synthesis as blocked
    (missing configuration) without ever touching the network.
    """
    missing = [
        name
        for name in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL)
        if not str(env.get(name, "")).strip()
    ]
    if missing:
        return None, missing
    timeout_raw = str(env.get(ENV_TIMEOUT_SECONDS, "")).strip()
    timeout = DEFAULT_TIMEOUT_SECONDS
    if timeout_raw:
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise SynthesisApiError(
                f"{ENV_TIMEOUT_SECONDS} must be a number of seconds "
                f"(got an unacceptable value: {exc})"
            ) from exc
    try:
        return (
            TextApiConfig(
                base_url=str(env[ENV_BASE_URL]),
                api_key=str(env[ENV_API_KEY]),
                model=str(env[ENV_MODEL]),
                timeout_seconds=timeout,
            ),
            [],
        )
    except SynthesisApiError:
        raise


# --------------------------------------------------------------------------
# Evidence sanitization (BEFORE anything is placed on the wire)
# --------------------------------------------------------------------------

# Local-filesystem path shapes never allowed on the wire. Evidence
# markdown legitimately references frame/audio artifacts by path; those
# references describe local machine layout and stay local.
_PATH_RE = re.compile(
    r"(?:(?:/[\w.@-]+)+|~[\w.@/-]*|file://\S+)"
)

# Credential-looking strings (same KINDS as the synthesis scrub; here
# they are REPLACED, because this text leaves the machine).
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)),
    ("password_assignment", re.compile(r"\bpass(?:word|wd)\s*[=:]\s*\S+", re.IGNORECASE)),
    ("api_key_assignment", re.compile(r"\bapi[_-]?key\s*[=:]\s*\S+", re.IGNORECASE)),
    ("secret_assignment", re.compile(r"\b(?:client[_-]?secret|secret|token)\s*[=:]\s*\S+", re.IGNORECASE)),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("authorization_header", re.compile(r"\bauthorization\s*:\s*\S+", re.IGNORECASE)),
)


def sanitize_evidence_text(text: str) -> str:
    """Strip paths and credential-looking strings from evidence text.

    Purely textual: labelled metadata sections are KEPT (they are part
    of the sanitized evidence contract), while local filesystem paths
    are replaced with ``[path removed]`` and credential-looking strings
    with ``[redacted:<kind>]``.
    """
    sanitized = _PATH_RE.sub("[path removed]", text)
    for kind, pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(f"[redacted:{kind}]", sanitized)
    return sanitized


def _bounded(text: str, label: str) -> tuple[str, str | None]:
    if len(text) <= MAX_EVIDENCE_CHARS_PER_MODALITY:
        return text, None
    return (
        text[:MAX_EVIDENCE_CHARS_PER_MODALITY],
        f"{label} truncated at {MAX_EVIDENCE_CHARS_PER_MODALITY} chars "
        "(bounding the outbound payload; full evidence stays local)",
    )


def build_evidence_payload(video_md: str, audio_md: str) -> str:
    """The ONLY text that may reach the provider, assembled and bounded.

    Structure is explicit and labelled so the provider can distinguish
    modalities; the truncation note (if any) is in-band and honest.
    """
    video_text, video_note = _bounded(video_md.strip(), "video.md evidence")
    audio_text, audio_note = _bounded(audio_md.strip(), "audio.md evidence")
    sections = [
        "### video.md evidence (sanitized; paths and secrets removed)",
        video_text,
    ]
    if video_note:
        sections.append(f"[note: {video_note}]")
    sections += [
        "### audio.md evidence (sanitized; paths and secrets removed)",
        audio_text,
    ]
    if audio_note:
        sections.append(f"[note: {audio_note}]")
    return "\n\n".join(sections)


# --------------------------------------------------------------------------
# Request construction (tools/retrieval/execution disabled)
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a careful evidence synthesizer for a local media-ingest pipeline.
You receive ONLY sanitized visual and audio evidence text derived from one short video.
Return ONE JSON object and nothing else, matching this exact schema:
{
  "classification": {"root": one of ["tecnología","recurso","educativo","consumo","ruido"], "subgroup": string, "confidence": number 0..1},
  "entities": [{"name": string, "candidate_urls": [string, ...]}, ...],
  "claims": [{"claim": string, "source": one of ["caption","audio","visual","metadata"], "evidence": [string, ...], "verdict": null or one of ["confirmed","overstated","unverifiable","contradicted","unnamed"], "note": string or null, "candidate_urls": [string, ...]}, ...],
  "actionables": [string, ...],
  "fit": string,
  "model": string (your own model identifier),
  "generated_at": string (ISO-8601 timestamp)
}
Rules: use ONLY what the evidence text supports; never invent classifications, claims, entities or URLs; mark uncertainty honestly; claim sources must say where each claim came from; candidate_urls may be empty."""


def build_request_messages(payload: str) -> list[dict[str, str]]:
    """Messages for the chat-completions request (system + user only)."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": payload},
    ]


def build_request_body(config: TextApiConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    """The exact JSON body: structured JSON requested, tools DISABLED."""
    return {
        "model": config.model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "tool_choice": "none",
        "tools": [],
        "stream": False,
        "temperature": 0,
    }


# --------------------------------------------------------------------------
# One request, no retry, scrubbed failures
# --------------------------------------------------------------------------


def scrub_secrets(text: str, config: TextApiConfig | None = None) -> str:
    """Remove the configured key and credential-looking text from a string."""
    cleaned = text
    if config is not None and config.api_key:
        cleaned = cleaned.replace(config.api_key, "[redacted:api-key]")
    for kind, pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(f"[redacted:{kind}]", cleaned)
    return cleaned


def default_api_transport(
    request: urllib.request.Request, *, timeout: float
):  # pragma: no cover - real network boundary, faked in tests
    return urllib.request.urlopen(request, timeout=timeout)


def request_json_document(
    config: TextApiConfig,
    messages: list[dict[str, str]],
    *,
    transport: Callable[..., Any] = default_api_transport,
) -> dict[str, Any]:
    """ONE chat-completions request returning a parsed JSON object.

    Failures (timeout, HTTP error, refusal, malformed output) raise
    :class:`SynthesisApiError` with scrubbed messages. No retry, ever:
    the caller surfaces a resumable synthesis stop.
    """
    url = f"{config.base_url}/chat/completions"
    body = json.dumps(build_request_body(config, messages)).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - explicit operator config
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
    )
    try:
        with transport(request, timeout=config.timeout_seconds) as response:
            status = getattr(response, "status", 200)
            raw = response.read().decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SynthesisApiError(
            scrub_secrets(
                f"text API request failed ({type(exc).__name__}: {exc}); "
                "this is a resumable synthesis stop — no classification "
                "was manufactured",
                config,
            )
        ) from exc
    if status != 200:
        raise SynthesisApiError(
            scrub_secrets(
                f"text API returned HTTP {status}; this is a resumable "
                "synthesis stop — no classification was manufactured",
                config,
            )
        )
    try:
        document = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise SynthesisApiError(
            scrub_secrets(
                f"text API response is not valid JSON ({exc}); this is a "
                "resumable synthesis stop — no classification was "
                "manufactured",
                config,
            )
        ) from exc
    choices = document.get("choices") if isinstance(document, dict) else None
    if not isinstance(choices, list) or not choices:
        raise SynthesisApiError(
            "text API response carries no choices; this is a resumable "
            "synthesis stop — no classification was manufactured"
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise SynthesisApiError(
            "text API response carries no message; this is a resumable "
            "synthesis stop — no classification was manufactured"
        )
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        raise SynthesisApiError(
            scrub_secrets(
                "the text API refused the request (provider refusal); this "
                "is a resumable synthesis stop — no classification was "
                "manufactured",
                config,
            )
        )
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise SynthesisApiError(
            "text API response content is empty; this is a resumable "
            "synthesis stop — no classification was manufactured"
        )
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise SynthesisApiError(
            scrub_secrets(
                f"text API message content is not valid JSON ({exc}); this "
                "is a resumable synthesis stop — no classification was "
                "manufactured",
                config,
            )
        ) from exc
    if not isinstance(parsed, dict):
        raise SynthesisApiError(
            "text API message content is not a JSON object; this is a "
            "resumable synthesis stop — no classification was manufactured"
        )
    return parsed


def read_evidence_text(run_dir: Path | str) -> tuple[str, str]:
    """Read the sanitized ``video.md``/``audio.md`` text from a run dir."""
    run_dir = Path(run_dir)
    video_path = run_dir / "video.md"
    audio_path = run_dir / "audio.md"
    missing = [p.name for p in (video_path, audio_path) if not p.is_file()]
    if missing:
        raise SynthesisApiError(
            "sanitized evidence incomplete in the run directory (missing: "
            + ", ".join(missing)
            + "); the synthesis stage requires both modalities"
        )
    return (
        video_path.read_text(encoding="utf-8"),
        audio_path.read_text(encoding="utf-8"),
    )


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "ENV_TIMEOUT_SECONDS",
    "MAX_EVIDENCE_CHARS_PER_MODALITY",
    "SynthesisApiError",
    "TextApiConfig",
    "build_evidence_payload",
    "build_request_body",
    "build_request_messages",
    "load_text_api_config",
    "read_evidence_text",
    "request_json_document",
    "sanitize_evidence_text",
    "scrub_secrets",
]
