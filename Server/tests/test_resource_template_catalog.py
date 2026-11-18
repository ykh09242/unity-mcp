"""Resource discovery projects parameterized routes without reading Unity state."""

import json

import pytest
from click.testing import CliRunner
from fastmcp import Client, Context, FastMCP
from fastmcp.resources.template import expand_uri_template

CHILDREN_TEMPLATE = "mcpforunity://scene/gameobject/{instance_id}/children{?page_size,cursor}"
FIXED_URI = "mcpforunity://fixture/fixed"
TEMPLATE = "mcpforunity://fixture/{instance_id}{?page_size,cursor}"
HIDDEN_TEMPLATE = "mcpforunity://fixture/hidden/{instance_id}"
DISABLED_TEMPLATE = "mcpforunity://fixture/disabled/{instance_id}"
QUERY_TEMPLATE = "mcpforunity://fixture/page{?cursor}{&labels*}"


@pytest.fixture
def template_server(monkeypatch):
    from core.config import config
    from services.tools import register_all_tools

    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("resource-template-discovery")
    register_all_tools(server, project_scoped_tools=False)

    @server.resource(FIXED_URI)
    def fixed() -> str:
        return "457"

    @server.resource(TEMPLATE, version="1.0")
    def old_children(instance_id: int, page_size: int = 10, cursor: str | None = None) -> str:
        return f"old:{instance_id}:{page_size}:{cursor}"

    @server.resource(TEMPLATE, version="2.0")
    def children(instance_id: int, page_size: int = 10, cursor: str | None = None) -> str:
        return f"{instance_id}:{page_size}:{cursor}"

    @server.resource(QUERY_TEMPLATE)
    def page(cursor: str | None = None, labels: list[str] | None = None) -> str:
        return f"{cursor}:{labels}"

    @server.resource(HIDDEN_TEMPLATE, auth=lambda _: False)
    def hidden(instance_id: int) -> str:
        return str(instance_id)

    @server.resource(DISABLED_TEMPLATE, tags={"disabled-template"})
    def disabled(instance_id: int) -> str:
        return str(instance_id)

    server.disable(tags={"disabled-template"})
    return server


async def _tool(client, **arguments):
    return (await client.call_tool("manage_tools", arguments)).structured_content


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_template_pages_match_visible_inventory_without_shifting_fixed_resources(
    template_server, mode
):
    async with Client(template_server, mode=mode) as client:
        inventory = {item.uri_template for item in await client.list_resource_templates()}
        assert inventory == {TEMPLATE, QUERY_TEMPLATE}
        index = await _tool(client, action="catalog")
        fixed = await _tool(client, action="catalog", group="resources", limit=1)

        selector = fixed["resource_templates"]["browse"]
        first = await _tool(client, **selector, limit=1)
        second = await _tool(client, **selector, limit=1, offset=first["next_offset"])

    assert index["resource_templates"]["browse"] == selector
    assert [row["uri"] for row in fixed["resources"]] == [FIXED_URI]
    assert fixed["total"] == 1
    assert fixed["next_offset"] is None
    assert first["next_offset"] == 1 and second["next_offset"] is None
    assert first["total"] == second["total"] == len(inventory)
    rows = first["resource_templates"] + second["resource_templates"]
    assert [row["uri_template"] for row in rows] == sorted(inventory)
    assert all(row["kind"] == "resource_template" and "uri" not in row for row in rows)
    assert all(row["advertised"] is True and row["readiness"] == "unobserved" for row in rows)
    path_target, query_target = rows[1], rows[0]
    assert path_target["name"] == "children"
    assert path_target["required_parameters"] == ["instance_id"]
    assert path_target["optional_parameters"] == ["page_size", "cursor"]
    assert query_target["required_parameters"] == []
    assert query_target["optional_parameters"] == ["cursor", "labels"]


@pytest.mark.asyncio
async def test_template_catalog_respects_request_session_visibility(template_server):
    @template_server.tool
    async def hide_page(ctx: Context) -> None:
        await ctx.disable_components(components={"template"}, names={QUERY_TEMPLATE})

    async with Client(template_server, mode="legacy") as selected:
        await selected.call_tool("hide_page")
        filtered = await _tool(selected, action="catalog", group="resource_templates")
    async with Client(template_server, mode="legacy") as independent:
        unfiltered = await _tool(independent, action="catalog", group="resource_templates")

    assert {row["uri_template"] for row in filtered["resource_templates"]} == {TEMPLATE}
    assert {row["uri_template"] for row in unfiltered["resource_templates"]} == {
        QUERY_TEMPLATE,
        TEMPLATE,
    }


@pytest.mark.asyncio
async def test_search_template_can_be_expanded_with_an_observed_identifier(template_server):
    async with Client(template_server) as client:
        observed_id = int((await client.read_resource(FIXED_URI))[0].text)
        result = await _tool(client, action="search", query=TEMPLATE)
        route = result["routes"][0]
        uri = expand_uri_template(
            route["uri_template"], {"instance_id": observed_id, "page_size": 4}
        )
        content = await client.read_resource(uri)

    assert route["kind"] == "resource_template"
    assert route["uri_template"] == TEMPLATE
    assert "uri" not in route
    assert result["navigation"]["resource_templates"]["total"] == 2
    assert result["navigation"]["resource_templates"]["browse"]["group"] == "resource_templates"
    assert content[0].text == "457:4:None"


def test_cli_template_catalog_and_search_are_offline():
    from cli.main import cli

    runner = CliRunner()
    options = ["--no-project-context", "--format", "json", "tool"]
    page = runner.invoke(cli, [*options, "catalog", "--group", "resource_templates"])
    assert page.exit_code == 0, page.output
    catalog = json.loads(page.stdout)
    row = next(
        row for row in catalog["resource_templates"] if row["uri_template"] == CHILDREN_TEMPLATE
    )
    assert row["required_parameters"] == ["instance_id"]
    assert row["optional_parameters"] == ["page_size", "cursor"]
    assert row["advertised"] is None

    result = runner.invoke(cli, ["--no-project-context", "tool", "search", CHILDREN_TEMPLATE])
    assert result.exit_code == 0, result.output
    assert f"resource_template {CHILDREN_TEMPLATE}" in result.stdout


def test_optional_segment_and_continued_query_preserve_schema_required_variables():
    from services.registry.action_catalog import resource_templates

    uri = "mcpforunity://objects/{id}{/child}{?fields*}{&cursor}"
    (row,) = resource_templates(
        [{"uri": uri, "name": "objects", "description": "", "schema_required": ("id",)}]
    )
    assert row["uri_template"] == uri
    assert row["required_parameters"] == ["id"]
    assert row["optional_parameters"] == ["child", "fields", "cursor"]
