"""Portable boundary tests; native transform behavior has a separate Unity fixture."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from services.tools.manage_gameobject import manage_gameobject


@pytest.fixture
def transport(monkeypatch):
    sender = AsyncMock(return_value={"success": True, "data": {"preview_token": "owned"}})
    monkeypatch.setattr("services.tools.manage_gameobject.send_with_unity_instance", sender)
    monkeypatch.setattr(
        "services.tools.manage_gameobject.get_unity_instance_from_context",
        AsyncMock(return_value="owned"),
    )
    monkeypatch.setattr("services.tools.manage_gameobject.preflight", AsyncMock(return_value=None))
    return sender


def invoke(**kwargs):
    async def run():
        return await manage_gameobject(SimpleNamespace(), **kwargs)

    return anyio.run(run)


def test_explicit_transform_contract_reaches_native(transport):
    # Given a fully explicit reparent and transform request.
    options = dict(
        action="modify",
        target="123",
        space="world",
        position=[1, 2, 3],
        rotation=[0, 90, 0],
        world_position_stays=False,
        detach=True,
        sibling_index=0,
    )
    # When the public adapter processes the request.
    result = invoke(**options)
    # Then false and zero survive transport without a synthetic parent selector.
    assert result["success"]
    wire = transport.call_args.args[3]
    assert wire["space"] == "world"
    assert wire["world_position_stays"] is False
    assert wire["detach"] is True
    assert wire["sibling_index"] == 0
    assert "parent" not in wire


@pytest.mark.parametrize(
    "options",
    [
        {"space": "world", "scale": [1, 1, 1]},
        {"space": "screen"},
        {"detach": True, "parent": "Parent"},
        {"sibling_index": -1},
        {"sibling_index": True},
        {"world_position_stays": "not-a-bool"},
    ],
)
def test_invalid_transform_rejects_before_transport(transport, options):
    # Given malformed or unsupported explicit transform semantics.
    # When validation runs, then no native operation is sent.
    result = invoke(action="modify", target="123", **options)
    assert not result["success"]
    transport.assert_not_awaited()


def test_ordered_placement_preview_and_apply_forward_without_single_target(transport):
    # Given ordered selectors and a preview token returned by native Unity.
    options = dict(
        action="placement",
        targets=["3", "1", "2"],
        placement_mode="distribute",
        axis="x",
        space="world",
    )
    # When preview and apply are requested.
    assert invoke(**options)["success"]
    preview_wire = transport.call_args.args[3]
    assert invoke(**options, preview=False, preview_token="owned")["success"]
    # Then order, preview default, and the apply token survive exactly.
    assert preview_wire["targets"] == ["3", "1", "2"]
    assert preview_wire["preview"] is True
    wire = transport.call_args.args[3]
    assert wire["preview"] is False
    assert wire["preview_token"] == "owned"
    assert "target" not in wire


@pytest.mark.parametrize(
    "options",
    [
        {"targets": ["1"]},
        {"targets": ["1", "1"]},
        {"targets": [str(i) for i in range(129)]},
        {"targets": ["1", "2"], "preview": False},
        {"targets": ["1", "2"], "axis": "w"},
        {"targets": ["1", "2"], "placement_mode": "scatter"},
    ],
)
def test_invalid_placement_rejects_before_transport(transport, options):
    # Given an invalid bounded placement request.
    # When admission runs, then transport stays untouched.
    result = invoke(action="placement", **options)
    assert not result["success"]
    transport.assert_not_awaited()


def test_legacy_modify_omits_new_transform_parameters(transport):
    # Given a legacy request using local-default position.
    # When it crosses the adapter, then no new explicit transform policy is injected.
    assert invoke(action="modify", target="123", position=[1, 2, 3])["success"]
    wire = transport.call_args.args[3]
    assert not {"space", "world_position_stays", "detach", "sibling_index"} & wire.keys()


def test_registered_schema_accepts_placement_without_single_target(transport):
    from fastmcp import Client, FastMCP

    # Given a real registered tool schema and client validation path.
    server = FastMCP("placement-contract")
    server.tool(manage_gameobject)

    async def run():
        async with Client(server) as client:
            schema = (await client.list_tools())[0].input_schema
            assert "targets" in schema["properties"]
            # When the bounded ordered preview is invoked through MCP.
            result = await client.call_tool(
                "manage_gameobject", {"action": "placement", "targets": ["3", "1", "2"]}
            )
            assert not result.is_error

    anyio.run(run)
    # Then native receives the order and preview default.
    assert transport.call_args.args[3]["targets"] == ["3", "1", "2"]
    assert transport.call_args.args[3]["preview"] is True


@pytest.mark.parametrize("index", [True, 1.0])
def test_registered_schema_does_not_coerce_sibling_index(transport, index):
    from fastmcp import Client, FastMCP

    # Given a JSON value that is not an integer sibling index.
    server = FastMCP("placement-integer-contract")
    server.tool(manage_gameobject)

    async def run():
        async with Client(server) as client:
            # When client-side schema/argument validation runs.
            result = await client.call_tool(
                "manage_gameobject",
                {"action": "modify", "target": "123", "sibling_index": index},
                raise_on_error=False,
            )
            assert result.is_error or result.structured_content.get("success") is False

    anyio.run(run)
    # Then true and floating-point values never become a different valid native index.
    transport.assert_not_awaited()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            [
                "modify",
                "123",
                "--space",
                "world",
                "--detach",
                "--no-world-position-stays",
                "--sibling-index",
                "0",
            ],
            {
                "action": "modify",
                "target": "123",
                "space": "world",
                "detach": True,
                "world_position_stays": False,
                "sibling_index": 0,
            },
        ),
        (
            ["create", "Owned", "--space", "local", "--sibling-index", "0"],
            {"action": "create", "name": "Owned", "space": "local", "sibling_index": 0},
        ),
        (
            ["placement", "3", "1", "2", "--mode", "distribute", "--axis", "z", "--space", "world"],
            {
                "action": "placement",
                "targets": ["3", "1", "2"],
                "placement_mode": "distribute",
                "axis": "z",
                "space": "world",
                "preview": True,
            },
        ),
        (
            ["placement", "3", "1", "--apply-token", "owned"],
            {
                "action": "placement",
                "targets": ["3", "1"],
                "placement_mode": "align",
                "axis": "x",
                "space": "local",
                "preview": False,
                "preview_token": "owned",
            },
        ),
    ],
)
def test_cli_explicit_transform_and_ordered_preview(monkeypatch, args, expected):
    from click.testing import CliRunner
    from cli.main import cli
    from cli.commands import gameobject as commands

    calls = []
    monkeypatch.setattr(
        commands,
        "run_command",
        lambda tool, params, config: calls.append(params) or {"success": True},
    )
    # Given the ordinary CLI command surface, when new explicit options are supplied.
    result = CliRunner().invoke(cli, ["--format", "json", "gameobject", *args])
    # Then a single ordered native request is emitted.
    assert result.exit_code == 0, result.output
    assert calls == [expected]


@pytest.mark.parametrize(
    "args",
    [
        ["modify", "123", "--space", "world", "--scale", "1", "1", "1"],
        ["create", "Owned", "--space", "world", "--scale", "1", "1", "1"],
        ["modify", "123", "--parent", "Parent", "--detach"],
        ["modify", "123", "--sibling-index", "-1"],
        ["placement", "123"],
        ["placement", "123", "123"],
    ],
)
def test_cli_invalid_placement_policy_does_not_send(monkeypatch, args):
    from click.testing import CliRunner
    from cli.main import cli
    from cli.commands import gameobject as commands

    calls = []
    monkeypatch.setattr(commands, "run_command", lambda *args: calls.append(args))
    # Given invalid explicit policy, when the CLI validates, then native is never called.
    result = CliRunner().invoke(cli, ["gameobject", *args])
    assert result.exit_code != 0
    assert calls == []
