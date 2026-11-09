"""A changed file alone is not proof that a disconnected mutation succeeded."""

import base64
import importlib
import hashlib
from unittest.mock import AsyncMock

import pytest

refresh = importlib.import_module("services.tools.refresh_unity")
scripts = importlib.import_module("services.tools.manage_script")
structured = importlib.import_module("services.tools.script_apply_edits")


@pytest.mark.asyncio
@pytest.mark.parametrize("structured_first", [False, True])
@pytest.mark.parametrize(
    "failure", ["structured", "text_refresh", "structured_refresh", "transport"]
)
async def test_mixed_failure_retains_committed_bytes(
    monkeypatch, tmp_path, structured_first, failure
):
    # Given a real source file behind the native transport boundary.
    source = tmp_path / "Foo.cs"
    source.write_bytes(b"class Foo {}\n")
    calls = []

    async def native(_sender, _instance, _command, params):
        calls.append(params["action"])
        match params["action"]:
            case "validate_edit":
                return {"success": True}
            case "read":
                return {"success": True, "data": {"contents": source.read_text()}}
            case "get_sha":
                return {
                    "success": True,
                    "data": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
                }
        raise AssertionError(params)

    async def mutate(_ctx, _instance, _command, params, **_kwargs):
        action = params["action"]
        calls.append(action)
        if action == "edit" and failure == "transport":
            raise ConnectionError("lost structured result")
        if action == "edit" and failure == "structured":
            return {
                "success": False,
                "error": "Method Missing not found",
                "data": {"source_saved": False},
            }
        if action == "apply_text_edits":
            source.write_bytes(source.read_bytes() + params["edits"][0]["newText"].encode())
        else:
            source.write_bytes(source.read_bytes().replace(b"class Foo", b"class Changed"))
        refresh_failed = failure == (
            "text_refresh" if action == "apply_text_edits" else "structured_refresh"
        )
        return {
            "success": not refresh_failed,
            "error": "injected refresh failure" if refresh_failed else None,
            "data": {
                "source_saved": True,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "refresh_status": "failed" if refresh_failed else "requested",
            },
        }

    monkeypatch.setattr(
        structured, "get_unity_instance_from_context", AsyncMock(return_value="Game@01234567")
    )
    monkeypatch.setattr(structured, "send_with_unity_instance", native)
    monkeypatch.setattr(structured, "send_mutation", mutate)
    edits = [
        {"op": "append", "text": "// appended\n"},
        {"op": "replace_method", "methodName": "Missing", "replacement": "void Missing() {}"},
    ]
    if structured_first:
        edits.reverse()

    # When a later phase fails, even if the original request lists the structured edit first.
    response = await structured.script_apply_edits(AsyncMock(), "Foo", "Assets", edits)

    # Then the public receipt describes the committed prefix without inviting a replay.
    expected = (
        b"class Changed {}\n// appended\n"
        if failure == "structured_refresh"
        else b"class Foo {}\n// appended\n"
    )
    assert source.read_bytes() == expected
    assert response["success"] is False
    data = response["data"]
    assert data["source_saved"] is True
    assert data["outcome"] == "partial"
    assert data["path"] == "Assets/Foo.cs"
    assert data["committed_sha256"] == hashlib.sha256(expected).hexdigest()
    assert data["failed_phase"] == ("text" if failure == "text_refresh" else "structured")
    assert data["retry_safe"] is False
    assert data["recovery_action"] == (
        "refresh_then_resume_remaining"
        if failure == "text_refresh"
        else "refresh_only"
        if failure == "structured_refresh"
        else "inspect_then_resume_remaining"
    )
    assert data["phase_results"][0]["response"]["data"]["source_saved"] is True
    assert calls.count("apply_text_edits") == 1
    assert calls.count("edit") == (0 if failure == "text_refresh" else 1)


@pytest.mark.asyncio
async def test_mixed_noop_before_failure_does_not_claim_publication(monkeypatch, tmp_path):
    # Given an unchanged on-disk source and the native editor's explicit no-op receipt.
    source = tmp_path / "Foo.cs"
    original = b"class Foo {}\n"
    source.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(structured, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(
        structured,
        "send_with_unity_instance",
        AsyncMock(
            side_effect=[
                {"success": True},
                {"success": True, "data": {"contents": original.decode()}},
            ]
        ),
    )
    monkeypatch.setattr(
        structured,
        "send_mutation",
        AsyncMock(
            side_effect=[
                {"success": True, "data": {"no_op": True, "editsApplied": 0, "sha256": digest}},
                {"success": False, "error": "Method Missing not found"},
            ]
        ),
    )
    # When a text identity edit is followed by a structural rejection.
    response = await structured.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [
            {"op": "regex_replace", "pattern": "Foo", "replacement": "Foo"},
            {"op": "delete_method", "methodName": "Missing"},
        ],
    )
    # Then success without publication must not become a committed-prefix claim.
    assert source.read_bytes() == original
    assert response["success"] is False
    assert response["data"].get("source_saved") is not True
    assert not response["data"].get("committed_phases")
    assert response["data"]["phase_results"][0]["response"]["data"]["no_op"] is True


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
