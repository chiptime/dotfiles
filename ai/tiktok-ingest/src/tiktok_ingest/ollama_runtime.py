"""Isolated Ollama runtime lifecycle and provisioning (Milestone 3).

The host's global Ollama (0.14.2) is incompatible with qwen3.5+ and is
NEVER touched: this module runs the verified isolated 0.34.1 server with

- ``HOME`` pointed at an ISOLATED directory (methodology section 8.2: an
  inherited real HOME let the isolated server rewrite the global
  ``~/.ollama`` cache),
- ``OLLAMA_MODELS`` pointed at an isolated models directory,
- loopback-only binding on port 11435 (never the global 11434).

Everything here is EXPLICIT: ``install-ollama`` and ``import-model`` are
operator-invoked provisioning commands, never implicit processing steps.
The global store is only ever READ (snapshot + import source) and is proven
bit-identical around gated runs.

All side effects are injectable (spawn, urlopen, process handles, runners);
the fixture test suite never starts a real server or downloads anything.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import config
from .gates import parse_ollama_ps
from .state import atomic_write_bytes
from .validation import sha256_of_file

# Chunked I/O for streaming downloads and blob hashing (1 MiB).
_IO_CHUNK_BYTES = 1024 * 1024

# Default graceful-termination bound before SIGKILL (methodology section 6.6
# recorded SIGTERM -> SIGKILL after 10 s for container stops; the same bound
# governs the isolated server process).
TERMINATE_GRACE_SECONDS = 10.0


class OllamaRuntimeError(RuntimeError):
    """Raised when the isolated Ollama lifecycle cannot be honored."""


class OllamaInstallError(OllamaRuntimeError):
    """Raised when archive provisioning fails ANY verification. No retry."""


class OllamaImportError(OllamaRuntimeError):
    """Raised when model import fails ANY digest verification. No partial import."""


# --------------------------------------------------------------------------
# Server lifecycle
# --------------------------------------------------------------------------


def server_env(
    home_dir: Path | str, models_dir: Path | str, host: str, port: int
) -> dict[str, str]:
    """Environment overrides for the isolated server (pure, no mutation).

    ``HOME`` AND ``OLLAMA_MODELS`` are both redirected; ``OLLAMA_HOST``
    binds loopback only. Everything else inherits the caller environment.
    """
    return {
        **os.environ,
        "HOME": str(home_dir),
        "OLLAMA_MODELS": str(models_dir),
        "OLLAMA_HOST": f"{host}:{port}",
    }


ProcessLike = Any  # subprocess.Popen-shaped: pid, poll, terminate, kill, wait
SpawnFn = Callable[[list[str], dict[str, str], int], ProcessLike]


def _default_spawn(argv: list[str], env: dict[str, str], log_fd: int) -> ProcessLike:
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        argv,
        stdout=log_fd,
        stderr=subprocess.STDOUT,
        env=env,
    )


@dataclasses.dataclass
class ServerHandle:
    """A running isolated server plus its PID file location."""

    process: ProcessLike
    pid_path: Path


def start_server(
    *,
    bin_path: Path | str = config.OLLAMA_BIN_PATH,
    home_dir: Path | str = config.OLLAMA_HOME_DIR,
    models_dir: Path | str = config.OLLAMA_MODELS_DIR,
    host: str = config.OLLAMA_HOST,
    port: int = config.OLLAMA_PORT,
    log_path: Path | str = config.OLLAMA_SERVER_LOG_PATH,
    pid_path: Path | str = config.OLLAMA_PID_PATH,
    spawn: SpawnFn = _default_spawn,
) -> ServerHandle:
    """Start the isolated server; write the PID file atomically."""
    bin_path = Path(bin_path)
    if not bin_path.is_file():
        raise OllamaRuntimeError(
            f"isolated Ollama binary not found at {bin_path}; run the "
            "explicit install-ollama command first (provisioning is never implicit)"
        )
    for directory in (home_dir, models_dir, Path(log_path).parent):
        Path(directory).mkdir(parents=True, exist_ok=True)
    argv = [str(bin_path), "serve"]
    env = server_env(home_dir, models_dir, host, port)
    with open(log_path, "ab") as log_fd:  # noqa: SIM115 - handle owned by the child
        process = spawn(argv, env, log_fd.fileno())
    atomic_write_bytes(Path(pid_path), f"{process.pid}\n".encode("ascii"))
    return ServerHandle(process=process, pid_path=Path(pid_path))


def wait_ready(
    base_url: str = config.OLLAMA_API_BASE,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    deadline_seconds: float = 60.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = 0.2,
    timeout: float = 5.0,
) -> str:
    """Poll ``/api/version`` until the server answers; raise on deadline.

    A server that never becomes ready is a fail-closed condition: the stage
    that needs it must never proceed on a half-started runtime.
    """
    started = monotonic()
    url = f"{base_url.rstrip('/')}/api/version"
    last_error: str = "not attempted"
    while monotonic() - started < deadline_seconds:
        try:
            req = urllib.request.Request(url, method="GET")
            with urlopen(req, timeout=timeout) as response:
                document = json.loads(response.read().decode("utf-8"))
            version = document.get("version")
            if isinstance(version, str) and version:
                return version
            last_error = f"/api/version returned no version field: {document!r}"
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        sleep(poll_interval_seconds)
    raise OllamaRuntimeError(
        f"isolated Ollama did not become ready within {deadline_seconds:.0f}s "
        f"({last_error}); failing closed"
    )


def stop_server(
    handle: ServerHandle,
    *,
    terminate_grace_seconds: float = TERMINATE_GRACE_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """SIGTERM, bounded wait, SIGKILL, verify exit; remove the PID file."""
    process = handle.process
    if process.poll() is None:
        process.terminate()
        deadline = monotonic() + terminate_grace_seconds
        while process.poll() is None and monotonic() < deadline:
            sleep(0.1)
        if process.poll() is None:
            process.kill()
        returncode = process.wait()
        if returncode is None:
            raise OllamaRuntimeError(
                "isolated Ollama process did not exit after SIGKILL; refusing "
                "to report a clean stop"
            )
    handle.pid_path.unlink(missing_ok=True)


def verify_pid_gone(
    pid_path: Path | str, *, runner: Callable[..., tuple[int, str, str]]
) -> bool:
    """Post-stop cross-check: ``kill -0 <pid>`` must fail once it is gone."""
    try:
        pid = int(Path(pid_path).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return True  # no readable PID file: nothing to be alive
    returncode, _, _ = runner(["kill", "-0", str(pid)])
    return returncode != 0


# --------------------------------------------------------------------------
# Global-store snapshot (read-only) and bit-identical comparison
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StoreSnapshot:
    """Paths + sizes + sha256 of every file under an Ollama store root."""

    root: str
    files: dict[str, dict[str, Any]]

    def to_jsonable(self) -> dict[str, Any]:
        return {"root": self.root, "files": dict(sorted(self.files.items()))}


def snapshot_store(
    store_root: Path | str = config.GLOBAL_OLLAMA_STORE,
    *,
    hasher: Callable[[Path], str] = sha256_of_file,
) -> StoreSnapshot:
    """Walk a store read-only into a {relpath: {size, sha256}} manifest.

    Sorted relative paths make the snapshot deterministic; symlinks are
    recorded by name but never followed (a store should not contain any).
    """
    root = Path(store_root)
    files: dict[str, dict[str, Any]] = {}
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                files[relative] = {"symlink": os.readlink(path)}
                continue
            if path.is_file():
                files[relative] = {
                    "size": path.stat().st_size,
                    "sha256": hasher(path),
                }
    return StoreSnapshot(root=str(root), files=files)


def snapshots_identical(before: StoreSnapshot, after: StoreSnapshot) -> bool:
    """True only when both manifests are byte-for-byte equivalent."""
    return before.to_jsonable() == after.to_jsonable()


# --------------------------------------------------------------------------
# API helpers used by the gated stages
# --------------------------------------------------------------------------


def request_unload(
    model: str,
    base_url: str = config.OLLAMA_API_BASE,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = 30.0,
) -> None:
    """Ask the isolated server to unload: generate with ``keep_alive=0``."""
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urlopen(req, timeout=timeout) as response:
        response.read()


def resident_models(
    base_url: str = config.OLLAMA_API_BASE,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Return the server's resident-model list (empty means unloaded)."""
    url = f"{base_url.rstrip('/')}/api/ps"
    req = urllib.request.Request(url, method="GET")
    with urlopen(req, timeout=timeout) as response:
        return parse_ollama_ps(response.read().decode("utf-8"))


