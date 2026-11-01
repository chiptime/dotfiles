#!/usr/bin/env python3
"""
agy-gc.py — Garbage collector and TTL pruner for Antigravity CLI (agy) subagents and workers.

Identifies and purges:
1. Phantom sessions in DB whose disk artifacts (brain/) were already removed.
2. Native agy subagents (parent_conversation_id != "").
3. Ephemeral workspace executions (/tmp/agy-*, /tmp/agy-spike/*, ~/.cache/ai-stack/*).
4. Headless worker runs delegated by external harnesses:
   - SDD/ODD explorers (read-only explorer, sdd exploration, explore fallback sub-agent)
   - Task specialists (file search specialist, title generator)
5. Automated test probes / harness integration tests (ping/pong, word storage, counting).
6. Abandoned empty sessions (step_count == 0).
7. Orphaned MCP bridge definitions (pi-bridge-<PID> where PID is dead).

HARD RULE: Orchestrator sessions (Gentle AI SDD/ODD orchestrator) are NEVER deleted.

Default behavior: DRY RUN. Pass --apply to perform actual deletions.
Pass --list to view all detected sessions with their titles and dates.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

UUID_REGEX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

ORCHESTRATOR_SIGNATURES = [
    "gentle-ai:orchestrator",
    "sdd orchestrator",
    "odd orchestrator",
    "gentle ai — orchestrator",
    "orchestrator",
]

HARNESS_SIGNATURES = [
    ("sdd_odd_explorer", "you are the read-only explorer"),
    ("sdd_odd_explorer", "you are the sdd exploration"),
    ("sdd_odd_explorer", "you are the dedicated sdd-"),
    ("sdd_odd_explorer", "sdd explore router"),
    ("sdd_odd_explorer", "you are a subagent"),
    ("explore_fallback", "you are the explore fallback sub-agent"),
    ("file_search", "you are a file search specialist"),
    ("title_generator", "you are a title generator"),
]

PROBE_SIGNATURES = [
    ("test_probe", "say ok and nothing else"),
    ("test_probe", "remember the word manzana"),
    ("test_probe", "reply with exactly"),
    ("test_probe", "count from 1 to"),
    ("test_probe", "first say the word"),
    ("test_probe", "reply only ok"),
    ("test_probe", "ping"),
]


def parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        pass
    try:
        clean = dt_str.split(".")[0]
        if "+" in dt_str:
            offset = "+" + dt_str.split("+")[1]
            return datetime.fromisoformat(clean + offset)
        return datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def get_dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            pass
    return total


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_conversation_active_in_os(cid: str) -> bool:
    try:
        res = subprocess.run(
            ["pgrep", "-f", cid],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return bool(res.stdout.strip())
    except Exception:
        return False


def is_orchestrator_session(head: str, title: str, preview: str) -> bool:
    combined = (head + " " + title + " " + preview).lower()
    return any(sig in combined for sig in ORCHESTRATOR_SIGNATURES)


def inspect_transcript_harness(brain_session_dir: Path, title: str, preview: str) -> Optional[Tuple[str, str]]:
    transcript_file = brain_session_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript_file.is_file():
        return None
    try:
        with transcript_file.open("r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096).lower()
            # Hard rule: NEVER delete orchestrator sessions
            if is_orchestrator_session(head, title, preview):
                return None

            for cat_name, sig in HARNESS_SIGNATURES:
                if sig in head:
                    return cat_name, sig
            for cat_name, sig in PROBE_SIGNATURES:
                if sig in head:
                    return cat_name, sig
    except Exception:
        pass
    return None


class Candidate:
    def __init__(
        self,
        cid: str,
        category: str,
        reason: str,
        title: str,
        preview: str,
        workspace: str,
        mtime: Optional[datetime],
        brain_size: int,
        conv_size: int,
    ):
        self.cid = cid
        self.category = category
        self.reason = reason
        self.title = title
        self.preview = preview
        self.workspace = workspace
        self.mtime = mtime
        self.brain_size = brain_size
        self.conv_size = conv_size

    @property
    def display_text(self) -> str:
        txt = self.title if self.title.strip() else self.preview
        return txt.replace("\n", " ").strip() if txt else "[VACÍO]"

    @property
    def total_size(self) -> int:
        return self.brain_size + self.conv_size


def scan_candidates(
    db_path: Path,
    brain_dir: Path,
    conv_dir: Path,
    ttl_days: int,
) -> Tuple[List[Candidate], int]:
    if not db_path.is_file():
        return [], 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    candidates: List[Candidate] = []

    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    cursor = conn.cursor()

    rows = cursor.execute("""
        SELECT 
            conversation_id, 
            title,
            preview,
            workspace_uris, 
            step_count, 
            parent_conversation_id, 
            last_modified_time,
            status,
            not_fully_idle
        FROM conversation_summaries
    """).fetchall()
    conn.close()

    total_scanned = len(rows)

    for cid, title, prev, ws, steps, parent_id, mtime_str, status, not_fully_idle in rows:
        if not cid or not UUID_REGEX.match(cid):
            continue

        mtime = parse_iso_datetime(mtime_str)
        # Check cutoff unless date is invalid / 0001-01-01 (abandoned sessions)
        if mtime and mtime.year > 1970 and mtime > cutoff:
            continue

        # Active state safety gates
        if not_fully_idle == 1 or status == "CASCADE_RUN_STATUS_RUNNING" or status == "ACTIVE":
            continue
        if is_conversation_active_in_os(cid):
            continue

        title_str = title or ""
        prev_str = prev or ""

        # Hard guard: orchestrator sessions must NEVER be deleted
        if is_orchestrator_session("", title_str, prev_str):
            continue

        category = ""
        reason = ""
        session_brain = brain_dir / cid

        # Category 0: Phantom session in DB whose brain directory is already gone
        if not session_brain.is_dir():
            category = "phantom_session"
            reason = "missing brain on disk"
        # Category 1: Empty abandoned session
        elif steps == 0 or (mtime and mtime.year <= 1970):
            category = "empty_abandoned"
            reason = f"{steps} steps"
        # Category 2: Native agy subagent
        elif parent_id and parent_id.strip():
            category = "native_subagent"
            reason = f"parent={parent_id[:8]}"
        # Category 3: Ephemeral or cache workspace
        elif ws and ("/tmp/" in ws.lower() or ".cache/ai-stack" in ws.lower()):
            category = "tmp_cache_workspace"
            reason = "tmp/cache workspace"
        # Category 4: Harness signatures & probes in transcript
        else:
            sig_match = inspect_transcript_harness(session_brain, title_str, prev_str)
            if sig_match:
                category, reason = sig_match

        if category:
            b_size = get_dir_size(session_brain)
            c_size = (
                get_dir_size(conv_dir / f"{cid}.db")
                + get_dir_size(conv_dir / f"{cid}.db-wal")
                + get_dir_size(conv_dir / f"{cid}.db-shm")
            )
            candidates.append(
                Candidate(
                    cid=cid,
                    category=category,
                    reason=reason,
                    title=title_str,
                    preview=prev_str,
                    workspace=ws or "",
                    mtime=mtime,
                    brain_size=b_size,
                    conv_size=c_size,
                )
            )

    return candidates, total_scanned


def scan_dead_mcp_bridges() -> List[Tuple[Path, int]]:
    dead_bridges: List[Tuple[Path, int]] = []
    roots = [
        Path.home() / ".gemini" / "antigravity-cli" / "mcp",
        Path.home() / ".gemini" / "antigravity-ide" / "mcp",
    ]
    for r in roots:
        if not r.is_dir():
            continue
        for p in r.iterdir():
            if p.is_dir() and p.name.startswith("pi-bridge-"):
                pid_str = p.name.replace("pi-bridge-", "")
                if pid_str.isdigit():
                    pid = int(pid_str)
                    if not is_process_running(pid):
                        dead_bridges.append((p, pid))
    return dead_bridges


def clean_mcp_config(dead_bridges: List[Tuple[Path, int]], apply: bool) -> int:
    config_path = Path.home() / ".gemini" / "config" / "mcp_config.json"
    if not config_path.is_file():
        return 0

    dead_names = {p.name for p, _ in dead_bridges}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0

    servers = data.get("mcpServers", {})
    to_delete = [
        k for k in servers.keys()
        if k in dead_names or (k.startswith("pi-bridge-") and k.replace("pi-bridge-", "").isdigit() and not is_process_running(int(k.replace("pi-bridge-", ""))))
    ]

    if not to_delete:
        return 0

    if apply:
        for k in to_delete:
            del servers[k]
        try:
            with config_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: could not update mcp_config.json: {e}", file=sys.stderr)

    return len(to_delete)


def purge_candidates(
    candidates: List[Candidate],
    db_path: Path,
    brain_dir: Path,
    conv_dir: Path,
) -> int:
    if not candidates:
        return 0

    conn = sqlite3.connect(db_path.as_posix())
    cursor = conn.cursor()

    deleted_count = 0
    try:
        for cand in candidates:
            # 1. Delete from SQLite
            cursor.execute(
                "DELETE FROM conversation_summaries WHERE conversation_id = ?",
                (cand.cid,),
            )

            # 2. Delete brain directory
            b_path = brain_dir / cand.cid
            if b_path.exists():
                shutil.rmtree(b_path, ignore_errors=True)

            # 3. Delete conversation database files
            for ext in (".db", ".db-wal", ".db-shm"):
                f_path = conv_dir / f"{cand.cid}{ext}"
                if f_path.exists():
                    try:
                        f_path.unlink()
                    except OSError:
                        pass

            deleted_count += 1

        conn.commit()
    finally:
        conn.close()

    # Prune and refresh protobuf store to stop ghosts from resurrecting
    proto_file = db_path.parent / "jetbox_summaries_proto.pb"
    if proto_file.exists():
        try:
            proto_file.unlink()
            # Trigger a lightweight agy call to regenerate a clean proto from SQLite
            subprocess.run(["agy", "agents"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            pass

    return deleted_count


def purge_dead_bridges(dead_bridges: List[Tuple[Path, int]]) -> int:
    count = 0
    for p, _ in dead_bridges:
        try:
            shutil.rmtree(p, ignore_errors=True)
            count += 1
        except OSError:
            pass
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Garbage collector and TTL pruner for agy subagents and worker runs."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletions (default is dry-run mode).",
    )
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=7,
        help="Retention period in days (default: 7).",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all matching sessions with ID, date, size and title/summary.",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Skip cleaning orphaned pi-bridge MCP directories.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose candidate details.",
    )

    args = parser.parse_args()

    base_dir = Path.home() / ".gemini" / "antigravity-cli"
    db_path = base_dir / "conversation_summaries.db"
    brain_dir = base_dir / "brain"
    conv_dir = base_dir / "conversations"

    if not db_path.exists():
        print(f"Error: {db_path} not found.", file=sys.stderr)
        return 1

    candidates, total_scanned = scan_candidates(
        db_path=db_path,
        brain_dir=brain_dir,
        conv_dir=conv_dir,
        ttl_days=args.ttl_days,
    )

    dead_bridges = [] if args.no_mcp else scan_dead_mcp_bridges()

    # Category breakdown
    cats: Dict[str, List[Candidate]] = {}
    for c in candidates:
        cats.setdefault(c.category, []).append(c)

    total_bytes = sum(c.total_size for c in candidates)
    mcp_bytes = sum(get_dir_size(p) for p, _ in dead_bridges)

    mode_label = "APPLY" if args.apply else "DRY-RUN"

    print(f"===========================================================")
    print(f" agy-gc: Antigravity Subagent Garbage Collector [{mode_label}]")
    print(f" TTL: {args.ttl_days} days | Database: {db_path}")
    print(f"===========================================================")
    print(f"Total conversations scanned: {total_scanned}")
    print(f"Candidates identified:       {len(candidates)}")
    for cat_name, cat_list in sorted(cats.items()):
        cat_mb = sum(c.total_size for c in cat_list) / (1024 * 1024)
        print(f"  - {cat_name:<25}: {len(cat_list):3d} sessions ({cat_mb:6.2f} MB)")

    if dead_bridges:
        print(f"Orphaned MCP pi-bridge dirs: {len(dead_bridges):3d} ({mcp_bytes / (1024*1024):.2f} MB)")

    print(f"-----------------------------------------------------------")
    print(f"Total space to reclaim:      {(total_bytes + mcp_bytes) / (1024 * 1024):.2f} MB")
    print(f"-----------------------------------------------------------")

    if args.list or args.verbose:
        for cat_name, cat_list in sorted(cats.items()):
            print(f"\n--- {cat_name.upper()} ({len(cat_list)} sesiones) ---")
            for c in cat_list:
                dt_str = c.mtime.strftime("%Y-%m-%d") if c.mtime and c.mtime.year > 1970 else "0001-01-01"
                mb = c.total_size / (1024 * 1024)
                print(f"[{dt_str}] ({mb:4.1f}MB) {c.cid} : {c.display_text[:75]}")

    if not args.apply:
        print("\n[!] DRY-RUN complete. No files or records were modified.")
        if not args.list:
            print("    Use '--list' to inspect all sessions.")
        print("    Run with '--apply' to execute the purge.")
        return 0

    # Perform actual purge
    deleted_sessions = purge_candidates(candidates, db_path, brain_dir, conv_dir)
    deleted_bridges = purge_dead_bridges(dead_bridges)
    cleaned_configs = clean_mcp_config(dead_bridges, apply=True)

    print(f"\n[+] PURGE COMPLETED SUCCESSFULLY:")
    print(f"    - Sessions deleted from SQLite & disk: {deleted_sessions}")
    if not args.no_mcp:
        print(f"    - Dead MCP bridge directories removed: {deleted_bridges}")
        print(f"    - Stale mcp_config.json entries pruned: {cleaned_configs}")
    print(f"    - Space reclaimed: {(total_bytes + mcp_bytes) / (1024 * 1024):.2f} MB\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
