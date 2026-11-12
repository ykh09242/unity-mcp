"""Language interpretation is explicit at the caller boundary; ranking is shared."""

import json
import unicodedata

from click.testing import CliRunner
import pytest


@pytest.mark.parametrize(
    "query",
    [
        "셰이더그래프 검사",
        "シェーダーグラフを検査",
        "检查着色器图",
        "فحص مخطط التظليل",
        "शेडर ग्राफ की जाँच",
        "Inspecter le graphe de shader",
        "Проверить граф шейдера",
        "กราฟเชเดอร์",
    ],
)
def test_caller_normalized_intent_has_identical_ranking_in_every_language(query):
    from cli.main import cli

    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "tool",
            "search",
            query,
            "--catalog-query",
            "shader graph inspect",
            "--limit",
            "3",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    baseline = CliRunner().invoke(
        cli, ["--format", "json", "tool", "search", "shader graph inspect", "--limit", "3"]
    )
    assert baseline.exit_code == 0, baseline.output
    assert payload["query"] == query
    assert payload["matched_query"] == "shader graph inspect"
    assert payload["matching"] == "lexical"
    assert payload["results"][0]["name"] == "manage_shader"
    assert payload["results"] == json.loads(baseline.stdout)["results"]
    assert all(item["readiness"] == "unobserved" for item in payload["results"])


@pytest.mark.parametrize("query", ["ＣＡＰＴＵＲＥ＿ＰＲＥＦＡＢ", "capture_prefab"])
def test_compatibility_forms_preserve_exact_identifier_priority(query):
    from services.registry.task_search import SearchRequest, search_catalog

    entries = [
        {"name": name, "group": "camera", "description": "", "kwargs": {}}
        for name in ("capture_prefab", "capture_camera")
    ]
    result = search_catalog(SearchRequest(query=query), entries)
    assert result["results"][0]["name"] == "capture_prefab"


@pytest.mark.parametrize("text", ["café", "각도", "الظِل", "शेडर", "グラフ"])
def test_unicode_tokens_preserve_combining_marks_and_normalize_equivalent_forms(text):
    from services.registry.task_search import _words

    assert _words(unicodedata.normalize("NFD", text)) == {
        unicodedata.normalize("NFKC", text).casefold()
    }


def test_unmatched_query_explains_catalog_normalization():
    from services.registry.task_search import SearchRequest, search_catalog

    result = search_catalog(SearchRequest(query="未知の操作"), [])
    assert result["results"] == []
    assert result["catalog_language"] == "en"
    assert result["next_action"] == "provide_catalog_query"
    assert "catalog_query" in result["hint"]


def test_unmatched_normalized_query_suggests_broadening():
    from services.registry.task_search import SearchRequest, search_catalog

    result = search_catalog(SearchRequest(query="未知の操作", catalog_query="unknown task"), [])
    assert result["next_action"] == "broaden_catalog_query"


@pytest.mark.parametrize("catalog_query", ["", "  ", "x" * 513, 12, True, ["shader"]])
def test_catalog_query_validation_is_bounded_and_strict(catalog_query):
    from pydantic import ValidationError
    from services.registry.task_search import SearchRequest

    with pytest.raises(ValidationError):
        SearchRequest(query="shader", catalog_query=catalog_query)


@pytest.mark.parametrize("catalog_query", ["", "  ", "x" * 513])
def test_cli_rejects_invalid_catalog_query_before_discovery(monkeypatch, catalog_query):
    from cli.main import cli
    from unittest.mock import Mock
    import utils.module_discovery as discovery

    discover = Mock(side_effect=AssertionError("invalid query reached discovery"))
    monkeypatch.setattr(discovery, "discover_modules", discover)
    result = CliRunner().invoke(cli, ["tool", "search", "shader", "--catalog-query", catalog_query])
    assert result.exit_code == 2
    discover.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "search", "query": "shader", "catalog_query": "  "},
        {"action": "reset", "catalog_query": "shader"},
    ],
)
async def test_invalid_catalog_query_calls_fail_before_visibility_or_mutation(arguments):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services.tools.manage_tools import manage_tools

    context = SimpleNamespace(
        fastmcp=SimpleNamespace(), request_context=None, reset_visibility=AsyncMock()
    )
    result = await manage_tools(context, **arguments)
    assert result["success"] is False
    context.reset_visibility.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_mcp_search_accepts_caller_normalized_intent():
    from fastmcp import Client, FastMCP
    from services.tools.manage_tools import manage_tools
    from services.tools import manage_shader  # Populate the real registry.

    assert manage_shader
    server = FastMCP("language-search")
    server.tool(manage_tools)
    async with Client(server) as client:
        result = await client.call_tool(
            "manage_tools",
            {"action": "search", "query": "着色器图", "catalog_query": "shader graph inspect"},
        )
    payload = result.structured_content
    assert payload["query"] == "着色器图"
    assert payload["matched_query"] == "shader graph inspect"
    assert payload["scope"] == "mcp_context"
    assert payload["results"][0]["name"] == "manage_shader"
