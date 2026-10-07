"""
Telemetry decorator for MCP for Unity tools
"""

import functools
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
)

_log = logging.getLogger("unity-mcp-telemetry")
_decorator_log_count = 0


def telemetry_tool(tool_name: str):
    """Decorator to add telemetry tracking to MCP tools"""

    def decorator(func: Callable) -> Callable:
        register_tool_actions(tool_name, func)

        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs) -> Any:
            start_time = perf_counter_ns()
            success = False
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
                success = True
                action_val = sub_action
                try:
                    if tool_name == "manage_script" and action_val == "create":
                        record_milestone(MilestoneType.FIRST_SCRIPT_CREATION)
                    elif tool_name.startswith("manage_scene"):
                        record_milestone(MilestoneType.FIRST_SCENE_MODIFICATION)
                    record_milestone(MilestoneType.FIRST_TOOL_USAGE)
                except Exception:
                    _log.debug("milestone emit failed", exc_info=True)
                return result
            except Exception as e:
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_tool_usage(tool_name, success, duration_ms, error, sub_action=sub_action)
                except Exception:
                    _log.debug("record_tool_usage failed", exc_info=True)

        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs) -> Any:
            start_time = perf_counter_ns()
            success = False
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
                success = True
                action_val = sub_action
                try:
                    if tool_name == "manage_script" and action_val == "create":
                        record_milestone(MilestoneType.FIRST_SCRIPT_CREATION)
                    elif tool_name.startswith("manage_scene"):
                        record_milestone(MilestoneType.FIRST_SCENE_MODIFICATION)
                    record_milestone(MilestoneType.FIRST_TOOL_USAGE)
                except Exception:
                    _log.debug("milestone emit failed", exc_info=True)
                return result
            except Exception as e:
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_tool_usage(tool_name, success, duration_ms, error, sub_action=sub_action)
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
            success = False
            error = None
            try:
                global _decorator_log_count
                if _decorator_log_count < 10:
                    _log.info(f"telemetry_decorator sync: resource={resource_name}")
                    _decorator_log_count += 1
                result = func(*args, **kwargs)
                success = True
                return result
            except Exception as e:
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_resource_usage(resource_name, success, duration_ms, error)
                except Exception:
                    _log.debug("record_resource_usage failed", exc_info=True)

        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs) -> Any:
            start_time = perf_counter_ns()
            success = False
            error = None
            try:
                global _decorator_log_count
                if _decorator_log_count < 10:
                    _log.info(f"telemetry_decorator async: resource={resource_name}")
                    _decorator_log_count += 1
                result = await func(*args, **kwargs)
                success = True
                return result
            except Exception as e:
                error = e
                raise
            finally:
                duration_ms = (perf_counter_ns() - start_time) / 1_000_000
                try:
                    record_resource_usage(resource_name, success, duration_ms, error)
                except Exception:
                    _log.debug("record_resource_usage failed", exc_info=True)

        return _async_wrapper if inspect.iscoroutinefunction(func) else _sync_wrapper

    return decorator
