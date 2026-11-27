"""Connection tools read only resolved resources published to the same caller."""

import base64
from enum import Enum
import json

from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.middleware import Middleware
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import MCPError
import pytest

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

    @app.resource(uri)
    async def concrete(ctx: Context) -> ResourceResult:
        reads.append("concrete")
        return content(await ctx.get_state("unity_instance"))

    @app.resource(template_uri)
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

    @server.resource(prefix + "filtered")
    def filtered() -> str:
        reads.append("filtered")
        return "filtered"

    @server.resource(prefix + "disabled")
    def disabled() -> str:
        reads.append("disabled")
        return "disabled"

    @server.resource(prefix + "{part}")
    def first(part: str) -> str:
        reads.append(part)
        return part

    @server.resource(prefix + "{alternative}")
    def second(alternative: str) -> str:
        reads.append(alternative)
        return alternative

    @server.resource("skill://guide/SKILL.md")
    def guide() -> str:
        reads.append("skill")
        return "skill"

    @server.resource("file:///private.txt")
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

    @server.resource(uri)
    def detail() -> str:
        return "content"

    @server.resource(template, version="1")
    def older(choice: str) -> str:
        return "older"

    @server.resource(template, version="2")
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
async def test_advertised_read_resource_hints_cover_conditional_project_code(server):
    async with Client(server) as client:
        tool = next(t for t in await client.list_tools() if t.name == "manage_tools")
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is True
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True
        card = (
            await client.call_tool(
                "manage_tools",
                {"action": "describe", "tool": "manage_tools", "operation": "read_resource"},
            )
        ).structured_content["operation"]
        assert "evaluate_getters=true" in card["purpose"]
        assert card["effects"] == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_read_drafts_and_calls_accept_the_same_selector_inputs(server, mode):
    uri = "skill://guide/SKILL.md"
    reads = []

    @server.resource(uri)
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
    ("request_can_run", "plugin_can_run"), [(True, False), (False, True), (True, True)]
)
async def test_getter_read_checks_requester_and_selected_plugin_before_handler(
    command_hub, monkeypatch, request_can_run, plugin_can_run
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

    @app.resource("mcpforunity://scene/gameobject/{object_id}/components{?evaluate_getters}")
    def observe(object_id: str, evaluate_getters: str = "false") -> str:
        reads.append(object_id)
        return evaluate_getters

    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    grants = {
        key: {"unity_mcp_permissions": ["read", "edit", "run"] if allowed else ["read"]}
        for key, allowed in (("request", request_can_run), ("plugin", plugin_can_run))
    }
    async with permission_service(monkeypatch, grants) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            async with Client(app) as client:
                args = {
                    "action": "read_resource",
                    "uri": "mcpforunity://scene/gameobject/3/components?evaluate_getters=true",
                    "unity_instance": "Fixture@hash",
                }
                if request_can_run and plugin_can_run:
                    result = await client.call_tool("manage_tools", args)
                    assert result.structured_content["contents"][0]["text"] == "true"
                    assert reads == ["3"]
                else:
                    with pytest.raises(MCPError):
                        await client.call_tool("manage_tools", args)
                    assert not reads
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_expanded_resource_keeps_outer_receipt_and_retrievable_resource_result(
    command_hub, tmp_path, monkeypatch
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
    target = {"unity_instance": "Fixture@" + PROJECT}
    value = "payload" * 5000
    reads = []

    @app.resource("mcpforunity://scene/gameobject/{object_id}/components{?evaluate_getters}")
    def observe(object_id: str, evaluate_getters: str = "false") -> str:
        reads.append((object_id, evaluate_getters))
        return value

    try:
        async with Client(app, mode="2026-07-28", cache=False) as client:
            args = {
                "action": "read_resource",
                "uri": "mcpforunity://scene/gameobject/3/components?evaluate_getters=true",
            }
            ambiguous = await client.call_tool("manage_tools", args, raise_on_error=False)
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
            page = await client.call_tool(
                "read_response", {"response_id": handle["response_id"], **target}
            )
            retained = json.loads(base64.b64decode(page.structured_content["data"]["data_base64"]))
            assert retained["contents"] == [
                {"uri": args["uri"], "mimeType": "text/plain", "text": value}
            ]
            assert reads == [("3", "true")]
            scope = (await runtime.authority(PROJECT)).scope
            with pytest.raises(ReceiptNotFound):
                runtime.store.get(
                    scope,
                    store_id=runtime.store.store_id,
                    grants=frozenset({"read"}),
                    idempotency_key=KEY,
                )
    finally:
        runtime.close()
