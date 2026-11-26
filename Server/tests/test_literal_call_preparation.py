"""Check caller-selected exact text through the installed MCP SDK."""

import pytest
from fastmcp import Client

from .test_agent_call_preparation import server as server


def _script_edit(content):
    return {
        "uri": "Assets/Scripts/Example.cs",
        "edits": [{"startLine": 3, "startCol": 1, "endLine": 3, "endCol": 1, "newText": content}],
        "options": {"preview": True},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("reuse_draft_as_expected", [False, True])
async def test_prepare_rejects_added_punctuation_even_if_the_draft_is_reused_as_expected(
    server, mode, reuse_draft_as_expected
):
    literal = "Weiter zu den Einstellungen"
    draft = {
        "path": "Assets/UI/Example.uxml",
        "source_selector": "#next",
        "source_changes": {"tooltip": literal + ".", "text": literal},
    }
    expected = draft["source_changes"]["tooltip"] if reuse_draft_as_expected else literal

    async with Client(server, mode=mode) as client:
        response = await client.call_tool(
            "manage_tools",
            {
                "action": "prepare",
                "tool": "manage_ui",
                "operation": "patch_source",
                "arguments": draft,
                "query": f"Set the tooltip to «{literal}» and leave the rest as is.",
                "exact_text": [{"path": ["source_changes", "tooltip"], "expected": expected}],
            },
            raise_on_error=False,
        )

    result = response.structured_content
    assert result is not None, response.content
    assert result["ready"] is False
    assert "prepared_call" not in result
    assert result["exact_text_errors"] == [
        {"index": 0, "reason": "expected_not_in_query" if reuse_draft_as_expected else "mismatch"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("family", ["ui", "script"])
async def test_prepare_preserves_intentional_punctuation_whitespace_and_emoji(server, mode, family):
    literal = "  路線 e\u0301 🚋。 \r\n"
    if family == "ui":
        draft = {
            "path": "Assets/UI/Example.uxml",
            "source_selector": "#next",
            "source_changes": {"text": literal, "tooltip": "Unrelated value!"},
        }
        path = ["source_changes", "text"]
        binding = {"tool": "manage_ui", "operation": "patch_source"}
        outbound = {**draft, "action": "patch_source"}
    else:
        draft = _script_edit(literal)
        path = ["edits", 0, "newText"]
        binding = {"tool": "apply_text_edits"}
        outbound = draft

    async with Client(server, mode=mode) as client:
        response = await client.call_tool(
            "manage_tools",
            {
                "action": "prepare",
                **binding,
                "arguments": draft,
                "query": f"Use exactly «{literal}».",
                "exact_text": [{"path": path, "expected": literal}],
            },
        )

    result = response.structured_content
    assert result["ready"] is True and result["exact_text_checked"] == 1
    assert result["prepared_call"] == {"tool": binding["tool"], "arguments": outbound}


@pytest.mark.asyncio
async def test_prepare_reports_which_array_paths_or_unicode_values_failed(server):
    literal = " e\u0301 🚋 \r\n"
    canonically_equivalent = " é 🚋 \r\n"
    draft = _script_edit(canonically_equivalent)
    draft["edits"].extend(
        [
            {"newText": literal.strip()},
            {"newText": literal.replace("\r\n", "\n")},
            {"newText": literal.replace("🚋", "🚃")},
            {"newText": {"value": literal}},
            {"newText": literal},
        ]
    )
    paths_with_reasons = [
        (["edits", 0, "newText"], "mismatch"),
        (["edits", 1, "newText"], "mismatch"),
        (["edits", 2, "newText"], "mismatch"),
        (["edits", 3, "newText"], "mismatch"),
        (["edits", 4, "newText"], "not_string"),
        (["edits", 5, "newText"], None),
        (["edits", "5", "newText"], "missing_path"),
        (["edits", 6, "newText"], "missing_path"),
        (["edits", 5, "wrong"], "missing_path"),
    ]

    async with Client(server, mode="2026-07-28") as client:
        response = await client.call_tool(
            "manage_tools",
            {
                "action": "prepare",
                "tool": "apply_text_edits",
                "arguments": draft,
                "query": f"Insert «{literal}» as written.",
                "exact_text": [
                    {"path": path, "expected": literal} for path, _ in paths_with_reasons
                ],
            },
            raise_on_error=False,
        )

    result = response.structured_content
    assert result["ready"] is False and "prepared_call" not in result
    assert result["exact_text_errors"] == [
        {"index": index, "reason": reason}
        for index, (_, reason) in enumerate(paths_with_reasons)
        if reason is not None
    ]


@pytest.mark.asyncio
async def test_prepare_rejects_malformed_exact_text_arguments_at_the_sdk_boundary(server):
    literal = "test"
    path = ["edits", 0, "newText"]
    valid = {"path": path, "expected": literal}
    cases = [
        [],
        valid,
        [valid] * 17,
        [{"expected": literal}],
        [{**valid, "expected": ""}],
        [{**valid, "expected": "x" * 513}],
        [{**valid, "expected": 7}],
        [{**valid, "extra": True}],
        [{**valid, "path": "edits.0.newText"}],
        [{**valid, "path": []}],
        [{**valid, "path": ["key"] * 33}],
        [{**valid, "path": ["edits", -1, "newText"]}],
        [{**valid, "path": ["edits", True, "newText"]}],
        [{**valid, "path": ["edits", 0.0, "newText"]}],
    ]

    async with Client(server, mode="legacy") as client:
        for claims in cases:
            response = await client.call_tool(
                "manage_tools",
                {
                    "action": "prepare",
                    "tool": "apply_text_edits",
                    "arguments": _script_edit(literal),
                    "query": f"Insert «{literal}».",
                    "exact_text": claims,
                },
                raise_on_error=False,
            )
            assert response.is_error and response.structured_content is None, claims


@pytest.mark.asyncio
async def test_exact_text_requires_prepare_and_a_nonempty_query(server):
    literal = "test"
    assertion = {"path": ["edits", 0, "newText"], "expected": literal}
    base = {"tool": "apply_text_edits", "exact_text": [assertion]}
    async with Client(server, mode="legacy") as client:
        for options in ({}, {"query": None}, {"query": ""}):
            rejected = await client.call_tool(
                "manage_tools",
                {"action": "prepare", "arguments": _script_edit(literal), **base, **options},
                raise_on_error=False,
            )
            assert rejected.structured_content["ready"] is False
            assert "prepared_call" not in rejected.structured_content
        unrelated = await client.call_tool(
            "manage_tools",
            {"action": "describe", "query": literal, **base},
            raise_on_error=False,
        )
        assert unrelated.structured_content["success"] is False


@pytest.mark.asyncio
async def test_prepare_omitting_exact_text_preserves_normal_drafts(server):
    draft = _script_edit("")
    async with Client(server, mode="legacy") as client:
        response = await client.call_tool(
            "manage_tools",
            {"action": "prepare", "tool": "apply_text_edits", "arguments": draft},
        )

    result = response.structured_content
    assert result["ready"] is True and "exact_text_checked" not in result
    assert result["prepared_call"]["arguments"] == draft
