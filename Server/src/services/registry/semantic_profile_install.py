"""Explicit provisioning of the optional offline semantic-search profile."""

from __future__ import annotations

import shutil
import subprocess
from http.client import HTTPException
from pathlib import Path
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.file_lock import file_lock
from services.registry.semantic_profile import (
    MODEL_FILES,
    READY_BYTES,
    READY_FILE,
    RERANKER_FILES,
    RERANKER_READY_BYTES,
    RERANKER_READY_FILE,
    ModelFile,
    ModelIntegrityError,
    ready_profile,
    reranker_ready,
    runtime_python,
    verify_model_file,
    verify_reranker_weights,
    verify_weights,
)

_WORKER: Final = Path(__file__).with_name("semantic_labse_worker.py")
_RERANKER_WORKER: Final = Path(__file__).with_name("semantic_reranker_worker.py")
_REQUIREMENTS: Final = Path(__file__).with_name("model") / "labse-cpu-py313.lock"
_CHUNK_SIZE: Final = 1024 * 1024
_DEFAULT_INDEX: Final = "https://pypi.org/simple"


class ProfileInstallError(RuntimeError):
    """An operator action is needed to complete or verify provisioning."""


def _transfer_model(artifact: ModelFile, partial: Path, offset: int) -> None:
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    request = Request(artifact.url, headers=headers)
    with urlopen(request, timeout=60) as response:
        if response.status == 206:
            expected_range = f"bytes {offset}-{artifact.size - 1}/{artifact.size}"
            if response.headers.get("Content-Range") != expected_range:
                raise ProfileInstallError(f"Unexpected download range for {artifact.name}.")
        elif response.status == 200:
            # A mirror that ignores Range can still provide the complete file.
            offset = 0
        else:
            raise ProfileInstallError(f"Unexpected HTTP status for {artifact.name}.")

        with partial.open("ab" if offset else "wb") as target:
            while chunk := response.read(_CHUNK_SIZE):
                offset += len(chunk)
                if offset > artifact.size:
                    raise ProfileInstallError(f"Oversized download for {artifact.name}.")
                target.write(chunk)
        if offset != artifact.size:
            raise ProfileInstallError(
                f"Incomplete download for {artifact.name}. Retry installation."
            )


def _download(artifact: ModelFile, partial: Path) -> None:
    try:
        offset = partial.stat().st_size
    except FileNotFoundError:
        offset = 0
    try:
        _transfer_model(artifact, partial, offset if offset < artifact.size else 0)
    except HTTPError as error:
        raise ProfileInstallError(
            f"Download failed for {artifact.name} (HTTP {error.code}). Retry installation."
        ) from error
    except (URLError, HTTPException, ConnectionError, TimeoutError) as error:
        raise ProfileInstallError(
            f"Download interrupted for {artifact.name}. Retry installation."
        ) from error


def _valid_file(path: Path, artifact: ModelFile) -> bool:
    try:
        verify_model_file(path, artifact)
    except (FileNotFoundError, ModelIntegrityError):
        return False
    return True


def _stage_model(profile: Path, artifact: ModelFile, source: Path | None, *, offline: bool) -> None:
    target = profile / artifact.name
    if _valid_file(target, artifact):
        return

    partial = target.with_suffix(target.suffix + ".part")
    if not _valid_file(partial, artifact):
        if source is not None:
            shutil.copyfile(source / artifact.name, partial)
        elif offline:
            raise ProfileInstallError(
                f"{artifact.name} is missing or invalid. Supply --from-directory or retry online."
            )
        else:
            _download(artifact, partial)

        try:
            verify_model_file(partial, artifact)
        except ModelIntegrityError:
            partial.unlink(missing_ok=True)
            raise
    partial.replace(target)


def _run(command: list[str], *, timeout: int, task: str) -> None:
    try:
        subprocess.run(
            command, check=True, capture_output=True, text=True, errors="replace", timeout=timeout
        )
    except subprocess.CalledProcessError as error:
        output = (error.stderr or error.stdout or "").strip()
        if output:
            raise ProfileInstallError(f"{task} failed.\n{output}") from error
        raise ProfileInstallError(f"{task} failed (exit {error.returncode}).") from error
    except subprocess.TimeoutExpired as error:
        raise ProfileInstallError(f"{task} timed out.") from error


def _provision_runtime(profile: Path, *, offline: bool) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise ProfileInstallError(
            "Install uv (https://docs.astral.sh/uv/getting-started/installation/) and retry."
        )
    global_args = [uv, "--no-config"]
    if offline:
        global_args.append("--offline")
    _run(
        [
            *global_args,
            "venv",
            "--allow-existing",
            "--python",
            "cpython@3.13",
            str(profile / "runtime"),
        ],
        timeout=300,
        task="Creating the isolated CPython 3.13 runtime",
    )
    _run(
        [
            *global_args,
            "pip",
            "sync",
            "--python",
            str(runtime_python(profile)),
            "--default-index",
            _DEFAULT_INDEX,
            "--require-hashes",
            "--only-binary",
            ":all:",
            "--reinstall",
            str(_REQUIREMENTS),
        ],
        timeout=600,
        task="Installing the pinned CPU runtime wheels",
    )


def verify_installation(profile: Path) -> None:
    """Check pinned LaBSE files and execute the same encoder used by search."""
    verify_weights(profile)
    _run(
        [str(runtime_python(profile)), "-I", str(_WORKER), "--check", str(profile)],
        timeout=90,
        task="Checking offline LaBSE inference",
    )


def verify_reranker_installation(profile: Path) -> None:
    """Check pinned BGE files and execute the same reranker used by search."""
    verify_reranker_weights(profile)
    _run(
        [str(runtime_python(profile)), "-I", str(_RERANKER_WORKER), "--check", str(profile)],
        timeout=90,
        task="Checking offline BGE reranking",
    )


def _publish_ready(marker: Path, contents: bytes) -> None:
    pending = marker.with_suffix(".part")
    pending.write_bytes(contents)
    pending.replace(marker)


def _install_encoder(profile: Path, source: Path | None, *, offline: bool) -> None:
    if ready_profile(profile):
        try:
            verify_installation(profile)
            return
        except (OSError, ModelIntegrityError, ProfileInstallError):
            pass

    marker = profile / READY_FILE
    marker.unlink(missing_ok=True)
    # Fail on a missing or unsupported runtime before fetching the large model.
    _provision_runtime(profile, offline=offline)
    for artifact in MODEL_FILES:
        _stage_model(profile, artifact, source, offline=offline)
    verify_installation(profile)
    _publish_ready(marker, READY_BYTES)


def _install_reranker(profile: Path, source: Path | None, *, offline: bool) -> None:
    if reranker_ready(profile):
        try:
            verify_reranker_installation(profile)
            return
        except (OSError, ModelIntegrityError, ProfileInstallError):
            pass

    marker = profile / RERANKER_READY_FILE
    marker.unlink(missing_ok=True)
    for artifact in RERANKER_FILES:
        _stage_model(profile, artifact, source, offline=offline)
    verify_reranker_installation(profile)
    _publish_ready(marker, RERANKER_READY_BYTES)


def install_profile(
    profile: Path, *, source: Path | None = None, offline: bool = False, encoder_only: bool = False
) -> None:
    """Provision the encoder and enable the reranker as a separate update."""
    profile.mkdir(parents=True, exist_ok=True)
    with file_lock(profile / ".install.lock", timeout_seconds=900):
        _install_encoder(profile, source, offline=offline)
        if not encoder_only:
            _install_reranker(profile, source, offline=offline)
