"""Task discovery uses local declarations and never grants tool access."""

import json

from click.testing import CliRunner
import pytest


def test_korean_intent_finds_official_skill_without_claiming_readiness():
    # Given: the installed built-in catalog without an editor or MCP connection.
    from cli.main import cli

    # When: a Korean collision task is searched through the public CLI.
    result = CliRunner().invoke(cli, ["--format", "json", "tool", "search", "충돌", "--limit", "2"])

    # Then: bounded, deterministic suggestions are explicitly unobserved.
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scope"] == "local_catalog"
    assert len(payload["results"]) == 2
    assert all(item["advertised"] is None for item in payload["results"])
    assert all(item["readiness"] == "unobserved" for item in payload["results"])
    assert any(item["official_skills"] for item in payload["results"])


@pytest.mark.parametrize(
    "arguments",
    [[""], [" "], ["x" * 513], ["physics", "--limit", "0"], ["physics", "--limit", "21"]],
)
def test_cli_refuses_invalid_search_bounds(arguments):
    # Given: malformed query or bounds.
    from cli.main import cli

    # When: arguments cross the CLI boundary.
    result = CliRunner().invoke(cli, ["tool", "search", *arguments])

    # Then: parsing refuses before catalog discovery or connection activity.
    assert result.exit_code == 2


def test_search_no_results_is_a_successful_empty_page():
    # Given: one real metadata declaration and a valid unmatched query.
    from services.registry.task_search import SearchRequest, search_catalog

    # When: a deterministic task query has no relevant terms.
    result = search_catalog(SearchRequest(query="unmatchablexyz"), [], visible_names=set())

    # Then: absence is distinct from invalid input.
    assert result["results"] == []
    assert result["total_matches"] == 0
    assert result["truncated"] is False


def test_search_orders_ties_by_name_and_does_not_change_disabled_entries():
    # Given: local declarations and a final visibility cap excluding both.
    from services.registry.task_search import SearchRequest, search_catalog

    entries = [
        {"name": name, "group": "graphics", "description": "render probe", "kwargs": {}}
        for name in ("probe_b", "probe_a")
    ]

    # When: the same request is evaluated over reverse declaration order.
    result = search_catalog(SearchRequest(query="probe", limit=1), entries, visible_names=set())

    # Then: ties are stable and discovery does not claim a disabled tool is callable.
    assert result["results"][0]["name"] == "probe_a"
    assert result["results"][0]["advertised"] is False
    assert result["results"][0]["maturity"] == "stable"
    assert result["truncated"] is True
    assert result["total_matches"] == 2


def test_exact_capability_query_prefers_focused_tool_and_preserves_action_maturity():
    # Given: a focused capability and its legacy source.
    from services.registry.task_search import SearchRequest, search_catalog

    entries = [
        {"name": name, "group": "graphics", "description": "", "kwargs": {}}
        for name in ("manage_render_pipeline", "manage_graphics")
    ]

    # When: the exact focused name is searched.
    result = search_catalog(SearchRequest(query="manage_render_pipeline"), entries)

    # Then: support status remains separate from tool/action selection and readiness.
    first = result["results"][0]
    assert first["name"] == "manage_render_pipeline"
    assert first["maturity"] == "stable"
    assert first["action_maturity"]["pipeline_get_diagnostics"] == "experimental"
    assert first["advertised"] is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "manage_render_pipeline",
            [
                "pipeline_get_diagnostics [experimental]",
                "validate-urp-render-graph-renderer-feature",
            ],
        ),
        ("manage_localization", ["com.unity.localization", "localization/SKILL.md"]),
    ],
)
def test_text_search_shows_actions_support_and_requirements(query, expected):
    from cli.main import cli

    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--instance",
            "Selected@abc",
            "tool",
            "search",
            query,
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    for value in expected:
        assert value in result.stdout
    assert "unobserved" in result.stdout
    assert (
        "--instance Selected@abc" in result.stdout or "--instance 'Selected@abc'" in result.stdout
    )
    assert f"tool describe {query}" in result.stdout


def test_text_search_reports_empty_results():
    from cli.main import cli

    result = CliRunner().invoke(cli, ["--no-project-context", "tool", "search", "unmatchablexyz"])
    assert result.exit_code == 0, result.output
    assert "No matching tools" in result.stdout
