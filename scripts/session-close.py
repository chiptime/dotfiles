#!/usr/bin/env python3
"""session-close — one-shot "archiva y cierra" for a project work session.

Collapses the projects-tracker close ritual into one command:
  1. detects the current OpenCode session (id, title, active hours) from
     ~/.local/share/opencode/opencode.db, matching the working directory;
  2. updates the project's block in ~/.dotfiles/scripts/projects.yaml
     (status / desc / next), printing the before -> after diff;
  3. appends the AI hours to the time ledger (`time-ledger add-ai`), with the
     session id so the dashboard deep-links to the conversation;
  4. regenerates the dashboard snapshot (`projects`) and prints its counts;
  5. prints the improve-system cadence (`improve-system --status`);
  6. commits ONLY scripts/projects.yaml in ~/.dotfiles, and only when "now" is
     outside working hours (default Mon-Thu 09:00-19:00, Fri 08:00-16:00,
     the same rule as commit-hours-audit). Inside working hours it skips.

Usage:
  session-close                       proposal only: session, hours, repos with
                                      commits since the session started, and
                                      the current block of this project
  session-close <project> [options]   close the session for <project>

Options:
  --next TEXT        concrete next action        --desc TEXT   one-line description
  --status S         active|proposal|paused|closed|cold
  --scope S          scope for a NEW block (default: personal)
  --hours H          override detected active hours
  --note TEXT        ledger note (default: session title)
  --session ID       override detected ses_... id
  --date D/M/YYYY    ledger date (default: today)
  --no-commit        never commit the tracker
  --dry-run          show what would change; write nothing

Environment (mostly for tests): DOTFILES, CODE_ROOT, SESSION_CLOSE_DB,
SESSION_CLOSE_NOW (ISO local time), WORK_HOURS_RULE (e.g. 1-4:09:00-19:00,5:08:00-16:00).
"""
import argparse
import math
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

STATUSES = ("active", "proposal", "paused", "closed", "cold")
DEFAULT_RULE = "1-4:09:00-19:00,5:08:00-16:00"
IDLE_GAP_MIN = 30  # gaps between messages longer than this are idle time
KEYS = ("status", "desc", "next")

HOME = Path(os.environ.get("HOME", str(Path.home())))
DOTFILES = Path(os.environ.get("DOTFILES", str(HOME / ".dotfiles")))
YAML_REL = "scripts/projects.yaml"
YAML = DOTFILES / YAML_REL
CODE_ROOT = Path(os.environ.get("CODE_ROOT", str(HOME / "Code")))
DB = Path(os.environ.get("SESSION_CLOSE_DB", str(HOME / ".local/share/opencode/opencode.db")))
SNAPSHOT = HOME / ".local/share/projects-dashboard/PROJECTS.md"


def die(msg: str) -> None:
    print(f"session-close: {msg}", file=sys.stderr)
    sys.exit(2)


def now() -> datetime:
    value = os.environ.get("SESSION_CLOSE_NOW")
    return datetime.fromisoformat(value) if value else datetime.now()


# --- working hours -----------------------------------------------------------

def parse_rule(spec: str) -> dict:
    rules = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        m = re.fullmatch(r"(\d)(?:-(\d))?:(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", part)
        if not m:
            die(f'invalid WORK_HOURS_RULE segment "{part}" (expected e.g. 1-4:09:00-19:00)')
        first, last = int(m[1]), int(m[2] or m[1])
        start, end = int(m[3]) * 60 + int(m[4]), int(m[5]) * 60 + int(m[6])
        if not (1 <= first <= last <= 7) or start >= end:
            die(f'invalid WORK_HOURS_RULE segment "{part}"')
        for day in range(first, last + 1):
            rules[day] = (start, end)
    return rules


def inside_working_hours(moment: datetime, rules: dict) -> bool:
    window = rules.get(moment.isoweekday())
    minutes = moment.hour * 60 + moment.minute
    return window is not None and window[0] <= minutes < window[1]


# --- session detection -------------------------------------------------------

