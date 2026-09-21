"""Milestone-3 Ollama runtime: lifecycle, snapshots, install, import."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from tiktok_ingest import config
from tiktok_ingest.gates import GateParseError
from tiktok_ingest.ollama_runtime import (
    OllamaImportError,
    OllamaInstallError,
    OllamaRuntimeError,
    ServerHandle,
    import_model,
    install_ollama,
    parse_model_name,
    read_server_log,
    request_unload,
    resident_models,
    server_env,
    snapshot_store,
    snapshots_identical,
    start_server,
    stop_server,
    verify_pid_gone,
    wait_ready,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, pid: int = 4242, stubborn: bool = False) -> None:
        self.pid = pid
        self._stubborn = stubborn
        self._returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self._stubborn:
            self._returncode = 0

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self) -> int:
        return self._returncode if self._returncode is not None else 0


class FakeResponse:
    """Minimal urlopen()-returned body: context manager + read()."""

    def __init__(self, body: bytes) -> None:
        self._buffer = io.BytesIO(body)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


def fake_urlopen_body(body: bytes) -> FakeResponse:
    return FakeResponse(body)


def fake_urlopen_stream(chunks: list[bytes], calls: list[int]):
    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size: int = -1) -> bytes:
            return chunks.pop(0) if chunks else b""

    def urlopen(req, timeout=None):
        calls.append(1)
        return Stream()

    return urlopen


def build_archive_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members.items():
            data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class NoSleep:
    def __call__(self, seconds: float) -> None:
        return None


class TickingClock:
    def __init__(self, step: float = 10.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


# --------------------------------------------------------------------------
# server_env / start / stop
# --------------------------------------------------------------------------


class ServerEnvTests(unittest.TestCase):
    def test_overrides_home_models_and_host(self) -> None:
        env = server_env("/iso/home", "/iso/models", "127.0.0.1", 11435)
        self.assertEqual(env["HOME"], "/iso/home")
        self.assertEqual(env["OLLAMA_MODELS"], "/iso/models")
        self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:11435")


class StartStopServerTests(unittest.TestCase):
    def test_start_requires_the_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OllamaRuntimeError) as ctx:
                start_server(
                    bin_path=Path(tmp) / "missing",
                    home_dir=Path(tmp) / "home",
                    models_dir=Path(tmp) / "models",
                    log_path=Path(tmp) / "log",
                    pid_path=Path(tmp) / "ollama.pid",
                )
            self.assertIn("install-ollama", str(ctx.exception))

    def test_start_writes_pid_file_and_spawn_receives_isolated_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_path = tmp_path / "ollama"
            bin_path.write_text("#!/bin/sh\n")
            captured: dict = {}

            def spawn(argv, env, log_fd):
                captured.update(argv=argv, env=env)
                return FakeProcess(pid=999)

            handle = start_server(
                bin_path=bin_path,
                home_dir=tmp_path / "home",
                models_dir=tmp_path / "models",
                log_path=tmp_path / "server.log",
                pid_path=tmp_path / "ollama.pid",
                spawn=spawn,
            )
            self.assertEqual(captured["argv"], [str(bin_path), "serve"])
            self.assertEqual(captured["env"]["HOME"], str(tmp_path / "home"))
            self.assertEqual(captured["env"]["OLLAMA_MODELS"], str(tmp_path / "models"))
            self.assertEqual(captured["env"]["OLLAMA_HOST"], "127.0.0.1:11435")
            self.assertEqual(handle.pid_path.read_text().strip(), "999")

    def test_graceful_stop_terminates_and_removes_pid_file(self) -> None:
        process = FakeProcess()
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "ollama.pid"
            pid_path.write_text("4242\n")
            handle = ServerHandle(process=process, pid_path=pid_path)
            stop_server(handle, terminate_grace_seconds=0.01, sleep=NoSleep())
            self.assertTrue(process.terminated)
            self.assertFalse(process.killed)
            self.assertFalse(pid_path.exists())

    def test_stubborn_process_is_killed_after_bounded_wait(self) -> None:
        process = FakeProcess(stubborn=True)
        with tempfile.TemporaryDirectory() as tmp:
            handle = ServerHandle(process=process, pid_path=Path(tmp) / "pid")
            stop_server(handle, terminate_grace_seconds=0.01, sleep=NoSleep())
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)

    def test_verify_pid_gone_via_kill_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "ollama.pid"
            pid_path.write_text("4242\n")
            gone = verify_pid_gone(pid_path, runner=lambda argv, timeout=None: (1, "", "no such process"))
            self.assertTrue(gone)
            alive = verify_pid_gone(pid_path, runner=lambda argv, timeout=None: (0, "", ""))
            self.assertFalse(alive)


class WaitReadyTests(unittest.TestCase):
    def test_polls_until_version_answers(self) -> None:
        attempts: list[int] = []

        def flaky(req, timeout=None):
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError("not up yet")
            return fake_urlopen_body(b'{"version":"0.34.1"}')

        version = wait_ready(
            "http://127.0.0.1:11435",
            urlopen=flaky,
            deadline_seconds=120,
            monotonic=TickingClock(step=1.0),
            sleep=NoSleep(),
        )
        self.assertEqual(version, "0.34.1")

    def test_deadline_raises_fail_closed(self) -> None:
        def down(req, timeout=None):
            raise OSError("connection refused")

        with self.assertRaises(OllamaRuntimeError) as ctx:
            wait_ready(
                urlopen=down,
                deadline_seconds=30,
                monotonic=TickingClock(step=10.0),
                sleep=NoSleep(),
            )
        self.assertIn("failing closed", str(ctx.exception))


class ApiHelperTests(unittest.TestCase):
    def test_request_unload_sends_keep_alive_zero(self) -> None:
        captured: dict = {}

        def urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return fake_urlopen_body(b'{"done":true}')

        request_unload("qwen3.5:9b", "http://127.0.0.1:11435", urlopen=urlopen)
        self.assertEqual(captured["url"], "http://127.0.0.1:11435/api/generate")
        self.assertEqual(captured["data"], {"model": "qwen3.5:9b", "keep_alive": 0})

    def test_resident_models_parses_ps(self) -> None:
        def urlopen(req, timeout=None):
            self.assertTrue(req.full_url.endswith("/api/ps"))
            return fake_urlopen_body(b'{"models": []}')

        self.assertEqual(resident_models(urlopen=urlopen), [])

    def test_resident_models_malformed_raises(self) -> None:
        def urlopen(req, timeout=None):
            return fake_urlopen_body(b"garbage")

        with self.assertRaises(GateParseError):
            resident_models(urlopen=urlopen)


class ReadServerLogTests(unittest.TestCase):
    def test_tail_only_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = read_server_log(Path(tmp) / "absent.log")
            self.assertEqual(missing, "")
            log = Path(tmp) / "server.log"
            log.write_text("x" * 100 + "\noffloaded 29/29 layers to GPU\n")
            text = read_server_log(log, tail_bytes=64)
            self.assertIn("29/29", text)
            self.assertNotIn("x" * 50, text)


# --------------------------------------------------------------------------
# Global-store snapshot
# --------------------------------------------------------------------------


class SnapshotTests(unittest.TestCase):
    def test_identical_stores_are_bit_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            (store / "blobs").mkdir(parents=True)
            (store / "blobs" / "a").write_bytes(b"alpha")
            (store / "manifests").mkdir()
            (store / "manifests" / "m").write_bytes(b"beta")
            before = snapshot_store(store)
            after = snapshot_store(store)
            self.assertTrue(snapshots_identical(before, after))

    def test_single_byte_change_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            (store / "blobs").mkdir(parents=True)
            blob = store / "blobs" / "a"
            blob.write_bytes(b"alpha")
            before = snapshot_store(store)
            blob.write_bytes(b"alphb")
            after = snapshot_store(store)
            self.assertFalse(snapshots_identical(before, after))

    def test_added_or_removed_files_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            (store / "blobs").mkdir(parents=True)
            (store / "blobs" / "a").write_bytes(b"alpha")
            before = snapshot_store(store)
            (store / "blobs" / "b").write_bytes(b"new")
            after = snapshot_store(store)
            self.assertFalse(snapshots_identical(before, after))


# --------------------------------------------------------------------------
# install-ollama
# --------------------------------------------------------------------------


class InstallOllamaTests(unittest.TestCase):
    ARCHIVE = build_archive_bytes({"bin/ollama": "#!/bin/sh\n", "lib/ollama/x": b"y"})

    def test_install_extracts_and_deletes_the_archive(self) -> None:
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as tmp:
            ollama_dir = Path(tmp) / "ollama"
            binary = install_ollama(
                ollama_dir,
                expected_sha256=hashlib.sha256(self.ARCHIVE).hexdigest(),
                expected_size_bytes=len(self.ARCHIVE),
                urlopen=fake_urlopen_stream([self.ARCHIVE], calls),
                url="https://example.test/ollama.tgz",
                euid=1000,
            )
            self.assertTrue(binary.is_file())
            self.assertEqual(binary.read_text(), "#!/bin/sh\n")
            self.assertEqual((ollama_dir / "lib" / "ollama" / "x").read_bytes(), b"y")
            leftovers = [p.name for p in ollama_dir.iterdir() if p.name.startswith(".archive-")]
            self.assertEqual(leftovers, [], "the archive must be deleted after install")
            self.assertEqual(len(calls), 1, "exactly ONE download: no retries")

    def test_size_mismatch_is_refused_without_retry_or_extraction(self) -> None:
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as tmp:
            ollama_dir = Path(tmp) / "ollama"
            with self.assertRaises(OllamaInstallError) as ctx:
                install_ollama(
                    ollama_dir,
                    expected_sha256=hashlib.sha256(self.ARCHIVE).hexdigest(),
                    expected_size_bytes=len(self.ARCHIVE) + 1,
                    urlopen=fake_urlopen_stream([self.ARCHIVE], calls),
                    euid=1000,
                )
            self.assertIn("without any retry", str(ctx.exception))
            self.assertEqual(len(calls), 1)
            self.assertFalse((ollama_dir / "bin").exists())

    def test_hash_mismatch_is_refused_without_retry(self) -> None:
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OllamaInstallError) as ctx:
                install_ollama(
                    Path(tmp) / "ollama",
                    expected_sha256="0" * 64,
                    expected_size_bytes=len(self.ARCHIVE),
                    urlopen=fake_urlopen_stream([self.ARCHIVE], calls),
                    euid=1000,
                )
            self.assertIn("refusing without any retry", str(ctx.exception))
            self.assertEqual(len(calls), 1)

    def test_oversized_stream_is_refused_immediately(self) -> None:
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OllamaInstallError) as ctx:
                install_ollama(
                    Path(tmp) / "ollama",
                    expected_sha256="0" * 64,
                    expected_size_bytes=10,
                    urlopen=fake_urlopen_stream([b"x" * 11], calls),
                    euid=1000,
                )
            self.assertIn("refusing", str(ctx.exception))

    def test_existing_runtime_is_never_overwritten_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ollama_dir = Path(tmp) / "ollama"
            (ollama_dir / "bin").mkdir(parents=True)
            (ollama_dir / "bin" / "ollama").write_text("existing")
            with self.assertRaises(OllamaInstallError):
                install_ollama(
                    ollama_dir,
                    expected_sha256=hashlib.sha256(self.ARCHIVE).hexdigest(),
                    expected_size_bytes=len(self.ARCHIVE),
                    urlopen=fake_urlopen_stream([self.ARCHIVE], []),
                    euid=1000,
                )

    def test_refuses_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OllamaInstallError) as ctx:
                install_ollama(
                    Path(tmp) / "ollama",
                    urlopen=fake_urlopen_stream([self.ARCHIVE], []),
                    euid=0,
                )
            self.assertIn("no sudo", str(ctx.exception))

    def test_configured_archive_identity_matches_methodology(self) -> None:
        self.assertEqual(
            config.OLLAMA_ARCHIVE_SHA256,
            "f361dc3992ec07e4ad429f4bb2d10d4663ba2c295f9a9a688c7d52f4ba650034",
        )
        self.assertEqual(config.OLLAMA_ARCHIVE_SIZE_BYTES, 1429323296)


# --------------------------------------------------------------------------
# import-model
# --------------------------------------------------------------------------


class ImportModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.global_store = root / "global"
        self.isolated = root / "isolated"
        self.config_bytes = b'{"architecture":"qwen3.5"}'
        self.blob_bytes = b"ggml-blob-payload"
        config_hex = hashlib.sha256(self.config_bytes).hexdigest()
        blob_hex = hashlib.sha256(self.blob_bytes).hexdigest()
        self.config_hex = config_hex
        self.blob_hex = blob_hex
        manifest = {
            "schemaVersion": 2,
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                       "digest": f"sha256:{config_hex}", "size": len(self.config_bytes)},
            "layers": [
                {"mediaType": "application/vnd.ollama.image.model",
                 "digest": f"sha256:{blob_hex}", "size": len(self.blob_bytes)}
            ],
        }
        manifest_dir = (
            self.global_store / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5"
        )
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "9b").write_text(json.dumps(manifest))
        blobs = self.global_store / "blobs"
        blobs.mkdir()
        (blobs / f"sha256-{config_hex}").write_bytes(self.config_bytes)
        (blobs / f"sha256-{blob_hex}").write_bytes(self.blob_bytes)

    def test_import_copies_verified_blobs_and_manifest(self) -> None:
        report = import_model("qwen3.5:9b", self.global_store, self.isolated)
        self.assertEqual(report.blobs_total, 2)
        self.assertEqual(report.blobs_copied, 2)
        copied_blob = self.isolated / "blobs" / f"sha256-{self.blob_hex}"
        self.assertEqual(copied_blob.read_bytes(), self.blob_bytes)
        copied_manifest = (
            self.isolated / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5" / "9b"
        )
        self.assertTrue(copied_manifest.is_file())

    def test_reimport_reuses_verified_existing_blobs(self) -> None:
        import_model("qwen3.5:9b", self.global_store, self.isolated)
        report = import_model("qwen3.5:9b", self.global_store, self.isolated)
        self.assertEqual(report.blobs_copied, 0)
        self.assertEqual(report.blobs_reused, 2)

    def test_global_store_is_never_written(self) -> None:
        before = snapshot_store(self.global_store)
        import_model("qwen3.5:9b", self.global_store, self.isolated)
        after = snapshot_store(self.global_store)
        self.assertTrue(snapshots_identical(before, after))

    def test_corrupt_blob_refuses_with_no_partial_import(self) -> None:
        blobs = self.global_store / "blobs"
        (blobs / f"sha256-{self.blob_hex}").write_bytes(b"CORRUPTED")
        with self.assertRaises(OllamaImportError) as ctx:
            import_model("qwen3.5:9b", self.global_store, self.isolated)
        self.assertIn("digest mismatch", str(ctx.exception))
        # Nothing was imported for this corrupted source:
        self.assertFalse((self.isolated / "manifests").exists())

    def test_missing_blob_refuses(self) -> None:
        (self.global_store / "blobs" / f"sha256-{self.blob_hex}").unlink()
        with self.assertRaises(OllamaImportError) as ctx:
            import_model("qwen3.5:9b", self.global_store, self.isolated)
        self.assertIn("missing from the global store", str(ctx.exception))

    def test_missing_manifest_refuses(self) -> None:
        with self.assertRaises(OllamaImportError):
            import_model("qwen2.5vl:7b", self.global_store, self.isolated)

    def test_model_name_must_be_name_tag(self) -> None:
        for bad in ("qwen3.5", "qwen3.5:9b:extra", ":tag", "name:"):
            with self.assertRaises(OllamaImportError):
                parse_model_name(bad)
        self.assertEqual(parse_model_name("qwen3.5:9b"), ("qwen3.5", "9b"))

    def test_malformed_digest_refuses(self) -> None:
        manifest_dir = (
            self.global_store / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5"
        )
        manifest = json.loads((manifest_dir / "9b").read_text())
        manifest["layers"][0]["digest"] = "md5:abcdef"
        (manifest_dir / "9b").write_text(json.dumps(manifest))
        with self.assertRaises(OllamaImportError):
            import_model("qwen3.5:9b", self.global_store, self.isolated)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
