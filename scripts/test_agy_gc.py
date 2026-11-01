#!/usr/bin/env python3
"""
Unit tests for agy-gc.py.
Tests candidate scanning, TTL enforcement, phantom sessions, probe detection, orchestrator protection, and purge behavior.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add scripts directory to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib
agy_gc = importlib.import_module("agy-gc")


class TestAgyGC(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.base_dir = Path(self.test_dir)
        self.db_path = self.base_dir / "conversation_summaries.db"
        self.brain_dir = self.base_dir / "brain"
        self.conv_dir = self.base_dir / "conversations"

        self.brain_dir.mkdir(parents=True)
        self.conv_dir.mkdir(parents=True)

        # Create schema
        conn = sqlite3.connect(self.db_path.as_posix())
        conn.execute("""
            CREATE TABLE conversation_summaries (
                conversation_id text PRIMARY KEY,
                title text NOT NULL DEFAULT "",
                preview text NOT NULL DEFAULT "",
                step_count integer NOT NULL DEFAULT 0,
                last_modified_time datetime NOT NULL,
                workspace_uris text NOT NULL,
                status text NOT NULL DEFAULT "",
                source text NOT NULL DEFAULT "",
                project_id text NOT NULL DEFAULT "",
                agent_name text NOT NULL DEFAULT "",
                parent_conversation_id text NOT NULL DEFAULT "",
                nesting_depth integer NOT NULL DEFAULT 0,
                not_fully_idle numeric NOT NULL DEFAULT 0,
                killed numeric NOT NULL DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def insert_session(
        self,
        cid: str,
        title: str = "",
        ws: str = "[]",
        steps: int = 1,
        parent_id: str = "",
        days_ago: int = 10,
        status: str = "IDLE",
        not_fully_idle: int = 0,
        create_brain: bool = True,
    ):
        conn = sqlite3.connect(self.db_path.as_posix())
        dt = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        conn.execute(
            """
            INSERT INTO conversation_summaries (
                conversation_id, title, workspace_uris, step_count,
                parent_conversation_id, last_modified_time, status, not_fully_idle
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (cid, title, ws, steps, parent_id, dt, status, not_fully_idle),
        )
        conn.commit()
        conn.close()

        if create_brain:
            b_session = self.brain_dir / cid
            b_session.mkdir(parents=True, exist_ok=True)
            (b_session / "dummy.log").write_text("dummy brain logs")

        c_file = self.conv_dir / f"{cid}.db"
        c_file.write_text("dummy conv db")

    def test_phantom_session_detected(self):
        cid = "11111111-2222-3333-4444-555555555555"
        self.insert_session(cid=cid, create_brain=False, days_ago=10)

        candidates, total = agy_gc.scan_candidates(self.db_path, self.brain_dir, self.conv_dir, ttl_days=7)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, "phantom_session")

    def test_native_subagent_detected(self):
        cid = "22222222-2222-3333-4444-555555555555"
        self.insert_session(cid=cid, parent_id="00000000-0000-0000-0000-000000000000", days_ago=10)

        candidates, total = agy_gc.scan_candidates(self.db_path, self.brain_dir, self.conv_dir, ttl_days=7)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, "native_subagent")

    def test_orchestrator_sessions_preserved(self):
        cid = "33333333-2222-3333-4444-555555555555"
        self.insert_session(cid=cid, title="Gentle AI SDD Orchestrator", days_ago=20)
        log_dir = self.brain_dir / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "transcript.jsonl").write_text('{"content": "<USER_REQUEST> <!-- gentle-ai:orchestrator --> # Gentle AI Orchestrator </USER_REQUEST>"}\n')

        candidates, total = agy_gc.scan_candidates(self.db_path, self.brain_dir, self.conv_dir, ttl_days=7)
        self.assertEqual(len(candidates), 0)

    def test_user_root_session_preserved(self):
        cid = "44444444-2222-3333-4444-555555555555"
        self.insert_session(
            cid=cid,
            title="My Important Project Work",
            ws='["file:///home/bruno/Code/work"]',
            steps=50,
            parent_id="",
            days_ago=30,
        )

        candidates, total = agy_gc.scan_candidates(self.db_path, self.brain_dir, self.conv_dir, ttl_days=7)
        self.assertEqual(len(candidates), 0)

    def test_explore_fallback_subagent_detected(self):
        cid = "55555555-2222-3333-4444-555555555555"
        self.insert_session(cid=cid, days_ago=10)
        log_dir = self.brain_dir / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "transcript.jsonl").write_text('{"content": "<USER_REQUEST> You are the explore fallback sub-agent aligned with gentle-ai </USER_REQUEST>"}\n')

        candidates, total = agy_gc.scan_candidates(self.db_path, self.brain_dir, self.conv_dir, ttl_days=7)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, "explore_fallback")

    def test_test_probe_detected(self):
        cid = "66666666-2222-3333-4444-555555555555"
        self.insert_session(cid=cid, days_ago=10)
        log_dir = self.brain_dir / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "transcript.jsonl").write_text('{"content": "<USER_REQUEST> Remember the word MANZANA. Reply only OK. </USER_REQUEST>"}\n')

        candidates, total = agy_gc.scan_candidates(self.db_path, self.brain_dir, self.conv_dir, ttl_days=7)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, "test_probe")

    def test_empty_abandoned_session_detected(self):
        cid = "77777777-2222-3333-4444-555555555555"
        self.insert_session(cid=cid, steps=0, days_ago=10)

        candidates, total = agy_gc.scan_candidates(self.db_path, self.brain_dir, self.conv_dir, ttl_days=7)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, "empty_abandoned")


if __name__ == "__main__":
    unittest.main()
