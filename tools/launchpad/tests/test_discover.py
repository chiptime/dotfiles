"""Tests for tools/launchpad/scripts/discover.py and its timer plumbing.

Isolation: every test runs with HOME, XDG_STATE_HOME, XDG_CONFIG_HOME,
LAUNCHPAD_TILES and LAUNCHPAD_PROJECT_ROOTS pointing into a temp dir outside
the workspace. Discovery is always replaced by a fixture, so no live port
probe (ss/curl) ever runs. No test touches git history.

Run: python3 -m unittest discover -s tools/launchpad/tests -p 'test_discover.py' -v
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

LAUNCHPAD = Path(__file__).resolve().parents[1]
DISCOVER_PATH = LAUNCHPAD / "scripts" / "discover.py"
DEPLOY_SH = LAUNCHPAD / "scripts" / "deploy.sh"
INSTALL_SH = LAUNCHPAD / "scripts" / "install-discovery.sh"
SERVICE = LAUNCHPAD / "systemd" / "launchpad-discover.service"
TIMER = LAUNCHPAD / "systemd" / "launchpad-discover.timer"


def load_module():
    spec = importlib.util.spec_from_file_location("launchpad_discover_ut", DISCOVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


discover = load_module()

# Subprocess driver: loads discover.py by path, replaces discovery with a JSON
# fixture, forbids real subprocesses (live probes) and records side effects in
# files the test can read. Works with the pre-change script as well, so the
# same harness can compare old and new CLI behavior.
CLI_DRIVER = r'''
import importlib.util, json, os, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("discover_under_test", os.environ["DISCOVER_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def forbidden(*a, **k):
    raise AssertionError("live subprocess probe attempted")
mod.subprocess.run = forbidden

def fake_all():
    with open(os.environ["CALLS_FILE"], "a") as fh:
        fh.write("x")
    with open(os.environ["FAKE_DISCOVERED"]) as fh:
        return json.load(fh)
mod.discover_all = fake_all

marker = os.environ.get("BLOCK_MARKER")
if marker:
    real_flock = mod.fcntl.flock
    def spy_flock(fd, op):
        try:
            return real_flock(fd, op)
        except BlockingIOError:
            with open(marker, "a") as fh:
                fh.write("blocked")
            raise
    mod.fcntl.flock = spy_flock

sys.argv = ["discover.py"] + sys.argv[1:]
sys.exit(mod.main())
'''

# Subprocess driver that holds the cooperative lock while mutating: signals
# READY, waits for GO, then adds a tile in the same locked read-modify-write.
HOLD_DRIVER = r'''
import importlib.util, os, sys, time
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("discover_under_test", os.environ["DISCOVER_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def mutate(tiles):
    open(os.environ["HOLD_READY"], "w").close()
    deadline = time.monotonic() + 30
    while not os.path.exists(os.environ["HOLD_GO"]):
        if time.monotonic() > deadline:
            raise SystemExit("never released")
        time.sleep(0.01)
    tiles.append({"id": "from-a", "kind": "script"})

mod.update_tiles(mutate)
'''

LOCK_HOLDER = r'''
import importlib.util, os, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("discover_under_test", os.environ["DISCOVER_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with mod.tiles_lock():
    print("LOCKED", flush=True)
    sys.stdin.readline()
'''


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def wait_for(path: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not Path(path).exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


EXISTING = {
    "version": 1,
    "tiles": [
        {
            "id": "alpha", "kind": "script", "name": "Alpha",
            "description": "Teams helper", "tags": ["llm", "manual-x"],
            "location": "~/Code/personal/alpha", "custom": {"keep": True},
        },
        {"id": "beta", "kind": "web", "name": "Beta", "url": "http://127.0.0.1:47000"},
    ],
    "note": "top-level extra",
}

DISCOVERED = [
    {"id": "gamma", "kind": "script", "name": "Gamma", "description": None,
     "command": "gamma", "location": "~/.dotfiles/scripts/gamma.sh"},
    {"id": "alpha", "kind": "script", "name": "Alpha Duplicate"},
    {"id": "port-47000", "kind": "web", "name": "Covered", "url": "http://127.0.0.1:47000"},
    {"id": "port-47111", "kind": "web", "name": "New", "url": "http://127.0.0.1:47111"},
]


class IsolatedEnv(unittest.TestCase):
    """Temp HOME/state/config/tiles outside the workspace for every test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="launchpad-ut-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.assertFalse(str(self.tmp).startswith(str(LAUNCHPAD.parents[1])))
        self.home = self.tmp / "home"
        self.state = self.tmp / "state"
        self.cfg = self.tmp / "cfg"
        self.roots = self.tmp / "home" / "Code" / "personal"
        for d in (self.home, self.state, self.cfg, self.roots):
            d.mkdir(parents=True, exist_ok=True)
        self.tiles = self.cfg / "tiles.json"
        self.env = {
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.tmp / "xdgcfg"),
            "LAUNCHPAD_TILES": str(self.tiles),
            "LAUNCHPAD_PROJECT_ROOTS": str(self.roots),
        }
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("HOME", self.home), ("PROJECT_ROOTS", str(self.roots))):
            p = mock.patch.object(discover, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.fixture = self.tmp / "discovered.json"
        self.fixture.write_text(json.dumps(DISCOVERED))
        self.calls = self.tmp / "calls"

    def write_tiles(self, data=None, raw: bytes | None = None) -> Path:
        if raw is not None:
            self.tiles.write_bytes(raw)
        else:
            self.tiles.write_text(canonical(data if data is not None else EXISTING),
                                  encoding="utf-8")
        return self.tiles

    def run_main(self, argv):
        """In-process main() with fixture discovery; returns (rc, out, err, calls)."""
        calls = []

        def fake_all():
            calls.append(1)
            return copy.deepcopy(DISCOVERED)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(discover, "discover_all", fake_all), \
                mock.patch.object(discover.subprocess, "run",
                                  side_effect=AssertionError("live probe")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = discover.main(argv)
            except SystemExit as e:  # argparse rejection
                rc = e.code
        return rc, out.getvalue(), err.getvalue(), len(calls)

    def run_cli(self, argv, extra_env=None, script=None, driver=CLI_DRIVER, timeout=60):
        env = dict(os.environ)
        env.update(self.env)
        env.update({"DISCOVER_PATH": str(script or DISCOVER_PATH),
                    "FAKE_DISCOVERED": str(self.fixture),
                    "CALLS_FILE": str(self.calls), "PYTHONDONTWRITEBYTECODE": "1"})
        env.update(extra_env or {})
        return subprocess.run([sys.executable, "-c", driver, *argv], env=env,
                              capture_output=True, text=True, timeout=timeout)


class SyncBehavior(IsolatedEnv):
    def test_sync_adds_new_ids_and_retags_in_one_pass(self):
        self.write_tiles()
        rc, out, err, calls = self.run_main(["--sync"])
        self.assertEqual((rc, err, calls), (0, "", 1))
        self.assertEqual(
            out,
            "+ gamma\n+ port-47111\n~ alpha\n"
            f"2 added, 1 retagged, 4 total -> {self.tiles}\n")
        data = json.loads(self.tiles.read_text())
        self.assertEqual(list(data), ["version", "tiles", "note"])
        self.assertEqual(data["note"], "top-level extra")
        self.assertEqual([t["id"] for t in data["tiles"]],
                         ["alpha", "beta", "gamma", "port-47111"])
        alpha = data["tiles"][0]
        self.assertEqual(alpha["kind"], "project")
        self.assertEqual(alpha["tags"], ["ia", "manual-x", "personal", "teams"])
        self.assertEqual(alpha["custom"], {"keep": True})
        self.assertEqual(list(alpha),
                         ["id", "kind", "name", "description", "tags", "location", "custom"])
        self.assertEqual(self.tiles.read_text(), canonical(data))

    def test_sync_second_run_is_noop_and_does_not_rewrite(self):
        self.write_tiles()
        self.run_main(["--sync"])
        before = self.tiles.stat()
        digest = sha(self.tiles)
        rc, out, _err, calls = self.run_main(["--sync"])
        self.assertEqual((rc, calls), (0, 1))
        self.assertIn("0 added, 0 retagged, 4 total", out)
        after = self.tiles.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(digest, sha(self.tiles))

    def test_sync_creates_missing_file_like_merge(self):
        self.assertFalse(self.tiles.exists())
        rc, out, _err, _ = self.run_main(["--sync"])
        self.assertEqual(rc, 0)
        # Port coverage is judged against the stored tiles only (legacy merge rule).
        self.assertIn("4 added, 0 retagged, 4 total", out)
        self.assertEqual([t["id"] for t in json.loads(self.tiles.read_text())["tiles"]],
                         ["gamma", "alpha", "port-47000", "port-47111"])

    def test_noop_merge_and_retag_do_not_rewrite(self):
        self.write_tiles()
        self.run_main(["--sync"])
        st = self.tiles.stat()
        for mode in ("--merge", "--retag"):
            rc, _o, _e, _c = self.run_main([mode])
            self.assertEqual(rc, 0)
            now = self.tiles.stat()
            self.assertEqual((st.st_ino, st.st_mtime_ns), (now.st_ino, now.st_mtime_ns), mode)


class LegacyModes(IsolatedEnv):
    def test_default_prints_json_and_never_writes(self):
        self.write_tiles()
        digest = sha(self.tiles)
        rc, out, err, calls = self.run_main([])
        self.assertEqual((rc, err, calls), (0, "", 1))
        self.assertEqual(out, canonical({"tiles": DISCOVERED}))
        self.assertEqual(digest, sha(self.tiles))
        self.assertFalse((self.state / "launchpad").exists())

    def test_merge_output_and_saved_json(self):
        self.write_tiles({"tiles": EXISTING["tiles"]})
        rc, out, _e, calls = self.run_main(["--merge"])
        self.assertEqual((rc, calls), (0, 1))
        self.assertEqual(out, f"+ gamma\n+ port-47111\n2 added, 4 total -> {self.tiles}\n")
        expected = {"tiles": EXISTING["tiles"] + [DISCOVERED[0], DISCOVERED[3]]}
        self.assertEqual(self.tiles.read_text(), canonical(expected))

    def test_retag_output_saved_json_and_no_discovery(self):
        self.write_tiles({"tiles": EXISTING["tiles"]})
        rc, out, _e, calls = self.run_main(["--retag"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, 0, "--retag must not run discovery or live port scans")
        self.assertEqual(out, f"~ alpha\n1 retagged, 2 total -> {self.tiles}\n")
        alpha = json.loads(self.tiles.read_text())["tiles"][0]
        self.assertEqual((alpha["kind"], alpha["tags"]),
                         ("project", ["ia", "manual-x", "personal", "teams"]))

    def test_retag_missing_file_is_error_exit_1(self):
        rc, out, err, _ = self.run_main(["--retag"])
        self.assertEqual((rc, out), (1, ""))
        self.assertTrue(err.startswith(f"error: cannot parse {self.tiles}:"))
        self.assertFalse(self.tiles.exists())

    def test_merge_creates_missing_file(self):
        rc, out, _e, _c = self.run_main(["--merge"])
        self.assertEqual(rc, 0)
        self.assertIn(f"4 added, 4 total -> {self.tiles}", out)
        self.assertTrue(self.tiles.exists())

    def test_cli_subprocess_matches_inprocess_for_retag_without_discovery(self):
        self.write_tiles({"tiles": EXISTING["tiles"]})
        r = self.run_cli(["--retag"])
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout, f"~ alpha\n1 retagged, 2 total -> {self.tiles}\n")


class Rejections(IsolatedEnv):
    BAD_PAYLOADS = {
        "invalid-json": b"{not json",
        "empty": b"",
        "invalid-utf8": b'{"tiles": ["\xff\xfe"]}',
        "top-level-list": b"[1, 2]",
        "tiles-not-list": b'{"tiles": {"a": 1}}',
        "tile-not-object": b'{"tiles": ["x"]}',
        "tags-not-list": b'{"tiles": [{"id": "a", "tags": "voz"}]}',
    }

    def test_invalid_stored_data_fails_and_keeps_exact_bytes(self):
        for name, payload in self.BAD_PAYLOADS.items():
            for mode in ("--sync", "--merge", "--retag"):
                with self.subTest(payload=name, mode=mode):
                    if name == "tags-not-list" and mode == "--merge":
                        continue  # merge never inspects tags
                    self.write_tiles(raw=payload)
                    digest = sha(self.tiles)
                    listing_before = sorted(p.name for p in self.cfg.iterdir())
                    rc, out, err, _ = self.run_main([mode])
                    self.assertEqual(rc, 1)
                    self.assertEqual(out, "")
                    self.assertTrue(err.startswith("error: cannot parse"), err)
                    self.assertEqual(digest, sha(self.tiles))
                    self.assertEqual(listing_before, sorted(p.name for p in self.cfg.iterdir()))

    def test_rejected_options_fail_nonzero_and_leave_bytes_unchanged(self):
        self.write_tiles()
        digest = sha(self.tiles)
        cases = [["--bogus"], ["--sync", "--merge"], ["--sync", "--retag"],
                 ["--merge", "--retag"], ["positional"], ["--syn"], ["--sync", "--bogus"]]
        for argv in cases:
            with self.subTest(argv=argv):
                r = self.run_cli(argv)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(r.stdout, "")
                self.assertNotEqual(r.stderr, "")
                self.assertEqual(digest, sha(self.tiles))
        self.assertFalse(self.calls.exists(), "rejected options must not run discovery")


class AtomicWrites(IsolatedEnv):
    def test_symlink_preserved_target_replaced_and_mode_kept(self):
        real_dir = self.tmp / "repo-config"
        real_dir.mkdir()
        target = real_dir / "tiles.json"
        target.write_text(canonical(EXISTING), encoding="utf-8")
        os.chmod(target, 0o640)
        link = self.cfg / "tiles.json"
        link.symlink_to(target)
        inode_before = target.stat().st_ino
        rc, _o, err, _ = self.run_main(["--sync"])
        self.assertEqual((rc, err), (0, ""))
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(target))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertNotEqual(target.stat().st_ino, inode_before, "write must be an atomic replace")
        self.assertIn("gamma", target.read_text())
        self.assertEqual(sorted(p.name for p in real_dir.iterdir()), ["tiles.json"])

    def test_new_file_is_created_with_umask_permissions(self):
        old = os.umask(0o027)
        self.addCleanup(os.umask, old)
        self.run_main(["--merge"])
        self.assertEqual(stat.S_IMODE(self.tiles.stat().st_mode), 0o640)

    def _assert_failure_cleans_up(self, exc):
        self.write_tiles()
        digest = sha(self.tiles)
        with mock.patch.object(discover.os, "replace", side_effect=exc):
            if isinstance(exc, KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_main(["--sync"])
            else:
                rc, _o, err, _ = self.run_main(["--sync"])
                self.assertEqual(rc, 1)
                self.assertIn("error: cannot write", err)
        self.assertEqual(digest, sha(self.tiles))
        self.assertEqual(sorted(p.name for p in self.cfg.iterdir()), ["tiles.json"])

    def test_replace_failure_leaves_original_and_no_temp(self):
        self._assert_failure_cleans_up(OSError(28, "No space left on device"))

    def test_interrupt_during_replace_cleans_temp(self):
        self._assert_failure_cleans_up(KeyboardInterrupt())

    def test_write_failure_cleans_temp(self):
        self.write_tiles()
        digest = sha(self.tiles)
        with mock.patch.object(discover.os, "fsync", side_effect=OSError(5, "I/O error")):
            rc, _o, err, _ = self.run_main(["--sync"])
        self.assertEqual(rc, 1)
        self.assertIn("error: cannot write", err)
        self.assertEqual(digest, sha(self.tiles))
        self.assertEqual(sorted(p.name for p in self.cfg.iterdir()), ["tiles.json"])


class CooperativeLock(IsolatedEnv):
    def test_lock_lives_in_machine_local_state_not_repo_or_config(self):
        path = discover.lock_path()
        self.assertEqual(path, self.state / "launchpad" / "tiles.lock")
        with mock.patch.dict(os.environ):
            os.environ.pop("XDG_STATE_HOME")
            self.assertEqual(discover.lock_path(),
                             self.home / ".local" / "state" / "launchpad" / "tiles.lock")
        self.assertNotIn(str(LAUNCHPAD.parents[1]), str(path))

    def test_every_writer_mode_waits_then_times_out_when_lock_is_held(self):
        self.write_tiles()
        digest = sha(self.tiles)
        for mode in ("--sync", "--merge", "--retag"):
            with self.subTest(mode=mode):
                holder = subprocess.Popen(
                    [sys.executable, "-c", LOCK_HOLDER], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, text=True,
                    env={**os.environ, "DISCOVER_PATH": str(DISCOVER_PATH),
                         "PYTHONDONTWRITEBYTECODE": "1"})
                try:
                    self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
                    r = self.run_cli([mode], {"LAUNCHPAD_LOCK_TIMEOUT_SECS": "0.3"})
                    self.assertEqual(r.returncode, 1, r.stderr)
                    self.assertIn("error: cannot lock", r.stderr)
                    self.assertEqual(r.stdout, "")
                    self.assertEqual(digest, sha(self.tiles))
                finally:
                    holder.stdin.write("\n")
                    holder.stdin.close()
                    holder.wait(timeout=15)
                    holder.stdout.close()
        r = self.run_cli(["--retag"], {"LAUNCHPAD_LOCK_TIMEOUT_SECS": "5"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_default_mode_ignores_the_lock(self):
        with discover.tiles_lock():
            with mock.patch.dict(os.environ, {"LAUNCHPAD_LOCK_TIMEOUT_SECS": "0.2"}):
                rc, out, _e, _c = self.run_main([])
        self.assertEqual(rc, 0)
        self.assertIn('"tiles"', out)

    def test_two_processes_are_serialized_without_lost_update(self):
        self.write_tiles({"tiles": [
            {"id": "alpha", "kind": "script", "location": "~/Code/personal/alpha",
             "tags": ["llm"]}]})
        ready, go, blocked = self.tmp / "ready", self.tmp / "go", self.tmp / "blocked"
        env = {**os.environ, **self.env, "DISCOVER_PATH": str(DISCOVER_PATH),
               "HOLD_READY": str(ready), "HOLD_GO": str(go),
               "PYTHONDONTWRITEBYTECODE": "1"}
        a = subprocess.Popen([sys.executable, "-c", HOLD_DRIVER], env=env,
                             stderr=subprocess.PIPE, text=True)
        b = None
        try:
            wait_for(ready)
            b_env = {**env, "FAKE_DISCOVERED": str(self.fixture),
                     "CALLS_FILE": str(self.calls), "BLOCK_MARKER": str(blocked)}
            b = subprocess.Popen([sys.executable, "-c", CLI_DRIVER, "--retag"], env=b_env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # Deterministic: only release A after B has observed a contended lock.
            wait_for(blocked)
            self.assertIsNone(b.poll(), "B must be blocked while A holds the lock")
            go.touch()
            b_out, b_err = b.communicate(timeout=60)
            a.wait(timeout=60)
        finally:
            for p in (a, b):
                if p and p.poll() is None:
                    p.kill()
            a.wait(timeout=15)
            a.stderr.close()
        self.assertEqual((a.returncode, b.returncode), (0, 0), b_err)
        tiles = json.loads(self.tiles.read_text())["tiles"]
        self.assertEqual([t["id"] for t in tiles], ["alpha", "from-a"])
        self.assertEqual(tiles[0]["tags"], ["ia", "personal"])  # B saw A's committed file
        self.assertEqual(tiles[0]["kind"], "project")


class TimerPlumbing(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_service_contract(self):
        text = self.read(SERVICE)
        self.assertIn("Type=oneshot", text)
        self.assertRegex(text, r"(?m)^ExecStart=/usr/bin/python3 %h/\.dotfiles/tools/launchpad/scripts/discover\.py --sync$")
        self.assertRegex(text, r"(?m)^TimeoutStartSec=\d+")
        self.assertNotRegex(text, r"(?m)^\[Install\]")
        for forbidden in ("ntfy", "ssh", "morning", "hub", "http://", "https://"):
            self.assertNotIn(forbidden, text.lower())

    def test_timer_contract(self):
        text = self.read(TIMER)
        self.assertRegex(text, r"(?m)^OnStartupSec=2min$")
        self.assertRegex(text, r"(?m)^OnUnitActiveSec=30min$")
        self.assertRegex(text, r"(?m)^WantedBy=timers\.target$")
        self.assertNotIn("OnCalendar", text)

    def test_installer_is_idempotent_symlink_and_enables_timer_only(self):
        text = self.read(INSTALL_SH)
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertIn("set -euo pipefail", text)
        self.assertIn("ln -sfn", text)
        self.assertIn("daemon-reload", text)
        self.assertRegex(text, r"systemctl --user enable --now launchpad-discover\.timer")
        self.assertNotRegex(text, r"enable[^\n]*launchpad-discover\.service")
        self.assertNotRegex(text, r"\brm\b")

    def test_deploy_installs_timer_after_health_check_and_keeps_enable_fix(self):
        lines = self.read(DEPLOY_SH).splitlines()

        def index(fragment):
            return next(i for i, l in enumerate(lines) if fragment in l)

        enable = index("systemctl --user enable launchpad.service")
        health = index("/api/health")
        install = index("install-discovery.sh")
        self.assertLess(enable, health)
        self.assertLess(health, install)

    def test_shell_scripts_parse(self):
        for script in (DEPLOY_SH, INSTALL_SH):
            r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stderr), (0, ""), script)


if __name__ == "__main__":
    unittest.main()
