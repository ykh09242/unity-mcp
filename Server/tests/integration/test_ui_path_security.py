import importlib
from unittest.mock import AsyncMock

import pytest

ui = importlib.import_module("services.tools.manage_ui")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "read", "update", "delete", "link_stylesheet"])
@pytest.mark.parametrize(
    "path",
    [
        "Assets//tmp/file.uxml",
        "Assets/C:/file.uxml",
        "Assets/../file.uxml",
        "Assets/\\tmp/file.uxml",
    ],
)
async def test_rooted_paths_never_reach_unity(monkeypatch, action, path):
    mutation, read = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ui, "send_mutation", mutation)
    monkeypatch.setattr(ui, "send_with_unity_instance", read)
    result = await ui.manage_ui(AsyncMock(), action, path=path, stylesheet="Assets/UI/a.uss")
    assert not result["success"]
    mutation.assert_not_called()
    read.assert_not_called()


@pytest.mark.asyncio
async def test_link_forwards_canonical_paths_and_validates_stylesheet(monkeypatch):
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(ui, "send_mutation", send)
    result = await ui.manage_ui(
        AsyncMock(), "link_stylesheet", path="assets\\UI\\a.uxml", stylesheet="assets\\UI\\a.uss"
    )
    assert result["success"]
    params = send.call_args.args[3]
    assert params["path"] == "Assets/UI/a.uxml"
    assert params["stylesheet"] == "Assets/UI/a.uss"
    send.reset_mock()
    result = await ui.manage_ui(
        AsyncMock(), "link_stylesheet", path="Assets/UI/a.uxml", stylesheet="Assets//tmp/a.uss"
    )
    assert not result["success"]
    send.assert_not_called()
