"""Milestone-3 whisper WS client: RFC 6455 framing and audio stage flows.

Fixture-only: no network, no services, no GPU. The WebSocket wire format
is exercised against a scripted fake socket (PRD section 6.4 contract),
every transport seam of ``transcribe_audio`` is injected, and the audio
stage outcomes (including the missing-audio vs no-speech distinction and
the observed completion modes) are verified end to end on a prepared
fixture job.
"""

from __future__ import annotations

import json
import struct
import tempfile
import time
import unittest
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.contracts import BlocklistEntry, StageOutcome, utc_now_iso
from tiktok_ingest.pipeline import video_status
from tiktok_ingest.state import Blocklist, Processed
from tiktok_ingest.tests.fixtures import FakeResponse, make_prepared_job
from tiktok_ingest.whisper_client import (
    KNOWN_MESSAGE_TYPES,
    TranscribeSession,
    accept_key_for,
    assemble_audio_md,
    build_handshake,
    decode_frame,
    encode_frame,
    health_precheck,
    parse_handshake_response,
    run_audio_stage,
    transcribe_audio,
)

# RFC 6455 section 1.3 worked example: the canonical handshake vector.
RFC_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
RFC_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

FIXED_MASK = b"\x01\x02\x03\x04"
AUDIO_ID = "7684788219510033665"  # make_prepared_job's default video ID
AUDIO_BYTES = b"\x00\x01" * 48000  # 3 seconds of 16 kHz mono s16le


# --------------------------------------------------------------------------
# Scripted transport
# --------------------------------------------------------------------------


def handshake_ok(key: str = RFC_KEY) -> bytes:
    """A valid RFC 6455 101 response for ``key`` plus trailing frame bytes."""
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key_for(key)}\r\n"
        "\r\n"
    ).encode("ascii")


def text_frame(text: str) -> bytes:
    return encode_frame(0x1, text.encode("utf-8"), FIXED_MASK)


def binary_frame(data: bytes) -> bytes:
    return encode_frame(0x2, data, FIXED_MASK)


def close_frame(code: int = 1000) -> bytes:
    return encode_frame(0x8, struct.pack("!H", code), FIXED_MASK)


def unmasked_frame(opcode: int, payload: bytes) -> bytes:
    """Server-to-client frame without masking (the decoder must tolerate it)."""
    return _frame_bytes(opcode, payload, fin=True, mask=None)


def _frame_bytes(opcode: int, payload: bytes, *, fin: bool, mask: bytes | None) -> bytes:
    first = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, (0x80 if mask else 0) | length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, (0x80 if mask else 0) | 126, length)
    else:
        header = struct.pack("!BBQ", first, (0x80 if mask else 0) | 127, length)
    if mask is None:
        return header + payload
    return header + mask + bytes(
        byte ^ mask[index % 4] for index, byte in enumerate(payload)
    )


