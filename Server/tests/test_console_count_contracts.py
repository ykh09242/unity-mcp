import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize(
    "count,expected", [("all", None), ("*", None), (" ALL ", None), (None, 10), ("5", 5), (0, 0)]
)
def test_console_explicit_all_and_default_count(monkeypatch, count, expected):
    module = importlib.import_module("services.tools.read_console")
    send = AsyncMock(return_value={"success": True, "data": []})
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    result = asyncio.run(module.read_console(SimpleNamespace(), count=count))
    assert result["success"] is True
    assert send.call_args.args[3]["count"] == expected
