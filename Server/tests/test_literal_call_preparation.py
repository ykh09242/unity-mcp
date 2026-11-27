"""Check caller-selected exact text through the installed MCP SDK."""

import json
from typing import Annotated
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from pydantic import BaseModel, Field, StrictStr

from .test_agent_call_preparation import server as server
from transport.execution_receipts import META_KEY
from transport.unity_instance_middleware import UnityInstanceMiddleware


def _script_edit(content):
    return {
        "uri": "Assets/Scripts/Example.cs",
        "edits": [{"startLine": 3, "startCol": 1, "endLine": 3, "endCol": 1, "newText": content}],
        "options": {"preview": True},
    }


class RecoveryDraft(BaseModel):
    name: StrictStr


@pytest.mark.asyncio
@pytest.mark.parametrize("example_size", [10, 600])
async def test_unrelated_nested_validation_uses_published_example_or_same_connection_schema(
    server, example_size
):
    example = {"name": "R" * example_size}
    calls = []

    @server.tool
    async def recovery_example(draft: Annotated[RecoveryDraft, Field(examples=[example])]):
        calls.append(draft.name)
        return {"accepted": True}

    private = "private-unknown-key"
    async with Client(server) as client:
        published = next(
            item for item in await client.list_tools() if item.name == "recovery_example"
        )
        result = await client.call_tool(
            published.name, {"draft": {private: "value"}}, raise_on_error=False
        )

    message = result.content[0].text
    assert result.is_error and "draft[*]: missing" in message
    assert private not in message and calls == []
    sample = published.input_schema["properties"]["draft"]["examples"][0]
    if example_size < 512:
        assert json.dumps(sample, separators=(",", ":")) in message
    else:
        assert sample["name"] not in message and "tools/list" in message


@pytest.mark.asyncio
async def test_unpublished_fallback_schema_does_not_add_retry_guidance(server):
    server.disable(names={"manage_tools"})
    async with Client(server) as client:
        assert "manage_tools" not in {item.name for item in await client.list_tools()}
        denial = await client.call_tool(
            "manage_tools", {"action": "prepare", "exact_text": "malformed"}, raise_on_error=False
        )

    assert denial.is_error
    message = denial.content[0].text
    assert "Published example" not in message and "tools/list" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_late_validation_omits_examples_for_an_unpublished_resource_tool(server, mode):
    name = "list_menu_declarations"
    uri = "mcpforunity://menu-items"
    example = {"name": "unpublished-schema-example"}
    server.local_provider.remove_resource(uri)
    server.local_provider.remove_tool(name)

    @server.tool(name=name)
    async def resource_fixture(draft: Annotated[RecoveryDraft, Field(examples=[example])]):
        raise AssertionError(f"Invalid draft entered the handler: {draft.name}")

    async with Client(server, mode=mode) as client:
        assert name not in {item.name for item in await client.list_tools()}
        denied = await client.call_tool(name, {"draft": {"name": 42}}, raise_on_error=False)

    assert denied.is_error and denied.structured_content is None
    message = denied.content[0].text
    assert "string_type" in message and "input_value" not in message
    assert example["name"] not in message and "tools/list" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("scope", ["hidden", "explicit", "receipt"])