class FakeSocket:
    """A scripted socket: preloaded server bytes plus drain/EOF behaviour.

    With ``eof_immediate=False`` (default), an empty buffer during a
    timed drain raises ``TimeoutError`` (the service is still computing)
    and an empty buffer in blocking mode returns ``b""`` (the service
    closed the stream). With ``eof_immediate=True`` an empty buffer is an
    immediate EOF everywhere, modelling a service that died mid-job.
    """

    def __init__(self, script: bytes = b"", *, eof_immediate: bool = False) -> None:
        self._pending = bytearray(script)
        self.sent = bytearray()
        self.closed = False
        self.timeouts: list[float | None] = []
        self._timeout: float | None = None
        self.eof_immediate = eof_immediate

    def settimeout(self, value: float | None) -> None:
        self._timeout = value
        self.timeouts.append(value)

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        if not self._pending:
            if self._timeout is not None and not self.eof_immediate:
                raise TimeoutError("drain window elapsed")
            return b""
        chunk = bytes(self._pending[:size])
        del self._pending[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


def fixed_masks(n: int) -> bytes:
    """Deterministic client mask key (``os.urandom`` stand-in)."""
    return FIXED_MASK


class ScriptedService:
    """Handshake bytes plus one buffered script for the fake service."""

    def __init__(self, script: bytes, *, eof_immediate: bool = False) -> None:
        self.socket = FakeSocket(script, eof_immediate=eof_immediate)
        self.connections: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> FakeSocket:
        self.connections.append((host, port))
        return self.socket


def ack_script(*extra: bytes) -> bytes:
    return handshake_ok() + text_frame('{"type":"ack"}') + b"".join(extra)


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------


class HandshakeTests(unittest.TestCase):
    def test_rfc6455_accept_vector(self) -> None:
        self.assertEqual(accept_key_for(RFC_KEY), RFC_ACCEPT)

    def test_build_handshake_request_shape(self) -> None:
        request = build_handshake("127.0.0.1", 8767, "/ws/transcribe", key=RFC_KEY)
        self.assertTrue(request.startswith("GET /ws/transcribe HTTP/1.1\r\n"))
        self.assertIn("Host: 127.0.0.1:8767\r\n", request)
        self.assertIn("Upgrade: websocket\r\n", request)
        self.assertIn("Connection: Upgrade\r\n", request)
        self.assertIn(f"Sec-WebSocket-Key: {RFC_KEY}\r\n", request)
        self.assertIn("Sec-WebSocket-Version: 13\r\n", request)
        self.assertTrue(request.endswith("\r\n\r\n"))

    def test_parse_accepts_valid_101(self) -> None:
        headers = parse_handshake_response(handshake_ok(), RFC_KEY)
        self.assertEqual(headers["upgrade"], "websocket")

    def test_parse_rejects_wrong_accept_key(self) -> None:
        raw = handshake_ok().replace(
            f"Sec-WebSocket-Accept: {RFC_ACCEPT}".encode("ascii"),
            b"Sec-WebSocket-Accept: c2hvcnQ=",
        )
        with self.assertRaisesRegex(RuntimeError, "Accept mismatch"):
            parse_handshake_response(raw, RFC_KEY)

    def test_parse_rejects_non_101(self) -> None:
        raw = b"HTTP/1.1 400 Bad Request\r\n\r\n"
        with self.assertRaisesRegex(RuntimeError, "101"):
            parse_handshake_response(raw, RFC_KEY)

    def test_parse_rejects_incomplete_response(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            parse_handshake_response(b"HTTP/1.1 101 Switching\r\n", RFC_KEY)


# --------------------------------------------------------------------------
# Frame encoding/decoding
# --------------------------------------------------------------------------


class FrameTests(unittest.TestCase):
    def test_text_round_trip_with_fixed_mask(self) -> None:
        frame = encode_frame(0x1, "hola".encode("utf-8"), FIXED_MASK)
        decoded, rest = decode_frame(frame)  # type: ignore[misc]
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.opcode, 0x1)  # type: ignore[union-attr]
        self.assertTrue(decoded.fin)  # type: ignore[union-attr]
        self.assertEqual(decoded.payload, b"hola")  # type: ignore[union-attr]
        self.assertEqual(rest, b"")

    def test_masked_payload_is_reversible(self) -> None:
        payload = bytes(range(256))
        frame = encode_frame(0x2, payload, b"\xaa\xbb\xcc\xdd")
        decoded, _ = decode_frame(frame)  # type: ignore[misc]
        self.assertEqual(decoded.payload, payload)  # type: ignore[union-attr]

    def test_unmasked_server_frames_are_decoded(self) -> None:
        decoded, _ = decode_frame(unmasked_frame(0x1, b"plain"))  # type: ignore[misc]
        self.assertEqual(decoded.payload, b"plain")  # type: ignore[union-attr]

    def test_length_forms_125_126_65535_65536(self) -> None:
        for size in (125, 126, 65535, 65536):
            payload = b"x" * size
            decoded, rest = decode_frame(encode_frame(0x2, payload, FIXED_MASK))  # type: ignore[misc]
            self.assertEqual(len(decoded.payload), size)  # type: ignore[union-attr]
            self.assertEqual(rest, b"")

    def test_incomplete_buffer_returns_none(self) -> None:
        frame = encode_frame(0x1, b"0123456789", FIXED_MASK)
        for cut in (0, 1, 2, len(frame) - 1):
            self.assertIsNone(decode_frame(frame[:cut]))

    def test_decode_keeps_remaining_bytes(self) -> None:
        one = encode_frame(0x1, b"a", FIXED_MASK)
        two = encode_frame(0x1, b"b", FIXED_MASK)
        decoded, rest = decode_frame(one + two)  # type: ignore[misc]
        self.assertEqual(decoded.payload, b"a")  # type: ignore[union-attr]
        self.assertEqual(rest, two)


# --------------------------------------------------------------------------
# Session over the fake socket
# --------------------------------------------------------------------------


class SessionTests(unittest.TestCase):
    def connect(self, script: bytes) -> tuple[TranscribeSession, FakeSocket]:
        factory = ScriptedService(script)
        session = TranscribeSession.connect(
            config.WHISPER_WS_HOST,
            config.WHISPER_WS_PORT,
            config.WHISPER_WS_PATH,
            socket_factory=factory,
            mask_key_provider=fixed_masks,
            handshake_key=RFC_KEY,
        )
        return session, factory.socket

    def test_connect_sends_handshake_and_buffers_leftover_frames(self) -> None:
        payload = text_frame('{"type":"ack"}')
        session, sock = self.connect(handshake_ok() + payload)
        self.assertTrue(sock.sent.startswith(b"GET /ws/transcribe HTTP/1.1\r\n"))
        self.assertIn(f"Sec-WebSocket-Key: {RFC_KEY}".encode(), sock.sent)
        message = session.receive_message()
        self.assertEqual(message.kind, "text")  # type: ignore[union-attr]
        self.assertEqual(  # type: ignore[union-attr]
            json.loads(message.payload), {"type": "ack"}  # type: ignore[union-attr]
        )

    def test_connect_eof_during_handshake_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "handshake"):
            self.connect(b"")

    def test_binary_message_round_trip(self) -> None:
        session, _ = self.connect(handshake_ok() + binary_frame(b"\x00\x01"))
        message = session.receive_message()
        self.assertEqual(message.kind, "binary")  # type: ignore[union-attr]
        self.assertEqual(message.payload, b"\x00\x01")  # type: ignore[union-attr]

    def test_fragmented_text_message_is_assembled(self) -> None:
        first = _frame_bytes(0x1, b"hel", fin=False, mask=FIXED_MASK)
        cont1 = _frame_bytes(0x0, b"l", fin=False, mask=FIXED_MASK)
        last = _frame_bytes(0x0, b"o", fin=True, mask=FIXED_MASK)
        session, _ = self.connect(handshake_ok() + first + cont1 + last)
        message = session.receive_message()
        self.assertEqual(message.kind, "text")  # type: ignore[union-attr]
        self.assertEqual(message.payload, b"hello")  # type: ignore[union-attr]

    def test_continuation_without_a_started_message_is_refused(self) -> None:
        stray = encode_frame(0x0, b"orphan", FIXED_MASK)
        session, _ = self.connect(handshake_ok() + stray)
        with self.assertRaisesRegex(RuntimeError, "continuation"):
            session.receive_message()

    def test_ping_is_answered_with_pong(self) -> None:
        ping = encode_frame(0x9, b"keepalive", FIXED_MASK)
        follow_up = text_frame('{"type":"partial","text":"x","accumulated":"x"}')
        session, sock = self.connect(handshake_ok() + ping + follow_up)
        session.receive_message()  # consumes ping, sends pong, returns the partial
        pong = encode_frame(0xA, b"keepalive", FIXED_MASK)
        self.assertIn(pong, bytes(sock.sent))

    def test_close_frame_is_reported_and_echoed(self) -> None:
        session, sock = self.connect(handshake_ok() + close_frame(1000))
        message = session.receive_message()
        self.assertEqual(message.kind, "close")  # type: ignore[union-attr]
        self.assertEqual(message.close_code, 1000)  # type: ignore[union-attr]
        self.assertIn(close_frame(1000), bytes(sock.sent))

    def test_unsupported_opcode_fails_closed(self) -> None:
        session, _ = self.connect(handshake_ok() + encode_frame(0x5, b"?", FIXED_MASK))
        with self.assertRaisesRegex(RuntimeError, "opcode"):
            session.receive_message()

    def test_close_without_a_code_is_tolerated(self) -> None:
        bare_close = encode_frame(0x8, b"", FIXED_MASK)
        session, _ = self.connect(handshake_ok() + bare_close)
        message = session.receive_message()
        self.assertIsNone(message.close_code)  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Documented contract constants
