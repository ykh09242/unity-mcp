import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from cli.main import cli
from click.testing import CliRunner
from services.registry import semantic_profile, semantic_profile_install
from services.registry.semantic_profile import (
    PROFILE_ENV,
    READY_FILE,
    ModelFile,
    ModelIntegrityError,
)
from services.registry.semantic_profile_install import ProfileInstallError


def _artifact(body: bytes, url: str = "https://example.invalid/model") -> ModelFile:
    return ModelFile("model.onnx", url, len(body), hashlib.sha256(body).hexdigest())


@contextmanager
def _range_server(body: bytes, offset: int):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Range") != f"bytes={offset}-":
                self.send_error(416)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {offset}-{len(body) - 1}/{len(body)}")
            self.end_headers()
            self.wfile.write(body[offset:])

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/model"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_staged_model_resumes_partial_download(tmp_path):
    body = b"complete-model-bytes"
    (tmp_path / "model.onnx.part").write_bytes(body[:5])

    with _range_server(body, offset=5) as url:
        semantic_profile_install._stage_model(tmp_path, _artifact(body, url), None, offline=False)

    assert (tmp_path / "model.onnx").read_bytes() == body
    assert not (tmp_path / "model.onnx.part").exists()


def test_staged_model_rejects_wrong_digest_even_with_correct_size(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.onnx").write_bytes(b"bad-data")

    with pytest.raises(ModelIntegrityError, match="SHA-256"):
        semantic_profile_install._stage_model(
            tmp_path, _artifact(b"gooddata"), source, offline=True
        )

    assert not (tmp_path / "model.onnx").exists()
    assert not (tmp_path / "model.onnx.part").exists()


def test_offline_install_refuses_partial_model(tmp_path):
    (tmp_path / "model.onnx.part").write_bytes(b"partial")

    with pytest.raises(ProfileInstallError, match="--from-directory"):
        semantic_profile_install._stage_model(
            tmp_path, _artifact(b"partial model"), None, offline=True
        )

    assert not (tmp_path / "model.onnx").exists()
    assert (tmp_path / "model.onnx.part").read_bytes() == b"partial"


def test_failed_worker_check_never_activates_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setattr(semantic_profile_install, "MODEL_FILES", ())

    def provision(target: Path, *, offline: bool) -> None:
        worker = semantic_profile.runtime_python(target)
        worker.parent.mkdir(parents=True)
        worker.touch()

    def verify(target: Path) -> None:
        raise ProfileInstallError("Worker process did not return a valid embedding.")

    monkeypatch.setattr(semantic_profile_install, "_provision_runtime", provision)
    monkeypatch.setattr(semantic_profile_install, "verify_installation", verify)

    with pytest.raises(ProfileInstallError, match="valid embedding"):
        semantic_profile_install.install_profile(profile, offline=True)

    assert not (profile / READY_FILE).exists()


def test_concurrent_installs_share_one_completed_profile(tmp_path, monkeypatch):
    body = b"pinned data"
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.onnx").write_bytes(body)
    profile = tmp_path / "profile"
    files = (_artifact(body),)
    monkeypatch.setattr(semantic_profile_install, "MODEL_FILES", files)
    monkeypatch.setattr(semantic_profile, "MODEL_FILES", files)
    first_claimed = Event()
    second_waiting = Event()
    real_lock = semantic_profile_install.file_lock

    @contextmanager
    def observe_contention(path: Path, *, timeout_seconds: float):
        if first_claimed.is_set():
            with pytest.raises(BlockingIOError), real_lock(path, wait=False):
                pytest.fail("The second installer locked an active destination")
            second_waiting.set()
        with real_lock(path, timeout_seconds=timeout_seconds):
            yield

    def provision(target: Path, *, offline: bool) -> None:
        worker = semantic_profile.runtime_python(target)
        worker.parent.mkdir(parents=True, exist_ok=True)
        # The exclusive create fails if a second installer provisions the same runtime.
        worker.touch(exist_ok=False)
        first_claimed.set()
        assert second_waiting.wait(10)
        assert not (target / READY_FILE).exists()

    def install_again() -> None:
        assert first_claimed.wait(10)
        semantic_profile_install.install_profile(profile, source=source, offline=True)

    monkeypatch.setattr(semantic_profile_install, "file_lock", observe_contention)
    monkeypatch.setattr(semantic_profile_install, "_provision_runtime", provision)
    monkeypatch.setattr(
        semantic_profile_install, "verify_installation", semantic_profile.verify_weights
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            semantic_profile_install.install_profile, profile, source=source, offline=True
        )
        second = pool.submit(install_again)
        first.result(timeout=10)
        second.result(timeout=10)

    assert semantic_profile.ready_profile(profile)
    assert (profile / "model.onnx").read_bytes() == body


def test_regular_cli_reports_missing_profile_without_creating_it(tmp_path):
    profile = tmp_path / "absent"

    result = CliRunner().invoke(
        cli,
        ["--no-project-context", "--format", "json", "search-model", "status"],
        env={PROFILE_ENV: str(profile)},
    )

    assert result.exit_code == 0, result.output
    status = json.loads(result.stdout)
    assert status["directory"] == str(profile)
    assert status["status"] == "unavailable"
    assert not profile.exists()


def test_regular_cli_verify_fails_with_install_command_for_missing_profile(tmp_path):
    profile = tmp_path / "absent"

    result = CliRunner().invoke(
        cli,
        ["--no-project-context", "search-model", "status", "--verify"],
        env={PROFILE_ENV: str(profile)},
    )

    assert result.exit_code == 1, result.output
    assert "unity-mcp search-model install" in result.stderr
    assert not profile.exists()
