"""Agent selection depends only on public declarations, including every sibling operation."""

import pytest

from services.registry import task_search


def tool(name, actions):
    return {
        "name": name,
        "group": None,
        "description": "Read or adjust instrument ports.",
        "kwargs": {},
        "input_schema": {"properties": {"action": {"enum": actions}}},
    }


def test_agent_can_compare_every_public_binding_without_a_ranker(monkeypatch):
    entries = [tool("ports", ["status", "list", "set", "reset"]), tool("private_port", ["erase"])]
    monkeypatch.setattr(task_search, "_term_evidence", lambda *_: pytest.fail("invoked ranker"))
    query = "  Which port is selected?  "

    def request(original=query, **extra):
        return task_search.search_catalog(
            task_search.SearchRequest(selection="agent", query=original, limit=2, **extra),
            entries,
            visible_names={"ports"},
        )

    index = request()
    assert index["query"] == query and index["matching"] == "catalog"
    assert [f["family"] for f in index["families"]] == ["ports"]
    first = request(family="ports")
    last = request(original=None, family="ports", offset=first["next_offset"])
    assert last["next_offset"] is None and first["total"] == 4
    assert {op["action"] for op in first["operations"] + last["operations"]} == {
        "status",
        "list",
        "set",
        "reset",
    }
    assert all(
        op["tool"] == "ports" and op["advertised"]
        for op in first["operations"] + last["operations"]
    )
    assert request(original=None, family="private_port").get("success") is False
    assert request(host_tool_names=[])["families"] == []


def test_resources_and_templates_present_independent_public_read_contracts():
    resources = [
        {
            "uri": "mcpforunity://meters/summary",
            "name": "Summary",
            "description": "Read meter overview.",
            "kwargs": {},
        },
        {
            "uri": "mcpforunity://meters/{meter}",
            "name": "Meter",
            "description": "Read a named meter.",
            "kwargs": {},
        },
    ]

    def run(**kw):
        r = task_search.SearchRequest(
            selection="agent", query="meter", route_surfaces="resources", **kw
        )
        return task_search.search_catalog(
            r, [tool("ports", ["status"])], resources=resources, visible_names={"ports"}
        )

    index = run()
    assert {f["family"] for f in index["families"]} == {r["uri"] for r in resources}
    (fixed,) = run(family=resources[0]["uri"])["operations"]
    (template,) = run(family=resources[1]["uri"])["operations"]
    assert fixed["uri"] == resources[0]["uri"] and fixed["intent"] == "observe"
    assert template["uri_template"] == resources[1]["uri"] and template["required_parameters"] == [
        "meter"
    ]


def test_visible_forwarders_share_a_family_with_hidden_owner_and_keep_distinct_wire_contracts(
    monkeypatch,
):
    from services.registry import family_catalog

    entries = [
        tool("native_meter", ["status", "calibrate"]),
        tool("meter_reads", ["status"]),
        tool("meter_changes", ["calibrate"]),
    ]
    monkeypatch.setattr(family_catalog, "operation_owner", lambda name, selector: "native_meter")
    visible = {"meter_reads", "meter_changes"}

    def run(**options):
        return task_search.search_catalog(
            task_search.SearchRequest(selection="agent", query="read", **options),
            entries,
            visible_names=visible,
        )

    (family,) = run()["families"]
    assert "native_meter" not in str(family)
    page = run(family=family["family"])
    assert [(r["tool"], r["action"]) for r in page["operations"]] == [
        ("meter_changes", "calibrate"),
        ("meter_reads", "status"),
    ]
    assert (
        run(host_tool_evidence={"candidates": ["meter_changes"], "present": [], "complete": True})[
            "families"
        ][0]["count"]
        == 1
    )


