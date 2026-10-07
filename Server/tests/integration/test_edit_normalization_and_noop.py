import pytest

from .test_helpers import DummyContext, DummyMCP, setup_script_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("source_line, expected_column", [("abc", 3), ("😀x", 2)])
async def test_normalizes_lsp_and_index_ranges(monkeypatch, source_line, expected_column):
    tools = setup_script_tools()
    apply = tools["apply_text_edits"]
    calls = []

    async def fake_send(cmd, params, **kwargs):
        if params.get("action") == "read":
            return {"success": True, "data": {"contents": "\n" * 10 + source_line + "\n"}}
        calls.append(params)
        return {"success": True}

    # Patch the send_command_with_retry function at the module level where it's imported
    import transport.legacy.unity_connection

    monkeypatch.setattr(
        transport.legacy.unity_connection,
        "async_send_command_with_retry",
        fake_send,
    )
    # No need to patch tools.manage_script; it calls unity_connection.send_command_with_retry

    # LSP-style
    edits = [
        {
            "range": {"start": {"line": 10, "character": 2}, "end": {"line": 10, "character": 2}},
            "newText": "// lsp\n",
        }
    ]
    response = await apply(
        DummyContext(),
        uri="mcpforunity://path/Assets/Scripts/F.cs",
        edits=edits,
        precondition_sha256="x",
    )
    assert response["success"] is True
    p = calls[-1]
    e = p["edits"][0]
    assert p["action"] == "apply_text_edits"
    assert e == {
        "startLine": 11,
        "startCol": expected_column,
        "endLine": 11,
        "endCol": expected_column,
        "newText": "// lsp\n",
    }

    # Index pair
    calls.clear()
    edits = [{"range": [0, 0], "text": "// idx\n"}]
    # fake read to provide contents length

    async def fake_read(cmd, params, **kwargs):
        if params.get("action") == "read":
            return {"success": True, "data": {"contents": "hello\n"}}
        calls.append(params)
        return {"success": True}

    # Override unity_connection for this read normalization case
    monkeypatch.setattr(
        transport.legacy.unity_connection,
        "async_send_command_with_retry",
        fake_read,
    )
    response = await apply(
        DummyContext(),
        uri="mcpforunity://path/Assets/Scripts/F.cs",
        edits=edits,
        precondition_sha256="x",
    )
    assert response["success"] is True
    assert calls[-1]["action"] == "apply_text_edits"
    assert calls[-1]["edits"] == [
        {
            "startLine": 1,
            "startCol": 1,
            "endLine": 1,
            "endCol": 1,
            "newText": "// idx\n",
        }
    ]


@pytest.mark.asyncio
async def test_noop_evidence_shape(monkeypatch):
    tools = setup_script_tools()
    apply = tools["apply_text_edits"]
    # Route response from Unity indicating no-op

    async def fake_send(cmd, params, **kwargs):
        return {
            "success": True,
            "data": {"no_op": True, "evidence": {"reason": "identical_content"}},
        }

    # Patch the send_command_with_retry function at the module level where it's imported
    import transport.legacy.unity_connection

    monkeypatch.setattr(
        transport.legacy.unity_connection,
        "async_send_command_with_retry",
        fake_send,
    )
    # No need to patch tools.manage_script; it calls unity_connection.send_command_with_retry

    resp = await apply(
        DummyContext(),
        uri="mcpforunity://path/Assets/Scripts/F.cs",
        edits=[{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": ""}],
        precondition_sha256="x",
    )
    assert resp["success"] is True
    assert resp.get("data", {}).get("no_op") is True


@pytest.mark.asyncio
async def test_atomic_multi_span_and_relaxed(monkeypatch):
    tools_text = setup_script_tools()
    apply_text = tools_text["apply_text_edits"]
    # Fake send for read and write; verify atomic applyMode and validate=relaxed passes through
    sent = {}

    async def fake_send(cmd, params, **kwargs):
        if params.get("action") == "read":
            return {
                "success": True,
                "data": {"contents": "public class C{\nvoid M(){ int x=2; }\n}\n"},
            }
        sent.setdefault("calls", []).append(params)
        return {"success": True}

    # Patch the send_command_with_retry function at the module level where it's imported
    import transport.legacy.unity_connection

    monkeypatch.setattr(
        transport.legacy.unity_connection,
        "async_send_command_with_retry",
        fake_send,
    )

    edits = [
        {"startLine": 2, "startCol": 14, "endLine": 2, "endCol": 15, "newText": "3"},
        {"startLine": 3, "startCol": 2, "endLine": 3, "endCol": 2, "newText": "// tail\n"},
    ]
    resp = await apply_text(
        DummyContext(),
        uri="mcpforunity://path/Assets/Scripts/C.cs",
        edits=edits,
        precondition_sha256="sha",
        options={"validate": "relaxed", "applyMode": "atomic"},
    )
    assert resp["success"] is True
    # Last manage_script call should include options with applyMode atomic and validate relaxed
    last = sent["calls"][-1]
    assert last.get("options", {}).get("applyMode") == "atomic"
    assert last.get("options", {}).get("validate") == "relaxed"
