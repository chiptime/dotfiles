"""Live collection inventory through a headed, run-scoped browser.

Phase 0 browser boundary (P0-FR-03..06). At RUNTIME this drives a real
headed Chromium through Playwright with a DEDICATED run-scoped profile
directory; it never inspects, copies or automates the operator's
default browser profile, ambient sessions or cookies. Login and
captcha are solved by the HUMAN operator in the visible window — this
module exposes no click/type/fill capability at all, so login
automation is impossible by construction.

Inventory itself reuses the EXISTING offline contracts from
:mod:`tiktok_ingest.collection` (``extract_page_items``,
``merge_observation``, ``CollectionScanStore``, ``sync_inventory_from_scan``):
container-scoped links only, recommendations counted but never items,
declared/observed/end/access/completeness recorded separately, DOM
changes fail explicitly, state stays merge-only.

Lifecycle honesty rules:

- Inventory does not start until the operator EXPLICITLY confirms the
  collection is visible (after handling login/captcha by hand).
- The browser context is closed on success, handled failure or
  interruption (``finally``); the run-scoped profile directory is
  removed afterwards. A close/cleanup failure is REPORTED, never
  hidden, and never claimed as clean.
- An abrupt process kill cannot run cleanup: recovery must come from
  the evidence of real state, never from a promise. Any leftover
  profile directory under the profile base is machine-local data.
- The captured HTML is persisted under the state root for audit and
  DOM-change diagnosis BEFORE parsing, so a failure still leaves the
  evidence the operator saw.

The whole browser boundary is injectable for tests: production uses
:class:`PlaywrightController` (lazy Playwright import); the fixture
suite injects :class:`FakeController`-style doubles and never touches
Playwright, a browser or the network.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol

from .collection import (
    CollectionDOMError,
    CollectionRef,
    CollectionScanStore,
    extract_page_items,
    merge_observation,
    sync_inventory_from_scan,
)
from .contracts import utc_now_iso
from .guided import ConsentProvider, ConsentWindow
from .state import StateRoot, atomic_write_bytes

# Default container scope for the live scan, identical to the offline
# command's documented defaults (README "Collection inventory").
DEFAULT_CONTAINER_ATTR = "data-testid"
DEFAULT_CONTAINER_VALUE = "collection-container"


class BrowserInventoryError(RuntimeError):
    """Browser-boundary failure with an operator-readable reason."""


class PlaywrightMissingError(BrowserInventoryError):
    """The optional Playwright dependency is not installed."""


def playwright_available() -> tuple[bool, str]:
    """Read-only availability check; NEVER installs anything."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        return (
            False,
            "the Playwright Python package is not available in this "
            f"interpreter ({exc}); install it separately (for example "
            "'pip install playwright && playwright install chromium' in "
            "the environment you authorize) — this pipeline never "
            "installs dependencies itself",
        )
    return True, "playwright available"


class BrowserController(Protocol):
    """The injectable browser seam: open -> read HTML -> close. Nothing else.

    The deliberately tiny surface is the safety property: there is no
    method here that could automate login, captcha or scrolling.
    """

    def open(self, url: str, profile_dir: Path) -> None: ...

    def current_html(self) -> str: ...

    def close(self) -> None: ...


class PlaywrightController:
    """Real runtime browser: headed Chromium, dedicated profile only.

    ``playwright`` is imported lazily so the fixture suite (and any
    machine without the optional dependency) never needs it. The
    context is headed (``headless=False``) so the operator can perform
    login/captcha manually.
    """

    def __init__(self) -> None:
        self._context: Any = None
        self._page: Any = None
        self._playwright: Any = None

    def open(self, url: str, profile_dir: Path) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise PlaywrightMissingError(
                "the Playwright Python package is not available in this "
                f"interpreter ({exc}); the browser boundary needs it — "
                "install it in the environment you authorize; this "
                "pipeline never installs dependencies itself"
            ) from exc
        if self._context is not None:
            raise BrowserInventoryError("browser already open")
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
        )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._page.goto(url)

    def current_html(self) -> str:
        if self._page is None:
            raise BrowserInventoryError("browser is not open")
        return str(self._page.content())

    def close(self) -> None:
        errors: list[str] = []
        if self._context is not None:
            try:
                self._context.close()
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                errors.append(f"context close failed: {exc}")
            finally:
                self._context = None
                self._page = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                errors.append(f"playwright stop failed: {exc}")
            finally:
                self._playwright = None
        if errors:
            raise BrowserInventoryError(
                "browser cleanup was not fully clean (" + "; ".join(errors)
                + "); the browser window/profile may need manual attention"
            )