def test_discriminated_wire_schema_preserves_declared_purposes_and_conditional_inputs():
    entry = tool("ports", [])
    entry["input_schema"] = {
        "oneOf": [
            {
                "properties": {"action": {"const": "status"}},
                "description": "Read the current port.",
            },
            {
                "properties": {"action": {"const": "set"}},
                "description": "Change the current port.",
                "required": ["action", "port"],
            },
        ]
    }
    response = task_search.search_catalog(
        task_search.SearchRequest(selection="agent", family="ports", query="request"), [entry]
    )
    observed = {
        row["action"]: (row["purpose"], row["required_parameters"])
        for row in response["operations"]
    }
    assert observed == {
        "status": ("Read the current port.", []),
        "set": ("Change the current port.", ["port"]),
    }
    from services.registry.action_catalog import describe_action

    described = describe_action(entry, {"inputSchema": entry["input_schema"]}, "set")
    assert described["tool"]["inputSchema"]["properties"]["action"]["const"] == "set"
    assert described["tool"]["inputSchema"]["oneOf"] == entry["input_schema"]["oneOf"]
    assert described["tool"]["inputSchema"]["required"] == ["action", "port"]


@pytest.mark.parametrize("nullable", [False, True])
def test_local_enum_references_expose_every_public_selector(nullable):
    from services.registry.action_catalog import declared_actions

    ref = {"$ref": "#/$defs/Operation"}
    field = {"anyOf": [ref, {"type": "null"}]} if nullable else ref
    entry = tool("ports", [])
    entry["input_schema"] = {
        "type": "object",
        "$defs": {"Operation": {"type": "string", "enum": ["inspect", "adjust"]}},
        "properties": {"action": field},
        "required": ["action"],
    }
    assert declared_actions(entry) == ["inspect", "adjust"]


def test_plain_enum_cards_keep_the_published_operation_limits():
    entry = tool("ports", ["stop", "inspect"])
    entry["description"] = "Port operations.\n- stop: Stop polling locally. The port keeps running."
    request = task_search.SearchRequest(selection="agent", family="ports", query="ports")
    page = task_search.search_catalog(request, [entry])
    cards = {card["action"]: card for card in page["operations"]}
    assert cards["stop"]["purpose"] == "Stop polling locally. The port keeps running."
    assert cards["stop"]["purpose_source"] == "declared"
    assert cards["inspect"]["purpose_source"] == "identifier"


@pytest.mark.parametrize("alternate", [{"enum": ["inspect", "close"]}, {"type": "string"}])
@pytest.mark.asyncio
async def test_describe_preserves_each_valid_input_form_for_a_shared_selector(alternate):
    from fastmcp import Client, FastMCP
    from fastmcp.tools import Tool
    from jsonschema import Draft202012Validator
    from services.tools import register_all_tools

    def route(action: str, port=None, path=None, meter=None):
        """Work with a meter."""
        pytest.fail("Describing the operation invoked it")

    schema = {
        "type": "object",
        "properties": {"action": {"enum": ["inspect", "open", "close"]}},
        "oneOf": [
            {
                "properties": {"action": {"enum": ["inspect", "open"]}},
                "required": ["action", "meter", "port"],
                "description": "Operate by port.",
            },
            {
                "properties": {"action": alternate},
                "required": ["action", "path", "meter"],
                "description": "Operate by path.",
            },
        ],
    }
    server = FastMCP("alternative-arguments")
    register_all_tools(server)
    definition = Tool.from_function(route)
    definition.parameters = schema
    server.add_tool(definition)
    async with Client(server, mode="legacy", cache=False) as client:
        public = next(t.input_schema for t in await client.list_tools() if t.name == "route")
        result = (
            await client.call_tool(
                "manage_tools",
                {
                    "action": "describe",
                    "tool": "route",
                    "operation": "inspect",
                },
            )
        ).structured_content
    card = result["operation"]
    assert card["required_parameters"] == ["meter"]
    assert card["purpose_source"] == "identifier"
    bound = result["tool"]["input_schema"]
    assert bound["oneOf"] == public["oneOf"]
    for extra in ({"port": "first"}, {"path": "a"}):
        payload = {"action": "inspect", "meter": 1, **extra}
        Draft202012Validator(public).validate(payload)
        Draft202012Validator(bound).validate(payload)


