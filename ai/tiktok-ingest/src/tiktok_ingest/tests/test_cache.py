"""Cache keying: content hash + stage fingerprint."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tiktok_ingest.tests import fixtures
from tiktok_ingest.resume import build_fingerprint
from tiktok_ingest.state import CacheStore, StateError, stage_cache_key


class CacheKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fingerprint = fixtures.sample_fingerprints()["vision"]

    def test_key_is_deterministic(self) -> None:
        first = stage_cache_key(fixtures.MEDIA_SHA256, self.fingerprint)
        second = stage_cache_key(fixtures.MEDIA_SHA256, self.fingerprint)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_key_depends_on_content_hash(self) -> None:
        key_a = stage_cache_key(fixtures.MEDIA_SHA256, self.fingerprint)
        key_b = stage_cache_key(fixtures.content_hash("other-media"), self.fingerprint)
        self.assertNotEqual(key_a, key_b)

    def test_key_depends_on_stage_fingerprint_versions(self) -> None:
        changed = dict(self.fingerprint)
        changed["versions"] = {**self.fingerprint["versions"], "vision_model": "qwen3.8:27b"}
        self.assertNotEqual(
            stage_cache_key(fixtures.MEDIA_SHA256, self.fingerprint),
            stage_cache_key(fixtures.MEDIA_SHA256, changed),
        )

    def test_key_depends_on_input_hash(self) -> None:
        changed = {**self.fingerprint, "input_sha256": fixtures.content_hash("edited")}
        self.assertNotEqual(
            stage_cache_key(fixtures.MEDIA_SHA256, self.fingerprint),
            stage_cache_key(fixtures.MEDIA_SHA256, changed),
        )

    def test_key_is_insensitive_to_dict_order(self) -> None:
        reordered = {
            "versions": dict(reversed(list(self.fingerprint["versions"].items()))),
            "input_sha256": self.fingerprint["input_sha256"],
            "stage": self.fingerprint["stage"],
        }
        self.assertEqual(
            stage_cache_key(fixtures.MEDIA_SHA256, self.fingerprint),
            stage_cache_key(fixtures.MEDIA_SHA256, reordered),
        )


class CacheStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = fixtures.make_state_root(self._tmp.name)
        self.store = CacheStore(self.state)

    def test_put_has_get_round_trip(self) -> None:
        key = stage_cache_key(fixtures.MEDIA_SHA256, fixtures.sample_fingerprints()["prepare"])
        self.assertFalse(self.store.has(key))
        path = self.store.put_bytes(key, b"prepared-frames", suffix="bin")
        self.assertTrue(self.store.has(key, suffix="bin"))
        self.assertEqual(self.store.get_bytes(key, suffix="bin"), b"prepared-frames")
        self.assertTrue(path.is_relative_to(self.state.cache_dir))

    def test_missing_key_raises_state_error(self) -> None:
        with self.assertRaises(StateError):
            self.store.get_bytes(fixtures.content_hash("never-written"))

    def test_invalid_key_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            self.store.path_for("../../escape")

    def test_fingerprint_change_targets_a_different_blob(self) -> None:
        base_fp = fixtures.sample_fingerprints()["audio"]
        key_a = stage_cache_key(fixtures.MEDIA_SHA256, base_fp)
        changed_fp = build_fingerprint(
            "audio",
            base_fp["input_sha256"],
            {**base_fp["versions"], "whisper_model": "large-v2"},
        )
        key_b = stage_cache_key(fixtures.MEDIA_SHA256, changed_fp)
        self.store.put_bytes(key_a, b"large-v3-transcript")
        self.store.put_bytes(key_b, b"large-v2-transcript")
        self.assertEqual(self.store.get_bytes(key_a), b"large-v3-transcript")
        self.assertEqual(self.store.get_bytes(key_b), b"large-v2-transcript")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
