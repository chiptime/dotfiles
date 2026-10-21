#!/usr/bin/env python3
"""Discover the custom dotfiles ecosystem and emit/merge launchpad tiles.

Sources:
  - scripts/*.sh        every non-test script; description from its header
                        comment; command resolved via shell/aliases.sh when
                        the script is aliased, else the repo path
  - ai/*/ with README   subsystem catalog entries (name, description, docs)
  - ~/Code/personal/*/  personal project repos (LAUNCHPAD_PROJECT_ROOTS to
                        override, colon-separated); *-worktrees skipped
  - listening ports     web tiles for local HTTP services not already
                        covered by an existing tile (any HTTP response
                        counts, title becomes the name)

Modes:
  (no args)   print the discovered tiles as JSON to stdout
  --merge     add only NEW tile ids to tiles.json (LAUNCHPAD_TILES or
              ~/.config/launchpad/tiles.json); never removes or overwrites
              existing entries, so manual edits always win
  --retag     add missing auto tags to EXISTING tiles (union with manual
              tags, never removes); idempotent; does not run discovery
  --sync      discover once, then --merge and --retag in ONE locked
              read-modify-write (what the launchpad-discover timer runs)

All writer modes (--merge, --retag, --sync) serialize on one cooperative
flock kept in machine-local state (see lock_path) from read through commit,
and replace the resolved config target atomically, so a config symlink,
its permissions, manual fields and unknown top-level keys survive. The lock
only coordinates writers that use this script; it does not stop an editor
from saving the file at any time.

Stdlib only; read/write apart from the explicit merge/retag/sync target.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOME = Path(os.environ.get("HOME", "/home"))
MAX_DESC = 90
# System/infra ports that are never ecosystem services.
SKIP_PORTS = {53, 631}


def tilde(path: Path | str) -> str:
    return str(path).replace(str(HOME), "~", 1)


def clean_desc(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[: MAX_DESC - 1] + "…" if len(text) > MAX_DESC else text


# Keyword domains matched over id+name+description so the front's type chips
# group tiles across sources (teams tooling, voice stack, AI, SAP).
DOMAIN_PATTERNS = [
    ("teams", re.compile(r"\bteams?\b")),
    ("voz", re.compile(r"voz|voice|whisper|\btts\b|herdr")),
    ("ia", re.compile(
        r"\bia\b|\bllm\b|\bai\b|\bagy\b|llama|embedding|\bagents?\b|\bagente\b|\bwhisper\b")),
    ("sap", re.compile(r"\bsap\b|odata|ssff")),
]

# Personal/work project roots; the basename maps to its tag (case is not
# significant for the tag lookup — the real work root is ~/Code/Work).
PROJECT_ROOTS = os.environ.get("LAUNCHPAD_PROJECT_ROOTS") or ":".join(
    [f"{HOME}/Code/personal", f"{HOME}/Code/Work"])
ROOT_TAG_MAP = {"personal": "personal", "work": "trabajo"}

# Vendor/build dirs never walked for nested git repos.
SKIP_WALK_DIRS = {"node_modules", "dist", "target"}

# Repo basenames too generic to be tile ids on their own.
GENERIC_REPO_NAMES = {"backend", "frontend", "api", "web", "app", "server", "client", "mid"}


def repo_identity(repo: Path, used: set[str]) -> str:
    """Repo basename as id. When the basename is generic (backend, frontend,
    ...) or already taken by another repo in this run, parent dirs are
    prepended until the id is specific and unique."""
    parts = [repo.name]
    current = repo.parent
    while (parts[0].lower() in GENERIC_REPO_NAMES
           or "-".join(parts).lower() in used) and current.name:
        parts.insert(0, current.name)
        current = current.parent
    return "-".join(parts)

# Legacy auto tags from earlier taxonomy versions: renamed on --retag,
# never removed. Manual tags outside this list are always preserved.
TAG_RENAMES = {"proyecto": "personal", "llm": "ia"}


def auto_tags(tile: dict) -> list[str]:
    """Tags derivable from the tile itself: source shapes (personal, trabajo,
    ia) plus keyword domains. Manual tags are never touched here; --retag
    unions."""
    text = " ".join(str(tile.get(k) or "") for k in ("id", "name", "description")).lower()
    loc = " ".join(str(tile.get(k) or "") for k in ("location", "docs")).lower()
    tags: set[str] = set()
    if "code/personal/" in loc:
        tags.add("personal")
    if "code/work/" in loc:
        tags.add("trabajo")
    if ".dotfiles/ai/" in loc:
        tags.add("ia")
    for tag, pattern in DOMAIN_PATTERNS:
        if pattern.search(text):
            tags.add(tag)
    return sorted(tags)


def alias_map() -> dict[str, str]:
    """basename -> alias name, from `alias name='...<basename>'` lines."""
    aliases: dict[str, str] = {}
    alias_file = REPO / "shell" / "aliases.sh"
    if not alias_file.exists():
        return aliases
    for line in alias_file.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*alias\s+([\w.-]+)=(['\"])(.*?)\2", line)
        if not m:
            continue
        name, _, target = m.groups()
        base = Path(target).name
        aliases.setdefault(base, name)
    return aliases


def discover_scripts(aliases: dict[str, str]) -> list[dict]:
    tiles = []
    scripts_dir = REPO / "scripts"
    if not scripts_dir.is_dir():
        return tiles
    for f in sorted(scripts_dir.glob("*.sh")):
        if f.name.startswith(("test-", "test_")):
            continue
        desc = ""
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines()[:8]:
            line = line.strip()
            if line.startswith("#!"):
                continue
            if line.startswith("#"):
                desc = clean_desc(line.lstrip("# ").rstrip("-="))
                break
        command = aliases.get(f.name, tilde(f))
        tile = {
            "id": f.stem,
            "name": f.stem.replace("-", " ").replace("_", " ").title(),
            "kind": "script",
            "description": desc or None,
            "command": command,
            "location": tilde(f),
        }
        if (tags := auto_tags(tile)):
            tile["tags"] = tags
        tiles.append(tile)
    return tiles


def readme_first_body_line(readme: Path) -> str:
    """First meaningful body line of a README (skips the `# ` title itself)."""
    seen_title = False
    for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            seen_title = True
            continue
        if not seen_title:
            continue
        if stripped.startswith(("|", "!", ">", "[![", "Status", "status")):
            continue
        return clean_desc(stripped)
    return ""


def discover_subsystems() -> list[dict]:
    tiles = []
    ai_dir = REPO / "ai"
    if not ai_dir.is_dir():
        return tiles
    for d in sorted(ai_dir.iterdir()):
        readme = d / "README.md"
        if not (d.is_dir() and readme.exists()):
            continue
        desc = readme_first_body_line(readme)
        tile = {
            "id": d.name,
            "name": d.name.replace("-", " ").replace("_", " ").title(),
            "kind": "script",
            "description": desc or None,
            "docs": tilde(readme),
            "location": tilde(d),
        }
        if (tags := auto_tags(tile)):
            tile["tags"] = tags
        tiles.append(tile)
    return tiles


def find_git_repos(base: Path) -> list[Path]:
    """Every git repo under base at any depth. Prunes hidden dirs, vendor
    dirs and worktree collections; a repo's own tree is not descended into."""
    repos: list[Path] = []
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name.endswith("-worktrees") or entry.name in SKIP_WALK_DIRS:
                continue
            if (entry / ".git").exists():
                repos.append(entry)
            else:
                stack.append(entry)
    return sorted(repos, key=lambda p: str(p))


