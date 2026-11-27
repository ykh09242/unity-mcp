"""Integer selectors retain port semantics at the public FastMCP boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from core.config import config
from transport.plugin_hub import PluginHub
from transport.legacy.unity_connection import UnityConnectionPool
from transport.unity_instance_middleware import UnityInstanceMiddleware, resolve_discovered_instance


@pytest.mark.parametrize(
    "selector,expected",
    [
        ("6401", "A"),
        ("06401", "A"),
        ("6401f", "B"),
        ("6500", "E"),
        ("00006401", "Legacy"),
        ("00006402", "B"),
        ("0000000000006401", "Full"),
        ("Full@0000000000006401", "Full"),
        ("0000000000006402", None),
        ("2048", "Second"),
        ("abcdef", "Hex"),
        ("ABCDEF", "Hex"),
        ("204810ddf5bd2940", "Second"),
        ("Ordinary", "Ordinary"),
        ("Game@9999aaaabbbbcccc", None),
        ("Game@deadbeef", None),
        ("Game@deadbeef11223344", "Game"),
        ("Game@9999aaaabbbbcccc@1111222233334444", "Game@9999aaaabbbbcccc"),
        ("204", None),
        ("Twin", None),
        ("missing", None),
    ],
)
def test_stdio_selector_collisions_resolve_identically_for_explicit_and_default(
    monkeypatch, selector, expected
):
    instances = [
        SimpleNamespace(id=f"{name}@{value}", hash=value, name=name, port=port, path=f"/{name}")
        for name, value, port in [
            ("A", "aabbccddeeff0001", 6401),
            ("B", "6401ffff11223344", 6402),
            ("Legacy", "00006401", 6403),
            ("Long", "0000640112345678", 6406),
            ("Full", "0000000000006401", 6404),
            ("E", "6500ffff11223344", 6405),
            ("2048", "aaaaaaaa55555555", 6410),
            ("Second", "204810ddf5bd2940", 6420),
            ("abcdef", "0011223344556677", 6421),
            ("Hex", "abcdef0011223344", 6422),
            ("204810ddf5bd2940", "bbccaabbccddeeff", 6423),
            ("00006401", "aa55bb66cc77dd88", 6424),
            ("0000000000006401", "bbbbcccceeeeffff", 6429),
            ("Ordinary", "aaaa0000bbbb1111", 6425),
            ("204", "2049112222333344", 6426),
            ("Twin", "ffff0000bbbb3333", 6427),
            ("Twin", "ffff1111bbbb3333", 6428),
            ("Game", "deadbeef11223344", 6430),
            ("Game@9999aaaabbbbcccc", "1111222233334444", 6431),
        ]
    ]
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", selector)
    pool = UnityConnectionPool()
    monkeypatch.setattr(pool, "_discover_instance_candidates", lambda *_: instances)
    if expected is None:
        with pytest.raises(ValueError):
            resolve_discovered_instance(selector, instances, "stdio")
        with pytest.raises(ConnectionError):
            pool.resolve_instance()
        with pytest.raises(ConnectionError):
            pool.resolve_instance(selector)
        return
    selected = next(row.id for row in instances if row.name == expected)
    assert resolve_discovered_instance(selector, instances, "stdio") == selected
    assert pool.resolve_instance().id == selected
    assert pool.resolve_instance(selector).id == selected


@pytest.mark.parametrize("selector", ["A@6401", "A@aabb"])
def test_legacy_pool_keeps_named_shorthand_while_public_composites_require_a_full_id(
    monkeypatch, selector
):
    target = SimpleNamespace(
        id="A@aabbccddeeff0001", hash="aabbccddeeff0001", name="A", port=6401, path="/A"
    )
    neighbor = SimpleNamespace(
        id="A@bb01ffff11223344", hash="bb01ffff11223344", name="A", port=6402, path="/other/A"
    )
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", selector)
    pool = UnityConnectionPool()
    monkeypatch.setattr(pool, "_discover_instance_candidates", lambda *_: [neighbor, target])
    assert pool.resolve_instance().id == target.id
    with pytest.raises(ValueError, match="not found"):
        resolve_discovered_instance(selector, [neighbor, target], "stdio")


@pytest.mark.parametrize(
    "selector,hashes",
    [
        ("Game@Stage", ["aabbccdd11223344"]),
        ("Game@cafe", ["aabbccdd11223344"]),
        ("Game@cafe", ["aabbccdd11223344", "9988776655443322"]),
    ],
)
def test_pool_treats_a_noncanonical_literal_project_name_before_named_shorthand(
    monkeypatch, selector, hashes
):
    literal = [
        SimpleNamespace(
            id=f"{selector}@{value}",
            name=selector,
            hash=value,
            port=6410 + index,
            path=f"/{index}/{selector}",
        )
        for index, value in enumerate(hashes)
    ]
    neighbor = SimpleNamespace(
        id="Game@cafebabe00112233", name="Game", hash="cafebabe00112233", port=6420, path="/Game"
    )
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", selector)
    pool = UnityConnectionPool()
    monkeypatch.setattr(pool, "_discover_instance_candidates", lambda *_: [neighbor, *literal])
    if len(literal) == 1:
        assert pool.resolve_instance().id == literal[0].id
    else:
        with pytest.raises(ConnectionError, match="ambiguous"):
            pool.resolve_instance()


def test_legacy_named_hint_that_matches_a_port_and_another_hash_is_ambiguous(monkeypatch):
    by_port = SimpleNamespace(
        id="Game@cafebabe00112233",
        name="Game",
        hash="cafebabe00112233",
        port=6401,
        path="/one/Game",
    )
    by_hash = SimpleNamespace(
        id="Game@6401aabbccdd1122",
        name="Game",
        hash="6401aabbccdd1122",
        port=6402,
        path="/two/Game",
    )
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", "Game@6401")
    pool = UnityConnectionPool()
    monkeypatch.setattr(pool, "_discover_instance_candidates", lambda *_: [by_port, by_hash])
    with pytest.raises(ConnectionError, match="ambiguous"):
        pool.resolve_instance()


@pytest.mark.asyncio
async def test_two_stdio_clients_dispatch_to_same_project_for_numeric_name_collision(monkeypatch):
    from transport.legacy import unity_connection

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "require_explicit_target", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", "2048")
    named = SimpleNamespace(
        id="2048@aaaaaaaa55555555", name="2048", hash="aaaaaaaa55555555", port=6410, path="/2048"
    )
    hashed = SimpleNamespace(
        id="Second@204810ddf5bd2940",
        name="Second",
        hash="204810ddf5bd2940",
        port=6420,
        path="/Second",
    )
    pool = UnityConnectionPool()
    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(pool, "discover_all_instances", lambda **_: [named, hashed])
    server = FastMCP("stdio-routing")
    server.add_middleware(UnityInstanceMiddleware())
    dispatched = []

    @server.tool
    async def mutation_probe(ctx: Context) -> str:
        selected = await ctx.get_state("unity_instance")
        dispatched.append(selected)
        return selected

    async with Client(server) as explicit, Client(server) as default:
        assert (
            await explicit.call_tool("mutation_probe", {"unity_instance": "2048"})
        ).data == hashed.id
        assert (await default.call_tool("mutation_probe", {})).data == hashed.id
    assert dispatched == [hashed.id, hashed.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector,name,project_hash",
    [
        ("Game@deadbeef", "Game", "deadbeef11223344"),
        ("Game@9999aaaabbbbcccc", "Game@9999aaaabbbbcccc", "1111222233334444"),
    ],
)
async def test_missing_qualified_target_never_dispatches_from_either_sdk_client(
    monkeypatch, selector, name, project_hash
):
    from transport.legacy import unity_connection

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "require_explicit_target", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", selector)
    other = SimpleNamespace(
        id=f"{name}@{project_hash}", name=name, hash=project_hash, port=6430, path=f"/{name}"
    )
    pool = UnityConnectionPool()
    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(pool, "_discover_instance_candidates", lambda *_args, **_kwargs: [other])
    server = FastMCP("missing-qualified-target")
    server.add_middleware(UnityInstanceMiddleware())
    dispatched = []

    @server.tool
    async def mutation_probe(ctx: Context) -> str:
        target = pool.resolve_instance(await ctx.get_state("unity_instance"))
        dispatched.append(target.id)
        return target.id

    async with Client(server) as explicit, Client(server) as default:
        with pytest.raises(ToolError, match="explicit_unity_target_unavailable") as missing:
            await explicit.call_tool("mutation_probe", {"unity_instance": selector})
        guidance = str(missing.value)
        assert "mcpforunity://instances" in guidance
        assert selector not in guidance and other.id not in guidance
        result = await default.call_tool("mutation_probe", {}, raise_on_error=False)
        assert result.is_error
    assert not dispatched


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("selector", [6400, "6400", 6401])
async def test_public_selector_preserves_integer_port_semantics(monkeypatch, transport, selector):
    monkeypatch.setattr(config, "transport_mode", transport)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    middleware = UnityInstanceMiddleware()
    target = SimpleNamespace(id="B@6400abcd12345678", hash="6400abcd12345678", port=6401)
    discovery = AsyncMock(return_value=[target])
    monkeypatch.setattr(middleware, "_discover_instances", discovery)
    server = FastMCP("integer-routing")
    server.add_middleware(middleware)
    dispatched = []

    @server.tool
    async def mutation_probe(ctx: Context) -> str:
        selected = await ctx.get_state("unity_instance")
        dispatched.append(selected)
        return selected

    allowed = selector == "6400" or (transport == "stdio" and selector == 6401)
    async with Client(server) as client:
        if allowed:
            result = await client.call_tool("mutation_probe", {"unity_instance": selector})
            assert result.data == target.id
        else:
            with pytest.raises(MCPError):
                await client.call_tool("mutation_probe", {"unity_instance": selector})
    assert dispatched == ([target.id] if allowed else [])
    if transport == "http" and isinstance(selector, int):
        discovery.assert_not_awaited()