# --------------------------------------------------------------------------


class ContractConstantsTests(unittest.TestCase):
    def test_recorded_endpoints_match_prd_section_6_4(self) -> None:
        self.assertEqual(config.WHISPER_WS_URL, "ws://127.0.0.1:8767/ws/transcribe")
        self.assertEqual(config.WHISPER_HEALTH_URL, "http://127.0.0.1:8766/health")
        self.assertEqual(config.WHISPER_LANGUAGE, "es")

    def test_only_documented_message_types_are_known(self) -> None:
        self.assertEqual(KNOWN_MESSAGE_TYPES, ("ack", "partial"))


# --------------------------------------------------------------------------
# transcribe_audio
# --------------------------------------------------------------------------


class TranscribeTests(unittest.TestCase):
    def run_transcribe(
        self,
        script: bytes,
        *,
        audio: bytes = AUDIO_BYTES,
        eof_immediate: bool = False,
    ):
        service = ScriptedService(script, eof_immediate=eof_immediate)
        result = transcribe_audio(
            self._audio_file(audio),
            socket_factory=service,
            mask_key_provider=fixed_masks,
            handshake_key=RFC_KEY,
            ack_wait_seconds=0.0,
        )
        return result, service

    def expected_client_bytes(self, audio: bytes) -> bytes:
        """Exactly what the client must send: handshake + ONE masked binary
        message carrying the complete WAV + the client-initiated close."""
        expected = build_handshake(
            config.WHISPER_WS_HOST,
            config.WHISPER_WS_PORT,
            config.WHISPER_WS_PATH,
            key=RFC_KEY,
        ).encode("ascii")
        expected += encode_frame(0x2, audio, FIXED_MASK)
        expected += encode_frame(0x8, struct.pack("!H", 1000), FIXED_MASK)
        return expected

    def _audio_file(self, audio: bytes) -> Path:
        path = Path(self.tmp) / "audio.wav"
        path.write_bytes(audio)
        return path

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.tmp = Path(self._tmp_dir.name)

    def test_single_message_response_then_client_close(self) -> None:
        # Real service contract: ONE binary message carries the whole WAV,
        # the service answers with ONE partial, and completion is
        # CLIENT-initiated (server.py transcribes each binary message whole;
        # no final service message exists).
        result, service = self.run_transcribe(
            handshake_ok()
            + text_frame(
                '{"type":"partial","text":"hola mundo","accumulated":"hola mundo"}'
            )
        )
        self.assertIsNone(result.ack)  # no initial_prompt sent -> no ack (observed)
        self.assertEqual(result.transcript, "hola mundo")
        self.assertEqual(result.bytes_sent, len(AUDIO_BYTES))
        self.assertEqual(result.chunk_count, 1)
        self.assertEqual(result.completion_mode, "client_close_after_response")
        self.assertFalse(result.close_frame)
        # Host and port come from the recorded contract.
        self.assertEqual(service.connections, [(config.WHISPER_WS_HOST, 8767)])
        # The audio went out as ONE masked binary message, followed by the
        # client-initiated close frame.
        self.assertEqual(bytes(service.socket.sent), self.expected_client_bytes(AUDIO_BYTES))

    def test_ack_only_when_prompt_is_sent(self) -> None:
        result, _ = self.run_transcribe(
            handshake_ok()
            + text_frame('{"type":"partial","text":"x","accumulated":"x"}')
        )
        self.assertIsNone(result.ack)
        self.assertNotIn("ack", result.message_types_observed)

    def test_partials_record_coarse_arrival_provenance(self) -> None:
        result, _ = self.run_transcribe(
            handshake_ok()
            + text_frame('{"type":"partial","text":"a","accumulated":"a"}')
        )
        self.assertEqual(len(result.partials), 1)
        first = result.partials[0]
        self.assertEqual(first["observed_after_chunk"], 1)
        # Single-message transport: the response arrives after the WHOLE
        # file, so the coarse arrival provenance equals the full duration.
        self.assertAlmostEqual(first["approx_audio_seconds"], 3.0)
        self.assertEqual([entry["accumulated"] for entry in result.partials], ["a"])

    def test_eof_before_partial_response_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "before a partial response"):
            self.run_transcribe(handshake_ok(), eof_immediate=True)

    def test_close_frame_before_partial_response_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "before a partial response"):
            self.run_transcribe(handshake_ok() + close_frame(1000))

    def test_eof_mid_send_fails_without_retry(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "before a partial response"):
            self.run_transcribe(handshake_ok(), eof_immediate=True)

    def test_prompt_gets_the_documented_ack(self) -> None:
        # The real server sends ack ONLY in response to initial_prompt
        # (server.py:82); the client must send the prompt text frame and
        # record the ack when it arrives.
        service = ScriptedService(
            handshake_ok()
            + text_frame('{"type":"ack","message":"initial_prompt received"}')
            + text_frame('{"type":"partial","text":"x","accumulated":"x"}')
        )
        result = transcribe_audio(
            self._audio_file(AUDIO_BYTES),
            socket_factory=service,
            mask_key_provider=fixed_masks,
            handshake_key=RFC_KEY,
            initial_prompt="contexto",
            ack_wait_seconds=1.0,
        )
        self.assertEqual(result.ack, {"type": "ack", "message": "initial_prompt received"})
        self.assertIn("ack", result.message_types_observed)
        # The prompt went out as a masked TEXT frame before the audio
        # (client frames are masked, so compare against the encoded frame).
        prompt_frame = encode_frame(
            0x1,
            json.dumps({"type": "initial_prompt", "text": "contexto"}).encode("utf-8"),
            FIXED_MASK,
        )
        self.assertIn(prompt_frame, bytes(service.socket.sent))

    def test_non_json_ack_fails_closed(self) -> None:
        service = ScriptedService(handshake_ok() + text_frame("hello"))
        with self.assertRaisesRegex(RuntimeError, "not valid JSON"):
            transcribe_audio(
                self._audio_file(AUDIO_BYTES),
                socket_factory=service,
                mask_key_provider=fixed_masks,
                handshake_key=RFC_KEY,
                initial_prompt="contexto",
                ack_wait_seconds=1.0,
            )

    def test_unknown_message_types_are_recorded_not_trusted(self) -> None:
        result, _ = self.run_transcribe(
            handshake_ok()
            + text_frame('{"type":"status","progress":0.5}')
            + text_frame('{"type":"partial","text":"x","accumulated":"x"}')
        )
        self.assertIn("status", result.message_types_observed)
        self.assertEqual(result.completion_mode, "client_close_after_response")

    def test_undecodable_text_frames_are_recorded(self) -> None:
        result, _ = self.run_transcribe(
            handshake_ok()
            + text_frame("not-json")
            + text_frame('{"type":"partial","text":"x","accumulated":"x"}')
        )
        self.assertIn("<undecodable text frame>", result.message_types_observed)

    def test_unexpected_binary_frames_are_counted_not_interpreted(self) -> None:
        result, _ = self.run_transcribe(
            handshake_ok()
            + binary_frame(b"\x00\x00")
            + text_frame('{"type":"partial","text":"x","accumulated":"x"}')
        )
        self.assertEqual(result.unexpected_binary_frames, 1)

    def test_short_audio_is_one_single_message(self) -> None:
        short = b"\x00\x01" * 100  # 200 bytes
        result, service = self.run_transcribe(
            handshake_ok()
            + text_frame('{"type":"partial","text":"","accumulated":""}'),
            audio=short,
        )
        self.assertEqual(result.chunk_count, 1)
        self.assertEqual(result.bytes_sent, len(short))
        self.assertAlmostEqual(result.audio_seconds_total, len(short) / 32000)

    def test_connection_refused_propagates_without_retry(self) -> None:
        def refusing_factory(host: str, port: int) -> FakeSocket:
            raise OSError("connection refused")

        with self.assertRaises(OSError):
            transcribe_audio(
                self._audio_file(AUDIO_BYTES),
                socket_factory=refusing_factory,
            )