def discover_projects() -> list[dict]:
    """Personal/work project repos under the roots (default ~/Code/personal
    and ~/Code/Work, override via LAUNCHPAD_PROJECT_ROOTS, colon-separated).
    Roots are walked recursively for git repos (nested client/repo layouts).
    Root basename maps to its tag case-insensitively (ROOT_TAG_MAP); id is
    the repo basename, disambiguated with the parent dir on collision."""
    tiles = []
    used_ids: set[str] = set()
    for root in PROJECT_ROOTS.split(":"):
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        base_tag = ROOT_TAG_MAP.get(base.name.lower())
        for repo in find_git_repos(base):
            tile_id = repo_identity(repo, used_ids)
            readme = repo / "README.md"
            desc = readme_first_body_line(readme) if readme.exists() else ""
            tile = {
                "id": tile_id,
                "name": tile_id.replace("-", " ").replace("_", " ").title(),
                "kind": "project",
                "description": desc or None,
                "docs": tilde(readme) if readme.exists() else None,
                "location": tilde(repo),
            }
            tags = set(auto_tags(tile))
            if base_tag:
                tags.add(base_tag)
            if tags:
                tile["tags"] = sorted(tags)
            used_ids.add(tile_id)
            tiles.append(tile)
    return tiles


def discover_ports() -> list[dict]:
    tiles = []
    try:
        out = subprocess.run(
            ["ss", "-tlnH"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return tiles
    ports: set[int] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or ":*" in parts[3]:
            continue
        try:
            port = int(parts[3].rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        if port > 1024 and port not in SKIP_PORTS:
            ports.add(port)
    for port in sorted(ports):
        try:
            probe = subprocess.run(
                ["curl", "-s", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}",
                 f"http://127.0.0.1:{port}/"],
                capture_output=True, text=True, timeout=4,
            )
            if probe.stdout.strip() not in {"200", "301", "302", "401", "403"}:
                continue
            title_out = subprocess.run(
                ["curl", "-s", "-m", "2", f"http://127.0.0.1:{port}/"],
                capture_output=True, text=True, timeout=4,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        title = ""
        m = re.search(r"<title[^>]*>([^<]+)", title_out, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
        tile = {
            "id": f"port-{port}",
            "name": title or f"Port {port}",
            "kind": "web",
            "description": None,
            "url": f"http://127.0.0.1:{port}",
        }
        if (tags := auto_tags(tile)):
            tile["tags"] = tags
        tiles.append(tile)
    return tiles


def discover_all() -> list[dict]:
    aliases = alias_map()
    return (
        discover_scripts(aliases)
        + discover_subsystems()
        + discover_projects()
        + discover_ports()
    )


def tiles_target() -> Path:
    target = os.environ.get("LAUNCHPAD_TILES") or str(
        Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
        / "launchpad" / "tiles.json"
    )
    return Path(target)


LOCK_TIMEOUT_SECS = 120.0


class UpdateError(Exception):
    """A tiles update was rejected or failed; the stored file is untouched."""


def lock_path() -> Path:
    """Machine-local writer lock: XDG_STATE_HOME (else ~/.local/state), never
    the repo or the tiles config directory."""
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "launchpad" / "tiles.lock"


def lock_timeout() -> float:
    raw = os.environ.get("LAUNCHPAD_LOCK_TIMEOUT_SECS")
    if not raw:
        return LOCK_TIMEOUT_SECS
    try:
        return max(0.0, float(raw))
    except ValueError:
        raise UpdateError(
            f"cannot lock {lock_path()}: invalid LAUNCHPAD_LOCK_TIMEOUT_SECS={raw!r}"
        ) from None


@contextlib.contextmanager
def tiles_lock():
    """Cooperative exclusive flock, bounded wait. Only writers that use this
    script honor it; arbitrary editors are not stopped."""
    path = lock_path()
    timeout = lock_timeout()
    fd = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise UpdateError(
                        f"cannot lock {path}: another discover writer held it "
                        f"for over {timeout:g}s") from None
                time.sleep(0.05)
    except OSError as e:
        if fd is not None:
            os.close(fd)
        raise UpdateError(f"cannot lock {path}: {e}") from e
    except UpdateError:
        os.close(fd)
        raise
    try:
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the flock


def _load_doc(path: Path, shown: Path, create_missing: bool) -> tuple[dict, bool]:
    """Parse the stored config. Returns (document, existed)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError as e:
        if create_missing:
            return {"tiles": []}, False
        raise UpdateError(f"cannot parse {shown}: {e}") from e
    except OSError as e:
        raise UpdateError(f"cannot parse {shown}: {e}") from e
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as e:  # JSONDecodeError and UnicodeDecodeError
        raise UpdateError(f"cannot parse {shown}: {e}") from e
    tiles = doc.get("tiles", []) if isinstance(doc, dict) else None
    if (not isinstance(tiles, list)
            or not all(isinstance(t, dict) and isinstance(t.get("id", ""), str)
                       for t in tiles)):
        raise UpdateError(
            f"cannot parse {shown}: expected an object whose \"tiles\" is a list "
            "of objects with string ids")
    return doc, True


def _atomic_write(resolved: Path, text: str) -> None:
    """Temp file in the target's directory, fsync, os.replace; the temp file
    is removed on any failure, including interrupts."""
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        st = resolved.stat()
        mode, owner = stat.S_IMODE(st.st_mode), (st.st_uid, st.st_gid)
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode, owner = 0o666 & ~umask, None
    fd, tmp_name = tempfile.mkstemp(
        dir=resolved.parent, prefix=f".{resolved.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fchmod(fh.fileno(), mode)
            if owner:
                with contextlib.suppress(OSError):
                    os.fchown(fh.fileno(), *owner)
            os.fsync(fh.fileno())
        os.replace(tmp_name, resolved)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    with contextlib.suppress(OSError):
        dir_fd = os.open(resolved.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def update_tiles(mutate, *, create_missing: bool = True):
    """Locked read-modify-write of the tiles config. `mutate(tiles)` edits the
    list in place and returns a result; invalid input (ValueError from it or
    a bad stored shape) raises UpdateError with the stored bytes untouched.
    The symlinked config is resolved and its target replaced atomically, so
    the link, permissions, manual fields and unknown top-level data survive.
    An unchanged document is not rewritten. Returns (shown path, result)."""
    shown = tiles_target()
    with tiles_lock():
        try:
            resolved = shown.resolve()
        except (OSError, RuntimeError) as e:
            raise UpdateError(f"cannot parse {shown}: {e}") from e
        doc, existed = _load_doc(resolved, shown, create_missing)
        before = copy.deepcopy(doc)
        tiles = doc.get("tiles", [])
        try:
            result = mutate(tiles)
        except ValueError as e:
            raise UpdateError(f"cannot parse {shown}: {e}") from e
        if "tiles" not in doc and tiles:
            doc["tiles"] = tiles
        if existed and doc == before:
            return shown, result
        try:
            _atomic_write(resolved, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        except OSError as e:
            raise UpdateError(f"cannot write {resolved}: {e}") from e
    return shown, result


def retag_tiles(tiles: list[dict]) -> list:
    """Sync auto-assignable fields on existing tiles: rename legacy tags,
    reconcile script → project kind for tiles under a project root, and add
    missing auto tags (union). Manual tags are never removed; second run is
    a no-op. Returns the ids of changed tiles."""
    for tile in tiles:
        tags = tile.get("tags")
        if tags is not None and not (
                isinstance(tags, list) and all(isinstance(t, str) for t in tags)):
            raise ValueError(f"tile {tile.get('id')!r}: tags must be a list of strings")
    root_paths = [str(Path(r).expanduser()).rstrip("/") for r in PROJECT_ROOTS.split(":")]
    changed = []
    for tile in tiles:
        current_tags = set(tile.get("tags") or [])
        renamed = {TAG_RENAMES.get(tag, tag) for tag in current_tags}
        merged = renamed | set(auto_tags(tile))
        tags_changed = merged != current_tags
        kind_changed = False
        location = str(tile.get("location") or "")
        if tile.get("kind") == "script" and location:
            expanded = os.path.expanduser(location)
            if any(expanded.startswith(root + "/") for root in root_paths):
                tile["kind"] = "project"
                kind_changed = True
        if tags_changed:
            tile["tags"] = sorted(merged)
        if tags_changed or kind_changed:
            changed.append(tile.get("id"))
    return changed


def merge_tiles(tiles: list[dict], discovered: list[dict]) -> list:
    """Append discovered tiles with NEW ids (and not on an already covered
    port) in place; existing entries are never touched. Returns added ids."""
    known_ids = {t.get("id") for t in tiles}
    covered_ports = {
        m.group(1) for t in tiles if isinstance(t.get("url"), str) and t["url"]
        for m in [re.search(r":(\d+)(?:/|$)", t["url"])] if m
    }
    added = []
    for cand in discovered:
        if cand["id"] in known_ids:
            continue
        m = re.search(r":(\d+)(?:/|$)", cand.get("url", ""))
        if m and m.group(1) in covered_ports:
            continue
        tiles.append(cand)
        known_ids.add(cand["id"])
        added.append(cand["id"])
    return added


def retag() -> int:
    """--retag: no discovery, one locked read-modify-write."""
    def mutate(tiles):
        changed = retag_tiles(tiles)
        return changed, len(tiles)

    path, (changed, total) = update_tiles(mutate, create_missing=False)
    for tid in changed:
        print(f"~ {tid}")
    print(f"{len(changed)} retagged, {total} total -> {path}")
    return 0


def merge(discovered: list[dict]) -> int:
    """--merge: add only new ids; a missing config is created."""
    def mutate(tiles):
        return merge_tiles(tiles, discovered), len(tiles)

    path, (added, total) = update_tiles(mutate)
    for tid in added:
        print(f"+ {tid}")
    print(f"{len(added)} added, {total} total -> {path}")
    return 0


def sync(discovered: list[dict]) -> int:
    """--sync: merge new ids, then retag the pre-existing tiles, all inside
    ONE locked read-modify-write (new tiles already carry their auto tags)."""
    def mutate(tiles):
        existing = len(tiles)
        added = merge_tiles(tiles, discovered)
        return added, retag_tiles(tiles[:existing]), len(tiles)

    path, (added, changed, total) = update_tiles(mutate)
    for tid in added:
        print(f"+ {tid}")
    for tid in changed:
        print(f"~ {tid}")
    print(f"{len(added)} added, {len(changed)} retagged, {total} total -> {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discover.py", allow_abbrev=False,
        description="Discover ecosystem tiles; prints JSON unless a writer mode is given.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--merge", action="store_true",
                       help="add only NEW tile ids to the tiles config")
    modes.add_argument("--retag", action="store_true",
                       help="add missing auto tags / rename legacy tags (no discovery)")
    modes.add_argument("--sync", action="store_true",
                       help="discover once, then merge new ids and retag in one locked write")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.retag:
            return retag()
        discovered = discover_all()
        if args.merge:
            return merge(discovered)
        if args.sync:
            return sync(discovered)
    except UpdateError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"tiles": discovered}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
