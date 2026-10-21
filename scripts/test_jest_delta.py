#!/usr/bin/env python3
"""
Unit and integration tests for jest-delta CLI.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

JEST_DELTA_BIN = pathlib.Path(__file__).parent / "jest-delta"

MOCK_JEST_OUTPUT_BASE = """
FAIL srv/OperationalCenters/HiringRequest/HiringRequestRepository.test.ts
  ● HiringRequestRepository › should save entity

    expect(received).toBe(expected)

    Expected: 200
    Received: 500

      at Object.<anonymous> (HiringRequestRepository.test.ts:42:5)

  ● HiringRequestRepository › should handle attachment timeout

    TimeoutError: Timed out waiting for response

      at Object.<anonymous> (HiringRequestRepository.test.ts:88:5)

Test Suites: 1 failed, 1 total
Tests:       2 failed, 10 passed, 12 total
Snapshots:   0 total
Time:        3.456 s
"""

MOCK_JEST_OUTPUT_IDENTICAL = MOCK_JEST_OUTPUT_BASE

MOCK_JEST_OUTPUT_REGRESSION = """
FAIL srv/OperationalCenters/HiringRequest/HiringRequestRepository.test.ts
  ● HiringRequestRepository › should save entity

    expect(received).toBe(expected)

    Expected: 200
    Received: 500

  ● HiringRequestRepository › should handle attachment timeout

    TimeoutError: Timed out waiting for response

  ● HiringRequestRepository › NEW REGRESSION: should validate input schema

    ValidationError: invalid schema parameter

      at Object.<anonymous> (HiringRequestRepository.test.ts:150:5)

Test Suites: 1 failed, 1 total
Tests:       3 failed, 9 passed, 12 total
"""

MOCK_JEST_OUTPUT_FIXED = """
FAIL srv/OperationalCenters/HiringRequest/HiringRequestRepository.test.ts
  ● HiringRequestRepository › should save entity

    expect(received).toBe(expected)

    Expected: 200
    Received: 500

Test Suites: 1 failed, 1 total
Tests:       1 failed, 11 passed, 12 total
"""


class TestJestDelta(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="jest-delta-test-")
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_parse_and_diff_identical(self):
        """When outputs are identical, report 0 new failures and exit 0."""
        base_log = os.path.join(self.temp_dir, "base.log")
        with open(base_log, "w", encoding="utf-8") as f:
            f.write(MOCK_JEST_OUTPUT_BASE)

        # Run jest-delta using --stdin with base log
        proc = subprocess.run(
            [str(JEST_DELTA_BIN), "--base-log", base_log, "--stdin"],
            input=MOCK_JEST_OUTPUT_IDENTICAL,
            capture_output=True,
            text=True
        )

        self.assertEqual(proc.returncode, 0, f"Expected exit 0, got {proc.returncode}: {proc.stderr}")
        self.assertIn("✓ IDENTICAL / NO REGRESSIONS: 2 pre-existing failures, 0 new failures.", proc.stdout)

    def test_parse_and_diff_regression(self):
        """When a new test fails, report regression and exit 1."""
        base_log = os.path.join(self.temp_dir, "base.log")
        with open(base_log, "w", encoding="utf-8") as f:
            f.write(MOCK_JEST_OUTPUT_BASE)

        proc = subprocess.run(
            [str(JEST_DELTA_BIN), "--base-log", base_log, "--stdin"],
            input=MOCK_JEST_OUTPUT_REGRESSION,
            capture_output=True,
            text=True
        )

        self.assertEqual(proc.returncode, 1, f"Expected exit 1, got {proc.returncode}")
        self.assertIn("✗ REGRESSION DETECTED: 1 new failure(s) found! (2 pre-existing failures)", proc.stdout)
        self.assertIn("● HiringRequestRepository › NEW REGRESSION: should validate input schema", proc.stdout)
        # Pre-existing failures should NOT be listed in the new failures section
        self.assertNotIn("● HiringRequestRepository › should save entity", proc.stdout)

    def test_parse_and_diff_fixed(self):
        """When pre-existing failures are resolved, exit 0 and report improvement."""
        base_log = os.path.join(self.temp_dir, "base.log")
        with open(base_log, "w", encoding="utf-8") as f:
            f.write(MOCK_JEST_OUTPUT_BASE)

        proc = subprocess.run(
            [str(JEST_DELTA_BIN), "--base-log", base_log, "--stdin"],
            input=MOCK_JEST_OUTPUT_FIXED,
            capture_output=True,
            text=True
        )

        self.assertEqual(proc.returncode, 0)
        self.assertIn("✓ IDENTICAL / NO REGRESSIONS: 1 pre-existing failures, 0 new failures.", proc.stdout)
        self.assertIn("1 pre-existing failure(s) were FIXED!", proc.stdout)

    def test_json_output_pass(self):
        """--json flag should output structured JSON with PASS status."""
        base_log = os.path.join(self.temp_dir, "base.log")
        with open(base_log, "w", encoding="utf-8") as f:
            f.write(MOCK_JEST_OUTPUT_BASE)

        proc = subprocess.run(
            [str(JEST_DELTA_BIN), "--json", "--base-log", base_log, "--stdin"],
            input=MOCK_JEST_OUTPUT_IDENTICAL,
            capture_output=True,
            text=True
        )

        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "PASS")
        self.assertFalse(data["regression"])
        self.assertEqual(data["new_failures_count"], 0)
        self.assertEqual(data["pre_existing_failures_count"], 2)
        self.assertEqual(len(data["new_failures"]), 0)

    def test_json_output_regression(self):
        """--json flag should output structured JSON with FAIL status and new failures list."""
        base_log = os.path.join(self.temp_dir, "base.log")
        with open(base_log, "w", encoding="utf-8") as f:
            f.write(MOCK_JEST_OUTPUT_BASE)

        proc = subprocess.run(
            [str(JEST_DELTA_BIN), "--json", "--base-log", base_log, "--stdin"],
            input=MOCK_JEST_OUTPUT_REGRESSION,
            capture_output=True,
            text=True
        )

        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "FAIL")
        self.assertTrue(data["regression"])
        self.assertEqual(data["new_failures_count"], 1)
        self.assertEqual(data["new_failures"], ["HiringRequestRepository › NEW REGRESSION: should validate input schema"])
        self.assertTrue(len(data["new_failures_details"]) == 1)
        self.assertEqual(data["new_failures_details"][0]["name"], "HiringRequestRepository › NEW REGRESSION: should validate input schema")
        self.assertIn("ValidationError", data["new_failures_details"][0]["snippet"])

    def test_ansi_color_stripping(self):
        """ANSI escape sequences should be cleanly stripped."""
        # Simulated ANSI colored Jest output
        ansi_output = "\x1b[31m  ● \x1b[39m\x1b[1mSuite › colored test failure\x1b[22m\n\n    Error\n"
        base_log = os.path.join(self.temp_dir, "base.log")
        with open(base_log, "w", encoding="utf-8") as f:
            f.write(ansi_output)

        proc = subprocess.run(
            [str(JEST_DELTA_BIN), "--json", "--base-log", base_log, "--stdin"],
            input=ansi_output,
            capture_output=True,
            text=True
        )

        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["pre_existing_failures_count"], 1)
        self.assertEqual(data["pre_existing_failures"], ["Suite › colored test failure"])

    def test_git_repo_caching_and_worktree(self):
        """Test baseline caching and ephemeral worktree inside a real Git repo."""
        repo_dir = os.path.join(self.temp_dir, "mock-repo")
        os.makedirs(repo_dir)

        # Initialize git repo
        subprocess.check_call(["git", "init"], cwd=repo_dir)
        subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo_dir)
        subprocess.check_call(["git", "config", "user.name", "Test Runner"], cwd=repo_dir)

        # Create mock test runner script
        mock_runner = os.path.join(repo_dir, "run-mock-jest.sh")
        with open(mock_runner, "w") as f:
            f.write("""#!/bin/sh
