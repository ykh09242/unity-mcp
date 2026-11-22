"""Pinned provenance and cache contract for local semantic search models."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROFILE_ENV: Final = "UNITY_MCP_SEARCH_MODEL_DIR"
MODEL_REVISION: Final = "147002d501fc76ecd313c631999ea796520fea74"
DENSE_REVISION: Final = "836121a0533e5664b21c7aacc5d22951f2b8b25b"
MODEL_ID: Final = f"labse-onnx-int8:{MODEL_REVISION}:{DENSE_REVISION}"
MODEL_LICENSE: Final = "Apache-2.0"
READY_FILE: Final = "READY"
READY_BYTES: Final = f"{MODEL_ID}\n".encode("ascii")
RERANKER_REVISION: Final = "6f5ff65298512715a1e669753bc754d2bc8f367b"
RERANKER_ID: Final = f"bge-reranker-v2-m3-onnx-int8:{RERANKER_REVISION}"
COMBINED_MODEL_ID: Final = f"{MODEL_ID}+{RERANKER_ID}"
RERANKER_READY_FILE: Final = "RERANKER_READY"
RERANKER_READY_BYTES: Final = f"{RERANKER_ID}\n".encode("ascii")
_RERANKER_URL: Final = (
    f"https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX/resolve/{RERANKER_REVISION}"
)


@dataclass(frozen=True, slots=True)
class ModelFile:
    name: str
    url: str
    size: int
    sha256: str


MODEL_FILES: Final = (
    ModelFile(
        "labse.onnx",
        f"https://huggingface.co/Xenova/LaBSE/resolve/{MODEL_REVISION}/onnx/model_quantized.onnx",
        471_527_949,
        "2d209704455c8e5718dbef9c46ca22c823eb35256b74b4122d90845310c7ef82",
    ),
    ModelFile(
        "labse-tokenizer.json",
        f"https://huggingface.co/Xenova/LaBSE/resolve/{MODEL_REVISION}/tokenizer.json",
        13_631_919,
        "09216b42d2697b7b4a26ac05ff09ba8bf52dc19b896c5ceee8bbff9f39055322",
    ),
    ModelFile(
        "labse-dense.safetensors",
        "https://huggingface.co/sentence-transformers/LaBSE/resolve/"
        f"{DENSE_REVISION}/2_Dense/model.safetensors",
        2_362_528,
        "f866c945fa2147ce4940c8ead0b4b20af4217e0f4664bd35f41c2a0967c4fe77",
    ),
)

RERANKER_FILES: Final = (
    ModelFile(
        "reranker.onnx",
        f"{_RERANKER_URL}/onnx/model_quantized.onnx",
        570_727_094,
        "912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d",
    ),
    ModelFile(
        "reranker-tokenizer.json",
        f"{_RERANKER_URL}/tokenizer.json",
        17_082_900,
        "8bf8afbfd11306bd872018c53bfdf2e160a56f8edbcf49933324404791c148d3",
    ),
)


class ModelIntegrityError(ValueError):
    """The cached file differs from the pinned artifact."""


def profile_dir() -> Path:
    """Resolve the optional profile relative to the server user's cache directory."""
    override = os.environ.get(PROFILE_ENV)
    if override:
        return Path(override).expanduser().resolve()

    match sys.platform:
        case "win32":
            cache = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        case "darwin":
            cache = Path.home() / "Library" / "Caches"
        case _:
            cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return (
        cache
        / "unity-mcp"
        / "search-models"
        / f"labse-int8-{MODEL_REVISION[:12]}-{DENSE_REVISION[:12]}"
    )


def runtime_python(profile: Path) -> Path:
    """Find the independent CPython worker executable inside the profile."""
    if os.name == "nt":
        return profile / "runtime" / "Scripts" / "python.exe"
    return profile / "runtime" / "bin" / "python"


def _has_marker(path: Path, expected: bytes) -> bool:
    try:
        with path.open("rb") as marker:
            return marker.read(len(expected) + 1) == expected
    except OSError:
        return False


def ready_profile(profile: Path) -> bool:
    """Check whether provisioning published the LaBSE encoder and its runtime."""
    try:
        return _has_marker(profile / READY_FILE, READY_BYTES) and runtime_python(profile).is_file()
    except OSError:
        return False


def reranker_ready(profile: Path) -> bool:
    """Check whether provisioning published BGE alongside its LaBSE profile."""
    return ready_profile(profile) and _has_marker(
        profile / RERANKER_READY_FILE, RERANKER_READY_BYTES
    )


def verify_model_file(path: Path, artifact: ModelFile) -> None:
    """Check the pinned length and digest before exposing a model file."""
    with path.open("rb") as source:
        if os.fstat(source.fileno()).st_size != artifact.size:
            raise ModelIntegrityError(f"{artifact.name} has the wrong byte length.")
        if hashlib.file_digest(source, "sha256").hexdigest() != artifact.sha256:
            raise ModelIntegrityError(f"{artifact.name} failed SHA-256 verification.")


def verify_weights(profile: Path) -> None:
    """Verify every LaBSE model file needed by the offline encoder."""
    for artifact in MODEL_FILES:
        verify_model_file(profile / artifact.name, artifact)


def verify_reranker_weights(profile: Path) -> None:
    """Verify every BGE model file needed by the offline reranker."""
    for artifact in RERANKER_FILES:
        verify_model_file(profile / artifact.name, artifact)