async def test_early_errors_omit_guidance_when_the_requested_scope_is_unverified(
    server, monkeypatch, mode, scope
):
    monkeypatch.setattr(
        UnityInstanceMiddleware, "_maybe_autoselect_instance", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        UnityInstanceMiddleware,
        "_resolve_enabled_tool_names_for_context",
        AsyncMock(return_value=set()),
    )
    resolve = AsyncMock(side_effect=ValueError("test target unavailable"))
    monkeypatch.setattr(UnityInstanceMiddleware, "_resolve_instance_value", resolve)
    marker = "private-rejected-value"
    name = "manage_tools"
    arguments = {"action": "prepare", "exact_text": marker}
    meta = None
    if scope == "hidden":
        name, arguments = "manage_terrain", {"action": {"wrong": marker}}
    elif scope == "explicit":
        arguments["unity_instance"] = "TestMissing@ab12cd34"
    else:
        meta = {META_KEY: {"version": 1, "store_id": "0" * 32, "project_hash": "1" * 32}}

    async with Client(server, mode=mode) as client:
        advertised = {item.name for item in await client.list_tools()}
        assert "manage_tools" in advertised and "manage_terrain" not in advertised
        result = await client.call_tool(name, arguments, meta=meta, raise_on_error=False)

    assert result.is_error and result.structured_content is None
    message = result.content[0].text
    assert ("action" if scope == "hidden" else "exact_text: list_type") in message
    assert (
        marker not in message and "Published example" not in message and "tools/list" not in message
    )
    resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_malformed_assertions_return_the_published_retry_shape_without_echoing_input(
    server, mode
):
    private = "do-not-echo-this-value"
    attempts = [
        private,
        [{"field": private, "text": private}],
        [{"path": private, "expected": private}],
    ]
    path = ["source_changes", "tooltip"]
    draft = {
        "path": "Assets/UI/Example.uxml",
        "source_selector": "#next",
        "source_changes": {"tooltip": "Continue."},
    }
    prepare = {
        "action": "prepare",
        "tool": "manage_ui",
        "operation": "patch_source",
        "arguments": draft,
        "query": f'Change the tooltip to "Continue." This note is private: {private}',
    }

    async with Client(server, mode=mode) as client:
        tool = next(item for item in await client.list_tools() if item.name == "manage_tools")
        field = tool.input_schema["properties"]["exact_text"]
        example = json.dumps(field["examples"][0], sort_keys=True, separators=(",", ":"))
        for invalid in attempts:
            error = await client.call_tool(
                "manage_tools", {**prepare, "exact_text": invalid}, raise_on_error=False
            )
            assert error.is_error and error.structured_content is None
            message = error.content[0].text
            assert private not in message
            assert example in message and field["description"] in message

        assertion = json.loads(example)[0]
        response = await client.call_tool(
            "manage_tools",
            {
                **prepare,
                "exact_text": [{**assertion, "path": path, "expected": "Continue."}],
            },
        )

    result = response.structured_content
    assert result["ready"] is True and result["exact_text_checked"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize(
    ("tool", "operation"), [("manage_ui", "patch_source"), ("manage_gameobject", "modify")]
)
async def test_prepare_accepts_the_example_shared_by_first_list_and_selected_operation(
    server, mode, tool, operation
):
    async with Client(server, mode=mode) as client:
        tools = {item.name: item for item in await client.list_tools()}
        advertised = tools["manage_tools"].input_schema["properties"]["exact_text"]
        described = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": tool, "operation": operation}
        )
        card = described.structured_content["operation"]
        assert advertised.get("examples") and card.get("prepare_exact_text"), (
            list(advertised),
            list(card),
        )
        guide = card["prepare_exact_text"]
        assert guide["description"] == advertised["description"]
        example = guide["example"]
        assert example["exact_text"] in advertised["examples"]

        template = card["prepare_call"]
        assert set(template) == {"tool", "arguments"}
        assert {"query", "exact_text"}.isdisjoint(template["arguments"])
        sample = example["exact_text"][0]
        literal = sample["expected"]
        if tool == "manage_ui":
            path = ["source_changes", "tooltip"]
            draft = {
                "path": "Assets/UI/Example.uxml",
                "source_selector": "#next",
                "source_changes": {"tooltip": literal},
            }
        else:
            path = ["name"]
            draft = {"target": "PreviousName", "name": literal}
        request = {
            **template["arguments"],
            **example,
            "arguments": {**template["arguments"]["arguments"], **draft},
            "exact_text": [{**sample, "path": path}],
        }

        response = await client.call_tool(template["tool"], request)

    result = response.structured_content
    assert result["ready"] is True and result["exact_text_checked"] == 1
    assert result["prepared_call"] == {"tool": tool, "arguments": request["arguments"]}


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
        [{"field": "name", "text": literal}],
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