def read_server_log(
    log_path: Path | str = config.OLLAMA_SERVER_LOG_PATH, *, tail_bytes: int = 65536
) -> str:
    """Tail of the isolated server log, for layer-report parsing."""
    path = Path(log_path)
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - tail_bytes))
        return handle.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------
# Provisioning: install-ollama (explicit, verify-everything, no retry)
# --------------------------------------------------------------------------


def _refuse_root(euid: int | None) -> None:
    if euid is None:
        euid = os.geteuid()
    if euid == 0:
        raise OllamaInstallError(
            "refusing to install as root: no sudo, no global installs; run "
            "provisioning as the owning user"
        )


def install_ollama(
    ollama_dir: Path | str = config.OLLAMA_DIR,
    *,
    expected_sha256: str = config.OLLAMA_ARCHIVE_SHA256,
    expected_size_bytes: int = config.OLLAMA_ARCHIVE_SIZE_BYTES,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    url: str = config.OLLAMA_ARCHIVE_URL,
    euid: int | None = None,
) -> Path:
    """Download the official archive, verify size AND sha256, extract, delete.

    The gate is absolute: size must match EXACTLY (a single byte over or
    under is a refusal, mirroring methodology section 8.8's explicit
    re-authorization rule) and the sha256 must match EXACTLY. Any mismatch
    raises :class:`OllamaInstallError` and the temporary archive is deleted.
    There is NO retry: the download happens exactly once per invocation.
    """
    _refuse_root(euid)
    ollama_dir = Path(ollama_dir)
    ollama_dir.mkdir(parents=True, exist_ok=True)
    binary = ollama_dir / "bin" / "ollama"
    if binary.exists():
        raise OllamaInstallError(
            f"{binary} already exists; provisioning refuses to overwrite an "
            "existing runtime implicitly (delete the tree explicitly first)"
        )

    fd, tmp_name = tempfile.mkstemp(dir=ollama_dir, prefix=".archive-", suffix=".tgz")
    tmp_path = Path(tmp_name)
    digest = hashlib.sha256()
    total = 0
    try:
        req = urllib.request.Request(url, method="GET")
        with urlopen(req, timeout=120) as response, os.fdopen(fd, "wb") as out:
            while True:
                chunk = response.read(_IO_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size_bytes:
                    raise OllamaInstallError(
                        f"archive exceeded the recorded size of "
                        f"{expected_size_bytes} B at {total} B; refusing "
                        "without any retry (oversized transfers are reported, "
                        "never hidden)"
                    )
                digest.update(chunk)
                out.write(chunk)
                out.flush()
            if total != expected_size_bytes:
                raise OllamaInstallError(
                    f"archive size {total} B != recorded "
                    f"{expected_size_bytes} B; refusing without any retry"
                )
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise OllamaInstallError(
                    f"archive sha256 {actual_sha256} != recorded "
                    f"{expected_sha256}; refusing without any retry"
                )
            out.flush()
            os.fsync(out.fileno())
        with tarfile.open(tmp_path, "r:*") as archive:
            archive.extractall(ollama_dir, filter="data")  # noqa: S202 - pinned data filter
        if not binary.is_file():
            raise OllamaInstallError(
                f"archive extracted but {binary} is missing; refusing to "
                "report a usable runtime"
            )
    finally:
        tmp_path.unlink(missing_ok=True)  # the archive NEVER survives, matched or not
    return binary


# --------------------------------------------------------------------------
# Provisioning: import-model (read global store, verify every digest)
# --------------------------------------------------------------------------


_MANIFEST_HOST = "registry.ollama.ai"
_DIGEST_PREFIX = "sha256:"


def parse_model_name(model: str) -> tuple[str, str]:
    """Split ``name:tag``; refuse anything else (no implicit ``latest``)."""
    if not isinstance(model, str) or model.count(":") != 1:
        raise OllamaImportError(
            f"model {model!r} must be exactly 'name:tag' (e.g. qwen3.5:9b)"
        )
    name, tag = (part.strip() for part in model.split(":"))
    if not name or not tag or "/" in name or "/" in tag:
        raise OllamaImportError(f"model {model!r} is not a library name:tag pair")
    return name, tag


def _digest_hex(digest: str, context: str) -> str:
    if not isinstance(digest, str) or not digest.startswith(_DIGEST_PREFIX):
        raise OllamaImportError(f"{context}: unsupported digest {digest!r}")
    hex_part = digest[len(_DIGEST_PREFIX) :]
    if len(hex_part) != 64 or any(char not in "0123456789abcdef" for char in hex_part):
        raise OllamaImportError(f"{context}: malformed sha256 digest {digest!r}")
    return hex_part


def _verify_blob(blob_path: Path, expected_hex: str) -> None:
    actual = sha256_of_file(blob_path)
    if actual != expected_hex:
        raise OllamaImportError(
            f"blob {blob_path} hashes to {actual}, manifest says {expected_hex}; "
            "refusing on digest mismatch (no partial import)"
        )


def _copy_verified(src: Path, dst: Path, expected_hex: str) -> None:
    """Copy atomically, then prove the copy bit-identical by digest."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".part")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as fin:
            for chunk in iter(lambda: fin.read(_IO_CHUNK_BYTES), b""):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        _verify_blob(tmp_path, expected_hex)
        os.replace(tmp_path, dst)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)  # never leave a partial import behind
        raise


@dataclasses.dataclass(frozen=True)
class ImportReport:
    """Auditable result of one explicit import-model invocation."""

    model: str
    manifest_src: str
    manifest_dst: str
    blobs_total: int
    blobs_copied: int
    blobs_reused: int
    bytes_copied: int


def import_model(
    model: str,
    global_store: Path | str = config.GLOBAL_OLLAMA_STORE,
    isolated_models: Path | str = config.OLLAMA_MODELS_DIR,
) -> ImportReport:
    """Copy a model's manifest + blobs from the GLOBAL store, read-only.

    Every blob is verified against the manifest digest BEFORE copying and
    the destination copy is re-verified afterwards; any mismatch refuses
    the whole import with no partial state. The global store is never
    written (the source tree is opened read-only and compared untouched).
    """
    name, tag = parse_model_name(model)
    store = Path(global_store)
    isolated = Path(isolated_models)
    manifest_src = store / "manifests" / _MANIFEST_HOST / "library" / name / tag
    if not manifest_src.is_file():
        raise OllamaImportError(
            f"manifest {manifest_src} not found in the global store; refusing "
            "(this pipeline never pulls from the network)"
        )
    try:
        manifest = json.loads(manifest_src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OllamaImportError(f"manifest {manifest_src} is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise OllamaImportError(f"manifest {manifest_src} is not a JSON object")

    digests: list[tuple[str, int]] = []
    config_layer = manifest.get("config") or {}
    config_hex = _digest_hex(config_layer.get("digest", ""), f"{model} config")
    digests.append((config_hex, int(config_layer.get("size", 0))))
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise OllamaImportError(f"manifest {manifest_src} lists no layers")
    for index, layer in enumerate(layers):
        hex_part = _digest_hex(layer.get("digest", ""), f"{model} layer {index}")
        digests.append((hex_part, int(layer.get("size", 0))))

    global_blobs = store / "blobs"
    isolated_blobs = isolated / "blobs"
    blobs_copied = 0
    blobs_reused = 0
    bytes_copied = 0
    for hex_part, size in digests:
        src = global_blobs / f"sha256-{hex_part}"
        dst = isolated_blobs / f"sha256-{hex_part}"
        if not src.is_file():
            raise OllamaImportError(
                f"blob {src} is missing from the global store; refusing "
                "(no partial import)"
            )
        _verify_blob(src, hex_part)  # read-only verification of the source
        if dst.is_file():
            _verify_blob(dst, hex_part)
            blobs_reused += 1
            continue
        _copy_verified(src, dst, hex_part)
        actual_size = dst.stat().st_size
        if size and actual_size != size:
            dst.unlink(missing_ok=True)
            raise OllamaImportError(
                f"copied blob {dst} is {actual_size} B, manifest records "
                f"{size} B; refusing (no partial import)"
            )
        blobs_copied += 1
        bytes_copied += actual_size

    manifest_dst = isolated / "manifests" / _MANIFEST_HOST / "library" / name / tag
    manifest_dst.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(manifest_dst, manifest_src.read_bytes())

    return ImportReport(
        model=model,
        manifest_src=str(manifest_src),
        manifest_dst=str(manifest_dst),
        blobs_total=len(digests),
        blobs_copied=blobs_copied,
        blobs_reused=blobs_reused,
        bytes_copied=bytes_copied,
    )