def detect_session(cwd: str):
    """Most recently updated top-level session opened in cwd, or None."""
    if not DB.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "select id, title, time_created from session where parent_id is null and directory = ?"
            " order by time_updated desc limit 1",
            (cwd,),
        ).fetchone()
        if row is None:
            return None
        stamps = [r[0] for r in con.execute(
            "select time_created from message where session_id = ? order by time_created", (row[0],))]
        con.close()
    except sqlite3.Error as exc:
        print(f"session-close: warning: cannot read {DB}: {exc}", file=sys.stderr)
        return None
    active_ms = sum(b - a for a, b in zip(stamps, stamps[1:]) if b - a <= IDLE_GAP_MIN * 60_000)
    hours = max(0.25, math.ceil(active_ms / 3_600_000 * 4) / 4) if stamps else None
    return {"id": row[0], "title": row[1], "start_ms": row[2], "hours": hours}


def fmt_hours(hours: float) -> str:
    text = f"{hours:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


# --- projects.yaml block editing ---------------------------------------------

def block_range(lines: list, project: str):
    try:
        start = lines.index(f"{project}:")
    except ValueError:
        return None
    end = start + 1
    while end < len(lines) and (lines[end] == "" or lines[end].startswith(" ")):
        end += 1
    while end > start + 1 and lines[end - 1] == "":
        end -= 1
    return start, end


def read_block(lines: list, project: str) -> dict:
    found = block_range(lines, project)
    if found is None:
        return {}
    values = {}
    for line in lines[found[0] + 1:found[1]]:
        m = re.fullmatch(r"  ([A-Za-z_]+): ?(.*)", line)
        if m:
            values[m[1]] = m[2]
    return values


def apply_block(text: str, project: str, updates: dict, scope: str):
    """Return (new_text, diff_lines). Only keys in `updates` change."""
    lines = text.split("\n")
    found = block_range(lines, project)
    diff = []
    if found is None:
        block = [f"{project}:", f"  scope: {scope}", f"  status: {updates.get('status', 'active')}"]
        block += [f"  {k}: {updates[k]}" for k in ("desc", "next") if k in updates]
        diff = [f"+ {line}" for line in block]
        body = text if text.endswith("\n") or text == "" else text + "\n"
        return body + "\n" + "\n".join(block) + "\n", diff
    start, end = found
    for key in KEYS:
        if key not in updates:
            continue
        new_line = f"  {key}: {updates[key]}"
        idx = next((i for i in range(start + 1, end) if re.match(rf"  {key}:", lines[i])), None)
        if idx is None:
            lines.insert(end, new_line)
            end += 1
            diff.append(f"+ {key}: {updates[key]}")
        elif lines[idx] != new_line:
            diff.append(f"- {lines[idx].strip()}")
            diff.append(f"+ {key}: {updates[key]}")
            lines[idx] = new_line
    return "\n".join(lines), diff


# --- helpers -----------------------------------------------------------------

def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def project_of(cwd: str) -> str:
    top = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    path = top.stdout.strip() if top.returncode == 0 else cwd
    name = Path(path).name
    # Worktrees live in <repo>-worktrees/<name>: the tracker key is <repo>.
    parent = Path(path).parent.name
    return parent[: -len("-worktrees")] if parent.endswith("-worktrees") else name


def repos_touched_since(start_ms: int):
    since = f"@{start_ms // 1000}"
    seen = set()
    for git_entry in sorted(CODE_ROOT.glob("**/.git")):
        repo = git_entry.parent
        if len(repo.relative_to(CODE_ROOT).parts) > 6 or "node_modules" in repo.parts:
            continue
        log = run(["git", "log", "--all", f"--since={since}", "--format=%h %s"], cwd=repo)
        commits = [c for c in log.stdout.splitlines() if c.strip()]
        if log.returncode == 0 and commits:
            # A worktree shares refs with its main checkout: list that history once.
            key = tuple(commits)
            if key not in seen:
                seen.add(key)
                yield repo, commits


def snapshot_counts() -> list:
    if not SNAPSHOT.exists():
        return []
    return [line[3:].strip() for line in SNAPSHOT.read_text().splitlines() if line.startswith("## ")]


# --- commands ----------------------------------------------------------------

def propose(cwd: str) -> int:
    session = detect_session(cwd)
    if session is None:
        print(f"No OpenCode session found for {cwd}.")
    else:
        started = datetime.fromtimestamp(session["start_ms"] / 1000)
        print(f"Session: {session['id']} — {session['title']}")
        print(f"Started: {started:%d/%m/%Y %H:%M} · active ≈ {fmt_hours(session['hours'] or 0.25)} h")
        print("\nRepos with commits since the session started:")
        touched = list(repos_touched_since(session["start_ms"]))
        for repo, commits in touched:
            print(f"  {repo.relative_to(CODE_ROOT)}")
            for commit in commits[:10]:
                print(f"    {commit}")
        if not touched:
            print("  (none)")
    project = project_of(cwd)
    lines = YAML.read_text().split("\n") if YAML.exists() else []
    block = read_block(lines, project)
    print(f"\nTracker block '{project}':" if block else f"\nNo tracker block for '{project}'.")
    for key, value in block.items():
        print(f"  {key}: {value}")
    print(f"\nClose with: session-close {project} --next \"...\" [--desc \"...\"] [--status ...]")
    return 0


