"""Bundled catalog models can be inspected through the offline CLI."""

import json

import pytest
from click.testing import CliRunner
from cli.main import cli
from services.registry import semantic_coverage, semantic_encoder


@pytest.mark.parametrize("args", [("status",), ("status", "--verify")])
def test_status_reports_both_bundled_models_without_a_profile(tmp_path, args):
    old_profile = tmp_path / "old-profile"

    result = CliRunner().invoke(
        cli,
        ["--no-project-context", "--format", "json", "search-model", *args],
        env={"UNITY_MCP_SEARCH_MODEL_DIR": str(old_profile)},
    )

    assert result.exit_code == 0, result.output
    status = json.loads(result.stdout)
    assert status["verified"] is True
    assert {model["model"] for model in status["models"] if model["verified"]} == {
        semantic_encoder.MODEL_ID,
        semantic_coverage.MODEL_ID,
    }
    assert not old_profile.exists()


@pytest.mark.parametrize(
    "module,contents,args,exit_code",
    [
        (semantic_encoder, None, ("status",), 0),
        (semantic_coverage, b"damaged model", ("status", "--verify"), 1),
    ],
)
def test_status_identifies_the_unusable_asset(
    monkeypatch, tmp_path, module, contents, args, exit_code
):
    artifact = tmp_path / "model-data"
    if contents is not None:
        artifact.write_bytes(contents)
    monkeypatch.setattr(module, "_ARTIFACT", artifact)

    result = CliRunner().invoke(
        cli, ["--no-project-context", "--format", "json", "search-model", *args]
    )

    assert result.exit_code == exit_code, result.output
    status = json.loads(result.stdout)
    assert status["verified"] is False
    assert {model["model"] for model in status["models"] if not model["verified"]} == {
        module.MODEL_ID
    }


def test_legacy_install_performs_the_bundled_check_and_does_not_create_a_cache(tmp_path):
    old_profile = tmp_path / "old-profile"

    result = CliRunner().invoke(
        cli,
        ["--no-project-context", "--format", "json", "search-model", "install"],
        env={"UNITY_MCP_SEARCH_MODEL_DIR": str(old_profile)},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["verified"] is True
    assert not old_profile.exists()


@pytest.mark.parametrize(
    "args", [("--from-directory", "source"), ("--encoder-only",), ("--offline",)]
)
def test_legacy_install_rejects_removed_options(args):
    result = CliRunner().invoke(cli, ["--no-project-context", "search-model", "install", *args])

    assert result.exit_code == 2, result.output
    assert args[0] in result.stderr
    assert "search-model status --verify" in result.stderr


def test_search_model_help_lists_the_status_command_only():
    result = CliRunner().invoke(cli, ["search-model", "--help"])

    assert result.exit_code == 0, result.output
    assert "status" in result.stdout
    assert "install" not in result.stdout


def test_status_table_displays_both_model_rows():
    result = CliRunner().invoke(cli, ["--format", "table", "search-model", "status"])

    assert result.exit_code == 0, result.output
    assert "static-similarity" in result.stdout
    assert "potion-multilingual" in result.stdout
