"""Bounded, safe verification stage (MVP-PRD section 3.D, PRD sections 6.6-6.8).

Boundary rules encoded here:

- CREDENTIAL-FREE and SEARCH-FREE: nothing is auto-searched. The only URLs
  ever fetched are the operator/model-curated ``candidate_urls`` recorded
  in the synthesis document (per claim or per entity). Text from media,
  transcripts or fetched pages is UNTRUSTED DATA: it is matched
  mechanically, never followed as instructions, and never used to discover
  new fetch targets.
- Every candidate URL — and every redirect hop target — passes through the
  pure :func:`validate_public_http_url` validator (public http(s) only;
  loopback, private, link-local, metadata, credential-bearing and
  non-default-port URLs are refused) BEFORE it is fetched. The default
  transport additionally disables urllib's automatic redirect following so
  this stage owns and validates every hop.
- Retrieval is bounded (2 MiB body cap, 15 s timeout, 3 redirect cap)
  through an injectable ``urlopen``. A failed retrieval is an explicit
  ``unverifiable`` reason for that claim, never an invented source.
- ``unnamed`` is a first-class outcome (PRD section 6.6) but it means ONE
  specific thing: the entity could not be identified. A claim whose subject
  IS identified (an entity name from the synthesis document mechanically
  appears in it) but which has no candidate source is ``unverifiable``
  with an explicit ``no_candidate_urls`` reason — absence of URLs is never
  read as absence of a name. No adoption-relevant verdict is manufactured
  for either outcome.

The stage emits the local backlog entry (PRD section 8.1) through the
existing state machinery with taxonomy subgroup normalization, atomic
append and duplicate-emission prevention on resume. It never writes to
Notion, Gertru or any external system.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html
import ipaddress
import json
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from . import config
from .contracts import (
    BacklogEntry,
    Claim,
    JobManifest,
    StageOutcome,
    StageRecord,
    StageResult,
    utc_now_iso,
)
from .resume import build_fingerprint, emit_backlog_entry
from .state import (
    Backlog,
    Blocklist,
    InventoryStore,
    Processed,
    StateRoot,
    Taxonomy,
    atomic_write_bytes,
)
from .synthesis import SynthesisClaim, SynthesisDocument, SynthesisEntity

# Stage artifact name inside the run directory.
VERIFICATION_FILENAME = "verification.json"


class VerifyError(RuntimeError):
    """Raised for verification errors that surface as outcome ``failed``."""


class UnsafeURLError(ValueError):
    """Raised by the pure URL validator when a target must not be fetched."""


# --------------------------------------------------------------------------
# Pure safe-URL validation (heavily tested; no DNS, no network, no I/O)
# --------------------------------------------------------------------------

# Literal-IP networks that must never be fetched: loopback, private ranges,
# link-local (including the 169.254.169.254 metadata endpoint), CGNAT,
# unspecified and multicast/reserved space.
_REFUSED_V4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/3",
    )
)

# IPv6: loopback (::1), unspecified (::), unique-local (fc00::/7, which
# covers fd00::/8), link-local (fe80::/10) and multicast.
_REFUSED_V6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(net)
    for net in (
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

# Only the default HTTP(S) ports are acceptable; anything else is refused
# (bounded retrieval targets public first-party pages, not odd services).
_ALLOWED_PORTS = {None, 80, 443}


def refused_ip_reason(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Refusal reason for a literal IP address, or ``None`` when acceptable.

    IPv4-mapped IPv6 addresses (``::ffff:127.0.0.1``) are reduced to their
    IPv4 form and checked against the IPv4 rules so the mapped form cannot
    smuggle a private target past the validator.
    """
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return refused_ip_reason(mapped)
        for net in _REFUSED_V6_NETWORKS:
            if address in net:
                return f"IPv6 address {address} is inside refused network {net}"
        return None
    for net in _REFUSED_V4_NETWORKS:
        if address in net:
            return f"IPv4 address {address} is inside refused network {net}"
    return None