@dataclasses.dataclass
class BrowserInventoryReport:
    """What one live inventory capture honestly produced."""

    collection_url: str
    observed_items: int
    out_of_container_count: int
    access_markers: tuple[str, ...]
    scan_status: str
    scan_status_reason: str
    audit_html_path: str
    profile_dir: str
    profile_removed: bool
    cleanup_notes: list[str]
    interrupted: bool = False
    confirmed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _remove_profile(profile_dir: Path, notes: list[str]) -> bool:
    import shutil

    try:
        shutil.rmtree(profile_dir)
        return True
    except OSError as exc:
        notes.append(
            f"profile directory cleanup failed for {profile_dir}: {exc}; "
            "remove it manually — it is machine-local data and must not "
            "be reused as a browser profile"
        )
        return False


def run_browser_inventory(
    *,
    state: StateRoot,
    ref: CollectionRef,
    consent: ConsentProvider,
    out: Callable[[str], None] | None = None,
    controller: BrowserController | None = None,
    profile_base: Path | str | None = None,
    declared_count: int | None = None,
    end_evidence: str | None = None,
    container_attr: str = DEFAULT_CONTAINER_ATTR,
    container_value: str = DEFAULT_CONTAINER_VALUE,
) -> BrowserInventoryReport:
    """One consent-gated live inventory pass; reuses collection contracts.

    Sequence: availability preflight → browser window consent (nothing
    has run) → run-scoped profile creation → headed open → operator
    login/captcha by hand → EXPLICIT visibility confirmation → capture
    HTML → close browser → persist audit HTML → parse with the existing
    collection rules → merge-only scan state + inventory sync → remove
    the profile. Every step after consent runs inside ``finally``-guarded
    cleanup.
    """
    out = out or (lambda _text: None)
    notes: list[str] = []
    if controller is None:
        available, detail = playwright_available()
        if not available:
            raise PlaywrightMissingError(detail)

    base = Path(profile_base) if profile_base is not None else None
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)

    window = ConsentWindow(
        kind="browser",
        title="open a headed browser for collection inventory",
        destinations=(ref.collection_url,),
        operations=(
            "launch headed Chromium through Playwright with a DEDICATED "
            "run-scoped profile directory (never your default browser "
            "profile, ambient sessions or cookies)",
            f"navigate one tab to {ref.collection_url}",
            "wait while YOU handle login/captcha manually (this command "
            "cannot and will not automate them)",
            "after your explicit confirmation, capture the page HTML once "
            f"and enumerate items inside the {container_attr}="
            f"{container_value!r} container (recommendation links are "
            "counted, never items)",
        ),
        limits=(
            "one tab; the browser is used ONLY for inventory and closes "
            "before any media processing",
            "the run-scoped profile is removed on success, failure or "
            "interruption; an abrupt kill cannot guarantee cleanup",
        ),
        effects=(
            "persists the captured HTML under the state root for audit",
            "persists/merges collection scan state and inventory entries "
            "(merge-only; enumeration never authorizes downloads)",
        ),
    )
    if not consent.ask(window):
        raise BrowserInventoryError(
            "browser window denied: no browser was opened, nothing was "
            "captured and nothing was written"
        )

    profile_dir = Path(
        tempfile.mkdtemp(
            prefix=f"collection-run-{ref.collection_key}-", dir=base
        )
        if base is not None
        else tempfile.mkdtemp(prefix=f"collection-run-{ref.collection_key}-")
    )
    live = controller if controller is not None else PlaywrightController()
    interrupted = False
    confirmed = False
    html = ""
    try:
        out(
            "browser opening: complete login/captcha in the visible "
            "window, scroll the collection as you wish, then answer the "
            "confirmation prompt here"
        )
        live.open(ref.collection_url, profile_dir)
        confirm = ConsentWindow(
            kind="browser-confirm",
            title=(
                "confirm the collection is visible (inventory starts only "
                "after your explicit confirmation)"
            ),
            destinations=(ref.collection_url,),
            operations=(
                "capture the CURRENT page HTML once and enumerate the "
                "collection container",
            ),
            limits=(
                "a single snapshot of what is loaded right now; without "
                "end-of-list evidence the scan status stays honestly "
                "incomplete (in_progress) — it never claims completeness",
            ),
            effects=(
                "the captured HTML is persisted for audit and the "
                "existing merge-only scan state/inventory are updated",
            ),
        )
        confirmed = consent.ask(confirm)
        if not confirmed:
            raise BrowserInventoryError(
                "visibility confirmation denied (or input ended): no "
                "inventory was captured; the browser was closed and the "
                "run-scoped profile removed"
            )
        html = live.current_html()
    except KeyboardInterrupt:
        interrupted = True
        notes.append(
            "interrupted during the browser window: the browser was closed "
            "and the run-scoped profile removed; nothing else was written"
        )
    finally:
        try:
            live.close()
        except BrowserInventoryError as exc:
            notes.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - cleanup is never hidden
            notes.append(f"browser close error: {exc}")
        profile_removed = _remove_profile(profile_dir, notes)

    if interrupted or not confirmed:
        raise BrowserInventoryError(
            "browser inventory did not complete: "
            + ("interrupted" if interrupted else "not confirmed")
            + f"; cleanup notes: {'; '.join(notes) or 'none'}"
        )

    # Audit HTML persists BEFORE parsing: a DOM-change failure still
    # leaves the evidence the operator actually saw.
    captures_dir = state.collections_dir / f"{ref.collection_key}-captures"
    captures_dir.mkdir(parents=True, exist_ok=True)
    audit_path = captures_dir / f"{utc_now_iso().replace(':', '').replace('+', '_')}.html"
    atomic_write_bytes(audit_path, html.encode("utf-8"))

    try:
        observation = extract_page_items(
            html, container_attr, container_value
        )
    except CollectionDOMError as exc:
        raise BrowserInventoryError(
            f"the collection page DOM changed: {exc}; the captured HTML "
            f"was preserved at {audit_path} for selector diagnosis — "
            "failing explicitly instead of returning a fake empty list"
        ) from exc

    existing = CollectionScanStore(state).load(ref.collection_key)
    merged = merge_observation(
        existing,
        ref,
        observation,
        declared_count=declared_count,
        end_evidence=end_evidence,
        stop_reason=None,
        captured_at=utc_now_iso(),
        container_attr=container_attr,
        container_value=container_value,
    )
    CollectionScanStore(state).save(merged)
    sync_inventory_from_scan(state, merged)

    return BrowserInventoryReport(
        collection_url=ref.collection_url,
        observed_items=len(merged.items),
        out_of_container_count=observation.out_of_container_count,
        access_markers=observation.access_markers,
        scan_status=merged.status,
        scan_status_reason=merged.status_reason,
        audit_html_path=str(audit_path),
        profile_dir=str(profile_dir),
        profile_removed=profile_removed,
        cleanup_notes=notes,
    )


__all__ = [
    "BrowserController",
    "BrowserInventoryError",
    "BrowserInventoryReport",
    "DEFAULT_CONTAINER_ATTR",
    "DEFAULT_CONTAINER_VALUE",
    "PlaywrightController",
    "PlaywrightMissingError",
    "playwright_available",
    "run_browser_inventory",
]
