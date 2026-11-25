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

    def request(**extra):
        return task_search.search_catalog(
            task_search.SearchRequest(selection="agent", query=query, limit=2, **extra),
            entries,
            visible_names={"ports"},
        )

    index = request()
    assert index["query"] == query and index["matching"] == "catalog"
    assert [f["family"] for f in index["families"]] == ["ports"]
    first = request(family="ports")
    last = request(family="ports", offset=first["next_offset"])
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
    assert request(family="private_port").get("success") is False
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
        reply = await client.call_tool("manage_tools", {"action": "search", **args})
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
