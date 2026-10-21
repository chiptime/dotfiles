"""Unified ingestion entry point: one command for collections or direct videos.

Handles:
1. TikTok collection URLs (e.g. /@author/collection/<slug> or /@author/playlist/<slug>)
   Delegates to collection_run with auto_verify=True and skip_browser_consent=True.
2. TikTok direct video URLs (e.g. /@author/video/<id>)
   Directly coordinates Whisper, Ollama, automatic text synthesis, and safe verification
   under ONE single upfront run-plan consent in the live Curses TUI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from .collection import CollectionError, parse_collection_url
from .collection_run import make_auto_synthesizer, run_collection
from .guided import narrate_stages, run_guided, whisper_coordinated_stage_functions
from .pipeline import PipelineError, _record_inventory, _utc_now, parse_tiktok_url
from .runtime import default_urlopen, subprocess_runner
from .state import StateRoot
from .synthesis_api import load_text_api_config


class IngestError(RuntimeError):
    """Raised when an unsupported URL format or ingestion failure occurs."""


def run_ingest(
    url: str,
    *,
    state: StateRoot,
    dry_run: bool = False,
    limit: int | None = None,
    consent: Any | None = None,
    out: Callable[[str], None] | None = None,
    env: Mapping[str, str] | None = None,
    api_transport: Callable[..., Any] | None = None,
    podman_runner: Callable[..., Any] | None = None,
    auto_verify: bool = True,
    skip_browser_consent: bool = True,
    rescan: bool = False,
    retry_failed: int = 1,
) -> tuple[int, dict[str, Any]]:
    """Ingest a collection or single video URL with unified single-consent UX."""
    url_str = url.strip()
    env = dict(env if env is not None else os.environ)
    api_transport = api_transport or default_urlopen
    podman_runner = podman_runner or subprocess_runner

    # 1. Try collection URL
    is_collection = False
    try:
        parse_collection_url(url_str)
        is_collection = True
    except CollectionError:
        pass

    if is_collection:
        return run_collection(
            collection_url=url_str,
            state=state,
            consent=consent,
            out=out,
            dry_run=dry_run,
            limit=limit,
            api_transport=api_transport,
            env=env,
            podman_runner=podman_runner,
            auto_verify=auto_verify,
            skip_browser_consent=skip_browser_consent,
            rescan=rescan,
            retry_failed=retry_failed,
        )

    # 2. Try direct video URL
    try:
        video_id, canonical_url, author = parse_tiktok_url(url_str)
    except PipelineError as exc:
        raise IngestError(
            f"unsupported TikTok URL: {exc}. Must be a collection URL "
            f"(@author/collection/<name>) or a video URL (@author/video/<id>)."
        ) from exc

    # Seed inventory so guided-run recognizes the ID
    _record_inventory(state, video_id, canonical_url, author, _utc_now())

    api_config, _missing = load_text_api_config(env)
    out_fn = out or (lambda text: print(text, file=sys.stderr, flush=True))

    auto_synthesizer = (
        make_auto_synthesizer(api_config, transport=api_transport, out=out_fn)
        if api_config is not None
        else None
    )
    stages = narrate_stages(
        whisper_coordinated_stage_functions(runner=podman_runner), out=out_fn
    )

    exit_code, report = run_guided(
        state=state,
        ids=[video_id],
        limit=limit or 1,
        dry_run=dry_run,
        stages=stages,
        consent=consent,
        out=out_fn,
        auto_synthesizer=auto_synthesizer,
        auto_verify=auto_verify,
    )
    return exit_code, report
