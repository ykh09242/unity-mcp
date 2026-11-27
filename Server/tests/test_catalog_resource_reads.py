"""Connection tools read only resolved resources published to the same caller."""

import base64
from enum import Enum
from functools import partial
import json

from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import AuthorizationError, ToolError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.middleware import Middleware
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import MCPError
import pytest

from services.resource_inventory import register_catalog_observation

from .test_action_catalog_entrypoints import server as server
from .test_http_command_outcome import command_hub  # pylint: disable=unused-import


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_catalog_and_search_bind_reads_on_the_selected_editor(command_hub, mode):
    from services.tools.manage_tools import manage_tools, search_unity_tools
    from services.tools.read_unity_resource import read_unity_resource
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("catalog resource reads")
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(name="manage_tools")(manage_tools)
    app.tool(name="search_unity_tools")(search_unity_tools)
    app.tool(read_unity_resource)
    await command_hub.registry.register("second", "Second", "other", "6000.3")
    target = {"unity_instance": "Second@other"}
    uri = "mcpforunity://fixture/info"
    template_uri = "mcpforunity://fixture/{part}"
    reads = []

    def content(value):
        return ResourceResult(
            [
                ResourceContent(value, mime_type="text/markdown", meta={"language": "ko"}),
                ResourceContent(b"\x00\xff", mime_type="application/octet-stream"),
            ],
            meta={"revision": 2},
            ttl_ms=0,
            cache_scope="private",
        )

    @partial(register_catalog_observation, app, uri)
    async def concrete(ctx: Context) -> ResourceResult:
        reads.append("concrete")
        return content(await ctx.get_state("unity_instance"))

    @partial(register_catalog_observation, app, template_uri)
    async def item(part: str, ctx: Context) -> ResourceResult:
        reads.append(part)
        return content(part + ":" + await ctx.get_state("unity_instance"))

    async with Client(app, mode=mode, cache=False) as client:

        async def call(tool, **arguments):
            return (await client.call_tool(tool, {**arguments, **target})).structured_content

        index = await call("manage_tools", action="catalog")
        browse = index["resources"]["browse"]
        assert browse == {"action": "catalog", "group": "resources", **target}
        page = (await client.call_tool("manage_tools", browse)).structured_content
        assert (
            page["resource_templates"]["browse"]
            == index["resource_templates"]["browse"]
            == {"action": "catalog", "group": "resource_templates", **target}
        )
        [row] = page["resources"]
        native = {"method": "resources/read", "params": {"uri": uri, "_meta": target}}
        tool = {
            "tool": "manage_tools",
            "arguments": {"action": "read_resource", "uri": uri, **target},
        }
        assert row["read_call"] == native and row["read_tool"] == tool
        direct = {"tool": "read_unity_resource", "arguments": {"uri": uri, **target}}
        assert row["preferred_read_tool"] == direct
        for scope, available, preferred in [
            ({}, True, True),
            ({"host_tool_names": ["manage_tools"]}, True, False),
            ({"host_tool_names": ["read_unity_resource"]}, False, True),
            ({"host_tool_names": []}, False, False),
            (
                {
                    "host_tool_evidence": {
                        "candidates": ["manage_tools"],
                        "present": [],
                        "complete": False,
                    }
                },
                False,
                False,
            ),
        ]:
            page = await call("search_unity_tools", selection="agent", family=uri, **scope)
            [binding] = page["operations"]
            assert binding["read_call"] == native
            assert binding.get("read_tool") == (tool if available else None)
            assert binding.get("preferred_read_tool") == (direct if preferred else None)
        ranked = await call(
            "search_unity_tools",
            query=uri,
            route_surfaces="resources",
            host_tool_names=["read_unity_resource"],
        )
        for binding in (ranked["routes"][0], ranked["route_groups"][0]["representative"]):
            assert binding["preferred_read_tool"] == direct
            assert "read_tool" not in binding
        resources = await call("manage_tools", action="catalog", group="resource_templates")
        [template] = resources["resource_templates"]
        page = await call("search_unity_tools", selection="agent", family=template_uri)
        [operation] = page["operations"]
        for descriptor in (operation, template):
            assert "read_tool" not in descriptor and "read_call" not in descriptor
            adapter = descriptor["read_after_expansion"]
            assert adapter["arguments"] == {"action": "read_resource", **target}
            assert adapter["set_argument"] == "uri"
            assert descriptor["preferred_read_after_expansion"] == {
                "tool": "read_unity_resource",
                "arguments": target,
                "set_argument": "uri",
            }
        assert reads == []
        for resolved, value in (
            (uri, "Second@other"),
            (uri.replace("info", "other"), "other:Second@other"),
        ):
            result = await call("manage_tools", action="read_resource", uri=resolved)
            expected = (
                content(value)
                .to_mcp_result(resolved)
                .model_dump(mode="json", by_alias=True, exclude_none=True)
            )
            assert result == expected
            assert await call("read_unity_resource", uri=resolved) == expected
            assert (await client.read_resource(resolved, meta=target))[0].text == value
        assert reads == ["concrete"] * 3 + ["other"] * 3
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_only_the_resolved_descriptor_authorizes_an_expanded_uri(server, mode):
    reads = []
    prefix = "mcpforunity://test/"

    @partial(register_catalog_observation, server, prefix + "filtered")
    def filtered() -> str:
        reads.append("filtered")
        return "filtered"

    @partial(register_catalog_observation, server, prefix + "disabled")
    def disabled() -> str:
        reads.append("disabled")
        return "disabled"

    @partial(register_catalog_observation, server, prefix + "{part}")
    def first(part: str) -> str:
        reads.append(part)
        return part

    @partial(register_catalog_observation, server, prefix + "{alternative}")
    def second(alternative: str) -> str:
        reads.append(alternative)
        return alternative

    @partial(register_catalog_observation, server, "skill://guide/SKILL.md")
    def guide() -> str:
        reads.append("skill")
        return "skill"

    @partial(register_catalog_observation, server, "file:///private.txt")
    def local_file() -> str:
        reads.append("file")
        return "file"

    server.disable(keys={(await server.get_resource(prefix + "disabled")).key})
    winner = (await server.get_resource_template(prefix + "choice")).key
    hidden_templates = set()

    class Policy(Middleware):
        async def on_list_resources(self, context, call_next):
            return [r for r in await call_next(context) if str(r.uri) != prefix + "filtered"]

        async def on_list_resource_templates(self, context, call_next):
            return [t for t in await call_next(context) if t.key not in hidden_templates]

        async def check_resource_access(self, context):
            if str(context.message.uri) == prefix + "denied":
                raise AuthorizationError("Resource unavailable")

    server.add_middleware(Policy())
    async with Client(server, mode=mode) as client:

        async def read(uri):
            return await client.call_tool(
                "manage_tools", {"action": "read_resource", "uri": uri}, raise_on_error=False
            )

        assert (await read("skill://guide/SKILL.md")).structured_content["contents"][0][
            "text"
        ] == "skill"
        assert (await read(prefix + "choice")).structured_content["contents"][0]["text"] == "choice"
        for uri in (
            prefix + "filtered",
            prefix + "disabled",
            prefix + "denied",
            prefix + "{part}",
            "mcpforunity://unknown/path",
            "file:///private.txt",
            "https://example.invalid/private",
        ):
            assert (await read(uri)).is_error, uri
        hidden_templates.add(winner)
        assert (await read(prefix + "choice")).is_error
    assert reads == ["skill", "choice"]