# --------------------------------------------------------------------------
# Health precheck
# --------------------------------------------------------------------------


class HealthTests(unittest.TestCase):
    def test_healthy_endpoint_passes(self) -> None:
        ok, detail = health_precheck(urlopen=lambda req, timeout: FakeResponse(b'{"ok":true}'))
        self.assertTrue(ok)

    def test_non_200_is_a_block(self) -> None:
        class Busy:
            status = 503

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size: int = -1) -> bytes:
                return b"busy"

        ok, detail = health_precheck(urlopen=lambda req, timeout: Busy())
        self.assertFalse(ok)
        self.assertIn("503", detail)

    def test_unreachable_is_a_block(self) -> None:
        def refused(req, timeout):
            raise OSError("connection refused")

        ok, detail = health_precheck(urlopen=refused)
        self.assertFalse(ok)
        self.assertIn("unreachable", detail)


# --------------------------------------------------------------------------
# audio.md assembly
# --------------------------------------------------------------------------


class AudioMdTests(unittest.TestCase):
    def result(self, **overrides):
        from tiktok_ingest.whisper_client import TranscriptionResult

        base = dict(
            transcript="texto reconocido",
            ack={"type": "ack"},
            partials=[
                {
                    "text": "texto",
                    "accumulated": "texto reconocido",
                    "observed_after_chunk": 2,
                    "approx_audio_seconds": 2.0,
                }
            ],
            message_types_observed=["ack", "partial", "partial"],
            completion_mode="client_close_after_response",
            close_frame=False,
            bytes_sent=64000,
            chunk_count=2,
            unexpected_binary_frames=0,
            audio_seconds_total=3.0,
        )
        base.update(overrides)
        return TranscriptionResult(**base)

    def document(self, result) -> str:
        return assemble_audio_md(
            video_id="vid",
            media_sha256="m" * 64,
            audio_sha256="a" * 64,
            result=result,
        )

    def test_method_language_and_discipline_are_recorded(self) -> None:
        document = self.document(self.result())
        self.assertIn("faster-whisper large-v3, CUDA float16, beam 3", document)
        self.assertIn("language: es", document)
        self.assertIn("per-message language control does NOT exist", document)
        self.assertIn("full-audio single pass", document)
        self.assertIn("one connection per job", document)
        self.assertIn("no automatic retry", document)
        self.assertIn("initial_prompt: omitted", document)

    def test_completion_and_timing_provenance(self) -> None:
        document = self.document(self.result())
        self.assertIn("client_close_after_response", document)
        self.assertIn("coarse chunk intervals from partial arrivals", document)
        self.assertIn("partial 1: observed after chunk 2", document)

    def test_timing_unavailable_without_partials(self) -> None:
        document = self.document(self.result(partials=[], transcript=""))
        self.assertIn("timing_unavailable", document)

    def test_no_speech_is_a_transcription_finding_not_missing_audio(self) -> None:
        document = self.document(self.result(transcript="", partials=[]))
        self.assertIn("no_speech_candidate", document)
        self.assertIn("NOT missing audio", document)
        self.assertIn("[empty transcript", document)

    def test_non_empty_transcript_has_no_no_speech_flag(self) -> None:
        self.assertNotIn("no_speech_candidate", self.document(self.result()))

    def test_hallucination_uncertainty_is_flagged(self) -> None:
        self.assertIn("hallucinate", self.document(self.result()))


