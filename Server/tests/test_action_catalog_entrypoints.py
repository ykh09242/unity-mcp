"""The public MCP and offline CLI use the same complete discovery catalog."""

import json
from typing import Annotated, Literal

from click.testing import CliRunner
from fastmcp import Client, Context, FastMCP
from jsonschema import Draft202012Validator
import pytest


@pytest.fixture
def server(monkeypatch, request):
    from core.config import config
    from services.tools import register_all_tools

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("action-catalog-tests", dereference_schemas=getattr(request, "param", True))
    register_all_tools(server, project_scoped_tools=False)
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_catalog_selects_one_declared_operation_within_the_current_inventory(server, mode):
    async with Client(server, mode=mode) as client:
        before = {tool.name for tool in await client.list_tools()}
        args = {"action": "catalog", "tool": "prefab_inspect", "operation": "get_hierarchy"}
        page = (await client.call_tool("manage_tools", args)).structured_content
        assert [row["action"] for row in page["actions"]] == ["get_hierarchy"]
        assert page["total"] == 1 and page["next_offset"] is None
        assert page["tool"]["advertised"] == ("prefab_inspect" in before)
        for filters in ({"intent": "edit"}, {"offset": 1}):
            other = (await client.call_tool("manage_tools", {**args, **filters})).structured_content
            assert other["actions"] == []
        unknown = (
            await client.call_tool("manage_tools", {**args, "operation": "unpublished"})
        ).structured_content
        assert unknown["success"] is False
        assert {tool.name for tool in await client.list_tools()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("server", [True, False], indirect=True)
async def test_structured_search_matches_flat_fields_on_the_advertised_connection(
    server, mode, monkeypatch
):
    from core.config import config
    from transport.plugin_hub import PluginHub
    from transport.plugin_registry import PluginRegistry
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(PluginHub, "_registry", PluginRegistry())
    server.add_middleware(UnityInstanceMiddleware())
    # The first form reproduces the rejected search after a verified source edit.
    requests = [
        {
            "query": "UI Toolkit hover pointer move mouse tooltip live UXML UI Builder",
            "selection": "agent",
        },
        {
            "query": "  요청 그대로؟  ",
            "selection": "agent",
            "limit": 100,
            "route_surfaces": "resources",
        },
        {
            "observation_source": "editor_live",
            "intent": "observe",
            "host_tool_names": ["manage_tools"],
        },
    ]
    async with Client(server, mode=mode) as client:
        inventory = {tool.name: tool.input_schema for tool in await client.list_tools()}
        directory = (await client.call_tool("search_unity_tools", requests[0])).structured_content
        requests.append(
            {"selection": "agent", "family": directory["families"][0]["family"], "limit": 1}
        )
        for tool, prefix in (
            ("search_unity_tools", {}),
            ("manage_tools", {"action": "search"}),
            ("manage_tools", {"action": "catalog"}),
        ):
            validator = Draft202012Validator(inventory[tool])
            draft = {**prefix, "query": requests[0]}
            prepared = (
                await client.call_tool(
                    "manage_tools", {"action": "prepare", "tool": tool, "arguments": draft}
                )
            ).structured_content
            assert prepared["ready"] and prepared["prepared_call"]["arguments"] == draft
            for fields in requests:
                wrapped = {**prefix, "query": fields}
                validator.validate(wrapped)
                canonical = {"action": "search"} if prefix else {}
                expected = (
                    await client.call_tool(tool, {**canonical, **fields})
                ).structured_content
                received = (await client.call_tool(tool, wrapped)).structured_content
                assert received == expected and received.get("success") is not False
            for fields in (
                {**prefix, "query": {"query": {"query": "ports"}}},
                {**prefix, "query": {"query": "ports", "unity_instance": "Other@123"}},
                {**prefix, "query": {"query": "ports", "action": "activate"}},
                {
                    **prefix,
                    "query": {"selection": "agent", "query": "ports"},
                    "selection": "ranked",
                },
                {**prefix, "query": {"query": "ports", "limit": 1}, "limit": 5},
            ):
                assert not validator.is_valid(fields)
                assert (await client.call_tool(tool, fields, raise_on_error=False)).is_error
        for action in ("activate", "prepare", "read_skill"):
            fields = {"action": action, "query": {"query": "ports"}}
            assert not Draft202012Validator(inventory["manage_tools"]).is_valid(fields)
            assert (await client.call_tool("manage_tools", fields, raise_on_error=False)).is_error
        assert {tool.name for tool in await client.list_tools()} == set(inventory)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_discovery_caps_requested_pages_and_routes_unscoped_catalog_text(server, mode):
    async with Client(server, mode=mode) as client:
        before = {tool.name for tool in await client.list_tools()}
        for selection in ("agent", "ranked"):
            arguments = {"query": "inspect a saved file", "selection": selection}
            expected = (
                await client.call_tool("search_unity_tools", {**arguments, "limit": 20})
            ).structured_content
            assert expected.get("success") is not False
            oversized = (
                await client.call_tool("search_unity_tools", {**arguments, "limit": 100})
            ).structured_content
            assert oversized == expected
            catalog = (
                await client.call_tool(
                    "manage_tools", {"action": "catalog", **arguments, "limit": 100}
                )
            ).structured_content
            search = (
                await client.call_tool(
                    "manage_tools", {"action": "search", **arguments, "limit": 20}
                )
            ).structured_content
            assert catalog == search
        args = {"action": "catalog", "tool": "manage_shader"}
        bounded = (await client.call_tool("manage_tools", {**args, "limit": 20})).structured_content
        assert (
            await client.call_tool("manage_tools", {**args, "limit": 100})
        ).structured_content == bounded
        refused = await client.call_tool(
            "manage_tools", {"action": "activate", "group": "ui", "limit": 100}
        )
        assert refused.structured_content["success"] is False
        assert {tool.name for tool in await client.list_tools()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_catalog_and_describe_preserve_inventory_and_schema(server, mode):
    async with Client(server, mode=mode) as client:
        before = {tool.name for tool in await client.list_tools()}
        index = (await client.call_tool("manage_tools", {"action": "catalog"})).structured_content
        names = {name for group in index["groups"] for name in group["tools"]}
        assert names == set(server._tool_discovery.definitions)
        assert "execute_custom_tool" not in names
        assert "manage_shader" in names and "manage_shader" not in before
        page = (
            await client.call_tool(
                "manage_tools",
                {
                    "action": "catalog",
                    "tool": "manage_shader",
                    "limit": 20,
                },
            )
        ).structured_content
        assert page["tool"]["advertised"] is False
        assert "inspect_graph" in {action["action"] for action in page["actions"]}
        result = (
            await client.call_tool(
                "manage_tools",
                {
                    "action": "describe",
                    "tool": "manage_shader",
                    "operation": "inspect_graph",
                },
            )
        ).structured_content
        assert result["operation"]["action"] == "inspect_graph"
        assert result["tool"]["input_schema"]["properties"]["action"]["const"] == "inspect_graph"
        assert result["catalog"]["advertised"] is False
        assert "prepare_call" not in result["operation"]
        assert result["catalog"]["actions"] == page["tool"]["actions"]
        assert {tool.name for tool in await client.list_tools()} == before
        original = server._tool_discovery.definitions["manage_shader"].parameters
        assert "const" not in original["properties"]["action"]

        graph = (
            await client.call_tool(
                "manage_tools",
                {"action": "describe", "tool": "manage_reference_graph", "operation": "path"},
            )
        ).structured_content
        schema = graph["tool"]["input_schema"]
        assert "options" in schema["required"]
        assert schema["properties"]["options"]["required"] == ["node", "target"]
        assert graph["operation"]["options_schema"]["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "catalog", "group": "core", "tool": "manage_shader"},
        {"action": "catalog", "query": "shader", "group": "core", "selection": "agent"},
        {"action": "catalog", "operation": "read"},
        {"action": "catalog", "tool": "unknown"},
        {"action": "catalog", "offset": 1},
        {"action": "search", "query": "shader", "offset": 1},
        {"action": "activate", "tool": "manage_shader", "operation": "read"},
        {"action": "describe", "tool": "manage_shader", "operation": "invented"},
        {"action": "describe", "tool": "manage_shader", "operation": "   "},
    ],
)
async def test_bad_selection_fails_without_activation(server, arguments):
    async with Client(server, mode="legacy") as client:
        before = {tool.name for tool in await client.list_tools()}
        call = await client.call_tool("manage_tools", arguments, raise_on_error=False)
        result = call.structured_content or {}
        assert call.is_error or result["success"] is False
        if arguments["action"] == "describe":
            assert arguments["tool"] not in before and "next_call" not in result
        assert {tool.name for tool in await client.list_tools()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_scoped_tool_filter_respects_advertised_inventory_policy(server, mode):
    from fastmcp.server.middleware import Middleware

    @server.tool
    def example_visible() -> str:
        """Report a sample measurement."""
        return "observed"

    @server.tool
    def example_hidden() -> str:
        """Report a sample measurement."""
        raise AssertionError("Inventory filtering must not invoke a tool")

    class InventoryPolicy(Middleware):
        async def on_list_tools(self, context, call_next):
            return [tool for tool in await call_next(context) if tool.name != "example_hidden"]

    server.add_middleware(InventoryPolicy())
    async with Client(server, mode=mode) as client:
        for name, advertised in (("example_visible", True), ("example_hidden", False)):
            page = (
                await client.call_tool(
                    "manage_tools", {"action": "catalog", "tool": name, "query": "sample"}
                )
            ).structured_content
            if advertised:
                assert page["total"] == 1 and page["tool"]["name"] == name
            else:
                assert page["success"] is False and "actions" not in page


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_scoped_filter_uses_current_wire_metadata_without_same_name_guidance(server, mode):
    from typing import Literal

    from fastmcp.server.middleware import Middleware
    from fastmcp.tools import Tool
    from mcp.types import ToolAnnotations
    from services.registry.action_catalog import ACTION_OBSERVATIONS_META

    def replacement(action: Literal["get_hierarchy"], sample_key: str) -> str:
        """Read a measurement sample."""
        raise AssertionError("Catalog must remain read-only")

    observation = {"source": "editor_live", "values": ["sample_value"]}
    replacement_tool = Tool.from_function(
        replacement,
        name="prefab_inspect",
        title="Sample telemetry",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
        meta={ACTION_OBSERVATIONS_META: {"get_hierarchy": observation}},
    )
    target = "scene_inspect"

    class WireProjection(Middleware):
        async def on_list_tools(self, context, call_next):
            return [
                replacement_tool
                if item.name == "prefab_inspect"
                else item.model_copy(
                    update={
                        "title": "Visible Observatory",
                        "description": "Published Telemetry",
                        "annotations": ToolAnnotations(readOnlyHint=False, destructiveHint=False),
                    }
                )
                if item.name == target
                else item
                for item in await call_next(context)
            ]

    server.add_middleware(WireProjection())
    async with Client(server, mode=mode) as client:
        tools = {item.name: item for item in await client.list_tools()}
        assert tools[target].description == "Published Telemetry"
        assert tools["prefab_inspect"].title == "Sample telemetry"

        group = {"action": "catalog", "group": server._tool_discovery.groups[target]}
        found = (
            await client.call_tool("manage_tools", {**group, "query": "observatory telemetry"})
        ).structured_content
        original_title = (
            await client.call_tool("manage_tools", {**group, "query": "inspect scenes hierarchies"})
        ).structured_content
        action = (
            await client.call_tool(
                "manage_tools",
                {"action": "catalog", "tool": "prefab_inspect", "query": "get hierarchy"},
            )
        ).structured_content

    assert [row["name"] for row in found["tools"]] == [target]
    assert found["tools"][0]["description"] == "Published Telemetry"
    assert found["tools"][0]["effects"]["readOnlyHint"] is False
    assert target not in {row["name"] for row in original_title["tools"]}
    assert action["tool"]["title"] == "Sample telemetry"
    assert action["tool"]["group"] is None
    assert action["actions"][0]["purpose"] == "get hierarchy"
    assert action["actions"][0]["required_parameters"] == ["sample_key"]
    assert action["actions"][0]["effects"]["readOnlyHint"] is False
    assert action["actions"][0]["observation"] == observation


def test_catalog_permission_is_read_only():
    from services.operation_permissions import Permission, required_permissions

    assert required_permissions("manage_tools", {"action": "catalog"}) == {Permission.READ}


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_describe_observes_the_same_tool_list_policy_as_prepare(server, denied):
    from fastmcp.exceptions import AuthorizationError
    from fastmcp.server.middleware import Middleware
    from transport.protocol import is_internal_catalog_list

    @server.tool
    def hidden_probe():
        return True

    class Policy(Middleware):
        async def on_list_tools(self, context, call_next):
            if denied and is_internal_catalog_list(context):
                raise AuthorizationError("Inventory unavailable")
            return [
                tool
                for tool in await call_next(context)
                if tool.name not in {"search_unity_tools", "hidden_probe"}
            ]

    server.add_middleware(Policy())
    async with Client(server, mode="legacy") as client:
        reply = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "search_unity_tools"}
        )
        description = reply.structured_content
        assert description["catalog"]["advertised"] is (None if denied else False)
        assert "prepare_call" not in description["operation"]
        hidden = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "hidden_probe"}
        )
        assert hidden.structured_content["success"] is False
        assert "tool" not in hidden.structured_content


