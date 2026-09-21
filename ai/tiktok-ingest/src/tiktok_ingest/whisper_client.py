"""Whisper WS client and audio stage (Milestone 3).

Implements the recorded contract from PRD section 6.4 EXACTLY — no
invented protocol fields:

    ws://127.0.0.1:8767/ws/transcribe        (host-published port)
    → (optional) text {"type":"initial_prompt"}   — omitted by this MVP
    ← {"type":"ack",...}
    → binary WAV frames (16 kHz mono s16le) in bounded chunks
    ← {"type":"partial","text":"<chunk>","accumulated":"<session total>"}

    health: GET http://127.0.0.1:8766/health

The WebSocket client is implemented BY HAND on the stdlib socket (RFC 6455
client handshake + framing): text frames carry JSON, binary frames carry
raw WAV bytes. Completion is detected ONLY from what the service actually
does: the documented contract records ack/partial messages and nothing
else, so this client treats "connection closed after partials" as
completion, records every observed message type, and never assumes an
undocumented final-message field.

Discipline (PRD sections 4.1 and 6.4): one connection per job, no
multiplexing, no automatic retry. Language is the service-level ``es``
environment default; per-message language control does NOT exist. Health
precheck fails closed with a ``blocked`` outcome.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import socket
import struct
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import config
from .contracts import JobManifest, StageOutcome, StageRecord, StageResult, utc_now_iso
from .prepare import StageClock
from .prepare import StageDeadlineExceeded
from .state import Blocklist, Processed, StateRoot, atomic_write_bytes
from .validation import sha256_of_file

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Bounded wait for the documented ack before audio streaming starts. The
# contract ties ack to the optional initial_prompt; real-service observation
# (2026-09-20) showed no ack when no prompt is sent, so silence here is a
# recorded observation, not an error.
_ACK_WAIT_SECONDS = 5.0

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

# The ONLY message types the recorded contract documents. Anything else is
# recorded as observed but never trusted as a completion signal.
KNOWN_MESSAGE_TYPES: tuple[str, ...] = ("ack", "partial")


class WhisperProtocolError(RuntimeError):
    """Raised when the service violates the recorded wire contract."""


class HealthCheckFailed(RuntimeError):
    """Raised when the whisper health endpoint is unreachable (blocked)."""


# --------------------------------------------------------------------------
# RFC 6455 framing (pure byte functions; injectable mask key for tests)
# --------------------------------------------------------------------------


def build_handshake(
    host: str, port: int, path: str, key: str | None = None
) -> str:
    """The RFC 6455 client handshake request (text form)."""
    if key is None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )


def accept_key_for(key: str) -> str:
    """RFC 6455 section 1.3: base64(SHA-1(key + GUID))."""
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()  # noqa: S324 - RFC 6455 mandates SHA-1 here
    return base64.b64encode(digest).decode("ascii")


def parse_handshake_response(raw: bytes, key: str) -> dict[str, str]:
    """Validate the 101 response and its Sec-WebSocket-Accept header."""
    header_blob, separator, _ = raw.partition(b"\r\n\r\n")
    if not separator:
        raise WhisperProtocolError("handshake response is incomplete")
    lines = header_blob.decode("latin-1").split("\r\n")
    status = lines[0] if lines else ""
    if " 101 " not in f" {status} ":
        raise WhisperProtocolError(
            f"handshake did not switch protocols (101): {status!r}"
        )
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    accept = headers.get("sec-websocket-accept")
    if accept != accept_key_for(key):
        raise WhisperProtocolError(
            f"Sec-WebSocket-Accept mismatch: {accept!r} != expected"
        )
    return headers


def encode_frame(
    opcode: int, payload: bytes, mask_key: bytes | None = None
) -> bytes:
    """Encode one client frame: FIN set, client-to-server MUST be masked."""
    if mask_key is None:
        mask_key = os.urandom(4)
    if len(mask_key) != 4:
        raise WhisperProtocolError("mask key must be exactly 4 bytes")
    first = 0x80 | opcode
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, 0x80 | length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, length)
    masked = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
    return header + mask_key + masked


@dataclasses.dataclass(frozen=True)
class WsFrame:
    """One decoded WebSocket frame."""

    fin: bool
    opcode: int
    payload: bytes


def decode_frame(buffer: bytes) -> tuple[WsFrame, bytes] | None:
    """Decode the first frame in ``buffer``; ``None`` while incomplete.

    Tolerates server masking (a correct server masks, but the decoder does
    not depend on it) and returns the remaining bytes alongside the frame.
    """
    if len(buffer) < 2:
        return None
    first, second = buffer[0], buffer[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    offset = 2
    if length == 126:
        if len(buffer) < offset + 2:
            return None
        length = struct.unpack("!H", buffer[offset : offset + 2])[0]
        offset += 2
    elif length == 127:
        if len(buffer) < offset + 8:
            return None
        length = struct.unpack("!Q", buffer[offset : offset + 8])[0]
        offset += 8
    mask_key = b""
    if masked:
        if len(buffer) < offset + 4:
            return None
        mask_key = buffer[offset : offset + 4]
        offset += 4
    if len(buffer) < offset + length:
        return None
    payload = buffer[offset : offset + length]
    if masked:
        payload = bytes(
            byte ^ mask_key[index % 4] for index, byte in enumerate(payload)
        )
    return WsFrame(fin=fin, opcode=opcode, payload=payload), buffer[offset + length :]


# --------------------------------------------------------------------------
# Session over an injectable socket
# --------------------------------------------------------------------------


class _WouldBlock(Exception):
    """Internal: recv would block (drain window elapsed)."""


@dataclasses.dataclass(frozen=True)
class WsMessage:
    """One assembled application message."""

    kind: str  # "text" | "binary" | "close"
    payload: bytes
    close_code: int | None = None


SocketLike = Any  # sendall/recv/close/settimeout, socket-shaped
SocketFactory = Callable[[str, int], SocketLike]
MaskKeyProvider = Callable[[int], bytes]


def default_socket_factory(host: str, port: int) -> SocketLike:
    return socket.create_connection((host, port), timeout=10.0)


class TranscribeSession:
    """One RFC 6455 client session (one connection per job, by contract)."""

    def __init__(
        self,
        sock: SocketLike,
        *,
        mask_key_provider: MaskKeyProvider = os.urandom,
    ) -> None:
        self.sock = sock
        self._mask_key_provider = mask_key_provider
        self._buffer = b""

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        path: str,
        *,
        socket_factory: SocketFactory,
        mask_key_provider: MaskKeyProvider = os.urandom,
        handshake_key: str | None = None,
    ) -> "TranscribeSession":
        key = handshake_key or base64.b64encode(os.urandom(16)).decode("ascii")
        sock = socket_factory(host, port)
        session = cls(sock, mask_key_provider=mask_key_provider)
        sock.sendall(build_handshake(host, port, path, key=key).encode("ascii"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                raise WhisperProtocolError(
                    "connection closed during the WebSocket handshake"
                )
            raw += chunk
        parse_handshake_response(raw, key)
        session._buffer = raw.partition(b"\r\n\r\n")[2]
        return session

    # -- sending ------------------------------------------------------------

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        self.sock.sendall(encode_frame(opcode, payload, self._mask_key_provider(4)))

    def send_text(self, text: str) -> None:
        self._send_frame(_OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self._send_frame(_OP_BINARY, data)

    # -- receiving ----------------------------------------------------------

    def _recv_more(self) -> bytes:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("connection closed by the service")
        return chunk

    def _next_frame(self) -> WsFrame:
        while True:
            decoded = decode_frame(self._buffer)
            if decoded is not None:
                frame, self._buffer = decoded
                return frame
            try:
                self._buffer += self._recv_more()
            except TimeoutError as exc:
                raise _WouldBlock from exc

    def receive_message(self) -> WsMessage | None:
        """Read one assembled message; ``None`` when the service closes.

        Ping frames are answered automatically (RFC 6455). A close frame is
        answered once and reported as ``None`` (the service signaled the
        end). Fragmented messages are assembled across continuation frames.
        """
        fragments: list[bytes] = []
        fragment_opcode: int | None = None
        while True:
            frame = self._next_frame()
            if frame.opcode == _OP_PING:
                self._send_frame(_OP_PONG, frame.payload)
                continue
            if frame.opcode == _OP_CLOSE:
                code = (
                    struct.unpack("!H", frame.payload[:2])[0]
                    if len(frame.payload) >= 2
                    else None
                )
                try:
                    self._send_frame(_OP_CLOSE, frame.payload[:2])
                except (OSError, WhisperProtocolError):
                    pass  # the peer may already be gone; the close stands
                return WsMessage(kind="close", payload=frame.payload, close_code=code)
            if frame.opcode in (_OP_TEXT, _OP_BINARY):
                if frame.fin:
                    kind = "text" if frame.opcode == _OP_TEXT else "binary"
                    return WsMessage(kind=kind, payload=frame.payload)
                fragment_opcode = frame.opcode
                fragments = [frame.payload]
                continue
            if frame.opcode == _OP_CONT:
                if fragment_opcode is None:
                    raise WhisperProtocolError(
                        "continuation frame without a started message"
                    )
                fragments.append(frame.payload)
                if frame.fin:
                    kind = (
                        "text" if fragment_opcode == _OP_TEXT else "binary"
                    )
                    return WsMessage(kind=kind, payload=b"".join(fragments))
                continue
            raise WhisperProtocolError(
                f"unsupported WebSocket opcode 0x{frame.opcode:02x}"
            )

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Transcription (one connection per job, no retry)
# --------------------------------------------------------------------------


@dataclasses.dataclass
class TranscriptionResult:
    """Everything the audio stage records about one WS transcription."""

    transcript: str = ""
    ack: dict[str, Any] | None = None
    partials: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    message_types_observed: list[str] = dataclasses.field(default_factory=list)
    completion_mode: str = "not_completed"
    close_frame: bool = False
    bytes_sent: int = 0
    chunk_count: int = 0
    unexpected_binary_frames: int = 0
    audio_seconds_total: float = 0.0


def default_urlopen(req: urllib.request.Request, timeout: float):  # type: ignore[no-untyped-def]
    """Default HTTP transport for the health precheck (stdlib)."""
    return urllib.request.urlopen(req, timeout=timeout)


def health_precheck(
    health_url: str = config.WHISPER_HEALTH_URL,
    *,
    urlopen: Callable[..., Any] = default_urlopen,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """GET the health endpoint; any failure is an explicit blocked condition."""
    req = urllib.request.Request(health_url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read(512).decode("utf-8", "replace")
    except OSError as exc:
        return False, f"whisper health endpoint unreachable: {exc}"
    if status != 200:
        return False, f"whisper health endpoint returned HTTP {status}"
    return True, body.strip()[:200] or "healthy"


def transcribe_audio(
    audio_path: Path | str,
    *,
    ws_host: str = config.WHISPER_WS_HOST,
    ws_port: int = config.WHISPER_WS_PORT,
    ws_path: str = config.WHISPER_WS_PATH,
    socket_factory: SocketFactory = default_socket_factory,
    mask_key_provider: MaskKeyProvider = os.urandom,
    initial_prompt: str | None = None,  # contract supports it; this MVP omits it
    handshake_key: str | None = None,  # injectable only so fixture tests can script the 101
    monotonic: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
    ack_wait_seconds: float = _ACK_WAIT_SECONDS,
) -> TranscriptionResult:
    """Send the normalized WAV over ONE connection; collect the response.

    REAL service contract, read from the actual implementation
    (voice-assistant/whisper-service/server.py, per PRD section 12.1):
    each BINARY message is written to a temp .wav file and transcribed
    WHOLE — request-response per message, not a streaming chunk protocol
    (the historical sketch was wrong about framing). ``max_size=None``
    exists precisely so one full WAV fits in a single message. There is
    NO final service message: the response to our single message is a
    ``partial``, and completion is CLIENT-initiated afterwards.

    ``deadline`` is an absolute monotonic timestamp; crossing it raises
    :class:`StageDeadlineExceeded` so the stage records a budget outcome.
    """
    audio = Path(audio_path).read_bytes()
    result = TranscriptionResult()
    sample_bytes_per_second = 2 * 16000  # 16 kHz mono signed 16-bit
    result.audio_seconds_total = len(audio) / sample_bytes_per_second

    def _check_deadline() -> None:
        if deadline is not None and monotonic() > deadline:
            raise StageDeadlineExceeded(
                f"audio stage deadline exceeded while transcribing "
                f"({result.bytes_sent} of {len(audio)} bytes sent); stopping "
                "with an explicit budget outcome"
            )

    session = TranscribeSession.connect(
        ws_host,
        ws_port,
        ws_path,
        socket_factory=socket_factory,
        mask_key_provider=mask_key_provider,
        handshake_key=handshake_key,
    )
    try:
        _check_deadline()
        # Optional initial prompt: the contract supports it; the MVP omits
        # it, so nothing is sent unless explicitly requested.
        if initial_prompt is not None:
            session.send_text(
                json.dumps({"type": "initial_prompt", "text": initial_prompt})
            )

        # The documented contract ties the ack to the OPTIONAL
        # initial_prompt ("→ (optional) initial_prompt ← ack"), so with no
        # prompt sent the service may legitimately stay silent first —
        # observed on the real service (2026-09-20): no ack without a
        # prompt. The ack wait is therefore OPTIONAL and BOUNDED: silence
        # within the window proceeds without one and is recorded as such;
        # a text frame must still be a valid ack (fail closed on anything
        # else); a binary frame before any ack is a contract violation.
        ack_deadline = monotonic() + max(0.0, ack_wait_seconds)
        first: WsMessage | None = None
        try:
            session.sock.settimeout(0.05)
        except (AttributeError, OSError):
            pass
        while first is None:
            _check_deadline()
            if monotonic() >= ack_deadline:
                break
            try:
                first = session.receive_message()
            except _WouldBlock:
                continue
        try:
            session.sock.settimeout(None)
        except (AttributeError, OSError):
            pass
        if first is None:
            # No ack observed before the first audio frame; proceed and
            # record the observation (service-contract verification).
            result.ack = None
        else:
            if first.kind != "text":
                raise WhisperProtocolError(
                    "expected the documented text ack (or silence) before "
                    "audio; got a binary frame"
                )
            ack_document_json = first.payload.decode("utf-8", "replace")
            try:
                ack_document = json.loads(ack_document_json)
            except json.JSONDecodeError as exc:
                raise WhisperProtocolError(
                    f"the documented ack frame is not valid JSON: {exc}"
                ) from exc
            if not isinstance(ack_document, dict) or ack_document.get("type") != "ack":
                observed = (
                    ack_document.get("type")
                    if isinstance(ack_document, dict)
                    else ack_document_json
                )
                raise WhisperProtocolError(
                    f"first text message was {observed!r}, expected 'ack'"
                )
            result.ack = ack_document
            result.message_types_observed.append("ack")

        # ONE binary message carries the COMPLETE normalized WAV; the
        # service transcribes each binary message whole and answers with a
        # partial (server.py: each message -> temp .wav -> model.transcribe).
        session.send_binary(audio)
        result.bytes_sent = len(audio)
        result.chunk_count = 1

        # Await the response to our single message. A close or raw EOF
        # before any partial means the service died mid-job: an explicit
        # failure without retry, never an empty-transcript completion.
        while not result.partials:
            _check_deadline()
            try:
                message = session.receive_message()
            except _WouldBlock:  # pragma: no cover - blocking mode here
                continue
            except ConnectionError as exc:
                raise WhisperProtocolError(
                    "connection closed by the service before a partial response"
                ) from exc
            if message is None:
                raise WhisperProtocolError(
                    "connection closed by the service before a partial response"
                )
            if not _absorb(message, result, 1, len(audio), sample_bytes_per_second):
                raise WhisperProtocolError(
                    "connection closed by the service before a partial response"
                )

        # Completion is CLIENT-initiated: the service keeps the session open
        # awaiting more audio and never sends a final message.
        try:
            session._send_frame(_OP_CLOSE, struct.pack("!H", 1000))
        except (OSError, WhisperProtocolError, AttributeError):
            pass  # the transcript is already secured; the close is courtesy
        result.completion_mode = "client_close_after_response"
    finally:
        session.close()

    last_accumulated = [
        str(partial.get("accumulated", "")) for partial in result.partials
    ]
    result.transcript = last_accumulated[-1] if last_accumulated else ""
    return result


def _absorb(
    message: WsMessage,
    result: TranscriptionResult,
    chunks_sent: int,
    chunk_bytes: int,
    sample_bytes_per_second: int,
) -> bool:
    """Fold one service message into the result; False stops the read loop."""
    if message.kind == "close":
        result.close_frame = True
        return False
    if message.kind == "binary":
        # The recorded contract sends TEXT partials only. Binary frames are
        # counted as unexpected, never interpreted.
        result.unexpected_binary_frames += 1
        return True
    try:
        document = json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        result.message_types_observed.append("<undecodable text frame>")
        return True
    message_type = document.get("type")
    result.message_types_observed.append(str(message_type))
    if message_type == "partial":
        approx_seconds = min(
            chunks_sent * (chunk_bytes / sample_bytes_per_second),
            result.audio_seconds_total,
        )
        result.partials.append(
            {
                "text": document.get("text", ""),
                "accumulated": document.get("accumulated", ""),
                "observed_after_chunk": chunks_sent,
                "approx_audio_seconds": round(approx_seconds, 3),
            }
        )
    # Unknown types are RECORDED (so a run log shows what actually
    # happened) but never treated as a documented completion signal.
    return True


# --------------------------------------------------------------------------
# audio.md assembly (pure)
# --------------------------------------------------------------------------


def assemble_audio_md(
    *,
    video_id: str,
    media_sha256: str,
    audio_sha256: str,
    result: TranscriptionResult,
    generated_at: str | None = None,
) -> str:
    """Build ``audio.md`` per PRD section 6.4 and MVP-PRD section 4."""
    generated_at = generated_at or utc_now_iso()
    timing_note = (
        "coarse chunk intervals from partial arrivals (never word alignment)"
        if result.partials
        else "timing_unavailable: the service exposed no partial arrivals"
    )
    empty_note = ""
    if not result.transcript.strip():
        empty_note = (
            "- no_speech_candidate: the analysed audio produced an EMPTY "
            "accumulated transcript. This is a transcription finding on "
            "analysed audio — it is NOT missing audio (missing audio fails "
            "the stage earlier, before any analysis).\n"
        )
    lines: list[str] = [
        f"# audio.md — {video_id}",
        "",
        "- media_sha256: " + media_sha256,
        "- audio_sha256: " + audio_sha256,
        "- method: reused whisper WebSocket service (faster-whisper "
        "large-v3, CUDA float16, beam 3) over "
        f"ws://{config.WHISPER_WS_HOST}:{config.WHISPER_WS_PORT}"
        f"{config.WHISPER_WS_PATH}",
        "- language: es — service-level environment default; per-message "
        "language control does NOT exist in the recorded contract",
        "- pass: full-audio single pass; one connection per job; no "
        "multiplexing; no automatic retry",
        f"- initial_prompt: omitted (contract supports it; this MVP does not send it)",
        f"- bytes_sent: {result.bytes_sent} in {result.chunk_count} bounded chunk(s)",
        f"- completion: {result.completion_mode} (observed; the recorded "
        "contract documents ack/partial only, so completion is the "
        "connection closing, never an assumed final-message field)",
        f"- message types observed: "
        + (", ".join(result.message_types_observed) or "none"),
        f"- timing: {timing_note}",
        f"- audio_seconds_total: {result.audio_seconds_total:.3f}",
    ]
    if result.unexpected_binary_frames:
        lines.append(
            f"- unexpected_binary_frames: {result.unexpected_binary_frames} "
            "(recorded, not interpreted — outside the documented contract)"
        )
    lines.append(empty_note.rstrip("\n") if empty_note else "")
    lines.extend(
        [
            "",
            "## Transcript (accumulated)",
            "",
        ]
    )
    if result.transcript.strip():
        lines.extend("> " + line if line else ">" for line in result.transcript.splitlines())
    else:
        lines.append("> [empty transcript — see no_speech_candidate above]")
    lines.extend(
        [
            "",
            "## Coarse chunk intervals (provenance only)",
            "",
        ]
    )
    if result.partials:
        for index, partial in enumerate(result.partials, start=1):
            approx = partial["approx_audio_seconds"]
            lines.append(
                f"- partial {index}: observed after chunk "
                f"{partial['observed_after_chunk']} (~{approx:.1f} s of audio "
                "sent); text length "
                f"{len(str(partial.get('text', '')))}"
            )
    else:
        lines.append("- timing_unavailable: no partial arrivals were observed")
    lines.extend(
        [
            "",
            "**Uncertainty.** Whisper can hallucinate, especially on poor "
            "audio: treat names/URLs from speech as recognition candidates, "
            "not facts. Non-Spanish content is outside the MVP scope and is "
            "never presented as validated. Coarse intervals are arrival "
            "provenance, never fabricated word alignment.",
            "",
        ]
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Stage orchestration
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AudioOutcome:
    """Result of one audio invocation, reusable by CLI and tests."""

    outcome: str
    reason: str
    reused: bool
    video_id: str
    run_dir: str | None = None
    transcript_chars: int | None = None


_AUDIO_NAME = "audio.wav"


def audio_fingerprint(media_sha256: str, audio_sha256: str) -> dict[str, Any]:
    from .resume import build_fingerprint

    return build_fingerprint(
        "audio",
        audio_sha256,
        {
            "media_sha256": media_sha256,
            "whisper_model": config.WHISPER_MODEL,
            "language": config.WHISPER_LANGUAGE,
            "service": "voice-assistant-whisper WS (faster-whisper large-v3 float16 beam 3)",
            "transport": "ws-binary-wav-single-message",
            "transcript_policy": config.TRANSCRIPT_POLICY,
        },
    )


def _load_manifest(manifest_path: Path, video_id: str, media_sha256: str) -> JobManifest:
    if manifest_path.exists():
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        return JobManifest.from_dict(document)
    now = utc_now_iso()
    return JobManifest(
        video_id=video_id,
        media_sha256=media_sha256,
        created_at=now,
        updated_at=now,
    )


def _write_manifest(manifest_path: Path, manifest: JobManifest) -> None:
    atomic_write_bytes(
        manifest_path,
        json.dumps(
            manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n",
    )


def run_audio_stage(
    video_id: str,
    *,
    state: StateRoot,
    urlopen: Callable[..., Any] = default_urlopen,
    socket_factory: SocketFactory = default_socket_factory,
    force: bool = False,
    handshake_key: str | None = None,  # injectable so fixture tests can script the 101
    monotonic: Callable[[], float] = time.monotonic,
) -> AudioOutcome:
    """Transcribe the prepared 16 kHz mono WAV through the whisper WS service.

    Preconditions fail closed: prepare must be complete, the normalized
    audio must exist, the health endpoint must answer. The pipeline never
    starts or stops the whisper service itself.
    """
    state.ensure_layout()
    if Blocklist(state).is_blocked(video_id):
        return AudioOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="video ID is permanently blocklisted; no flag can override "
            "an operator rejection",
            reused=False,
            video_id=video_id,
        )

    entry = Processed(state).get(video_id)
    if entry is None:
        return AudioOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="no fetch record for this ID; run fetch-url first",
            reused=False,
            video_id=video_id,
        )
    if not entry.artifacts:
        return AudioOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="fetch record has no run directory reference",
            reused=False,
            video_id=video_id,
        )
    run_dir = state.root / entry.artifacts[0]
    manifest_path = run_dir / "meta.json"
    manifest = _load_manifest(manifest_path, video_id, entry.media_sha256)
    prepare_record = manifest.stages.get("prepare")
    if prepare_record is None or prepare_record.result.outcome is not StageOutcome.COMPLETE:
        return AudioOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="prepare stage is not complete; audio refuses to run on "
            "unprepared media (fail closed)",
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )
    audio_path = run_dir / _AUDIO_NAME
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        return AudioOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="normalized audio.wav missing or empty: audio is MISSING "
            "(a media capability failure), which is never recorded as "
            "analysed no-speech",
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )
    audio_sha256 = sha256_of_file(audio_path)
    fingerprint = audio_fingerprint(entry.media_sha256, audio_sha256)

    recorded = manifest.stages.get("audio")
    audio_md_path = run_dir / "audio.md"
    if (
        not force
        and recorded is not None
        and recorded.result.outcome is StageOutcome.COMPLETE
        and recorded.fingerprint == fingerprint
        and audio_md_path.is_file()
    ):
        return AudioOutcome(
            outcome=StageOutcome.COMPLETE.value,
            reason="audio fingerprint and audio.md match the recorded run; "
            "zero re-transcription performed",
            reused=True,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    ok, detail = health_precheck(urlopen=urlopen)
    if not ok:
        return AudioOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason=f"{detail}; start the whisper service explicitly, then "
            "re-run this stage (the pipeline never starts services itself)",
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    started_at = utc_now_iso()
    clock = StageClock(
        config.BUDGETS.audio_stage_deadline_seconds,
        label="Audio stage",
        monotonic=monotonic,
    )
    deadline = monotonic() + clock.deadline_seconds

    try:
        result = transcribe_audio(
            audio_path,
            socket_factory=socket_factory,
            handshake_key=handshake_key,
            monotonic=monotonic,
            deadline=deadline,
        )
    except StageDeadlineExceeded as exc:
        return _finish_audio_failure(
            video_id,
            entry,
            run_dir,
            manifest_path,
            manifest,
            fingerprint,
            audio_sha256,
            started_at,
            clock,
            StageOutcome.BUDGET_EXCEEDED.value,
            str(exc),
        )
    except (WhisperProtocolError, OSError, ConnectionError) as exc:
        return _finish_audio_failure(
            video_id,
            entry,
            run_dir,
            manifest_path,
            manifest,
            fingerprint,
            audio_sha256,
            started_at,
            clock,
            StageOutcome.FAILED.value,
            f"whisper WS transcription failed without retry: {exc}",
        )

    audio_md = assemble_audio_md(
        video_id=video_id,
        media_sha256=entry.media_sha256,
        audio_sha256=audio_sha256,
        result=result,
    )
    atomic_write_bytes(audio_md_path, audio_md.encode("utf-8"))
    output_sha256 = sha256_of_file(audio_md_path)
    finished_at = utc_now_iso()
    manifest.resource_gates["audio"] = {
        "stage": "audio",
        "service": "voice-assistant-whisper WS",
        "health_precheck": detail[:120],
        "completion_mode": result.completion_mode,
        "message_types_observed": result.message_types_observed,
        "unexpected_binary_frames": result.unexpected_binary_frames,
        "bytes_sent": result.bytes_sent,
        "chunks": result.chunk_count,
    }
    no_speech = not result.transcript.strip()
    manifest.stages["audio"] = StageRecord(
        stage="audio",
        result=StageResult(
            outcome=StageOutcome.COMPLETE,
            reason=(
                "full-audio single pass completed over one WS connection; "
                f"{result.chunk_count} chunk(s), completion "
                f"{result.completion_mode}"
                + (
                    "; EMPTY transcript on analysed audio recorded as a "
                    "no-speech candidate (not missing audio)"
                    if no_speech
                    else ""
                )
            ),
        ),
        fingerprint=fingerprint,
        input_sha256=audio_sha256,
        output_sha256=output_sha256,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(clock.elapsed(), 3),
    )
    manifest.updated_at = finished_at
    _write_manifest(manifest_path, manifest)
    entry.stage_fingerprints["audio"] = fingerprint
    audio_ref = f"{entry.artifacts[0]}/audio.md"
    if audio_ref not in entry.artifacts:
        entry.artifacts.append(audio_ref)
    Processed(state).record(entry)
    return AudioOutcome(
        outcome=StageOutcome.COMPLETE.value,
        reason=manifest.stages["audio"].result.reason,
        reused=False,
        video_id=video_id,
        run_dir=str(run_dir),
        transcript_chars=len(result.transcript),
    )


def _finish_audio_failure(
    video_id: str,
    entry: Any,
    run_dir: Path,
    manifest_path: Path,
    manifest: JobManifest,
    fingerprint: dict[str, Any],
    audio_sha256: str,
    started_at: str,
    clock: StageClock,
    outcome_value: str,
    reason: str,
) -> AudioOutcome:
    manifest.stages["audio"] = StageRecord(
        stage="audio",
        result=StageResult(outcome=StageOutcome.parse(outcome_value), reason=reason),
        fingerprint={},
        input_sha256=audio_sha256,
        output_sha256=None,
        started_at=started_at,
        finished_at=utc_now_iso(),
        duration_seconds=round(clock.elapsed(), 3),
    )
    manifest.updated_at = utc_now_iso()
    _write_manifest(manifest_path, manifest)
    return AudioOutcome(
        outcome=outcome_value,
        reason=reason,
        reused=False,
        video_id=video_id,
        run_dir=str(run_dir),
    )