def validate_public_http_url(url: Any) -> str:
    """Validate one ABSOLUTE public http(s) URL; return it unchanged.

    Refusals (each raising :class:`UnsafeURLError` with an explicit
    reason): non-strings, empty or whitespace/control-character URLs,
    non-http(s) schemes, missing hosts, credentials in the URL
    (``user:pass@``), non-default ports, ``localhost`` names, and literal
    IPs in refused ranges (loopback, private, link-local/metadata,
    unique-local IPv6, IPv4-mapped IPv6).
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL must be a non-empty string")
    if re.search(r"[\s\x00-\x1f\x7f]", url):
        raise UnsafeURLError("URL contains whitespace or control characters")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port  # may raise ValueError for invalid ports
        hostname = parts.hostname  # may raise ValueError for malformed netlocs
    except ValueError as exc:
        raise UnsafeURLError(f"URL cannot be parsed safely: {exc}") from None
    scheme = parts.scheme.lower()
    if scheme not in config.VERIFY_ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"URL scheme {parts.scheme!r} refused; only "
            f"{list(config.VERIFY_ALLOWED_SCHEMES)} targets are fetched"
        )
    if not hostname:
        raise UnsafeURLError("URL has no host")
    lowered = hostname.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise UnsafeURLError(f"loopback host {hostname!r} refused")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("credential-bearing URL (user:pass@host) refused")
    if port not in _ALLOWED_PORTS:
        raise UnsafeURLError(f"non-default port {port} refused")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return url  # a DNS name: literal-IP rules do not apply here
    reason = refused_ip_reason(address)
    if reason is not None:
        raise UnsafeURLError(reason)
    return url


# --------------------------------------------------------------------------
# Transport: bounded retrieval with validated redirects
# --------------------------------------------------------------------------


class _NoAutoRedirect(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that DISABLES urllib's auto-following.

    Redirect safety belongs to :func:`bounded_fetch`, which validates each
    hop target through :func:`validate_public_http_url` before issuing the
    next request. Without this handler urllib would follow redirects on its
    own, potentially fetching a refused target before any check runs.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def build_verify_opener() -> urllib.request.OpenerDirector:
    """Opener used by the default transport (no automatic redirects)."""
    return urllib.request.build_opener(_NoAutoRedirect())


def default_verify_urlopen(req: urllib.request.Request, timeout: float) -> Any:
    """Default HTTP transport for verification retrieval (stdlib)."""
    return build_verify_opener().open(req, timeout=timeout)


Resolver = Callable[..., list[tuple[Any, ...]]]


@dataclasses.dataclass(frozen=True)
class FetchRecord:
    """One bounded retrieval attempt with its full provenance."""

    url: str
    final_url: str
    status: int | None
    ok: bool
    sha256: str | None
    body_text: str | None
    retrieved_at: str
    duration_seconds: float
    error: str | None
    hops: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _check_resolved_host(
    hostname: str, resolver: Resolver
) -> str | None:
    """Resolve a DNS name and refuse when ANY address lands in refused space.

    Pure-literal URLs never reach this (the validator already covered
    them); this closes the DNS-rebinding-shaped gap where a friendly name
    resolves to a private address. Returns the refusal reason or ``None``.
    """
    try:
        infos = resolver(hostname, 80, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return f"DNS resolution failed for {hostname!r}: {exc}"
    if not infos:
        return f"DNS resolution returned no address for {hostname!r}"
    for info in infos:
        raw = info[4][0] if info[4] else ""
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        reason = refused_ip_reason(address)
        if reason is not None:
            return f"{hostname!r} resolves to a refused target: {reason}"
    return None


def bounded_fetch(
    url: str,
    *,
    urlopen: Callable[..., Any] = default_verify_urlopen,
    resolver: Resolver = socket.getaddrinfo,
    max_bytes: int = config.VERIFY_MAX_RESPONSE_BYTES,
    timeout: float = config.VERIFY_TIMEOUT_SECONDS,
    max_redirects: int = config.VERIFY_MAX_REDIRECTS,
    now_fn: Callable[[], str] = utc_now_iso,
    monotonic: Callable[[], float] = time.monotonic,
) -> FetchRecord:
    """GET one candidate URL with hard caps and validated redirects.

    Refused targets are NEVER fetched: an unsafe redirect target fails the
    retrieval with an explicit reason while other claims continue. The
    final URL (after any hops) is validated again as defense in depth.
    """
    started = monotonic()
    retrieved_at = now_fn()

    def record(
        *,
        ok: bool,
        status: int | None = None,
        sha256: str | None = None,
        body_text: str | None = None,
        error: str | None = None,
        final_url: str | None = None,
        hops: tuple[str, ...] = (),
    ) -> FetchRecord:
        return FetchRecord(
            url=url,
            final_url=final_url or url,
            status=status,
            ok=ok,
            sha256=sha256,
            body_text=body_text,
            retrieved_at=retrieved_at,
            duration_seconds=round(monotonic() - started, 3),
            error=error,
            hops=hops,
        )

    try:
        current = validate_public_http_url(url)
    except UnsafeURLError as exc:
        return record(ok=False, error=f"refused target: {exc}")

    hops: list[str] = []
    for _ in range(max_redirects + 1):
        try:
            parsed = urllib.parse.urlsplit(current)
            hostname = parsed.hostname or ""
        except ValueError as exc:
            return record(ok=False, error=f"URL cannot be parsed safely: {exc}")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is None and hostname:
            reason = _check_resolved_host(hostname, resolver)
            if reason is not None:
                return record(ok=False, error=f"refused target: {reason}")

        request = urllib.request.Request(
            current, headers={"User-Agent": config.VERIFY_USER_AGENT}
        )
        try:
            response = urlopen(request, timeout)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                location = exc.headers.get("Location") if exc.headers else None
                next_target = _resolve_redirect(
                    current, location, hops, record, max_redirects
                )
                if isinstance(next_target, FetchRecord):
                    return next_target
                current = next_target
                continue
            return record(ok=False, status=exc.code, error=f"HTTP {exc.code}")
        except UnsafeURLError as exc:  # defensive: transport-side validation
            return record(ok=False, error=f"refused target: {exc}")
        except Exception as exc:  # noqa: BLE001 - explicit retrieval failure
            return record(ok=False, error=f"retrieval failed: {exc}")

        status = _response_status(response)
        if status is not None and 300 <= status < 400:
            headers = getattr(response, "headers", None)
            location = headers.get("Location") if headers else None
            next_target = _resolve_redirect(
                current, location, hops, record, max_redirects
            )
            if isinstance(next_target, FetchRecord):
                return next_target
            current = next_target
            continue
        if status is not None and status >= 400:
            return record(
                ok=False, status=status, error=f"HTTP {status}"
            )

        try:
            data = response.read(max_bytes + 1)
        except Exception as exc:  # noqa: BLE001 - read failures are explicit
            return record(ok=False, status=status, error=f"reading body failed: {exc}")
        if len(data) > max_bytes:
            return record(
                ok=False,
                status=status,
                error=(
                    f"response body exceeds the {max_bytes}-byte cap; "
                    "never silently truncated"
                ),
            )
        final_url = current
        geturl = getattr(response, "geturl", None)
        if callable(geturl):
            try:
                final_url = geturl() or current
            except Exception:  # noqa: BLE001 - final URL stays best-effort
                final_url = current
        try:
            validate_public_http_url(final_url)
        except UnsafeURLError as exc:
            return record(
                ok=False,
                status=status,
                error=f"refused final URL after redirects: {exc}",
                hops=tuple(hops),
            )
        return record(
            ok=True,
            status=status,
            sha256=hashlib.sha256(data).hexdigest(),
            body_text=data.decode("utf-8", "replace"),
            final_url=final_url,
            hops=tuple(hops),
        )
    return record(
        ok=False,
        error=f"too many redirects (cap {max_redirects}); last target {current!r}",
        hops=tuple(hops),
    )


def _response_status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        code = getcode()
        if isinstance(code, int):
            return code
    return None


def _resolve_redirect(
    current: str,
    location: Any,
    hops: list[str],
    record: Callable[..., FetchRecord],
    max_redirects: int,
) -> "FetchRecord | str":
    """Validate one redirect hop; return the next URL or a failed record.

    The hop cap is enforced BEFORE the new target is ever fetched: the
    exceeding hop is validated but refused, and the recorded hop chain
    never exceeds ``max_redirects``.
    """
    if not location or not isinstance(location, str):
        return record(ok=False, error="redirect response without a Location header")
    target = urllib.parse.urljoin(current, location)
    try:
        validate_public_http_url(target)
    except UnsafeURLError as exc:
        return record(
            ok=False,
            error=f"refused redirect target {location!r}: {exc}",
            hops=tuple(hops),
        )
    if len(hops) >= max_redirects:
        return record(
            ok=False,
            error=(
                f"too many redirects (cap {max_redirects}); refused hop "
                f"{target!r} was never fetched"
            ),
            hops=tuple(hops),
        )
    hops.append(target)
    return target


# --------------------------------------------------------------------------
# Pure evidence extraction (mechanical only; no model interpretation)
# --------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "the a an and or of to in on for with that this it is are was were be by "
    "as at from que de la el en los las un una del por con para es son era "
    "como mas más su sus y o al se lo suy hay va voy".split()
)

_WORD_RE = re.compile(r"[\w%€$°]+", re.UNICODE)


def _normalize_text(text: str) -> str:
    """Mechanical normalization: strip tags, unescape entities, fold case."""
    stripped = re.sub(r"<[^>]+>", " ", text)
    unescaped = html.unescape(stripped)
    decomposed = unicodedata.normalize("NFKD", unescaped)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_marks).strip().lower()


def claim_tokens(claim: str) -> tuple[str, ...]:
    """Significant normalized tokens of a claim (stopwords dropped)."""
    normalized = _normalize_text(claim)
    tokens = []
    for token in _WORD_RE.findall(normalized):
        if token in _STOPWORDS or len(token) < 2:
            continue
        tokens.append(token)
    return tuple(tokens)


def extract_passage(
    body: str, claim: str, *, min_tokens: int = 2, window: int = 240
) -> str | None:
    """Mechanical supporting-passage extraction, or ``None``.

    Support rule (deterministic, no model interpretation): every NUMERIC
    token of the claim must appear in the normalized body, and enough
    tokens overall must match (``min_tokens``, or the single token itself
    for one-token claims). The returned passage is a normalized window of
    the page around the first match — evidence text, never instructions.
    """
    normalized_body = _normalize_text(body)
    tokens = claim_tokens(claim)
    if not tokens:
        return None
    numeric = [token for token in tokens if any(ch.isdigit() for ch in token)]
    found = [token for token in tokens if token in normalized_body]
    if numeric and not all(token in normalized_body for token in numeric):
        return None
    required = min_tokens if len(tokens) > 1 else 1
    if len(found) < max(required, 1):
        return None
    position = normalized_body.find(found[0])
    start = max(0, position - 80)
    return normalized_body[start : position + window].strip()


_NUMBER_TEXT_RE = re.compile(
    r"^([0-9][0-9,.]*)\s*([kKmM]?)(?:\s*stars)?[%€$°]?$"
)


def _parse_number_text(text: str) -> int | None:
    """Parse ``3,456``, ``1.2k``, ``12k``, ``90%`` or ``1234`` into an int."""
    match = _NUMBER_TEXT_RE.match(text.strip())
    if match is None:
        return None
    digits, suffix = match.groups()
    try:
        if suffix:
            value = float(digits.replace(",", ""))
            return int(value * (1000 if suffix.lower() == "k" else 1_000_000))
        if "," in digits:
            return int(digits.replace(",", ""))
        if "." in digits:
            whole, _, fraction = digits.partition(".")
            if len(fraction) == 3:  # thousands separator form: 12.345
                return int(whole + fraction)
            return int(float(digits))
        return int(digits)
    except ValueError:
        return None


_OG_DESCRIPTION_RE = re.compile(
    r"<meta[^>]+property=[\"']og:description[\"'][^>]+content=[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
_STARS_TEXT_RE = re.compile(r"([0-9][0-9.,]*\s*[kKmM]?)\s*\*?\s*stars?", re.IGNORECASE)
_STAR_JSON_RE = re.compile(
    r'"(?:stargazers_count|starCount|stars)"\s*:\s*"?([0-9][0-9,_]*)"?'
)


def extract_github_stars(body: str) -> int | None:
    """Pure GitHub star-count parser: embedded JSON first, then og:text.

    Both shapes are mechanical parses of fetched page text (og-description
    or embedded JSON counts are acceptable per the contract). Returns
    ``None`` when absent or malformed — never a guess.
    """
    for match in _STAR_JSON_RE.finditer(body):
        digits = match.group(1).replace(",", "").replace("_", "")
        try:
            return int(digits)
        except ValueError:
            continue
    og = _OG_DESCRIPTION_RE.search(body)
    if og is not None:
        stars = _STARS_TEXT_RE.search(og.group(1))
        if stars is not None:
            return _parse_number_text(stars.group(1))
    stars = _STARS_TEXT_RE.search(body)
    if stars is not None:
        return _parse_number_text(stars.group(1))
    return None


def parse_claim_number(claim: str) -> int | None:
    """First numeric token of a claim as an integer (``90%`` -> 90)."""
    for token in claim_tokens(claim):
        if any(ch.isdigit() for ch in token):
            parsed = _parse_number_text(token)
            if parsed is not None:
                return parsed
    return None


def compare_star_claim(claimed: int, actual: int) -> str:
    """Pure verdict comparison for numeric claims; both numbers are recorded.

    Thresholds: ``actual >= claimed`` -> ``confirmed``; ``0 < actual <
    claimed`` -> ``overstated`` (real but weaker than presented);
    ``actual == 0`` -> ``contradicted``.
    """
    if actual >= claimed:
        return "confirmed"
    if actual > 0:
        return "overstated"
    return "contradicted"


# --------------------------------------------------------------------------
# Claim evaluation (pure given fetch records)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ClaimVerdictRecord:
    """One claim's final verdict with its mechanical decision trail."""

    claim: str
    source: str
    verdict: str
    evidence: str | None
    note: str | None
    reason: str
    fetches: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _mentioned_entities(
    claim_text: str, entities: tuple[SynthesisEntity, ...]
) -> tuple[SynthesisEntity, ...]:
    """Entities whose normalized name mechanically appears in the claim.

    This is the ONLY identity signal evaluate_claim uses: an explicit
    name from the operator/model-curated entity list contained in the
    claim text. No semantic matching is attempted.
    """
    normalized_claim = _normalize_text(claim_text)
    return tuple(
        entity
        for entity in entities
        if _normalize_text(entity.name)
        and _normalize_text(entity.name) in normalized_claim
    )


