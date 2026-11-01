"""Synthetic catalog construction preserves real registration and SDK execution."""

import inspect
import json
from functools import wraps

from fastmcp import FastMCP
from fastmcp.exceptions import ValidationError
from fastmcp.tools.base import Tool
import pytest

from services import tools
from services.registry import tool_registry
from tests.http.client_assurance_server import catalog_tool_factory, register_catalog


@pytest.fixture
def catalog(monkeypatch):
    # Own only this test's registry; importing the helper never registers tools.
    monkeypatch.setattr(tool_registry, "_tool_registry", [])
    monkeypatch.setattr(tools, "discover_modules", lambda *_: iter(()))
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    return register_catalog


@pytest.mark.asyncio
async def test_catalog_clones_match_all_actual_sdk_descriptors_and_keep_wrappers(
    catalog, monkeypatch
):
    # Given: all 512 real declarations and the unoptimized SDK/production registration.
    declarations = catalog(512)
    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(Tool, "from_function", parse)
    baseline = FastMCP("catalog-baseline")
    tools.register_all_tools(baseline)
    assert len(parsed) == 512
    expected = {tool.name: tool for tool in await baseline.local_provider.list_tools()}
    parsed.clear()
    factory = catalog_tool_factory(declarations)
    monkeypatch.setattr(Tool, "from_function", factory)

    # When: the same production registration constructs each independently wrapped entry.
    optimized = FastMCP("catalog-optimized")
    tools.register_all_tools(optimized)
    actual = {tool.name: tool for tool in await optimized.local_provider.list_tools()}

    # Then: the full wire catalog is identical; only the repeated parse is avoided.
    assert len(parsed) == 1
    assert len(actual) == len(expected) == 512
    for name, tool in actual.items():
        assert tool.to_mcp_tool().model_dump_json(by_alias=True, exclude_none=True) == (
            expected[name].to_mcp_tool().model_dump_json(by_alias=True, exclude_none=True)
        )
        assert tool.title is None
        assert tool.fn is not expected[name].fn
        assert inspect.unwrap(tool.fn) is inspect.unwrap(expected[name].fn)
    for attribute in ("key", "name"):
        assert len({getattr(tool, attribute) for tool in actual.values()}) == 512
    for attribute in ("fn", "parameters", "output_schema", "tags", "meta"):
        values = [getattr(tool, attribute) for tool in actual.values()]
        assert len({id(value) for value in values if value is not None}) == (
            sum(value is not None for value in values)
        )

    first, second, last = (
        actual[name]
        for name in ("assurance_catalog_0000", "assurance_catalog_0001", "assurance_catalog_0511")
    )
    for tool in (first, second, last):
        assert json.loads((await tool.run({})).content[0].text) == 7
        assert json.loads((await tool.run({"value": 23})).content[0].text) == 23
        for invalid in ([], "23", True):
            with pytest.raises(ValidationError):
                await tool.run({"value": invalid})

    first.parameters["properties"]["value"]["default"] = 99
    first.output_schema["clone-only"] = True
    first.tags.add("clone-only")
    first.meta = {"clone-only": True}
    assert second.parameters["properties"]["value"]["default"] == 7
    assert "clone-only" not in second.output_schema
    assert "clone-only" not in second.tags
    assert second.meta is None


@pytest.mark.parametrize(
    "change",
    [
        "options",
        "defaults",
        "float_default",
        "wrapped_default",
        "annotations",
        "code",
        "unknown",
        "callable",
    ],
)
def test_catalog_factory_reparses_unrecognized_declarations(catalog, monkeypatch, change):
    # Given: a pristine template and a second entry whose contract may change.
    declarations = catalog(2)
    first, second = declarations
    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(Tool, "from_function", parse)
    factory = catalog_tool_factory(declarations)
    options = {"description": None, "run_in_thread": True, "tags": {"group:vfx"}}
    factory(first, name=declarations[first], **options)

    async def replacement(value: str = "different") -> str:
        return value

    class UnhashableCallable:
        __hash__ = None

        async def __call__(self, value: int = 11) -> int:
            return value

    match change:
        case "options":
            options["description"] = "Changed metadata"
        case "defaults":
            second.__defaults__ = (19,)
        case "float_default":
            second.__defaults__ = (7.0,)
        case "wrapped_default":
            declaration = second

            @wraps(declaration)
            async def wrapped(value: int = 7) -> int:
                return await declaration(value)

            signature = inspect.signature(second)
            wrapped.__signature__ = signature.replace(
                parameters=[signature.parameters["value"].replace(default=7.0)]
            )
            second = wrapped
        case "annotations":
            second.__annotations__ = {"value": str, "return": str}
            second.__defaults__ = ("different",)
        case "code":
            second.__code__ = replacement.__code__
        case "unknown":
            second = replacement
        case "callable":
            second = UnhashableCallable()
    # When: an unsupported declaration/options pair reaches the construction seam.
    actual = factory(second, name="assurance_catalog_0001", **options)
    expected = original(second, name="assurance_catalog_0001", **options)
    # Then: its metadata/schema comes from the real parser, never the old template.
    assert actual.to_mcp_tool().model_dump_json() == expected.to_mcp_tool().model_dump_json()
    assert parsed == [first, second]


def test_catalog_import_has_no_registration_side_effects():
    from importlib import reload
    from tests.http import client_assurance_server

    before = tool_registry.get_registered_tools()
    reload(client_assurance_server)
    assert tool_registry.get_registered_tools() == before