def close(args, cwd: str) -> int:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.project):
        die("project must be a repository basename")
    if args.status is not None and args.status not in STATUSES:
        die(f"status must be one of: {', '.join(STATUSES)}")
    updates = {k: getattr(args, k) for k in KEYS if getattr(args, k) is not None}
    for key, value in [*updates.items(), ("note", args.note or ""), ("scope", args.scope)]:
        if "\n" in value or "\r" in value:
            die(f"--{key} must be a single line")
    if not YAML.exists():
        die(f"tracker not found: {YAML}")
    text = YAML.read_text()
    if block_range(text.split("\n"), args.project) is None and "desc" not in updates:
        die(f"'{args.project}' has no tracker block; pass --desc to create it")

    session = None if args.session and args.hours else detect_session(cwd)
    session_id = args.session or (session or {}).get("id", "")
    if args.hours is not None:
        try:
            if float(args.hours) <= 0:
                raise ValueError
        except ValueError:
            die("--hours must be a positive number")
        hours = args.hours
    elif session is not None and session["hours"] is not None:
        hours = fmt_hours(session["hours"])
    else:
        die("no OpenCode session detected for this directory; pass --hours")
    title = (session or {}).get("title") or ""
    # OpenCode's placeholder title ("New session - <iso>") says nothing useful.
    note = args.note or ("" if title.startswith("New session") else title) or "session"
    moment = now()
    date = args.date or f"{moment.day}/{moment.month}/{moment.year}"

    new_text, diff = apply_block(text, args.project, updates, args.scope)
    print(f"projects.yaml · {args.project}:")
    print("\n".join(f"  {line}" for line in diff) if diff else "  (no changes)")
    print(f"ledger: add-ai {date} {args.project} {hours} \"{note}\" {session_id or '(no session id)'}")

    rules = parse_rule(os.environ.get("WORK_HOURS_RULE", DEFAULT_RULE))
    working = inside_working_hours(moment, rules)
    if args.dry_run:
        print("dry-run: nothing written.")
        return 0

    if diff:
        YAML.write_text(new_text)
    ledger = run(["time-ledger", "add-ai", date, args.project, hours, note, *([session_id] if session_id else [])])
    print((ledger.stdout or ledger.stderr).strip())

    regen = run(["projects"])
    if regen.returncode != 0:
        print(f"warning: `projects` failed: {regen.stderr.strip()[:200]}")
    counts = snapshot_counts()
    if counts:
        print(f"snapshot: {SNAPSHOT} · " + " · ".join(counts))

    cadence = run(["improve-system", "--status"])
    if cadence.stdout.strip():
        print(cadence.stdout.strip().splitlines()[-1])

    if args.no_commit:
        print("commit omitido: --no-commit.")
    elif working:
        print(f"commit omitido: dentro de horario laboral ({moment:%a %H:%M}).")
    elif run(["git", "diff", "--quiet", "HEAD", "--", YAML_REL], cwd=DOTFILES).returncode == 0:
        print("commit omitido: projects.yaml sin cambios.")
    else:
        msg = f"chore(projects): update {args.project} tracker"
        res = run(["git", "commit", "-q", "-m", msg, "--", YAML_REL], cwd=DOTFILES)
        if res.returncode != 0:
            print(f"warning: commit failed: {(res.stderr or res.stdout).strip()[:300]}")
            return 1
        sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=DOTFILES).stdout.strip()
        print(f"commit: {sha} {msg} (solo {YAML_REL}; sin push)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="session-close", add_help=False)
    parser.add_argument("project", nargs="?")
    for flag in ("--next", "--desc", "--status", "--hours", "--note", "--session", "--date"):
        parser.add_argument(flag)
    parser.add_argument("--scope", default="personal")
    parser.add_argument("--no-commit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args()
    if args.help:
        print(__doc__.strip())
        return 0
    cwd = os.getcwd()
    return propose(cwd) if args.project is None else close(args, cwd)


if __name__ == "__main__":
    sys.exit(main())
