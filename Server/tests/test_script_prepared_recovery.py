"""Lost public edit replies require the exact native proposal prepared before dispatch."""

import asyncio
import hashlib
import importlib
from unittest.mock import AsyncMock

import pytest

scripts = importlib.import_module("services.tools.manage_script")
edits_tool = importlib.import_module("services.tools.script_apply_edits")
refresh = importlib.import_module("services.tools.refresh_unity")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proposal(original, candidate):
    return {
        "success": True,
        "data": {
            "preview": True,
            "complete": True,
            "truncated": False,
            "editsApplied": 0,
            "editsPrepared": 1,
            "scheduledRefresh": False,
            "original_contents": original,
            "new_contents": candidate,
            "original_sha256": digest(original),
            "sha256": digest(original),
            "candidate_sha256": digest(candidate),
            "candidate_bytes_sha256": digest(candidate),
            "encoding": "utf-8",
            "bom": False,
            "no_op": False,
            "path": "Assets/Foo.cs",
            "absolute_path": "C:/Unity/Assets/Foo.cs",
            "project_root": "C:/Unity",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["public_text", "legacy_text", "class", "mixed"])
@pytest.mark.parametrize("observed", ["expected", "unrelated", "newline_changed"])
@pytest.mark.parametrize("absolute", [False, True])
async def test_public_callbacks_verify_only_the_prepared_result(
    monkeypatch, tmp_path, entrypoint, observed, absolute
):
    original = "// 😀 한글\r\nclass Foo {}\r\n"
    text_result = original + "// café\r\n"
    class_result = "// 😀 한글\r\nclass Foo { int Added; }\r\n"
    final = class_result + ("// café\r\n" if entrypoint == "mixed" else "")
    if entrypoint in {"public_text", "legacy_text"}:
        final = text_result
    source = tmp_path / "Foo.cs"
    source.write_bytes(original.encode())
    calls = []
    preparations = []

    async def native(_sender, _instance, command, params, **_kwargs):
        assert command == "manage_script"
        action = params["action"]
        calls.append(action)
        current = source.read_bytes().decode()
        if action == "read":
            return {"success": True, "data": {"contents": current}}
        if action == "validate_edit":
            return {"success": True}
        if action.startswith("preview_"):
            candidate = text_result if action == "preview_text_edits" else final
            prepared = proposal(current, candidate)
            preparations.append(prepared)
            return prepared
        if action == "get_sha":
            return {"success": True, "data": {"sha256": digest(current)}}
        assert action in {"apply_text_edits", "edit"}, params
        # The callback must retain a scalar expected digest before dispatch, even
        # when a transport subsequently changes the returned preparation object.
        for prepared in preparations:
            prepared["data"]["candidate_sha256"] = digest("unrelated")
        candidate = text_result if action == "apply_text_edits" else final
        if observed == "unrelated":
            candidate = "// unrelated writer\r\n" + candidate
        elif observed == "newline_changed":
            candidate = candidate.replace("\r\n", "\n")
        source.write_bytes(candidate.encode())
        return {"success": False, "error": "connection closed"}

    for module in (scripts, edits_tool):
        monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
        monkeypatch.setattr(module, "send_with_unity_instance", native)
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", native)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", AsyncMock(return_value=(True, 0)))

    if entrypoint == "public_text":
        response = await scripts.apply_text_edits(
            AsyncMock(),
            "file:///C:/Unity/Assets/Foo.cs" if absolute else "Assets/Foo.cs",
            [{"startLine": 3, "startCol": 1, "endLine": 3, "endCol": 1, "newText": "// café\r\n"}],
            precondition_sha256=digest(original),
        )
    else:
        edits = []
        if entrypoint != "class":
            edits.append({"op": "append", "text": "// café\r\n"})
        if entrypoint in {"class", "mixed"}:
            edits.append(
                {
                    "op": "replace_class",
                    "className": "Foo",
                    "replacement": "class Foo { int Added; }",
                }
            )
        response = await edits_tool.script_apply_edits(
            AsyncMock(), "Foo", "C:/Unity/Assets" if absolute else "Assets", edits
        )

    assert response["success"] is (observed == "expected"), response
    if observed != "expected":
        assert response["data"]["execution_status"] == "unknown"
        assert response["data"]["retry_safe"] is False
    assert calls.count("apply_text_edits") == (0 if entrypoint == "class" else 1)
    assert calls.count("edit") == (
        1 if entrypoint == "class" or entrypoint == "mixed" and observed == "expected" else 0
    )
    if observed == "expected":
        assert source.read_bytes() == final.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unsupported", "invalid", "error", "cancelled"])
