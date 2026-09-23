"""Synthesis API adapter tests: fake transport, zero network.

Covers the ONE OpenAI-compatible adapter: explicit env configuration
(names echoed, values never), evidence sanitization (no paths, no
secrets on the wire), tools/retrieval disabled in the request body,
single-attempt request semantics, structured-JSON parsing, scrubbed
failures (timeout, HTTP error, refusal, malformed output) and the
resumable-stop wording that never manufactures a classification.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from tiktok_ingest import synthesis_api as sa

ENV_OK = {
    sa.ENV_BASE_URL: "https://api.example.com/v1",
    sa.ENV_API_KEY: "sk-test-DO-NOT-SHIP",
    sa.ENV_MODEL: "glm-5.3-flash",
}

VALID_DOCUMENT = {
    "classification": {"root": "tecnología", "subgroup": "cli-agentes", "confidence": 0.8},
    "entities": [{"name": "shunt", "candidate_urls": []}],
    "claims": [
        {
            "claim": "reduce tokens",
            "source": "audio",
            "candidate_urls": ["https://example.com/a"],
        }
    ],
    "actionables": ["check repo"],
    "fit": "adjacent",
    "model": "glm-5.3-flash",
    "generated_at": "2026-09-23T10:00:00Z",
}


def completion(document: Any = None, *, refusal: str | None = None) -> str:
    message: dict[str, Any] = {}
    if document is not None:
        message["content"] = json.dumps(document)
    if refusal is not None:
        message["refusal"] = refusal
    return json.dumps(
        {"choices": [{"message": message}], "model": "glm-5.3-flash"}
    )


class FakeResponse:
    def __init__(self, *, status: int = 200, body: str = "") -> None:
        self.status = status
        self._body = body.encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class FakeTransport:
    """Records (request, timeout); returns scripted responses/errors."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[tuple[Any, float]] = []

    def __call__(self, request: Any, *, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def call_count(self) -> int:
        return len(self.calls)


class TestConfig(unittest.TestCase):
    def test_missing_env_names_are_listed_without_values(self) -> None:
        config, missing = sa.load_text_api_config({})
        self.assertIsNone(config)
        self.assertEqual(
            missing,
            [sa.ENV_BASE_URL, sa.ENV_API_KEY, sa.ENV_MODEL],
        )

    def test_partial_env_is_rejected_without_echoing_values(self) -> None:
        config, missing = sa.load_text_api_config(
            {sa.ENV_BASE_URL: "https://api.example.com/v1", sa.ENV_API_KEY: "secret"}
        )
        self.assertIsNone(config)
        self.assertEqual(missing, [sa.ENV_MODEL])

    def test_full_env_builds_config_with_origin_not_key(self) -> None:
        config, _ = sa.load_text_api_config(ENV_OK)
        assert config is not None
        self.assertEqual(config.origin(), "https://api.example.com/v1")
        self.assertNotIn("sk-test", config.describe())
        self.assertIn("glm-5.3-flash", config.describe())

    def test_non_http_base_url_is_rejected(self) -> None:
        with self.assertRaises(sa.SynthesisApiError):
            sa.TextApiConfig(
                base_url="ftp://nope", api_key="k", model="m"
            )


class TestSanitization(unittest.TestCase):
    def test_paths_are_removed_but_labelled_metadata_kept(self) -> None:
        text = (
            "# video.md\nmetadata: captured_at 2026-09-23\n"
            "frame 00:12 /home/bruno/.local/state/tiktok-ingest/runs/r1/v1/frames/f3.png\n"
            "audio artifact: ~/.local/state/x/audio.wav\n"
            "file:///home/u/secret.html\n"
        )
        cleaned = sa.sanitize_evidence_text(text)
        self.assertNotIn("/home/bruno", cleaned)
        self.assertNotIn("~/.local", cleaned)
        self.assertNotIn("file://", cleaned)
        self.assertIn("metadata: captured_at 2026-09-23", cleaned)
        self.assertIn("[path removed]", cleaned)

    def test_credential_patterns_are_redacted_by_kind(self) -> None:
        text = "api_key = abc123xyz and Bearer eyJhbGciOi.9 and ghp_" + "x" * 30
        cleaned = sa.sanitize_evidence_text(text)
        self.assertNotIn("abc123xyz", cleaned)
        self.assertNotIn("Bearer eyJ", cleaned)
        self.assertIn("[redacted:api_key_assignment]", cleaned)
        self.assertIn("[redacted:bearer_token]", cleaned)
        self.assertIn("[redacted:github_token]", cleaned)

    def test_payload_is_labelled_and_bounded(self) -> None:
        big = "x" * (sa.MAX_EVIDENCE_CHARS_PER_MODALITY + 50)
        payload = sa.build_evidence_payload(big, "audio text")
        self.assertIn("### video.md evidence", payload)
        self.assertIn("### audio.md evidence", payload)
        self.assertIn("audio text", payload)
        self.assertIn("truncated", payload)

    def test_scrub_secrets_replaces_configured_key(self) -> None:
        config, _ = sa.load_text_api_config(ENV_OK)
        assert config is not None
        cleaned = sa.scrub_secrets(
            f"request failed with Authorization: Bearer {config.api_key}",
            config,
        )
        self.assertNotIn(config.api_key, cleaned)
        self.assertIn("[redacted:api-key]", cleaned)


class TestRequest(unittest.TestCase):
    def test_request_body_disables_tools_and_requests_json(self) -> None:
        config, _ = sa.load_text_api_config(ENV_OK)
        assert config is not None
        messages = sa.build_request_messages("payload text")
        body = sa.build_request_body(config, messages)
        self.assertEqual(body["model"], config.model)
        self.assertEqual(body["tool_choice"], "none")
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertFalse(body["stream"])
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["content"], "payload text")
        self.assertIn("tecnología", messages[0]["content"])

    def test_valid_document_is_parsed_single_attempt(self) -> None:
        config, _ = sa.load_text_api_config(ENV_OK)
        assert config is not None
        transport = FakeTransport([FakeResponse(body=completion(VALID_DOCUMENT))])
        document = sa.request_json_document(
            config, sa.build_request_messages("p"), transport=transport
        )
        self.assertEqual(document["classification"]["root"], "tecnología")
        self.assertEqual(transport.call_count, 1, "exactly one attempt, no retry")
        request, timeout = transport.calls[0]
        self.assertEqual(request.full_url, "https://api.example.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {config.api_key}")
        self.assertEqual(timeout, config.timeout_seconds)
        wire_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(wire_body["tool_choice"], "none")
        self.assertEqual(wire_body["tools"], [])
        self.assertEqual(wire_body["response_format"], {"type": "json_object"})

    def test_wire_payload_never_contains_local_paths(self) -> None:
        evidence = (
            "# video.md\nframe 00:04 /home/bruno/.local/state/runs/r/v/frames/f1.png\n"
            "# audio.md\nsegment 1 hello\npassword=hunter2\n"
        )
        payload = sa.build_evidence_payload(
            sa.sanitize_evidence_text(evidence), sa.sanitize_evidence_text("clean audio")
        )
        self.assertNotIn("/home/bruno", payload)
        self.assertNotIn("hunter2", payload)
        self.assertIn("segment 1 hello", payload)