@pytest.mark.asyncio
async def test_projected_descriptors_keep_the_selected_callback_and_version(server):
    uri = "mcpforunity://fixture/description"
    template = "mcpforunity://choices/{choice}"

    class Choice(str, Enum):
        ONE = "one"

    @partial(register_catalog_observation, server, uri)
    def detail() -> str:
        return "content"

    @partial(register_catalog_observation, server, template, version="1")
    def older(choice: str) -> str:
        return "older"

    @partial(register_catalog_observation, server, template, version="2")
    def current(choice: Choice) -> str:
        return choice

    hide_selected = False

    class Projection(Middleware):
        async def on_list_resources(self, context, call_next):
            return [
                r.model_copy(update={"description": "Client-facing description"})
                for r in await call_next(context)
            ]

        async def on_list_resource_templates(self, context, call_next):
            return [t for t in await call_next(context) if not hide_selected or t.version != "2"]

    server.add_middleware(Projection())
    resolved = "mcpforunity://choices/one"
    raw = await server.get_resource_template(resolved)
    assert "$defs" in raw.parameters and raw.version == "2"
    shown = next(t for t in await server.list_resource_templates() if t.key == raw.key)
    assert "$defs" not in shown.parameters
    async with Client(server, cache=False) as client:
        page = (
            await client.call_tool("manage_tools", {"action": "catalog", "group": "resources"})
        ).structured_content
        [row] = page["resources"]
        assert row["uri"] == uri and row["description"] == "Client-facing description"
        call = row["read_tool"]
        assert (await client.call_tool(call["tool"], call["arguments"])).structured_content[
            "contents"
        ][0]["text"] == "content"
        args = {"action": "read_resource", "uri": resolved}
        assert (await client.call_tool("manage_tools", args)).structured_content["contents"][0][
            "text"
        ] == "one"
        hide_selected = True
        assert (await client.call_tool("manage_tools", args, raise_on_error=False)).is_error


@pytest.mark.asyncio
async def test_static_resources_from_different_providers_keep_distinct_read_owners(server):
    from fastmcp.resources import TextResource
    from fastmcp.server.providers import LocalProvider

    uri = "mcpforunity://static/info"
    for name in ("first", "second"):
        provider = LocalProvider()
        provider.add_resource(TextResource(uri=uri, version="1", name=name, text=name))
        server.add_provider(provider)
    selected = await server.get_resource(uri)
    assert {r.name for r in await server.list_resources()} == {"first", "second"}
    hide_selected = False

    class Filter(Middleware):
        async def on_list_resources(self, context, call_next):
            return [
                r.model_copy(update={"description": "Projected"})
                for r in await call_next(context)
                if not hide_selected or r.name != selected.name
            ]

    server.add_middleware(Filter())
    async with Client(server, cache=False) as client:
        catalog = {"action": "catalog", "group": "resources"}
        visible = (await client.call_tool("manage_tools", catalog)).structured_content
        [row] = visible["resources"]
        assert row["name"] == selected.name
        call = row["read_tool"]
        allowed = await client.call_tool(call["tool"], call["arguments"])
        assert allowed.structured_content["contents"][0]["text"] == selected.name
        hide_selected = True
        assert len(await client.list_resources()) == 1
        filtered = (await client.call_tool("manage_tools", catalog)).structured_content
        assert not filtered["resources"]
        refused = await client.call_tool(call["tool"], call["arguments"], raise_on_error=False)
        assert refused.is_error


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_projected_packaged_skill_reads_match_the_native_resource(server, mode):
    from transport.skills_extension import SKILL_URI, install_skills

    assert install_skills(server) is not None

    class Projection(Middleware):
        async def on_list_resources(self, context, call_next):
            return [
                r.model_copy(update={"description": "Projected guide"})
                for r in await call_next(context)
            ]

    server.add_middleware(Projection())
    async with Client(server, mode=mode, cache=False) as client:
        native = await client.read_resource(SKILL_URI)
        expected = [
            item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in native
        ]
        assert expected[0]["text"]
        page = await client.call_tool(
            "manage_tools", {"action": "catalog", "group": "skills", "limit": 20}
        )
        [guide] = [row for row in page.structured_content["skills"] if row["uri"] == SKILL_URI]
        assert guide["description"] == "Projected guide"
        for arguments in (
            guide["read_tool"]["arguments"],
            {"action": "read_resource", "uri": SKILL_URI},
        ):
            result = await client.call_tool("manage_tools", arguments)
            assert result.structured_content["contents"] == expected
        for arguments in ({}, guide["preferred_read_tool"]["arguments"]):
            result = await client.call_tool("read_unity_resource", arguments)
            assert result.structured_content["contents"] == expected
        for uri in (
            "file:///host/.codex/skills/unity-mcp-skill/SKILL.md",
            "https://example.invalid/guide",
        ):
            assert (
                await client.call_tool("read_unity_resource", {"uri": uri}, raise_on_error=False)
            ).is_error


