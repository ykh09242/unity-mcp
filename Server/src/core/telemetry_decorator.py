"""
Telemetry decorator for MCP for Unity tools
"""

import functools
from asyncio import CancelledError
import inspect
import logging
from time import perf_counter_ns
from typing import Callable, Any

from core.telemetry import (
    record_resource_usage,
    record_tool_usage,
    record_milestone,
    MilestoneType,
    register_tool_actions,
    tool_action_label,
    TelemetryOutcome,
)
from models.models import MCPResponse

_log = logging.getLogger("unity-mcp-telemetry")
_decorator_log_count = 0


def _response_outcome(result: Any) -> TelemetryOutcome:
    """Read only known control fields, without dumping models or inspecting error text.

    Tool/resource returns are intentionally open-ended. Known models use stored fields;
    arbitrary objects' properties, serializers, and truthiness hooks are not inspected.
    """
    try:
        if issubclass(type(result), MCPResponse):
            fields = object.__getattribute__(result, "__dict__")
            if type(fields) is not dict:
                return TelemetryOutcome.OUTCOME_UNKNOWN
            success, data = fields.get("success"), fields.get("data")
        elif type(result) is dict and "success" in result:
            success, data = result["success"], result.get("data")
        else:
            return TelemetryOutcome.SUCCESS
        if success is True:
            return TelemetryOutcome.SUCCESS
        if success is not False:
            return TelemetryOutcome.OUTCOME_UNKNOWN
        if type(data) is dict:
            reason = data.get("reason")
            if type(reason) is str:
                if reason == "outcome_unknown":
                    return TelemetryOutcome.OUTCOME_UNKNOWN
                if reason == "command_deadline_exceeded":
                    return TelemetryOutcome.TIMEOUT
        return TelemetryOutcome.DOMAIN_ERROR
    except Exception:
        # Observation must never replace a return value or expose its private content.
        return TelemetryOutcome.OUTCOME_UNKNOWN


def telemetry_tool(tool_name: str):
    """Decorator to add telemetry tracking to MCP tools"""

    def decorator(func: Callable) -> Callable:
        register_tool_actions(tool_name, func)

        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs) -> Any:
            start_time = perf_counter_ns()
            outcome = TelemetryOutcome.OUTCOME_UNKNOWN
            error = None
            # Extract sub-action (e.g., 'get_hierarchy') from bound args when available
            sub_action = None
            try:
                sig = inspect.signature(func)
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                sub_action = tool_action_label(tool_name, bound.arguments.get("action"))
            except Exception:
                sub_action = None
            try:
                global _decorator_log_count
                if _decorator_log_count < 10:
                    _log.info(f"telemetry_decorator sync: tool={tool_name}")
                    _decorator_log_count += 1
                result = func(*args, **kwargs)
                outcome = _response_outcome(result)
                action_val = sub_action
                try:
                    if (
                        outcome is TelemetryOutcome.SUCCESS
                        and tool_name == "manage_script"
                        and action_val == "create"
                    ):
                        record_milestone(MilestoneType.FIRST_SCRIPT_CREATION)
                    elif outcome is TelemetryOutcome.SUCCESS and tool_name.startswith(
                        "manage_scene"
                    ):
                        record_milestone(MilestoneType.FIRST_SCENE_MODIFICATION)
                    if outcome is TelemetryOutcome.SUCCESS:
                        record_milestone(MilestoneType.FIRST_TOOL_USAGE)
                except Exception:
                    _log.debug("milestone emit failed", exc_info=True)
                return result
            except CancelledError:
                outcome = TelemetryOutcome.CANCELED
                raise
            except TimeoutError as e:
                outcome = TelemetryOutcome.TIMEOUT
                error = e
                raise
            except Exception as e:
                outcome = TelemetryOutcome.EXCEPTION
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_tool_usage(
                        tool_name,
                        outcome is TelemetryOutcome.SUCCESS,
                        duration_ms,
                        error,
                        sub_action=sub_action,
                        outcome=outcome,
                    )
                except Exception:
                    _log.debug("record_tool_usage failed", exc_info=True)

        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs) -> Any:
            start_time = perf_counter_ns()
            outcome = TelemetryOutcome.OUTCOME_UNKNOWN
            error = None
            # Extract sub-action (e.g., 'get_hierarchy') from bound args when available
            sub_action = None
            try:
                sig = inspect.signature(func)
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                sub_action = tool_action_label(tool_name, bound.arguments.get("action"))
            except Exception:
                sub_action = None
            try:
                global _decorator_log_count
                if _decorator_log_count < 10:
                    _log.info(f"telemetry_decorator async: tool={tool_name}")
                    _decorator_log_count += 1
                result = await func(*args, **kwargs)
                outcome = _response_outcome(result)
                action_val = sub_action
                try:
                    if (
                        outcome is TelemetryOutcome.SUCCESS
                        and tool_name == "manage_script"
                        and action_val == "create"
                    ):
                        record_milestone(MilestoneType.FIRST_SCRIPT_CREATION)
                    elif outcome is TelemetryOutcome.SUCCESS and tool_name.startswith(
                        "manage_scene"
                    ):
                        record_milestone(MilestoneType.FIRST_SCENE_MODIFICATION)
                    if outcome is TelemetryOutcome.SUCCESS:
                        record_milestone(MilestoneType.FIRST_TOOL_USAGE)
                except Exception:
                    _log.debug("milestone emit failed", exc_info=True)
                return result
            except CancelledError:
                outcome = TelemetryOutcome.CANCELED
                raise
            except TimeoutError as e:
                outcome = TelemetryOutcome.TIMEOUT
                error = e
                raise
            except Exception as e:
                outcome = TelemetryOutcome.EXCEPTION
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_tool_usage(
                        tool_name,
                        outcome is TelemetryOutcome.SUCCESS,
                        duration_ms,
                        error,
                        sub_action=sub_action,
                        outcome=outcome,
                    )
                except Exception:
                    _log.debug("record_tool_usage failed", exc_info=True)

        return _async_wrapper if inspect.iscoroutinefunction(func) else _sync_wrapper

    return decorator


