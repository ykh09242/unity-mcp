"""Read-only proposals have complete, verifiable Unity target identity."""
import hashlib
import importlib
from unittest.mock import AsyncMock

import pytest

tools = importlib.import_module("services.tools.script_apply_edits")


def prepared(original="class Foo {}\n", candidate="class Foo { void M() {} }\n"):
    sha = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    return {"success": True, "data": {
        "preview": True, "complete": True, "truncated": False,
        "path": "Assets/Foo.cs", "uri": "mcpforunity://path/Assets/Foo.cs",
        "project_root": "C:/UnityProject", "absolute_path": "C:/UnityProject/Assets/Foo.cs",
        "original_contents": original, "new_contents": candidate,
        "original_sha256": sha(original), "candidate_sha256": sha(candidate),
        "candidate_bytes_sha256": sha(candidate), "sha256": sha(original),
        "encoding": "utf-8", "bom": False, "no_op": original == candidate,
        "editsApplied": 0, "editsPrepared": 0 if original == candidate else 1, "scheduledRefresh": False,
    }}


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(tools, "get_unity_instance_from_context", AsyncMock(return_value="Unity@abc"))
    reader = AsyncMock(return_value=prepared())
    writer = AsyncMock()
    monkeypatch.setattr(tools, "send_with_unity_instance", reader)
    monkeypatch.setattr(tools, "send_mutation", writer)
    return reader, writer


@pytest.mark.asyncio
async def test_structured_preview_is_read_only_complete_handoff(wire):
    reader, writer = wire
    response = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert response["success"] is True
    writer.assert_not_awaited()
    params = reader.await_args.args[3]
    assert params["action"] == "preview_edit" and params["options"]["preview"] is True
    data = response["data"]
    assert data["absolute_path"] == "C:/UnityProject/Assets/Foo.cs"
    assert data["unity_instance"] == "Unity@abc"
    assert data["sha256"] == data["original_sha256"]
    assert data["native_apply"]["status"] == "verification_required"
    assert data["diff"] and data["new_contents"] == prepared()["data"]["new_contents"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("complete", False), ("truncated", True), ("new_contents", "truncated"),
    ("candidate_sha256", "0" * 64), ("candidate_bytes_sha256", "0" * 64),
    ("absolute_path", "C:/WrongProject/Assets/Foo.cs"), ("path", "Assets/../Foo.cs"),
    ("encoding", "utf-16"), ("bom", True), ("scheduledRefresh", True), ("editsApplied", 1),
    ("editsPrepared", 0), ("editsPrepared", -1), ("editsPrepared", True),
])
async def test_incomplete_or_inconsistent_preparation_cannot_be_used(wire, field, value):
    reader, writer = wire
    response = prepared()
    response["data"][field] = value
    reader.return_value = response
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert result["success"] is False
    assert result["code"] == "invalid_preview"
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_combined_source_candidate_limit_and_noop_count(wire):
    reader, _ = wire
    reader.return_value = prepared("x" * (512 * 1024), "y" * (512 * 1024 + 1))
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert result["success"] is False and result["code"] == "invalid_preview"
    response = prepared("same", "same")
    response["data"]["editsPrepared"] = 1
    reader.return_value = response
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert result["success"] is False and result["code"] == "invalid_preview"


@pytest.mark.asyncio
async def test_public_text_preview_uses_readonly_alias(wire, monkeypatch):
    module = importlib.import_module("services.tools.manage_script")
    reader, _ = wire
    writer = AsyncMock()
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="Unity@abc"))
    monkeypatch.setattr(module, "send_with_unity_instance", reader)
    monkeypatch.setattr(module, "send_mutation", writer)
    response = await module.apply_text_edits(AsyncMock(), "Assets/Foo.cs", [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// next\n"}], options={"preview": True})
    assert response["success"] is True
    assert reader.await_args.args[3]["action"] == "preview_text_edits"
    writer.assert_not_awaited()
    reader.return_value = {"success": False, "error": "Unknown action: preview_text_edits"}
    response = await module.apply_text_edits(AsyncMock(), "Assets/Foo.cs", [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// next\n"}], options={"preview": True})
    assert response["success"] is False and response["error"].startswith("Unknown action:")
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_and_noop_proposals_do_not_offer_local_apply(wire, monkeypatch):
    from core.config import config
    reader, _ = wire
    monkeypatch.setattr(config, "http_remote_hosted", True)
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert result["data"]["native_apply"]["status"] == "unavailable_remote"
    reader.return_value = prepared("class Foo {}\n", "class Foo {}\n")
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert result["data"]["native_apply"]["status"] == "not_needed"


@pytest.mark.asyncio
async def test_preview_disconnect_is_not_recovered_as_mutation_success(wire):
    reader, writer = wire
    reader.return_value = {"success": False, "error": "connection closed"}
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert result == {"success": False, "error": "connection closed"}
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [True, False])
async def test_older_unity_cannot_treat_preview_as_a_write(wire, structured):
    reader, writer = wire
    seen = []
    async def old_unity(*args, **kwargs):
        action = args[3]["action"]
        seen.append(action)
        if action == "read":
            return {"success": True, "data": {"contents": "class Foo {}\n"}}
        assert action not in ("edit", "apply_text_edits", "update")
        return {"success": False, "error": f"Unknown action: {action}"}
    reader.side_effect = old_unity
    edits = [{"op": "insert_method", "replacement": "void M() {}"}] if structured else [{"op": "append", "text": "// next\n"}]
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits, {"preview": True})
    assert result["success"] is False and "Unknown action:" in result["error"]
    assert seen == (["preview_edit"] if structured else ["read", "preview_text_edits"])
    writer.assert_not_awaited()
