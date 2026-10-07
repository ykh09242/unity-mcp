"""Scalar contracts at the shared registry and real in-memory SDK boundary."""

import asyncio
import importlib
import inspect
from typing import Annotated, Optional, get_type_hints

import pytest
from fastmcp import Client, Context, FastMCP
from pydantic import AfterValidator, BeforeValidator, Field, TypeAdapter

from services.registry import get_registered_tools, mcp_for_unity_tool
import services.registry.tool_registry as registry


@pytest.fixture(autouse=True)
def restore_registry():
    original_fixtures = [t for t in registry._tool_registry if t["name"] == "scalar_fixture"]
    yield
    registry._tool_registry[:] = [
        t for t in registry._tool_registry if t["name"] != "scalar_fixture"
    ]
    registry._tool_registry.extend(original_fixtures)


def register_fixture(server):
    calls, validations = [], []

    def before(value):
        validations.append(("before", value))
        return value

    def after(value):
        validations.append(("after", value))
        return value

    # Deliberately exercise a mutable collection default in the registered schema.
    async def scalar_fixture(  # pylint: disable=dangerous-default-value
        ctx: Context,
        flag: Annotated[bool, "Boolean flag description"] = True,
        count: Optional[
            Annotated[
                int,
                BeforeValidator(before),
                AfterValidator(after),
                Field(strict=False, ge=0, le=10, description="Bounded count description"),
            ]
        ] = 3,
        ratio: Annotated[float, "Numeric ratio description"] = 1.0,
        rows: Annotated[list[dict[str, list[int | float]]], "Nested rows description"] = [],
        flags: dict[str, bool] | None = None,
        selector: int | str | None = None,
        boolean_or_count: bool | int | None = None,
    ) -> dict:
        payload = {
            "flag": flag,
            "count": count,
            "ratio": ratio,
            "rows": rows,
            "flags": flags,
            "selector": selector,
            "boolean_or_count": boolean_or_count,
        }
        calls.append(payload)
        return payload

    original_signature = inspect.signature(scalar_fixture)
    original_return = scalar_fixture.__annotations__["return"]
    decorated = mcp_for_unity_tool()(scalar_fixture)
    assert decorated is scalar_fixture
    assert inspect.iscoroutinefunction(decorated)
    assert decorated.__annotations__["return"] is original_return
    assert inspect.signature(decorated).parameters["ctx"].annotation is Context
    assert [
        (p.name, p.kind, p.default) for p in inspect.signature(decorated).parameters.values()
    ] == [(p.name, p.kind, p.default) for p in original_signature.parameters.values()]
    metadata = next(t for t in get_registered_tools() if t["name"] == "scalar_fixture")
    server.tool(name=metadata["name"], **metadata["kwargs"])(metadata["func"])
    return calls, validations


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_registry_preserves_scalar_types_and_metadata_at_sdk_boundary(mode):
    server = FastMCP("scalar-registry-contract")
    calls, validations = register_fixture(server)

    async def exercise():
        async with Client(server, mode=mode) as client:
            tool = next(t for t in await client.list_tools() if t.name == "scalar_fixture")
            properties = tool.input_schema["properties"]
            for name, description in {
                "flag": "Boolean flag description",
                "count": "Bounded count description",
                "ratio": "Numeric ratio description",
                "rows": "Nested rows description",
            }.items():
                assert properties[name].get("description") == description
            errors = []
            invalid = (
                [{"flag": value} for value in (0, 1, "false", "true")]
                + [{"count": value} for value in (True, False, 0.5, 1.0, "1", -1, 11)]
                + [
                    {"ratio": value}
                    for value in (True, False, "2.5", float("nan"), float("inf"), float("-inf"))
                ]
                + [
                    {"rows": [{"vector": [1, True]}]},
                    {"flags": {"enabled": 0}},
                    {"selector": True},
                ]
            )
            for payload in invalid:
                before = len(calls)
                result = await client.call_tool("scalar_fixture", payload, raise_on_error=False)
                if not result.is_error or len(calls) != before:
                    errors.append(f"{payload!r} reached body as {calls[-1]!r}")
            assert not errors, "\n".join(errors)

            for payload in (
                {},
                {"flag": False, "count": 0, "ratio": 2},
                {"count": None, "selector": "42", "flags": {"enabled": False}},
                {"selector": 42, "rows": [{"vector": [1, 2.5]}]},
                {"boolean_or_count": True},
                {"boolean_or_count": 1},
            ):
                result = await client.call_tool("scalar_fixture", payload)
                assert not result.is_error
                assert calls[-1].items() >= payload.items()
            assert type(calls[-2]["boolean_or_count"]) is bool
            assert type(calls[-1]["boolean_or_count"]) is int
            assert ("after", 0) in validations
            assert validations.index(("before", 0)) < validations.index(("after", 0))

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_synchronous_tools_keep_container_shapes_and_scalar_types(mode):
    calls = []

    @mcp_for_unity_tool()
    def scalar_fixture(
        coordinates: tuple[int, float],
        choices: set[int],
        frozen: frozenset[int],
    ) -> dict:
        calls.append((coordinates, choices, frozen))
        return {"success": True}

    assert not inspect.iscoroutinefunction(scalar_fixture)
    server = FastMCP("sync-scalar-contract")
    server.tool(scalar_fixture)

    async def exercise():
        async with Client(server, mode=mode) as client:
            valid = {"coordinates": [1, 2], "choices": [0, 1], "frozen": [2]}
            result = await client.call_tool("scalar_fixture", valid)
            assert not result.is_error
            assert calls == [((1, 2.0), {0, 1}, frozenset({2}))]
            for override in (
                {"coordinates": [True, 2]},
                {"coordinates": [1, True]},
                {"choices": [False]},
                {"frozen": [True]},
            ):
                result = await client.call_tool(
                    "scalar_fixture", {**valid, **override}, raise_on_error=False
                )
                assert result.is_error
                assert len(calls) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_actual_tools_reject_wrong_scalar_types_before_transport(monkeypatch, mode):
    import_model = importlib.import_module("services.tools.import_model_file")
    ui = importlib.import_module("services.tools.manage_ui")
    find = importlib.import_module("services.tools.find_gameobjects")
    sprite = importlib.import_module("services.tools.manage_sprite")
    sent = []

    async def send(*args, **kwargs):
        sent.append(args[-1])
        return {"success": True, "data": {}}

    async def preflight(*args, **kwargs):
        return None

    for module in (import_model, ui, find, sprite):
        monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(ui, "send_mutation", send)
    monkeypatch.setattr(find, "preflight", preflight)
    server = FastMCP("actual-scalar-contract")
    for module, name in (
        (import_model, "import_model_file"),
        (ui, "manage_ui"),
        (find, "find_gameobjects"),
        (sprite, "manage_sprite"),
    ):
        metadata = next(t for t in get_registered_tools() if t["name"] == name)
        server.tool(name=name, **metadata["kwargs"])(getattr(module, name))

    async def exercise():
        async with Client(server, mode=mode) as client:
            errors = []
            for name, payload in (
                ("import_model_file", {"source_path": "Assets/Model.fbx", "target_size": True}),
                ("manage_ui", {"action": "modify_visual_element", "enabled": 0}),
                ("manage_ui", {"action": "get_visual_tree", "max_depth": True}),
                ("find_gameobjects", {"search_term": "Cube", "page_size": True}),
                ("find_gameobjects", {"search_term": "Cube", "include_inactive": 0}),
                (
                    "manage_sprite",
                    {"action": "slice_sheet", "path": "Assets/hero.png", "cols": True},
                ),
                (
                    "manage_sprite",
                    {"action": "slice_sheet", "path": "Assets/hero.png", "cols": 1.5},
                ),
                (
                    "manage_sprite",
                    {"action": "get_info", "path": "Assets/hero.png", "page_size": True},
                ),
                (
                    "manage_sprite",
                    {"action": "full_setup", "path": "Assets/hero.png", "cols": 4, "overwrite": 1},
                ),
                (
                    "manage_sprite",
                    {
                        "action": "full_setup",
                        "path": "Assets/hero.png",
                        "cols": 4,
                        "add_to_scene": "false",
                    },
                ),
            ):
                before = len(sent)
                result = await client.call_tool(name, payload, raise_on_error=False)
                if not result.is_error or len(sent) != before:
                    errors.append(
                        f"{name} {payload!r} -> {sent[-1] if len(sent) > before else None!r}"
                    )
            assert not errors, "\n".join(errors)
            await client.call_tool(
                "import_model_file", {"source_path": "Assets/Model.fbx", "target_size": 2}
            )
            assert sent[-1]["targetSize"] == 2.0
            await client.call_tool(
                "find_gameobjects",
                {"search_term": "Cube", "page_size": "5", "include_inactive": "false"},
            )
            assert sent[-1]["pageSize"] == 5 and sent[-1]["includeInactive"] is False
            await client.call_tool(
                "manage_sprite",
                {
                    "action": "get_info",
                    "path": "Assets/hero.png",
                    "page_size": 5,
                    "cursor": 0,
                },
            )
            assert sent[-1]["page_size"] == 5 and sent[-1]["cursor"] == 0

    asyncio.run(exercise())


