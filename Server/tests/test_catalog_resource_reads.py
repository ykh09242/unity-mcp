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
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("catalog resource reads")
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(name="manage_tools")(manage_tools)
    app.tool(name="search_unity_tools")(search_unity_tools)
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
        for scope, available in [
            ({}, True),
            ({"host_tool_names": ["manage_tools"]}, True),
            ({"host_tool_names": []}, False),
            (
                {
                    "host_tool_evidence": {
                        "candidates": ["manage_tools"],
                        "present": [],
                        "complete": False,
                    }
                },
                False,
            ),
        ]:
            page = await call("search_unity_tools", selection="agent", family=uri, **scope)
            [binding] = page["operations"]
            assert binding["read_call"] == native
            assert binding.get("read_tool") == (tool if available else None)
        resources = await call("manage_tools", action="catalog", group="resource_templates")
        [template] = resources["resource_templates"]
        page = await call("search_unity_tools", selection="agent", family=template_uri)
        [operation] = page["operations"]
        for descriptor in (operation, template):
            assert "read_tool" not in descriptor and "read_call" not in descriptor
            adapter = descriptor["read_after_expansion"]
            assert adapter["arguments"] == {"action": "read_resource", **target}
            assert adapter["set_argument"] == "uri"
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
            assert (await client.read_resource(resolved, meta=target))[0].text == value
        assert reads == ["concrete", "concrete", "other", "other"]
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
async def test_projected_packaged_skill_reads_match_the_native_resource(server):
    from transport.skills_extension import SKILL_URI, install_skills

    assert install_skills(server) is not None

    class Projection(Middleware):
        async def on_list_resources(self, context, call_next):
            return [
                r.model_copy(update={"description": "Projected guide"})
                for r in await call_next(context)
            ]

    server.add_middleware(Projection())
    async with Client(server, cache=False) as client:
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
                native = row.get("read_call") or row["native_read_after_expansion"]
                assert native["method"] == "resources/read"
        expanded = template.replace("{instance_id}", "7").replace("{component_name}", "Canvas")
        for uri in (fixed, expanded, skill, subclass, privileged):
            declined = await client.call_tool(
                "manage_tools", {"action": "read_resource", "uri": uri}, raise_on_error=False
            )
            assert declined.is_error
        assert (
            await client.call_tool("manage_tools", {"action": "read_skill"}, raise_on_error=False)
        ).is_error
        assert reads == []
        for uri in (fixed, expanded, skill, subclass, privileged):
            await client.read_resource(uri)
        assert reads == ["fixed", "Canvas", "skill", "subclass"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_registered_component_preserves_selectors_and_reserves_getters_for_native(
    command_hub, mode
):
    from services.resources import register_all_resources
    from services.tools.manage_tools import manage_tools
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("registered observations")
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(manage_tools)
    register_all_resources(app)
    target = {"unity_instance": "Fixture@hash"}
    uri = "mcpforunity://scene/gameobject/7/component/UnityEngine.Canvas"
    async with Client(app, cache=False, mode=mode) as client:

        async def read(uri):
            return await client.call_tool(
                "manage_tools",
                {"action": "read_resource", "uri": uri, **target},
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
            # These owners were registered only for their fixed inventory operations.
            if replacement_time == "before_call":
                generic = await client.call_tool(
                    "manage_tools",
                    {"action": "read_resource", "uri": uri, **target},
                    raise_on_error=False,
                )
                assert generic.is_error
                replace(uri)
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
    assert reads == list(calls)
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_advertised_read_resource_hints_cover_observations_only(server):
    async with Client(server) as client:
        tool = next(t for t in await client.list_tools() if t.name == "manage_tools")
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False
        card = (
            await client.call_tool(
                "manage_tools",
                {"action": "describe", "tool": "manage_tools", "operation": "read_resource"},
            )
        ).structured_content["operation"]
        assert "evaluate_getters=true" in card["purpose"]
        assert card["effects"] == {
            "readOnlyHint": True,
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
async def test_observation_requires_both_read_grants_and_never_executes_getters(
    command_hub, monkeypatch, request_grants, plugin_grants, mode
):
    from core.config import config
    from services.credential_lease import bind_credential_lease
    from services.tools.manage_tools import manage_tools
    from tests.http.test_remote_permissions import permission_service
    from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("resource read grants")
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(name="manage_tools")(manage_tools)
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
                    "action": "read_resource",
                    "uri": "mcpforunity://scene/gameobject/3/components",
                    "unity_instance": "Fixture@hash",
                }
                with pytest.raises((MCPError, ToolError)):
                    await client.call_tool(
                        "manage_tools", {**args, "uri": args["uri"] + "?evaluate_getters=true"}
                    )
                assert not reads
                if "read" in request_grants and "read" in plugin_grants:
                    result = await client.call_tool("manage_tools", args)
                    assert result.structured_content["contents"][0]["text"] == "false"
                    assert reads == ["3"]
                else:
                    with pytest.raises(MCPError):
                        await client.call_tool("manage_tools", args)
                    assert not reads
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_expanded_resource_keeps_outer_receipt_and_retrievable_resource_result(
    command_hub, tmp_path, monkeypatch, mode
):
    from core.config import config
    from services.execution_receipt_identity import ReceiptNotFound
    from services.tools.manage_tools import manage_tools
    from services.tools.read_response import read_response
    from tests.test_execution_receipt_integration import KEY, PROJECT, options
    from transport import response_limit_middleware as limits
    from transport.execution_receipts import META_KEY, install_execution_receipts
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    app = FastMCP("retained resource")
    app.add_middleware(limits.ResponseLimitMiddleware())
    app.add_middleware(UnityInstanceMiddleware())
    app.tool(manage_tools)
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
                "action": "read_resource",
                "uri": "mcpforunity://scene/gameobject/3/components?evaluate_getters=false",
            }
            ambiguous = await client.call_tool(
                "manage_tools",
                {**args, "uri": args["uri"].replace("false", "true")},
                raise_on_error=False,
            )
            assert ambiguous.is_error and not reads
            meta = {META_KEY: options(runtime)}
            with monkeypatch.context() as pressure:
                pressure.setattr(limits, "MCP_RESULT_BYTES", 8192)
                first = await client.call_tool("manage_tools", {**args, **target}, meta=meta)
                replay = await client.call_tool("manage_tools", {**args, **target}, meta=meta)
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
            page = await client.call_tool(
                "read_response", {"response_id": handle["response_id"], **target}
            )
            retained = json.loads(base64.b64decode(page.structured_content["data"]["data_base64"]))
            assert retained["contents"] == [
                {"uri": args["uri"], "mimeType": "text/plain", "text": value}
            ]
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
