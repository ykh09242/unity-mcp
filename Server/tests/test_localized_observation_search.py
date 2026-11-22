"""Search real declarations with raw product questions and no catalog translation."""

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from services.registry.localized_read_search import localized_read_terms
from services.registry.task_search import _normalized
from services.resources import register_all_resources
from services.tools import register_all_tools


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


@pytest.mark.parametrize(
    "query",
    [
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
    ],
)
def test_unrelated_and_mutating_queries_do_not_acquire_localized_read_evidence(query):
    assert localized_read_terms(_normalized(query)) == (set(), set())


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
def test_direct_requests_to_read_errors_provide_evidence_in_every_locale(query):
    console, camera = localized_read_terms(_normalized(query))
    assert len(console) == 3 and not camera


def test_camera_configuration_noun_preserves_read_evidence():
    query = "¿Qué cámara tiene la mayor profundidad según la configuración actual?"
    console, camera = localized_read_terms(_normalized(query))
    assert not console and len(camera) == 3


@pytest.mark.asyncio
async def test_localized_reads_respect_host_and_explicit_search_controls(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    server = FastMCP("localized-read-controls")
    register_all_tools(server)
    register_all_resources(server)
    async with Client(server, mode="legacy", cache=False) as client:
        for query, tool in (
            ("콘솔의 현재 오류 메시지는?", "read_console"),
            ("どのカメラの深度が最大？", "inspect_scene_cameras"),
        ):
            for override in (
                {"catalog_query": "find assets by name"},
                {"intent": "edit"},
                {"required_values": ["nodeCount"]},
                {"host_tool_names": ["manage_tools"]},
            ):
                args = {"action": "search", "query": query, "route_surfaces": "tools", **override}
                result = (await client.call_tool("manage_tools", args)).structured_content
                assert all(
                    r.get("tool") != tool
                    for group in result["route_groups"]
                    for r in (group["representative"], *group["alternatives"])
                )
