"""Assertions for browsing actual MCP bindings from the frozen discovery fixtures."""

from dataclasses import dataclass
import os
from unittest.mock import patch

from fastmcp import Client, FastMCP

from core.config import config
from services.resources import register_all_resources
from services.tools import register_all_tools
from transport.legacy import unity_connection
from transport.plugin_hub import PluginHub

from tools.experiments.discovery_evaluation_fixture import EvaluationSuite, Request
from tools.experiments.universal_discovery_evaluation import SEARCH, SearchBoundary, read_inventory

_PAGE_SIZE = 20
_EDIT_ACTIONS = {
    "add_layer",
    "add_tag",
    "close_scene",
    "load",
    "remove_layer",
    "remove_tag",
    "set_active_scene",
}
_REQUIRED_PARAMETERS = {
    "add_layer": {"layer_name"},
    "add_tag": {"tag_name"},
    "get_package_info": {"package"},
    "list": {"scene_handle", "kind"},
    "list_packages": set(),
    "read": {"scene_handle", "kind", "target"},
    "remove_layer": {"layer_name"},
    "remove_tag": {"tag_name"},
}


@dataclass(frozen=True, slots=True)
class BindingExclusions:
    inventory: frozenset[str]
    catalog_only: frozenset[str] = frozenset()


async def _browse(client: Client, request: Request, family: str | None = None) -> list[dict]:
    arguments = {"selection": "agent", "query": request.query, "limit": _PAGE_SIZE, "offset": 0}
    if family is not None:
        arguments["family"] = family
    view = "families" if family is None else "operations"
    rows = []
    while True:
        response = (await client.call_tool(SEARCH, arguments)).structured_content
        assert response["selection"] == "agent" and response["matching"] == "catalog", response
        assert response["scope"] == "mcp_context" and response["view"] == view, response
        assert response["query"] == request.query, response
        assert response["offset"] == len(rows), response
        if family is not None:
            assert response["family"] == family, response
        rows.extend(response[view])
        next_offset = response["next_offset"]
        if next_offset is None:
            assert len(rows) == response["total"], response
            return rows
        assert arguments["offset"] < next_offset == len(rows) < response["total"], response
        arguments["offset"] = next_offset


def _binding(card: dict) -> str:
    if card["kind"] == "tool":
        tool, action = card["tool"], card["action"]
        assert card["invoke"] == {"tool": tool, "arguments": {"action": action} if action else {}}
        return f"{tool}.{action}" if action else tool
    return card.get("uri") or card["uri_template"]


def _assert_declared_effect(binding: str, operation: dict) -> None:
    _, _, action = binding.partition(".")
    assert operation["intent"] == ("edit" if action in _EDIT_ACTIONS else "observe"), binding
    assert operation["purpose"].strip(), binding
    if operation["kind"] == "tool":
        assert operation["purpose_source"] == "declared", binding
        assert operation["effects_scope"] in {"tool", "action"}, binding
        assert isinstance(operation["effects"]["readOnlyHint"], bool), binding
        if action in _EDIT_ACTIONS:
            assert operation["effects"]["readOnlyHint"] is False, binding
    if action in _REQUIRED_PARAMETERS:
        assert set(operation["required_parameters"]) == _REQUIRED_PARAMETERS[action], binding
    if action == "get_active":
        observation = operation["observation"]
        assert observation["source"] == "editor_live", binding
        assert {"active_scene.name", "active_scene.path"} <= set(observation["values"]), binding


async def assert_public_agent_contract(
    profile: str,
    suite: EvaluationSuite,
    *,
    shared_family: bool = False,
    exclusions: BindingExclusions | None = None,
) -> None:
    """Check the catalog evidence exposed for each request, without picking a winner."""
    scope = {"UNITY_MCP_TOOL_PROFILE": profile, "UNITY_MCP_DISABLE_TELEMETRY": "1"}
    with (
        patch.dict(os.environ, scope),
        patch.object(config, "http_remote_hosted", False),
        patch.object(
            PluginHub, "send_command_for_instance", side_effect=AssertionError("native HTTP send")
        ) as http,
        patch.object(
            unity_connection,
            "_async_send_command_response",
            side_effect=AssertionError("async stdio send"),
        ) as stdio_async,
        patch.object(
            unity_connection,
            "_send_command_with_retry",
            side_effect=AssertionError("sync stdio send"),
        ) as stdio_sync,
    ):
        os.environ.pop("UNITY_MCP_TOOL_GROUPS", None)
        boundary = SearchBoundary()
        server = FastMCP("agent-discovery-contract", middleware=[boundary])
        register_all_tools(server)
        register_all_resources(server)
        fixture_bindings = set().union(
            *(request.expected | request.wrong_effect for request in suite.requests)
        )
        hidden_tools = {"manage_scene"} if profile == "core" else set()
        visible_bindings = {
            binding for binding in fixture_bindings if binding.partition(".")[0] not in hidden_tools
        }
        catalog_exclusions = exclusions.catalog_only if exclusions else frozenset()
        if exclusions:
            visible_bindings.difference_update(exclusions.inventory)
        eligible_bindings = visible_bindings - catalog_exclusions

        async with Client(server, mode="legacy", cache=False) as client:
            before = await read_inventory(client, fixture_bindings)
            assert before.actions & fixture_bindings == visible_bindings, profile
            for request in suite.requests:
                families = await _browse(client, request)
                family_names = {family["family"] for family in families}
                assert len(family_names) == len(families), request
                assert family_names.isdisjoint(hidden_tools), request
                candidates = request.expected | request.wrong_effect
                candidate_tools = {binding.partition(".")[0] for binding in candidates}
                cards = {}
                matched_families = set()
                for family in families:
                    family_name = family["family"]
                    family_tools = set(family.get("tools", ()))
                    assert (family_tools or {family_name}) <= before.names, family
                    if family_name not in candidates and candidate_tools.isdisjoint(family_tools):
                        continue
                    operations = await _browse(client, request, family_name)
                    assert len(operations) == family["count"], family
                    for operation in operations:
                        binding = _binding(operation)
                        assert binding in before.actions and binding not in cards, operation
                        assert operation["advertised"] is True, operation
                        assert operation["readiness"] == "unobserved", operation
                        cards[binding] = operation
                        if binding in candidates:
                            matched_families.add(family_name)

                expected = request.expected & eligible_bindings
                opposite = request.wrong_effect & eligible_bindings
                candidate_cards = cards.keys() & candidates
                assert candidate_cards == expected | opposite, (request, candidate_cards)
                siblings = {
                    binding
                    for binding in before.actions
                    if binding.partition(".")[0] in candidate_tools
                }
                siblings.difference_update(catalog_exclusions)
                assert siblings <= cards.keys(), (request, siblings - cards.keys())
                if shared_family:
                    assert len(matched_families) == 1, (request, matched_families)
                if exclusions is None:
                    assert expected and opposite, request
                    for binding in candidate_cards:
                        _assert_declared_effect(binding, cards[binding])
                expected_purposes = {
                    cards[binding]["purpose"].strip().casefold() for binding in expected
                }
                opposite_purposes = {
                    cards[binding]["purpose"].strip().casefold() for binding in opposite
                }
                assert expected_purposes.isdisjoint(opposite_purposes), request

            assert await read_inventory(client, fixture_bindings) == before
        assert not boundary.unexpected_calls
        assert http.call_count == stdio_async.call_count == stdio_sync.call_count == 0
