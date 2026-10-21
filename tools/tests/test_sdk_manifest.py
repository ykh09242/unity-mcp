"""Review drift between installation metadata, the lock and runtime adapter gates."""

import json
from pathlib import Path

import pytest


def test_shipped_sdk_manifest_is_generated_from_the_reviewed_lock():
    from generate_sdk_compatibility import render, ROOT, TARGET

    assert TARGET.read_text(encoding="utf-8") == render(ROOT)


def fixture_root(tmp_path, *, dependency="fastmcp==4.1.1", locked="4.1.1"):
    server = tmp_path / "Server"
    server.mkdir()
    (server / "pyproject.toml").write_text(
        "[project]\ndependencies = " + json.dumps([dependency, "mcp==2.3.0", "mcp-types==2.3.0"])
    )
    (server / "uv.lock").write_text(
        f'[[package]]\nname = "fastmcp"\nversion = "{locked}"\n'
        '[[package]]\nname = "mcp"\nversion = "2.3.0"\n'
        '[[package]]\nname = "mcp-types"\nversion = "2.3.0"\n'
    )
    return tmp_path


@pytest.mark.parametrize(
    "dependency,locked", [("fastmcp>=4.1.1,<5", "4.1.1"), ("fastmcp==4.1.1", "4.1.2")]
)
def test_generation_rejects_allowed_or_locked_sdk_drift_before_writing(
    tmp_path, dependency, locked
):
    from generate_sdk_compatibility import render

    root = fixture_root(tmp_path, dependency=dependency, locked=locked)
    with pytest.raises(ValueError, match="exact dependency"):
        render(root)
    assert not (root / "Server/src/core/sdk_compatibility.json").exists()


def test_generation_is_deterministic_and_includes_all_three_sdk_packages(tmp_path):
    from generate_sdk_compatibility import render

    root = fixture_root(tmp_path)
    assert render(root) == render(root)
    assert json.loads(render(root))["packages"] == {
        "fastmcp": "4.1.1",
        "mcp": "2.3.0",
        "mcp-types": "2.3.0",
    }