cat test_results.txt 2>/dev/null || exit 0
""")
        os.chmod(mock_runner, 0o755)

        # Initial commit with 2 failures
        test_results = os.path.join(repo_dir, "test_results.txt")
        with open(test_results, "w") as f:
            f.write(MOCK_JEST_OUTPUT_BASE)

        subprocess.check_call(["git", "add", "."], cwd=repo_dir)
        subprocess.check_call(["git", "commit", "-m", "Initial commit with pre-existing failures"], cwd=repo_dir)

        # Step 1: Run jest-delta when repo is clean.
        # Should populate the cache and report 0 new failures.
        proc1 = subprocess.run(
            [str(JEST_DELTA_BIN), "./run-mock-jest.sh"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc1.returncode, 0)
        self.assertIn("✓ IDENTICAL / NO REGRESSIONS: 2 pre-existing failures, 0 new failures.", proc1.stdout)

        # Check that cache was created in .git/jest-delta-cache
        cache_files = list(pathlib.Path(repo_dir, ".git", "jest-delta-cache").glob("*.json"))
        self.assertEqual(len(cache_files), 1)

        # Step 2: Make dirty changes introducing a regression
        with open(test_results, "w") as f:
            f.write(MOCK_JEST_OUTPUT_REGRESSION)

        # Run jest-delta again. Should detect cache hit for HEAD and catch regression.
        proc2 = subprocess.run(
            [str(JEST_DELTA_BIN), "./run-mock-jest.sh"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc2.returncode, 1)
        self.assertIn("✗ REGRESSION DETECTED: 1 new failure(s) found! (2 pre-existing failures)", proc2.stdout)
        self.assertIn("● HiringRequestRepository › NEW REGRESSION: should validate input schema", proc2.stdout)

        # Step 3: Test --clear-cache
        proc_clear = subprocess.run(
            [str(JEST_DELTA_BIN), "--clear-cache"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc_clear.returncode, 0)
        self.assertIn("✓ Cleared 1 cached baseline(s).", proc_clear.stdout)
        cache_files_after = list(pathlib.Path(repo_dir, ".git", "jest-delta-cache").glob("*.json"))
        self.assertEqual(len(cache_files_after), 0)

        # Step 4: With dirty working tree and NO cache, running jest-delta will
        # automatically create ephemeral worktree to evaluate HEAD baseline!
        proc3 = subprocess.run(
            [str(JEST_DELTA_BIN), "./run-mock-jest.sh"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc3.returncode, 1)
        self.assertIn("✗ REGRESSION DETECTED: 1 new failure(s) found! (2 pre-existing failures)", proc3.stdout)
        self.assertIn("● HiringRequestRepository › NEW REGRESSION: should validate input schema", proc3.stdout)

        # And verify baseline was re-cached
        cache_files_recreated = list(pathlib.Path(repo_dir, ".git", "jest-delta-cache").glob("*.json"))
        self.assertEqual(len(cache_files_recreated), 1)


if __name__ == "__main__":
    unittest.main()
