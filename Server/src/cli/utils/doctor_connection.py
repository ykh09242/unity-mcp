"""One bounded public-SDK diagnostic connection, without desktop configuration discovery."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from importlib import metadata
import ipaddress
import json
import re
from typing import Final, Literal

import anyio
import httpx2
from pydantic import ValidationError

from cli.utils.config import CLIConfig
from cli.utils.connection import UnityConnectionError, _auth_headers, _server_url
from models.doctor import EXACT_TARGET_PATTERN, DoctorCLIReceipt, DoctorResourceResponse

_DOCTOR_URI: Final = "mcpforunity://doctor"
_TARGET: Final = re.compile(EXACT_TARGET_PATTERN)


class DiagnosticLimit(ValueError):
    """A constant-sized limit error that never contains rejected bytes."""


@dataclass(slots=True)
class _ByteBudget:
    total: int = 0


class _BoundedStream(httpx2.AsyncByteStream):
    def __init__(
        self, response: httpx2.Response, budget: _ByteBudget, response_limit: int, total_limit: int
    ):
        self.response = response
        self.budget = budget
        self.response_limit = response_limit
        self.total_limit = total_limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        response_bytes = 0
        async for chunk in self.response.aiter_bytes():
            response_bytes += len(chunk)
            self.budget.total += len(chunk)
            if response_bytes > self.response_limit or self.budget.total > self.total_limit:
                raise DiagnosticLimit()
            yield chunk

    async def aclose(self) -> None:
        await self.response.aclose()


class _BoundedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, base: httpx2.AsyncBaseTransport, factory: "DoctorHttpFactory"):
        self.base = base
        self.factory = factory

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        # SDK cancellation/termination may send again during cleanup; never reopen expired I/O.
        if self.factory.deadline is not None and anyio.current_time() >= self.factory.deadline:
            raise TimeoutError()
        remaining = self.factory.remaining()
        with anyio.fail_after(remaining):
            try:
                response = await self.base.handle_async_request(request)
            except httpx2.ConnectError:
                if self.factory.failure is None:
                    self.factory.failure = ("server_unreachable", 1, "start_selected_server")
                raise
            except httpx2.TimeoutException:
                if self.factory.failure is None:
                    self.factory.failure = ("deadline", 3, "retry_later")
                raise
        if response.status_code in (401, 403):
            self.factory.failure = ("auth_required", 1, "use_authenticated_client")
        headers = [
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in ("content-encoding", "content-length")
        ]
        return httpx2.Response(
            response.status_code,
            headers=headers,
            stream=_BoundedStream(
                response, self.factory.budget, self.factory.response_limit, self.factory.total_limit
            ),
            request=request,
        )

    async def aclose(self) -> None:
        await self.base.aclose()


class DoctorHttpFactory:
    """SDK-supported HTTP factory; redirects are refused even when the SDK requests them."""

    def __init__(
        self,
        response_limit: int = 1_048_576,
        total_limit: int = 4_194_304,
        deadline: float | None = None,
    ):
        self.response_limit = response_limit
        self.total_limit = total_limit
        self.deadline = deadline
        self.budget = _ByteBudget()
        self.failure: tuple[str, int, str] | None = None

    def remaining(self) -> float:
        return (
            max(0.001, self.deadline - anyio.current_time()) if self.deadline is not None else 15.0
        )

    def __call__(
        self,
        *,
        headers: dict[str, str] | None = None,
        auth: httpx2.Auth | None = None,
        timeout: httpx2.Timeout | None = None,
        follow_redirects: bool = True,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> httpx2.AsyncClient:
        # The SDK passes True. Intentionally ignore that input at this security boundary.
        del follow_redirects
        return httpx2.AsyncClient(
            headers=headers,
            auth=auth,
            timeout=timeout or httpx2.Timeout(15.0),
            follow_redirects=False,
            max_redirects=0,
            transport=_BoundedTransport(transport or httpx2.AsyncHTTPTransport(retries=0), self),
        )


class _SdkConnection:
    """A public MCP client/transport lifetime owned by the calling diagnostic task."""

    def __init__(
        self, config: CLIConfig, protocol: str, factory: DoctorHttpFactory, headers: dict[str, str]
    ):
        self.config = config
        self.protocol = protocol
        self.factory = factory
        self.headers = headers
        self._stack = None
        self._client = None
        self._connected = False

    async def __aenter__(self):
        from mcp.client import Client
        from mcp.client.streamable_http import streamable_http_client

        async with AsyncExitStack() as stack:
            # LIFO: close the MCP session and transport task groups before caller-owned HTTP.
            http = await stack.enter_async_context(
                self.factory(headers=self.headers, timeout=httpx2.Timeout(self.factory.remaining()))
            )
            transport = streamable_http_client(
                _server_url(self.config, "/mcp"), http_client=http, max_sse_event_size=1_048_576
            )
            client = await stack.enter_async_context(
                Client(
                    transport,
                    mode=self.protocol,
                    cache=None,
                    read_timeout_seconds=self.factory.remaining(),
                )
            )
            self._client = client
            self._connected = True
            self._stack = stack.pop_all()
            return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            if self._stack is not None:
                return await self._stack.__aexit__(exc_type, exc_value, traceback)
        finally:
            self._connected = False
            self._client = None
            self._stack = None

    @property
    def protocol_version(self) -> str | None:
        return self._client.protocol_version if self._client is not None else None

    def is_connected(self) -> bool:
        return self._connected

    async def list_resources_mcp(self, *, cursor=None, cache_mode="bypass"):
        return await self._client.list_resources(cursor=cursor, cache_mode=cache_mode)

    async def read_resource(self, uri, *, meta=None):
        from mcp_types import RequestParamsMeta

        result = await self._client.read_resource(
            uri, meta=RequestParamsMeta(**meta) if meta is not None else None, cache_mode="bypass"
        )
        return result.contents


def _make_client(
    config: CLIConfig, protocol: str, factory: DoctorHttpFactory, headers: dict[str, str]
):
    return _SdkConnection(config, protocol, factory, headers)


def _endpoint(config: CLIConfig) -> str | None:
    host = config.host.strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
            return None
    return _server_url(config, "/mcp")


def _failure(
    config: CLIConfig,
    protocol: Literal["auto", "legacy"],
    reason,
    exit_code: Literal[1, 2, 3, 4],
    next_action,
    *,
    observed_protocol: str | None = None,
    discovery: str = "unknown",
) -> DoctorCLIReceipt:
    target = config.unity_instance
    safe_target = target if isinstance(target, str) and _TARGET.fullmatch(target) else None
    return DoctorCLIReceipt(
        endpoint=_endpoint(config),
        requested_target=safe_target,
        requested_protocol=protocol,
        observed_protocol=observed_protocol,
        overall="failed" if exit_code in (1, 2, 4) else "incomplete",
        exit_code=exit_code,
        reason=reason,
        next_action=next_action,
        stages={
            "dependency": "failed" if exit_code == 4 else "passed",
            "discovery": discovery,
            "native_read": "not_checked",
        },
    )


def _cause(error: BaseException) -> tuple[str, int, str]:
    """Classify captured exception types/status only, never their potentially secret text."""
    pending = [error]
    visited = set()
    for _ in range(64):
        if not pending:
            break
        item = pending.pop()
        if id(item) in visited:
            continue
        visited.add(id(item))
        if isinstance(item, DiagnosticLimit):
            return "diagnostic_limit", 3, "retry_later"
        if isinstance(item, (TimeoutError, httpx2.TimeoutException)):
            return "deadline", 3, "retry_later"
        if isinstance(item, httpx2.ConnectError):
            return "server_unreachable", 1, "start_selected_server"
        if isinstance(item, httpx2.HTTPStatusError):
            if item.response.status_code in (401, 403):
                return "auth_required", 1, "use_authenticated_client"
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions[:16])
        if item.__cause__ is not None:
            pending.append(item.__cause__)
        elif item.__context__ is not None:
            pending.append(item.__context__)
    return "source_unavailable", 3, "retry_later"


def _check_json_limits(raw) -> None:
    pending = [(raw, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 512 or depth > 12:
            raise DiagnosticLimit()
        if isinstance(item, dict):
            pending.extend((value, depth + 1) for value in item.values())
        elif isinstance(item, list):
            pending.extend((value, depth + 1) for value in item)


async def _target_failure(
    client, config: CLIConfig, protocol: Literal["auto", "legacy"]
) -> DoctorCLIReceipt:
    from mcp.shared.exceptions import MCPError

    failure = _failure(
        config,
        protocol,
        "source_unavailable",
        3,
        "retry_later",
        observed_protocol=client.protocol_version,
        discovery="passed",
    )
    try:
        blocks = await client.read_resource("mcpforunity://instances")
        if len(blocks) != 1 or not hasattr(blocks[0], "text"):
            return failure
        text = blocks[0].text
        if len(text.encode("utf-8")) > 32768:
            raise DiagnosticLimit()
        raw = json.loads(text)
        _check_json_limits(raw)
        if not isinstance(raw, dict) or raw.get("success") is not True:
            return failure
        instances = raw.get("instances")
        if not isinstance(instances, list) or len(instances) > 512:
            return failure
        if not instances:
            return _failure(
                config,
                protocol,
                "unity_unavailable",
                1,
                "inspect_editor",
                observed_protocol=client.protocol_version,
                discovery="passed",
            )
        if all(
            isinstance(item, dict) and isinstance(item.get("id"), str) for item in instances
        ) and not any(item["id"] == config.unity_instance for item in instances):
            return _failure(
                config,
                protocol,
                "target_unavailable",
                1,
                "select_exact_instance",
                observed_protocol=client.protocol_version,
                discovery="passed",
            )
    except (MCPError, ValueError, RecursionError):
        return failure
    return failure


async def _read(client, config: CLIConfig, protocol: Literal["auto", "legacy"]) -> DoctorCLIReceipt:
    seen = set()
    cursor = None
    entries = 0
    found = False
    for _ in range(4):
        catalog = await client.list_resources_mcp(cursor=cursor, cache_mode="bypass")
        entries += len(catalog.resources)
        if entries > 512:
            return _failure(
                config,
                protocol,
                "discovery_incomplete",
                3,
                "update_selected_server",
                observed_protocol=client.protocol_version,
            )
        if any(str(item.uri) == _DOCTOR_URI for item in catalog.resources):
            found = True
            break
        cursor = catalog.next_cursor
        if cursor is None:
            return _failure(
                config,
                protocol,
                "doctor_unavailable",
                3,
                "update_selected_server",
                observed_protocol=client.protocol_version,
                discovery="passed",
            )
        if cursor in seen:
            break
        seen.add(cursor)
    if not found:
        return _failure(
            config,
            protocol,
            "discovery_incomplete",
            3,
            "retry_later",
            observed_protocol=client.protocol_version,
        )
    from mcp.shared.exceptions import MCPError

    try:
        blocks = await client.read_resource(
            _DOCTOR_URI, meta={"unity_instance": config.unity_instance}
        )
    except MCPError:
        return await _target_failure(client, config, protocol)
    if len(blocks) != 1 or not hasattr(blocks[0], "text"):
        return _failure(
            config,
            protocol,
            "invalid_receipt",
            3,
            "update_selected_server",
            observed_protocol=client.protocol_version,
            discovery="passed",
        )
    text = blocks[0].text
    if len(text.encode("utf-8")) > 32768:
        raise DiagnosticLimit()
    raw = json.loads(text)
    _check_json_limits(raw)
    receipt = DoctorResourceResponse.model_validate(raw).data
    expected_checks = {"target", "native_read", "source", "readiness"}
    source = receipt.checks.get("source")
    consistent_source = source is not None and (
        (
            receipt.source_comparison == "matched"
            and source.status == "passed"
            and source.reason == "observed"
        )
        or (
            receipt.source_comparison == "mismatch"
            and source.status == "failed"
            and source.reason == "source_mismatch"
            and receipt.server.source_commit is not None
            and receipt.package.effective_server_commit is not None
            and receipt.server.source_commit != receipt.package.effective_server_commit
        )
        or (receipt.source_comparison == "unknown" and source.status == "unknown")
    )
    if (
        not consistent_source
        or set(receipt.checks) != expected_checks
        or (
            receipt.connection_verified
            and (
                receipt.target.instance_id != config.unity_instance
                or receipt.target.generation is None
                or receipt.checks["target"].status != "passed"
            )
        )
        or (receipt.connection_verified and receipt.checks["native_read"].status != "passed")
        or (
            receipt.source_comparison == "matched"
            and (
                receipt.server.source_commit is None
                or receipt.server.source_commit != receipt.package.effective_server_commit
            )
        )
    ):
        return _failure(
            config,
            protocol,
            "invalid_receipt",
            3,
            "update_selected_server",
            observed_protocol=client.protocol_version,
            discovery="passed",
        )
    if receipt.target.instance_id and receipt.target.instance_id != config.unity_instance:
        return _failure(
            config,
            protocol,
            "wrong_target",
            1,
            "select_exact_instance",
            observed_protocol=client.protocol_version,
            discovery="passed",
        )
    failed = next((check for check in receipt.checks.values() if check.status == "failed"), None)
    if failed is not None:
        reason = failed.reason
        action = "restart_client_server" if reason == "source_mismatch" else "inspect_editor"
        overall, code = "failed", 1
    elif not receipt.connection_verified:
        reason, action, overall, code = "source_unavailable", "retry_later", "incomplete", 3
    elif receipt.source_comparison == "unknown":
        reason, action, overall, code = (
            "comparison_unknown",
            "verify_selected_source",
            "incomplete",
            3,
        )
    elif receipt.source_comparison == "matched" and receipt.checks["source"].status == "passed":
        reason, action, overall, code = "verified", "none", "verified", 0
    else:
        return _failure(
            config, protocol, "invalid_receipt", 3, "update_selected_server", discovery="passed"
        )
    return DoctorCLIReceipt(
        endpoint=_endpoint(config),
        requested_target=config.unity_instance,
        requested_protocol=protocol,
        observed_protocol=client.protocol_version,
        connection_verified=receipt.connection_verified,
        overall=overall,
        exit_code=code,
        reason=reason,
        next_action=action,
        stages={
            "dependency": "passed",
            "discovery": "passed",
            "native_read": "passed" if receipt.connection_verified else "unknown",
        },
        data=receipt,
    )


async def diagnose(
    config: CLIConfig, protocol: Literal["auto", "legacy"] = "auto"
) -> DoctorCLIReceipt:
    """Diagnose only this fresh connection, using one deadline and no client config search."""
    if not config.unity_instance or not _TARGET.fullmatch(config.unity_instance):
        return _failure(config, protocol, "invalid_target", 2, "select_exact_instance")
    if _endpoint(config) is None:
        return _failure(config, protocol, "invalid_endpoint", 2, "start_selected_server")
    deadline = anyio.current_time() + min(config.timeout, 15)
    factory = DoctorHttpFactory(deadline=deadline)
    try:
        for package in ("fastmcp", "mcp"):
            metadata.version(package)
        with anyio.fail_after(max(0.001, deadline - anyio.current_time())):
            headers = await anyio.to_thread.run_sync(_auth_headers, config, abandon_on_cancel=True)
        if anyio.current_time() >= deadline:
            return _failure(config, protocol, "deadline", 3, "retry_later")
        client = _make_client(config, protocol, factory, headers)
    except TimeoutError:
        return _failure(config, protocol, "deadline", 3, "retry_later").model_copy(
            update={"residual_auth_io": "unknown"}
        )
    except metadata.PackageNotFoundError:
        return _failure(config, protocol, "dependency_missing", 4, "install_declared_dependencies")
    except ModuleNotFoundError as error:
        reason = (
            "dependency_missing"
            if error.name in ("fastmcp", "mcp")
            else "internal_dependency_error"
        )
        return _failure(config, protocol, reason, 4, "install_declared_dependencies")
    except (UnityConnectionError, OSError, UnicodeError):
        return _failure(config, protocol, "auth_required", 1, "use_authenticated_client")
    try:
        with anyio.fail_after(max(0.001, deadline + 1 - anyio.current_time())):
            async with client:
                with anyio.fail_after(max(0.001, deadline - anyio.current_time())):
                    return await _read(client, config, protocol)
    except (ValidationError, ValueError, RecursionError) as error:
        reason = "diagnostic_limit" if isinstance(error, DiagnosticLimit) else "invalid_receipt"
        return _failure(config, protocol, reason, 3, "update_selected_server")
    except Exception as error:  # noqa: BROAD_EXCEPT_OK -- CLI boundary; only typed classifications escape.
        reason, code, action = factory.failure or _cause(error)
        return _failure(config, protocol, reason, code, action)
