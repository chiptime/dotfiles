"""Adopted defaults and operating limits for the TikTok ingest MVP.

This module encodes the user-adopted decisions (MVP-PRD section 5) as plain
constants. It executes nothing: the isolation and resource-gate values are
configuration only until a later milestone implements and tests them.

Sources:
- P1: default vision model (MVP-PRD section 1, adopted).
- P2: transcript policy, full Spanish audio primary (adopted override).
- P3: hybrid sampling budget (adopted).
- P4: operating budgets (adopted).
- Isolation/gates: carried forward from MVP-PRD section 5 runtime gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Models and language (P1, P2)
# --------------------------------------------------------------------------

# Adopted default vision model.
VISION_MODEL = "qwen3.5:9b"

# Reserved for high-density frame rereads only. It is NOT the default and is
# never selected automatically (no automatic escalation between models).
HIGH_DENSITY_VISION_MODEL = "qwen3.8:27b"

WHISPER_MODEL = "large-v3"

# Fixed Spanish-only scope. No per-job language selection in the MVP.
WHISPER_LANGUAGE = "es"

# Full Spanish audio through large-v3 is the primary transcript; native
# subtitle tracks are stored as auxiliary evidence and never skip ASR.
TRANSCRIPT_POLICY = "full-audio-primary-subtitles-auxiliary"

# --------------------------------------------------------------------------
# Sampling (P3)
# --------------------------------------------------------------------------

SAMPLING_STRATEGY = "hybrid"

# Baseline frame budget for a supported clip (upper bound, not a target).
MAX_BASELINE_FRAMES = 64

# Bounded crop / nearby-frame rereads within the same vision residency.
MAX_TARGETED_REREADS = 8

# --------------------------------------------------------------------------
# Milestone 2: hybrid sampler parameters (benchmark methodology section 3.2)
# --------------------------------------------------------------------------

# Bump when the sampler rules change so recorded prepare-stage fingerprints
# distinguish sampler generations.
#
# Version 2: uniform candidates are snapped onto REAL stream frame PTS
# (from the integrity-pass showinfo); the fps-grid timestamps of a 59.94
# fps stream almost never coincide with actual frames.
SAMPLING_RULES_VERSION = "2"

# Uniform-coverage scan density (FPS scan for candidate generation only;
# frames are later thinned by the minimum gap below).
SAMPLING_UNIFORM_FPS = 2

# Minimum seconds between retained uniform-coverage frames.
SAMPLING_UNIFORM_MIN_GAP_SECONDS = 4.0

# ffmpeg scene-change detection threshold.
SAMPLING_SCENE_THRESHOLD = 0.3

# Lower-third text-region scene-change threshold.
SAMPLING_TEXT_REGION_THRESHOLD = 0.15

# Candidate timestamps within this window merge into one frame.
SAMPLING_DEDUP_WINDOW_SECONDS = 0.08

# Upper bound of text-region crop CANDIDATES that are marked (metadata only;
# crops are never rendered or inferred in milestone 2).
SAMPLING_CROP_CANDIDATE_CAP = 16

# Lower-third band geometry used for text-region detection and recorded as
# the crop coordinate proposal for targeted rereads (milestone 3).
SAMPLING_CROP_HEIGHT_FRACTION = 0.35
SAMPLING_CROP_Y_OFFSET_FRACTION = 0.65

# A gap between retained frames longer than this is exposed as an uncovered
# interval in the sampling manifest (never silently accepted).
SAMPLING_MAX_COVERAGE_GAP_SECONDS = 4.0

# --------------------------------------------------------------------------
# Milestone 2: pinned extractor and runtime locations (outside Git)
# --------------------------------------------------------------------------

# Pinned public extractor (benchmark methodology section 2). The installed
# version must match this pin exactly before any execution.
EXTRACTOR_PACKAGE = "yt-dlp"
EXTRACTOR_PIN = "2026.08.19"
EXTRACTOR_REQUIREMENT = f"{EXTRACTOR_PACKAGE}=={EXTRACTOR_PIN}"

# Runtime binaries and virtual environments live OUTSIDE Git (dotfiles
# doctrine): the repository pins the version and the gate rules, the
# installed environment is machine-local state under the share root.
SHARE_ROOT = Path.home() / ".local" / "share" / "tiktok-ingest"
EXTRACTOR_VENV_DIR = SHARE_ROOT / "extractor-venv"
EXTRACTOR_GATE_PROBE_PATH = SHARE_ROOT / "extractor-gate-probe.py"
EXTRACTOR_RUNNER_PATH = SHARE_ROOT / "extractor-runner.py"

# Gate rules generation; recorded in extraction fingerprints so a rule
# change invalidates recorded extractions.
#
# Version 2: verified against the installed pinned source (2026.08.19).
# The challenge area's real enforcement point is
# ``TikTokBase._solve_challenge_and_set_cookies`` (tiktok.py); the
# underscore-fragment candidates below are retained for forward
# compatibility — the probe tolerates missing candidates and requires at
# least one located symbol per area.
EXTRACTOR_GATE_RULES_VERSION = "2"

# Candidate attribute names for the protected extractor areas. The gate
# probe searches these names on the TikTok extractor classes at runtime and
# hard-blocks every symbol it locates. An area with ZERO located symbols
# fails the gate: the protected paths cannot be proven blocked (fail
# closed). The lists are verified against the pinned source on the first
# authorized run.
EXTRACTOR_GATE_APP_API_SYMBOLS: tuple[str, ...] = (
    "_call_api",
    "_call_api_impl",
    "_build_api_request",
)
EXTRACTOR_GATE_CHALLENGE_SYMBOLS: tuple[str, ...] = (
    "_solve_challenge_and_set_cookies",
    "_solve_challenge",
    "_challenge_solver",
    "_generate_bogus",
    "_get_bogus",
    "_verify_fp",
)

# Command-line options that transfer ambient browser credentials to the
# extractor. The wrapper refuses them unconditionally; no ambient cookies,
# no cookie export (PRD section 6.2).
EXTRACTOR_FORBIDDEN_OPTIONS: tuple[str, ...] = (
    "--cookies",
    "--cookie",
    "--cookiefile",
    "--cookies-from-browser",
)

# Public oEmbed metadata endpoint (PRD section 5.1.1, S1). Metadata only:
# never a transcript or media source.
OEMBED_ENDPOINT = "https://www.tiktok.com/oembed"

# --------------------------------------------------------------------------
# Milestone 2: FFmpeg container image
# --------------------------------------------------------------------------

# CPU-only FFmpeg/ffprobe execute inside this existing local image via
# podman, always network-isolated. The benchmark recorded version
# 4.4.2-0ubuntu0.22.04.1 with image ID prefix below; the pipeline resolves
# and records the FULL immutable image ID read-only at runtime and never
# invents a digest from the prefix (MVP-PRD section 3.B).
FFMPEG_IMAGE_REF = "localhost/voice-assistant_whisper:latest"
FFMPEG_IMAGE_ID_PREFIX = "51b152706d"

# --------------------------------------------------------------------------
# Budgets (P4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Budgets:
    """Product limits; proposals bounded by policy, not measured capacity."""

    batch_pilot_clips: int = 5
    media_max_duration_seconds: int = 600  # 10 minutes per clip
    media_max_bytes: int = 250 * 1024 * 1024  # 250 MiB per clip
    fetch_deadline_seconds: int = 300  # 5 minutes
    http_max_metadata_concurrent: int = 2
    metadata_ttl_seconds: int = 24 * 3600  # 24 hours
    cpu_prep_deadline_seconds: int = 1200  # 20 minutes
    cpu_prep_max_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB derived outputs
    vision_stage_deadline_seconds: int = 600  # 10 minutes
    audio_stage_deadline_seconds: int = 600  # 10 minutes
    working_cache_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB


BUDGETS = Budgets()

# --------------------------------------------------------------------------
# Isolation and resource gates — configuration only, NOT implemented here.
# Any later execution requires separate authorization (MVP-PRD sections 5-7).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IsolationGates:
    """Constants describing the required isolation and stop rules.

    ``isolated_ollama_version`` is the verified isolated Ollama used for
    Qwen, bound to loopback with isolated ``HOME`` and ``OLLAMA_MODELS``.
    ``global_ollama_version`` is the host install: it is incompatible with
    qwen3.5+ and must never be touched or used for this pipeline.
    """

    isolated_ollama_version: str = "0.34.1"
    global_ollama_version: str = "0.14.2"
    # Require at least this much free VRAM before every Qwen load.
    free_vram_gate_mib: int = 20480
    # Swap I/O delta per phase, measured from the immediate phase baseline.
    swap_delta_limit_mib: int = 512
    # Per-model VRAM delta limits from the benchmark.
    vram_delta_limit_mib: dict[str, int] = field(
        default_factory=lambda: {"qwen3.5:9b": 12288}
    )
    # Whisper and vision models are never GPU-resident simultaneously.
    allow_gpu_co_residency: bool = False
    # Unload verification: post-phase VRAM must return to within this much
    # of the session baseline (methodology section 5: +71 MiB observed
    # against a 2,443 MiB baseline; tolerance ±512 MiB).
    session_vram_tolerance_mib: int = 512


GATES = IsolationGates()

# --------------------------------------------------------------------------
# Milestone 3: isolated Ollama runtime (methodology sections 2 and 5)
# --------------------------------------------------------------------------

# Official 0.34.1 linux archive identity. The sha256 AND the byte size are
# BOTH verified exactly by ``install-ollama`` before extraction; any
# mismatch is refused with no retry (methodology sections 2 and 8.7-8.8).
# The URL is the official release location for the pinned version; it is
# provenance, while the recorded hash/size pair is the authority.
OLLAMA_ARCHIVE_URL = (
    "https://github.com/ollama/ollama/releases/download/"
    "v0.34.1/ollama-linux-amd64.tar.zst"
)
OLLAMA_ARCHIVE_SHA256 = (
    "f361dc3992ec07e4ad429f4bb2d10d4663ba2c295f9a9a688c7d52f4ba650034"
)
OLLAMA_ARCHIVE_SIZE_BYTES = 1429323296

# Isolated runtime tree under the share root, outside Git. HOME and
# OLLAMA_MODELS are SEPARATE subdirectories: the global store must never be
# touched, and HOME itself must be isolated or the isolated server rewrites
# the real ``~/.ollama`` cache (methodology section 8.2).
OLLAMA_DIR = SHARE_ROOT / "ollama"
OLLAMA_BIN_PATH = OLLAMA_DIR / "bin" / "ollama"
OLLAMA_HOME_DIR = OLLAMA_DIR / "home"
OLLAMA_MODELS_DIR = OLLAMA_DIR / "models"
OLLAMA_SERVER_LOG_PATH = OLLAMA_DIR / "server.log"
OLLAMA_PID_PATH = OLLAMA_DIR / "ollama.pid"

# Loopback-only binding on the benchmark port (never 11434: the global
# server owns it).
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11435
OLLAMA_API_BASE = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"

# Read-only view of the host Ollama store: snapshot source for the
# bit-identical before/after check and blob source for ``import-model``.
# NEVER written by this pipeline.
GLOBAL_OLLAMA_STORE = Path.home() / ".ollama" / "models"

# --------------------------------------------------------------------------
# Milestone 3: reused whisper WS service endpoints (PRD sections 4.1, 6.4)
# --------------------------------------------------------------------------

WHISPER_WS_HOST = "127.0.0.1"
WHISPER_WS_PORT = 8767
WHISPER_WS_PATH = "/ws/transcribe"
WHISPER_HEALTH_PORT = 8766
WHISPER_WS_URL = f"ws://{WHISPER_WS_HOST}:{WHISPER_WS_PORT}{WHISPER_WS_PATH}"
WHISPER_HEALTH_URL = f"http://{WHISPER_WS_HOST}:{WHISPER_HEALTH_PORT}/health"

# Unconditional no-speech recording horizon for partial arrivals: coarse
# chunk provenance is derived from chunk counts at observation time, never
# from word alignment (PRD section 6.4).

# --------------------------------------------------------------------------
# Milestone 4: bounded verification retrieval (MVP-PRD section 3.D)
#
# Pure configuration: no endpoints, no API keys, no model identifiers. The
# pipeline stays credential-free; verification retrieval only ever targets
# the operator/model-curated first-party candidate URLs recorded in the
# synthesis document, through these hard caps.
# --------------------------------------------------------------------------

# Allowed URL schemes for verification retrieval: public HTTP(S) only.
# Everything else (file://, ftp:, data:, ws:, ...) is refused.
VERIFY_ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https")

# Per-response body size cap. A larger body is a FAILED retrieval with an
# explicit reason, never a silent truncation.
VERIFY_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB

# Per-request timeout in seconds.
VERIFY_TIMEOUT_SECONDS = 15

# Maximum redirect hops per retrieval. Every hop target passes through the
# same safe-URL validation BEFORE it is fetched; a refused target fails that
# claim's retrieval and the remaining claims continue.
VERIFY_MAX_REDIRECTS = 3

# User-Agent sent with verification retrieval requests.
VERIFY_USER_AGENT = "tiktok-ingest/+local"

# Bump when verification rules (verdict comparison thresholds, passage
# matching, verdict identity semantics) change, so recorded verify-stage
# fingerprints distinguish rule generations and resume invalidates stale
# verdicts. Version 2 separated "unnamed" (entity not identified) from
# "unverifiable"/no_candidate_urls (entity identified, no sources).
VERIFY_RULES_VERSION = "2"
