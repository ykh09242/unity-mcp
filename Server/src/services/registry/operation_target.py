"""Capture bounded invocation fences without retaining requests or credentials."""

from dataclasses import dataclass, field
from collections.abc import Callable, Awaitable

from core.config import config
from services.registry.operation_scheduler import OperationScope
from transport.native_arbitration import CAPABILITY, valid_generation
from services.operation_permissions import JsonValue


@dataclass(frozen=True, slots=True)
class OperationTarget:
    scope: OperationScope
    is_current: Callable[[], bool] = field(repr=False, compare=False)
    negotiated: bool = False
    authorize_public: Callable[[str, dict[str, JsonValue]], Awaitable[None]] | None = field(
        default=None, repr=False, compare=False
    )
    transport_generation: str | None = None
    public_available: Callable[[tuple[str, ...], object | None], Awaitable[bool]] | None = field(
        default=None, repr=False, compare=False
    )
    capacity_error: Callable[[], dict[str, JsonValue] | None] | None = field(
        default=None, repr=False, compare=False
    )


async def capture_target(
    instance: str | None, *, user_id: str | None = None
) -> OperationTarget | None:
    """Pin a resolved instance's actual socket generation before waiting."""
    if (config.transport_mode or "stdio").lower() == "http":
        from transport.plugin_hub import PluginHub, NoUnitySessionError

        if PluginHub._registry is None:
            return None
        try:
            session_id = await PluginHub._resolve_session_id(
                instance, user_id=user_id, retry_on_reload=False
            )
        except NoUnitySessionError:
            return None
        return await capture_http_target(session_id)
    from transport.legacy import unity_connection as legacy
    from services.execution_receipt_context import owned_to_thread

    try:
        connection = await owned_to_thread(legacy.get_unity_connection, instance)
    except (ConnectionError, RuntimeError):
        return None
    return capture_stdio_target(connection)


def capture_stdio_target(connection) -> OperationTarget:
    from transport.legacy import unity_connection as legacy

    pool = legacy._unity_connection_pool
    instance = connection.instance_id or "stdio:" + str(connection.port)
    generation = connection.session_generation if connection is not None else None
    native = (
        connection.authenticated_session.arbitration_generation
        if connection is not None and connection.authenticated_session is not None
        else None
    )
    scope = OperationScope(
        "local",
        instance.rsplit("@", 1)[-1].lower(),
        (generation or "unconnected") + ":" + (native or "legacy"),
    )

    def current() -> bool:
        return (
            legacy._unity_connection_pool is pool
            and (pool is None or pool._connections.get(instance) is connection)
            and (
                connection is None
                or not connection._retired
                and connection.session_generation == generation
                and (
                    connection.authenticated_session.arbitration_generation
                    if connection.authenticated_session is not None
                    else None
                )
                == native
            )
        )

    session = connection.authenticated_session if connection is not None else None
    negotiated = (
        session is not None and CAPABILITY in session.capabilities and valid_generation(native)
    )

    async def available(names: tuple[str, ...], server: object | None) -> bool:
        from services.custom_tool_service import CustomToolService

        service = CustomToolService._instance
        if service is None or service._mcp is not server:
            return True
        catalog = service.get_stdio_availability(scope.instance, generation)
        return catalog is None or any(name in catalog for name in names)

    return OperationTarget(
        scope, current, negotiated, transport_generation=generation, public_available=available
    )


async def capture_http_target(session_id: str, *, hub=None) -> OperationTarget | None:
    from transport.plugin_hub import PluginHub

    hub = hub or PluginHub
    registry = hub._registry
    if registry is None:
        return None
    session = await registry.get_session(session_id)
    socket = hub._connections.get(session_id)
    if session is None or socket is None:
        return None
    generation = getattr(socket.state, "plugin_generation", None)
    native = getattr(socket.state, "plugin_arbitration_generation", None)
    scope = OperationScope(
        "remote:" + session.user_id if session.user_id is not None else "local",
        session.project_hash,
        (generation or "unregistered") + ":" + (native or "legacy"),
    )

    def current() -> bool:
        return (
            hub._registry is registry
            and hub._connections.get(session_id) is socket
            and getattr(socket.state, "plugin_generation", None) == generation
            and getattr(socket.state, "plugin_arbitration_generation", None) == native
            and getattr(socket.state, "user_id", None) == session.user_id
        )

    async def authorize(name: str, params: dict[str, JsonValue]) -> None:
        from services.credential_lease import require_credential_lease, CredentialLeaseError
        from services.operation_permissions import authorize_operation

        requester = await require_credential_lease()
        authority = hub._socket_lease(socket)
        if authority is None or authority.user_id != requester.user_id:
            raise CredentialLeaseError()
        await authority.check()
        authorize_operation(authority.permissions, name, params)

    async def available(names: tuple[str, ...], _server: object | None) -> bool:
        fresh = await registry.get_session(session_id)
        return (
            current()
            and fresh is session
            and (not fresh.tools_known or any(name in fresh.tools for name in names))
        )

    return OperationTarget(
        scope,
        current,
        hub._supports(socket, CAPABILITY) and valid_generation(native),
        authorize if config.http_remote_hosted else None,
        generation,
        available,
        lambda: hub._pending_capacity_error(session_id, session.user_id),
    )
