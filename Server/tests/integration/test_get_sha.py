import pytest

from .test_helpers import DummyContext, setup_script_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("bytes_sha256", [None, "b" * 64])
async def test_get_sha_param_shape_and_routing(monkeypatch, bytes_sha256):
    tools = setup_script_tools()
    get_sha = tools["get_sha"]

    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["cmd"] = cmd
        captured["params"] = params
        response = {
            "success": True,
            "data": {
                "sha256": "abc",
                "lengthBytes": 1,
                "lastModifiedUtc": "2020-01-01T00:00:00Z",
                "uri": "mcpforunity://path/Assets/Scripts/A.cs",
                "path": "Assets/Scripts/A.cs",
            },
        }
        if bytes_sha256 is not None:
            response["data"]["bytes_sha256"] = bytes_sha256
        return response

    # Patch the send_command_with_retry function at the module level where it's imported
    import transport.legacy.unity_connection

    monkeypatch.setattr(
        transport.legacy.unity_connection,
        "async_send_command_with_retry",
        fake_send,
    )
    # No need to patch tools.manage_script; it now calls unity_connection.send_command_with_retry

    resp = await get_sha(DummyContext(), uri="mcpforunity://path/Assets/Scripts/A.cs")
    assert captured["cmd"] == "manage_script"
    assert captured["params"]["action"] == "get_sha"
    assert captured["params"]["name"] == "A"
    assert captured["params"]["path"].endswith("Assets/Scripts")
    assert resp["success"] is True
    assert resp["data"] == {"sha256": "abc", "bytes_sha256": bytes_sha256, "lengthBytes": 1}
