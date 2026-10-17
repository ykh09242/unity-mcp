"""Inspect one actual SDK advertisement without invoking tools or deriving schemas."""

from contextlib import AsyncExitStack
import copy
import json
import math
from urllib.parse import quote

import anyio
import httpx2
from pydantic import ValidationError

from cli.utils.config import CLIConfig
from cli.utils.connection import UnityConnectionError, _auth_headers, _server_url
from cli.utils.doctor_connection import DoctorHttpFactory, DiagnosticLimit
from models.capabilities import ProjectCapabilitiesResponse
from models.tool_description import ToolAvailability, ToolDescriptionReceipt
from models.tool_diagnostics import ToolDiagnosticsResponse

_READ_TIMEOUT_SECONDS = 15.0
_CLEANUP_GRACE_SECONDS = 1.0
_MAX_PAGES = 8
_MAX_COMPONENTS = 512
_MESSAGE_BYTES = 1_048_576
_TOTAL_BYTES = 4_194_304
_SCHEMA_BYTES = 262_144
_JSON_DEPTH = 64
_JSON_NODES = 32_768
_RESOURCES = {
    "capabilities": "mcpforunity://capabilities",
    "custom_tools": "mcpforunity://custom-tools",
    "tool_groups": "mcpforunity://tool-groups",
    "tool_diagnostics": "mcpforunity://tool-diagnostics",
}
_DESCRIPTOR_FIELDS = {
    "name",
    "description",
    "schema_version",
    "input_schema",
    "output_data_schema",
    "parameters",
    "structured_output",
    "requires_polling",
    "poll_action",
    "max_poll_seconds",
}


def valid_tool_name(name: str) -> bool:
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 128
        and all(character.isalnum() or character in "_.-" for character in name)
    )


def _remaining(deadline):
    value = deadline - anyio.current_time()
    if value <= 0:
        raise TimeoutError()
    return value


