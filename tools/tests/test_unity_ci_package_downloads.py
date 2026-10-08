"""Complete registry fetch contracts with real HTTPResponse bodies and no network."""

from dataclasses import dataclass
import hashlib
from http.client import HTTPResponse, IncompleteRead
import importlib.util
import io
import json
from pathlib import Path
import socket
import ssl
import sys
import tarfile
from urllib.error import HTTPError, URLError
import urllib.request

import pytest


SPEC = importlib.util.spec_from_file_location(
    "unity_ci_package_downloads", Path(__file__).resolve().parents[1] / "unity_ci_packages.py"
)
assert SPEC and SPEC.loader
packages = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = packages
SPEC.loader.exec_module(packages)
NAME = "com.unity.download.fixture"
VERSION = "1.2.3"
IDENTITY = f"{NAME}@{VERSION}"
METADATA_URL = f"https://packages.unity.com/{NAME}"
ARCHIVE_URL = f"https://download.packages.unity.com/{NAME}/-/{NAME}-{VERSION}.tgz"


@dataclass(frozen=True, slots=True)
class MemorySocket:
    payload: bytes

    def makefile(self, *args, **kwargs) -> io.BytesIO:
        return io.BytesIO(self.payload)


def response(content: bytes, *, truncated: bool = False) -> HTTPResponse:
    length = len(content)
    if truncated:
        content = content[: max(1, length // 2)]
    headers = f"HTTP/1.1 200 OK\r\nContent-Length: {length}\r\n\r\n".encode()
    result = HTTPResponse(MemorySocket(headers + content))
    result.begin()
    return result


def archive(member: str = "package/package.json") -> bytes:
    content = json.dumps({"name": NAME, "version": VERSION}).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        entry = tarfile.TarInfo(member)
        entry.size = len(content)
        bundle.addfile(entry, io.BytesIO(content))
    return output.getvalue()


def metadata(content: bytes, **dist) -> bytes:
    release = {
        "name": NAME,
        "version": VERSION,
        "dist": {"tarball": ARCHIVE_URL, "shasum": hashlib.sha1(content).hexdigest(), **dist},
    }
    return json.dumps({"versions": {VERSION: release}}).encode()


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args, **kwargs):
        raise AssertionError("Real network access is forbidden")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(urllib.request, "urlopen", denied)


def transport(monkeypatch: pytest.MonkeyPatch, metadata_outcomes, archive_outcomes):
    calls = []
    delays = []
    outcomes = {METADATA_URL: iter(metadata_outcomes), ARCHIVE_URL: iter(archive_outcomes)}

    def open_response(url, *, timeout):
        assert timeout == 60
        calls.append(url)
        outcome = next(outcomes[url])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(packages, "urlopen", open_response)
    monkeypatch.setattr(packages, "sleep", delays.append, raising=False)
    return calls, delays


def test_interrupted_archive_restarts_fresh_and_extracts_only_verified_complete_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = archive()
    interrupted, complete = response(content, truncated=True), response(content)
    calls, delays = transport(monkeypatch, [response(metadata(content))], [interrupted, complete])
    destination = tmp_path / "package"
    digest = packages.fetch_registry_package(NAME, VERSION, destination)
    assert digest == hashlib.sha256(content).hexdigest()
    assert json.loads((destination / "package.json").read_text()) == {
        "name": NAME,
        "version": VERSION,
    }
    assert calls == [METADATA_URL, ARCHIVE_URL, ARCHIVE_URL]
    assert delays == [1]
    assert interrupted.isclosed() and complete.isclosed()


def test_metadata_and_archive_retries_are_separate_complete_gets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = archive()
    data = metadata(content)
    calls, delays = transport(
        monkeypatch,
        [response(data, truncated=True), response(data)],
        [response(content, truncated=True), response(content)],
    )
    assert (
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
        == hashlib.sha256(content).hexdigest()
    )
    assert calls == [METADATA_URL, METADATA_URL, ARCHIVE_URL, ARCHIVE_URL]
    assert delays == [1, 1]


@pytest.mark.parametrize("stage", ["metadata", "archive"])
@pytest.mark.parametrize(
    "kind", ["timeout", "reset", "url_timeout", "url_reset", 408, 429, 500, 502, 503, 504]
)
def test_transient_failures_retry_only_the_failed_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str, kind
) -> None:
    content = archive()
    url = METADATA_URL if stage == "metadata" else ARCHIVE_URL
    errors = {
        "timeout": TimeoutError("fixture timeout"),
        "reset": ConnectionResetError("fixture reset"),
        "url_timeout": URLError(TimeoutError("fixture timeout")),
        "url_reset": URLError(ConnectionResetError("fixture reset")),
    }
    error = (
        errors[kind] if isinstance(kind, str) else HTTPError(url, kind, "fixture", {}, io.BytesIO())
    )
    data, payload = [response(metadata(content))], [response(content)]
    (data if stage == "metadata" else payload).insert(0, error)
    calls, delays = transport(monkeypatch, data, payload)
    digest = packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
    assert digest == hashlib.sha256(content).hexdigest()
    assert calls == (
        [METADATA_URL, METADATA_URL, ARCHIVE_URL]
        if stage == "metadata"
        else [METADATA_URL, ARCHIVE_URL, ARCHIVE_URL]
    )
    assert delays == [1]
    if isinstance(error, HTTPError):
        assert error.fp.closed


