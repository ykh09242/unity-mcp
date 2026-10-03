import asyncio
import functools
import inspect
import json
import keyword
import logging
import time
from hashlib import sha256
from threading import Lock
from typing import Annotated, Optional

from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import config
from models.models import MCPResponse, ToolDefinitionModel, ToolParameterModel
from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import (
    async_send_command_with_retry,
    get_unity_connection_pool,
)
from transport.plugin_hub import PluginHub
from services.tools import get_unity_instance_from_context
from services.registry import get_registered_tools

logger = logging.getLogger("mcp-for-unity-server")

_DEFAULT_POLL_INTERVAL = 1.0
_MAX_POLL_SECONDS = 600
_MAX_ACTIVE_POLLS_PER_SESSION = 16
_MAX_ACTIVE_POLLS_PER_USER = 32
_MAX_ACTIVE_POLLS_GLOBAL = 256


async def get_user_id_from_context(ctx: Context) -> str | None:
    """Read user_id from request-scoped context in remote-hosted mode."""
    if not config.http_remote_hosted:
        return None

    get_state = getattr(ctx, "get_state", None)
    if not callable(get_state):
        return None

    try:
        user_id = await get_state("user_id")
    except Exception:
        return None

    return user_id if isinstance(user_id, str) and user_id else None


class RegisterToolsPayload(BaseModel):
    project_id: str
    project_hash: str | None = None
    tools: list[ToolDefinitionModel]


class ToolRegistrationResponse(BaseModel):
    success: bool
    registered: list[str]
    replaced: list[str]
    message: str