def test_entire_registered_catalog_has_strict_scalar_leaf_schemas():
    from pathlib import Path
    from services.tools import __package__ as package
    from utils.module_discovery import discover_modules

    tools_dir = Path(importlib.import_module("services.tools").__file__).parent
    list(discover_modules(tools_dir, package))
    checked, failures = [], []

    def visit(schema, label):
        if isinstance(schema, dict):
            if "JsonValue" in str(schema.get("ref", "")):
                return  # Arbitrary JSON intentionally includes both booleans and numbers.
            if schema.get("type") in {"bool", "int", "float"}:
                checked.append(label)
                if schema.get("strict") is not True:
                    failures.append(label)
                if schema.get("type") == "float" and schema.get("allow_inf_nan") is not False:
                    failures.append(label + " permits nonfinite floats")
            for value in schema.values():
                visit(value, label)
        elif isinstance(schema, (list, tuple)):
            for value in schema:
                visit(value, label)

    tools = [
        t for t in get_registered_tools() if t["func"].__module__.startswith("services.tools.")
    ]
    assert len(tools) >= 51
    for tool in tools:
        hints = get_type_hints(tool["func"], include_extras=True)
        for name in inspect.signature(tool["func"]).parameters:
            if name != "ctx" and name in hints:
                visit(TypeAdapter(hints[name]).core_schema, tool["name"] + "." + name)
    assert len(checked) >= 150
    assert not failures, failures
