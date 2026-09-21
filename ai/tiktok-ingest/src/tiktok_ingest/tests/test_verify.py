"""Milestone 4: verification stage (fixture-only, ZERO network).

Covers: the pure safe-URL validator (every refused class + accepted public
URLs), bounded retrieval with a fake urlopen (caps, 404, redirect
refusal), the GitHub star parser, the verdict assignment matrix, exact
PRD section 8.1 backlog emission with taxonomy normalization, and
duplicate-emission prevention on resume.
"""

from __future__ import annotations

import io
import json
import socket
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.contracts import StageOutcome
from tiktok_ingest.state import Backlog, Taxonomy
from tiktok_ingest.synthesis import SynthesisClaim, SynthesisEntity
from tiktok_ingest.tests.fixtures import (
    FIXED_NOW,
    GITHUB_REPO_URL,
    VIDEO_ID,
    FakeVerifyResponse,
    github_star_page,
    make_synthesized_job,
    sample_synthesis_document,
)
from tiktok_ingest.verify import (
    FetchRecord,
    bounded_fetch,
    claim_tokens,
    compare_star_claim,
    evaluate_claim,
    extract_github_stars,
    extract_passage,
    parse_claim_number,
    refused_ip_reason,
    run_verify_stage,
    validate_public_http_url,
    verify_fingerprints,
)
from tiktok_ingest.verify import UnsafeURLError


# --------------------------------------------------------------------------
# Safe-URL validator
# --------------------------------------------------------------------------


class SafeUrlValidatorTests(unittest.TestCase):
    def test_accepts_public_urls(self) -> None:
        accepted = [
            "https://github.com/fixture/tool",
            "http://example.com/docs",
            "https://example.com/path?q=1&lang=es#frag",
            "https://93.184.216.34/page",  # public literal IPv4
            "https://example.com:443/explicit-default",
            "http://example.com:80/explicit-default",
        ]
        for url in accepted:
            self.assertEqual(validate_public_http_url(url), url)

    def test_refuses_non_http_schemes(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/file",
            "data:text/html;base64,PGI+",
            "ws://example.com/socket",
            "gopher://example.com",
        ):
            with self.assertRaises(UnsafeURLError, msg=url):
                validate_public_http_url(url)

    def test_refuses_missing_or_relative_urls(self) -> None:
        for url in ("", "   ", "example.com/path", None, 42, "//example.com"):
            with self.assertRaises(UnsafeURLError, msg=str(url)):
                validate_public_http_url(url)

    def test_refuses_control_characters_and_whitespace(self) -> None:
        with self.assertRaises(UnsafeURLError):
            validate_public_http_url("https://exa mple.com/x")
        with self.assertRaises(UnsafeURLError):
            validate_public_http_url("https://example.com/x\nEvil: 1")

    def test_refuses_localhost(self) -> None:
        for url in (
            "https://localhost/",
            "http://LOCALHOST:8080/",
            "https://api.localhost/x",
        ):
            with self.assertRaises(UnsafeURLError, msg=url):
                validate_public_http_url(url)

    def test_refuses_private_v4_ranges(self) -> None:
        refused = [
            "https://127.0.0.1/",
            "http://127.200.1.2/",
            "https://10.1.2.3/",
            "https://172.16.0.1/",
            "https://172.31.255.254/",
            "https://192.168.1.50/",
            "https://0.0.0.0/",
            "https://100.64.0.10/",
            "https://169.254.10.20/",
        ]
        for url in refused:
            with self.assertRaises(UnsafeURLError, msg=url):
                validate_public_http_url(url)
        # Boundary: just outside the ranges is acceptable.
        for url in ("https://172.32.0.1/", "https://11.0.0.1/", "https://192.169.0.1/"):
            validate_public_http_url(url)

    def test_refuses_metadata_endpoint(self) -> None:
        with self.assertRaises(UnsafeURLError) as ctx:
            validate_public_http_url("http://169.254.169.254/latest/meta-data/")
        self.assertIn("169.254", str(ctx.exception))

    def test_refuses_private_v6(self) -> None:
        for url in (
            "https://[::1]/",
            "https://[::]/",
            "https://[fd00::1]/",
            "https://[fdab:0:0::5]/",
            "https://[fe80::1]/",
            "https://[ff02::1]/",
            "https://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
            "https://[::ffff:10.0.0.1]/",
        ):
            with self.assertRaises(UnsafeURLError, msg=url):
                validate_public_http_url(url)
        validate_public_http_url("https://[2606:4700::1111]/")  # public v6

    def test_refuses_credentials_in_url(self) -> None:
        for url in (
            "https://user:pass@example.com/",
            "https://user@example.com/",
            "http://:@example.com/",
        ):
            with self.assertRaises(UnsafeURLError, msg=url):
                validate_public_http_url(url)

    def test_refuses_non_default_ports(self) -> None:
        for url in (
            "https://example.com:8080/",
            "http://example.com:9000/",
            "https://example.com:22/",
        ):
            with self.assertRaises(UnsafeURLError, msg=url):
                validate_public_http_url(url)

    def test_refused_ip_reason_reports_network(self) -> None:
        import ipaddress

        self.assertIn(
            "refused network",
            refused_ip_reason(ipaddress.ip_address("169.254.169.254")),
        )
        self.assertIsNone(refused_ip_reason(ipaddress.ip_address("1.1.1.1")))
        mapped = ipaddress.ip_address("::ffff:192.168.0.9")
        self.assertIsNotNone(refused_ip_reason(mapped))


