import asyncio
import functools
import inspect
import json
import keyword
import logging
import time
from hashlib import sha256
from threading import Lock, RLock
from dataclasses import dataclass
from collections import OrderedDict
from contextlib import nullcontext
from typing import Annotated, Optional

from fastmcp import Context, FastMCP
from fastmcp.tools import Tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from starlette.requests import Request
from starlette.responses import JSONResponse

from services.execution_receipt_context import owned_to_thread
from core.config import config
from models.models import (
    MCPResponse,
    ToolDefinitionModel,
    ToolParameterModel,
    parse_tool_parameter_default,
)
from models.response_lifetime import ResponsePollLifetime
from models.custom_tool_schema import (
    PreparedSchema,
    SchemaContractError,
    prepare_descriptor,
    validation_contract,
)
from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import (
    async_send_command_with_retry,
    get_unity_connection_pool,
)
from transport.plugin_hub import PluginHub, extend_http_request_deadline
from services.tools import get_unity_instance_from_context
from services.registry import get_registered_tools
from services.registry.scalar_validation import strict_scalar_annotation

logger = logging.getLogger("mcp-for-unity-server")

_DEFAULT_POLL_INTERVAL = 1.0
_MAX_POLL_SECONDS = 600
# Native backend commands used by existing Python wrappers, never extra public custom tools.
# Keep its raw descriptor available for native admission and parent/alias policy checks.
_BACKEND_ONLY_TOOL_NAMES = frozenset(
    {
        "tail_console",
        "inspect_menu_item",
        "execute_menu_item_observed",
        "manage_components_selected",
        "manage_object_handles_v1",
        "inspect_serialized_object_v1",
    }
)
_MAX_ACTIVE_POLLS_PER_SESSION = 16
_MAX_ACTIVE_POLLS_PER_USER = 32
_MAX_ACTIVE_POLLS_GLOBAL = 256
_MAX_INPUT_MODELS = 128
_MAX_CATALOG_TOOLS = 256
_MAX_CATALOG_BYTES = 512 * 1024
_MAX_CATALOG_SOURCES = 256


@dataclass(frozen=True)
class _SourceCatalog:
    key: tuple[str, str | None, str]
    generation: str | None
    revision: int
    ordinal: int
    tools: dict[str, ToolDefinitionModel]
    prepared: dict[str, Tool]
    publish_global: bool
    known: bool = True


class _CatalogAdmission:
    """One invocation's descriptor/source identity; checks run at actual admission."""

    def __init__(self, service, catalog, project_id, user_id):
        self.service = service
        self.catalog = catalog
        self.user_id = user_id
        source_project = service._transport_project_id(project_id)
        http = service._sources.get(("http", user_id, source_project))
        stdio = service._sources.get(("stdio", None, source_project))
        self.session_id = http.generation if http is not None else None
        self.stdio_generation = stdio.generation if stdio is not None else None
        self._stdio_identity = None

    def _current(self):
        return (
            not self.service._publishing
            and self.service._sources.get(self.catalog.key) is self.catalog
        )

    def check_http(self, session_id, user_id):
        with self.service._catalog_lock:
            if not self._current() or self.user_id != user_id:
                return False
            if self.session_id is None:
                self.session_id = session_id
            return self.session_id == session_id

    def select_stdio(self, connection):
        with self.service._catalog_lock:
            if self._stdio_identity is None:
                self._stdio_identity = (
                    id(connection),
                    id(connection.sock),
                    connection.session_generation,
                )

    def check_stdio(self, connection):
        with self.service._catalog_lock:
            return (
                self._current()
                and self._stdio_identity
                == (id(connection), id(connection.sock), connection.session_generation)
                and (
                    self.stdio_generation is None
                    or self.stdio_generation == connection.session_generation
                )
            )

    def current(self):
        with self.service._catalog_lock:
            return self._current()


def custom_catalog_changed(reason="custom_tool_catalog_changed", job_id=None):
    data = {"reason": reason, "dispatched": False}
    if job_id is not None:
        data["job_id"] = job_id
    return MCPResponse(
        success=False,
        error="Custom tool catalog changed; rediscover the selected project's tools.",
        hint="inspect_state_before_retry",
        data=data,
    )


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
    project_id: str = Field(min_length=1, max_length=256)
    project_hash: str | None = Field(default=None, max_length=256)
    tools: list[ToolDefinitionModel] = Field(max_length=256)
    replace: bool = False


class ToolRegistrationResponse(BaseModel):
    success: bool
    registered: list[str]
    replaced: list[str]
    message: str
    removed: list[str] = Field(default_factory=list)