# --------------------------------------------------------------------------
# Audio stage flows (fixture job, no services)
# --------------------------------------------------------------------------


class AudioStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.tmp = Path(self._tmp_dir.name)

    def run_stage(
        self,
        state,
        *,
        script: bytes = ack_script(
            text_frame('{"type":"partial","text":"hola","accumulated":"hola"}'),
            close_frame(1000),
        ),
        urlopen=None,
        eof_immediate: bool = False,
        monotonic=None,
    ):
        service = ScriptedService(script, eof_immediate=eof_immediate)
        if urlopen is None:
            urlopen = lambda req, timeout: FakeResponse(b'{"status":"ok"}')  # noqa: E731
        return (
            run_audio_stage(
                AUDIO_ID,
                state=state,
                urlopen=urlopen,
                socket_factory=service,
                handshake_key=RFC_KEY,
                monotonic=monotonic or time.monotonic,
            ),
            service,
        )

    def test_complete_run_writes_audio_md_and_records_stage(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        outcome, service = self.run_stage(state)
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        run_dir = Path(outcome.run_dir)
        audio_md = (run_dir / "audio.md").read_text(encoding="utf-8")
        self.assertIn("accumulated", audio_md)
        # One connection only, loopback host/port from the contract.
        self.assertEqual(len(service.connections), 1)
        self.assertEqual(
            service.connections[0], (config.WHISPER_WS_HOST, config.WHISPER_WS_PORT)
        )
        # Handshake went to the recorded path.
        self.assertIn(b"GET /ws/transcribe HTTP/1.1", service.socket.sent)
        # Manifest + processed artifacts record the stage.
        processed = Processed(state).get(AUDIO_ID)
        self.assertIn(f"{processed.artifacts[0]}/audio.md", processed.artifacts)
        status = video_status(AUDIO_ID, state=state)
        self.assertEqual(
            status["items"][0]["stage_outcomes"]["audio"], StageOutcome.COMPLETE.value
        )

    def test_reuse_performs_zero_transcription(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        first, service_one = self.run_stage(state)
        self.assertFalse(first.reused)
        second, service_two = self.run_stage(state)
        self.assertTrue(second.reused)
        self.assertEqual(len(service_two.connections), 0)
        self.assertEqual(second.outcome, StageOutcome.COMPLETE.value)

    def test_blocklisted_video_is_blocked(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        Blocklist(state).reject(
            BlocklistEntry(
                video_id=AUDIO_ID,
                rejected_at=utc_now_iso(),
                reason="operator",
            )
        )
        outcome, _ = self.run_stage(state)
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)

    def test_unknown_video_fails(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        outcome, _ = self.run_stage(state)
        unknown = run_audio_stage(
            "4242424242424242424", state=state, urlopen=lambda req, timeout: FakeResponse(b"{}")
        )
        self.assertEqual(unknown.outcome, StageOutcome.FAILED.value)

    def test_prepare_incomplete_fails_closed(self) -> None:
        state, _ = make_prepared_job(self.tmp, prepare_complete=False)
        outcome, _ = self.run_stage(state)
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("prepare", outcome.reason)

    def test_missing_audio_is_not_recorded_as_no_speech(self) -> None:
        state, _ = make_prepared_job(self.tmp, include_audio=False)
        outcome, service = self.run_stage(state)
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("MISSING", outcome.reason)
        self.assertEqual(service.connections, [])

    def test_health_down_is_blocked_not_started(self) -> None:
        state, _ = make_prepared_job(self.tmp)

        def refused(req, timeout):
            raise OSError("connection refused")

        outcome, service = self.run_stage(state, urlopen=refused)
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)
        self.assertIn("start the whisper service explicitly", outcome.reason)
        self.assertEqual(service.connections, [])

    def test_transport_failure_is_failed_without_retry(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        # The service answers the handshake + ack, then dies mid-job: the
        # first drain hits a raw EOF while bytes remain unsent.
        outcome, _ = self.run_stage(state, script=ack_script(), eof_immediate=True)
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("without retry", outcome.reason)

    def test_stage_deadline_is_budget_exceeded(self) -> None:
        state, _ = make_prepared_job(self.tmp)
        # Calls: StageClock start (0), deadline computation (0), then the
        # first deadline check inside transcribe jumps past the 600 s budget.
        clock_values = [0.0, 0.0, 10_000.0]
        monotonic = lambda: (  # noqa: E731
            clock_values.pop(0) if clock_values else 10_000.0
        )
        outcome, service = self.run_stage(state, monotonic=monotonic)
        self.assertEqual(outcome.outcome, StageOutcome.BUDGET_EXCEEDED.value)
        # The connection is opened before the first deadline check (the
        # deadline guards the transcription, not the dial), so exactly one
        # connection happens — and zero audio bytes are sent.
        self.assertEqual(len(service.connections), 1)
        self.assertIn("0 of 18 bytes sent", outcome.reason)

    def test_empty_transcript_completes_as_no_speech_candidate(self) -> None:
        # The real service answers silence with an EMPTY partial
        # (server.py sends {"type":"partial","text":""}); a close without
        # any partial is a service failure, not a transcription finding.
        state, _ = make_prepared_job(self.tmp)
        outcome, _ = self.run_stage(
            state,
            script=ack_script(
                text_frame('{"type":"partial","text":"","accumulated":""}')
            ),
        )
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        self.assertIn("no-speech candidate", outcome.reason)
        audio_md = (Path(outcome.run_dir) / "audio.md").read_text(encoding="utf-8")
        self.assertIn("no_speech_candidate", audio_md)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