@pytest.mark.asyncio
async def test_agent_search_and_describe_match_live_enum_and_open_selector_contracts():
    from enum import Enum

    from fastmcp import Client, FastMCP
    from models.models import ToolDefinitionModel
    from services.custom_tool_schema_tool import SchemaTool, install_schema_listing_policy
    from services.resources import register_all_resources
    from services.tools import register_all_tools

    class Reading(str, Enum):
        inspect = "inspect_reading"
        change = "change_reading"

    server = FastMCP("dynamic-meter-discovery")
    register_all_tools(server)
    register_all_resources(server)
    install_schema_listing_policy(server)

    async def unused_handler(*_args):
        pytest.fail("Discovery dispatched a rich meter action")

    server.add_tool(
        SchemaTool(
            ToolDefinitionModel(
                name="rich_meter",
                schema_version=1,
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "$defs": {"Operation": {"type": "string", "enum": ["inspect", "adjust"]}},
                    "properties": {
                        "action": {"anyOf": [{"$ref": "#/$defs/Operation"}, {"type": "null"}]}
                    },
                    "required": ["action"],
                },
            ),
            unused_handler,
        )
    )

    @server.tool()
    def selected_meter(action: Reading):
        """Operate a meter."""
        pytest.fail("Discovery dispatched a meter action")

    @server.tool()
    def open_meter(action: str):
        """Pass a selector to the connected meter."""
        pytest.fail("Discovery dispatched a meter action")

    async with Client(server, mode="legacy", cache=False) as client:
        tools = {t.name: t for t in await client.list_tools()}
        args = {"selection": "agent", "query": "meter", "family": "selected_meter"}
        reply = (await client.call_tool("search_unity_tools", args)).structured_content
        wanted = tools["selected_meter"].input_schema["properties"]["action"]["enum"]
        assert {card["action"] for card in reply["operations"]} == set(wanted)
        for action in wanted:
            result = await client.call_tool(
                "manage_tools",
                {
                    "action": "describe",
                    "tool": "selected_meter",
                    "operation": action,
                },
            )
            assert result.structured_content["operation"]["invoke"]["arguments"] == {
                "action": action
            }
        args["family"] = "open_meter"
        (card,) = (await client.call_tool("search_unity_tools", args)).structured_content[
            "operations"
        ]
        assert card["action"] is None and card["required_parameters"] == ["action"]
        args["family"] = "rich_meter"
        del args["query"]
        reply = await client.call_tool("manage_tools", {"action": "search", **args})
        assert reply.structured_content["query"] is None
        assert {c["action"] for c in reply.structured_content["operations"]} == {
            "inspect",
            "adjust",
        }
        described = await client.call_tool(
            "manage_tools",
            {
                "action": "describe",
                "tool": "rich_meter",
                "operation": "inspect",
            },
        )
        assert described.structured_content["operation"]["invoke"]["arguments"] == {
            "action": "inspect"
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "ranked_only", "ranked_mode"),
    [
        ("search_unity_tools", {"intent": "observe", "catalog_query": "inspect ports"}, "lexical"),
        (
            "manage_tools",
            {
                "intent": "interact",
                "observation_source": "editor_live",
                "required_values": ["render_size.width", "render_size.height"],
            },
            "observation",
        ),
    ],
)
async def test_conflicting_search_modes_offer_valid_scoped_retries(
    tool_name, ranked_only, ranked_mode
):
    from fastmcp import Client, FastMCP
    from services.tools import register_all_tools

    server = FastMCP("search-mode-recovery")
    register_all_tools(server)

    @server.tool()
    def wire_meter() -> str:
        """Inspect ports and render size."""
        pytest.fail("Discovery dispatched a tool")

    baseline = {
        "query": "  포트와 렌더 크기를 확인해 줘.  ",
        "limit": 2,
        "route_surfaces": "tools",
        **(
            {"host_tool_names": ["wire_meter"]}
            if ranked_mode == "lexical"
            else {
                "host_tool_evidence": {
                    "candidates": ["wire_meter"],
                    "present": ["wire_meter"],
                    "complete": True,
                }
            }
        ),
    }
    wrapper = {"action": "search"} if tool_name == "manage_tools" else {}
    async with Client(server, mode="legacy", cache=False) as client:
        invalid = (
            await client.call_tool(
                tool_name, {**wrapper, **baseline, **ranked_only, "selection": "agent"}
            )
        ).structured_content
        assert invalid["success"] is False
        assert set(invalid["incompatible_fields"]) == set(ranked_only)
        retries = {retry["arguments"]["selection"]: retry for retry in invalid["search_retries"]}
        assert set(retries) == ({"ranked", "agent"} if ranked_mode == "lexical" else {"ranked"})
        ranked = retries["ranked"]["arguments"]
        assert ranked == {**wrapper, **baseline, **ranked_only, "selection": "ranked"}
        ranked_reply = (await client.call_tool(tool_name, ranked)).structured_content
        assert ranked_reply["matching"] == ranked_mode
        if ranked_mode == "observation":
            assert ranked_reply["routes"] == []
            assert ranked_reply["no_match"]
            separate_action = ranked_reply["retry_search"]
            assert set(separate_action["omit"]) == {"observation_source", "required_values"}
            assert separate_action["arguments"] == {
                **wrapper,
                **baseline,
                "selection": "ranked",
                "intent": "interact",
            }
        else:
            browse_retry = retries["agent"]["arguments"]
            assert browse_retry == {**baseline, "selection": "agent"}
            browse = (
                await client.call_tool(tool_name, {**wrapper, **browse_retry})
            ).structured_content
            assert [family["family"] for family in browse["families"]] == ["wire_meter"]