class TestFailures(unittest.TestCase):
    def _config(self) -> sa.TextApiConfig:
        config, _ = sa.load_text_api_config(ENV_OK)
        assert config is not None
        return config

    def _run(self, transport: FakeTransport) -> str:
        try:
            sa.request_json_document(
                self._config(), sa.build_request_messages("p"), transport=transport
            )
        except sa.SynthesisApiError as exc:
            return str(exc)
        self.fail("expected SynthesisApiError")

    def test_timeout_is_resumable_and_scrubbed(self) -> None:
        config = self._config()
        transport = FakeTransport([TimeoutError("boom " + config.api_key)])
        message = self._run(transport)
        self.assertIn("resumable synthesis stop", message)
        self.assertIn("no classification was manufactured", message)
        self.assertNotIn(config.api_key, message)
        self.assertEqual(transport.call_count, 1, "no retry after timeout")

    def test_http_error_is_resumable(self) -> None:
        transport = FakeTransport([FakeResponse(status=500, body="oops")])
        message = self._run(transport)
        self.assertIn("HTTP 500", message)
        self.assertIn("resumable", message)

    def test_provider_refusal_is_resumable(self) -> None:
        transport = FakeTransport([FakeResponse(body=completion(refusal="no"))])
        message = self._run(transport)
        self.assertIn("refused", message)

    def test_malformed_content_json_is_resumable(self) -> None:
        malformed = json.dumps({"choices": [{"message": {"content": "{not json"}}]})
        transport = FakeTransport([FakeResponse(body=malformed)])
        message = self._run(transport)
        self.assertIn("not valid JSON", message)

    def test_non_object_content_is_resumable(self) -> None:
        arr = json.dumps({"choices": [{"message": {"content": "[1, 2]"}}]})
        transport = FakeTransport([FakeResponse(body=arr)])
        message = self._run(transport)
        self.assertIn("not a JSON object", message)

    def test_empty_content_is_resumable(self) -> None:
        empty = json.dumps({"choices": [{"message": {"content": ""}}]})
        transport = FakeTransport([FakeResponse(body=empty)])
        message = self._run(transport)
        self.assertIn("content is empty", message)

    def test_no_choices_is_resumable(self) -> None:
        transport = FakeTransport([FakeResponse(body=json.dumps({}))])
        message = self._run(transport)
        self.assertIn("no choices", message)


class TestEvidenceReading(unittest.TestCase):
    def test_missing_modality_is_an_explicit_error(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "video.md").write_text("v", encoding="utf-8")
            with self.assertRaises(sa.SynthesisApiError) as ctx:
                sa.read_evidence_text(run_dir)
            self.assertIn("audio.md", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
