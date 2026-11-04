"""Exercise the real Windows npm command-shim boundary without npm downloads."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import generate_mcpb as bundle


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows command shim boundary")


@pytest.fixture
def windows_packer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The shim has the same %* forwarding boundary used by npm's npx.cmd.
    directory = tmp_path / "node & tools %MCPB_EXPAND% !bang! ^caret (owned)"
    directory.mkdir()
    script = directory / "pack.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['MCPB_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('fixture-version')\n"
        "else:\n"
        "    pathlib.Path(sys.argv[-1]).write_bytes(b'packed fixture')\n"
        "sys.exit(int(os.environ.get('MCPB_EXIT', '0')))\n",
        encoding="utf-8",
    )
    (directory / "npx.cmd").write_text(
        f'@"{sys.executable}" "%~dp0pack.py" %*\n@exit /b %errorlevel%\n', encoding="utf-8"
    )
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("MCPB_CAPTURE", str(tmp_path / "argv.json"))
    monkeypatch.setenv("MCPB_EXPAND", "incorrect-expanded-value")
    monkeypatch.setenv("MCPB_SIDE_EFFECT", str(tmp_path / "side-effect"))
    monkeypatch.setattr(bundle, "REPO_ROOT", tmp_path)
    template = tmp_path / "manifest.json"
    template.write_text('{"name":"fixture","version":"old"}', encoding="utf-8")
    monkeypatch.setattr(bundle, "MANIFEST_TEMPLATE", template)
    return directory


def test_bundle_launches_cmd_shim_and_preserves_metacharacter_paths(
    tmp_path: Path, windows_packer: Path
) -> None:
    # Given a real npm-style shim and paths with spaces and shell metacharacters.
    destination = tmp_path / "bundle&mkdir %MCPB_SIDE_EFFECT%&rem %MCPB_EXPAND% !bang! ^caret"
    destination.mkdir()
    output = destination / "output.mcpb"
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"fixture icon")
    # When the production generator invokes the shim.
    bundle.generate_mcpb("1.2.3", output, icon)
    # Then argv is intact and no metacharacter content became a shell command.
    arguments = json.loads((tmp_path / "argv.json").read_text())
    assert arguments[:4] == ["--yes", "@anthropic-ai/mcpb@2.1.2", "pack", "."]
    assert Path(arguments[-1]).parent.parent == destination
    assert output.read_bytes() == b"packed fixture"
    assert not (tmp_path / "side-effect").exists()


def test_cmd_fixture_has_an_observable_injection_negative_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the same valid-filename payload placed unquoted into shell source.
    side_effect = tmp_path / "side-effect"
    monkeypatch.setenv("MCPB_SIDE_EFFECT", str(side_effect))
    # When an intentionally unsafe command expands that payload.
    result = subprocess.run(
        os.environ["COMSPEC"] + " /d /v:off /s /c echo bundle&mkdir %MCPB_SIDE_EFFECT%&rem tail",
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the sentinel proves command interpretation would be observable.
    assert result.returncode == 0, result.stderr
    assert side_effect.is_dir()


@pytest.mark.parametrize("argument", ['embedded"quote', "line\nbreak", "line\rbreak"])
def test_cmd_shim_rejects_impossible_path_characters(
    tmp_path: Path, windows_packer: Path, argument: str
) -> None:
    # Given a value that cannot be a valid Windows bundle path.
    # When the production launcher reaches a command shim.
    with pytest.raises(ValueError, match="quotes or newlines"):
        bundle.run_npx([argument], tmp_path)
    # Then it is rejected before a process is launched.
    assert not (tmp_path / "argv.json").exists()


def test_cmd_shim_failure_preserves_exit_code_and_existing_bundle(
    tmp_path: Path, windows_packer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a real shim that returns a failing exit code.
    monkeypatch.setenv("MCPB_EXIT", "37")
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"fixture icon")
    output = tmp_path / "output.mcpb"
    output.write_bytes(b"existing bundle")
    # When the production generator runs the shim.
    with pytest.raises(subprocess.CalledProcessError) as error:
        bundle.generate_mcpb("1.2.3", output, icon)
    # Then the failing code survives and replacement does not occur.
    assert error.value.returncode == 37
    assert output.read_bytes() == b"existing bundle"