def _checked_json(value, byte_limit=_MESSAGE_BYTES):
    pending = [(value, 0, False)]
    active = set()
    nodes = 0
    while pending:
        item, depth, leaving = pending.pop()
        if leaving:
            active.remove(id(item))
            continue
        nodes += 1
        if nodes > _JSON_NODES or depth > _JSON_DEPTH:
            raise DiagnosticLimit()
        if type(item) in (dict, list):
            if id(item) in active:
                raise DiagnosticLimit()
            active.add(id(item))
            pending.append((item, depth, True))
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise DiagnosticLimit()
                pending.extend((child, depth + 1, False) for child in item.values())
            else:
                pending.extend((child, depth + 1, False) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise DiagnosticLimit()
        elif isinstance(item, float) and not math.isfinite(item):
            raise DiagnosticLimit()
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > byte_limit:
            raise DiagnosticLimit()
    except (ValueError, UnicodeError, RecursionError) as error:
        raise DiagnosticLimit() from error
    return value


def _evidence(reason="source_unavailable", *, observed=False):
    return {
        "status": "observed" if observed else "unknown",
        "reason": reason,
        "next_action": "none" if observed else "retry_later",
    }


class _ToolClient:
    """Public SDK lifetime only; budget transport is reused from committed DEV09."""

    def __init__(self, config, protocol, factory, headers):
        self.config, self.protocol = config, protocol
        self.factory, self.headers = factory, headers
        self.stack = None
        self.client = None

    async def __aenter__(self):
        from mcp.client import Client
        from mcp.client.streamable_http import streamable_http_client

        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(
                self.factory(headers=self.headers, timeout=httpx2.Timeout(self.factory.remaining()))
            )
            transport = streamable_http_client(
                _server_url(self.config, "/mcp"),
                http_client=http,
                max_sse_event_size=_MESSAGE_BYTES,
            )
            self.client = await stack.enter_async_context(
                Client(
                    transport,
                    mode=self.protocol,
                    cache=None,
                    read_timeout_seconds=self.factory.remaining(),
                )
            )
            self.stack = stack.pop_all()
            return self

    async def __aexit__(self, *args):
        try:
            if self.stack is not None:
                return await self.stack.__aexit__(*args)
        finally:
            self.stack = self.client = None

    @property
    def protocol_version(self):
        return self.client.protocol_version

    async def list_tools(self, *, cursor=None, meta=None):
        from mcp_types import RequestParamsMeta

        return await self.client.list_tools(
            cursor=cursor, meta=RequestParamsMeta(**meta) if meta else None, cache_mode="bypass"
        )

    async def list_resources(self, *, cursor=None, meta=None):
        from mcp_types import RequestParamsMeta

        return await self.client.list_resources(
            cursor=cursor, meta=RequestParamsMeta(**meta) if meta else None, cache_mode="bypass"
        )

    async def read_resource(self, uri, *, meta=None):
        from mcp_types import RequestParamsMeta

        return await self.client.read_resource(
            uri, meta=RequestParamsMeta(**meta) if meta else None, cache_mode="bypass"
        )


def _make_client(config, protocol, factory, headers):
    return _ToolClient(config, protocol, factory, headers)


async def _listing(client, name, meta, deadline, state):
    cursor, seen = None, set()
    total = 0
    fallback = False
    for _ in range(_MAX_PAGES):
        _remaining(deadline)
        page = await client.list_tools(cursor=cursor, meta=meta)
        total += len(page.tools)
        if total > _MAX_COMPONENTS:
            break
        for tool in page.tools:
            fallback |= tool.name == "execute_custom_tool"
            if tool.name != name:
                continue
            state["advertised"] = True
            raw = tool.model_dump(by_alias=True, exclude_none=True)
            _checked_json(
                {key: raw.get(key) for key in ("inputSchema", "outputSchema")}, _SCHEMA_BYTES
            )
            _checked_json(raw)
            safe = {
                key: value
                for key, value in raw.items()
                if key
                in {"name", "title", "description", "inputSchema", "outputSchema", "annotations"}
            }
            if state["tool"] is not None and state["tool"] != safe:
                state["tool"], state["advertised"] = None, None
                break
            state["tool"] = safe
        else:
            cursor = page.next_cursor
            if cursor is None:
                state["listing_complete"] = True
                state["advertised"] = state["advertised"] is True
                state["fallback_advertised"] = fallback
                return
            if isinstance(cursor, str) and 0 < len(cursor) <= 1024 and cursor not in seen:
                seen.add(cursor)
                continue
        break
    state["fallback_advertised"] = True if fallback else None
    state["failure"] = "listing_incomplete"


async def _resource_uris(client, meta, deadline):
    cursor, seen, uris = None, set(), set()
    total = 0
    for _ in range(_MAX_PAGES):
        _remaining(deadline)
        page = await client.list_resources(cursor=cursor, meta=meta)
        total += len(page.resources)
        if total > _MAX_COMPONENTS:
            raise DiagnosticLimit()
        uris.update(
            str(resource.uri)
            for resource in page.resources
            if str(resource.uri) in _RESOURCES.values()
        )
        cursor = page.next_cursor
        if cursor is None:
            return uris
        if not isinstance(cursor, str) or not 0 < len(cursor) <= 1024 or cursor in seen:
            break
        seen.add(cursor)
    raise DiagnosticLimit()


async def _read_project(client, meta, deadline, state, name):
    uris = await _resource_uris(client, meta, deadline)

    async def read(key, uri, support_uri=None):
        if (support_uri or uri) not in uris:
            return
        try:
            _remaining(deadline)
            response = await client.read_resource(uri, meta=meta)
            if len(response.contents) != 1 or not hasattr(response.contents[0], "text"):
                return
            text = response.contents[0].text
            if len(text.encode("utf-8")) > _MESSAGE_BYTES:
                raise DiagnosticLimit()
            raw = _checked_json(json.loads(text))
            if not isinstance(raw, dict) or raw.get("success") is False:
                return
            state["reads"][key] = raw
            state["evidence"][key] = _evidence("observed", observed=True)
        except Exception:  # BOUNDARY: optional source failures never echo remote bodies/errors.
            state["evidence"][key] = _evidence()

    async with anyio.create_task_group() as group:
        for key, uri in _RESOURCES.items():
            if key == "tool_diagnostics":
                group.start_soon(read, key, uri + "/" + quote(name, safe=""), uri)
            else:
                group.start_soon(read, key, uri)


def _project_fields(name, requested, state):
    availability = ToolAvailability()
    verified = None
    cap = None

    def discard_project(reason):
        for key in _RESOURCES:
            state["evidence"][key] = _evidence(reason)
        return None, availability, False, []

    raw_cap = state["reads"].get("capabilities")
    if raw_cap is not None:
        try:
            cap = ProjectCapabilitiesResponse.model_validate(raw_cap, strict=True).data
            target = cap.connection.instance_id
            matches = target is not None and requested in (target, target.rsplit("@", 1)[-1])
            if target is not None and (not matches or cap.target.instance_id != target):
                return discard_project("wrong_target")
            if (
                matches
                and cap.target.instance_id == target
                and cap.connection.status == "connected"
                and cap.connection.generation is not None
            ):
                verified = True
                availability = ToolAvailability(
                    packages=cap.packages,
                    features={
                        key: value.model_dump(mode="json") for key, value in cap.features.items()
                    },
                    editor_readiness=cap.readiness.model_dump(mode="json"),
                )
            else:
                return discard_project("connection_unavailable")
        except (ValidationError, ValueError):
            state["evidence"]["capabilities"] = _evidence("invalid_source_response")
    diagnostic_raw = state["reads"].get("tool_diagnostics")
    if diagnostic_raw is not None:
        try:
            diagnostic = ToolDiagnosticsResponse.model_validate(diagnostic_raw).data
            owner = diagnostic.connection
            if not (
                verified is True
                and cap is not None
                and owner.status == "connected"
                and owner.instance_id == cap.connection.instance_id
                and owner.project_hash == cap.connection.project_hash
                and owner.generation == cap.connection.generation
            ):
                state["evidence"]["tool_diagnostics"] = _evidence("wrong_target")
            else:
                winners = [
                    row
                    for row in diagnostic.native.rows
                    if row.tool_name == name
                    and row.native_enabled is not None
                    and row.state in ("discovered", "disabled")
                ]
                rows = [
                    row
                    for attempt in diagnostic.server
                    for row in attempt.rows
                    if row.tool_name == name
                ]
                if diagnostic.native.complete and len(winners) == 1:
                    availability = availability.model_copy(
                        update={"native_enabled": winners[0].native_enabled}
                    )
                    rows = winners
                elif diagnostic.native.complete:
                    rejected = [
                        row
                        for row in diagnostic.native.rows
                        if row.tool_name == name
                        and row.state == "rejected"
                        and row.reason
                        in ("unsupported_handler", "metadata_invalid", "schema_or_default_invalid")
                    ]
                    if rejected:
                        rows = rejected
                if rows:
                    row = rows[0]
                    state["evidence"]["tool_diagnostics"] = {
                        "status": "observed",
                        "reason": row.reason,
                        "stage": row.stage,
                        "state": row.state,
                        "next_action": row.next_action,
                    }
                else:
                    state["evidence"]["tool_diagnostics"] = _evidence("not_observed")
        except (ValidationError, ValueError):
            state["evidence"]["tool_diagnostics"] = _evidence("invalid_source_response")
    descriptor = None
    raw = state["reads"].get("custom_tools", {}).get("data")
    tools = raw.get("tools") if isinstance(raw, dict) else None
    if (
        isinstance(tools, list)
        and len(tools) <= 256
        and all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in tools)
        and len({item["name"] for item in tools}) == len(tools)
    ):
        matches = [item for item in tools if isinstance(item, dict) and item.get("name") == name]
        if len(matches) == 1:
            descriptor = {
                key: value for key, value in matches[0].items() if key in _DESCRIPTOR_FIELDS
            }
            _checked_json(descriptor, _SCHEMA_BYTES)
    elif "custom_tools" in state["reads"]:
        state["evidence"]["custom_tools"] = _evidence("invalid_source_response")
    groups = []
    raw_groups = state["reads"].get("tool_groups", {}).get("groups")
    if (
        isinstance(raw_groups, list)
        and len(raw_groups) <= 32
        and all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and len(item["name"]) <= 64
            and isinstance(item.get("tools"), list)
            and len(item["tools"]) <= _MAX_COMPONENTS
            and all(isinstance(value, str) for value in item["tools"])
            for item in raw_groups
        )
    ):
        groups = [
            item["name"]
            for item in raw_groups
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and len(item["name"]) <= 64
            and isinstance(item.get("tools"), list)
            and name in item["tools"]
        ]
    elif "tool_groups" in state["reads"]:
        state["evidence"]["tool_groups"] = _evidence("invalid_source_response")
    return descriptor, availability, verified, groups