def _entity_candidate_urls(
    claim_text: str, entities: tuple[SynthesisEntity, ...]
) -> tuple[str, ...]:
    """Candidate URLs of entities mechanically mentioned in the claim."""
    urls: list[str] = []
    for entity in _mentioned_entities(claim_text, entities):
        urls.extend(entity.candidate_urls)
    return tuple(urls)


def evaluate_claim(
    claim: SynthesisClaim,
    fetches: Mapping[str, FetchRecord],
    entities: tuple[SynthesisEntity, ...] = (),
) -> ClaimVerdictRecord:
    """Assign the final verdict for one claim. Pure; nothing is fetched here.

    Matrix (PRD section 6.6, with the identity/source separation):
    - operator-supplied verdict (when no candidate URL exists) -> kept
      verbatim with its note and an explicit operator-provenance reason;
    - no candidate URL and the synthesis document identifies NO entity ->
      ``unnamed`` (first-class outcome: the entity could not be named);
    - no candidate URL but the claim mechanically names an identified
      entity -> ``unverifiable`` with a ``no_candidate_urls`` reason
      (identity known, no first-party source to contrast against);
    - no candidate URL, entities exist but none appears in the claim ->
      ``unverifiable`` with an explicit indeterminate-identity reason
      (conservative: a missing name match is never read as evidence the
      claim is unnamed);
    - every retrieval failed -> ``unverifiable`` with the reasons;
    - a fetched source yields a mechanical supporting passage ->
      ``confirmed``;
    - a GitHub star count and a claimed number disagree -> ``overstated``
      or ``contradicted`` through :func:`compare_star_claim`;
    - fetched sources, no support -> ``unverifiable`` with the reason.
    Contradiction is NEVER inferred from a missing passage: mechanical
    absence is not disagreement.
    """
    mentioned = _mentioned_entities(claim.claim, entities)
    candidate_urls = claim.candidate_urls or tuple(
        url for entity in mentioned for url in entity.candidate_urls
    )
    fetch_dicts = tuple(
        fetches[url].to_dict() for url in candidate_urls if url in fetches
    )
    if not candidate_urls:
        if claim.verdict is not None:
            return ClaimVerdictRecord(
                claim=claim.claim,
                source=claim.source,
                verdict=claim.verdict,
                evidence="; ".join(claim.evidence) if claim.evidence else None,
                note=claim.note,
                reason="kept operator-supplied verdict; no candidate URL "
                "supplied and nothing was auto-searched",
                fetches=(),
            )
        if mentioned:
            names = ", ".join(f'"{entity.name}"' for entity in mentioned)
            return ClaimVerdictRecord(
                claim=claim.claim,
                source=claim.source,
                verdict="unverifiable",
                evidence=None,
                note=claim.note,
                reason="no_candidate_urls: the claim names identified "
                f"entity/entities {names} but no first-party candidate URL "
                "was supplied to contrast against; nothing was "
                "auto-searched (unverifiable, not unnamed: the entity IS "
                "identified)",
                fetches=(),
            )
        if entities:
            return ClaimVerdictRecord(
                claim=claim.claim,
                source=claim.source,
                verdict="unverifiable",
                evidence=None,
                note=claim.note,
                reason="no_candidate_urls: no entity name from the "
                "synthesis document appears in this claim, so the claim's "
                "entity identity could not be determined from explicit "
                "signals; no candidate URL was supplied and nothing was "
                "auto-searched (conservative: a missing name match is not "
                "evidence the claim is unnamed)",
                fetches=(),
            )
        return ClaimVerdictRecord(
            claim=claim.claim,
            source=claim.source,
            verdict="unnamed",
            evidence=None,
            note=claim.note,
            reason="no entity is identified by the synthesis document and "
            "no first-party candidate URL was supplied; the claim's "
            "subject could not be named (unnamed is a first-class "
            "outcome); nothing was auto-searched",
            fetches=(),
        )

    records = [fetches[url] for url in candidate_urls if url in fetches]
    ok_records = [item for item in records if item.ok]
    if not ok_records:
        reasons = "; ".join(
            f"{item.url}: {item.error or 'unknown retrieval failure'}"
            for item in records
        ) or "no retrieval was attempted"
        return ClaimVerdictRecord(
            claim=claim.claim,
            source=claim.source,
            verdict="unverifiable",
            evidence=None,
            note=claim.note,
            reason=f"every candidate retrieval failed: {reasons}",
            fetches=fetch_dicts,
        )

    claimed_number = parse_claim_number(claim.claim)
    wants_stars = "star" in _normalize_text(claim.claim)
    no_support: list[str] = []
    for item in ok_records:
        body = item.body_text or ""
        if wants_stars and claimed_number is not None:
            actual = extract_github_stars(body)
            if actual is not None:
                verdict = compare_star_claim(claimed_number, actual)
                passage = extract_passage(body, claim.claim) or ""
                evidence = json.dumps(
                    {
                        "source_url": item.final_url,
                        "retrieved_at": item.retrieved_at,
                        "passage": passage,
                        "sha256": item.sha256,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                return ClaimVerdictRecord(
                    claim=claim.claim,
                    source=claim.source,
                    verdict=verdict,
                    evidence=evidence,
                    note=(
                        f"claim states {claimed_number}; source reports {actual} "
                        f"(mechanical star-count comparison)"
                    ),
                    reason=f"numeric comparison against fetched source {item.url}",
                    fetches=fetch_dicts,
                )
            no_support.append(f"{item.url}: no star count found")
        passage = extract_passage(body, claim.claim)
        if passage is not None:
            evidence = json.dumps(
                {
                    "source_url": item.final_url,
                    "retrieved_at": item.retrieved_at,
                    "passage": passage,
                    "sha256": item.sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            return ClaimVerdictRecord(
                claim=claim.claim,
                source=claim.source,
                verdict="confirmed",
                evidence=evidence,
                note=claim.note,
                reason=f"mechanical token match in fetched source {item.url}",
                fetches=fetch_dicts,
            )
        no_support.append(f"{item.url}: no supporting passage found")
    return ClaimVerdictRecord(
        claim=claim.claim,
        source=claim.source,
        verdict="unverifiable",
        evidence=None,
        note=claim.note,
        reason="fetched "
        f"{len(ok_records)} candidate source(s); no supporting passage: "
        + "; ".join(no_support),
        fetches=fetch_dicts,
    )


# --------------------------------------------------------------------------
# Stage orchestration
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class VerifyOutcome:
    """Result of one verify invocation, reusable by CLI and tests."""

    outcome: str
    reason: str
    reused: bool
    video_id: str
    run_dir: str | None = None
    backlog_result: str | None = None
    claims: int | None = None


def verify_fingerprints(media_sha256: str, synthesis_sha256: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resume fingerprints for the verify and emit stages."""
    versions = {
        "synthesis_sha256": synthesis_sha256,
        "rules_version": config.VERIFY_RULES_VERSION,
    }
    return (
        build_fingerprint("verify", media_sha256, versions),
        build_fingerprint("emit", media_sha256, versions),
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


def _failure_record(
    manifest: JobManifest,
    manifest_path: Path,
    *,
    outcome: StageOutcome,
    reason: str,
    started_at: str,
) -> None:
    finished_at = utc_now_iso()
    manifest.stages["verify"] = StageRecord(
        stage="verify",
        result=StageResult(outcome=outcome, reason=reason),
        fingerprint={},
        started_at=started_at,
        finished_at=finished_at,
    )
    manifest.updated_at = finished_at
    _write_manifest(manifest_path, manifest)


def run_verify_stage(
    video_id: str,
    *,
    state: StateRoot,
    urlopen: Callable[..., Any] = default_verify_urlopen,
    resolver: Resolver = socket.getaddrinfo,
    force: bool = False,
) -> VerifyOutcome:
    """Verify the synthesized claims and emit the local backlog entry.

    Refuses to run without a COMPLETE synthesis stage (fail closed). All
    retrieval goes through :func:`bounded_fetch` with the injected
    transport; the backlog append is atomic and duplicate-guarded, so an
    unchanged rerun performs zero new emissions.
    """
    state.ensure_layout()
    if Blocklist(state).is_blocked(video_id):
        return VerifyOutcome(
            outcome=StageOutcome.BLOCKED.value,
            reason="video ID is permanently blocklisted; no flag can override "
            "an operator rejection",
            reused=False,
            video_id=video_id,
        )

    entry = Processed(state).get(video_id)
    if entry is None:
        return VerifyOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="no fetch record for this ID; run fetch-url first",
            reused=False,
            video_id=video_id,
        )
    if not entry.artifacts:
        return VerifyOutcome(
            outcome=StageOutcome.FAILED.value,
            reason="fetch record has no run directory reference",
            reused=False,
            video_id=video_id,
        )
    run_dir = state.root / entry.artifacts[0]
    manifest_path = run_dir / "meta.json"
    manifest = _load_manifest(manifest_path, video_id, entry.media_sha256)

    synthesis_record = manifest.stages.get("synthesis")
    synthesis_path = run_dir / "synthesis.json"
    if (
        synthesis_record is None
        or synthesis_record.result.outcome is not StageOutcome.COMPLETE
        or not synthesis_path.is_file()
    ):
        reason = (
            "synthesis stage is not complete; verify refuses to run without "
            "a complete synthesis stage (fail closed)"
        )
        started_at = utc_now_iso()
        _failure_record(
            manifest, manifest_path,
            outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
        )
        return VerifyOutcome(
            outcome=StageOutcome.FAILED.value,
            reason=reason,
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    synthesis_bytes = synthesis_path.read_bytes()
    synthesis_sha256 = hashlib.sha256(synthesis_bytes).hexdigest()
    verify_fp, emit_fp = verify_fingerprints(entry.media_sha256, synthesis_sha256)
    verification_path = run_dir / VERIFICATION_FILENAME

    recorded = manifest.stages.get("verify")
    emit_recorded = manifest.stages.get("emit")
    if (
        not force
        and recorded is not None
        and recorded.result.outcome is StageOutcome.COMPLETE
        and recorded.fingerprint == verify_fp
        and emit_recorded is not None
        and emit_recorded.result.outcome is StageOutcome.COMPLETE
        and verification_path.is_file()
        and Backlog(state).has(video_id)
    ):
        return VerifyOutcome(
            outcome=StageOutcome.COMPLETE.value,
            reason="verify fingerprints, verification.json and the backlog "
            "entry match the recorded run; zero re-emission performed",
            reused=True,
            video_id=video_id,
            run_dir=str(run_dir),
            backlog_result="already_emitted",
            claims=None,
        )

    started_at = utc_now_iso()
    try:
        document = SynthesisDocument.from_dict(
            json.loads(synthesis_bytes.decode("utf-8"))
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        reason = f"synthesis.json is not a valid synthesis document: {exc}"
        _failure_record(
            manifest, manifest_path,
            outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
        )
        return VerifyOutcome(
            outcome=StageOutcome.FAILED.value,
            reason=reason,
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    inventory_entries = {
        item.stable_id: item for item in InventoryStore(state).load()
    }
    inventory = inventory_entries.get(video_id)
    if inventory is None:
        reason = "no inventory record for this ID; the canonical URL and author are unknown"
        _failure_record(
            manifest, manifest_path,
            outcome=StageOutcome.FAILED, reason=reason, started_at=started_at,
        )
        return VerifyOutcome(
            outcome=StageOutcome.FAILED.value,
            reason=reason,
            reused=False,
            video_id=video_id,
            run_dir=str(run_dir),
        )

    # Fetch every distinct candidate URL through the bounded, validating
    # transport. Untrusted media text never contributes fetch targets.
    candidates: list[str] = []
    for claim in document.claims:
        for url in claim.candidate_urls:
            if url not in candidates:
                candidates.append(url)
    for entity in document.entities:
        for url in entity.candidate_urls:
            if url not in candidates:
                candidates.append(url)
    fetches: dict[str, FetchRecord] = {}
    for url in candidates:
        fetches[url] = bounded_fetch(url, urlopen=urlopen, resolver=resolver)

    records = [
        evaluate_claim(claim, fetches, document.entities) for claim in document.claims
    ]

    taxonomy = Taxonomy(state)
    taxonomy_entry, _ = taxonomy.add(
        document.classification.root,
        document.classification.subgroup,
        origin_video_id=video_id,
    )
    classification = {
        "root": document.classification.root,
        "subgroup": taxonomy_entry.subgroup,
        "confidence": document.classification.confidence,
    }
    backlog_claims = [
        Claim(
            claim=record.claim,
            source=record.source,
            verdict=record.verdict,
            evidence=record.evidence,
            note=record.note,
        )
        for record in records
    ]
    backlog_entry = BacklogEntry(
        id=video_id,
        url=inventory.canonical_url,
        ingested_at=utc_now_iso(),
        author=inventory.author,
        status="pending",
        classification=classification,  # type: ignore[arg-type]
        entities=[entity.name for entity in document.entities],
        claims=backlog_claims,
        fit=document.fit,
        actionable="; ".join(document.actionables) or None,
        artifacts=f"{entry.artifacts[0]}/",
    )

    backlog = Backlog(state)
    backlog_result = emit_backlog_entry(backlog, backlog_entry)

    finished_at = utc_now_iso()
    verification_document = {
        "schema": 1,
        "video_id": video_id,
        "generated_at": finished_at,
        "synthesis_sha256": synthesis_sha256,
        "rules_version": config.VERIFY_RULES_VERSION,
        "classification": classification,
        "claims": [record.to_dict() for record in records],
        "fetches": [fetches[url].to_dict() for url in candidates],
        "backlog": {"result": backlog_result, "id": video_id},
    }
    atomic_write_bytes(
        verification_path,
        json.dumps(
            verification_document, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n",
    )

    manifest.stages["verify"] = StageRecord(
        stage="verify",
        result=StageResult(
            outcome=StageOutcome.COMPLETE,
            reason=f"evaluated {len(records)} claim(s) against "
            f"{len(candidates)} candidate source(s); verdicts: "
            f"{sorted({record.verdict for record in records}) or 'none'}",
        ),
        fingerprint=verify_fp,
        input_sha256=entry.media_sha256,
        output_sha256=hashlib.sha256(verification_path.read_bytes()).hexdigest(),
        started_at=started_at,
        finished_at=finished_at,
    )
    manifest.stages["emit"] = StageRecord(
        stage="emit",
        result=StageResult(
            outcome=StageOutcome.COMPLETE,
            reason=f"backlog entry {backlog_result}",
        ),
        fingerprint=emit_fp,
        input_sha256=entry.media_sha256,
        output_sha256=None,
        started_at=started_at,
        finished_at=finished_at,
    )
    manifest.updated_at = finished_at
    _write_manifest(manifest_path, manifest)

    entry.stage_fingerprints["verify"] = verify_fp
    entry.stage_fingerprints["emit"] = emit_fp
    verification_ref = f"{entry.artifacts[0]}/{VERIFICATION_FILENAME}"
    if verification_ref not in entry.artifacts:
        entry.artifacts.append(verification_ref)
    Processed(state).record(entry)

    return VerifyOutcome(
        outcome=StageOutcome.COMPLETE.value,
        reason="verify completed: " + manifest.stages["verify"].result.reason,
        reused=False,
        video_id=video_id,
        run_dir=str(run_dir),
        backlog_result=backlog_result,
        claims=len(records),
    )
