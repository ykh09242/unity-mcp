"""Pinned-image and official-preview contracts without Docker or Unity IO."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unity_ci


BASE = "unityci/base:ubuntu-3.2.2@sha256:" + "a" * 64
STABLE_IMAGE = "unityci/editor:ubuntu-6000.3.25f1-base-3@sha256:" + "b" * 64
STABLE_TEST_IMAGE = "unityci/editor:ubuntu-6000.3.25f1-linux-il2cpp-3@sha256:" + "c" * 64
BETA_URL = ("https://download.unity3d.com/download_unity/d6546dc2b3a9/"
            "LinuxEditorInstaller/Unity-6000.7.0b2.tar.xz")


@pytest.fixture
def metadata():
    return {
        "defaultVersion": "6000.3.25f1", "previewBaseImage": BASE,
        "versions": [
            {"id": "6000.3.25f1", "role": "lts", "channel": "lts", "image": STABLE_IMAGE},
            {"id": "6000.7.0b2", "role": "preview", "channel": "beta",
             "editorDownload": {"url": BETA_URL, "integrity": "md5-myMEkqj41IpM3/OuO3PDzQ==",
                                "size": 4076709268}},
        ],
    }


@pytest.fixture
def test_metadata(metadata):
    metadata["versions"][0]["testImage"] = STABLE_TEST_IMAGE
    prefix = BETA_URL.split("/LinuxEditorInstaller/")[0] + "/LinuxEditorTargetInstaller/"
    metadata["versions"][1]["testModules"] = [
        {"name": "linux-il2cpp", "url": prefix + "UnitySetup-Linux-IL2CPP-Support-for-Editor-6000.7.0b2.tar.xz",
         "integrity": "md5-Uz0k9an3hIYx+/w8w1weYQ==", "size": 64521976,
         "destination": "Editor/Data/PlaybackEngines/LinuxStandaloneSupport"},
        {"name": "linux-server", "url": prefix + "UnitySetup-Linux-Server-Support-for-Editor-6000.7.0b2.tar.xz",
         "integrity": "md5-uWQ0d0nI3uPzI5tKEY440Q==", "size": 175632308,
         "destination": "Editor/Data/PlaybackEngines/LinuxStandaloneSupport"},
    ]
    return metadata


def write_manifest(tmp_path, metadata):
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def test_real_matrix_cli_emits_every_row_in_manifest_order(tmp_path, metadata):
    path = write_manifest(tmp_path, metadata)
    result = subprocess.run(
        [sys.executable, str(Path(unity_ci.__file__)), "matrix", "--manifest", str(path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {"version": "6000.3.25f1", "channel": "lts"},
        {"version": "6000.7.0b2", "channel": "beta"},
    ]


@pytest.mark.parametrize("problem", ["empty", "duplicate", "missing_default", "bad_base"])
def test_rejects_invalid_manifest_structure(tmp_path, metadata, problem):
    if problem == "empty":
        metadata["versions"] = []
    elif problem == "duplicate":
        metadata["versions"].append(copy.deepcopy(metadata["versions"][0]))
    elif problem == "missing_default":
        metadata["defaultVersion"] = "6000.3.99f1"
    else:
        metadata["previewBaseImage"] = "ubuntu:latest"
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, metadata))


@pytest.mark.parametrize("changes", [
    {"id": "6000.3.25f1;echo unsafe"},
    {"id": "\uff16\uff10\uff10\uff10.3.25f1"},
    {"role": ""},
    {"channel": "unknown"},
    {"channel": []},
    {"channel": "beta"},
    {"image": "unityci/editor:ubuntu-6000.3.25f1-base-3"},
    {"image": "unityci/editor:ubuntu-6000.3.26f1-base-3@sha256:" + "b" * 64},
    {"image": "attacker/editor:ubuntu-6000.3.25f1-base-3@sha256:" + "b" * 64},
    {"image": STABLE_IMAGE + "\nimage=unsafe"},
    {"editorDownload": {"url": BETA_URL}},
    {"image": None},
])
def test_rejects_invalid_stable_rows(tmp_path, metadata, changes):
    metadata["versions"][0].update(changes)
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, metadata))


@pytest.mark.parametrize("changes", [
    {"url": BETA_URL.replace("download.unity3d.com", "example.com")},
    {"url": BETA_URL.replace("6000.7.0b2", "6000.7.0a6")},
    {"url": BETA_URL + "?redirect=unsafe"},
    {"url": BETA_URL.replace("https:", "http:")},
    {"integrity": "sha256-myMEkqj41IpM3/OuO3PDzQ=="},
    {"integrity": "md5-aGVsbG8="},
    {"integrity": "md5-not base64"},
    {"size": 0},
    {"size": -1},
    {"size": True},
])
def test_rejects_invalid_official_downloads(tmp_path, metadata, changes):
    metadata["versions"][1]["editorDownload"].update(changes)
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, metadata))


def test_rejects_preview_channel_mismatch(tmp_path, metadata):
    metadata["versions"][1]["channel"] = "alpha"
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, metadata))


def test_stable_prepare_returns_digest_without_docker(tmp_path, metadata, monkeypatch):
    docker = Mock(side_effect=AssertionError("stable images need no build or pull"))
    monkeypatch.setattr(subprocess, "run", docker)
    output = tmp_path / "github-output"
    manifest = unity_ci.load_manifest(write_manifest(tmp_path, metadata))
    assert unity_ci.prepare(manifest, "6000.3.25f1", output) == STABLE_IMAGE
    assert output.read_text() == f"image={STABLE_IMAGE}\n"
    docker.assert_not_called()


def test_preview_build_uses_safe_arguments_and_narrow_context(tmp_path, metadata, monkeypatch):
    docker = Mock()
    monkeypatch.setattr(subprocess, "run", docker)
    output = tmp_path / "github-output"
    manifest = unity_ci.load_manifest(write_manifest(tmp_path, metadata))
    image = unity_ci.prepare(manifest, "6000.7.0b2", output)
    command = docker.call_args.args[0]
    assert command[:2] == ["docker", "build"]
    assert command[-1] == str(Path(unity_ci.__file__).resolve().parent / "unity-ci")
    assert f"PREVIEW_BASE_IMAGE={BASE}" in command
    assert f"EDITOR_URL={BETA_URL}" in command
    assert "EDITOR_MD5=9b230492a8f8d48a4cdff3ae3b73c3cd" in command
    assert "EDITOR_SIZE=4076709268" in command
    assert "UNITY_VERSION=6000.7.0b2" in command
    assert image == "unity-mcp-editor:6000.7.0b2"
    assert docker.call_args.kwargs["check"] is True
    assert not docker.call_args.kwargs.get("shell")
    assert output.read_text() == f"image={image}\n"


def test_failed_build_never_publishes_image(tmp_path, metadata, monkeypatch):
    docker = Mock(side_effect=subprocess.CalledProcessError(1, ["docker", "build"]))
    monkeypatch.setattr(subprocess, "run", docker)
    output = tmp_path / "github-output"
    manifest = unity_ci.load_manifest(write_manifest(tmp_path, metadata))
    with pytest.raises(subprocess.CalledProcessError):
        unity_ci.prepare(manifest, "6000.7.0b2", output)
    assert not output.exists()


def test_unknown_version_never_builds(tmp_path, metadata, monkeypatch):
    docker = Mock()
    monkeypatch.setattr(subprocess, "run", docker)
    manifest = unity_ci.load_manifest(write_manifest(tmp_path, metadata))
    with pytest.raises(ValueError):
        unity_ci.prepare(manifest, "6000.7.0b999")
    docker.assert_not_called()


def test_prepare_cli_publishes_exact_image(tmp_path, metadata, monkeypatch, capsys):
    path = write_manifest(tmp_path, metadata)
    output = tmp_path / "github-output"
    output.write_text("earlier=value\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert unity_ci.main(["prepare", "6000.3.25f1", "--manifest", str(path)]) == 0
    assert capsys.readouterr().out == STABLE_IMAGE + "\n"
    assert output.read_text() == "earlier=value\n" + f"image={STABLE_IMAGE}\n"


def test_failed_prepare_cli_has_no_stdout_or_output(tmp_path, metadata, monkeypatch, capsys):
    path = write_manifest(tmp_path, metadata)
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, ["docker"])))
    assert unity_ci.main(["prepare", "6000.7.0b2", "--manifest", str(path)]) == 1
    result = capsys.readouterr()
    assert result.out == "" and "failed" in result.err
    assert not output.exists()


def test_stable_tests_prepare_uses_distinct_pinned_runtime_image(tmp_path, test_metadata, monkeypatch):
    docker = Mock(side_effect=AssertionError("public test image must not build"))
    monkeypatch.setattr(subprocess, "run", docker)
    manifest = unity_ci.load_manifest(write_manifest(tmp_path, test_metadata))
    assert unity_ci.prepare(manifest, "6000.3.25f1") == STABLE_IMAGE
    assert unity_ci.prepare(manifest, "6000.3.25f1", purpose="tests") == STABLE_TEST_IMAGE
    docker.assert_not_called()


@pytest.mark.parametrize("purpose", ["compile", "tests"])
def test_preview_purpose_controls_tag_and_verified_module_arguments(tmp_path, test_metadata, monkeypatch, purpose):
    docker = Mock()
    monkeypatch.setattr(subprocess, "run", docker)
    manifest = unity_ci.load_manifest(write_manifest(tmp_path, test_metadata))
    image = unity_ci.prepare(manifest, "6000.7.0b2", purpose=purpose)
    assert image == "unity-mcp-editor:6000.7.0b2" + ("-tests" if purpose == "tests" else "")
    args = docker.call_args.args[0]
    assert f"IMAGE_PURPOSE={purpose}" in args
    module_args = [arg for arg in args if arg.startswith(("IL2CPP_", "SERVER_"))]
    if purpose == "compile":
        assert module_args == []
    else:
        assert "IL2CPP_URL=" + test_metadata["versions"][1]["testModules"][0]["url"] in args
        assert "SERVER_URL=" + test_metadata["versions"][1]["testModules"][1]["url"] in args
        assert "IL2CPP_MD5=533d24f5a9f7848631fbfc3cc35c1e61" in args
        assert "SERVER_MD5=b964347749c8dee3f3239b4a118e38d1" in args
        assert "IL2CPP_SIZE=64521976" in args
        assert "SERVER_SIZE=175632308" in args


@pytest.mark.parametrize("image", [STABLE_IMAGE, STABLE_TEST_IMAGE.replace("6000.3.25f1", "6000.3.26f1"),
                                   STABLE_TEST_IMAGE.split("@")[0], "example.com/editor@sha256:" + "c" * 64])
def test_rejects_invalid_test_image(tmp_path, test_metadata, image):
    test_metadata["versions"][0]["testImage"] = image
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, test_metadata))


@pytest.mark.parametrize("changes", [
    {"name": "android"}, {"destination": "../../outside"},
    {"url": "https://example.com/editor.tar.xz"},
    {"integrity": "md5-invalid"}, {"size": False},
])
def test_rejects_invalid_test_module_metadata(tmp_path, test_metadata, changes):
    test_metadata["versions"][1]["testModules"][0].update(changes)
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, test_metadata))


def test_rejects_modules_from_other_editor_revision(tmp_path, test_metadata):
    module = test_metadata["versions"][1]["testModules"][0]
    module["url"] = module["url"].replace("d6546dc2b3a9", "b5c7d4d317b3")
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, test_metadata))


@pytest.mark.parametrize("problem", ["missing", "duplicate"])
def test_requires_both_distinct_runtime_modules(tmp_path, test_metadata, problem):
    modules = test_metadata["versions"][1]["testModules"]
    test_metadata["versions"][1]["testModules"] = modules[:1] if problem == "missing" else [modules[0], modules[0]]
    with pytest.raises(ValueError):
        unity_ci.load_manifest(write_manifest(tmp_path, test_metadata))


def test_missing_runtime_provider_never_builds_or_publishes(tmp_path, metadata, monkeypatch):
    docker = Mock()
    monkeypatch.setattr(subprocess, "run", docker)
    manifest = unity_ci.load_manifest(write_manifest(tmp_path, metadata))
    output = tmp_path / "github-output"
    for version in ("6000.3.25f1", "6000.7.0b2"):
        with pytest.raises(ValueError):
            unity_ci.prepare(manifest, version, output, purpose="tests")
    docker.assert_not_called()
    assert not output.exists()


def test_tests_purpose_cli_build_failure_does_not_publish(tmp_path, test_metadata, monkeypatch, capsys):
    path = write_manifest(tmp_path, test_metadata)
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, ["docker"])))
    assert unity_ci.main(["prepare", "6000.7.0b2", "--purpose", "tests", "--manifest", str(path)]) == 1
    assert capsys.readouterr().out == ""
    assert not output.exists()


def test_preview_dockerfile_default_base_matches_pinned_manifest():
    tools_dir = Path(unity_ci.__file__).parent
    metadata = json.loads((tools_dir / "unity-versions.json").read_text())
    lines = (tools_dir / "unity-ci" / "Dockerfile").read_text().splitlines()
    assert "ARG PREVIEW_BASE_IMAGE=" + metadata["previewBaseImage"] in lines


def test_preview_downloads_retry_tls_failures_with_bounded_time():
    text = (Path(unity_ci.__file__).parent / "unity-ci" / "Dockerfile").read_text()
    commands = text.replace("\\\n", "").split("curl ")[1:]
    assert len(commands) == 2
    for command in commands:
        args = shlex.split(command.split(";", 1)[0])
        assert "--fail" in args and "--retry-all-errors" in args
        for option, value in (("--retry", "3"), ("--retry-max-time", "300"),
                              ("--connect-timeout", "30"), ("--max-time", "300"),
                              ("--proto", "=https"), ("--proto-redir", "=https")):
            assert args[args.index(option) + 1] == value
        assert args[args.index("--output") + 1] == "$archive"


def test_preview_dockerfile_verifies_before_extraction_and_cleans_archive():
    text = (Path(unity_ci.__file__).parent / "unity-ci" / "Dockerfile").read_text()
    assert text.index("md5sum -c") < text.index("tar -xJf")
    assert text.index('test "$(stat -c%s "$archive")" = "$EDITOR_SIZE"') < text.index("tar -xJf")
    assert 'test -x "$UNITY_PATH/Editor/Unity"' in text
    assert text.index('rm "$archive"') > text.index("tar -xJf")
    assert text.count("RUN ") == 1
    assert "xvfb-run -ae /dev/stdout" in text
    assert "COPY" not in text


@pytest.mark.parametrize("purpose, problem", [
    ("compile", None), ("tests", None), ("tests", "checksum"),
    ("tests", "size"), ("tests", "layout"),
])
def test_preview_installation_body_checks_each_archive_without_network(tmp_path, purpose, problem):
    shell = shutil.which("bash")
    if not shell and os.name == "nt" and Path("C:/Program Files/Git/bin/bash.exe").exists():
        shell = "C:/Program Files/Git/bin/bash.exe"
    if not shell:
        pytest.skip("Bash is required for the hermetic Dockerfile installation check")
    env = {name: os.environ[name] for name in (
        "PATH", "SystemRoot", "TEMP", "TMP", "HOME", "SYSTEMDRIVE",
    ) if name in os.environ}
    env.update(IMAGE_PURPOSE=purpose, UNITY_PATH="unity",
               MODULE_DESTINATION="Editor/Data/PlaybackEngines/LinuxStandaloneSupport",
               ARCHIVE_DIR=".", FIXTURE_DIR=".")
    for name in ("EDITOR", "IL2CPP", "SERVER"):
        path = tmp_path / f"{name}.tar.xz"
        member_name = "Editor/Unity" if name == "EDITOR" else f"Variations/{name}.txt"
        if problem == "layout" and name != "EDITOR":
            member_name = f"Other/{name}.txt"
        content = b"#!/bin/sh\n# controlled fixture, never executed\nexit 1\n" if name == "EDITOR" else b"controlled fixture"
        member = tarfile.TarInfo(member_name)
        member.mode = 0o755 if name == "EDITOR" else 0o644
        member.size = len(content)
        with tarfile.open(path, "w:xz") as archive:
            archive.addfile(member, io.BytesIO(content))
        env[f"{name}_URL"] = f"https://fixture.invalid/{name}"
        env[f"{name}_MD5"] = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
        env[f"{name}_SIZE"] = str(path.stat().st_size)
    if problem == "checksum":
        env["IL2CPP_MD5"] = "0" * 32
    if problem == "size":
        env["SERVER_SIZE"] = "1"
    text = (Path(unity_ci.__file__).parent / "unity-ci" / "Dockerfile").read_text()
    # Exercise the actual install body, excluding the wrapper that writes /usr/bin.
    body = text.split("RUN ", 1)[1].split("printf '%s\\n' '#!/bin/bash'", 1)[0]
    body = body.replace("\\\n", "").replace("/tmp/unity", "${ARCHIVE_DIR}/unity")
    program = r'''
curl() {
    local arg name="" output="${!#}"
    for arg in "$@"; do
        case "$arg" in https://fixture.invalid/*) name="${arg##*/}" ;; esac
    done
    test -n "$name" || return 1
    printf '%s\n' "$name" >> "$FIXTURE_DIR/downloads.txt"
    cp "$FIXTURE_DIR/$name.tar.xz" "$output"
}
tar() {
    printf '%s\n' "${2##*/}" >> "$FIXTURE_DIR/extractions.txt"
    command tar "$@"
}
''' + body
    result = subprocess.run([shell, "-c", program], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is (problem is None), result.stdout + result.stderr
    downloads = (tmp_path / "downloads.txt").read_text().splitlines()
    extractions = (tmp_path / "extractions.txt").read_text().splitlines()
    if purpose == "compile":
        assert downloads == ["EDITOR"] and extractions == ["unity-editor.tar.xz"]
    elif problem == "checksum":
        assert downloads == ["EDITOR", "IL2CPP"] and extractions == ["unity-editor.tar.xz"]
    elif problem == "size":
        assert downloads == ["EDITOR", "IL2CPP", "SERVER"]
        assert extractions == ["unity-editor.tar.xz", "unity-IL2CPP.tar.xz"]
    else:
        assert downloads == ["EDITOR", "IL2CPP", "SERVER"]
        assert extractions == ["unity-editor.tar.xz", "unity-IL2CPP.tar.xz", "unity-SERVER.tar.xz"]
    if result.returncode == 0:
        assert not list(tmp_path.glob("unity-*.tar.xz"))