def _error_reason(error):
    pending = [error]
    for _ in range(64):
        if not pending:
            break
        item = pending.pop()
        if isinstance(item, DiagnosticLimit):
            return "inspection_limit"
        if isinstance(item, (TimeoutError, httpx2.TimeoutException)):
            return "deadline"
        if isinstance(item, httpx2.ConnectError):
            return "server_unreachable"
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions[:16])
        if item.__cause__ is not None:
            pending.append(item.__cause__)
    return "source_unavailable"


async def inspect_tool(config: CLIConfig, name: str, *, protocol="auto") -> ToolDescriptionReceipt:
    """Describe this fresh CLI connection; never validate or execute a tool schema."""
    if not valid_tool_name(name):
        return ToolDescriptionReceipt(name="", reason="invalid_name", exit_code=2)
    if config.unity_instance is not None and (
        not isinstance(config.unity_instance, str)
        or not 0 < len(config.unity_instance) <= 256
        or not config.unity_instance.isprintable()
    ):
        return ToolDescriptionReceipt(name=name, reason="invalid_target", exit_code=2)
    timeout = min(float(config.timeout), _READ_TIMEOUT_SECONDS)
    deadline = anyio.current_time() + timeout
    factory = DoctorHttpFactory(
        response_limit=_MESSAGE_BYTES, total_limit=_TOTAL_BYTES, deadline=deadline
    )
    state = {
        "advertised": None,
        "tool": None,
        "listing_complete": False,
        "fallback_advertised": None,
        "failure": None,
        "reads": {},
        "evidence": {
            key: _evidence("not_checked" if config.unity_instance is None else "source_unavailable")
            for key in _RESOURCES
        },
    }
    observed_protocol, residual_auth = None, "none"
    maximum_scope, client_entered = None, False
    try:
        with anyio.fail_after(_remaining(deadline)):
            headers = await anyio.to_thread.run_sync(_auth_headers, config, abandon_on_cancel=True)
    except TimeoutError:
        state["failure"], residual_auth = "deadline", "unknown"
    except (UnityConnectionError, OSError, UnicodeError):
        state["failure"] = "auth_required"
    else:
        try:
            _remaining(deadline)
            client = _make_client(config, protocol, factory, headers)
            with anyio.fail_after(_remaining(deadline) + _CLEANUP_GRACE_SECONDS) as maximum_scope:
                async with AsyncExitStack() as stack:
                    try:
                        with anyio.fail_after(_remaining(deadline)):
                            client = await stack.enter_async_context(client)
                            client_entered = True
                            observed_protocol = client.protocol_version
                            meta = (
                                {"unity_instance": config.unity_instance}
                                if config.unity_instance
                                else None
                            )
                            await _listing(client, name, meta, deadline, state)
                            if config.unity_instance:
                                await _read_project(client, meta, deadline, state, name)
                    finally:
                        # Keep the scope outside SDK contexts and never lend unused read time.
                        maximum_scope.deadline = min(
                            maximum_scope.deadline,
                            anyio.current_time() + _CLEANUP_GRACE_SECONDS,
                        )
        except (
            Exception
        ) as error:  # BOUNDARY: stable receipt codes, never SDK/remote exception text.
            failure_reason, *_ = factory.failure or (_error_reason(error),)
            state["failure"] = failure_reason
    transport_reason, *_ = factory.failure or (None,)
    if transport_reason == "auth_required":
        state["failure"] = "auth_required"
    if not state["listing_complete"]:
        # Later pages may conflict with an early match; only a whole scan is authoritative.
        state["tool"], state["advertised"] = None, None
    cleanup_expired = maximum_scope is not None and maximum_scope.cancel_called
    if cleanup_expired:
        state["evidence"]["cleanup"] = _evidence("cleanup_incomplete")
    descriptor, availability, verified, groups = None, ToolAvailability(), None, []
    try:
        if config.unity_instance:
            descriptor, availability, verified, groups = _project_fields(
                name, config.unity_instance, state
            )
    except DiagnosticLimit:
        state["failure"] = "inspection_limit"
    tool = state["tool"]
    rich_declared = (
        descriptor is not None
        and type(descriptor.get("schema_version")) is int
        and descriptor["schema_version"] == 1
        and isinstance(descriptor.get("input_schema"), dict)
    )
    source = (
        "sdk_tools_list"
        if tool is not None
        else "project_declared_only"
        if rich_declared
        else "unavailable"
    )
    reason = (
        "advertised"
        if tool is not None
        else "project_declared_only"
        if rich_declared
        else state["failure"] or "not_advertised"
    )
    code = 0 if tool is not None else 3
    if state["failure"] in ("auth_required", "server_unreachable"):
        reason, code = state["failure"], 1
    partial = not state["listing_complete"] or any(
        item["status"] != "observed" for item in state["evidence"].values()
    )
    return ToolDescriptionReceipt(
        name=name,
        requested_instance=config.unity_instance,
        scope="selected_project" if config.unity_instance else "sdk_session",
        observed_protocol=observed_protocol,
        advertised=state["advertised"],
        tool=copy.deepcopy(tool),
        project_descriptor=copy.deepcopy(descriptor),
        schema_source=source,
        fallback_advertised=state["fallback_advertised"],
        selection_verified=verified,
        availability=availability,
        groups=groups,
        evidence=state["evidence"],
        partial=partial,
        reason=reason,
        next_action="none"
        if code == 0
        else "use_authenticated_client"
        if reason == "auth_required"
        else "inspect_tool_availability",
        exit_code=code,
        residual_auth_io=residual_auth,
        cleanup_complete=False if cleanup_expired else True if client_entered else None,
        limits={
            "read_timeout_seconds": timeout,
            "cleanup_grace_seconds": _CLEANUP_GRACE_SECONDS,
            "max_pages": _MAX_PAGES,
            "max_tools": _MAX_COMPONENTS,
            "message_bytes": _MESSAGE_BYTES,
            "total_bytes": _TOTAL_BYTES,
            "schema_bytes": _SCHEMA_BYTES,
            "json_depth": _JSON_DEPTH,
            "json_nodes": _JSON_NODES,
        },
    )
