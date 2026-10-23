---
title: manage_profiler
sidebar_label: manage_profiler
description: "Unity Profiler session control, counter reads, memory snapshots, and Frame Debugger."
---

# `manage_profiler`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `profiling` &nbsp;·&nbsp; **Module:** `services.tools.manage_profiler`

## Description

Unity Profiler session control, counter reads, memory snapshots, and Frame Debugger.

SESSION:
- profiler_start: Enable profiler, optionally record to .raw file (log_file, enable_callstacks)
- profiler_stop: Disable profiler, stop recording
- profiler_status: Get enabled state, active areas, recording path
- profiler_set_areas: Toggle ProfilerAreas on/off (areas dict)

COUNTERS:
- get_frame_timing: FrameTimingManager data (12 fields, synchronous)
- get_counters: Generic counter read by category + optional counter names (async, 1-frame wait)
- get_object_memory: Memory size of a specific object by path

INDEPENDENT WINDOWS:
- window_start: Bounded category/markers sampling with warmup and deadline; returns window_id
- window_status/window_result/window_cancel: Inspect, read terminal statistics, or dispose a window
Completed per-frame sums use native units; p95 is nearest-rank, missing is distinct from zero.

MEMORY SNAPSHOT (requires com.unity.memoryprofiler):
- memory_take_snapshot: Capture memory snapshot to file
- memory_list_snapshots: List available .snap files
- memory_compare_snapshots: Compare legacy file metadata

MEMORY ANALYSIS (native bytes are distinct from managed/retained size):
- memory_aggregate: Bounded hierarchy/shared runtime-resource sums with object deduplication
- memory_snapshot_reader_status: Exact supported reader package/format capability
- memory_analyze_snapshots: Actual typed native-object count/byte/identity deltas; requires Memory Profiler 1.1.12

FRAME DEBUGGER:
- frame_debugger_enable: Turn on Frame Debugger, report event count
- frame_debugger_disable: Turn off Frame Debugger
- frame_debugger_get_events: Get draw call events (paged, best-effort via reflection)

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes | The profiler action to perform. |
| `category` | `str \| None` | — | Profiler category name for get_counters (e.g. Render, Scripts, Memory, Physics). |
| `counters` | `list[str] \| None` | — | Specific counter names for get_counters. Omit to read all in category. |
| `object_path` | `str \| None` | — | Scene hierarchy or asset path for get_object_memory. |
| `log_file` | `str \| None` | — | Path to .raw file for profiler_start recording. Project-relative or absolute inside the project or Unity temporary cache directory. |
| `enable_callstacks` | `bool \| None` | — | Enable allocation callstacks for profiler_start. |
| `areas` | `dict[str, bool] \| None` | — | Dict of area name to bool for profiler_set_areas. |
| `snapshot_path` | `str \| None` | — | Output path for memory_take_snapshot. Project-relative or absolute inside the project or Unity temporary cache directory. |
| `search_path` | `str \| None` | — | Search directory for memory_list_snapshots. Project-relative or absolute inside the project or Unity temporary cache directory. |
| `snapshot_a` | `str \| None` | — | First snapshot path for memory_compare_snapshots. Project-relative or absolute inside the project or Unity temporary cache directory. |
| `snapshot_b` | `str \| None` | — | Second snapshot path for memory_compare_snapshots. Project-relative or absolute inside the project or Unity temporary cache directory. |
| `page_size` | `int \| None` | — | Page size for frame_debugger_get_events (default 50, clamped to 1..500). |
| `cursor` | `int \| None` | — | Cursor offset for frame_debugger_get_events (default 0, negative values treated as 0). |
| `markers` | `list[str] \| None` | — | 1–8 distinct exact marker/counter names for window_start. |
| `frame_count` | `int \| None` | — | Window measurement frame limit (default 60). |
| `warmup_frames` | `int \| None` | — | Excluded warmup frames (default 0). |
| `timeout_seconds` | `int \| None` | — | Observation budget: snapshot 1..86400 (default 300), sampling window 1..120 (default 30). |
| `window_id` | `str \| None` | — | Session-scoped ID returned by window_start; required for status/result/cancel. |
| `include_samples` | `bool \| None` | — | Include bounded completed samples in window result (default false). |
| `targets` | `list[str] \| None` | — | Loaded GameObject session ID strings for memory_aggregate; duplicate roots deduplicate. |
| `max_objects` | `int \| None` | — | Read work budget: at most 1000000 objects; aggregate default250000, snapshot default1000000. |
| `include_references` | `bool \| None` | — | Include declared shared runtime resources in memory_aggregate (default true). |
| `include_objects` | `bool \| None` | — | Return bounded object rows for memory_aggregate (default false). |
| `object_limit` | `int \| None` | — | Maximum returned object rows per list (default128); totals remain separately scoped. |
| `name_prefix` | `str \| None` | — | Exact ordinal native object-name prefix for memory_analyze_snapshots; omit for all within budget. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

