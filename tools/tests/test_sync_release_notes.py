"""Regression coverage for GitHub-backed release notes and website metadata."""

import json
from pathlib import Path
import subprocess
import sys
from typing import TypedDict
import urllib.error

import pytest

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import sync_release_notes as sync  # noqa: E402


class ReleaseFixture(TypedDict):
    tag_name: str
    html_url: str
    published_at: str
    draft: bool
    prerelease: bool
    body: str


def _release(tag: str, *, prerelease: bool = False, body: str = "Release notes.") -> ReleaseFixture:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/CoplayDev/unity-mcp/releases/tag/{tag}",
        "published_at": "2026-10-04T02:52:24Z",
        "draft": False,
        "prerelease": prerelease,
        "body": body,
    }


LATEST = _release("v10.3.0")
HISTORY = [_release("v11.0.0-beta.1", prerelease=True), _release("v10.4.0"), LATEST]
METADATA = {key: LATEST[key] for key in ("tag_name", "html_url", "published_at")}


@pytest.fixture
def outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    release_notes = tmp_path / "releases.md"
    readme = tmp_path / "README.md"
    metadata = tmp_path / "release-metadata.json"
    release_notes.write_text(sync.render_releases_md(HISTORY), encoding="utf-8")
    readme.write_text(sync.render_readme_recent(HISTORY), encoding="utf-8")
    metadata.write_text(json.dumps(METADATA, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sync, "RELEASES_MD", release_notes)
    monkeypatch.setattr(sync, "README_MD", readme)
    monkeypatch.setattr(sync, "RELEASE_METADATA", metadata, raising=False)
    return release_notes, readme, metadata


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        payload = LATEST if command[2].endswith("/latest") else HISTORY
        if "--slurp" in command:
            payload = [payload]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(sync.shutil, "which", lambda executable: "gh")
    monkeypatch.setattr(sync.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("metadata_exists", [True, False])
def test_main_syncs_official_latest_stable_instead_of_highest_history_version(
    outputs: tuple[Path, Path, Path],
    github: list[list[str]],
    metadata_exists: bool,
) -> None:
    # Given: higher stable/beta history entries and a stale website version.
    metadata = outputs[2]
    if metadata_exists:
        metadata.write_text('{"tag_name": "v10.0.0"}\n', encoding="utf-8")
    else:
        metadata.unlink()
    # When: the real sync entry point reads the API responses.
    result = sync.main([])
    # Then: the official latest endpoint determines the complete metadata.
    assert result == 0
    assert json.loads(metadata.read_text(encoding="utf-8")) == METADATA
    assert any(command[2] == "repos/CoplayDev/unity-mcp/releases/latest" for command in github)
    assert "v11.0.0-beta.1 (beta)" in outputs[0].read_text(encoding="utf-8")


def test_check_detects_only_metadata_drift_without_writing_any_output(
    outputs: tuple[Path, Path, Path],
    github: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: matching release notes and README, but stale metadata.
    outputs[2].write_text('{"tag_name": "v10.0.0"}\n', encoding="utf-8")
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in outputs]
    # When: check mode runs the real fetch/render pipeline.
    result = sync.main(["--check"])
    # Then: metadata drift is reported without changing any bytes.
    assert result == 1
    assert "release-metadata.json" in capsys.readouterr().out
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in outputs] == before


def test_check_passes_when_all_three_outputs_match(
    outputs: tuple[Path, Path, Path],
    github: list[list[str]],
) -> None:
    # Given: all outputs match the fixture API snapshot.
    before = [path.read_bytes() for path in outputs]
    # When: check mode runs.
    result = sync.main(["--check"])
    # Then: no drift or writes occur.
    assert result == 0
    assert [path.read_bytes() for path in outputs] == before


@pytest.mark.parametrize("args", [[], ["--check"]])
@pytest.mark.parametrize(
    "latest",
    [
        {},
        [],
        None,
        {**LATEST, "draft": True},
        {**LATEST, "prerelease": True},
        {**LATEST, "prerelease": "false"},
        {**LATEST, "tag_name": ""},
        {**LATEST, "published_at": None},
        {**LATEST, "html_url": " "},
    ],
)
def test_invalid_latest_aborts_before_any_output_changes(
    outputs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    github: list[list[str]],
    args: list[str],
    latest,
) -> None:
    # Given: valid history, an invalid latest response, and existing good files.
    outputs[0].write_text("Protected existing release notes.\n", encoding="utf-8")
    outputs[1].write_text("Protected existing README.\n", encoding="utf-8")
    before = [path.read_bytes() for path in outputs]
    run_history = sync.subprocess.run

    def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if command[2].endswith("/latest"):
            return subprocess.CompletedProcess(command, 0, json.dumps(latest), "")
        return run_history(command, **kwargs)

    monkeypatch.setattr(sync.subprocess, "run", run)
    # When: the sync/check pipeline receives the invalid response.
    result = sync.main(args)
    # Then: it fails without blanking or partially updating good outputs.
    assert result == 2
    assert [path.read_bytes() for path in outputs] == before


def test_empty_history_aborts_before_any_output_changes(
    outputs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no history, and existing good files.
    outputs[0].write_text("Protected existing release notes.\n", encoding="utf-8")
    outputs[1].write_text("Protected existing README.\n", encoding="utf-8")
    before = [path.read_bytes() for path in outputs]
    monkeypatch.setattr(sync, "_fetch_via_gh", lambda path, **kwargs: [])
    # When: normal sync runs.
    result = sync.main([])
    # Then: it fails without writing any output.
    assert result == 2
    assert [path.read_bytes() for path in outputs] == before


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError(sync.API + "/latest", 404, "Not Found", None, None),
        urllib.error.URLError("connection unavailable"),
        json.JSONDecodeError("Invalid JSON", "{", 1),
    ],
)
def test_latest_fetch_error_aborts_before_any_output_changes(
    outputs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    # Given: the urllib fallback succeeds for history but fails for latest.
    outputs[0].write_text("Protected existing release notes.\n", encoding="utf-8")
    outputs[1].write_text("Protected existing README.\n", encoding="utf-8")
    before = [path.read_bytes() for path in outputs]
    monkeypatch.setattr(sync.shutil, "which", lambda executable: None)

    def fetch(url: str):
        if url.endswith("/latest"):
            raise error
        return HISTORY

    monkeypatch.setattr(sync, "_fetch_via_urllib", fetch)
    # When: sync attempts to refresh outputs.
    result = sync.main([])
    # Then: the latest failure is surfaced before the first write.
    assert result == 2
    assert [path.read_bytes() for path in outputs] == before


def test_urllib_fallback_uses_official_latest_endpoint(
    outputs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: gh is unavailable and both API endpoints have fixture responses.
    urls: list[str] = []
    monkeypatch.setattr(sync.shutil, "which", lambda executable: None)
    outputs[2].write_text("{}\n", encoding="utf-8")

    def fetch(url: str):
        urls.append(url)
        return LATEST if url.endswith("/latest") else HISTORY

    monkeypatch.setattr(sync, "_fetch_via_urllib", fetch)
    # When: normal sync uses urllib.
    result = sync.main([])
    # Then: the stable version comes from /latest rather than list ordering.
    assert result == 0
    assert sync.API + "/latest" in urls
    assert json.loads(outputs[2].read_text(encoding="utf-8")) == METADATA


@pytest.mark.parametrize(
    "body",
    [
        "Normal notes.",
        "Keep literal ][ inside release notes.",
        "See [the guide][1] and [notes][2].",
    ],
)
@pytest.mark.parametrize("page_count", [1, 2])
def test_gh_pagination_parses_structured_pages_without_mutating_release_body(
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    page_count: int,
) -> None:
    # Given: two pages as gh emits them, with newline-separated legacy output.
    expected = [_release("v10.3.0", body=body), _release("v10.2.0")][:page_count]
    pages = [[release] for release in expected]
    commands: list[list[str]] = []
    monkeypatch.setattr(sync.shutil, "which", lambda executable: "gh")

    def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output = (
            json.dumps(pages)
            if "--slurp" in command
            else "\n".join(json.dumps(page) for page in pages)
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(sync.subprocess, "run", run)
    # When: the real gh adapter fetches all pages.
    releases = sync._fetch_via_gh("repos/CoplayDev/unity-mcp/releases?per_page=100")
    # Then: both releases and every literal body character survive parsing.
    assert releases == expected
    assert "--paginate" in commands[0] and "--slurp" in commands[0]


@pytest.mark.parametrize(
    "existing",
    [
        sync.README_MARKER_OPEN + "\nold\n" + sync.README_MARKER_CLOSE,
        "<details><summary><strong>Recent Updates</strong></summary>old</details>",
    ],
)
def test_readme_replacement_preserves_literal_backslashes_in_release_body(existing: str) -> None:
    # Given: release text containing Windows paths and regex-like escapes.
    body = r"Use C:\tools\bin; preserve \1 and \g<0> literally."
    replacement = sync.render_readme_recent([_release("v10.3.0", body=body)])
    # When: the real marker/legacy replacement applies rendered release text.
    updated = sync.replace_marked_block(existing, replacement)
    # Then: replacement text is inserted verbatim instead of as a regex template.
    assert updated == replacement
    assert body in updated