def test_large_custom_input_retains_its_exact_constraints_at_the_returned_schema_pointer():
    from jsonschema import Draft202012Validator
    from services.registry.action_catalog import describe_action

    field = "sensor/scale~%"
    choices = [f"channel-{n}" for n in range(300)]
    schema = {"type": "object", "properties": {field: {"enum": choices}}}
    result = describe_action({"name": "selector", "kwargs": {}}, {"inputSchema": schema})
    reference = result["operation"]["input_fields"][field]
    assert set(reference) == {"$ref"}
    validator = Draft202012Validator(
        {
            **result["tool"]["inputSchema"],
            "properties": {**schema["properties"], "value": reference},
        }
    )
    assert validator.is_valid({"value": choices[-1]})
    assert not validator.is_valid({"value": "unregistered"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_described_inputs_lead_to_a_callable_draft(server, mode):
    from typing import Literal

    @server.tool
    def sample_channel(
        action: Literal["sample", "status"], options: dict, label: str | None = None
    ):
        return {"observed": options["enabled"], "label": label}

    @server.tool
    def sample_offset(offset: int, enabled: bool = True):
        return {"observed": offset if enabled else None}

    async with Client(server, mode=mode) as client:
        rejected = (
            await client.call_tool(
                "manage_tools",
                {"action": "describe", "tool": "sample_channel", "operation": "invented"},
            )
        ).structured_content
        assert rejected["success"] is False
        retry = rejected["next_call"]
        assert retry == {
            "tool": "manage_tools",
            "arguments": {"action": "describe", "tool": "sample_channel"},
        }
        family = (await client.call_tool(retry["tool"], retry["arguments"])).structured_content
        assert set(family["catalog"]["actions"]) == {"sample", "status"}
        assert family["operation"] is None
        for name, op, values, expected in (
            (
                "sample_channel",
                "sample",
                {"options": {"enabled": False}, "label": None},
                {"observed": False, "label": None},
            ),
            ("sample_offset", None, {"offset": 0, "enabled": False}, {"observed": None}),
        ):
            selected = {"operation": op} if op else {}
            result = await client.call_tool(
                "manage_tools", {"action": "describe", "tool": name, **selected}
            )
            card = result.structured_content["operation"]
            required = "options" if op else "offset"
            assert card["input_fields"][required]["type"] == ("object" if op else "integer")
            followup = card["prepare_call"]
            draft = followup["arguments"]["arguments"]
            assert draft == ({"action": op} if op else {})
            incomplete = await client.call_tool(followup["tool"], followup["arguments"])
            assert incomplete.structured_content["ready"] is False
            draft.update(values)
            if op:
                wrong = await client.call_tool(
                    followup["tool"],
                    {**followup["arguments"], "arguments": {**draft, "action": "status"}},
                )
                assert wrong.structured_content["ready"] is False
            prepared = await client.call_tool(followup["tool"], followup["arguments"])
            call = prepared.structured_content["prepared_call"]
            assert call == {"tool": name, "arguments": draft}
            assert (
                await client.call_tool(call["tool"], call["arguments"])
            ).structured_content == expected


@pytest.mark.asyncio
async def test_selected_material_fields_explain_nested_patch_and_addressing(server):
    async with Client(server, mode="legacy") as client:
        result = await client.call_tool(
            "manage_tools",
            {"action": "describe", "tool": "manage_material", "operation": "set_render_state"},
        )
        fields = result.structured_content["operation"]["input_fields"]
        assert {"material_path", "target", "slot", "search_method", "mode", "render_state"} == set(
            fields
        )
        assert "render_queue" in fields["render_state"]["description"]
        assert any(branch.get("type") == "object" for branch in fields["render_state"]["anyOf"])


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [True, False], indirect=True, ids=["inlined", "references"])
async def test_local_schema_alternatives_keep_their_nested_inputs_and_required_values(server):
    from fastmcp.tools import Tool
    from jsonschema import Draft202012Validator

    def inspect_port(action: str, probe=None):
        return {"probe": probe}

    definition = Tool.from_function(inspect_port)
    definition.parameters = {
        "type": "object",
        "properties": {"action": True},
        "$defs": {
            "Read": {
                "properties": {"action": {"const": "read"}, "probe": {"$ref": "#/$defs/Probe"}},
                "required": ["action", "probe"],
            },
            "Probe": {
                "type": "object",
                "properties": {"channel": {"type": "integer", "minimum": 1}},
                "required": ["channel"],
            },
        },
        "oneOf": [
            {"$ref": "#/$defs/Read"},
            {"properties": {"action": {"const": "status"}}, "required": ["action"]},
        ],
    }
    server.add_tool(definition)
    async with Client(server, mode="legacy") as client:
        public = next(t.input_schema for t in await client.list_tools() if t.name == "inspect_port")
        description = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "inspect_port", "operation": "read"}
        )
        result = description.structured_content
        operation = result["operation"]
        assert operation["required_parameters"] == ["probe"]
        assert result["tool"]["input_schema"]["oneOf"] == public["oneOf"]
        field = Draft202012Validator(
            {"$defs": public.get("$defs", {}), **operation["input_fields"]["probe"]}
        )
        assert field.is_valid({"channel": 1}) and not field.is_valid({"channel": 0})
        assert operation["input_variants"] == [
            {"schema_path": "#/oneOf/0", "required_parameters": ["probe"], "parameters": ["probe"]}
        ]
        prepare = operation["prepare_call"]
        for channel, ready in ((0, False), (1, True)):
            prepare["arguments"]["arguments"]["probe"] = {"channel": channel}
            check = (
                await client.call_tool(prepare["tool"], prepare["arguments"])
            ).structured_content
            assert check["ready"] is ready
            if ready:
                call = check["prepared_call"]
                assert (
                    await client.call_tool(call["tool"], call["arguments"])
                ).structured_content == {"probe": {"channel": 1}}


