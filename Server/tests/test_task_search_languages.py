"""Language interpretation is explicit at the caller boundary; ranking is shared."""

import json
import unicodedata

from click.testing import CliRunner
import pytest


@pytest.mark.parametrize(
    "query",
    [
        unicodedata.normalize("NFD", "색상 보정 창은 어느 메뉴에 등록돼 있나요?"),
        "照明ウィンドウのﾒﾆｭｰ内の場所を教えて。",
        "¿En qué menú del editor encuentro el mezclador de audio?",
        "¿Dónde está el menú de Frame Debugger?",
        "Necesito la ruta del menu para el mezclador de audio.",
        "ما مسار أمر الإضاءة في قَوَائِم المحرر؟",
        "ما هو مسار القائمة لنافذة Frame Debugger؟",
        "أين توجد أداة Frame Debugger في قائمة Unity؟",
        "SES KARIŞTIRICI HANGİ MENÜDE?",
        "Assets/Audio/Mixer.asset 작업에 쓰는 창의 메뉴 경로가 뭐야?",
        "Assets/Audio Mixers/Mixer.asset 작업에 쓰는 창의 메뉴 경로가 뭐야?",
        "Assets/Audio Mixers 폴더 작업에 쓰는 창은 어느 메뉴에 등록돼 있나요?",
        "게임 내 사운드를 보려면 편집기의 메뉴 경로가 어디야?",
        "이 편집기에서 오디오 믹서 창을 어디서 여는지 경로만 알려줘.",
        "オーディオミキサーをどこから開きますか。場所だけ教えてください。",
        "¿Desde dónde se accede al mezclador de audio del editor? Solo la ruta.",
        "من أين أفتح مازج الصوت في المحرر؟ أحتاج المسار فقط.",
        "Düzenleyicide Ses Karıştırıcı'ya nereden ulaşabilirim? Yalnızca yolunu yaz.",
        "에디터에서 오디오 믹서를 어디서 엽니까?",
        "프로파일러 창이 어디야?",
        "¿En qué parte del editor se abre el mezclador de audio?",
    ],
)
def test_cli_finds_unknown_menu_paths_for_other_window_names(query):
    from cli.main import cli

    run = CliRunner().invoke(
        cli,
        ["--format", "json", "tool", "search", query, "--limit", "3", "--route-surfaces", "tools"],
    )
    assert run.exit_code == 0, run.output
    result = json.loads(run.stdout)
    assert query == result["query"] == result["matched_query"]
    assert any(
        group["representative"].get("tool") == "list_menu_declarations"
        for group in result["route_groups"]
    )
    assert "list_menu_declarations" in [row["name"] for row in result["results"]]
    assert result["recommended_action"] == "inspect_candidates"


@pytest.mark.parametrize(
    "query",
    [
        "파일 경로를 복사하고 메뉴를 닫아줘.",
        "메뉴 항목을 실행해 줘.",
        "이 도구를 메뉴에 등록해 줘.",
        "Assets/메뉴경로.asset 파일은 어디에 있어?",
        "Window/Analysis/Profiler 메뉴 경로가 맞는지 확인해.",
        "Assets/Create/Material 메뉴 경로를 확인해.",
        "도구/분석/프로파일러 메뉴 경로를 실행해 줘.",
        "도구 / 분석 / 프로파일러 메뉴 경로를 실행해 줘.",
        "도구 › 분석 › 프로파일러 메뉴 경로를 실행해 줘.",
        "게임 내 일시정지 메뉴 위치를 화면 중앙으로 옮겨 줘.",
        "에디터에서 게임 내 일시정지 메뉴 위치를 화면 중앙으로 옮겨 줘.",
        "メニューを閉じて、シェーダーのパスを表示して。",
        "そのメニューを実行して。",
        "このコマンドをメニューに登録してください。",
        "ゲーム内のポーズメニューの位置を変更して。",
        "Cierra el menú y copia la ruta del archivo.",
        "Ejecuta el comando del menú.",
        "¿Dónde está el menú de pausa del juego?",
        "ما مسار قائمة الأغاني؟",
        "أين قائمة الملفات في المحرر؟",
        "افتح قائمة الأدوات.",
        "ما هو مسار القائمة داخل اللعبة؟",
        "Menüyü kapat ve dosya yolunu kopyala.",
        "Menü komutunu çalıştır.",
        "Oyun içi menü konumunu değiştir.",
        "오디오 믹서 창을 열어줘.",
        "オーディオミキサーを開いてください。",
        "Abre la ventana del mezclador de audio.",
        "افتح نافذة مازج الصوت في المحرر.",
        "Düzenleyicide Ses Karıştırıcı penceresini aç.",
        "에디터에서 이 파일을 어디서 여는지 경로만 알려줘.",
        "このエディターで設定ファイルをどこから開きますか。場所だけ教えて。",
        "¿Desde dónde se abre la carpeta del proyecto en el editor? Solo la ruta.",
        "من أين أفتح الملف في المحرر؟ أحتاج المسار فقط.",
        "Düzenleyicide bu dosyaya nereden ulaşabilirim? Yalnızca yolunu yaz.",
        "에디터 창에서 이 URL을 어디서 여나요?",
        "¿Desde dónde se abre la ventana de pausa del juego? Solo la ruta.",
        "이 프리팹은 에디터 어디에서 열 수 있나요?",
        "게임 일시정지 창은 어디에서 열 수 있어?",
        "에디터에서 이 창을 어디로 옮길까요?",
        "¿A qué parte del editor puedo mover esa ventana?",
        "에디터에서 오디오 믹서 창의 위치를 어디로 바꿔야 해?",
        "¿En qué parte del editor se cambia la posición de la ventana del mezclador?",
    ],
)
def test_unrelated_actions_and_known_paths_do_not_add_menu_discovery_terms(query):
    from services.registry.menu_path_search import localized_menu_path_terms
    from services.registry.task_search import _normalized

    assert not localized_menu_path_terms(_normalized(query))


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