def test_cli_can_browse_an_exact_family_without_repeating_the_original():
    import json

    from click.testing import CliRunner
    from cli.main import cli

    result = CliRunner().invoke(
        cli, ["tool", "search", "--selection", "agent", "--family", "manage_scene", "--limit", "20"]
    )

    assert result.exit_code == 0, result.output
    page = json.loads(result.stdout)
    assert page["query"] is None and page["family"] == "manage_scene"
    assert any(row["action"] == "set_active_scene" for row in page["operations"])


@pytest.mark.asyncio
async def test_manage_tools_ranked_offset_offers_scoped_agent_page():
    from typing import Literal

    from fastmcp import Client, FastMCP
    from services.tools import register_all_tools

    server = FastMCP("search-offset-recovery")
    register_all_tools(server)

    @server.tool()
    def wire_ports(action: Literal["inspect", "adjust"]) -> str:
        """Read or adjust ports."""
        pytest.fail("Discovery dispatched a tool")

    arguments = {
        "action": "search",
        "family": "wire_ports",
        "offset": 1,
        "limit": 1,
        "route_surfaces": "tools",
        "host_tool_names": ["wire_ports"],
    }
    async with Client(server, mode="legacy", cache=False) as client:
        result = (await client.call_tool("manage_tools", arguments)).structured_content
        assert result["incompatible_fields"] == ["family", "offset"]
        (retry,) = result["search_retries"]
        assert retry["arguments"] == {**arguments, "selection": "agent"}
        page = (await client.call_tool("manage_tools", retry["arguments"])).structured_content
    assert page["family"] == "wire_ports" and page["offset"] == 1
    assert [card["action"] for card in page["operations"]] == ["inspect"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"selection": "agent", "intent": "edit"},
        {"selection": "agent", "catalog_query": "ports"},
        {"selection": "agent", "observation_source": "editor_live"},
        {"family": "ports"},
        {"offset": 3},
    ],
)
def test_family_selection_never_silently_filters_away_siblings(arguments):
    with pytest.raises(ValueError):
        task_search.SearchRequest(query="request", **arguments)