def test_interrupted_download_exhaustion_has_package_identity_and_no_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = archive()
    interrupted = [response(content, truncated=True) for _ in range(3)]
    calls, delays = transport(monkeypatch, [response(metadata(content))], interrupted)
    destination = tmp_path / "package"
    with pytest.raises(packages.PreparationError, match=IDENTITY) as caught:
        packages.fetch_registry_package(NAME, VERSION, destination)
    assert "3 attempts" in str(caught.value)
    assert isinstance(caught.value.__cause__, IncompleteRead)
    assert calls == [METADATA_URL, ARCHIVE_URL, ARCHIVE_URL, ARCHIVE_URL]
    assert delays == [1, 2]
    assert all(item.isclosed() for item in interrupted)
    assert not destination.exists()


@pytest.mark.parametrize("stage", ["metadata", "archive"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 501, 505])
def test_permanent_http_errors_close_body_without_retry_or_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str, status: int
) -> None:
    content = archive()
    error = HTTPError(
        METADATA_URL if stage == "metadata" else ARCHIVE_URL,
        status,
        "permanent fixture",
        {},
        io.BytesIO(),
    )
    data, payload = [response(metadata(content))], [response(content)]
    (data if stage == "metadata" else payload).insert(0, error)
    calls, delays = transport(monkeypatch, data, payload)
    destination = tmp_path / "package"
    with pytest.raises(packages.PreparationError, match=IDENTITY) as caught:
        packages.fetch_registry_package(NAME, VERSION, destination)
    assert caught.value.__cause__ is error
    assert calls == ([METADATA_URL] if stage == "metadata" else [METADATA_URL, ARCHIVE_URL])
    assert delays == [] and error.fp.closed and not destination.exists()


@pytest.mark.parametrize(
    "reason", ["permanent name failure", ssl.SSLCertVerificationError("untrusted certificate")]
)
def test_permanent_url_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reason
) -> None:
    error = URLError(reason)
    calls, delays = transport(monkeypatch, [error], [])
    with pytest.raises(packages.PreparationError, match=IDENTITY) as caught:
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
    assert caught.value.__cause__ is error
    assert calls == [METADATA_URL] and delays == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("data, exception", [(b"{broken", json.JSONDecodeError), (b"{}", KeyError)])
def test_metadata_json_or_schema_failure_does_not_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: bytes, exception
) -> None:
    calls, delays = transport(monkeypatch, [response(data)], [])
    with pytest.raises(exception):
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
    assert calls == [METADATA_URL] and delays == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "dist",
    [
        {"tarball": "https://untrusted.invalid/archive.tgz"},
        {"tarball": "http://packages.unity.com/archive.tgz"},
        {"shasum": "missing"},
    ],
)
def test_trust_and_digest_metadata_still_fail_before_archive_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dist
) -> None:
    calls, delays = transport(monkeypatch, [response(metadata(archive(), **dist))], [])
    with pytest.raises(packages.PreparationError):
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
    assert calls == [METADATA_URL] and delays == [] and list(tmp_path.iterdir()) == []


def test_integrity_failure_after_interruption_is_not_retried_or_extracted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = archive()
    calls, delays = transport(
        monkeypatch,
        [response(metadata(content))],
        [response(content, truncated=True), response(b"wrong complete payload")],
    )
    with pytest.raises(packages.PreparationError, match="integrity mismatch"):
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
    assert calls == [METADATA_URL, ARCHIVE_URL, ARCHIVE_URL]
    assert delays == [1] and list(tmp_path.iterdir()) == []


def test_safe_extraction_preflight_still_applies_after_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = archive("package/../escape")
    calls, delays = transport(
        monkeypatch,
        [response(metadata(content))],
        [response(content, truncated=True), response(content)],
    )
    with pytest.raises(packages.PreparationError, match="Unsafe package archive member"):
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
    assert calls == [METADATA_URL, ARCHIVE_URL, ARCHIVE_URL]
    assert delays == [1] and list(tmp_path.iterdir()) == []


def test_partial_exception_is_released_before_backoff_and_fresh_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import gc
    import weakref

    content = archive()
    calls, delays, observed = [], [], []

    def open_response(url, *, timeout):
        assert timeout == 60
        calls.append(url)
        if url == METADATA_URL:
            return response(metadata(content))
        assert url == ARCHIVE_URL
        if calls.count(ARCHIVE_URL) == 1:
            failure = IncompleteRead(b"discarded partial payload", len(content))
            observed.append(weakref.ref(failure))
            raise failure
        assert observed[0]() is None
        return response(content)

    def backoff(seconds):
        gc.collect()
        assert observed[0]() is None, "Partial download remains referenced across attempts"
        delays.append(seconds)

    monkeypatch.setattr(packages, "urlopen", open_response)
    monkeypatch.setattr(packages, "sleep", backoff)
    assert (
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
        == hashlib.sha256(content).hexdigest()
    )
    assert calls == [METADATA_URL, ARCHIVE_URL, ARCHIVE_URL] and delays == [1]


@pytest.mark.parametrize("kind", ["http", "url", "direct"])
def test_each_transient_error_branch_exhausts_at_three_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    errors = [
        {
            "http": HTTPError(ARCHIVE_URL, 503, "temporary fixture", {}, io.BytesIO()),
            "url": URLError(TimeoutError("temporary fixture")),
            "direct": TimeoutError("temporary fixture"),
        }[kind]
        for _ in range(3)
    ]
    calls, delays = transport(monkeypatch, [response(metadata(archive()))], errors)
    with pytest.raises(packages.PreparationError, match=IDENTITY) as caught:
        packages.fetch_registry_package(NAME, VERSION, tmp_path / "package")
    assert "3 attempts" in str(caught.value) and caught.value.__cause__ is errors[-1]
    assert calls == [METADATA_URL, ARCHIVE_URL, ARCHIVE_URL, ARCHIVE_URL]
    assert delays == [1, 2] and list(tmp_path.iterdir()) == []
    if kind == "http":
        assert all(error.fp.closed for error in errors)
