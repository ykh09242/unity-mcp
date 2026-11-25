"""Assertions for browsing actual MCP bindings from the frozen discovery fixtures."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
import socket
import subprocess
from typing import Literal
from unittest.mock import patch

from fastmcp import Client, FastMCP

from core.config import config
from services.resources import register_all_resources
from services.tools import register_all_tools
from transport.legacy import unity_connection
from transport.plugin_hub import PluginHub

from tools.experiments.discovery_evaluation_fixture import EvaluationSuite
from tools.experiments.universal_discovery_evaluation import (
    SEARCH,
    Inventory,
    SearchBoundary,
    read_inventory,
)

_PAGE_SIZE = 20
_SURFACE_KINDS = {
    "tools": {"tool"},
    "resources": {"resource", "resource_template"},
    "both": {"tool", "resource", "resource_template"},
}
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


@dataclass(frozen=True, slots=True)
class AgentBrowse:
    query: str
    surface: Literal["tools", "resources", "both"] = "both"
    entry: Literal["search_unity_tools", "manage_tools"] = SEARCH


@dataclass(frozen=True, slots=True)
class ObservationBinding:
    tool: str
    value: str
    action: str | None = None
    uri: str | None = None


@dataclass(frozen=True, slots=True)
class AgentCatalog:
    client: Client
    inventory: Inventory


class _CatalogBoundary(SearchBoundary):
    async def on_call_tool(self, context, call_next):
        args = context.message.arguments or {}
        if (
            context.message.name == "manage_tools"
            and args.get("action") == "search"
            and args.get("selection") == "agent"
        ):
            return await call_next(context)
        return await super().on_call_tool(context, call_next)


async def _browse(
    client: Client, request: AgentBrowse, family: str | None = None, *, limit: int = _PAGE_SIZE
) -> list[dict]:
    arguments = {
        "selection": "agent",
        "query": request.query,
        "route_surfaces": request.surface,
        "limit": limit,
        "offset": 0,
    }
    if request.entry == "manage_tools":
        arguments["action"] = "search"
    if family is not None:
        arguments["family"] = family
    view = "families" if family is None else "operations"
    rows = []
    total = None
    while True:
        response = (await client.call_tool(request.entry, arguments)).structured_content
        assert response["selection"] == "agent" and response["matching"] == "catalog", response
        assert response["scope"] == "mcp_context" and response["view"] == view, response
        assert response["query"] == request.query, response
        assert response["route_surfaces"] == request.surface, response
        assert response["offset"] == len(rows), response
        if family is not None:
            assert response["family"] == family, response
        page = response[view]
        assert len(page) <= limit, response
        assert all(row["kind"] in _SURFACE_KINDS[request.surface] for row in page), response
        if total is None:
            total = response["total"]
        assert total == response["total"], response
        rows.extend(page)
        next_offset = response["next_offset"]
        if next_offset is None:
            assert len(rows) == total, response
            ids = [_binding(row) if family is not None else row["family"] for row in rows]
            assert len(ids) == len(set(ids)), (request, family, ids)
            return rows
        assert arguments["offset"] < next_offset == len(rows) < total, response
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


@asynccontextmanager
async def agent_catalog(
    profile: str, bindings: set[str], *, mode: Literal["auto", "legacy"] = "legacy"
) -> AsyncIterator[AgentCatalog]:
    scope = {"UNITY_MCP_TOOL_PROFILE": profile, "UNITY_MCP_DISABLE_TELEMETRY": "1"}
    denied = AssertionError("Agent discovery crossed the offline catalog boundary")
    with (
        patch.object(os, "environ", {**os.environ, **scope}),
        patch.object(config, "http_remote_hosted", False),
        patch.object(PluginHub, "send_command_for_instance", side_effect=denied) as http,
        patch.object(unity_connection, "_async_send_command_response", side_effect=denied) as stdio,
        patch.object(unity_connection, "_send_command_with_retry", side_effect=denied) as sync,
        patch("services.registry.task_search._term_evidence", side_effect=denied) as ranking,
    ):
        os.environ.pop("UNITY_MCP_TOOL_GROUPS", None)
        boundary = _CatalogBoundary()
        server = FastMCP("agent-discovery-contract", middleware=[boundary])
        register_all_tools(server)
        register_all_resources(server)
        async with Client(server, mode=mode, cache=False) as client:
            before = await read_inventory(client, bindings)
            with (
                patch.object(socket, "getaddrinfo", side_effect=denied) as dns,
                patch.object(socket.socket, "connect", side_effect=denied) as connect,
                patch.object(socket.socket, "connect_ex", side_effect=denied) as connect_ex,
                patch.object(subprocess, "Popen", side_effect=denied) as subprocess_start,
            ):
                yield AgentCatalog(client, before)
            assert not any(probe.called for probe in (dns, connect, connect_ex, subprocess_start))
            assert await read_inventory(client, bindings) == before
        assert not boundary.unexpected_calls
        assert not any(probe.called for probe in (http, stdio, sync, ranking))


async def assert_observation_browsable(
    catalog: AgentCatalog, request: AgentBrowse, target: ObservationBinding
) -> None:
    families = await _browse(catalog.client, request)
    tool_binding = f"{target.tool}.{target.action}" if target.action else target.tool
    expected = set()
    if request.surface != "resources":
        expected.add(tool_binding)
    if request.surface != "tools" and target.uri:
        expected.add(target.uri)
    assert expected and expected <= catalog.inventory.actions, (request, expected)

    found = set()
    for family in families:
        family_id = family["family"]
        exposes_tool = target.tool in family.get("tools", ())
        if not exposes_tool and family_id != target.uri:
            continue
        cards = await _browse(catalog.client, request, family_id, limit=1)
        assert len(cards) == family["count"], (request, family)
        if exposes_tool:
            bindable = {
                binding
                for binding in catalog.inventory.actions
                if binding.partition(".")[0] == target.tool
            }
            assert {_binding(card) for card in cards if card.get("tool") == target.tool} == bindable
        for card in cards:
            binding = _binding(card)
            if binding not in {tool_binding, target.uri}:
                continue
            if binding == tool_binding:
                assert card["kind"] == "tool" and card["tool"] == target.tool, card
                assert card["action"] == target.action, card
            else:
                assert card["kind"] == "resource" and card["uri"] == target.uri, card
            assert card["advertised"] is True and card["readiness"] == "unobserved", card
            assert card["intent"] == "observe" and card["required_parameters"] == [], card
            assert card["observation"]["source"] == "editor_live", card
            assert target.value in card["observation"]["values"], card
            found.add(binding)
    assert found == expected, (request, expected, found)


async def assert_public_agent_contract(
    profile: str,
    suite: EvaluationSuite,
    *,
    shared_family: bool = False,
    exclusions: BindingExclusions | None = None,
) -> None:
    """Check the catalog evidence exposed for each request, without picking a winner."""
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

    async with agent_catalog(profile, fixture_bindings) as catalog:
        client, before = catalog.client, catalog.inventory
        assert before.actions & fixture_bindings == visible_bindings, profile
        for request in suite.requests:
            browse = AgentBrowse(request.query)
            families = await _browse(client, browse)
            family_names = {family["family"] for family in families}
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
                operations = await _browse(client, browse, family_name)
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
