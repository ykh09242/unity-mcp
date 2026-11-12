import importlib
from unittest.mock import AsyncMock

import pytest

search = importlib.import_module("services.tools.find_in_file")


@pytest.mark.asyncio
async def test_search_returns_the_same_read_snapshot_identities(monkeypatch):
    read = AsyncMock(
        return_value={
            "success": True,
            "data": {"contents": "class Test {}", "sha256": "a" * 64, "bytes_sha256": "b" * 64},
        }
    )
    monkeypatch.setattr(
        search, "get_unity_instance_from_context", AsyncMock(return_value="Editor@project")
    )
    monkeypatch.setattr(search, "send_with_unity_instance", read)
    result = await search.find_in_file(None, "Assets/Test.cs", "Test")
    assert result["success"]
    assert result["data"]["sha256"] == "a" * 64
    assert result["data"]["bytes_sha256"] == "b" * 64
    assert read.await_count == 1


@pytest.mark.asyncio
async def test_search_never_substitutes_text_digest_for_missing_raw_identity(monkeypatch):
    monkeypatch.setattr(
        search, "get_unity_instance_from_context", AsyncMock(return_value="Editor@project")
    )
    monkeypatch.setattr(
        search,
        "send_with_unity_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"contents": "class Test {}", "sha256": "a" * 64},
            }
        ),
    )
    result = await search.find_in_file(None, "Assets/Test.cs", "Test")
    assert result["data"]["sha256"] == "a" * 64
    assert result["data"]["bytes_sha256"] is None
