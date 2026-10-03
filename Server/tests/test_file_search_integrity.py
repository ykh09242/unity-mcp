"""File-search routing and match metadata with controlled Unity reads."""

import base64
import importlib
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def search_module(monkeypatch):
    module = importlib.import_module("services.tools.find_in_file")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Owned@fixture")
    )
    sender = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", sender)
    return module, sender


async def search(search_module, contents, pattern, **kwargs):
    module, sender = search_module
    sender.return_value = {"success": True, "data": {"contents": contents}}
    return await module.find_in_file(AsyncMock(), "Assets/Fixture.cs", pattern, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri,directory",
    [
        ("file:///C:/Fixture/Assets/A%2520B/Foo.cs", "Assets/A%20B"),
        ("file://localhost/C:/Fixture/Assets/A%252FB/Foo.cs", "Assets/A%2FB"),
        ("file://server/share/Assets/A%2525B/Foo.cs", "Assets/A%25B"),
        ("file:///C:/Fixture/Assets/A%20B/Foo.cs", "Assets/A B"),
        ("mcpforunity://path/Assets/A%2520B/Foo.cs", "Assets/A%20B"),
        ("Assets/A%2520B/Foo.cs", "Assets/A%20B"),
    ],
)
async def test_uri_decodes_once_without_changing_literal_directory(search_module, uri, directory):
    module, sender = search_module
    sender.return_value = {"success": True, "data": {"contents": "owned"}}
    response = await module.find_in_file(AsyncMock(), uri, "owned")
    assert response["data"]["count"] == 1
    assert sender.call_args.args[1:] == (
        "Owned@fixture", "manage_script", {"action": "read", "name": "Foo", "path": directory}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern,reverse", [("a", False), ("(?r)a", True)])
async def test_line_offsets_and_regex_order_survive_crlf_and_astral_text(search_module, pattern, reverse):
    response = await search(search_module, "a\r\n😀a\n\na\n", pattern)
    expected = [
        {"line": 1, "content": "a", "match": "a", "start": 0, "end": 1},
        {"line": 2, "content": "😀a", "match": "a", "start": 4, "end": 5},
        {"line": 4, "content": "a", "match": "a", "start": 7, "end": 8},
    ]
    assert response["data"] == {
        "matches": expected[::-1] if reverse else expected, "count": 3, "total_matches": 3
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern,reverse", [("", False), ("(?r)", True)])
async def test_zero_width_matches_keep_newline_and_eof_coordinates(search_module, pattern, reverse):
    response = await search(search_module, "a\r\n😀a\n\na\n", pattern)
    lines = [1, 1, 1, 2, 2, 2, 3, 4, 4, 5]
    excerpts = ["a", "a", "a", "😀a", "😀a", "😀a", "", "a", "a", ""]
    expected = [
        {"line": line, "content": excerpt, "match": "", "start": index, "end": index}
        for index, (line, excerpt) in enumerate(zip(lines, excerpts))
    ]
    assert response["data"]["matches"] == (expected[::-1] if reverse else expected)
    assert response["data"]["total_matches"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,expected_count", [(0, 1), (-1, 1), (2, 2), (1001, 1000)])
async def test_result_cap_retains_total_match_count(search_module, limit, expected_count):
    response = await search(search_module, "a " * 1001, "(?r)a", max_results=limit)
    assert response["data"]["count"] == expected_count
    assert response["data"]["total_matches"] == 1001
    assert response["data"]["matches"][0]["start"] == 2000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,pattern,start",
    [
        ({"contentsEncoded": True, "encodedContents": base64.b64encode("😀 match".encode()).decode()}, "match", 2),
        ({"contents": "plain", "contentsEncoded": True, "encodedContents": base64.b64encode(b"other").decode()}, "plain", 0),
        ({"contentsEncoded": True, "encodedContents": base64.b64encode(b"\xffmatch").decode()}, "match", 1),
    ],
)
async def test_encoded_fallback_plain_precedence_and_invalid_utf8_remain_compatible(search_module, data, pattern, start):
    module, sender = search_module
    sender.return_value = {"success": True, "data": data}
    response = await module.find_in_file(AsyncMock(), "Assets/Fixture.cs", pattern)
    assert response["data"]["count"] == 1
    assert response["data"]["matches"][0]["start"] == start


@pytest.mark.asyncio
async def test_excerpt_and_matched_text_payload_caps_remain(search_module):
    response = await search(search_module, " " + "a" * 3000 + " ", "a+")
    assert response["data"]["matches"] == [
        {"line": 1, "content": "a" * 2000, "match": "a" * 2000, "start": 1, "end": 3001}
    ]


class ScanCountedText(str):
    """Count metadata work without a timing-dependent assertion."""

    def __new__(cls, value):
        result = super().__new__(cls, value)
        result.prefix_chars = 0
        result.excerpt_chars = 0
        return result

    def count(self, sub, start=0, end=None):
        stop = len(self) if end is None else end
        self.prefix_chars += max(0, stop - start)
        return super().count(sub, start, stop)

    def __getitem__(self, key):
        if isinstance(key, slice):
            self.excerpt_chars += len(range(*key.indices(len(self))))
        return super().__getitem__(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", ["\n", ""])
async def test_metadata_does_not_rescan_prefixes_or_copy_one_long_line_per_match(search_module, separator):
    text = ScanCountedText(("prefix " + "x" * 1000 + separator) * 1000)
    response = await search(search_module, text, "prefix", max_results=1000)
    assert response["data"]["count"] == 1000
    assert text.prefix_chars <= len(text)
    assert text.excerpt_chars <= len(text)


@pytest.mark.asyncio
async def test_sparse_late_match_on_newline_dense_text(search_module):
    text = ScanCountedText("\n" * 1_000_000 + "needle")
    response = await search(search_module, text, "needle")
    assert response["data"]["matches"] == [
        {"line": 1_000_001, "content": "needle", "match": "needle", "start": 1_000_000, "end": 1_000_006}
    ]
    assert text.prefix_chars <= len(text)


@pytest.mark.asyncio
async def test_native_read_error_and_regex_rejection_remain_structured(search_module):
    module, sender = search_module
    failure = {"success": False, "message": "Owned read rejected.", "data": {"path": "Assets/Fixture.cs"}}
    sender.return_value = failure
    assert await module.find_in_file(AsyncMock(), "Assets/Fixture.cs", "x") == failure
    response = await search(search_module, "owned", "[")
    assert response["success"] is False
    assert response["message"].startswith("Regex search rejected:")


@pytest.mark.asyncio
async def test_empty_file_and_case_sensitive_search_remain_supported(search_module):
    response = await search(search_module, "", "")
    assert response["data"]["matches"] == [
        {"line": 1, "content": "", "match": "", "start": 0, "end": 0}
    ]
    response = await search(search_module, "Owned owned", "owned", ignore_case="false")
    assert response["data"]["count"] == 1
    assert response["data"]["matches"][0]["start"] == 6