class CustomToolService:
    _instance: "CustomToolService | None" = None

    def __init__(self, mcp: FastMCP, project_scoped_tools: bool = True):
        CustomToolService._instance = self
        self._mcp = mcp
        self._project_scoped_tools = project_scoped_tools
        self._project_tools: dict[str, dict[str, ToolDefinitionModel]] = {}
        self._hash_to_project: dict[str, str] = {}
        self._global_tools: dict[str, ToolDefinitionModel] = {}
        # Reservations include initial dispatch, sleeps and all subsequent polls.
        # The tenant/project key survives plugin reconnects and target name aliases.
        self._polling_lock = Lock()
        self._active_polls = 0
        self._polls_by_session: dict[tuple[str | None, str], int] = {}
        self._polls_by_user: dict[str | None, int] = {}
        self._register_http_routes()

    @classmethod
    def get_instance(cls) -> "CustomToolService":
        if cls._instance is None:
            raise RuntimeError("CustomToolService has not been initialized")
        return cls._instance

    # --- HTTP Routes -----------------------------------------------------
    def _register_http_routes(self) -> None:
        # The plugin registers custom tools over the hub WebSocket (register_tools message),
        # so this REST route only serves local tooling. A remote-hosted server must not expose
        # it: it carries no API-key check, so any caller could replace tool definitions for
        # every tenant. Mirrors the /api/command gate in main.py.
        if config.http_remote_hosted:
            return

        @self._mcp.custom_route("/register-tools", methods=["POST"])
        async def register_tools(request: Request) -> JSONResponse:
            try:
                payload = RegisterToolsPayload.model_validate(await request.json())
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JSONResponse({"success": False, "error": "Request body must be valid JSON"}, status_code=400)
            except ValidationError as exc:
                return JSONResponse({"success": False, "error": exc.errors()}, status_code=400)

            registered, replaced = self._register_project_tools(
                payload.project_id, payload.tools, project_hash=payload.project_hash)

            message = f"Registered {len(registered)} tool(s)"
            if replaced:
                message += f" (replaced: {', '.join(replaced)})"

            response = ToolRegistrationResponse(
                success=True,
                registered=registered,
                replaced=replaced,
                message=message,
            )
            return JSONResponse(response.model_dump())

    # --- Public API for MCP tools ---------------------------------------
    async def list_registered_tools(
        self,
        project_id: str,
        user_id: str | None = None,
    ) -> list[ToolDefinitionModel]:
        if config.http_remote_hosted:
            if not user_id:
                return []
            return await PluginHub.get_tools_for_project(project_id, user_id=user_id)
        legacy = list(self._project_tools.get(project_id, {}).values())
        hub_tools = await PluginHub.get_tools_for_project(project_id, user_id=user_id)
        return legacy + hub_tools

    async def get_tool_definition(
        self,
        project_id: str,
        tool_name: str,
        user_id: str | None = None,
    ) -> ToolDefinitionModel | None:
        if config.http_remote_hosted:
            if not user_id:
                return None
            return await PluginHub.get_tool_definition(project_id, tool_name, user_id=user_id)
        tool = self._project_tools.get(project_id, {}).get(tool_name)
        if tool:
            return tool
        tool = await PluginHub.get_tool_definition(project_id, tool_name, user_id=user_id)
        if tool:
            return tool
        return self._global_tools.get(tool_name)

    async def execute_tool(
        self,
        project_id: str,
        tool_name: str,
        unity_instance: str | None,
        params: dict[str, object] | None = None,
        user_id: str | None = None,
    ) -> MCPResponse:
        params = params or {}
        logger.info("Executing custom tool")
        if config.http_remote_hosted and not user_id:
            return MCPResponse(success=False, message="Authenticated user required for custom tools")

        definition = await self.get_tool_definition(project_id, tool_name, user_id=user_id)
        if definition is None:
            return MCPResponse(
                success=False,
                message=f"Tool '{tool_name}' not found for project {project_id}",
            )

        if not definition.requires_polling:
            response = await send_with_unity_instance(
                async_send_command_with_retry, unity_instance, tool_name, params, user_id=user_id,
            )
            result = self._normalize_response(response)
            logger.info("Custom tool completed (success=%s, polled=False)", result.success)
            return result

        if not unity_instance:
            return MCPResponse(success=False, message="Explicit Unity instance required for custom tool polling")
        target = unity_instance.rsplit("@", 1)[-1].lower()
        session_key = (user_id, target)
        if not self._reserve_polling(session_key):
            return MCPResponse(
                success=False,
                message="Custom tool polling capacity reached; please retry after active work completes",
                hint="retry",
            )

        try:
            timeout = self._bounded_poll_seconds(definition.max_poll_seconds)
            deadline = time.monotonic() + timeout
            try:
                response = await asyncio.wait_for(
                    send_with_unity_instance(
                        async_send_command_with_retry, unity_instance, tool_name, params, user_id=user_id,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return self._poll_timeout(tool_name, None)
            result = await self._poll_until_complete(
                tool_name, unity_instance, params, response, definition.poll_action or "status",
                user_id=user_id, max_poll_seconds=timeout, deadline=deadline,
            )
            logger.info("Custom tool completed (success=%s, polled=True)", result.success)
            return result
        finally:
            self._release_polling(session_key)

    # --- Internal helpers ------------------------------------------------
    @staticmethod
    def _bounded_poll_seconds(seconds: int) -> int:
        """Clamp again for definitions constructed or mutated without validation."""
        return min(seconds, _MAX_POLL_SECONDS) if seconds > 0 else _MAX_POLL_SECONDS

    def _reserve_polling(self, session_key: tuple[str | None, str]) -> bool:
        """Reject excess work immediately; do not accumulate admission waiters."""
        user_id = session_key[0]
        with self._polling_lock:
            session_count = self._polls_by_session.get(session_key, 0)
            user_count = self._polls_by_user.get(user_id, 0)
            if (self._active_polls >= _MAX_ACTIVE_POLLS_GLOBAL
                    or session_count >= _MAX_ACTIVE_POLLS_PER_SESSION
                    or user_count >= _MAX_ACTIVE_POLLS_PER_USER):
                return False
            self._active_polls += 1
            self._polls_by_session[session_key] = session_count + 1
            self._polls_by_user[user_id] = user_count + 1
            return True

    def _release_polling(self, session_key: tuple[str | None, str]) -> None:
        """Release once in execute_tool's finally, including cancellation/error."""
        user_id = session_key[0]
        with self._polling_lock:
            self._active_polls -= 1
            session_count = self._polls_by_session.pop(session_key) - 1
            user_count = self._polls_by_user.pop(user_id) - 1
            if session_count:
                self._polls_by_session[session_key] = session_count
            if user_count:
                self._polls_by_user[user_id] = user_count

    def _poll_timeout(self, tool_name: str, response) -> MCPResponse:
        return MCPResponse(
            success=False, message=f"Timeout waiting for {tool_name} to complete",
            data=self._safe_response(response),
        )

    def _is_registered(self, project_id: str, tool_name: str) -> bool:
        return tool_name in self._project_tools.get(project_id, {})

    def _register_tool(self, project_id: str, definition: ToolDefinitionModel) -> None:
        self._project_tools.setdefault(project_id, {})[
            definition.name] = definition

    def get_project_id_for_hash(self, project_hash: str | None) -> str | None:
        if config.http_remote_hosted:
            return None
        if not project_hash:
            return None
        return self._hash_to_project.get(project_hash.lower())

    async def _poll_until_complete(
        self,
        tool_name: str,
        unity_instance,
        initial_params: dict[str, object],
        initial_response,
        poll_action: str,
        user_id: str | None = None,
        max_poll_seconds: int = 0,
        deadline: float | None = None,
    ) -> MCPResponse:
        poll_params = dict(initial_params)
        poll_params["action"] = poll_action or "status"
        if poll_params.get("job_id") is None and isinstance(initial_response, dict):
            data = initial_response.get("data")
            job_id = data.get("job_id") if isinstance(data, dict) else None
            if isinstance(job_id, str) and job_id.strip():
                poll_params["job_id"] = job_id

        timeout = self._bounded_poll_seconds(max_poll_seconds)
        deadline = min(deadline, time.monotonic() + timeout) if deadline is not None else time.monotonic() + timeout
        response = initial_response

        while True:
            if time.monotonic() >= deadline:
                break
            status, poll_interval = self._interpret_status(response)

            if status in ("complete", "error", "final"):
                return self._normalize_response(response)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            await asyncio.sleep(min(poll_interval, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            try:
                response = await asyncio.wait_for(
                    send_with_unity_instance(
                        async_send_command_with_retry,
                        unity_instance,
                        tool_name,
                        poll_params,
                        user_id=user_id,
                    ),
                    timeout=remaining,
                )
                if time.monotonic() >= deadline:
                    break
            except asyncio.TimeoutError:
                if time.monotonic() >= deadline:
                    break
                response = {"_mcp_status": "pending", "_mcp_poll_interval": poll_interval}
            except Exception as exc:  # pragma: no cover - network/domain reload variability
                logger.debug("Custom tool polling failed; retrying (%s)", type(exc).__name__)
                # Back off modestly but stay responsive.
                response = {
                    "_mcp_status": "pending",
                    "_mcp_poll_interval": min(max(poll_interval * 2, _DEFAULT_POLL_INTERVAL), 5.0),
                    "message": f"Retrying after transient error: {exc}",
                }

        return self._poll_timeout(tool_name, response)

    def _interpret_status(self, response) -> tuple[str, float]:
        if response is None:
            return "pending", _DEFAULT_POLL_INTERVAL

        if not isinstance(response, dict):
            return "final", _DEFAULT_POLL_INTERVAL

        status = response.get("_mcp_status")
        if status is None:
            if len(response.keys()) == 0:
                return "pending", _DEFAULT_POLL_INTERVAL
            return "final", _DEFAULT_POLL_INTERVAL

        if status == "pending":
            interval_raw = response.get(
                "_mcp_poll_interval", _DEFAULT_POLL_INTERVAL)
            try:
                interval = float(interval_raw)
            except (TypeError, ValueError):
                interval = _DEFAULT_POLL_INTERVAL

            interval = max(0.1, min(interval, 5.0))
            return "pending", interval

        if status == "complete":
            return "complete", _DEFAULT_POLL_INTERVAL

        if status == "error":
            return "error", _DEFAULT_POLL_INTERVAL

        return "final", _DEFAULT_POLL_INTERVAL

    def _normalize_response(self, response) -> MCPResponse:
        if isinstance(response, MCPResponse):
            return response
        if isinstance(response, dict):
            return MCPResponse(
                success=False if response.get("_mcp_status") == "error" else response.get("success", True),
                message=response.get("message"),
                error=response.get("error"),
                hint=response.get("hint"),
                data=response.get("data", response),
            )

        return MCPResponse(success=False, message=str(response))

    def _safe_response(self, response):
        if isinstance(response, dict):
            return response
        if response is None:
            return None
        return {"message": str(response)}

    def _register_project_tools(
        self,
        project_id: str,
        tools: list[ToolDefinitionModel],
        project_hash: str | None = None,
    ) -> tuple[list[str], list[str]]:
        registered: list[str] = []
        replaced: list[str] = []
        for tool in tools:
            if self._is_registered(project_id, tool.name):
                replaced.append(tool.name)
            self._register_tool(project_id, tool)
            registered.append(tool.name)
            if not self._project_scoped_tools:
                self._register_global_tool(tool)

        if project_hash:
            self._hash_to_project[project_hash.lower()] = project_id

        return registered, replaced

    def register_global_tools(self, tools: list[ToolDefinitionModel]) -> None:
        # Global custom tools are always registered, even when project-scoped tools
        # are enabled. Project-scoped tools can override globals by name, but
        # disabling globals entirely would break shared tooling that projects expect.
        builtin_names = self._get_builtin_tool_names()
        for tool in tools:
            if tool.name in builtin_names:
                logger.info(
                    "Skipping global custom tool registration for built-in tool '%s'",
                    tool.name,
                )
                continue
            self._register_global_tool(tool)

    def _get_builtin_tool_names(self) -> set[str]:
        return {tool["name"] for tool in get_registered_tools()}

    def _register_global_tool(self, definition: ToolDefinitionModel) -> None:
        if config.http_remote_hosted:
            return
        existing = self._global_tools.get(definition.name)
        if existing:
            if existing.model_dump() != definition.model_dump():
                logger.warning(
                    "Custom tool '%s' already registered with a different schema; keeping existing definition.",
                    definition.name,
                )
            return

        try:
            handler = self._build_global_tool_handler(definition)
            tool_handler = handler
            if not definition.structured_output:
                from fastmcp.tools import ToolResult
                from mcp.types import TextContent

                @functools.wraps(handler)
                async def content_only_handler(*args, **kwargs):
                    response = await handler(*args, **kwargs)
                    return ToolResult(content=[TextContent(type="text", text=response.model_dump_json())])

                tool_handler = content_only_handler
            wrapped = log_execution(definition.name, "Tool")(tool_handler)
            wrapped = telemetry_tool(definition.name)(wrapped)
            # Python 3.14 wraps copies __annotate__, not dynamically assigned annotations.
            wrapped.__annotations__ = dict(handler.__annotations__)
            wrapped = self._mcp.tool(
                name=definition.name,
                description=definition.description,
                **({"output_schema": None} if not definition.structured_output else {}),
            )(wrapped)
        except Exception as exc:  # pragma: no cover - defensive against tool conflicts
            logger.warning(
                "Failed to register custom tool '%s' globally: %s",
                definition.name,
                type(exc).__name__,
            )
            return

        self._global_tools[definition.name] = definition

    def _build_global_tool_handler(self, definition: ToolDefinitionModel):
        async def _handler(ctx: Context, **kwargs) -> MCPResponse:
            unity_instance = await get_unity_instance_from_context(ctx)
            if not unity_instance:
                return MCPResponse(
                    success=False,
                    message="No active Unity instance. Call set_active_instance with Name@hash from mcpforunity://instances.",
                )

            project_id = resolve_project_id_for_unity_instance(unity_instance)
            if project_id is None:
                return MCPResponse(
                    success=False,
                    message=f"Could not resolve project id for {unity_instance}. Ensure Unity is running and reachable.",
                )

            params = {k: v for k, v in kwargs.items() if v is not None}
            user_id = await get_user_id_from_context(ctx)
            service = CustomToolService.get_instance()
            return await service.execute_tool(
                project_id,
                definition.name,
                unity_instance,
                params,
                user_id=user_id,
            )

        _handler.__name__ = f"custom_tool_{definition.name}"
        _handler.__doc__ = definition.description or ""
        _handler.__signature__ = self._build_signature(definition)
        _handler.__annotations__ = self._build_annotations(definition)
        return _handler

    def _build_signature(self, definition: ToolDefinitionModel) -> inspect.Signature:
        params: list[inspect.Parameter] = [
            inspect.Parameter(
                "ctx",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=Context,
            )
        ]
        # Context injection and middleware routing consume these argument names.
        parameter_names = {"ctx", "unity_instance"}
        for param in definition.parameters:
            if not param.name.isidentifier() or keyword.iskeyword(param.name) or param.name in parameter_names:
                raise ValueError(
                    f"Custom tool '{definition.name}' has an invalid or duplicate parameter name '{param.name}'"
                )
            parameter_names.add(param.name)
            default = inspect._empty if param.required else self._coerce_default(
                param.default_value, param.type)
            params.append(
                inspect.Parameter(
                    param.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=self._map_param_type(param),
                )
            )
        return inspect.Signature(parameters=params)

    def _build_annotations(self, definition: ToolDefinitionModel) -> dict[str, object]:
        annotations: dict[str, object] = {"ctx": Context}
        for param in definition.parameters:
            if not param.name.isidentifier():
                continue
            annotations[param.name] = self._map_param_type(param)
        return annotations

    def _map_param_type(self, param: ToolParameterModel):
        ptype = (param.type or "string").lower()
        mapped_type = {
            "integer": int, "int": int,
            "number": float, "float": float, "double": float,
            "bool": bool, "boolean": bool,
            "array": list, "list": list,
            "object": dict, "dict": dict,
        }.get(ptype, str)
        if param.description:
            return Annotated[mapped_type, Field(description=param.description)]
        return mapped_type

    def _coerce_default(self, value: str | None, param_type: str | None):
        if value is None:
            return None
        try:
            ptype = (param_type or "string").lower()
            if ptype in ("integer", "int"):
                return int(value)
            if ptype in ("number", "float", "double"):
                return float(value)
            if ptype in ("bool", "boolean"):
                return str(value).lower() in ("1", "true", "yes", "on")
            container_type = {"array": list, "list": list, "object": dict, "dict": dict}.get(ptype)
            if container_type is not None:
                decoded = json.loads(value)
                if isinstance(decoded, container_type):
                    return decoded
            return value
        except Exception:
            return value


def compute_project_id(project_name: str, project_path: str) -> str:
    """
    DEPRECATED: Computes a SHA256-based project ID.
    This function is no longer used as of the multi-session fix.
    Unity instances now use their native project_hash (SHA1-based) for consistency
    across stdio and WebSocket transports.
    """
    combined = f"{project_name}:{project_path}"
    return sha256(combined.encode("utf-8")).hexdigest().upper()[:16]


def resolve_project_id_for_unity_instance(unity_instance: str | None) -> str | None:
    if unity_instance is None:
        return None

    # stdio transport: resolve via discovered instances with name+path
    try:
        pool = get_unity_connection_pool()
        instances = pool.discover_all_instances()
        target = None
        if "@" in unity_instance:
            name_part, _, hash_hint = unity_instance.rpartition("@")
            target = next(
                (
                    inst for inst in instances
                    if inst.name == name_part and inst.hash.startswith(hash_hint)
                ),
                None,
            )
        else:
            target = next(
                (
                    inst for inst in instances
                    if inst.id == unity_instance or inst.hash.startswith(unity_instance)
                ),
                None,
            )

        if target:
            # Return the project_hash from Unity (not a computed SHA256 hash).
            # This matches the hash Unity uses when registering tools via WebSocket.
            if target.hash:
                return target.hash
            logger.warning(
                f"Unity instance {target.id} has empty hash; cannot resolve project ID")
            return None
    except Exception:
        logger.debug(
            f"Failed to resolve project id via connection pool for {unity_instance}")

    # HTTP/WebSocket transport: resolve via PluginHub using project_hash
    try:
        hash_part: Optional[str] = None
        if "@" in unity_instance:
            _, _, suffix = unity_instance.rpartition("@")
            hash_part = suffix or None
        else:
            hash_part = unity_instance

        if hash_part:
            lowered = hash_part.lower()
            mapped: Optional[str] = None
            try:
                service = CustomToolService.get_instance()
                mapped = service.get_project_id_for_hash(lowered)
            except RuntimeError:
                mapped = None
            if mapped:
                return mapped
            return lowered
    except Exception:
        logger.debug(
            f"Failed to resolve project id via plugin hub for {unity_instance}")

    return None