def test_static_projection_requires_the_same_source_and_read_policy():
    from fastmcp.resources import TextResource
    from services.resource_inventory import same_resource_binding

    resource = TextResource(uri="mcpforunity://static/policy", text="public")
    assert same_resource_binding(resource, resource.model_copy(update={"description": "Preview"}))
    assert not same_resource_binding(
        resource, resource.model_copy(update={"auth": lambda _: False})
    )
    assert not same_resource_binding(resource, resource.model_copy(update={"text": "different"}))

    class Custom(TextResource):
        pass

    custom = Custom(uri=resource.uri, text="public")
    assert not same_resource_binding(custom, custom.model_copy())


@pytest.mark.asyncio
async def test_default_guide_requires_its_installed_snapshot(server):
    from fastmcp.resources import TextResource
    from transport.skills_extension import SKILL_URI, install_skills

    assert install_skills(server) is not None
    server.add_resource(TextResource(uri=SKILL_URI, text="Replacement document"))
    async with Client(server, cache=False) as client:
        for name, args in (("read_unity_resource", {}), ("manage_tools", {"action": "read_skill"})):
            assert (await client.call_tool(name, args, raise_on_error=False)).is_error
        explicit = await client.call_tool("read_unity_resource", {"uri": SKILL_URI})
        assert explicit.structured_content["contents"][0]["text"] == "Replacement document"


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden_via", ["disabled", "list", "replacement"])
async def test_resource_catalog_offers_only_its_visible_serving_reader(server, hidden_via):
    from fastmcp.resources import TextResource

    uri = "mcpforunity://fixture/info"
    server.add_resource(TextResource(uri=uri, text="info"))
    if hidden_via == "disabled":
        server.disable(keys={(await server.get_tool("read_unity_resource")).key})
    elif hidden_via == "list":

        class HideReader(Middleware):
            async def on_list_tools(self, context, call_next):
                return [t for t in await call_next(context) if t.name != "read_unity_resource"]

        server.add_middleware(HideReader())
    else:

        @server.tool(name="read_unity_resource")
        def replacement(uri: str) -> str:
            pytest.fail("The catalog executed a replacement")

    async with Client(server, cache=False) as client:
        page = await client.call_tool("manage_tools", {"action": "catalog", "group": "resources"})
        [row] = page.structured_content["resources"]
        assert "preferred_read_tool" not in row
        assert row["read_tool"]["tool"] == "manage_tools"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_undeclared_providers_are_native_only_in_catalog_and_calls(server, mode):
    from fastmcp.resources import TextResource

    fixed = "mcpforunity://editor/game-view-size"
    template = "mcpforunity://scene/gameobject/{instance_id}/component/{component_name}"
    skill = "skill://unity-mcp-orchestrator/SKILL.md"
    reads = []

    @server.resource(fixed, name="game_view_size", meta={"catalog_observation": True})
    def observer() -> str:
        reads.append("fixed")
        return "fixed"

    @server.resource(template, name="gameobject_component")
    def component(instance_id: str, component_name: str) -> str:
        reads.append(component_name)
        return instance_id

    @server.resource(skill)
    def document() -> str:
        reads.append("skill")
        return "skill"

    class CustomText(TextResource):
        async def read(self) -> str:
            reads.append("subclass")
            return self.text

    subclass = "mcpforunity://custom/text"
    server.add_resource(CustomText(uri=subclass, text="custom"))
    privileged = "mcpforunity://scene/gameobject/7/components?evaluate_getters=true"
    server.add_resource(TextResource(uri=privileged, text="fixed native option"))
    async with Client(server, mode=mode, cache=False) as client:
        for group, key in (
            ("resources", "read_tool"),
            ("resource_templates", "read_after_expansion"),
            ("skills", "read_tool"),
        ):
            catalog = await client.call_tool("manage_tools", {"action": "catalog", "group": group})
            for row in catalog.structured_content[group]:
                assert key not in row
                assert "preferred_" + key not in row
                native = row.get("read_call") or row["native_read_after_expansion"]
                assert native["method"] == "resources/read"
        expanded = template.replace("{instance_id}", "7").replace("{component_name}", "Canvas")
        for uri in (fixed, expanded, skill, subclass, privileged):
            declined = await client.call_tool(
                "manage_tools", {"action": "read_resource", "uri": uri}, raise_on_error=False
            )
            assert declined.is_error
            assert (
                await client.call_tool("read_unity_resource", {"uri": uri}, raise_on_error=False)
            ).is_error
        assert (
            await client.call_tool("manage_tools", {"action": "read_skill"}, raise_on_error=False)
        ).is_error
        assert (await client.call_tool("read_unity_resource", {}, raise_on_error=False)).is_error
        assert reads == []
        for uri in (fixed, expanded, skill, subclass, privileged):
            await client.read_resource(uri)
        assert reads == ["fixed", "Canvas", "skill", "subclass"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("reader", ["manage_tools", "read_unity_resource"])
async def test_registered_component_preserves_selectors_and_reserves_getters_for_native(
    command_hub, mode, reader
):
    from services.resources import register_all_resources
    from services.tools.manage_tools import manage_tools
    from services.tools.read_unity_resource import read_unity_resource
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("registered observations")
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(manage_tools)
    app.tool(read_unity_resource)
    register_all_resources(app)
    target = {"unity_instance": "Fixture@hash"}
    uri = "mcpforunity://scene/gameobject/7/component/UnityEngine.Canvas"
    async with Client(app, cache=False, mode=mode) as client:

        async def read(uri):
            return await client.call_tool(
                reader,
                {
                    **({"action": "read_resource"} if reader == "manage_tools" else {}),
                    "uri": uri,
                    **target,
                },
                raise_on_error=False,
            )

        templates = (
            await client.call_tool(
                "manage_tools",
                {"action": "catalog", "group": "resource_templates", "limit": 20, **target},
            )
        ).structured_content
        template = next(
            t for t in templates["resource_templates"] if t["name"] == "gameobject_component"
        )
        assert template["read_after_expansion"]["arguments"] == {
            "action": "read_resource",
            **target,
        }
        assert (
            "getter" in template["read_guidance"] and "resources/read" in template["read_guidance"]
        )
        assert template["native_read_after_expansion"]["params"]["_meta"] == target
        for option in (
            "evaluate_getters=true",
            "%65valuate_getters=%74rue",
            "evaluateGetters=true",
            "evaluate_getters=false&evaluate_getters=true",
            "evaluate_getters=1",
            "unknown=true",
            "component_index=1&component_index=2",
        ):
            assert (await read(uri + "?" + option)).is_error
        assert (await read(uri + "#skip")).is_error
        assert command_hub.sent == []
        result = await read(uri + "?component_index=1&evaluate_getters=false")
        assert json.loads(result.structured_content["contents"][0]["text"])["success"]
        await client.read_resource(uri + "?evaluate_getters=true", meta=target)
        commands = [(m["name"], m["params"]) for m in command_hub.sent if m["type"] == "execute"]
        assert commands == [
            (
                "get_gameobject_component_selected",
                {"instanceID": 7, "componentName": "UnityEngine.Canvas", "componentIndex": 1},
            ),
            (
                "get_gameobject_component",
                {"instanceID": 7, "componentName": "UnityEngine.Canvas", "evaluateGetters": True},
            ),
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_compact_template_families_bind_the_requested_observation_or_native_options(
    command_hub, monkeypatch, mode
):
    from fastmcp.resources.template import expand_uri_template
    from tests.test_camera_inventory_tool import inventory_server

    target = {"unity_instance": "Fixture@hash"}
    search = {
        "selection": "agent",
        "query": "Inspect these components",
        "route_surfaces": "resources",
        "limit": 20,
        **target,
    }
    async with Client(
        inventory_server(monkeypatch, profile="full"), mode=mode, cache=False
    ) as client:

        async def directory(**scope):
            page = await client.call_tool("search_unity_tools", {**search, **scope})
            return page.structured_content

        offset = 0
        while True:
            page = await directory(offset=offset)
            candidates = [
                row for row in page["families"] if "/component/{component_name}" in row["family"]
            ]
            if candidates:
                break
            offset = page["next_offset"]
            assert offset is not None

        (row,) = candidates
        family = row["family"]
        fixed = {"evaluate_getters": "false"}
        assert "strict_selection,evaluate_getters}" in family
        adapter = row["preferred_read_after_expansion"]
        native = row["native_read_after_expansion"]
        assert adapter["fixed_uri_variables"] == fixed
        assert adapter["arguments"] == target
        assert "fixed_uri_variables" not in native
        assert native["method"] == "resources/read" and native["params"]["_meta"] == target
        for hosts, key in ((["manage_tools"], "read_after_expansion"), ([], None)):
            scoped = await directory(offset=offset, host_tool_names=hosts)
            listed = next(item for item in scoped["families"] if item["family"] == family)
            assert "preferred_read_after_expansion" not in listed
            assert listed["native_read_after_expansion"] == native
            if key:
                assert listed[key]["fixed_uri_variables"] == fixed
            else:
                assert "read_after_expansion" not in listed
        detailed = await directory(family=family)
        (binding,) = detailed["operations"]
        assert binding["preferred_read_after_expansion"] == adapter
        assert binding["read_after_expansion"]["fixed_uri_variables"] == fixed

        variables = {"instance_id": -15782, "component_name": "Canvas", "strict_selection": "true"}
        for options in ({}, adapter["fixed_uri_variables"]):
            expanded = expand_uri_template(family, {**variables, **options})
            args = {**adapter["arguments"], adapter["set_argument"]: expanded}
            result = await client.call_tool(adapter["tool"], args)
            assert json.loads(result.structured_content["contents"][0]["text"])["success"]
        requested = expand_uri_template(family, {**variables, "evaluate_getters": "true"})
        for tool, args in (
            ("read_unity_resource", {"uri": requested}),
            ("manage_tools", {"action": "read_resource", "uri": requested}),
        ):
            preview = await client.call_tool(
                "manage_tools", {"action": "prepare", "tool": tool, "arguments": args, **target}
            )
            assert preview.structured_content["ready"] is False
            refused = await client.call_tool(tool, {**args, **target}, raise_on_error=False)
            assert refused.is_error
        result = await client.read_resource(requested, meta=native["params"]["_meta"])
        assert json.loads(result[0].text)["success"]
        expected = {"instanceID": -15782, "componentName": "Canvas", "strictSelection": True}
        assert [(m["name"], m["params"]) for m in command_hub.sent] == [
            ("get_gameobject_component_selected", expected),
            ("get_gameobject_component_selected", expected),
            ("get_gameobject_component_selected", {**expected, "evaluateGetters": True}),
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_template_option_domains_follow_the_published_binding(server, mode):
    trusted = "mcpforunity://meter/{meter}{?page}{&evaluate_getters}"
    other = "mcpforunity://native/{meter}{?evaluate_getters}"
    implicit = "mcpforunity://implicit/{meter}"
    repeated = "mcpforunity://repeated/{meter}{?evaluate_getters*}"
    literal = "mcpforunity://literal/{meter}?reading={evaluate_getters}"
    path = "mcpforunity://path/{meter}/{evaluate_getters}"
    hyphen = "mcpforunity://hyphen/{meter}{?evaluate-getters}"
    reads = []

    @partial(register_catalog_observation, server, trusted)
    def observation(meter: str, page: str = "1", evaluate_getters: str = "true") -> str:
        return f"{meter}/{page}/{evaluate_getters}"

    @server.resource(other)
    def native_only(meter: str, evaluate_getters: str = "false") -> str:
        return f"{meter}/{evaluate_getters}"

    def conditional(meter: str, evaluate_getters: str = "true") -> str:
        reads.append(evaluate_getters)
        return f"{meter}/{evaluate_getters}"

    for template in (implicit, literal, path, hyphen):
        register_catalog_observation(server, template, conditional)

    @partial(register_catalog_observation, server, repeated)
    def multi(meter: str, evaluate_getters: list[str] | None = None) -> str:
        reads.append(evaluate_getters)
        return meter

    async with Client(server, mode=mode, cache=False) as client:
        request = {
            "query": "meter",
            "selection": "agent",
            "route_surfaces": "resources",
            "limit": 20,
        }
        result = await client.call_tool("search_unity_tools", request)
        rows = {row["family"]: row for row in result.structured_content["families"]}
        assert rows[trusted]["preferred_read_after_expansion"]["fixed_uri_variables"] == {
            "evaluate_getters": "false"
        }
        assert rows[trusted]["preferred_read_after_expansion"]["required_uri_variables"] == [
            "evaluate_getters"
        ]
        for template in (other, implicit, repeated, literal):
            assert rows[template]["native_read_after_expansion"]["method"] == "resources/read"
            assert (
                "preferred_read_after_expansion" not in rows[template]
                and "read_after_expansion" not in rows[template]
            )
        for template, variable in ((path, "evaluate_getters"), (hyphen, "evaluate-getters")):
            adapter = rows[template]["preferred_read_after_expansion"]
            assert adapter["fixed_uri_variables"] == {variable: "false"}
            assert adapter["required_uri_variables"] == [variable]
        value = await client.call_tool(
            "read_unity_resource", {"uri": "mcpforunity://meter/main?page=2&evaluate_getters=false"}
        )
        assert value.structured_content["contents"][0]["text"] == "main/2/false"
        omitted = await client.call_tool(
            "read_unity_resource", {"uri": "mcpforunity://meter/main?page=2"}, raise_on_error=False
        )
        assert omitted.is_error
        for uri, permitted in (
            ("mcpforunity://implicit/main", False),
            ("mcpforunity://repeated/main?evaluate_getters=false", False),
            ("mcpforunity://literal/main?reading=false", False),
            ("mcpforunity://path/main/true", False),
            ("mcpforunity://path/main/false", True),
            ("mcpforunity://hyphen/main?evaluate-getters=true", False),
            ("mcpforunity://hyphen/main?evaluate-getters=false", True),
        ):
            args = {"uri": uri}
            before = len(reads)
            preview = await client.call_tool(
                "manage_tools",
                {"action": "prepare", "tool": "read_unity_resource", "arguments": args},
            )
            assert preview.structured_content["ready"] is permitted, uri
            assert len(reads) == before
            actual = await client.call_tool("read_unity_resource", args, raise_on_error=False)
            assert actual.is_error is not permitted, uri
        assert reads == ["false", "false"]
        assert (await client.read_resource("mcpforunity://implicit/main"))[0].text == "main/true"
        assert (await client.read_resource("mcpforunity://literal/main?reading=false"))[
            0
        ].text == "main/false"
        assert (await client.read_resource("mcpforunity://repeated/main?evaluate_getters=false"))[
            0
        ].text == "main"
        assert reads[-3:] == ["true", "false", ["false"]]


@pytest.mark.asyncio
async def test_read_rechecks_the_serving_owner_after_resource_middleware(server):
    uri = "mcpforunity://fixture/change"
    reads = []

    @partial(register_catalog_observation, server, uri)
    def original() -> str:
        reads.append("original")
        return "original"

    def replacement() -> str:
        reads.append("replacement")
        return "replacement"

    class ReplaceAtRead(Middleware):
        async def on_read_resource(self, context, call_next):
            server.resource(uri)(replacement)
            return await call_next(context)

    server.add_middleware(ReplaceAtRead())
    async with Client(server, cache=False) as client:
        catalog = await client.call_tool(
            "manage_tools", {"action": "catalog", "group": "resources"}
        )
        binding = catalog.structured_content["resources"][0]["read_tool"]
        assert (
            await client.call_tool(binding["tool"], binding["arguments"], raise_on_error=False)
        ).is_error
        assert not reads


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("replacement_time", ["before_call", "read_middleware"])
async def test_fixed_inventory_adapters_pin_the_registered_owner(
    command_hub, mode, replacement_time
):
    from fastmcp.resources import TextResource
    from services.resources import register_all_resources
    from services.tools import register_all_tools
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("fixed inventory ownership")
    app.add_middleware(UnityInstanceMiddleware())
    register_all_tools(app)
    register_all_resources(app)
    calls = {
        "mcpforunity://instances": ("manage_tools", {"action": "list_instances"}),
        "mcpforunity://scene/cameras": ("inspect_scene_cameras", {}),
        "mcpforunity://menu-items": ("list_menu_declarations", {}),
    }
    reads = []
    replaced = set()

    def replace(uri):
        @app.resource(uri, meta={"catalog_observation": True})
        def substitute() -> str:
            reads.append(uri)
            return '{"success":true,"source":"substitute"}'

        replaced.add(uri)

    class ChangeOwner(Middleware):
        async def on_read_resource(self, context, call_next):
            uri = str(context.message.uri)
            if uri in calls and uri not in replaced:
                replace(uri)
            return await call_next(context)

    if replacement_time == "read_middleware":
        app.add_middleware(ChangeOwner())
    async with Client(app, mode=mode, cache=False) as client:
        initial = {tool.name for tool in await client.list_tools()}
        assert {tool for tool, _ in calls.values()} <= initial
        for uri, (tool, args) in calls.items():
            target = {} if tool == "manage_tools" else {"unity_instance": "Fixture@hash"}
            if replacement_time == "before_call":
                replace(uri)
            for name, arguments in (
                ("manage_tools", {"action": "read_resource", "uri": uri}),
                ("read_unity_resource", {"uri": uri}),
            ):
                generic = await client.call_tool(
                    name, {**arguments, **target}, raise_on_error=False
                )
                assert generic.is_error and uri not in reads
            denied = await client.call_tool(tool, {**args, **target}, raise_on_error=False)
            assert denied.is_error and uri not in reads
            native = await client.read_resource(uri, meta=target)
            assert json.loads(native[0].text)["source"] == "substitute"
        visible = {tool.name for tool in await client.list_tools()}
        assert {"inspect_scene_cameras", "list_menu_declarations"}.isdisjoint(visible)
        assert "manage_tools" in visible
        # A safe static value also lacks authority to speak for the connection inventory.
        app.add_resource(TextResource(uri="mcpforunity://instances", text='{"success":true}'))
        refused = await client.call_tool(
            "manage_tools", {"action": "list_instances"}, raise_on_error=False
        )
        assert refused.is_error
        for uri in calls:
            app.add_resource(TextResource(uri=uri, text="unrelated replacement"))
            refused = await client.call_tool(
                "read_unity_resource", {"uri": uri}, raise_on_error=False
            )
            assert refused.is_error
    assert reads == list(calls)
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_published_project_inventories_follow_the_selected_editor(
    command_hub, monkeypatch, mode
):
    from fastmcp.resources import TextResource
    from tests.http.test_project_capabilities import install_native_replies
    from tests.test_camera_inventory_tool import inventory_server

    selected = {"unity_instance": "Fixture@hash"}
    uri = "mcpforunity://editor/state"
    await install_native_replies(
        command_hub,
        {
            "get_editor_state": {"sequence": 19, "compilation": {"is_compiling": False}},
            "get_tags": ["Player"],
            "get_layers": {"6": "Fixture"},
        },
    )
    server = inventory_server(monkeypatch)
    async with Client(server, mode=mode, cache=False) as client:
        sole = await client.call_tool("read_unity_resource", {"uri": uri})
        sole_state = json.loads(sole.structured_content["contents"][0]["text"])["data"]
        assert sole_state["unity"]["instance_id"] == selected["unity_instance"]
        assert sole_state["sequence"] == 19
    await command_hub.registry.register("other", "Other", "other", "6000.3")
    async with Client(server, mode=mode, cache=False) as client:
        for resource, native in (
            (uri, "get_editor_state"),
            ("mcpforunity://project/tags", "get_tags"),
            ("mcpforunity://project/layers", "get_layers"),
        ):
            args = {"uri": resource}
            discovery = {"selection": "agent", "family": resource, **selected}
            catalog = await client.call_tool("search_unity_tools", discovery)
            (listed,) = catalog.structured_content["operations"]
            assert listed["preferred_read_tool"]["arguments"] == {**args, **selected}
            refused = await client.call_tool("read_unity_resource", args, raise_on_error=False)
            assert refused.is_error
            reply = await client.call_tool("read_unity_resource", {**args, **selected})
            payload = json.loads(reply.structured_content["contents"][0]["text"])
            assert payload["success"]
            assert command_hub.sent[-1]["name"] == native
            resource_data = json.loads(
                (await client.read_resource(resource, meta=selected))[0].text
            )["data"]
            if resource == uri:
                dedicated = (
                    await client.call_tool("inspect_editor_state", selected)
                ).structured_content
                for data in (resource_data, payload["data"], dedicated["data"]):
                    assert data["sequence"] == 19 and data["compilation"]["is_compiling"] is False
                    assert data["unity"]["instance_id"] == selected["unity_instance"]
            else:
                assert payload["data"] == resource_data
            server.add_resource(TextResource(uri=resource, text="substitute"))
            denied = await client.call_tool(
                "read_unity_resource", {**args, **selected}, raise_on_error=False
            )
            assert denied.is_error
            catalog = await client.call_tool("search_unity_tools", discovery)
            (listed,) = catalog.structured_content["operations"]
            assert "read_tool" not in listed and "preferred_read_tool" not in listed
        refused = await client.call_tool(
            "read_unity_resource",
            {"uri": "mcpforunity://scene/cameras", **selected},
            raise_on_error=False,
        )
        assert refused.is_error
    assert all(
        item["name"] in {"get_editor_state", "get_tags", "get_layers"} for item in command_hub.sent
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_reserved_concrete_uri_cannot_fall_back_to_an_observational_template(
    command_hub, monkeypatch, mode
):
    from tests.test_camera_inventory_tool import inventory_server

    server = inventory_server(monkeypatch)
    uri = "mcpforunity://editor/state"
    server.local_provider.remove_resource(uri)

    @partial(register_catalog_observation, server, "mcpforunity://editor/{part}")
    def published(part: str) -> str:
        return part

    async with Client(server, mode=mode, cache=False) as client:
        assert (await client.read_resource(uri))[0].text == "state"
        denied = await client.call_tool("read_unity_resource", {"uri": uri}, raise_on_error=False)
        assert denied.is_error
        ordinary = await client.call_tool(
            "read_unity_resource", {"uri": "mcpforunity://editor/unreserved"}
        )
        assert ordinary.structured_content["contents"][0]["text"] == "unreserved"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_connection_inventory_prepared_with_selected_editor_can_be_called(
    command_hub, monkeypatch, mode
):
    from tests.test_camera_inventory_tool import inventory_server

    uri = "mcpforunity://instances"
    selected = {"unity_instance": "Fixture@hash"}
    async with Client(inventory_server(monkeypatch), mode=mode, cache=False) as client:
        result = await client.call_tool(
            "search_unity_tools", {"selection": "agent", "family": uri, **selected}
        )
        (row,) = result.structured_content["operations"]
        for binding in ("read_tool", "preferred_read_tool"):
            assert "unity_instance" not in row[binding]["arguments"]
        for tool, operation, args in (
            ("read_unity_resource", {}, {"uri": uri}),
            ("manage_tools", {"operation": "read_resource"}, {"uri": uri}),
            ("manage_tools", {}, {"action": "read_resource", "uri": uri}),
        ):
            result = await client.call_tool(
                "manage_tools",
                {"action": "prepare", "tool": tool, **operation, "arguments": args, **selected},
            )
            preview = result.structured_content
            assert preview["ready"], preview
            call = preview["prepared_call"]
            assert "unity_instance" not in call["arguments"]
            assert preview["execution"]["request_unity_instance"] is None
            payload = (await client.call_tool(call["tool"], call["arguments"])).structured_content
            assert (
                json.loads(payload["contents"][0]["text"])["instances"][0]["id"]
                == selected["unity_instance"]
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("project_uri", [False, True])
async def test_advertised_read_resource_hints_cover_observations_only(server, mode, project_uri):
    if project_uri:

        class ConstrainResourceUri(Middleware):
            async def on_list_tools(self, context, call_next):
                tools = []
                for tool in await call_next(context):
                    if tool.name == "manage_tools":
                        fields = tool.parameters["properties"]
                        schema = {
                            **tool.parameters,
                            "properties": {**fields, "uri": {**fields["uri"], "minLength": 24}},
                        }
                        tool = tool.model_copy(update={"parameters": schema})
                    tools.append(tool)
                return tools

        server.add_middleware(ConstrainResourceUri())
    async with Client(server, mode=mode) as client:
        tool = next(t for t in await client.list_tools() if t.name == "manage_tools")
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False
        described = (
            await client.call_tool(
                "manage_tools",
                {"action": "describe", "tool": "manage_tools", "operation": "read_resource"},
            )
        ).structured_content
        card = described["operation"]
        assert "evaluate_getters=true" in card["purpose"]
        if project_uri:
            assert described["tool"]["input_schema"]["properties"]["uri"]["minLength"] == 24
        assert card["effects"] == {
            "readOnlyHint": not project_uri,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_read_drafts_and_calls_accept_the_same_selector_inputs(server, mode):
    uri = "skill://guide/SKILL.md"
    reads = []

    @partial(register_catalog_observation, server, uri)
    def guide() -> str:
        reads.append(uri)
        return "content"

    unrelated = {
        "group": "core",
        "tool": "manage_tools",
        "query": "guide",
        "catalog_query": "guide",
        "operation": "read_skill",
        "intent": "observe",
        "observation_source": "editor_live",
        "required_values": ["contents"],
        "route_surfaces": "resources",
        "host_tool_names": [],
        "host_tool_evidence": {"candidates": ["manage_tools"], "present": [], "complete": False},
        "family": "guide",
        "arguments": {},
        "exact_text": [{"path": ["uri"], "expected": "guide"}],
        "include_schema": True,
        "offset": 1,
        "selection": "agent",
        "limit": 21,
    }
    neutral = {field: None for field in unrelated}
    neutral.update(include_schema=False, offset=0, selection="ranked", limit=20)
    async with Client(server, mode=mode, cache=False) as client:
        schema = next(t.input_schema for t in await client.list_tools() if t.name == "manage_tools")
        advertised = Draft202012Validator(schema)
        for action, selector in (("read_skill", "skill_uri"), ("read_resource", "uri")):
            args = {"action": action, selector: uri}
            for field, value in {**unrelated, selector: "file:///unavailable"}.items():
                trial = {**args, field: value}
                assert not advertised.is_valid(trial), (action, field)
                prepared = await client.call_tool(
                    "manage_tools",
                    {"action": "prepare", "tool": "manage_tools", "arguments": trial},
                )
                assert not prepared.structured_content["ready"], (action, field)
                direct = await client.call_tool("manage_tools", trial, raise_on_error=False)
                assert direct.is_error or direct.structured_content["success"] is False, (
                    action,
                    field,
                )
            # Binding the operation during prepare uses the action-specific schema.
            selected = {"action": "prepare", "tool": "manage_tools", "operation": action}
            invalid = await client.call_tool(
                "manage_tools", {**selected, "arguments": {selector: uri, "group": "core"}}
            )
            assert not invalid.structured_content["ready"]
            valid = {**args, **neutral}
            advertised.validate(valid)
            prepared = (
                await client.call_tool("manage_tools", {**selected, "arguments": valid})
            ).structured_content
            assert prepared["ready"] and prepared["prepared_call"]["arguments"] == valid
            result = await client.call_tool("manage_tools", valid)
            assert result.structured_content["contents"][0]["text"] == "content"
        assert reads == [uri, uri]


def test_tool_uses_expanded_resource_permissions_for_receipts_and_scoped_access():
    from services.operation_permissions import (
        OperationGrants,
        OperationPermissionError,
        Permission,
        authorize_operation,
        required_permissions,
    )

    read = frozenset({Permission.READ})
    getters = read | {Permission.EDIT, Permission.RUN}
    uri = "mcpforunity://scene/gameobject/3/component/Fixture"
    for query, expected in (
        ("", read),
        ("?evaluate_getters=false", read),
        ("?evaluate_getters=true", getters),
    ):
        args = {"action": "read_resource", "uri": uri + query}
        assert required_permissions("manage_tools", args) == expected
        authorize_operation(OperationGrants(expected, True), "manage_tools", args)
        with pytest.raises(OperationPermissionError):
            authorize_operation(
                OperationGrants(expected - {Permission.READ}, True), "manage_tools", args
            )
    # The local template decodes its path argument before the handler parses the
    # integer. These selectors must carry the same pre-dispatch and receipt floor.
    for selector in ("+03", "%33", "%203%20"):
        alternate = uri.replace("/3/", f"/{selector}/")
        for query, expected in (
            ("?evaluate_getters=true", getters),
            ("?evaluate_getters=false", read),
        ):
            args = {"action": "read_resource", "uri": alternate + query}
            assert required_permissions("manage_tools", args) == expected
            with pytest.raises(OperationPermissionError):
                authorize_operation(OperationGrants(getters, True), "manage_tools", args)
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            OperationGrants(read, True),
            "manage_tools",
            {"action": "read_resource", "uri": uri + "?evaluate_getters=true"},
        )
    for value in (
        "mcpforunity://unregistered",
        "skill://guide/SKILL.md",
        uri + "?evaluateGetters=true",
        uri + "?evaluate_getters=false&evaluate_getters=true",
    ):
        with pytest.raises(OperationPermissionError):
            authorize_operation(
                OperationGrants(getters, True),
                "manage_tools",
                {"action": "read_resource", "uri": value},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_grants", "plugin_grants"),
    [
        (["read"], ["read"]),
        (["read"], ["edit", "run"]),
        (["edit", "run"], ["read"]),
        (["read", "edit", "run"], ["read", "edit", "run"]),
    ],
)
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("reader", ["manage_tools", "read_unity_resource"])
async def test_observation_requires_both_read_grants_and_never_executes_getters(
    command_hub, monkeypatch, request_grants, plugin_grants, mode, reader
):
    from core.config import config
    from services.credential_lease import bind_credential_lease
    from services.tools.manage_tools import manage_tools
    from services.tools.read_unity_resource import read_unity_resource
    from tests.http.test_remote_permissions import permission_service
    from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("resource read grants")
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(name="manage_tools")(manage_tools)
    app.tool(read_unity_resource)
    reads = []

    @partial(
        register_catalog_observation,
        app,
        "mcpforunity://scene/gameobject/{object_id}/components{?evaluate_getters}",
    )
    def observe(object_id: str, evaluate_getters: str = "false") -> str:
        reads.append(object_id)
        return evaluate_getters

    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    grants = {
        key: {"unity_mcp_permissions": permissions}
        for key, permissions in (("request", request_grants), ("plugin", plugin_grants))
    }
    async with permission_service(monkeypatch, grants) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            async with Client(app, mode=mode) as client:
                args = {
                    **({"action": "read_resource"} if reader == "manage_tools" else {}),
                    "uri": "mcpforunity://scene/gameobject/3/components",
                    "unity_instance": "Fixture@hash",
                }
                with pytest.raises((MCPError, ToolError)):
                    await client.call_tool(
                        reader, {**args, "uri": args["uri"] + "?evaluate_getters=true"}
                    )
                assert not reads
                if "read" in request_grants and "read" in plugin_grants:
                    result = await client.call_tool(reader, args)
                    assert result.structured_content["contents"][0]["text"] == "false"
                    assert reads == ["3"]
                else:
                    with pytest.raises(MCPError):
                        await client.call_tool(reader, args)
                    assert not reads
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("reader", ["manage_tools", "read_unity_resource"])
async def test_expanded_resource_keeps_outer_receipt_and_retrievable_resource_result(
    command_hub, tmp_path, monkeypatch, mode, reader
):
    from core.config import config
    from services.execution_receipt_identity import ReceiptNotFound
    from services.tools.manage_tools import manage_tools
    from services.tools.read_unity_resource import read_unity_resource
    from services.tools.read_response import read_response
    from tests.test_execution_receipt_integration import KEY, PROJECT, options
    from transport import response_limit_middleware as limits
    from transport.execution_receipts import META_KEY, install_execution_receipts
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("retained resource")
    app.add_middleware(limits.ResponseLimitMiddleware())
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(manage_tools)
    app.tool(read_unity_resource)
    app.tool(read_response)
    runtime = install_execution_receipts(app, tmp_path / "receipts", private_root=True)
    await runtime.start()
    monkeypatch.setattr(config, "require_explicit_target", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", PROJECT, "6000")
    await command_hub.registry.register("second", "Other", "aaaaaaaaaaaaaaaa", "6000")
    target = {"unity_instance": "Fixture@" + PROJECT}
    value = "payload" * 5000
    reads = []

    @partial(
        register_catalog_observation,
        app,
        "mcpforunity://scene/gameobject/{object_id}/components{?evaluate_getters}",
    )
    def observe(object_id: str, evaluate_getters: str = "false") -> str:
        reads.append((object_id, evaluate_getters))
        return value

    try:
        async with Client(app, mode=mode, cache=False) as client:
            args = {
                **({"action": "read_resource"} if reader == "manage_tools" else {}),
                "uri": "mcpforunity://scene/gameobject/3/components?evaluate_getters=false",
            }
            ambiguous = await client.call_tool(
                reader,
                {**args, "uri": args["uri"].replace("false", "true")},
                raise_on_error=False,
            )
            assert ambiguous.is_error and not reads
            meta = {META_KEY: options(runtime)}
            with monkeypatch.context() as pressure:
                pressure.setattr(limits, "MCP_RESULT_BYTES", 8192)
                first = await client.call_tool(reader, {**args, **target}, meta=meta)
                replay = await client.call_tool(reader, {**args, **target}, meta=meta)
            assert replay.meta[META_KEY]["replayed"]
            assert replay.structured_content == first.structured_content
            handle = json.loads(first.structured_content["contents"][0]["text"])["delivery"]
            assert handle["state"] == "retained"
            with pytest.raises(ToolError, match="response_not_found"):
                await client.call_tool(
                    "read_response",
                    {
                        "response_id": handle["response_id"],
                        "unity_instance": "Other@aaaaaaaaaaaaaaaa",
                    },
                )
            lookup = {"response_id": handle["response_id"], **target}
            page = await client.call_tool("read_response", lookup)
            retained = json.loads(base64.b64decode(page.structured_content["data"]["data_base64"]))
            assert retained["contents"] == [
                {"uri": args["uri"], "mimeType": "text/plain", "text": value}
            ]
            await client.call_tool("read_response", {**lookup, "release": True})
            with pytest.raises(ToolError, match="response_not_found"):
                await client.call_tool("read_response", lookup)
            assert reads == [("3", "false")]
            scope = (await runtime.authority(PROJECT)).scope
            with pytest.raises(ReceiptNotFound):
                runtime.store.get(
                    scope,
                    store_id=runtime.store.store_id,
                    grants=frozenset(),
                    idempotency_key=KEY,
                )
    finally:
        runtime.close()
