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

Stdlib only; read/write apart from the explicit merge target.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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
        tiles.append(
            {
                "id": f.stem,
                "name": f.stem.replace("-", " ").replace("_", " ").title(),
                "kind": "script",
                "description": desc or None,
                "command": command,
                "location": tilde(f),
            }
        )
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
        tiles.append(
            {
                "id": d.name,
                "name": d.name.replace("-", " ").replace("_", " ").title(),
                "kind": "script",
                "description": desc or None,
                "docs": tilde(readme),
                "location": tilde(d),
            }
        )
    return tiles


def discover_projects() -> list[dict]:
    """Personal project repos (default root ~/Code/personal, override via
    LAUNCHPAD_PROJECT_ROOTS, colon-separated). Worktrees are skipped."""
    roots = os.environ.get("LAUNCHPAD_PROJECT_ROOTS") or str(HOME / "Code" / "personal")
    tiles = []
    for root in roots.split(":"):
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name.endswith("-worktrees") or d.name.startswith("."):
                continue
            readme = d / "README.md"
            desc = readme_first_body_line(readme) if readme.exists() else ""
            tiles.append(
                {
                    "id": d.name,
                    "name": d.name.replace("-", " ").replace("_", " ").title(),
                    "kind": "script",
                    "description": desc or None,
                    "docs": tilde(readme) if readme.exists() else None,
                    "location": tilde(d),
                }
            )
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
        tiles.append(
            {
                "id": f"port-{port}",
                "name": title or f"Port {port}",
                "kind": "web",
                "description": None,
                "url": f"http://127.0.0.1:{port}",
            }
        )
    return tiles


def discover_all() -> list[dict]:
    aliases = alias_map()
    return (
        discover_scripts(aliases)
        + discover_subsystems()
        + discover_projects()
        + discover_ports()
    )


def merge(discovered: list[dict]) -> int:
    target = os.environ.get("LAUNCHPAD_TILES") or str(
        Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
        / "launchpad" / "tiles.json"
    )
    path = Path(target)
    existing: dict = {"tiles": []}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"error: cannot parse {target}: {e}", file=sys.stderr)
            return 1
    tiles: list[dict] = existing.get("tiles", [])
    known_ids = {t.get("id") for t in tiles}
    covered_ports = {
        m.group(1) for t in tiles if t.get("url")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tiles": tiles}, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    for tid in added:
        print(f"+ {tid}")
    print(f"{len(added)} added, {len(tiles)} total -> {target}")
    return 0


def main() -> int:
    discovered = discover_all()
    if "--merge" in sys.argv[1:]:
        return merge(discovered)
    print(json.dumps({"tiles": discovered}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
