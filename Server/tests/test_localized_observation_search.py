"""Search real declarations with raw product questions and no catalog translation."""

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from services.resources import register_all_resources
from services.tools import register_all_tools


@pytest.fixture
def observation_server(monkeypatch):
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    server = FastMCP("localized-observations")
    register_all_tools(server)
    register_all_resources(server)
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["core", "full"])
async def test_real_catalog_finds_error_read_and_scene_camera_inventory(monkeypatch, profile):
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
    server = FastMCP("localized-observations")
    register_all_tools(server)
    register_all_resources(server)
    corpora = Path(__file__).resolve().parents[2] / "tools" / "experiments"
    async with Client(server, mode="legacy", cache=False) as client:
        for filename in ["blind_tool_tasks.json", "blind_tool_tasks_natural.json"]:
            prompts = json.loads((corpora / filename).read_text(encoding="utf-8"))
            for locale in ("ko", "ja", "es", "ar", "tr"):
                for task, surface, key, target, action in (
                    ("console-errors", "tools", "tool", "read_console", "get"),
                    ("console-errors", "both", "tool", "read_console", "get"),
                    ("camera-depth", "tools", "tool", "inspect_scene_cameras", None),
                    ("camera-depth", "resources", "uri", "mcpforunity://scene/cameras", None),
                    ("camera-depth", "both", "tool", "inspect_scene_cameras", None),
                ):
                    query = prompts[task][locale]
                    args = {"query": query, "route_surfaces": surface, "limit": 3}
                    reply = (await client.call_tool("search_unity_tools", args)).structured_content
                    assert query == reply["query"] == reply["matched_query"]
                    routes = [group["representative"] for group in reply["route_groups"]]
                    assert any(
                        r.get(key) == target and r.get("operation") == action for r in routes
                    ), (locale, task, surface)
                    if key == "tool":
                        assert target in [item["name"] for item in reply["results"]]
                    assert reply["recommended_action"] == "inspect_candidates"


@pytest.mark.asyncio
async def test_raw_unrelated_and_mutating_queries_do_not_establish_read_evidence(
    observation_server,
):
    queries = [
        "지금 콘솔의 오류 메시지를 지워 줘.",
        "콘솔에 뜬 오류 메시지를 전부 삭제해 줘.",
        "콘솔의 현재 오류 메시지를 삭제할래?",
        "今のコンソールのエラーメッセージをクリアして。",
        "Borra los mensajes de error actuales de la consola.",
        "امسح رسائل الخطأ الحالية في وحدة التحكم.",
        "Konsoldaki güncel hata mesajlarını temizle.",
        "Konsoldaki hata iletilerini sil.",
        "Konsoldaki hata iletilerini dosyaya aktar.",
        "Konsoldaki hata iletilerini metin dosyasına aktar.",
        "Konsoldaki hata iletilerini temizler misin?",
        "카메라 깊이를 가장 큰 값으로 설정해 줘.",
        "カメラの深度を最大に変更してください。",
        "Ajusta la profundidad de la cámara al valor máximo.",
        "Pon la profundidad de la cámara al máximo.",
        "اضبط عمق الكاميرا على أعلى قيمة.",
        "Kamera derinliğini en yüksek değere ayarla.",
        "¿Cuál es la mayor resolución de la textura de profundidad de esta cámara?",
        "¿Qué cámara tiene la mayor profundidad de bits?",
        "カメラの深度バッファは最大何ビット？",
        "오류 메시지를 파일로 보여줘.",
        "브라우저 개발자 콘솔의 오류 메시지를 보여줘.",
        "¿Qué mensajes de error muestra la consola del navegador?",
        "¿Cuál es el valor máximo del plano de recorte de la cámara?",
    ]
    async with Client(observation_server, mode="legacy", cache=False) as client:
        for query in queries:
            response = await client.call_tool("search_unity_tools", {"query": query, "limit": 3})
            result = response.structured_content
            assert result["query"] == result["matched_query"] == query
            assert result["matching"] in {"hybrid", "lexical"}
            assert result["match_basis"] != "declared_observation"
            assert {"required_values", "observation_source"}.isdisjoint(result)
            assert result["recommended_action"] in {"inspect_candidates", "browse_catalog"}
            for group in result["route_groups"]:
                for route in (group["representative"], *group["alternatives"]):
                    assert route["readiness"] == "unobserved"
                    assert "inputSchema" not in route
                    binding = (route.get("tool"), route.get("operation"))
                    if binding == ("read_console", "get"):
                        assert route["arguments"] == {
                            "action": "get",
                            "format": "json",
                            "page_size": 10,
                        }
                    elif binding == ("inspect_scene_cameras", None):
                        assert route.get("arguments", {}) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "콘솔 오류 읽어줘.",
        "콘솔의 현재 오류 메시지를 읽어 줘. 삭제하지 마.",
        "コンソールのエラーを読んでください。",
        "Lee los errores de la consola.",
        "اقرأ أخطاء وحدة التحكم.",
        "Konsoldaki hataları oku.",
        "Konsoldaki hata iletilerini aynen aktar. Kayıtları silme.",
    ],
)
async def test_raw_requests_to_read_errors_suggest_console_get(observation_server, query):
    async with Client(observation_server, mode="legacy", cache=False) as client:
        response = await client.call_tool(
            "search_unity_tools", {"query": query, "limit": 3, "route_surfaces": "tools"}
        )
    result = response.structured_content
    assert result["query"] == result["matched_query"] == query
    assert ("read_console", "get") in [
        (group["representative"].get("tool"), group["representative"].get("operation"))
        for group in result["route_groups"]
    ]