class CustomToolService:
    _instance: "CustomToolService | None" = None

    def __init__(self, mcp: FastMCP, project_scoped_tools: bool = True):
        CustomToolService._instance = self
        self._mcp = mcp
        if isinstance(getattr(mcp, "middleware", None), list):
            from services.custom_tool_schema_tool import install_schema_listing_policy

            install_schema_listing_policy(mcp)
        self._project_scoped_tools = project_scoped_tools
        self._project_tools: dict[str, dict[str, ToolDefinitionModel]] = {}
        self._hash_to_project: dict[str, str] = {}
        self._global_tools: dict[str, ToolDefinitionModel] = {}
        self._sources: dict[tuple[str, str | None, str], _SourceCatalog] = {}
        self._owned_tools: dict[str, Tool] = {}
        self._global_owners: dict[str, tuple[str, str | None, str]] = {}
        self._catalog_lock = RLock()
        self._diagnostic_attempts = OrderedDict()
        self._diagnostic_sequence = 0
        self._publishing = False
        self._next_source_ordinal = 0
        self._input_models: OrderedDict[
            tuple[tuple[str, str, bool, str | None, str | None], ...], type[BaseModel]
        ] = OrderedDict()
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
                return JSONResponse(
                    {"success": False, "error": "Request body must be valid JSON"}, status_code=400
                )
            except ValidationError as exc:
                return JSONResponse(
                    {
                        "success": False,
                        "error": exc.errors(include_input=False, include_context=False),
                    },
                    status_code=400,
                )

            try:
                previous = set(self._project_tools.get(payload.project_id, {}))
                previous_sources = self._sources
                previous_components = self._owned_tools
                registered, replaced = self._register_project_tools(
                    payload.project_id,
                    payload.tools,
                    project_hash=payload.project_hash,
                    replace=payload.replace,
                )
            except ValueError:
                return JSONResponse(
                    {"success": False, "error": "Invalid custom tool registration"},
                    status_code=400,
                )

            if (
                self._sources is not previous_sources
                or self._owned_tools is not previous_components
            ):
                # This route is local-only. Manual callers cannot supply a
                # trusted hosted principal; use the existing local invalidation.
                await PluginHub._notify_mcp_tool_list_changed()
            message = f"Registered {len(registered)} tool(s)"
            if replaced:
                message += f" (replaced: {', '.join(replaced)})"

            response = ToolRegistrationResponse(
                success=True,
                registered=registered,
                replaced=replaced,
                message=message,
                removed=sorted(previous - set(self._project_tools.get(payload.project_id, {})))
                if payload.replace
                else [],
            )
            return JSONResponse(response.model_dump())

    # --- Public API for MCP tools ---------------------------------------
    def _transport_project_id(self, project_id):
        if config.http_remote_hosted:
            return project_id
        return next(
            (alias for alias, project in self._hash_to_project.items() if project == project_id),
            project_id,
        )

    async def list_registered_tools(
        self,
        project_id: str,
        user_id: str | None = None,
    ) -> list[ToolDefinitionModel]:
        if config.http_remote_hosted:
            if not user_id:
                return []
            return [
                tool
                for tool in await PluginHub.get_tools_for_project(project_id, user_id=user_id)
                if tool.name not in _BACKEND_ONLY_TOOL_NAMES
            ]
        definitions = dict(self._project_tools.get(project_id, {}))
        transport_project = self._transport_project_id(project_id)
        kind = "http" if config.transport_mode.lower() == "http" else "stdio"
        catalog = self._sources.get((kind, user_id if kind == "http" else None, transport_project))
        hub_tools = (
            catalog.tools.values()
            if catalog is not None
            else await PluginHub.get_tools_for_project(transport_project, user_id=user_id)
        )
        for tool in hub_tools:
            definitions.setdefault(tool.name, tool)
        return [tool for tool in definitions.values() if tool.name not in _BACKEND_ONLY_TOOL_NAMES]

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
        transport_project = self._transport_project_id(project_id)
        kind = "http" if config.transport_mode.lower() == "http" else "stdio"
        catalog = self._sources.get((kind, user_id if kind == "http" else None, transport_project))
        if catalog is not None:
            return catalog.tools.get(tool_name)
        tool = await PluginHub.get_tool_definition(transport_project, tool_name, user_id=user_id)
        if tool:
            return tool
        if project_id in self._project_tools:
            return None
        shared = self._sources.get(("global", None, "shared"))
        return shared.tools.get(tool_name) if shared is not None else None

    def _definition_catalog(self, project_id, tool_name, user_id):
        if not config.http_remote_hosted:
            manual = self._sources.get(("manual", None, project_id))
            if manual is not None and tool_name in manual.tools:
                return manual
            if tool_name in self._project_tools.get(project_id, {}):
                return None  # Compatibility with existing internal descriptor fixtures.
        kind = "http" if config.transport_mode.lower() == "http" else "stdio"
        transport_project = self._transport_project_id(project_id)
        catalog = self._sources.get((kind, user_id if kind == "http" else None, transport_project))
        if catalog is not None:
            return catalog
        return self._sources.get(("global", None, "shared"))

    async def _send_custom(self, guard, unity_instance, tool_name, params, user_id):
        if guard is not None and not guard.current():
            return custom_catalog_changed()
        options = {"user_id": user_id}
        if guard is not None:
            options["_catalog_guard"] = guard
        return await send_with_unity_instance(
            async_send_command_with_retry, unity_instance, tool_name, params, **options
        )

    async def execute_tool(
        self,
        project_id: str,
        tool_name: str,
        unity_instance: str | None,
        params: dict[str, object] | None = None,
        user_id: str | None = None,
        *,
        omit_nulls: bool = False,
        _expected_contract: str | None = None,
    ) -> MCPResponse:
        from services.operation_permissions import authorize_remote_operation

        await authorize_remote_operation(tool_name, params or {}, custom=True)
        params = params or {}
        logger.info("Executing custom tool")
        if config.http_remote_hosted and not user_id:
            return MCPResponse(
                success=False, message="Authenticated user required for custom tools"
            )

        definition = await self.get_tool_definition(project_id, tool_name, user_id=user_id)
        if definition is None:
            return MCPResponse(
                success=False,
                message=f"Tool '{tool_name}' not found for project {project_id}",
            )

        if _expected_contract is not None and self._wire_contract(definition) != _expected_contract:
            return custom_catalog_changed("custom_tool_schema_changed")
        catalog = self._definition_catalog(project_id, tool_name, user_id)
        if catalog is not None and catalog.tools.get(tool_name) != definition:
            return custom_catalog_changed()
        guard = (
            _CatalogAdmission(self, catalog, project_id, user_id) if catalog is not None else None
        )

        try:
            schema = prepare_descriptor(
                definition.schema_version,
                definition.input_schema,
                definition.output_data_schema,
                definition.structured_output,
            )
            if schema is not None:
                params = schema.validate_input(params)
            else:
                inputs = self._get_input_model(definition)
                supplied_names = set(params)
                validated = inputs.model_validate(params)
                params = {
                    name: value
                    for name, value in validated.model_dump(by_alias=True).items()
                    if value is not None or (not omit_nulls and name in supplied_names)
                }
        except (ValidationError, ValueError):
            return MCPResponse(
                success=False, message=f"Invalid parameters for custom tool '{tool_name}'"
            )

        if not definition.requires_polling:
            response = await self._send_custom(guard, unity_instance, tool_name, params, user_id)
            result = self._finish_schema_response(response, schema)
            logger.info("Custom tool completed (success=%s, polled=False)", result.success)
            return result

        if not unity_instance:
            return MCPResponse(
                success=False, message="Explicit Unity instance required for custom tool polling"
            )
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
            extend_http_request_deadline(timeout)
            deadline = time.monotonic() + timeout
            async with ResponsePollLifetime() as responses:
                try:
                    result = await self._poll_until_complete(
                        tool_name,
                        unity_instance,
                        params,
                        await responses.fetch(
                            self._send_custom(guard, unity_instance, tool_name, params, user_id),
                            timeout=timeout,
                        ),
                        definition.poll_action or "status",
                        user_id=user_id,
                        max_poll_seconds=timeout,
                        deadline=deadline,
                        responses=responses,
                        catalog_guard=guard,
                        output_schema=schema,
                    )
                except asyncio.TimeoutError:
                    return self._poll_timeout(tool_name, None)
                logger.info("Custom tool completed (success=%s, polled=True)", result.success)
                return result
        finally:
            self._release_polling(session_key)

    # --- Internal helpers ------------------------------------------------
    def _get_input_model(self, definition: ToolDefinitionModel) -> type[BaseModel]:
        # Cache only declaration data. Invocation values and tenant state never enter it.
        contract = tuple(
            (param.name, param.type, param.required, param.default_value, param.description)
            for param in definition.parameters
        )
        cached = self._input_models.get(contract)
        if cached is not None:
            self._input_models.move_to_end(contract)
            return cached
        signature = self._build_signature(definition)
        fields = {
            f"parameter_{index}": (
                parameter.annotation,
                Field(
                    default=...
                    if parameter.default is inspect.Parameter.empty
                    else parameter.default,
                    alias=name,
                ),
            )
            for index, (name, parameter) in enumerate(signature.parameters.items())
            if name != "ctx"
        }
        model = create_model(
            "CustomToolInputs",
            __config__=ConfigDict(extra="allow", validate_default=True),
            **fields,
        )
        self._input_models[contract] = model
        if len(self._input_models) > _MAX_INPUT_MODELS:
            self._input_models.popitem(last=False)
        return model

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
            if (
                self._active_polls >= _MAX_ACTIVE_POLLS_GLOBAL
                or session_count >= _MAX_ACTIVE_POLLS_PER_SESSION
                or user_count >= _MAX_ACTIVE_POLLS_PER_USER
            ):
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
            success=False,
            message=f"Timeout waiting for {tool_name} to complete",
            data=self._safe_response(response),
        )

    def _is_registered(self, project_id: str, tool_name: str) -> bool:
        return tool_name in self._project_tools.get(project_id, {})

    def _register_tool(self, project_id: str, definition: ToolDefinitionModel) -> None:
        self._project_tools.setdefault(project_id, {})[definition.name] = definition

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
        responses: ResponsePollLifetime | None = None,
        catalog_guard=None,
        output_schema: PreparedSchema | None = None,
    ) -> MCPResponse:
        async with (
            nullcontext(responses) if responses is not None else ResponsePollLifetime() as responses
        ):
            poll_params = dict(initial_params)
            poll_params["action"] = poll_action or "status"
            if poll_params.get("job_id") is None and isinstance(initial_response, dict):
                data = initial_response.get("data")
                job_id = data.get("job_id") if isinstance(data, dict) else None
                if isinstance(job_id, str) and job_id.strip():
                    poll_params["job_id"] = job_id

            timeout = self._bounded_poll_seconds(max_poll_seconds)
            deadline = (
                min(deadline, time.monotonic() + timeout)
                if deadline is not None
                else time.monotonic() + timeout
            )
            response = initial_response
            initial_response = data = None
            poll_response = False

            try:
                while True:
                    if time.monotonic() >= deadline:
                        break
                    failure_data = (
                        response.data
                        if isinstance(response, MCPResponse)
                        else response.get("data")
                        if isinstance(response, dict)
                        else None
                    )
                    if isinstance(failure_data, dict) and failure_data.get("reason") in {
                        "custom_tool_catalog_changed",
                        "custom_tool_schema_changed",
                    }:
                        return custom_catalog_changed(
                            failure_data["reason"], poll_params.get("job_id")
                        )
                    failure_data = None
                    status, poll_interval = self._interpret_status(
                        response, poll_response=poll_response
                    )

                    if status in ("complete", "error", "final"):
                        return self._finish_schema_response(
                            response, output_schema, poll_params.get("job_id")
                        )

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if catalog_guard is not None and not catalog_guard.current():
                        return custom_catalog_changed(job_id=poll_params.get("job_id"))

                    await asyncio.sleep(min(poll_interval, remaining))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break

                    try:
                        response = await responses.fetch(
                            self._send_custom(
                                catalog_guard, unity_instance, tool_name, poll_params, user_id
                            ),
                            timeout=remaining,
                        )
                        poll_response = True
                        if time.monotonic() >= deadline:
                            break
                    except asyncio.TimeoutError:
                        if time.monotonic() >= deadline:
                            break
                        responses.discard()
                        response = {"_mcp_status": "pending", "_mcp_poll_interval": poll_interval}
                    except Exception as exc:  # pragma: no cover - network/domain reload variability
                        logger.debug(
                            "Custom tool polling failed; retrying (%s)", type(exc).__name__
                        )
                        # The previous observation is replaced by this local diagnostic.
                        responses.discard()
                        # Back off modestly but stay responsive.
                        response = {
                            "_mcp_status": "pending",
                            "_mcp_poll_interval": min(
                                max(poll_interval * 2, _DEFAULT_POLL_INTERVAL), 5.0
                            ),
                            "message": f"Retrying after transient error: {exc}",
                        }

                return self._poll_timeout(tool_name, response)
            finally:
                # Cancelled tasks may retain their traceback after this frame exits.
                response = failure_data = None

    def _interpret_status(self, response, *, poll_response: bool = False) -> tuple[str, float]:
        if response is None:
            return "pending", _DEFAULT_POLL_INTERVAL

        if not isinstance(response, dict):
            return "final", _DEFAULT_POLL_INTERVAL

        status = response.get("_mcp_status")
        if status is None:
            # A transient status-poll failure does not terminate a job already started.
            if (
                poll_response
                and response.get("success") is False
                and response.get("hint") == "retry"
            ):
                return "pending", _DEFAULT_POLL_INTERVAL
            if len(response.keys()) == 0:
                return "pending", _DEFAULT_POLL_INTERVAL
            return "final", _DEFAULT_POLL_INTERVAL

        if status == "pending":
            interval_raw = response.get("_mcp_poll_interval", _DEFAULT_POLL_INTERVAL)
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

    def _normalize_response(self, response, *, typed_output: bool = False) -> MCPResponse:
        if isinstance(response, MCPResponse):
            return response
        if isinstance(response, dict):
            return MCPResponse(
                success=False
                if response.get("_mcp_status") == "error"
                else response.get("success", True),
                message=response.get("message"),
                error=response.get("error"),
                hint=response.get("hint"),
                data=response.get(
                    "data", None if typed_output and "success" in response else response
                ),
            )

        return MCPResponse(success=False, message=str(response))

    def _finish_schema_response(
        self, response, schema: PreparedSchema | None, job_id=None
    ) -> MCPResponse:
        result = self._normalize_response(
            response, typed_output=schema is not None and schema.output_json is not None
        )
        if schema is not None and result.success:
            try:
                schema.validate_output(result.data)
            except SchemaContractError:
                if job_id is None and isinstance(result.data, dict):
                    job_id = result.data.get("job_id")
                diagnostic = {
                    "reason": "custom_tool_output_invalid",
                    "execution_status": "unknown",
                    "retry_safe": False,
                }
                if isinstance(job_id, str) and len(job_id) <= 256:
                    diagnostic["job_id"] = job_id
                return MCPResponse(
                    success=False,
                    message="Custom tool returned invalid output",
                    hint="inspect_state_before_retry",
                    data=diagnostic,
                )
        return result

    def _safe_response(self, response):
        if isinstance(response, dict):
            return response
        if response is None:
            return None
        return {"message": str(response)}

    def _register_project_tools(
        self, project_id, tools, project_hash=None, *, replace=False
    ) -> tuple[list[str], list[str]]:
        previous = self._project_tools.get(project_id, {})
        registered = [tool.name for tool in tools]
        replaced = [name for name in registered if name in previous]
        if tools or replace:
            self.replace_project_catalog(
                project_id,
                tools,
                kind="manual",
                replace=replace,
                publish_global=not self._project_scoped_tools,
                project_hash=project_hash,
            )
        return registered, replaced

    def register_global_tools(self, tools: list[ToolDefinitionModel]) -> None:
        # Anonymous callers retain an additive shared catalog. Real transports
        # publish source-owned snapshots through replace_project_catalog.
        if config.http_remote_hosted or not tools:
            return
        self.replace_project_catalog(
            "shared", tools, kind="global", replace=False, publish_global=True
        )

    def catalog_revision(self, kind, project_id, user_id=None):
        with self._catalog_lock:
            catalog = self._sources.get((kind, user_id, project_id))
            return catalog.revision if catalog is not None else 0

    @staticmethod
    def _wire_contract(definition):
        schema = prepare_descriptor(
            definition.schema_version,
            definition.input_schema,
            definition.output_data_schema,
            definition.structured_output,
        )
        if schema is not None:
            return json.dumps(
                {
                    "schema_version": 1,
                    "input_schema": validation_contract(json.loads(schema.input_json)),
                    "output_data_schema": None
                    if schema.output_json is None
                    else validation_contract(json.loads(schema.output_json)),
                    "structured_output": bool(definition.structured_output),
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        aliases = {
            "int": "integer",
            "float": "number",
            "double": "number",
            "bool": "boolean",
            "list": "array",
            "dict": "object",
        }
        parameters = []
        for parameter in definition.parameters:
            kind = (parameter.type or "string").lower()
            kind = aliases.get(kind, kind)
            if kind not in {"integer", "number", "boolean", "array", "object"}:
                kind = "string"
            parameters.append(
                (
                    parameter.name,
                    kind,
                    parameter.required,
                    parse_tool_parameter_default(parameter.default_value, parameter.type)
                    if not parameter.required
                    else None,
                )
            )
        return json.dumps(
            {
                "parameters": sorted(parameters, key=lambda row: row[0]),
                "structured_output": bool(definition.structured_output),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def retire_diagnostic_generation(self, kind, project_id, generation, user_id=None):
        """Retire only this captured owner; a replacement generation is untouched."""
        with self._catalog_lock:
            self._diagnostic_attempts.pop((kind, user_id, project_id, generation), None)

    def get_diagnostic_attempts(self, kind, project_id, user_id=None, generation=None):
        """Copy the current safe observation for one exact source owner."""
        with self._catalog_lock:
            self._expire_diagnostic_attempts()
            value = self._diagnostic_attempts.get((kind, user_id, project_id, generation))
            return value[2] if value is not None else None

    def _expire_diagnostic_attempts(self):
        now = time.monotonic()
        for key, value in list(self._diagnostic_attempts.items()):
            if key[0] == "manual" and now - value[0] >= 600:
                self._diagnostic_attempts.pop(key)

    def diagnostic_attempt_is_newer(self, kind, project_id, user_id, generation, sequence):
        """Compare private publication order without exposing cross-owner activity."""
        with self._catalog_lock:
            value = self._diagnostic_attempts.get((kind, user_id, project_id, generation))
            return value is not None and value[3] > sequence

    def record_diagnostic_attempt(
        self,
        kind,
        project_id,
        generation=None,
        user_id=None,
        *,
        names=(),
        reason="none",
        offending=None,
        retained=False,
        revision=0,
        catalog_generation=None,
    ):
        """Record fixed vocabulary only; callers fence authenticated connection ownership.

        Quotas charge UTF-8 JSON including all scope identity and record metadata,
        separately from fixed row/field/object-count caps. They are not heap sizes.
        """
        from hashlib import sha256
        from models.tool_diagnostics import ToolDiagnosticAttempt, ToolDiagnosticRow

        if (
            kind not in ("http", "stdio", "manual")
            or any(
                value is not None
                and (not isinstance(value, str) or len(value) > 256 or not value.isprintable())
                for value in (project_id, generation, user_id, catalog_generation)
            )
            or not isinstance(project_id, str)
        ):
            return
        action = {
            "none": "none",
            "duplicate_name": "remove_duplicate_then_refresh",
            "schema_or_default_invalid": "review_schema_and_defaults",
            "publication_failed": "inspect_registration_then_refresh",
            "metadata_invalid": "review_tool_declaration",
            "diagnostic_limit": "read_named_tool_or_reduce_catalog",
        }.get(reason)
        if action is None:
            return
        with self._catalog_lock:
            self._expire_diagnostic_attempts()
            key = (kind, user_id, project_id, generation)
            previous = self._diagnostic_attempts.get(key)
            ordinal = previous[2].attempt + 1 if previous is not None else 1
            self._diagnostic_sequence += 1
            from itertools import islice

            safe_names = [
                name
                if isinstance(name, str)
                and len(name) <= 128
                and all(c.isalnum() or c in "_.-" for c in name)
                else None
                for name in islice(names, 256)
            ]
            if offending in safe_names:
                safe_names.remove(offending)
                safe_names.insert(0, offending)
            rows = []
            for name in (safe_names or [None])[:64]:
                collateral = reason != "none" and offending is not None and name != offending
                rows.append(
                    ToolDiagnosticRow(
                        tool_name=name,
                        stage="server_publication",
                        state="registered" if reason == "none" else "registration_failed",
                        reason="batch_rejected" if collateral else reason,
                        next_action="repair_batch_then_refresh" if collateral else action,
                    )
                )
            attempt = ToolDiagnosticAttempt(
                source_kind=kind,
                project_id=sha256(project_id.encode()).hexdigest(),
                generation=sha256(generation.encode()).hexdigest() if generation else None,
                catalog_generation=sha256(catalog_generation.encode()).hexdigest()
                if catalog_generation
                else None,
                attempt=ordinal,
                catalog_revision=revision,
                prior_catalog_retained=retained,
                truncated=len(safe_names) > 64,
                rows=rows,
            )

            def encoded_size(value):
                return len(
                    json.dumps(
                        {
                            "transport": kind,
                            "tenant": user_id,
                            "project": project_id,
                            "generation": generation,
                            "observation": value.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                )

            size = encoded_size(attempt)
            while size > 32 * 1024 and attempt.rows:
                attempt = attempt.model_copy(update={"rows": attempt.rows[:-1], "truncated": True})
                size = encoded_size(attempt)
            if size > 32 * 1024:
                return
            self._diagnostic_attempts.pop(key, None)
            self._diagnostic_attempts[key] = (
                time.monotonic(),
                size,
                attempt,
                self._diagnostic_sequence,
            )
            while len(self._diagnostic_attempts) > 256 or (
                sum(value[1] for value in self._diagnostic_attempts.values())
                + max(0, len(self._diagnostic_attempts) - 1)
                + len(b'{"records":[]}')
                > 512 * 1024
            ):
                self._diagnostic_attempts.popitem(last=False)

    def replace_project_catalog(
        self,
        project_id,
        tools,
        *,
        kind="http",
        generation=None,
        user_id=None,
        replace=True,
        publish_global=True,
        known=True,
        project_hash=None,
        visibility=None,
        expected_revision=None,
        withdraw=False,
    ):
        """Prepare and commit one bounded source without an async publication gap.

        The optional visibility pair contains detached synchronous apply/rollback
        closures owned by PluginHub. Callers holding registry locks must preserve
        Hub -> registry -> this short publication lock order.
        """
        key = (kind, user_id, project_id)
        with self._catalog_lock:
            if self._publishing:
                raise ValueError("Reentrant custom tool catalog publication")
            self._publishing = True
            diagnostic_reason = "schema_or_default_invalid"
            diagnostic_offending = None
            diagnostic_names = []
            diagnostic_record = True
            old = self._sources.get(key)
            try:
                old = self._sources.get(key)
                if expected_revision is not None and (
                    (old.revision if old is not None else 0) != expected_revision
                ):
                    diagnostic_record = False
                    raise ValueError("Custom tool catalog changed during refresh")
                base = (
                    old.tools
                    if old is not None
                    else self._project_tools.get(project_id, {})
                    if kind == "manual"
                    else {}
                )
                definitions = {} if replace else dict(base)
                incoming = {}
                for raw in tools:
                    diagnostic_offending = (
                        raw.name if isinstance(raw, ToolDefinitionModel) else None
                    )
                    definition = ToolDefinitionModel.model_validate(raw.model_dump())
                    if len(diagnostic_names) < 256:
                        diagnostic_names.append(definition.name)
                    if definition.name in incoming:
                        diagnostic_reason = "duplicate_name"
                        raise ValueError("Duplicate custom tool name in catalog")
                    incoming[definition.name] = definition
                definitions.update(incoming)
                retire = withdraw or (kind == "manual" and replace and not definitions)
                if old is None and not retire and len(self._sources) >= _MAX_CATALOG_SOURCES:
                    raise ValueError("Custom tool catalog source limit reached")
                if len(definitions) > _MAX_CATALOG_TOOLS:
                    raise ValueError("Custom tool catalog exceeds 256 tools")
                encoded = json.dumps(
                    [tool.model_dump() for tool in definitions.values()],
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                if len(encoded) > _MAX_CATALOG_BYTES:
                    raise ValueError("Custom tool catalog exceeds 512 KiB")
                prepared = dict(old.prepared) if old is not None and not replace else {}
                # Even a no-op advertisement must validate the complete incoming batch.
                for name, definition in definitions.items():
                    if name in incoming or name not in prepared:
                        diagnostic_offending = name
                        prepared[name] = self._prepare_global_tool(definition)
                diagnostic_offending = None
                diagnostic_reason = "publication_failed"
                changed = (
                    old is None
                    or old.generation != generation
                    or old.tools != definitions
                    or old.known != known
                    or old.publish_global != publish_global
                    or (
                        kind == "manual"
                        and project_hash is not None
                        and self._hash_to_project.get(project_hash.lower()) != project_id
                    )
                )
                if retire:
                    changed = old is not None or (
                        kind == "manual" and project_id in self._project_tools
                    )
                catalog = old
                if retire:
                    catalog = None
                elif changed:
                    catalog = _SourceCatalog(
                        key=key,
                        generation=generation,
                        revision=old.revision + 1 if old is not None else 1,
                        ordinal=old.ordinal if old is not None else self._next_source_ordinal,
                        tools=definitions,
                        prepared=prepared,
                        publish_global=publish_global,
                        known=known,
                    )
                sources = dict(self._sources)
                if retire:
                    sources.pop(key, None)
                else:
                    sources[key] = catalog
                builtin_names = self._get_builtin_tool_names()
                winners = {}
                for source in sorted(sources.values(), key=lambda value: value.ordinal):
                    if not source.publish_global or config.http_remote_hosted:
                        continue
                    for name, definition in source.tools.items():
                        if name in _BACKEND_ONLY_TOOL_NAMES:
                            continue
                        if name not in builtin_names or name in self._owned_tools:
                            winners.setdefault(name, (source, definition))
                owned = {}
                global_tools = {}
                global_owners = {}
                provider = self._mcp._local_provider if winners or self._owned_tools else None
                components = provider._components if provider is not None else {}
                effective_owned = {
                    name: tool
                    for name, tool in self._owned_tools.items()
                    if components.get(tool.key) is tool
                }
                for name, (source, definition) in winners.items():
                    candidate = source.prepared[name]
                    current = components.get(candidate.key)
                    if current is not None and current is not effective_owned.get(name):
                        # Withdrawal relinquishes a lost key. Other sources may
                        # stay usable through execute_custom_tool, but cannot
                        # replace a foreign SDK declaration as a side effect.
                        if retire or source.key != key:
                            continue
                        raise ValueError("Custom tool component ownership changed")
                    global_tools[name] = definition
                    global_owners[name] = source.key
                    if (
                        self._global_owners.get(name) == source.key
                        and self._global_tools.get(name) == definition
                        and name in self._owned_tools
                    ):
                        owned[name] = self._owned_tools[name]
                    else:
                        owned[name] = source.prepared[name]
                additions = [
                    tool for name, tool in owned.items() if effective_owned.get(name) is not tool
                ]
                removals = [tool for name, tool in effective_owned.items() if name not in owned]
                component_changed = bool(additions or removals)
                if not changed and not component_changed and visibility is None:
                    diagnostic_reason = "none"
                    return {
                        "changed": False,
                        "global_component_changed": False,
                        "revision": old.revision if old is not None else 0,
                        "removed": [],
                    }
                project_tools = self._project_tools
                hash_to_project = self._hash_to_project
                if kind == "manual":
                    if retire:
                        project_tools = {
                            project: rows
                            for project, rows in project_tools.items()
                            if project != project_id
                        }
                        hash_to_project = {
                            alias: project
                            for alias, project in hash_to_project.items()
                            if project != project_id
                        }
                    else:
                        project_tools = {**project_tools, project_id: definitions}
                    if project_hash and not retire:
                        hash_to_project = {
                            **{
                                alias: project
                                for alias, project in hash_to_project.items()
                                if project != project_id
                            },
                            project_hash.lower(): project_id,
                        }
                # FastMCP 4.1's local provider stores components by exact Tool.key;
                # _remove_component mutates that map and has no separate cache.
                # Keep this version-pinned rollback seam confined to owned keys.
                missing = object()
                delta = [(tool.key, tool, effective_owned.get(tool.name)) for tool in additions]
                delta.extend((tool.key, None, tool) for tool in removals)
                # A foreign replacement owns its key, even if our old declaration
                # still exists. Never overwrite/remove it during reconciliation.
                for component_key, _, previous_owned in delta:
                    if components.get(component_key, missing) is not (
                        previous_owned if previous_owned is not None else missing
                    ):
                        raise ValueError("Custom tool component ownership changed")
                previous = {
                    component_key: components.get(component_key, missing)
                    for component_key, _, _ in delta
                }
                attempted = []
                try:
                    for tool in additions:
                        attempted.append((tool.key, tool))
                        if self._mcp.add_tool(tool) is not tool:
                            raise ValueError("Custom tool replacement was not published")
                    for tool in removals:
                        attempted.append((tool.key, None))
                        provider._remove_component(tool.key)
                    if visibility is not None:
                        visibility[0]()
                except BaseException:
                    if visibility is not None:
                        visibility[1]()
                    for component_key, published in reversed(attempted):
                        if components.get(component_key, missing) is (
                            published if published is not None else missing
                        ):
                            old_component = previous[component_key]
                            if old_component is missing:
                                components.pop(component_key, None)
                            else:
                                components[component_key] = old_component
                    raise
                self._sources = sources
                self._global_tools = global_tools
                self._global_owners = global_owners
                self._owned_tools = owned
                if old is None and not retire:
                    self._next_source_ordinal += 1
                self._project_tools = project_tools
                self._hash_to_project = hash_to_project
                diagnostic_reason = "none"
                return {
                    "changed": changed or component_changed or visibility is not None,
                    "global_component_changed": component_changed,
                    "revision": catalog.revision
                    if catalog is not None
                    else old.revision + 1
                    if old is not None
                    else 0,
                    "removed": sorted(set(base) - set(definitions)) if replace else [],
                }
            finally:
                self._publishing = False
                if diagnostic_record:
                    current = self._sources.get(key)
                    if withdraw or not known or (diagnostic_reason == "none" and current is None):
                        self.retire_diagnostic_generation(
                            kind, project_id, old.generation if old else generation, user_id
                        )
                    else:
                        if (
                            diagnostic_reason == "none"
                            and old is not None
                            and old.generation != generation
                        ):
                            self.retire_diagnostic_generation(
                                kind, project_id, old.generation, user_id
                            )
                        self.record_diagnostic_attempt(
                            kind,
                            project_id,
                            generation,
                            user_id,
                            names=diagnostic_names,
                            reason=diagnostic_reason,
                            offending=diagnostic_offending,
                            retained=diagnostic_reason != "none" and current is not None,
                            revision=current.revision if current else 0,
                            catalog_generation=current.generation if current else None,
                        )

    def _publish_global_tools(self, prepared):
        self.register_global_tools([definition for definition, _ in prepared])

    def _get_builtin_tool_names(self) -> set[str]:
        return {tool["name"] for tool in get_registered_tools()}

    def _register_global_tool(self, definition: ToolDefinitionModel) -> None:
        self.register_global_tools([definition])

    def _prepare_global_tool(self, definition: ToolDefinitionModel) -> Tool:
        """Generate the runtime validator and SDK schema without publishing a tool."""
        schema = prepare_descriptor(
            definition.schema_version,
            definition.input_schema,
            definition.output_data_schema,
            definition.structured_output,
        )
        if schema is None:
            self._get_input_model(definition).model_json_schema()
        handler = self._build_global_tool_handler(definition)
        tool_handler = handler
        if schema is None and not definition.structured_output:
            from fastmcp.tools import ToolResult
            from mcp.types import TextContent

            @functools.wraps(handler)
            async def content_only_handler(*args, **kwargs):
                response = await handler(*args, **kwargs)
                return ToolResult(
                    content=[TextContent(type="text", text=response.model_dump_json())]
                )

            tool_handler = content_only_handler
        wrapped = log_execution(definition.name, "Tool")(tool_handler)
        wrapped = telemetry_tool(definition.name)(wrapped)
        # Python 3.14 wraps copies __annotate__, not dynamically assigned annotations.
        wrapped.__annotations__ = dict(handler.__annotations__)
        if schema is not None:
            from services.custom_tool_schema_tool import SchemaTool, require_schema_listing_policy

            require_schema_listing_policy(self._mcp)
            return SchemaTool(definition, wrapped)
        return Tool.from_function(
            wrapped,
            name=definition.name,
            description=definition.description,
            **({"output_schema": None} if not definition.structured_output else {}),
        )

    def _build_global_tool_handler(self, definition: ToolDefinitionModel):
        expected_contract = self._wire_contract(definition)

        async def _handler(ctx: Context, **kwargs) -> MCPResponse:
            unity_instance = await get_unity_instance_from_context(ctx)
            if not unity_instance:
                return MCPResponse(
                    success=False,
                    message="No active Unity instance. Call set_active_instance with Name@hash from mcpforunity://instances.",
                )

            project_id = await owned_to_thread(
                resolve_project_id_for_unity_instance, unity_instance
            )
            if project_id is None:
                return MCPResponse(
                    success=False,
                    message=f"Could not resolve project id for {unity_instance}. Ensure Unity is running and reachable.",
                )

            # Keep explicit nulls until descriptor validation so they suppress defaults.
            params = dict(kwargs)
            user_id = await get_user_id_from_context(ctx)
            service = CustomToolService.get_instance()
            return await service.execute_tool(
                project_id,
                definition.name,
                unity_instance,
                params,
                user_id=user_id,
                omit_nulls=True,
                _expected_contract=expected_contract,
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
            if (
                not param.name.isidentifier()
                or keyword.iskeyword(param.name)
                or param.name in parameter_names
            ):
                raise ValueError(
                    f"Custom tool '{definition.name}' has an invalid or duplicate parameter name '{param.name}'"
                )
            parameter_names.add(param.name)
            default = (
                inspect._empty
                if param.required
                else self._coerce_default(param.default_value, param.type)
            )
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
            "integer": int,
            "int": int,
            "number": float,
            "float": float,
            "double": float,
            "bool": bool,
            "boolean": bool,
            "array": list,
            "list": list,
            "object": dict,
            "dict": dict,
        }.get(ptype, str)
        if not param.required:
            mapped_type = Optional[mapped_type]
        mapped_type = strict_scalar_annotation(mapped_type)
        if param.description:
            return Annotated[mapped_type, Field(description=param.description)]
        return mapped_type

    def _coerce_default(self, value: str | None, param_type: str | None):
        return parse_tool_parameter_default(value, param_type)


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
    # HTTP routes use the plugin registry; local status files cannot identify that transport.
    if config.transport_mode.lower() != "http":
        try:
            pool = get_unity_connection_pool()
            instances = pool.discover_all_instances()
            target = None
            if "@" in unity_instance:
                name_part, _, hash_hint = unity_instance.rpartition("@")
                target = next(
                    (
                        inst
                        for inst in instances
                        if inst.name == name_part and inst.hash.startswith(hash_hint)
                    ),
                    None,
                )
            else:
                target = next(
                    (
                        inst
                        for inst in instances
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
                    f"Unity instance {target.id} has empty hash; cannot resolve project ID"
                )
                return None
        except Exception:
            logger.debug(f"Failed to resolve project id via connection pool for {unity_instance}")

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
        logger.debug(f"Failed to resolve project id via plugin hub for {unity_instance}")

    return None