async def test_preparation_failure_keeps_unknown_or_cancellation_without_replay(
    monkeypatch, failure
):
    calls = []

    async def native(_sender, _instance, _command, params, **_kwargs):
        action = params["action"]
        calls.append(action)
        if action == "preview_text_edits":
            if failure == "cancelled":
                raise asyncio.CancelledError
            if failure == "error":
                raise ConnectionError("preview response lost")
            if failure == "invalid":
                result = proposal("class Foo {}", "// requested\nclass Foo {}")
                result["data"]["path"] = "Assets/Other.cs"
                return result
            return {"success": False, "error": "Unknown action: preview_text_edits"}
        if action == "apply_text_edits":
            return {"success": False, "error": "connection closed"}
        if action == "get_sha":
            return {"success": True, "data": {"sha256": digest("unrelated")}}
        raise AssertionError(params)

    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(scripts, "send_with_unity_instance", native)
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", native)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", AsyncMock(return_value=(True, 0)))
    invocation = scripts.apply_text_edits(
        AsyncMock(),
        "Assets/Foo.cs",
        [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// requested\n"}],
        precondition_sha256=digest("class Foo {}"),
    )
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await invocation
        assert calls == ["preview_text_edits"]
    else:
        response = await invocation
        assert response["success"] is False
        assert response["data"]["execution_status"] == "unknown"
        assert response["data"]["retry_safe"] is False
        assert calls.count("apply_text_edits") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["verification_error", "verification_cancelled", "wrong_project", "wrong_original"]
)
async def test_public_recovery_never_replays_after_failed_observation(monkeypatch, stage):
    original, candidate = "class Foo {}\r\n", "// requested\r\nclass Foo {}\r\n"
    calls = []

    async def native(_sender, _instance, _command, params, **_kwargs):
        action = params["action"]
        calls.append(action)
        if action == "preview_text_edits":
            result = proposal(original, candidate)
            if stage == "wrong_project":
                result["data"]["absolute_path"] = "C:/Other/Assets/Foo.cs"
                result["data"]["project_root"] = "C:/Other"
            if stage == "wrong_original":
                result = proposal("// competing writer\r\n" + original, candidate)
            return result
        if action == "apply_text_edits":
            # Precondition from the caller must survive preparation unchanged.
            assert params["precondition_sha256"] == digest(original)
            return {"success": False, "error": "connection closed"}
        assert action == "get_sha"
        if stage == "verification_cancelled":
            raise asyncio.CancelledError
        if stage == "verification_error":
            raise ConnectionError("verification unavailable")
        return {"success": True, "data": {"sha256": digest(candidate)}}

    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(scripts, "send_with_unity_instance", native)
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", native)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", AsyncMock(return_value=(True, 0)))
    invocation = scripts.apply_text_edits(
        AsyncMock(),
        "file:///C:/Unity/Assets/Foo.cs",
        [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// requested\r\n"}],
        precondition_sha256=digest(original),
    )
    if stage == "verification_cancelled":
        with pytest.raises(asyncio.CancelledError):
            await invocation
    else:
        result = await invocation
        assert result["success"] is False
        assert result["data"]["execution_status"] == "unknown"
        assert result["data"]["retry_safe"] is False
    assert calls.count("apply_text_edits") == 1
    if stage in {"wrong_project", "wrong_original"}:
        assert "get_sha" not in calls, (
            "An invalid preparation cannot authorize result verification."
        )