def test_every_action_card_binds_a_real_schema_property(server):
    from jsonschema import Draft202012Validator
    from services.registry import get_registered_tools
    from services.registry.action_catalog import declared_actions, describe_action

    definitions = server._tool_discovery.definitions
    for entry in get_registered_tools():
        if entry["name"] not in definitions:
            continue
        descriptor = (
            definitions[entry["name"]].to_mcp_tool().model_dump(mode="json", exclude_none=True)
        )
        for action in declared_actions(entry) or [None]:
            result = describe_action(entry, descriptor, action)
            schema = result["tool"]["input_schema"]
            Draft202012Validator.check_schema(schema)
            card = result["operation"]
            assert set(card["required_parameters"]) <= schema["properties"].keys()
            if action is not None:
                assert set(card["required_parameters"]) <= set(schema["required"])
            assert set(card["forbidden_parameters"]) <= schema["properties"].keys()
            if action is not None:
                validator = Draft202012Validator(schema["properties"]["action"])
                assert validator.is_valid(action), (entry["name"], action)
                assert not validator.is_valid("unknown-action")


def test_offline_actions_survive_sdk_context_injection_into_a_postponed_signature():
    from services.registry.action_catalog import declared_actions

    async def example(
        ctx: Context, action: "Annotated[Literal['inspect', 'adjust'], 'Sample operation']"
    ) -> str:
        return action

    server = FastMCP("postponed-action-annotations")
    server.tool(example)

    entry = {"name": "example", "func": example}
    assert declared_actions(entry) == ["inspect", "adjust"]