@pytest.mark.asyncio
async def test_raw_camera_configuration_question_suggests_camera_inventory(observation_server):
    query = "¿Qué cámara tiene la mayor profundidad según la configuración actual?"
    async with Client(observation_server, mode="legacy", cache=False) as client:
        response = await client.call_tool(
            "search_unity_tools", {"query": query, "limit": 3, "route_surfaces": "tools"}
        )
    result = response.structured_content
    assert result["query"] == result["matched_query"] == query
    assert "inspect_scene_cameras" in [
        group["representative"].get("tool") for group in result["route_groups"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "value"),
    [
        ("Konsoldaki hata iletilerini dosyaya aktar.", "console.entries"),
        ("카메라 깊이를 가장 큰 값으로 설정해 줘.", "unityCameras"),
    ],
)
async def test_localized_query_cannot_supply_live_evidence_from_a_file(
    observation_server, query, value
):
    async with Client(observation_server, mode="legacy", cache=False) as client:
        response = await client.call_tool(
            "search_unity_tools",
            {"query": query, "observation_source": "asset_file", "required_values": [value]},
        )
    result = response.structured_content
    assert result["query"] == query and result["matching"] == "observation"
    assert result["route_groups"] == []
    assert result["no_match"]["reason"] == "wrong_source"
    assert result["no_match"]["alternatives"][0]["observation"]["source"] == "editor_live"


@pytest.mark.asyncio
async def test_localized_reads_respect_host_and_explicit_search_controls(observation_server):
    async with Client(observation_server, mode="legacy", cache=False) as client:
        for query, tool in (
            ("콘솔의 현재 오류 메시지는?", "read_console"),
            ("どのカメラの深度が最大？", "inspect_scene_cameras"),
        ):
            args = {"action": "search", "query": query, "route_surfaces": "tools"}
            raw = (await client.call_tool("manage_tools", args)).structured_content
            assert tool in [group["representative"].get("tool") for group in raw["route_groups"]]
            for override in (
                {"catalog_query": "find assets by name"},
                {"intent": "edit"},
                {"required_values": ["nodeCount"]},
                {"host_tool_names": ["manage_tools"]},
            ):
                result = (
                    await client.call_tool("manage_tools", {**args, **override})
                ).structured_content
                assert all(
                    r.get("tool") != tool
                    for group in result["route_groups"]
                    for r in (group["representative"], *group["alternatives"])
                )
