from typing import Annotated, Any, Optional

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.profiler_memory_analysis import (
    MEMORY_ANALYSIS_ACTIONS,
    validate_memory_analysis,
)
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

SESSION_ACTIONS = [
    "profiler_start",
    "profiler_stop",
    "profiler_status",
    "profiler_set_areas",
]

COUNTER_ACTIONS = [
    "get_frame_timing",
    "get_counters",
    "get_object_memory",
]

MEMORY_SNAPSHOT_ACTIONS = [
    "memory_take_snapshot",
    "memory_list_snapshots",
    "memory_compare_snapshots",
]

FRAME_DEBUGGER_ACTIONS = [
    "frame_debugger_enable",
    "frame_debugger_disable",
    "frame_debugger_get_events",
]

UTILITY_ACTIONS = ["ping"]
WINDOW_ACTIONS = ["window_start", "window_status", "window_result", "window_cancel"]

ALL_ACTIONS = (
    UTILITY_ACTIONS
    + SESSION_ACTIONS
    + COUNTER_ACTIONS
    + MEMORY_SNAPSHOT_ACTIONS
    + FRAME_DEBUGGER_ACTIONS
    + WINDOW_ACTIONS
    + MEMORY_ANALYSIS_ACTIONS
)


@mcp_for_unity_tool(
    group="profiling",
    description=(
        "Unity Profiler session control, counter reads, memory snapshots, and Frame Debugger.\n\n"
        "SESSION:\n"
        "- profiler_start: Enable profiler, optionally record to .raw file (log_file, enable_callstacks)\n"
        "- profiler_stop: Disable profiler, stop recording\n"
        "- profiler_status: Get enabled state, active areas, recording path\n"
        "- profiler_set_areas: Toggle ProfilerAreas on/off (areas dict)\n\n"
        "COUNTERS:\n"
        "- get_frame_timing: FrameTimingManager data (12 fields, synchronous)\n"
        "- get_counters: Generic counter read by category + optional counter names (async, 1-frame wait)\n"
        "- get_object_memory: Memory size of a specific object by path\n\n"
        "INDEPENDENT WINDOWS:\n"
        "- window_start: Bounded category/markers sampling with warmup and deadline; returns window_id\n"
        "- window_status/window_result/window_cancel: Inspect, read terminal statistics, or dispose a window\n"
        "Completed per-frame sums use native units; p95 is nearest-rank, missing is distinct from zero.\n\n"
        "MEMORY SNAPSHOT (requires com.unity.memoryprofiler):\n"
        "- memory_take_snapshot: Capture memory snapshot to file\n"
        "- memory_list_snapshots: List available .snap files\n"
        "- memory_compare_snapshots: Compare legacy file metadata\n\n"
        "MEMORY ANALYSIS (native bytes are distinct from managed/retained size):\n"
        "- memory_aggregate: Bounded hierarchy/shared runtime-resource sums with object deduplication\n"
        "- memory_snapshot_reader_status: Exact supported reader package/format capability\n"
        "- memory_analyze_snapshots: Actual typed native-object count/byte/identity deltas; requires Memory Profiler 1.1.12\n\n"
        "FRAME DEBUGGER:\n"
        "- frame_debugger_enable: Turn on Frame Debugger, report event count\n"
        "- frame_debugger_disable: Turn off Frame Debugger\n"
        "- frame_debugger_get_events: Get draw call events (paged, best-effort via reflection)"
    ),
    annotations=ToolAnnotations(
        title="Manage Profiler",
        destructiveHint=False,
        readOnlyHint=False,
    ),
)
async def manage_profiler(
    ctx: Context,
    action: Annotated[str, "The profiler action to perform."],
    category: Annotated[
        Optional[str],
        "Profiler category name for get_counters (e.g. Render, Scripts, Memory, Physics).",
    ] = None,
    counters: Annotated[
        Optional[list[str]],
        "Specific counter names for get_counters. Omit to read all in category.",
    ] = None,
    object_path: Annotated[
        Optional[str], "Scene hierarchy or asset path for get_object_memory."
    ] = None,
    log_file: Annotated[
        Optional[str],
        "Path to .raw file for profiler_start recording. Project-relative or absolute inside "
        "the project or Unity temporary cache directory.",
    ] = None,
    enable_callstacks: Annotated[
        Optional[bool], "Enable allocation callstacks for profiler_start."
    ] = None,
    areas: Annotated[
        Optional[dict[str, bool]], "Dict of area name to bool for profiler_set_areas."
    ] = None,
    snapshot_path: Annotated[
        Optional[str],
        "Output path for memory_take_snapshot. Project-relative or absolute inside the project "
        "or Unity temporary cache directory.",
    ] = None,
    search_path: Annotated[
        Optional[str],
        "Search directory for memory_list_snapshots. Project-relative or absolute inside the "
        "project or Unity temporary cache directory.",
    ] = None,
    snapshot_a: Annotated[
        Optional[str],
        "First snapshot path for memory_compare_snapshots. Project-relative or absolute inside "
        "the project or Unity temporary cache directory.",
    ] = None,
    snapshot_b: Annotated[
        Optional[str],
        "Second snapshot path for memory_compare_snapshots. Project-relative or absolute inside "
        "the project or Unity temporary cache directory.",
    ] = None,
    page_size: Annotated[
        Optional[int], "Page size for frame_debugger_get_events (default 50, clamped to 1..500)."
    ] = None,
    cursor: Annotated[
        Optional[int],
        "Cursor offset for frame_debugger_get_events (default 0, negative values treated as 0).",
    ] = None,
    markers: Annotated[
        Optional[list[str]], "1–8 distinct exact marker/counter names for window_start."
    ] = None,
    frame_count: Annotated[
        Optional[int],
        Field(strict=True, ge=1, le=600),
        "Window measurement frame limit (default 60).",
    ] = None,
    warmup_frames: Annotated[
        Optional[int], Field(strict=True, ge=0, le=120), "Excluded warmup frames (default 0)."
    ] = None,
    timeout_seconds: Annotated[
        Optional[int],
        Field(strict=True, ge=1, le=120),
        "Bounded window deadline (default 30 seconds).",
    ] = None,
    window_id: Annotated[
        Optional[str],
        "Session-scoped ID returned by window_start; required for status/result/cancel.",
    ] = None,
    include_samples: Annotated[
        Optional[bool],
        Field(strict=True),
        "Include bounded completed samples in window result (default false).",
    ] = None,
    targets: Annotated[
        Optional[list[str]],
        Field(min_length=1, max_length=32),
        "Loaded GameObject session ID strings for memory_aggregate; duplicate roots deduplicate.",
    ] = None,
    max_objects: Annotated[
        Optional[int],
        Field(strict=True, ge=1, le=1000000),
        "Read work budget: at most 1000000 objects; aggregate default250000, snapshot default1000000.",
    ] = None,
    include_references: Annotated[
        Optional[bool],
        Field(strict=True),
        "Include declared shared runtime resources in memory_aggregate (default true).",
    ] = None,
    include_objects: Annotated[
        Optional[bool],
        Field(strict=True),
        "Return bounded object rows for memory_aggregate (default false).",
    ] = None,
    object_limit: Annotated[
        Optional[int],
        Field(strict=True, ge=1, le=256),
        "Maximum returned object rows per list (default128); totals remain separately scoped.",
    ] = None,
    name_prefix: Annotated[
        Optional[str],
        Field(max_length=128),
        "Exact ordinal native object-name prefix for memory_analyze_snapshots; omit for all within budget.",
    ] = None,
) -> dict[str, Any]:
    action_lower = action.lower()
    if action_lower not in ALL_ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid actions: {', '.join(ALL_ACTIONS)}",
        }

    if action_lower in WINDOW_ACTIONS:
        error = _window_error(
            action_lower,
            category,
            markers,
            frame_count,
            warmup_frames,
            timeout_seconds,
            window_id,
            include_samples,
        )
        if error:
            return {"success": False, "message": error}

    if action_lower in MEMORY_ANALYSIS_ACTIONS:
        error = validate_memory_analysis(
            action_lower,
            targets=targets,
            max_objects=max_objects,
            include_references=include_references,
            include_objects=include_objects,
            object_limit=object_limit,
            name_prefix=name_prefix,
            snapshot_a=snapshot_a,
            snapshot_b=snapshot_b,
        )
        if error:
            return {"success": False, "message": error}

    unity_instance = await get_unity_instance_from_context(ctx)

    params_dict: dict[str, Any] = {"action": action_lower}

    param_map = {
        "category": category,
        "counters": counters,
        "object_path": object_path,
        "log_file": log_file,
        "enable_callstacks": enable_callstacks,
        "areas": areas,
        "snapshot_path": snapshot_path,
        "search_path": search_path,
        "snapshot_a": snapshot_a,
        "snapshot_b": snapshot_b,
        "page_size": page_size,
        "cursor": cursor,
        "markers": markers,
        "frame_count": frame_count,
        "warmup_frames": warmup_frames,
        "timeout_seconds": timeout_seconds,
        "window_id": window_id,
        "include_samples": include_samples,
        "targets": targets,
        "max_objects": max_objects,
        "include_references": include_references,
        "include_objects": include_objects,
        "object_limit": object_limit,
        "name_prefix": name_prefix,
    }
    for key, val in param_map.items():
        if val is not None:
            params_dict[key] = val

    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "manage_profiler", params_dict
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}


def _window_error(action, category, markers, frames, warmup, timeout, identity, samples):
    if action != "window_start":
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 64:
            return "window_id must be a nonempty session window ID (at most 64 characters)."
        return None
    if not isinstance(category, str) or not category.strip() or len(category) > 64:
        return "category must be a nonempty supported profiler category."
    if (
        not isinstance(markers, list)
        or not 1 <= len(markers) <= 8
        or any(not isinstance(name, str) or not name.strip() or len(name) > 256 for name in markers)
        or len(set(markers)) != len(markers)
    ):
        return "markers must contain 1–8 distinct nonempty strings of at most 256 characters."
    for name, value, minimum, maximum in (
        ("frame_count", frames, 1, 600),
        ("warmup_frames", warmup, 0, 120),
        ("timeout_seconds", timeout, 1, 120),
    ):
        if value is not None and (type(value) is not int or not minimum <= value <= maximum):
            return f"{name} must be an integer between {minimum} and {maximum}."
    if samples is not None and type(samples) is not bool:
        return "include_samples must be a boolean."
    return None