def telemetry_resource(resource_name: str):
    """Decorator to add telemetry tracking to MCP resources"""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs) -> Any:
            start_time = perf_counter_ns()
            outcome = TelemetryOutcome.OUTCOME_UNKNOWN
            error = None
            try:
                global _decorator_log_count
                if _decorator_log_count < 10:
                    _log.info(f"telemetry_decorator sync: resource={resource_name}")
                    _decorator_log_count += 1
                result = func(*args, **kwargs)
                outcome = _response_outcome(result)
                return result
            except CancelledError:
                outcome = TelemetryOutcome.CANCELED
                raise
            except TimeoutError as e:
                outcome = TelemetryOutcome.TIMEOUT
                error = e
                raise
            except Exception as e:
                outcome = TelemetryOutcome.EXCEPTION
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_resource_usage(
                        resource_name,
                        outcome is TelemetryOutcome.SUCCESS,
                        duration_ms,
                        error,
                        outcome=outcome,
                    )
                except Exception:
                    _log.debug("record_resource_usage failed", exc_info=True)

        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs) -> Any:
            start_time = perf_counter_ns()
            outcome = TelemetryOutcome.OUTCOME_UNKNOWN
            error = None
            try:
                global _decorator_log_count
                if _decorator_log_count < 10:
                    _log.info(f"telemetry_decorator async: resource={resource_name}")
                    _decorator_log_count += 1
                result = await func(*args, **kwargs)
                outcome = _response_outcome(result)
                return result
            except CancelledError:
                outcome = TelemetryOutcome.CANCELED
                raise
            except TimeoutError as e:
                outcome = TelemetryOutcome.TIMEOUT
                error = e
                raise
            except Exception as e:
                outcome = TelemetryOutcome.EXCEPTION
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_resource_usage(
                        resource_name,
                        outcome is TelemetryOutcome.SUCCESS,
                        duration_ms,
                        error,
                        outcome=outcome,
                    )
                except Exception:
                    _log.debug("record_resource_usage failed", exc_info=True)

        return _async_wrapper if inspect.iscoroutinefunction(func) else _sync_wrapper

    return decorator