def test_cli_browses_offline_and_preserves_pagination(monkeypatch):
    from cli.main import cli
    import cli.utils.connection as connection

    monkeypatch.setattr(connection, "_auth_headers", lambda *_: pytest.fail("catalog read auth"))
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "tool",
            "catalog",
            "--tool",
            "manage_shader",
            "--limit",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scope"] == "local_catalog"
    assert payload["tool"]["advertised"] is None
    assert payload["next_offset"] == 2
    assert len(payload["actions"]) == 2
    exact = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "tool",
            "catalog",
            "--tool",
            "manage_shader",
            "--operation",
            "inspect_graph",
        ],
    )
    assert exact.exit_code == 0, exact.output
    assert [row["action"] for row in json.loads(exact.stdout)["actions"]] == ["inspect_graph"]

    scoped = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "tool",
            "catalog",
            "--tool",
            "manage_shader",
            "--query",
            "ＩＮＳＰＥＣＴ_ＧＲＡＰＨ",
        ],
    )
    assert scoped.exit_code == 0, scoped.output
    assert [row["action"] for row in json.loads(scoped.stdout)["actions"]] == [
        "inspect_graph",
        "inspect_open_graph",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--group", "core", "--tool", "manage_shader"],
        ["--tool", "invented"],
        ["--limit", "21"],
        ["--offset", "-1"],
    ],
)
def test_cli_refuses_bad_catalog_selections(arguments):
    from cli.main import cli

    result = CliRunner().invoke(cli, ["tool", "catalog", *arguments])
    assert result.exit_code == 2, result.output