@pytest.mark.parametrize("text", ["ΐ", "ΰ", "Μαΐου"])
@pytest.mark.parametrize("query_upper", [False, True])
def test_caseless_matching_normalizes_composition_after_case_folding(text, query_upper):
    from services.registry.task_search import SearchRequest, search_catalog

    query = text.upper() if query_upper else text
    description = text if query_upper else text.upper()
    entries = [{"name": "inspect", "group": None, "description": description, "kwargs": {}}]
    result = search_catalog(SearchRequest(query=query), entries)
    assert [entry["name"] for entry in result["results"]] == ["inspect"]


def test_unmatched_query_offers_direct_navigation():
    from services.registry.task_search import SearchRequest, search_catalog

    result = search_catalog(SearchRequest(query="未知の操作"), [])
    assert result["results"] == []
    assert result["catalog_language"] == "en"
    assert result["next_action"] == "provide_catalog_query"
    assert result["recommended_action"] == "browse_catalog"
    assert "catalog_query" in result["hint"]


def test_unmatched_normalized_query_offers_the_same_navigation():
    from services.registry.task_search import SearchRequest, search_catalog

    result = search_catalog(SearchRequest(query="未知の操作", catalog_query="unknown task"), [])
    assert result["next_action"] == "broaden_catalog_query"
    assert result["recommended_action"] == "browse_catalog"


@pytest.mark.parametrize(
    "query",
    [
        "재생 중인지 알려줘",
        "¿Sigue en marcha?",
        "هل يعمل الآن؟",
        "いま動いていますか",
        "UI が隠れている",
    ],
)
def test_navigation_reaches_live_operations_and_state_without_translating_query(query):
    from cli.main import cli

    runner = CliRunner()

    def page(*args):
        response = runner.invoke(cli, ["--format", "json", "tool", *args])
        assert response.exit_code == 0, response.output
        return json.loads(response.stdout)

    navigation = page("search", query)["navigation"]
    index = page("catalog")
    assert navigation["resources"] == index["resources"]
    assert {row["group"] for row in navigation["groups"]} == {
        row["name"] for row in index["groups"] if row["name"] is not None
    }
    scopes = {row["group"]: row["browse"]["group"] for row in navigation["groups"]}
    ui = page("catalog", "--group", scopes["ui"], "--limit", "20")
    assert any(tool["name"] == "manage_ugui" for tool in ui["tools"])
    diagnostics = page("catalog", "--tool", "manage_ugui", "--limit", "20")
    raycast = next(card for card in diagnostics["actions"] if card["action"] == "diagnose_raycast")
    assert set(raycast["required_parameters"]) == {"target", "screen_position"}
    assert "RectTransform" in raycast["purpose"] and "bottom left" in raycast["purpose"]
    assert raycast["evidence_limit"] in diagnostics["tool"]["description"]
    cards = page("catalog", "--tool", "manage_input", "--limit", "20")
    click = next(card for card in cards["actions"] if card["action"] == "ui_click")
    assert "does not test raycast occlusion" in click["evidence_limit"]
    assert click["required_parameters"] == ["target"]
    assert click["purpose"] in cards["tool"]["description"]
    assert click["evidence_limit"] in cards["tool"]["description"]
    state = page("catalog", "--group", navigation["resources"]["browse"]["group"], "--limit", "20")
    assert any(row["uri"] == "mcpforunity://editor/state" for row in state["resources"])


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
    server.tool(manage_shader.manage_shader)
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