# --------------------------------------------------------------------------
# Bounded retrieval with a fake urlopen
# --------------------------------------------------------------------------


class FakeUrlopen:
    """Scripted urlopen: list of callables/responses consumed per call."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float]] = []
        self.headers_seen: list[dict[str, str]] = []

    def __call__(self, req, timeout):
        from urllib.parse import urlparse

        self.calls.append((req.full_url, timeout))
        self.headers_seen.append(dict(req.header_items()))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(req, timeout)
        return item


def _http_error(code: int, location: str | None = None) -> urllib.error.HTTPError:
    headers = {"Location": location} if location else {}
    return urllib.error.HTTPError(
        "https://fixture.example/x", code, "status", headers, io.BytesIO(b"")
    )


def _public_resolver(host, port, proto=None):
    """Fake getaddrinfo: any name 'resolves' to a public address."""
    return [(socket.AF_INET, None, None, "", ("93.184.216.34", 443))]


class BoundedFetchTests(unittest.TestCase):
    def fetch(self, fake, url=GITHUB_REPO_URL, **kwargs) -> FetchRecord:
        return bounded_fetch(
            url, urlopen=fake, resolver=_public_resolver, **kwargs
        )

    def test_happy_path_records_provenance(self) -> None:
        body = github_star_page(12000)
        fake = FakeUrlopen(FakeVerifyResponse(body, final_url=GITHUB_REPO_URL))
        record = self.fetch(fake)
        self.assertTrue(record.ok)
        self.assertEqual(record.status, 200)
        self.assertEqual(record.final_url, GITHUB_REPO_URL)
        self.assertEqual(record.hops, ())
        self.assertEqual(
            record.sha256,
            __import__("hashlib").sha256(body).hexdigest(),
        )
        self.assertIn("stargazers_count", record.body_text or "")
        # User-Agent and timeout caps applied.
        self.assertEqual(fake.calls[0][1], config.VERIFY_TIMEOUT_SECONDS)
        headers = {k.lower(): v for k, v in fake.headers_seen[0].items()}
        self.assertEqual(headers["user-agent"], config.VERIFY_USER_AGENT)

    def test_size_cap_fails_without_truncation(self) -> None:
        fake = FakeUrlopen(FakeVerifyResponse(b"x" * 11))
        record = bounded_fetch(
            GITHUB_REPO_URL,
            urlopen=fake,
            resolver=_public_resolver,
            max_bytes=10,
        )
        self.assertFalse(record.ok)
        self.assertIn("cap", record.error or "")
        self.assertIsNone(record.body_text)

    def test_404_is_explicit_failure(self) -> None:
        fake = FakeUrlopen(_http_error(404))
        record = self.fetch(fake)
        self.assertFalse(record.ok)
        self.assertEqual(record.status, 404)
        self.assertIn("HTTP 404", record.error or "")

    def test_404_response_object_path(self) -> None:
        fake = FakeUrlopen(FakeVerifyResponse(b"nope", status=404))
        record = self.fetch(fake)
        self.assertFalse(record.ok)
        self.assertEqual(record.status, 404)

    def test_redirect_to_private_target_is_refused_unfetched(self) -> None:
        fake = FakeUrlopen(
            _http_error(302, location="http://169.254.169.254/latest/meta-data/")
        )
        record = self.fetch(fake)
        self.assertFalse(record.ok)
        self.assertIn("refused redirect target", record.error or "")
        self.assertIn("169.254", record.error or "")
        # Only the FIRST call happened; the refused hop was never fetched.
        self.assertEqual(len(fake.calls), 1)

    def test_redirect_to_file_scheme_refused(self) -> None:
        fake = FakeUrlopen(_http_error(302, location="file:///etc/passwd"))
        record = self.fetch(fake)
        self.assertFalse(record.ok)
        self.assertIn("refused redirect target", record.error or "")

    def test_acceptable_redirect_is_followed_with_hop_record(self) -> None:
        final = "https://github.com/fixture/tool/blob/main/README.md"
        fake = FakeUrlopen(
            _http_error(302, location="/fixture/tool/blob/main/README.md"),
            FakeVerifyResponse(b"readme", final_url=final),
        )
        record = self.fetch(fake)
        self.assertTrue(record.ok)
        self.assertEqual(record.hops, (final,))
        self.assertEqual(record.final_url, final)

    def test_too_many_redirects_fails(self) -> None:
        fake = FakeUrlopen(*[_http_error(302, location="/hop") for _ in range(9)])
        record = bounded_fetch(
            GITHUB_REPO_URL,
            urlopen=fake,
            resolver=_public_resolver,
            max_redirects=3,
        )
        self.assertFalse(record.ok)
        self.assertIn("too many redirects", record.error or "")
        self.assertEqual(len(record.hops), 3)

    def test_transport_error_is_explicit(self) -> None:
        fake = FakeUrlopen(OSError("connection refused"))
        record = self.fetch(fake)
        self.assertFalse(record.ok)
        self.assertIn("retrieval failed", record.error or "")

    def test_dns_resolving_to_private_ip_is_refused(self) -> None:
        def evil_resolver(host, port, proto=None):
            return [(socket.AF_INET, None, None, "", ("10.9.8.7", 0))]

        record = bounded_fetch(
            "https://internal.example/x",
            urlopen=FakeUrlopen(),  # must never be called
            resolver=evil_resolver,
        )
        self.assertFalse(record.ok)
        self.assertIn("refused target", record.error or "")
        self.assertIn("10.9.8.7", record.error or "")

    def test_dns_failure_is_explicit(self) -> None:
        def failing_resolver(host, port, proto=None):
            raise OSError("name resolution failed")

        record = bounded_fetch(
            "https://missing.example/x",
            urlopen=FakeUrlopen(),
            resolver=failing_resolver,
        )
        self.assertFalse(record.ok)
        self.assertIn("DNS resolution failed", record.error or "")

    def test_unsafe_url_never_reaches_transport(self) -> None:
        fake = FakeUrlopen()
        record = bounded_fetch(
            "file:///etc/passwd", urlopen=fake, resolver=lambda *a, **k: []
        )
        self.assertFalse(record.ok)
        self.assertIn("refused target", record.error or "")
        self.assertEqual(fake.calls, [])

    def test_literal_ip_urls_skip_dns(self) -> None:
        fake = FakeUrlopen(FakeVerifyResponse(b"ok"))
        record = bounded_fetch(
            "https://93.184.216.34/x", urlopen=fake, resolver=None
        )
        self.assertTrue(record.ok)


# --------------------------------------------------------------------------
# Pure evidence extraction
# --------------------------------------------------------------------------


class PassageExtractionTests(unittest.TestCase):
    def test_supported_claim_yields_passage(self) -> None:
        body = (
            "<html><body><p>FixtureTool is a CLI agent framework. "
            "It cuts setup time by 90% on large repos.</p>"
            "<p>License: MIT. Requires Python 3.10+.</p></body></html>"
        )
        passage = extract_passage(body, "FixtureTool cuts setup time by 90%")
        self.assertIsNotNone(passage)
        self.assertIn("90%", passage)
        self.assertIn("fixturetool", passage)

    def test_unrelated_page_yields_none(self) -> None:
        passage = extract_passage(
            "<p>Delicious recipes for apple pie and cider.</p>",
            "FixtureTool cuts setup time by 90%",
        )
        self.assertIsNone(passage)

    def test_missing_numeric_token_yields_none(self) -> None:
        passage = extract_passage(
            "<p>FixtureTool is a CLI tool.</p>",
            "FixtureTool cuts setup time by 90%",
        )
        self.assertIsNone(passage)

    def test_spanish_accents_fold(self) -> None:
        passage = extract_passage(
            "<p>La librería reduce el código repetido en proyectos.</p>",
            "La libreria reduce el codigo repetido",
        )
        self.assertIsNotNone(passage)

    def test_single_token_claim(self) -> None:
        self.assertIsNotNone(extract_passage("rawkit is great", "rawkit"))
        self.assertIsNone(extract_passage("nothing here", "rawkit"))

    def test_claim_tokens_drop_stopwords(self) -> None:
        tokens = claim_tokens("The tool is a CLI for developers")
        self.assertNotIn("the", tokens)
        self.assertNotIn("is", tokens)
        self.assertIn("cli", tokens)


class GitHubStarParserTests(unittest.TestCase):
    def test_json_star_count(self) -> None:
        self.assertEqual(extract_github_stars('{"stargazers_count": 12345}'), 12345)
        self.assertEqual(extract_github_stars('{"starCount": "42"}'), 42)

    def test_og_description_star_count(self) -> None:
        body = (
            '<meta property="og:description" content="Fast tool — '
            '3,456 stars. Forked 210 times.">'
        )
        self.assertEqual(extract_github_stars(body), 3456)

    def test_k_suffix_multiplier(self) -> None:
        body = '<meta property="og:description" content="1.2k stars, MIT license">'
        self.assertEqual(extract_github_stars(body), 1200)

    def test_absent_count_returns_none(self) -> None:
        self.assertIsNone(extract_github_stars("<html><body>no counts</body></html>"))
        self.assertIsNone(extract_github_stars(""))

    def test_malformed_count_returns_none(self) -> None:
        self.assertIsNone(extract_github_stars('{"stargazers_count": "lots"}'))
        self.assertIsNone(extract_github_stars("12.34.56 stars in text"))

    def test_parse_claim_number(self) -> None:
        self.assertEqual(parse_claim_number("FixtureTool has 12000 stars"), 12000)
        self.assertEqual(parse_claim_number("corta los tokens 90%"), 90)
        self.assertIsNone(parse_claim_number("no numbers here"))

    def test_compare_star_claim_matrix(self) -> None:
        self.assertEqual(compare_star_claim(100, 100), "confirmed")
        self.assertEqual(compare_star_claim(100, 150), "confirmed")
        self.assertEqual(compare_star_claim(100, 99), "overstated")
        self.assertEqual(compare_star_claim(100, 1), "overstated")
        self.assertEqual(compare_star_claim(100, 0), "contradicted")


# --------------------------------------------------------------------------
# Verdict assignment matrix (pure)
# --------------------------------------------------------------------------


def _ok_fetch(url: str, body: bytes) -> FetchRecord:
    import hashlib

    from tiktok_ingest.contracts import utc_now_iso

    return FetchRecord(
        url=url,
        final_url=url,
        status=200,
        ok=True,
        sha256=hashlib.sha256(body).hexdigest(),
        body_text=body.decode("utf-8"),
        retrieved_at=utc_now_iso(),
        duration_seconds=0.01,
        error=None,
    )


def _failed_fetch(url: str, error: str) -> FetchRecord:
    from tiktok_ingest.contracts import utc_now_iso

    return FetchRecord(
        url=url,
        final_url=url,
        status=None,
        ok=False,
        sha256=None,
        body_text=None,
        retrieved_at=utc_now_iso(),
        duration_seconds=0.01,
        error=error,
    )


class EvaluateClaimTests(unittest.TestCase):
    def test_no_candidates_is_unnamed(self) -> None:
        record = evaluate_claim(
            SynthesisClaim(claim="Some tool exists", source="audio"),
            fetches={},
        )
        self.assertEqual(record.verdict, "unnamed")
        self.assertIn("first-class", record.reason)
        self.assertIsNone(record.evidence)

    def test_operator_supplied_verdict_is_kept_without_candidates(self) -> None:
        record = evaluate_claim(
            SynthesisClaim(
                claim="Some tool exists", source="audio", verdict="confirmed"
            ),
            fetches={},
        )
        self.assertEqual(record.verdict, "confirmed")
        self.assertIn("operator-supplied", record.reason)

    def test_entity_mention_supplies_candidates(self) -> None:
        # Entity-derived candidate URLs are used when the claim itself has
        # none. The mechanical rule takes the FIRST numeric token as the
        # claimed number (90%), the page reports 12000 stars -> confirmed.
        body = github_star_page(12000)
        claim = SynthesisClaim(
            claim="FixtureTool cuts setup time by 90% and has 12000 stars",
            source="audio",
        )
        record = evaluate_claim(
            claim,
            fetches={GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, body)},
            entities=(
                SynthesisEntity(name="FixtureTool", candidate_urls=[GITHUB_REPO_URL]),
            ),
        )
        self.assertEqual(record.verdict, "confirmed")
        self.assertTrue(record.fetches, "entity-derived fetches must be recorded")

    def test_all_fetches_failed_is_unverifiable_with_reason(self) -> None:
        record = evaluate_claim(
            SynthesisClaim(
                claim="FixtureTool has docs",
                source="visual",
                candidate_urls=[GITHUB_REPO_URL],
            ),
            fetches={
                GITHUB_REPO_URL: _failed_fetch(GITHUB_REPO_URL, "HTTP 404"),
            },
        )
        self.assertEqual(record.verdict, "unverifiable")
        self.assertIn("HTTP 404", record.reason)
        self.assertIn("every candidate retrieval failed", record.reason)

    def test_passage_match_confirms(self) -> None:
        body = b"<p>FixtureTool is a CLI agent framework for daily work</p>"
        record = evaluate_claim(
            SynthesisClaim(
                claim="FixtureTool is a CLI agent framework",
                source="visual",
                candidate_urls=[GITHUB_REPO_URL],
            ),
            fetches={GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, body)},
        )
        self.assertEqual(record.verdict, "confirmed")
        evidence = json.loads(record.evidence or "{}")
        self.assertEqual(evidence["source_url"], GITHUB_REPO_URL)
        self.assertIn("sha256", evidence)
        self.assertIn("retrieved_at", evidence)
        self.assertIn("passage", evidence)

    def test_star_comparison_records_both_numbers(self) -> None:
        claim = SynthesisClaim(
            claim="FixtureTool has 12000 stars",
            source="audio",
            candidate_urls=[GITHUB_REPO_URL],
        )
        fetches = {GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, github_star_page(3456))}
        record = evaluate_claim(claim, fetches)
        self.assertEqual(record.verdict, "overstated")
        self.assertIn("12000", record.note or "")
        self.assertIn("3456", record.note or "")

        fetches = {GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, github_star_page(12000))}
        self.assertEqual(evaluate_claim(claim, fetches).verdict, "confirmed")

        fetches = {GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, github_star_page(0))}
        record = evaluate_claim(claim, fetches)
        self.assertEqual(record.verdict, "contradicted")

    def test_fetched_but_unmatched_is_unverifiable(self) -> None:
        record = evaluate_claim(
            SynthesisClaim(
                claim="FixtureTool has 12000 stars",
                source="audio",
                candidate_urls=[GITHUB_REPO_URL],
            ),
            fetches={
                GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, b"<p>totally different</p>")
            },
        )
        self.assertEqual(record.verdict, "unverifiable")
        self.assertIn("no supporting passage", record.reason)
        self.assertIn("no star count found", record.reason)

    def test_missing_passage_is_never_contradicted(self) -> None:
        record = evaluate_claim(
            SynthesisClaim(
                claim="FixtureTool is written in Rust",
                source="visual",
                candidate_urls=[GITHUB_REPO_URL],
            ),
            fetches={GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, b"<p>cooking site</p>")},
        )
        self.assertEqual(record.verdict, "unverifiable")
        self.assertNotEqual(record.verdict, "contradicted")


class VerdictIdentitySemanticsTests(unittest.TestCase):
    """Rules-version-2 regressions: unnamed vs unverifiable/no_candidate_urls."""

    def test_identified_entity_without_urls_is_unverifiable(self) -> None:
        # The claim names an entity the document identified, but neither
        # the claim nor the entity carries candidate URLs. Absence of URLs
        # is NOT absence of a name: unverifiable, not unnamed.
        record = evaluate_claim(
            SynthesisClaim(claim="Solid Roots ships worldwide", source="caption"),
            fetches={},
            entities=(SynthesisEntity(name="Solid Roots"),),
        )
        self.assertEqual(record.verdict, "unverifiable")
        self.assertTrue(record.reason.startswith("no_candidate_urls"))
        self.assertIn("Solid Roots", record.reason)
        self.assertIn("identified", record.reason)
        self.assertEqual(record.fetches, ())
        self.assertIsNone(record.evidence)

    def test_no_entity_identified_is_unnamed(self) -> None:
        # Explicit no-entity signal: the document identifies nothing.
        record = evaluate_claim(
            SynthesisClaim(claim="Some tool exists", source="audio"),
            fetches={},
            entities=(),
        )
        self.assertEqual(record.verdict, "unnamed")
        self.assertIn("first-class", record.reason)
        self.assertIsNone(record.evidence)

    def test_identity_indeterminate_is_conservative_unverifiable(self) -> None:
        # Entities exist but none is mechanically mentioned in the claim.
        # A missing name match is never read as evidence the claim is
        # unnamed: conservative unverifiable with a clear reason. Also
        # asserts the entity's URLs are NOT fetched for such a claim.
        record = evaluate_claim(
            SynthesisClaim(claim="This tool has 12000 stars", source="audio"),
            fetches={},
            entities=(
                SynthesisEntity(name="FixtureTool", candidate_urls=[GITHUB_REPO_URL]),
            ),
        )
        self.assertEqual(record.verdict, "unverifiable")
        self.assertTrue(record.reason.startswith("no_candidate_urls"))
        self.assertIn("could not be determined", record.reason)
        self.assertEqual(record.fetches, ())

    def test_entity_inherited_url_retrieval_failure_is_unverifiable(self) -> None:
        claim = SynthesisClaim(claim="FixtureTool has docs", source="visual")
        entities = (
            SynthesisEntity(name="FixtureTool", candidate_urls=[GITHUB_REPO_URL]),
        )
        record = evaluate_claim(
            claim,
            fetches={GITHUB_REPO_URL: _failed_fetch(GITHUB_REPO_URL, "HTTP 500")},
            entities=entities,
        )
        self.assertEqual(record.verdict, "unverifiable")
        self.assertIn("every candidate retrieval failed", record.reason)
        self.assertIn("HTTP 500", record.reason)

    def test_entity_inherited_url_without_support_is_unverifiable(self) -> None:
        claim = SynthesisClaim(claim="FixtureTool has docs", source="visual")
        entities = (
            SynthesisEntity(name="FixtureTool", candidate_urls=[GITHUB_REPO_URL]),
        )
        record = evaluate_claim(
            claim,
            fetches={
                GITHUB_REPO_URL: _ok_fetch(GITHUB_REPO_URL, b"<p>unrelated page</p>")
            },
            entities=entities,
        )
        self.assertEqual(record.verdict, "unverifiable")
        self.assertIn("no supporting passage", record.reason)

    def test_operator_verdict_kept_for_identified_entity_without_urls(self) -> None:
        # Operator provenance is preserved verbatim: the verdict stays the
        # operator's, and the reason keeps stating it, never passing the
        # verdict off as an independent system check.
        record = evaluate_claim(
            SynthesisClaim(
                claim="Solid Roots ships worldwide",
                source="caption",
                verdict="confirmed",
                note="operator read the product page",
                evidence=["frame-042"],
            ),
            fetches={},
            entities=(SynthesisEntity(name="Solid Roots"),),
        )
        self.assertEqual(record.verdict, "confirmed")
        self.assertIn("operator-supplied", record.reason)
        self.assertEqual(record.note, "operator read the product page")
        self.assertEqual(record.evidence, "frame-042")
        self.assertEqual(record.fetches, ())

    def test_new_verdicts_survive_backlog_contract_serialization(self) -> None:
        # The new unverifiable/no_candidate_urls verdict must round-trip
        # through ClaimVerdictRecord.to_dict, the Claim contract and a
        # BacklogEntry serialization without any schema change.
        from tiktok_ingest.contracts import BacklogEntry, Claim

        record = evaluate_claim(
            SynthesisClaim(claim="Solid Roots ships worldwide", source="caption"),
            fetches={},
            entities=(SynthesisEntity(name="Solid Roots"),),
        )
        as_dict = record.to_dict()
        self.assertEqual(as_dict["verdict"], "unverifiable")
        claim = Claim(
            claim=record.claim,
            source=record.source,
            verdict=record.verdict,
            evidence=record.evidence,
            note=record.note,
        )
        entry = BacklogEntry(
            id="1" * 16,
            url="https://www.tiktok.com/@a/video/1",
            ingested_at="2026-09-21T00:00:00+00:00",
            unknown_classification_reason="serialization fixture",
            entities=["Solid Roots"],
            claims=[claim],
        )
        restored = BacklogEntry.from_dict(entry.to_dict())
        self.assertEqual(restored.claims[0].verdict, "unverifiable")
        self.assertEqual(restored.claims[0].claim, record.claim)
        self.assertEqual(restored.claims[0].source, record.source)

    def test_rules_version_bump_invalidates_recorded_verify_fingerprints(self) -> None:
        # A rules change must never let a recorded verify result be silently
        # reused: fingerprints embed VERIFY_RULES_VERSION, so version "1"
        # and version "2" fingerprints differ (recompute, not reuse). This
        # must not touch download/prepare/vision/audio/synthesis, which are
        # built from their own version inputs elsewhere.
        from tiktok_ingest.resume import fingerprints_equal

        media_sha = "a" * 64
        synthesis_sha = "b" * 64
        with unittest.mock.patch.object(config, "VERIFY_RULES_VERSION", "1"):
            v1_verify, v1_emit = verify_fingerprints(media_sha, synthesis_sha)
        with unittest.mock.patch.object(config, "VERIFY_RULES_VERSION", "2"):
            v2_verify, v2_emit = verify_fingerprints(media_sha, synthesis_sha)
        self.assertFalse(fingerprints_equal(v1_verify, v2_verify))
        self.assertFalse(fingerprints_equal(v1_emit, v2_emit))
        self.assertTrue(fingerprints_equal(v2_verify, v2_verify))


# --------------------------------------------------------------------------
# Stage orchestration + backlog emission
# --------------------------------------------------------------------------


class VerifyStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _fake_urlopen(self, body: bytes) -> FakeUrlopen:
        return FakeUrlopen(
            FakeVerifyResponse(body, final_url=GITHUB_REPO_URL)
        )

    def _run(self, state, urlopen, resolver=_public_resolver, force=False):
        return run_verify_stage(
            VIDEO_ID,
            state=state,
            urlopen=urlopen,
            resolver=resolver,
            force=force,
        )

    def test_refuses_without_complete_synthesis(self) -> None:
        from tiktok_ingest.tests.fixtures import make_prepared_job

        state, run_dir = make_prepared_job(self.tmp)
        outcome = self._run(state, FakeUrlopen())
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("synthesis stage is not complete", outcome.reason)
        self.assertFalse((state.root / "backlog.jsonl").exists())

    def test_happy_path_emits_exact_8_1_shape(self) -> None:
        state, run_dir = make_synthesized_job(self.tmp)
        outcome = self._run(state, self._fake_urlopen(github_star_page(3456)))
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        self.assertEqual(outcome.backlog_result, "appended")
        self.assertEqual(outcome.claims, 1)

        lines = (state.root / "backlog.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        # Exact PRD section 8.1 field set (plus the milestone-1 contract's
        # nullable unknown_classification_reason companion, null here
        # because classification is present).
        self.assertEqual(
            set(entry),
            {
                "id",
                "url",
                "author",
                "ingested_at",
                "status",
                "classification",
                "unknown_classification_reason",
                "entities",
                "claims",
                "fit",
                "actionable",
                "artifacts",
            },
        )
        self.assertEqual(entry["id"], VIDEO_ID)
        self.assertEqual(
            entry["url"],
            f"https://www.tiktok.com/@fixture-author/video/{VIDEO_ID}",
        )
        self.assertEqual(entry["author"], "fixture-author")
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["classification"]["root"], "tecnología")
        self.assertEqual(entry["classification"]["subgroup"], "cli-agentes")
        self.assertEqual(entry["classification"]["confidence"], 0.9)
        self.assertEqual(entry["entities"], ["FixtureTool", "Plain Entity"])
        self.assertEqual(entry["fit"], "adjacent")
        self.assertEqual(entry["actionable"], "Evaluate FixtureTool for CLI work")
        self.assertEqual(entry["artifacts"], f"runs/run-1/{VIDEO_ID}/")
        self.assertEqual(len(entry["claims"]), 1)
        claim = entry["claims"][0]
        self.assertEqual(
            set(claim), {"claim", "source", "verdict", "evidence", "note"}
        )
        self.assertEqual(claim["verdict"], "overstated")
        evidence = json.loads(claim["evidence"])
        self.assertEqual(evidence["source_url"], GITHUB_REPO_URL)
        self.assertIn("passage", evidence)

    def test_taxonomy_normalization_persists(self) -> None:
        state, _ = make_synthesized_job(self.tmp)
        self._run(state, self._fake_urlopen(github_star_page(3456)))
        taxonomy = Taxonomy(state)
        entry = taxonomy.lookup("CLI Agentes")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.subgroup, "cli-agentes")
        self.assertEqual(entry.root, "tecnología")

    def test_resume_reuses_and_zero_new_entries(self) -> None:
        state, _ = make_synthesized_job(self.tmp)
        first = self._run(state, self._fake_urlopen(github_star_page(3456)))
        self.assertEqual(first.backlog_result, "appended")
        second = self._run(state, self._fake_urlopen(github_star_page(3456)))
        self.assertTrue(second.reused)
        self.assertEqual(second.backlog_result, "already_emitted")
        lines = (state.root / "backlog.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)

    def test_forced_rerun_is_duplicate_guarded(self) -> None:
        state, _ = make_synthesized_job(self.tmp)
        self._run(state, self._fake_urlopen(github_star_page(3456)))
        forced = self._run(
            state, self._fake_urlopen(github_star_page(3456)), force=True
        )
        self.assertFalse(forced.reused)
        self.assertEqual(forced.backlog_result, "skipped_duplicate")
        lines = (state.root / "backlog.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)

    def test_changed_synthesis_invalidates_verify(self) -> None:
        state, run_dir = make_synthesized_job(self.tmp)
        self._run(state, self._fake_urlopen(github_star_page(3456)))
        synthesis_path = run_dir / "synthesis.json"
        document = sample_synthesis_document()
        document["fit"] = "core"
        synthesis_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        outcome = self._run(state, self._fake_urlopen(github_star_page(3456)))
        self.assertFalse(outcome.reused)
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)

    def test_verification_json_persists_records(self) -> None:
        state, run_dir = make_synthesized_job(self.tmp)
        self._run(state, self._fake_urlopen(github_star_page(3456)))
        document = json.loads(
            (run_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(document["schema"], 1)
        self.assertEqual(len(document["claims"]), 1)
        self.assertEqual(len(document["fetches"]), 1)
        self.assertEqual(document["fetches"][0]["url"], GITHUB_REPO_URL)
        self.assertEqual(document["fetches"][0]["status"], 200)
        self.assertEqual(document["backlog"]["result"], "appended")

    def test_blocklist_blocks_verify(self) -> None:
        from tiktok_ingest.contracts import BlocklistEntry
        from tiktok_ingest.state import Blocklist, Processed

        state, _ = make_synthesized_job(self.tmp)
        Blocklist(state).reject(
            BlocklistEntry(video_id=VIDEO_ID, rejected_at=FIXED_NOW, reason="no")
        )
        outcome = self._run(state, FakeUrlopen())
        self.assertEqual(outcome.outcome, StageOutcome.BLOCKED.value)

    def test_missing_inventory_is_failure(self) -> None:
        from tiktok_ingest.state import InventoryStore

        state, _ = make_synthesized_job(self.tmp)
        InventoryStore(state).save([])  # remove the inventory record
        outcome = self._run(state, self._fake_urlopen(github_star_page(3456)))
        self.assertEqual(outcome.outcome, StageOutcome.FAILED.value)
        self.assertIn("inventory record", outcome.reason)

    def test_refused_candidate_makes_claim_unverifiable(self) -> None:
        state, _ = make_synthesized_job(self.tmp)
        outcome = self._run(state, FakeUrlopen(_http_error(404)))
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        lines = (state.root / "backlog.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        self.assertEqual(entry["claims"][0]["verdict"], "unverifiable")

    def test_unnamed_claim_without_candidates(self) -> None:
        document = sample_synthesis_document()
        document["claims"] = [
            {"claim": "Una herramienta sin nombre", "source": "audio"},
        ]
        # Explicit no-entity signal: the document identifies NO entity, so
        # the claim is genuinely unnamed (rules version 2 semantics).
        document["entities"] = []
        state, _ = make_synthesized_job(self.tmp, document=document)
        outcome = self._run(state, FakeUrlopen())  # nothing may be fetched
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        self.assertEqual(outcome.claims, 1)
        lines = (state.root / "backlog.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        self.assertEqual(entry["claims"][0]["verdict"], "unnamed")

    def test_indeterminate_identity_claim_is_unverifiable(self) -> None:
        document = sample_synthesis_document()
        document["claims"] = [
            {"claim": "Una herramienta sin nombre", "source": "audio"},
        ]
        # Entities exist but none appears in the claim: identity is
        # indeterminate from explicit signals -> conservative unverifiable,
        # never unnamed.
        document["entities"] = ["Plain Entity"]
        state, run_dir = make_synthesized_job(self.tmp, document=document)
        outcome = self._run(state, FakeUrlopen())  # nothing may be fetched
        self.assertEqual(outcome.outcome, StageOutcome.COMPLETE.value)
        self.assertEqual(outcome.claims, 1)
        # The mechanical reason trail lives in verification.json; the
        # backlog Claim contract carries the verdict only.
        verification = json.loads(
            (run_dir / "verification.json").read_text(encoding="utf-8")
        )
        claim = verification["claims"][0]
        self.assertEqual(claim["verdict"], "unverifiable")
        self.assertIn("no_candidate_urls", claim["reason"])

    def test_status_reports_stage_and_backlog_state(self) -> None:
        from tiktok_ingest.pipeline import video_status

        state, _ = make_synthesized_job(self.tmp)
        self._run(state, self._fake_urlopen(github_star_page(3456)))
        report = video_status(VIDEO_ID, state=state)
        record = report["items"][0]
        self.assertEqual(record["stage_outcomes"]["synthesis"], "complete")
        self.assertEqual(record["stage_outcomes"]["verify"], "complete")
        self.assertEqual(record["stage_outcomes"]["emit"], "complete")
        self.assertTrue(record["backlog"]["emitted"])
        self.assertEqual(record["backlog"]["status"], "pending")

    def test_stage_records_written(self) -> None:
        from tiktok_ingest.contracts import JobManifest

        state, run_dir = make_synthesized_job(self.tmp)
        self._run(state, self._fake_urlopen(github_star_page(3456)))
        manifest = JobManifest.from_dict(
            json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        )
        self.assertEqual(
            manifest.stages["verify"].result.outcome, StageOutcome.COMPLETE
        )
        self.assertEqual(
            manifest.stages["emit"].result.outcome, StageOutcome.COMPLETE
        )
        self.assertEqual(
            manifest.stages["emit"].result.reason, "backlog entry appended"
        )
        self.assertTrue(manifest.stages["verify"].fingerprint)
        self.assertTrue(manifest.stages["emit"].fingerprint)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
