"""A changed file alone is not proof that a disconnected mutation succeeded."""

import base64
import importlib
from unittest.mock import AsyncMock

import pytest

refresh = importlib.import_module("services.tools.refresh_unity")
scripts = importlib.import_module("services.tools.manage_script")
structured = importlib.import_module("services.tools.script_apply_edits")


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", ["b" * 64, "a" * 64, None])
async def test_changed_sha_without_expected_result_is_not_verified(monkeypatch, observed):
    send = AsyncMock(return_value={"success": True, "data": {"sha256": observed}})
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    assert not await refresh.verify_edit_by_sha("Game@01234567", "Foo", "Assets", "a" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("observed,verified", [("b" * 64, True), ("c" * 64, False), (None, False)])
async def test_verification_requires_the_exact_expected_result(monkeypatch, observed, verified):
    send = AsyncMock(return_value={"success": True, "data": {"sha256": observed}})
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    assert (
        await refresh.verify_edit_by_sha(
            "Game@01234567", "Foo", "Assets", "a" * 64, expected_sha="b" * 64
        )
        is verified
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "structured"])
async def test_disconnected_public_edit_reports_unconfirmed_without_replay(monkeypatch, kind):
    async def disconnected(ctx, instance, command, params, *, verify_after_disconnect):
        return await verify_after_disconnect()

    for module in (scripts, structured):
        monkeypatch.setattr(
            module, "get_unity_instance_from_context", AsyncMock(return_value="Game@01234567")
        )
        monkeypatch.setattr(module, "send_mutation", disconnected)
        monkeypatch.setattr(
            module,
            "send_with_unity_instance",
            AsyncMock(return_value={"success": True, "data": {"sha256": "a" * 64}}),
        )
    monkeypatch.setattr(
        refresh.unity_transport,
        "send_with_unity_instance",
        AsyncMock(return_value={"success": True, "data": {"sha256": "b" * 64}}),
    )
    if kind == "text":
        result = await scripts.apply_text_edits(
            AsyncMock(),
            "Assets/Foo.cs",
            [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// requested"}],
            precondition_sha256="a" * 64,
        )
    else:
        result = await structured.script_apply_edits(
            AsyncMock(),
            "Foo",
            "Assets",
            [{"op": "insert_method", "replacement": "void Added() {}"}],
        )
    assert result["success"] is False
    assert result["data"]["execution_status"] == "unknown"
    assert result["data"]["retry_safe"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["create_script", "manage_script"])
@pytest.mark.parametrize(
    "requested,readback,created",
    [
        ("class Foo {}", {"contents": "class Foo {}"}, True),
        ("class Foo {}", {"contents": "class Existing {}"}, False),
        ("// 😀 café\r\nclass Foo {}\r\n", {"contents": "// 😀 café\r\nclass Foo {}\r\n"}, True),
        ("class Foo {}\r\n", {"contents": "class Foo {}\n"}, False),
        ("\ufeffclass Foo {}", {"contents": "class Foo {}"}, False),
        ("", {"contents": ""}, False),
        ("", {"contents": "class Foo {}\n"}, False),
        ("class Foo {}", {}, False),
        ("class Foo {}", {"contents": None}, False),
        (
            "class Foo {}",
            {
                "contentsEncoded": True,
                "encodedContents": base64.b64encode(b"class Foo {}").decode("ascii"),
            },
            True,
        ),
        ("class Foo {}", {"contentsEncoded": True, "encodedContents": "%%%"}, False),
        ("class Foo {}", {"contentsEncoded": True, "encodedContents": "/w=="}, False),
    ],
)
async def test_disconnected_creation_requires_expected_contents(
    monkeypatch, entrypoint, requested, readback, created
):
    # Given a lost create response and readback from the actual recovery boundary.
    disconnect = {"success": False, "error": "connection closed"}
    sender = AsyncMock(side_effect=[disconnect, {"success": True, "data": readback}])
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(scripts, "send_with_unity_instance", sender)
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", sender)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", AsyncMock(return_value=(True, 0)))

    # When either creation entrypoint attempts to verify its requested final result.
    if entrypoint == "create_script":
        response = await scripts.create_script(AsyncMock(), "Assets/Scripts/Foo.cs", requested)
    else:
        response = await scripts.manage_script(
            AsyncMock(), "create", "Foo", "Assets/Scripts", requested
        )

    # Then only exact content evidence confirms creation; uncertainty never replays it.
    assert response["success"] is created
    if not created:
        assert response == disconnect
    assert [call.args[3]["action"] for call in sender.await_args_list] == ["create", "read"]


@pytest.mark.asyncio
async def test_disconnected_default_template_creation_remains_unconfirmed(monkeypatch):
    disconnect = {"success": False, "error": "connection closed"}
    sender = AsyncMock(
        side_effect=[disconnect, {"success": True, "data": {"contents": "class Foo {}\n"}}]
    )
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(scripts, "send_with_unity_instance", sender)
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", sender)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", AsyncMock(return_value=(True, 0)))

    response = await scripts.manage_script(AsyncMock(), "create", "Foo", "Assets/Scripts")

    assert response == disconnect
    assert [call.args[3]["action"] for call in sender.await_args_list] == ["create", "read"]
