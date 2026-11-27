"""Follow only concrete skill resources advertised to this MCP connection."""

from functools import partial
from unittest.mock import AsyncMock

from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.middleware import Middleware
import pytest

from services.resource_inventory import register_catalog_observation

from .test_action_catalog_entrypoints import server as server
from .test_http_command_outcome import command_hub  # pylint: disable=unused-import


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_skill_directory_returns_readable_advertised_uris(server, mode, monkeypatch):
    visible = ["skill://first/SKILL.md", "skill://second/references/Guide.md"]
    hidden = "skill://private/SKILL.md"
    denied = "skill://restricted/Guide.md"
    local = "file:///host/SKILL.md"
    reads = []

    def document(uri):
        def read() -> str:
            reads.append(uri)
            return "contents of " + uri

        return read

    for uri in [*visible, hidden, denied, local]:
        register_catalog_observation(server, uri, document(uri))

    @partial(register_catalog_observation, server, "skill://first/templates/{page}")
    def template(page: str) -> str:
        return page

    class Policy(Middleware):
        async def on_list_resources(self, context, call_next):
            return [item for item in await call_next(context) if str(item.uri) != hidden]

    server.add_middleware(Policy())
    check = server._check_resource_access

    async def read_access(uri, *args, **kwargs):
        if str(uri) == denied:
            raise AuthorizationError("Read unavailable")
        return await check(uri, *args, **kwargs)

    monkeypatch.setattr(server, "_check_resource_access", read_access)
    async with Client(server, mode=mode) as client:
        index = (await client.call_tool("manage_tools", {"action": "catalog"})).structured_content
        assert index.get("skills", {}).get("total") == len(visible)
        browse = index["skills"]["browse"]
        monkeypatch.setattr(
            server, "list_resource_templates", AsyncMock(side_effect=AssertionError)
        )
        selected = []
        offset = 0
        while offset is not None:
            page = (
                await client.call_tool("manage_tools", {**browse, "offset": offset, "limit": 1})
            ).structured_content
            assert page["total"] == len(visible)
            selected.extend(page["skills"])
            offset = page["next_offset"]
        assert reads == []
        assert [row["uri"] for row in selected] == visible
        for row in selected:
            assert row["advertised"] is True
            call = row["read_call"]
            assert call == {"method": "resources/read", "params": {"uri": row["uri"]}}
            assert (await client.read_resource(**call["params"]))[0].text == "contents of " + row[
                "uri"
            ]
            binding = row["read_tool"]
            result = await client.call_tool(binding["tool"], binding["arguments"])
            assert result.structured_content["contents"][0]["text"] == "contents of " + row["uri"]
        assert reads == [uri for uri in visible for _ in range(2)]
        for uri in (hidden, denied, local, "skill://first/templates/unlisted"):
            result = await client.call_tool(
                "manage_tools", {"action": "read_skill", "skill_uri": uri}, raise_on_error=False
            )
            assert result.is_error or result.structured_content["success"] is False
        assert reads == [uri for uri in visible for _ in range(2)]


def test_local_catalog_does_not_invent_published_skill_resources():
    from services.registry.action_catalog import CatalogRequest, browse_catalog

    assert "skills" not in browse_catalog(CatalogRequest(), [])
    offline = browse_catalog(CatalogRequest(group="skills"), [])
    assert offline["scope"] == "local_catalog"
    assert offline["skills"] == [] and offline["total"] == 0
    with pytest.raises(ValueError, match="tool group"):
        CatalogRequest(group="skills", intent="observe")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_selected_editor_is_retained_through_skill_discovery_and_read(command_hub, mode):
    from services.tools.manage_tools import manage_tools
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("scoped skill reads")
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(name="manage_tools")(manage_tools)
    await command_hub.registry.register("second", "Second", "other", "6000.3")

    @partial(register_catalog_observation, app, "skill://fixture/SKILL.md")
    async def selected(ctx: Context) -> str:
        return await ctx.get_state("unity_instance")

    async with Client(app, mode=mode, cache=False) as client:
        target = {"unity_instance": "Second@other"}
        index = (
            await client.call_tool("manage_tools", {"action": "catalog", **target})
        ).structured_content
        browse = index["skills"]["browse"]
        assert browse == {"action": "catalog", "group": "skills", **target}
        files = (await client.call_tool("manage_tools", browse)).structured_content
        params = files["skills"][0]["read_call"]["params"]
        assert params == {"uri": "skill://fixture/SKILL.md", "_meta": target}
        saved = await client.read_resource(params["uri"], meta=params["_meta"])
        assert saved[0].text == "Second@other"
        call = files["skills"][0]["read_tool"]
        assert call["arguments"]["unity_instance"] == target["unity_instance"]
        content = await client.call_tool(call["tool"], call["arguments"])
        assert content.structured_content["contents"][0]["text"] == "Second@other"
    assert command_hub.sent == []
