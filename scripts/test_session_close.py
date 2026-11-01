#!/usr/bin/env python3
"""Behavior tests for scripts/session-close.py (isolated HOME, stubbed tools).

Run: python3 -m unittest scripts.test_session_close -q   (from ~/.dotfiles)
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "session-close.py"

YAML = textwrap.dedent(
    """\
    alpha:
      scope: personal
      status: active
      desc: Old alpha description
      next: old alpha next

    beta:
      scope: work
      status: paused
      desc: Beta stays untouched
      next: resume beta
    """
)


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


class SessionCloseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.dotfiles = root / "dotfiles"
        self.code = root / "code"
        self.bin = root / "bin"
        self.calls = root / "calls.log"
        self.workdir = self.code / "alpha"
        for d in (self.home, self.dotfiles / "scripts", self.bin, self.workdir):
            d.mkdir(parents=True)

        self.yaml = self.dotfiles / "scripts" / "projects.yaml"
        self.yaml.write_text(YAML)
        (self.dotfiles / "other.txt").write_text("base\n")
        self.git(self.dotfiles, "init", "-q")
        self.git(self.dotfiles, "add", "-A")
        self.git(self.dotfiles, "commit", "-q", "-m", "init")
        # Unrelated dirty work that must never be committed by the tool.
        (self.dotfiles / "other.txt").write_text("dirty\n")

        self.snapshot = self.home / ".local/share/projects-dashboard/PROJECTS.md"
        self.stub("time-ledger", 'echo "time-ledger $*" >> "$CALLS"')
        self.stub(
            "projects",
            'mkdir -p "$(dirname "$SNAP")"; printf "## 🟢 Active (2)\\n## ⚪ Cold / legacy (1)\\n" > "$SNAP"; echo projects >> "$CALLS"',
        )
        self.stub("improve-system", 'echo "improve-system: CADENCIA VENCIDA (9 días)"')

        self.db = root / "opencode.db"
        con = sqlite3.connect(self.db)
        con.execute(
            "create table session (id text primary key, parent_id text, directory text,"
            " title text, time_created integer, time_updated integer)"
        )
        con.execute("create table message (id text primary key, session_id text, time_created integer)")
        con.execute(
            "insert into session values (?,?,?,?,?,?)",
            ("ses_OTHER", None, "/elsewhere", "other", ms("2026-10-28T08:00:00"), ms("2026-10-28T21:00:00")),
        )
        con.execute(
            "insert into session values (?,?,?,?,?,?)",
            ("ses_CHILD", "ses_MAIN", str(self.workdir), "child", ms("2026-10-28T14:10:00"), ms("2026-10-28T20:59:00")),
        )
        con.execute(
            "insert into session values (?,?,?,?,?,?)",
            ("ses_MAIN", None, str(self.workdir), "Bump dependency", ms("2026-10-28T14:00:00"), ms("2026-10-28T20:30:00")),
        )
        # 14:00 -> 14:20 (20 min active), idle gap until 20:00, then 20:00 -> 20:30.
        for i, t in enumerate(["14:00", "14:10", "14:20", "20:00", "20:30"]):
            con.execute(
                "insert into message values (?,?,?)",
                (f"m{i}", "ses_MAIN", ms(f"2026-10-28T{t}:00")),
            )
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                              capture_output=True, text=True).stdout

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)

    def run_tool(self, *args, now="2026-10-28T20:45:00"):
        env = dict(
            os.environ,
            HOME=str(self.home),
            PATH=f"{self.bin}:{os.environ['PATH']}",
            DOTFILES=str(self.dotfiles),
            CODE_ROOT=str(self.code),
            SESSION_CLOSE_DB=str(self.db),
            SESSION_CLOSE_NOW=now,
            CALLS=str(self.calls),
            SNAP=str(self.snapshot),
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
        )
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.workdir,
                              env=env, capture_output=True, text=True)

    def calls_text(self):
        return self.calls.read_text() if self.calls.exists() else ""

    def head_files(self):
        return self.git(self.dotfiles, "show", "--name-only", "--format=%s", "HEAD").split()

    # --- rules ---------------------------------------------------------------

    def test_updates_only_the_named_block(self):
        r = self.run_tool("alpha", "--next", "ship it", "--desc", "New alpha")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.yaml.read_text()
        self.assertIn("alpha:\n  scope: personal\n  status: active\n  desc: New alpha\n  next: ship it\n", text)
        self.assertIn("beta:\n  scope: work\n  status: paused\n  desc: Beta stays untouched\n  next: resume beta\n", text)
        self.assertIn("- next: old alpha next", r.stdout)
        self.assertIn("+ next: ship it", r.stdout)

    def test_adds_missing_key_and_status_change(self):
        self.yaml.write_text(YAML.replace("  next: old alpha next\n", ""))
        r = self.run_tool("alpha", "--next", "new step", "--status", "paused")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alpha:\n  scope: personal\n  status: paused\n  desc: Old alpha description\n  next: new step\n",
                      self.yaml.read_text())

    def test_detects_session_and_active_hours_for_the_ledger(self):
        r = self.run_tool("alpha", "--next", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 20 + 30 active minutes = 50 min -> rounded up to 1.0 h; idle gap excluded.
        self.assertIn("time-ledger add-ai 28/10/2026 alpha 1.0 Bump dependency ses_MAIN", self.calls_text())
        self.assertIn("ses_MAIN", r.stdout)

    def test_explicit_hours_note_and_session_override_detection(self):
        r = self.run_tool("alpha", "--next", "x", "--hours", "0.5", "--note", "manual note", "--session", "ses_GIVEN")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("time-ledger add-ai 28/10/2026 alpha 0.5 manual note ses_GIVEN", self.calls_text())

    def test_regenerates_snapshot_and_reports_counts_and_cadence(self):
        r = self.run_tool("alpha", "--next", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("projects", self.calls_text())
        self.assertIn("Active (2)", r.stdout)
        self.assertIn("Cold / legacy (1)", r.stdout)
        self.assertIn("CADENCIA VENCIDA", r.stdout)

    def test_commits_only_the_tracker_outside_working_hours(self):
        r = self.run_tool("alpha", "--next", "x", now="2026-10-28T20:45:00")  # Wednesday evening
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head_files(), ["chore(projects):", "update", "alpha", "tracker", "scripts/projects.yaml"])
        self.assertEqual((self.dotfiles / "other.txt").read_text(), "dirty\n")
        self.assertIn("other.txt", self.git(self.dotfiles, "status", "--short"))

    def test_skips_commit_inside_working_hours(self):
        r = self.run_tool("alpha", "--next", "x", now="2026-10-30T10:00:00")  # Friday 10:00
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.git(self.dotfiles, "log", "--format=%s"), "init\n")
        self.assertIn("commit omitido", r.stdout)
        self.assertIn("next: x", self.yaml.read_text())

    def test_friday_after_16_and_weekend_are_outside_working_hours(self):
        for now in ("2026-10-30T16:00:00", "2026-10-31T11:00:00"):
            with self.subTest(now=now):
                r = self.run_tool("alpha", "--next", f"step {now}", now=now)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("scripts/projects.yaml", self.head_files())

    def test_no_commit_flag_never_commits(self):
        r = self.run_tool("alpha", "--next", "x", "--no-commit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.git(self.dotfiles, "log", "--format=%s"), "init\n")

    def test_dry_run_writes_nothing(self):
        r = self.run_tool("alpha", "--next", "x", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.yaml.read_text(), YAML)
        self.assertEqual(self.calls_text(), "")
        self.assertEqual(self.git(self.dotfiles, "log", "--format=%s"), "init\n")
        self.assertIn("+ next: x", r.stdout)

    def test_rejects_invalid_status_without_writing(self):
        r = self.run_tool("alpha", "--status", "done")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("status must be one of", r.stderr)
        self.assertEqual(self.yaml.read_text(), YAML)
        self.assertEqual(self.calls_text(), "")

    def test_rejects_multiline_value_without_writing(self):
        r = self.run_tool("alpha", "--next", "a\nb")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("single line", r.stderr)
        self.assertEqual(self.yaml.read_text(), YAML)

    def test_unknown_project_requires_desc_and_creates_block(self):
        r = self.run_tool("gamma", "--next", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--desc", r.stderr)
        self.assertEqual(self.yaml.read_text(), YAML)
        r = self.run_tool("gamma", "--desc", "Gamma tool", "--next", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.yaml.read_text().endswith(
            "\ngamma:\n  scope: personal\n  status: active\n  desc: Gamma tool\n  next: x\n"))

    def test_without_session_requires_hours(self):
        self.db.unlink()
        r = self.run_tool("alpha", "--next", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--hours", r.stderr)
        self.assertEqual(self.yaml.read_text(), YAML)

    def test_untitled_session_falls_back_to_generic_note(self):
        con = sqlite3.connect(self.db)
        con.execute("update session set title = 'New session - 2026-10-28T13:00:00.000Z' where id = 'ses_MAIN'")
        con.commit()
        con.close()
        r = self.run_tool("alpha", "--next", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("time-ledger add-ai 28/10/2026 alpha 1.0 session ses_MAIN", self.calls_text())

    def test_proposal_lists_a_worktree_sharing_commits_once(self):
        repo = self.code / "personal" / "shared"
        repo.mkdir(parents=True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t", GIT_AUTHOR_DATE="2026-10-28T15:00:00",
                   GIT_COMMITTER_DATE="2026-10-28T15:00:00")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "feat: shared"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "worktree", "add", "-q", "-b", "wt", str(self.code / "personal" / "shared-worktrees" / "wt")],
                       cwd=repo, check=True, env=env)
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("feat: shared"), 1)

    def test_without_project_only_proposes(self):
        repo = self.code / "personal" / "touched"
        repo.mkdir(parents=True)
        self.git(repo, "init", "-q")
        (repo / "f").write_text("x")
        self.git(repo, "add", "-A")
        env_date = {"GIT_AUTHOR_DATE": "2026-10-28T15:00:00", "GIT_COMMITTER_DATE": "2026-10-28T15:00:00"}
        subprocess.run(["git", "commit", "-q", "-m", "feat: touched work"], cwd=repo, check=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", **env_date))
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ses_MAIN", r.stdout)
        self.assertIn("1.0 h", r.stdout)
        self.assertIn("touched", r.stdout)
        self.assertIn("feat: touched work", r.stdout)
        self.assertIn("old alpha next", r.stdout)  # current block of the cwd project
        self.assertEqual(self.yaml.read_text(), YAML)
        self.assertEqual(self.calls_text(), "")


if __name__ == "__main__":
    unittest.main()
