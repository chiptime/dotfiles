"""Command-line interface for the TikTok ingest MVP (stdlib argparse).

Subcommands:

- ``install-extractor``  explicitly create the local extractor venv and
  install the pinned yt-dlp into it. NEVER runs implicitly: processing
  commands never install anything.
- ``fetch-url <url>``    one canonical TikTok video URL -> gated
  extraction -> validated shared media in the working cache.
- ``prepare <id>``       validated media -> baseline frames + 16 kHz mono
  WAV with full provenance inside the pinned FFmpeg container.
- ``install-ollama``     explicitly download the pinned isolated Ollama
  archive, verify sha256 AND size exactly, extract under the share root
  and delete the archive. NEVER runs implicitly; refuses any mismatch
  without retry.
- ``import-model``       explicitly copy the vision model manifest +
  blobs from the GLOBAL Ollama store (read-only) into the isolated
  models directory, verifying every digest. NEVER runs implicitly.
- ``vision <id>``        gated local vision over the prepared frames
  (isolated Ollama, whisper MUST be stopped; fails closed).
- ``audio <id>``         full-audio transcription through the reused
  whisper WS service (large-v3, fixed es; health precheck; fails closed).
- ``synthesize <id>``    validated-document ingestion of an explicitly
  operator-supplied synthesis JSON (``--from-file``); strict schema,
  credential scrub, no model calls, no credentials.
- ``verify <id>``        bounded, safe retrieval of the synthesis
  document's candidate first-party URLs, mechanical claim verdicts and
  atomic local backlog emission (never external task writes).
- ``status [id]``        persisted outcomes for inspection, including the
  synthesis/verify stages and backlog state.
- ``inventory-collect``  merge ONE operator-captured collection page
  snapshot (HTML) into the persisted scan state, sync the inventory and
  print the honest scan status. NEVER opens a browser and NEVER performs
  network I/O: snapshots come from the operator's separately authorized
  session.
- ``inventory-status``   print the persisted scan state for a collection
  (by URL or 16-hex collection key).
- ``inventory-plan``     print the document-only processing plan for a
  collection (classification against blocklist/processed state; it
  executes nothing).
- ``backlog-list``       list backlog entries with per-line hashes and an
  optional status filter (read-only).
- ``backlog-show``       one entry with its claims, provenance and the
  mechanical verification reasons from the recorded verification.json
  (read-only).
- ``backlog-decide``     record ONE explicit operator decision
  (pending/accepted/rejected) into a versioned JSONL decisions
  document. Decisions are never inferred.
- ``backlog-plan``       dry run of a decisions document: IDs, status
  changes, expected per-entry and whole-file hashes, blocklist
  additions and conflicts. Writes nothing.
- ``backlog-apply``      apply ONLY explicit, still-current decisions
                         with verified exclusive backups, fail-safe write
                         ordering (blocklist before backlog), a receipt
                         and post-write verification.
- ``guided-run``        consent-gated coordination of the existing
                         stages for one tanda (plan-driven or explicit
                         IDs): fetch -> prepare -> vision -> audio ->
                         synthesis ingestion -> verify, with a SEPARATE
                         authorization window before each effectful
                         phase, a hard stop at the synthesis boundary
                         when no document was supplied, and no writes of
                         its own. Never starts anything without its
                         window; ``--dry-run`` only prints the plan.
- ``collection-run``    Phase 0 one-command journey for ONE collection
                         URL: headed run-scoped browser inventory
                         (operator handles login/captcha and confirms
                         visibility), batches of at most five through
                         the guided coordinator, automatic synthesis via
                         ONE OpenAI-compatible adapter (env-configured),
                         bounded whisper stop/restore coordination, and
                         local PENDING backlog entries only. Invalid or
                         missing URLs are rejected before any effect;
                         ``--dry-run`` prints preflight + plan only.
Every state-touching command accepts ``--state-root`` (default
``~/.local/state/tiktok-ingest/``) so tests and dry inspection can point
at a scratch location. No subcommand stops services, browsers, downloads
beyond the explicitly requested item, or runs without the operator's
explicit invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config
from .bootstrap import BootstrapError, install_extractor, verify_installation
from .collection import (
    CollectionError,
    CollectionScanStore,
    build_collection_plan,
    extract_page_items,
    merge_observation,
    parse_collection_url,
    resolve_collection,
    sync_inventory_from_scan,
)
from .contracts import BACKLOG_STATUSES, utc_now_iso
from .extractor import ExtractorError
from .guided import GuidedError, parse_synthesis_args, run_guided
from .ollama_runtime import (
    OllamaRuntimeError,
    import_model,
    install_ollama,
)
from .pipeline import PipelineError, fetch_url, parse_tiktok_url, video_status
from .prepare import PrepareError, prepare_video
from .review import (
    ReviewError,
    apply_decisions,
    build_plan,
    list_entries,
    parse_decisions_file,
    record_decision,
    show_entry,
)
from .state import StateError, StateRoot
from .synthesis import SynthesisError, run_synthesis_stage
from .verify import VerifyError, run_verify_stage
from .vision import VisionError, run_vision_stage
from .whisper_client import run_audio_stage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tiktok_ingest",
        description=(
            "TikTok ingest MVP: authorized direct-URL input to validated "
            "CPU artifacts. No inference, no bypasses, no automatic retries."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_state_root(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--state-root",
            type=Path,
            default=None,
            help="state root (default: ~/.local/state/tiktok-ingest)",
        )

    install = subparsers.add_parser(
        "install-extractor",
        help=(
            "explicitly create the local extractor venv and install the "
            f"pinned {config.EXTRACTOR_REQUIREMENT} (never runs implicitly)"
        ),
        description=(
            "Creates a virtual environment under the local share root "
            "(outside Git) and pip-installs the pinned extractor with "
            "--no-deps. No sudo, no global installs, no committed binaries."
        ),
    )
    install.add_argument(
        "--share-root",
        type=Path,
        default=None,
        help="local share root (default: %(default)s)",
    )
    install.add_argument(
        "--venv-dir",
        type=Path,
        default=None,
        help="target venv directory (default: <share-root>/extractor-venv)",
    )

    fetch = subparsers.add_parser(
        "fetch-url",
        help="process one canonical TikTok video URL up to validated media",
        description=(
            "Direct-URL input mode: blocklist and cache checks, gated "
            "pinned extraction, media validation, content-addressed cache. "
            "Records input origin 'direct-url'; never claims collection "
            "membership. Never retries extraction automatically."
        ),
    )
    fetch.add_argument("url", help="canonical https://www.tiktok.com/@author/video/<id> URL")
    add_state_root(fetch)
    fetch.add_argument(
        "--share-root",
        type=Path,
        default=None,
        help="local share root holding the extractor venv (default: %(default)s)",
    )
    fetch.add_argument(
        "--venv-dir",
        type=Path,
        default=None,
        help="extractor venv directory (default: <share-root>/extractor-venv)",
    )
    fetch.add_argument(
        "--force",
        action="store_true",
        help="re-extract even when the fetch fingerprint matches (never "
        "overrides the blocklist)",
    )

    prepare = subparsers.add_parser(
        "prepare",
        help="prepare frames and normalized audio for a fetched video ID",
        description=(
            "Runs CPU preparation (hybrid baseline frames + 16 kHz mono "
            "s16 WAV) inside the pinned, network-isolated FFmpeg container. "
            "Reuses a matching recorded run with zero re-preparation."
        ),
    )
    prepare.add_argument("video_id", help="stable TikTok video ID previously fetched")
    add_state_root(prepare)
    prepare.add_argument(
        "--force",
        action="store_true",
        help="recompute even when the prepare fingerprint matches (never "
        "overrides the blocklist)",
    )

    status = subparsers.add_parser(
        "status",
        help="show persisted processing status",
        description="Read-only view of processed entries, outcomes and blocklist state.",
    )
    status.add_argument("video_id", nargs="?", default=None, help="optional video ID")
    add_state_root(status)

    install_ollama_parser = subparsers.add_parser(
        "install-ollama",
        help=(
            "explicitly download, verify (sha256 + size) and extract the "
            f"pinned isolated Ollama {config.GATES.isolated_ollama_version} "
            "runtime (never runs implicitly)"
        ),
        description=(
            "Downloads the official archive once, verifies the recorded "
            "sha256 AND byte size EXACTLY, extracts it under the local "
            "share root and deletes the archive. Any mismatch is refused "
            "without retry. No sudo, no global installs."
        ),
    )
    install_ollama_parser.add_argument(
        "--share-root",
        type=Path,
        default=None,
        help="local share root (default: %(default)s)",
    )

    import_model_parser = subparsers.add_parser(
        "import-model",
        help=(
            "explicitly copy the vision model from the GLOBAL Ollama store "
            "into the isolated models directory (read-only source; never "
            "runs implicitly)"
        ),
        description=(
            "Copies the model manifest and every blob from the global "
            "store, verifying each digest against the manifest and the "
            "copy. Refuses on ANY mismatch with no partial import; never "
            "writes to the global store; never pulls from the network."
        ),
    )
    import_model_parser.add_argument(
        "--model",
        default=config.VISION_MODEL,
        help="model name:tag to import (default: %(default)s)",
    )
    import_model_parser.add_argument(
        "--share-root",
        type=Path,
        default=None,
        help="local share root (default: %(default)s)",
    )
    import_model_parser.add_argument(
        "--global-store",
        type=Path,
        default=None,
        help="global Ollama store to read from (default: ~/.ollama)",
    )

    vision = subparsers.add_parser(
        "vision",
        help="run the gated local vision stage for a prepared video ID",
        description=(
            "Local VLM pass over the prepared frames through the ISOLATED "
            "Ollama runtime. Fails closed unless the whisper container is "
            "STOPPED (verified read-only; never stopped by this code) and "
            "the isolated server answers on 127.0.0.1:11435. Enforces all "
            "benchmark resource gates with no retries after a gate fires."
        ),
    )
    vision.add_argument("video_id", help="stable TikTok video ID previously prepared")
    add_state_root(vision)
    vision.add_argument(
        "--swap-limit-mib",
        type=int,
        default=None,
        metavar="MIB",
        help=(
            "OPERATOR-AUTHORIZED swap-gate budget change for THIS run only "
            f"(default {config.GATES.swap_delta_limit_mib} MiB); values at or "
            "below the default are ignored; the deviation is recorded in the "
            "durable gate report"
        ),
    )
    vision.add_argument(
        "--force",
        action="store_true",
        help="re-run even when the vision fingerprint matches (never "
        "overrides the blocklist)",
    )

    audio = subparsers.add_parser(
        "audio",
        help="run the whisper WS audio stage for a prepared video ID",
        description=(
            "Full-audio single-pass transcription through the reused "
            "whisper WebSocket service (large-v3, fixed es, one connection "
            "per job). Fails closed when the health endpoint is down or "
            "the prepared audio is missing. No retries."
        ),
    )
    audio.add_argument("video_id", help="stable TikTok video ID previously prepared")
    add_state_root(audio)
    audio.add_argument(
        "--force",
        action="store_true",
        help="re-run even when the audio fingerprint matches (never "
        "overrides the blocklist)",
    )

    synthesize = subparsers.add_parser(
        "synthesize",
        help="ingest an explicitly supplied synthesis document for a video ID",
        description=(
            "Validated-document synthesis ingestion: reads the sanitized "
            "video.md + audio.md from the recorded run, validates the "
            "operator-supplied --from-file synthesis JSON against a strict "
            "schema, scrubs it for credential-looking strings and persists "
            "synthesis.json with a resume fingerprint. The pipeline "
            "performs NO model calls and holds NO credentials: without "
            "--from-file the stage is blocked, never a silent empty. A "
            "missing modality is an explicit failure."
        ),
    )
    synthesize.add_argument("video_id", help="stable TikTok video ID with completed vision+audio stages")
    add_state_root(synthesize)
    synthesize.add_argument(
        "--from-file",
        dest="from_file",
        type=Path,
        default=None,
        help="operator-supplied synthesis JSON document to validate and ingest",
    )
    synthesize.add_argument(
        "--force",
        action="store_true",
        help="re-run even when the synthesis fingerprint matches",
    )

    verify = subparsers.add_parser(
        "verify",
        help="verify claims against candidate first-party sources and emit the local backlog entry",
        description=(
            "Bounded safe verification: fetches ONLY the candidate URLs "
            "recorded in the synthesis document (public http(s), validated "
            "literal IPs and redirect hops, 2 MiB / 15 s / 3-redirect "
            "caps), assigns mechanical claim verdicts (nothing is "
            "auto-searched; unnamed means the entity could not be "
            "identified, while an identified entity without candidate "
            "sources is unverifiable/no_candidate_urls) and "
            "appends the local backlog entry atomically with taxonomy "
            "normalization and duplicate-emission prevention. Refuses to "
            "run without a complete synthesis stage. No external writes."
        ),
    )
    verify.add_argument("video_id", help="stable TikTok video ID with a completed synthesis stage")
    add_state_root(verify)
    verify.add_argument(
        "--force",
        action="store_true",
        help="re-run even when the verify fingerprint matches (backlog "
        "appends stay duplicate-guarded)",
    )

    collect = subparsers.add_parser(
        "inventory-collect",
        help="merge one operator-captured collection page snapshot into scan state",
        description=(
            "Offline enumeration merge for ONE explicitly selected "
            "collection: reads a page snapshot (HTML) captured by an "
            "AUTHORIZED operator browser session, extracts container-scoped "
            "item links, merges them merge-only into the persisted scan "
            "state, syncs the inventory and prints the honest scan status. "
            "NEVER opens a browser, NEVER performs network I/O. A missing "
            "container marker fails explicitly instead of a fake empty "
            "list; access-block markers are access evidence, never proof "
            "of deletion or privacy."
        ),
    )
    collect.add_argument(
        "collection_url",
        help="canonical https://www.tiktok.com/@author/collection/<slug> URL",
    )
    collect.add_argument(
        "--html",
        required=True,
        help="path to the captured page snapshot, or '-' to read stdin",
    )
    collect.add_argument(
        "--container-attr",
        required=True,
        help="attribute name marking the collection container element",
    )
    collect.add_argument(
        "--container-value",
        required=True,
        help="exact attribute value marking the collection container element",
    )
    collect.add_argument(
        "--declared-count",
        type=int,
        default=None,
        metavar="N",
        help="declared item count visible on the page, if any",
    )
    collect.add_argument(
        "--end-evidence",
        default=None,
        help="operator-recorded end-of-list evidence text (required for "
        "'complete'; a count match alone never proves completeness)",
    )
    collect.add_argument(
        "--stop-reason",
        default=None,
        help="why the scroll stopped (a stopped scroll does NOT prove "
        "completeness; recorded as-is)",
    )
    collect.add_argument(
        "--blocked-marker",
        action="append",
        default=None,
        metavar="TEXT",
        help="extra access-block marker to scan the snapshot for (repeatable)",
    )
    add_state_root(collect)

    inventory_status = subparsers.add_parser(
        "inventory-status",
        help="print the persisted scan state for a collection",
        description=(
            "Read-only view of one collection's merge-only scan state: "
            "status, status reason, accumulated items, capture history and "
            "declared/end-evidence/stop-reason streams. Takes a canonical "
            "collection URL or a 16-hex collection key."
        ),
    )
    inventory_status.add_argument(
        "collection", help="collection URL or 16-hex collection key"
    )
    add_state_root(inventory_status)

    inventory_plan = subparsers.add_parser(
        "inventory-plan",
        help="print the document-only processing plan for a collection",
        description=(
            "Classifies each observed item against the EXISTING blocklist "
            "and processed stores (new_processable, processed_complete, "
            "partial_resumable, rejected, unsupported_photo) and prints "
            "the plan as JSON. This is a DOCUMENT, never an execution: it "
            "starts no downloads and no inference. Requires persisted scan "
            "state."
        ),
    )
    inventory_plan.add_argument(
        "collection", help="collection URL or 16-hex collection key"
    )
    add_state_root(inventory_plan)

    backlog_list = subparsers.add_parser(
        "backlog-list",
        help="list backlog entries with per-line hashes (read-only)",
        description=(
            "Read-only listing of the local backlog queue: id, status, "
            "url, author, ingested_at and the sha256 of each raw line "
            "(the concurrency token a decision binds). Optionally filter "
            "by operator status. Writes nothing."
        ),
    )
    backlog_list.add_argument(
        "--status",
        choices=list(BACKLOG_STATUSES),
        default=None,
        help="only list entries with this operator status",
    )
    add_state_root(backlog_list)

    backlog_show = subparsers.add_parser(
        "backlog-show",
        help="show one entry with claims and verification reasons (read-only)",
        description=(
            "Read-only detail for ONE backlog entry: the full entry, its "
            "claims, provenance and the mechanical verification reasons "
            "from the recorded verification.json of the referenced run "
            "directory. Writes nothing."
        ),
    )
    backlog_show.add_argument("video_id", help="stable TikTok video ID present in the backlog")
    add_state_root(backlog_show)

    backlog_decide = subparsers.add_parser(
        "backlog-decide",
        help="record ONE explicit operator decision into the decisions document",
        description=(
            "Records one EXPLICIT operator decision (pending, accepted "
            "or rejected) for one backlog entry into the versioned "
            "decisions JSONL document (--decisions-file is required). "
            "The decision binds the sha256 of the current raw backlog "
            "line so a later apply can detect concurrent changes. A "
            "rejected decision REQUIRES --reason (it becomes the "
            "permanent blocklist reason). Decisions are never inferred; "
            "nothing in the pipeline state is modified here."
        ),
    )
    backlog_decide.add_argument("video_id", help="stable TikTok video ID present in the backlog")
    backlog_decide.add_argument(
        "--decision",
        required=True,
        choices=list(BACKLOG_STATUSES),
        help="explicit operator decision",
    )
    backlog_decide.add_argument(
        "--reason",
        default=None,
        help="operator reason (required for --decision rejected)",
    )
    backlog_decide.add_argument(
        "--decisions-file",
        dest="decisions_file",
        type=Path,
        required=True,
        help="versioned decisions JSONL document "
        "(schema tiktok-ingest/backlog-decision@1)",
    )
    add_state_root(backlog_decide)

    backlog_plan = subparsers.add_parser(
        "backlog-plan",
        help="dry run of a decisions document: changes and expected hashes",
        description=(
            "Dry run for a decisions document: per-ID current status, "
            "the planned change, expected post-application entry "
            "hashes, expected whole-file backlog hash, blocklist "
            "additions and any conflicts (stale decisions, unknown IDs, "
            "blocklist-incoherent accepts). WRITES NOTHING."
        ),
    )
    backlog_plan.add_argument(
        "--decisions-file",
        dest="decisions_file",
        type=Path,
        required=True,
        help="versioned decisions JSONL document to plan",
    )
    add_state_root(backlog_plan)

    backlog_apply = subparsers.add_parser(
        "backlog-apply",
        help="apply explicit, still-current decisions with verified backups",
        description=(
            "Applies ONLY explicit, still-current decisions: verifies "
            "every entry hash against the recorded decision, creates "
            "EXCLUSIVE per-file backups and verifies their hashes "
            "BEFORE writing, publishes blocklist.json BEFORE "
            "backlog.jsonl (fail-safe order), writes an audit receipt "
            "and re-reads both files afterwards. Re-applying an already "
            "confirmed decision is an idempotent no-op (zero data "
            "writes). NOT a multi-file transaction: recovery relies on "
            "the receipt, the exclusive backups and idempotent re-runs."
        ),
    )
    backlog_apply.add_argument(
        "--decisions-file",
        dest="decisions_file",
        type=Path,
        required=True,
        help="versioned decisions JSONL document to apply",
    )
    add_state_root(backlog_apply)

    guided = subparsers.add_parser(
        "guided-run",
        help="consent-gated guided coordination of one tanda through the "
        "existing stages",
        description=(
            "A thin coordinator, NOT a new framework: sequences the "
            "existing fetch -> prepare -> vision -> audio -> synthesis "
            "(--from-file ingestion) -> verify stages for one operator-"
            "confirmed tanda (batch cap 5). Every effectful phase asks "
            "its own explicit authorization window first (tanda, network "
            "download, CPU container, GPU + isolated Ollama cycle, "
            "verification retrieval of the exact candidate URLs); denial, "
            "EOF or a non-interactive environment stops without assuming "
            "consent. Progress is derived from the product's own "
            "manifests/fingerprints; this command keeps no second state "
            "and writes nothing of its own. The synthesis boundary is a "
            "hard stop: without --synthesis ID=PATH it reports the "
            "video.md/audio.md paths and how to resume; it NEVER "
            "generates content. Whisper stays operator-managed (vision "
            "needs it stopped, audio needs it healthy); the isolated "
            "Ollama server is started only inside the consented GPU "
            "window and stopped at window end or failure. Backlog "
            "entries are appended as pending by verify only; review "
            "stays with backlog-list/show/decide/plan/apply."
        ),
    )
    guided.add_argument(
        "--collection",
        default=None,
        metavar="URL_OR_KEY",
        help="collection URL or 16-hex key with persisted scan state; "
        "plan-driven selection takes the new_processable items (capped)",
    )
    guided.add_argument(
        "--ids",
        default=None,
        metavar="ID[,ID...]",
        help="explicit video IDs (new or partial-resumable); unknown or "
        "ambiguous IDs are rejected",
    )
    guided.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=f"batch cap for the selection (1..{config.BUDGETS.batch_pilot_clips}; "
        f"default {config.BUDGETS.batch_pilot_clips})",
    )
    guided.add_argument(
        "--synthesis",
        action="append",
        default=None,
        metavar="ID=PATH",
        help="operator-authored synthesis document for one ID of this "
        "tanda (repeatable); validated and persisted by the existing "
        "synthesize stage — no model calls, nothing generated",
    )
    guided.add_argument(
        "--dry-run",
        action="store_true",
        help="print the selection and per-stage plan only: zero prompts, "
        "zero writes, zero network/GPU work",
    )
    add_state_root(guided)

    collection_run = subparsers.add_parser(
        "collection-run",
        help="Phase 0 one-command journey: browser inventory -> batches "
        "through the guided coordinator -> automatic synthesis -> local "
        "pending entries",
        description=(
            "One command takes a single TikTok collection URL through a "
            "headed, run-scoped Playwright browser inventory (operator "
            "handles login/captcha and explicitly confirms visibility), "
            "then processes eligible items in batches of at most five "
            "through the EXISTING consent-gated stages (fetch -> prepare "
            "-> vision -> audio -> synthesis -> verify). Synthesis is "
            "automatic through ONE OpenAI-compatible text API configured "
            "by TIKTOK_INGEST_TEXT_API_BASE_URL/_API_KEY/_MODEL and "
            "always validated by the existing strict document contract. "
            "The known shared whisper service may be stopped for vision "
            "and restored for audio, each only after identity "
            "verification and explicit consent. Output is local pending "
            "backlog entries only; review stays with the backlog CLI. "
            "Invalid or missing URLs are rejected before any effect."
        ),
    )
    collection_run.add_argument(
        "collection_url",
        metavar="COLLECTION_URL",
        help="canonical https://www.tiktok.com/@<author>/collection/<slug> "
        "(or /playlist/<slug>) URL",
    )
    collection_run.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=f"per-batch cap (1..{config.BUDGETS.batch_pilot_clips}; default "
        f"{config.BUDGETS.batch_pilot_clips})",
    )
    collection_run.add_argument(
        "--declared-count",
        type=int,
        default=None,
        metavar="N",
        help="declared item count observed in the browser (optional honest "
        "evidence; recorded separately from the observed count)",
    )
    collection_run.add_argument(
        "--end-evidence",
        default=None,
        metavar="TEXT",
        help="end-of-list evidence text observed in the browser (optional; "
        "completeness is only ever claimed from recorded evidence)",
    )
    collection_run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the preflight and the document-only plan: zero browser, "
        "zero network, zero containers, zero GPU, zero API calls, zero "
        "writes",
    )
    add_state_root(collection_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "install-extractor":
            share_root = args.share_root or config.SHARE_ROOT
            venv_dir = args.venv_dir or config.EXTRACTOR_VENV_DIR
            install_extractor(venv_dir, share_root)
            ok, reason = verify_installation(venv_dir)
            print(reason)
            return 0 if ok else 1

        if args.command == "install-ollama":
            share_root = args.share_root or config.SHARE_ROOT
            binary = install_ollama(Path(share_root) / "ollama")
            print(f"isolated Ollama installed at {binary}")
            return 0

        if args.command == "import-model":
            share_root = args.share_root or config.SHARE_ROOT
            report = import_model(
                args.model,
                global_store=args.global_store or config.GLOBAL_OLLAMA_STORE,
                isolated_models=Path(share_root) / "ollama" / "models",
            )
            print(json.dumps(report.__dict__, indent=2, sort_keys=True))
            return 0

        state = StateRoot(args.state_root) if args.state_root is not None else StateRoot()

        if args.command == "fetch-url":
            parse_tiktok_url(args.url)  # fail fast on unsupported input forms
            outcome = fetch_url(
                args.url,
                state=state,
                share_root=args.share_root,
                venv_dir=args.venv_dir,
                force=args.force,
            )
            print(json.dumps(outcome.__dict__, indent=2, sort_keys=True, default=str))
            return 0 if outcome.outcome == "complete" else 1

        if args.command == "prepare":
            outcome = prepare_video(args.video_id, state=state, force=args.force)
            print(json.dumps(outcome.__dict__, indent=2, sort_keys=True, default=str))
            return 0 if outcome.outcome == "complete" else 1

        state = StateRoot(args.state_root) if args.state_root is not None else StateRoot()

        if args.command == "vision":
            outcome = run_vision_stage(
                args.video_id,
                state=state,
                force=args.force,
                swap_limit_mib=args.swap_limit_mib,
            )
            print(json.dumps(outcome.__dict__, indent=2, sort_keys=True, default=str))
            return 0 if outcome.outcome == "complete" else 1

        if args.command == "audio":
            outcome = run_audio_stage(args.video_id, state=state, force=args.force)
            print(json.dumps(outcome.__dict__, indent=2, sort_keys=True, default=str))
            return 0 if outcome.outcome == "complete" else 1

        if args.command == "synthesize":
            outcome = run_synthesis_stage(
                args.video_id,
                state=state,
                from_file=args.from_file,
                force=args.force,
            )
            print(json.dumps(outcome.__dict__, indent=2, sort_keys=True, default=str))
            return 0 if outcome.outcome == "complete" else 1

        if args.command == "verify":
            outcome = run_verify_stage(args.video_id, state=state, force=args.force)
            print(json.dumps(outcome.__dict__, indent=2, sort_keys=True, default=str))
            return 0 if outcome.outcome == "complete" else 1

        if args.command == "status":
            print(json.dumps(video_status(args.video_id, state=state), indent=2, ensure_ascii=False))
            return 0

        if args.command == "inventory-collect":
            ref = parse_collection_url(args.collection_url)  # fail fast on input
            try:
                if args.html == "-":
                    html_text = sys.stdin.read()
                else:
                    html_text = Path(args.html).read_text(encoding="utf-8")
            except OSError as exc:
                raise CollectionError(
                    f"could not read page snapshot {args.html!r}: {exc}"
                ) from exc
            observation = extract_page_items(
                html_text,
                args.container_attr,
                args.container_value,
                extra_blocked_markers=tuple(args.blocked_marker or ()),
            )
            store = CollectionScanStore(state)
            existing = store.load(ref.collection_key)
            merged = merge_observation(
                existing,
                ref,
                observation,
                declared_count=args.declared_count,
                end_evidence=args.end_evidence,
                stop_reason=args.stop_reason,
                captured_at=utc_now_iso(),
                container_attr=args.container_attr,
                container_value=args.container_value,
            )
            store.save(merged)
            added, updated = sync_inventory_from_scan(state, merged)
            print(
                json.dumps(
                    {
                        "collection_key": merged.collection.collection_key,
                        "collection_url": merged.collection.collection_url,
                        "status": merged.status,
                        "status_reason": merged.status_reason,
                        "new_ids": list(merged.captures[-1].new_ids),
                        "observed_count": len(merged.items),
                        "declared_count": merged.declared_count,
                        "end_of_list_evidence": merged.end_of_list_evidence,
                        "stop_reason": merged.stop_reason,
                        "access_markers": list(
                            merged.captures[-1].access_markers
                        ),
                        "out_of_container_count": merged.captures[
                            -1
                        ].out_of_container_count,
                        "inventory_sync": {"added": added, "updated": updated},
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            # State (and inventory) stay persisted even when blocked.
            return 1 if merged.status == "blocked" else 0

        if args.command == "inventory-status":
            ref, scan = resolve_collection(state, args.collection)
            if scan is None:
                print(
                    f"no persisted scan state for {ref.collection_url}",
                    file=sys.stderr,
                )
                return 2
            print(
                json.dumps(
                    scan.to_dict(), indent=2, ensure_ascii=False, sort_keys=True
                )
            )
            return 0

        if args.command == "inventory-plan":
            ref, scan = resolve_collection(state, args.collection)
            if scan is None:
                print(
                    f"no persisted scan state for {ref.collection_url}",
                    file=sys.stderr,
                )
                return 2
            plan = build_collection_plan(state, scan)
            print(
                json.dumps(
                    plan.to_dict(), indent=2, ensure_ascii=False, sort_keys=True
                )
            )
            return 0

        if args.command == "backlog-list":
            entries = list_entries(state, status=args.status)
            print(
                json.dumps(
                    {"count": len(entries), "entries": entries},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "backlog-show":
            print(json.dumps(show_entry(state, args.video_id), indent=2, ensure_ascii=False))
            return 0

        if args.command == "backlog-decide":
            record = record_decision(
                state,
                video_id=args.video_id,
                decision=args.decision,
                decisions_file=args.decisions_file,
                reason=args.reason,
            )
            print(
                json.dumps(
                    {"recorded": record, "decisions_file": str(args.decisions_file)},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "backlog-plan":
            decisions = parse_decisions_file(args.decisions_file)
            plan = build_plan(state, decisions, decisions_file=str(args.decisions_file))
            print(json.dumps(plan, indent=2, ensure_ascii=False))
            return 1 if plan["conflicts"] else 0

        if args.command == "backlog-apply":
            decisions = parse_decisions_file(args.decisions_file)
            summary = apply_decisions(
                state, decisions, decisions_file=str(args.decisions_file)
            )
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0

        if args.command == "guided-run":
            if args.collection is None and args.ids is None:
                print(
                    "guided-run: --collection and/or --ids is required",
                    file=sys.stderr,
                )
                return 2
            ids = (
                [part for part in args.ids.split(",")]
                if args.ids is not None
                else None
            )
            synthesis = parse_synthesis_args(args.synthesis or [])
            exit_code, report = run_guided(
                state=state,
                collection=args.collection,
                ids=ids,
                limit=args.limit,
                synthesis=synthesis,
                dry_run=args.dry_run,
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return exit_code

        if args.command == "collection-run":
            from .collection_run import CollectionRunError, run_collection

            try:
                exit_code, report = run_collection(
                    collection_url=args.collection_url,
                    state=state,
                    dry_run=args.dry_run,
                    limit=args.limit,
                    declared_count=args.declared_count,
                    end_evidence=args.end_evidence,
                )
            except CollectionRunError as exc:
                print(f"collection-run: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return exit_code

    except ReviewError as exc:
        print(f"review error: {exc}", file=sys.stderr)
        return 2
    except StateError as exc:
        print(f"state error: {exc}", file=sys.stderr)
        return 2

    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BootstrapError as exc:
        print(f"install-extractor: {exc}", file=sys.stderr)
        return 2
    except ExtractorError as exc:
        print(f"extractor error: {exc}", file=sys.stderr)
        return 2
    except PrepareError as exc:
        print(f"prepare error: {exc}", file=sys.stderr)
        return 2
    except OllamaRuntimeError as exc:
        print(f"ollama provisioning error: {exc}", file=sys.stderr)
        return 2
    except VisionError as exc:
        print(f"vision error: {exc}", file=sys.stderr)
        return 2
    except SynthesisError as exc:
        print(f"synthesis error: {exc}", file=sys.stderr)
        return 2
    except VerifyError as exc:
        print(f"verify error: {exc}", file=sys.stderr)
        return 2
    except CollectionError as exc:
        print(f"collection error: {exc}", file=sys.stderr)
        return 2
    except GuidedError as exc:
        print(f"guided-run: {exc}", file=sys.stderr)
        return 2
    except (ConnectionError, OSError) as exc:
        print(f"whisper transport error: {exc}", file=sys.stderr)
        return 2

    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
