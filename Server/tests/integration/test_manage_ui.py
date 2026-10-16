"""Integration tests for manage_ui tool."""

import asyncio
import base64

import pytest

from .test_helpers import DummyContext
import services.tools.manage_ui as manage_ui_mod


def run_async(coro):
    """Simple wrapper to run a coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


SAMPLE_UXML = """<ui:UXML xmlns:ui="UnityEngine.UIElements">
    <ui:Label text="Hello World" />
</ui:UXML>"""

SAMPLE_USS = """.label {
    font-size: 24px;
    color: white;
}"""


class TestVisualTreeBudgets:
    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("max_depth", -1),
            ("max_depth", 25),
            ("max_depth", True),
            ("max_nodes", 0),
            ("max_nodes", 1001),
            ("max_nodes", 1.5),
            ("max_evaluations", 0),
            ("max_evaluations", 20001),
            ("max_characters", 0),
            ("max_characters", 262145),
            ("max_characters", []),
        ],
    )
    def test_invalid_budget_refuses_before_instance_or_dispatch(self, monkeypatch, key, value):
        async def unexpected(*_args, **_kwargs):
            raise AssertionError("invalid tree budget reached I/O")

        monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", unexpected)
        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", unexpected)
        response = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(), action="get_visual_tree", target="123", **{key: value}
            )
        )
        assert response["success"] is False
        assert key in response["message"]

    def test_all_budgets_forward_zero_and_preserve_partial_evidence(self, monkeypatch):
        sent = []
        native = {
            "success": True,
            "data": {
                "tree": {"truncated": True, "childCount": 8},
                "traversal": {
                    "returned_nodes": 1,
                    "evaluations": 2,
                    "characters": 9,
                    "truncated": True,
                    "complete": False,
                    "reasons": ["max_depth"],
                },
            },
        }

        async def instance(_ctx):
            return None

        async def send(_func, _instance, _cmd, params, **_kwargs):
            sent.append(params)
            return native

        monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", instance)
        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", send)
        response = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="get_visual_tree",
                target="123",
                max_depth=0,
                max_nodes=3,
                max_evaluations=5,
                max_characters=20,
            )
        )
        assert response == native
        assert {
            k: sent[0][k] for k in ("maxDepth", "maxNodes", "maxEvaluations", "maxCharacters")
        } == {"maxDepth": 0, "maxNodes": 3, "maxEvaluations": 5, "maxCharacters": 20}


class TestManageUIPathValidation:
    """Tests for path validation logic."""

    def test_create_rejects_path_outside_assets(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create",
                path="NotAssets/UI/Test.uxml",
                contents=SAMPLE_UXML,
            )
        )

        assert resp["success"] is False
        assert "Assets/" in resp["message"]

    def test_create_rejects_traversal(self, monkeypatch):
        async def fake_send(*_args, **_kwargs):
            return {"success": True}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create",
                path="Assets/../etc/passwd.uxml",
                contents=SAMPLE_UXML,
            )
        )

        assert resp["success"] is False
        # Path normalization resolves ".." so it either fails traversal or Assets/ check
        assert "traversal" in resp["message"] or "Assets/" in resp["message"]

    def test_create_rejects_invalid_extension(self, monkeypatch):
        async def fake_send(*_args, **_kwargs):
            return {"success": True}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create",
                path="Assets/UI/Test.cs",
                contents="some content",
            )
        )

        assert resp["success"] is False
        assert ".uxml or .uss" in resp["message"]

    def test_create_accepts_uxml_extension(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "Created"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create",
                path="Assets/UI/Menu.uxml",
                contents=SAMPLE_UXML,
            )
        )

        assert resp["success"] is True
        assert captured["params"]["path"] == "Assets/UI/Menu.uxml"

    def test_create_accepts_uss_extension(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "Created"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create",
                path="Assets/UI/Styles.uss",
                contents=SAMPLE_USS,
            )
        )

        assert resp["success"] is True
        assert captured["params"]["path"] == "Assets/UI/Styles.uss"


class TestManageUIContentsEncoding:
    """Tests for base64 content encoding."""

    def test_create_encodes_contents_as_base64(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "Created"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create",
                path="Assets/UI/Test.uxml",
                contents=SAMPLE_UXML,
            )
        )

        params = captured["params"]
        assert params["contentsEncoded"] is True
        decoded = base64.b64decode(params["encodedContents"]).decode("utf-8")
        assert decoded == SAMPLE_UXML
        # Raw contents should NOT be in params (only encoded)
        assert "contents" not in params

    def test_update_encodes_contents_as_base64(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "Updated"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="update",
                path="Assets/UI/Test.uss",
                contents=SAMPLE_USS,
            )
        )

        params = captured["params"]
        assert params["contentsEncoded"] is True
        decoded = base64.b64decode(params["encodedContents"]).decode("utf-8")
        assert decoded == SAMPLE_USS


class TestManageUIActionRouting:
    """Tests for action-based parameter routing."""

    def test_read_uses_non_mutation_path(self, monkeypatch):
        captured = {}

        async def fake_send(_func, _instance, cmd, params, **kwargs):
            captured["cmd"] = cmd
            captured["params"] = params
            return {"success": True, "data": {"contents": "test"}}

        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="read",
                path="Assets/UI/Test.uxml",
            )
        )

        assert captured["cmd"] == "manage_ui"
        assert captured["params"]["action"] == "read"

    def test_create_uses_mutation_path(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, cmd, _params, **kwargs):
            captured["cmd"] = cmd
            return {"success": True, "message": "Created"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create",
                path="Assets/UI/Test.uxml",
                contents=SAMPLE_UXML,
            )
        )

        assert captured["cmd"] == "manage_ui"

    def test_attach_ui_document_params(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "Attached"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="attach_ui_document",
                target="MyCanvas",
                source_asset="Assets/UI/Menu.uxml",
                panel_settings="Assets/UI/PanelSettings.asset",
                sort_order=5,
            )
        )

        params = captured["params"]
        assert params["action"] == "attach_ui_document"
        assert params["target"] == "MyCanvas"
        assert params["sourceAsset"] == "Assets/UI/Menu.uxml"
        assert params["panelSettings"] == "Assets/UI/PanelSettings.asset"
        assert params["sortOrder"] == 5

    def test_create_panel_settings_params(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "Created"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="create_panel_settings",
                path="Assets/UI/MyPanel.asset",
                scale_mode="ScaleWithScreenSize",
                reference_resolution={"width": 1920, "height": 1080},
            )
        )

        params = captured["params"]
        assert params["action"] == "create_panel_settings"
        assert params["scaleMode"] == "ScaleWithScreenSize"
        assert params["referenceResolution"] == {"width": 1920, "height": 1080}

    def test_get_visual_tree_params(self, monkeypatch):
        captured = {}

        async def fake_send(_func, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "data": {"tree": {}}}

        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="get_visual_tree",
                target="UIRoot",
                max_depth=5,
            )
        )

        params = captured["params"]
        assert params["action"] == "get_visual_tree"
        assert params["target"] == "UIRoot"
        assert params["maxDepth"] == 5

    def test_ping_uses_non_mutation_path(self, monkeypatch):
        captured = {}

        async def fake_send(_func, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "pong"}

        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="ping",
            )
        )

        assert resp["success"] is True
        assert captured["params"]["action"] == "ping"


class TestManageUINoneRemoval:
    """Tests that None values are properly excluded from params."""

    def test_none_params_excluded(self, monkeypatch):
        captured = {}

        async def fake_send(_func, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "data": {}}

        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", fake_send)

        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="read",
                path="Assets/UI/Test.uxml",
                # All other params are None
            )
        )

        params = captured["params"]
        assert "target" not in params
        assert "sourceAsset" not in params
        assert "panelSettings" not in params
        assert "sortOrder" not in params
        assert "scaleMode" not in params
        assert "maxDepth" not in params


class TestManageUIReadResponse:
    """Tests for read response handling."""

    def test_read_decodes_base64_response(self, monkeypatch):
        encoded = base64.b64encode(SAMPLE_UXML.encode("utf-8")).decode("utf-8")

        async def fake_send(*_args, **_kwargs):
            return {
                "success": True,
                "data": {
                    "path": "Assets/UI/Test.uxml",
                    "contents": SAMPLE_UXML,
                    "encodedContents": encoded,
                    "contentsEncoded": True,
                },
            }

        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="read",
                path="Assets/UI/Test.uxml",
            )
        )

        assert resp["success"] is True
        data = resp["data"]
        assert data["contents"] == SAMPLE_UXML
        assert "encodedContents" not in data
        assert "contentsEncoded" not in data


class TestManageUIRenderUI:
    """Tests for render_ui action."""

    @pytest.mark.parametrize("cancel_capture", [False, True])
    def test_capture_resume_and_cancel_forward_opaque_id_without_start_settings(
        self, monkeypatch, cancel_capture
    ):
        captured = []
        capture_id = "aeac918628bd4a56844254c294d4ce28"

        async def fake_send(_ctx, _instance, command, params, **_kwargs):
            captured.append((command, params))
            return {
                "success": True,
                "data": {"capture_id": capture_id, "cancelled": cancel_capture},
            }

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        response = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="render_ui",
                capture_id=capture_id,
                cancel_capture=cancel_capture,
            )
        )

        assert captured == [
            (
                "manage_ui",
                {
                    "action": "render_ui",
                    "capture_id": capture_id,
                    "cancel_capture": cancel_capture,
                },
            )
        ]
        assert response == {
            "success": True,
            "data": {"capture_id": capture_id, "cancelled": cancel_capture},
        }

    def test_render_ui_routes_params(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {
                "success": True,
                "message": "Rendered",
                "data": {"path": "Assets/Screenshots/test.png"},
            }

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="render_ui",
                target="UIRoot",
                width=1280,
                height=720,
                include_image=True,
                max_resolution=480,
                screenshot_file_name="my-preview",
            )
        )
        assert resp["success"] is True
        p = captured["params"]
        assert p["action"] == "render_ui"
        assert p["target"] == "UIRoot"
        assert p["width"] == 1280
        assert p["height"] == 720
        assert p["include_image"] is True
        assert p["max_resolution"] == 480
        assert p["file_name"] == "my-preview"

    def test_render_ui_none_excluded(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "ok"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)
        run_async(manage_ui_mod.manage_ui(ctx=DummyContext(), action="render_ui", target="X"))
        p = captured["params"]
        for k in ("width", "height", "include_image", "max_resolution", "file_name"):
            assert k not in p


class TestManageUILinkStylesheet:
    """Tests for link_stylesheet action."""

    def test_link_stylesheet_routes_params(self, monkeypatch):
        captured = {}

        async def fake_send(_ctx, _instance, _cmd, params, **kwargs):
            captured["params"] = params
            return {"success": True, "message": "Linked"}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)

        resp = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="link_stylesheet",
                path="Assets/UI/Menu.uxml",
                stylesheet="Assets/UI/Styles.uss",
            )
        )
        assert resp["success"] is True
        p = captured["params"]
        assert p["action"] == "link_stylesheet"
        assert p["path"] == "Assets/UI/Menu.uxml"
        assert p["stylesheet"] == "Assets/UI/Styles.uss"
        for k in ("width", "height", "include_image"):
            assert k not in p


class TestManageUIComponentSelection:
    @pytest.mark.parametrize("component_type", ["auto", "ui_document", "panel_renderer"])
    @pytest.mark.parametrize(
        "action",
        [
            "attach_ui_document",
            "detach_ui_document",
            "get_visual_tree",
            "modify_visual_element",
            "render_ui",
        ],
    )
    def test_component_selection_reaches_each_unity_action(
        self, monkeypatch, component_type, action
    ):
        captured = []

        async def fake_send(_context_or_sender, _instance, command, params, **kwargs):
            captured.append((command, params))
            return {"success": True, "data": {"componentType": component_type}}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)
        monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", fake_send)
        response = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action=action,
                target="UIRoot",
                source_asset="Assets/UI/Main.uxml",
                component_type=component_type,
            )
        )
        assert response["success"] is True
        assert captured == [
            (
                "manage_ui",
                {
                    "action": action,
                    "target": "UIRoot",
                    "componentType": component_type,
                    "sourceAsset": "Assets/UI/Main.uxml",
                },
            )
        ]

    def test_omitted_selector_preserves_existing_request_shape(self, monkeypatch):
        captured = []

        async def fake_send(_ctx, _instance, _command, params, **kwargs):
            captured.append(params)
            return {"success": True}

        monkeypatch.setattr(manage_ui_mod, "send_mutation", fake_send)
        run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="attach_ui_document",
                target="UIRoot",
                source_asset="Assets/UI/Main.uxml",
            )
        )
        assert "componentType" not in captured[0]

    def test_invalid_selector_is_rejected_before_transport(self, monkeypatch):
        async def unexpected_send(*args, **kwargs):
            pytest.fail("Invalid component selection must not reach Unity.")

        monkeypatch.setattr(manage_ui_mod, "send_mutation", unexpected_send)
        response = run_async(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(),
                action="render_ui",
                target="UIRoot",
                component_type="unknown",
            )
        )
        assert response["success"] is False
        assert "component_type" in response["message"]


@pytest.mark.parametrize(
    "selectors,expected",
    [
        ({"element_path": ""}, {"elementPath": ""}),
        ({"element_path": "/1/0"}, {"elementPath": "/1/0"}),
        (
            {"element_id": "unsaved:Fixture::Panel/name%3Alabel"},
            {"elementId": "unsaved:Fixture::Panel/name%3Alabel"},
        ),
        ({"strict_name": False}, {"strictName": False}),
        ({"strict_name": True}, {"strictName": True}),
        ({}, {}),
    ],
)
def test_mutation_selector_forwarding_preserves_empty_false_and_native_failure(
    monkeypatch, selectors, expected
):
    captured = []
    failure = {
        "success": False,
        "error": "Controlled native selector refusal",
        "data": {"modified": False},
    }

    async def instance(_ctx):
        return None

    async def send(_ctx, _instance, command, params, **_kwargs):
        assert command == "manage_ui"
        captured.append(params)
        return dict(failure)

    monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(manage_ui_mod, "send_mutation", send)
    response = run_async(
        manage_ui_mod.manage_ui(
            DummyContext(),
            action="modify_visual_element",
            target="123",
            element_name="shared",
            text="changed",
            **selectors,
        )
    )
    assert response == failure
    assert len(captured) == 1
    wire = captured[0]
    for key in ("elementPath", "elementId", "strictName"):
        assert (key in wire) == (key in expected)
        if key in expected:
            assert wire[key] == expected[key]


@pytest.mark.parametrize(
    "budgets",
    [
        {},
        {"max_depth": 0, "max_pairs": 0, "max_findings": 1},
        {"max_depth": None, "max_pairs": None},
    ],
)
def test_diagnose_read_transport_and_strict_peer_admission(monkeypatch, budgets):
    from tests.test_ui_layout_diagnostics import sample

    sent = []
    native = sample()
    for key, value in budgets.items():
        if value is not None:
            native["traversal"][key] = value

    async def instance(_ctx):
        return None

    async def read(_func, _instance, command, params, **_kwargs):
        sent.append((command, params))
        return {"success": True, "data": native, "untrusted_extra": "not published"}

    async def mutation(*_args, **_kwargs):
        raise AssertionError("diagnostics entered mutation transport")

    monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(manage_ui_mod, "send_with_unity_instance", read)
    monkeypatch.setattr(manage_ui_mod, "send_mutation", mutation)
    result = run_async(
        manage_ui_mod.manage_ui(DummyContext(), action="diagnose", target="123", **budgets)
    )
    assert result == {"success": True, "data": native}
    assert len(sent) == 1 and sent[0][0] == "manage_ui"
    for public, wire in (
        ("max_depth", "maxDepth"),
        ("max_pairs", "maxPairs"),
        ("max_findings", "maxFindings"),
    ):
        assert (wire in sent[0][1]) == (budgets.get(public) is not None)
        if wire in sent[0][1]:
            assert sent[0][1][wire] == budgets[public]
