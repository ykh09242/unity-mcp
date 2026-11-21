"""Fixtures, admissible calls and evidence for the controlled observation-choice study."""

import math
from functools import lru_cache
from pathlib import Path
import re
import sys

from tools.experiments import blind_tool_actions as actions


STUDY = "observation-choices"
TASKS = ("compile-current", "active-scene", "compile-outcome", "pointer-order")
STATE = "inspect_editor_state"
POINTER = "inspect_ugui_hit_order"
JOB = "get_compile_job"
OPEN_GRAPH_ROUTES = {"inspect_open_shader_graph", "manage_shader.inspect_open_graph"}
OPEN_GRAPH_LIMITS = {"no_matching_window", "open_model_unavailable", "window_lookup_unavailable"}


def open_graph_observation(path, data):
    if not path or data.get("path") != path:
        return {}
    status = data.get("status")
    if type(status) is str and status in OPEN_GRAPH_LIMITS:
        return {"status": status}
    nodes, edges, dirty = (
        data.get("nodeCount"),
        data.get("edgeCount"),
        data.get("hasUnsavedChanges"),
    )
    if (
        status != "open_model"
        or type(nodes) is not int
        or type(edges) is not int
        or min(nodes, edges) < 0
        or type(dirty) is not bool
    ):
        return {}
    return {
        "status": status,
        "data_state": "open",
        "nodes": nodes,
        "edges": edges,
        "has_unsaved_changes": dirty,
    }


@lru_cache(maxsize=1)
def _contracts():
    source = str(Path(__file__).resolve().parents[2] / "Server" / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from services.registry.action_catalog import (
        ACTIVE_SCENE_OBSERVATION,
        COMPILE_JOB_OBSERVATION,
        EDITOR_STATE_OBSERVATION,
        POINTER_ORDER_OBSERVATION,
    )
    from services.registry.observation_predicates import evaluate_observation

    return evaluate_observation, {
        JOB: (COMPILE_JOB_OBSERVATION, "compile_job.outcome"),
        POINTER: (POINTER_ORDER_OBSERVATION, "pointer_hit_order"),
        "compile-current": (EDITOR_STATE_OBSERVATION, "compilation.is_compiling"),
        "active-scene": (EDITOR_STATE_OBSERVATION, "active_scene.name"),
        "scene-reader": (ACTIVE_SCENE_OBSERVATION, "active_scene.name"),
    }


def establishes(contract, data):
    evaluate, contracts = _contracts()
    descriptor, field = contracts[contract]
    return evaluate(descriptor, {"success": True, "data": data}, [field])[field]


def fixture_availability(fixture, state):
    """Only an independent pre-prompt fixture receipt can declare a conditional task positive."""
    missing = missing_fixture(fixture)
    if missing:
        return missing
    task = fixture.get("task")
    if task not in ("compile-outcome", "pointer-order", "graph-open"):
        return "positive"
    proof = ((state or {}).get("observation_fixtures") or {}).get(task)
    if not isinstance(proof, dict):
        return "unverified"
    reply = proof.get("reply")
    if (
        not isinstance(reply, dict)
        or reply.get("success") is not True
        or not isinstance(reply.get("data"), dict)
    ):
        return "unavailable"
    args = proof.get("arguments")
    if task == "graph-open":
        path = fixture.get("graph_asset")
        if (
            not path
            or not isinstance(args, dict)
            or args.get("path") != path
            or reply["data"].get("path") != path
        ):
            return "unverified"
        observation = open_graph_observation(path, reply["data"])
        return "positive" if observation.get("data_state") == "open" else "unavailable"
    route = JOB if task == "compile-outcome" else POINTER
    if not isinstance(args, dict) or not permitted(route, args, fixture):
        return "unverified"
    data = reply["data"]
    if route == JOB and data.get("compile_id") != fixture["compile_id"]:
        return "unverified"
    if route == POINTER and data.get("screen_position") != fixture["point"]:
        return "unverified"
    return "positive" if establishes(route, data) else "unavailable"


def observation_fixture(args, task):
    if task == "graph-open":
        return {"task": task, "graph_asset": args.graph_asset}
    if task == actions.UI_TASK:
        return {"task": task, "ui_asset": getattr(args, "ui_asset", None)}
    if task in {actions.CAMERA_TASK, actions.MENU_TASK}:
        return {"task": task}
    if task == "compile-outcome":
        return {"task": task, "compile_id": getattr(args, "existing_compile_id", None)}
    if task == "pointer-order":
        return {
            "task": task,
            "target": getattr(args, "pointer_target", None),
            "point": getattr(args, "pointer_position", None),
        }
    return {"task": task} if task in TASKS else {}


def missing_fixture(fixture):
    if fixture.get("task") == "compile-outcome" and not fixture.get("compile_id"):
        return "existing_compile_id_unavailable"
    if fixture.get("task") == "pointer-order" and (
        not fixture.get("target") or fixture.get("point") is None
    ):
        return "pointer_target_or_position_unavailable"
    return None


def permitted(route, args, fixture):
    """Return a decision only for routes whose read scope depends on the selected task."""
    action = actions.permitted(route, args, fixture)
    if action is not None:
        return action
    if route == JOB:
        job = fixture.get("compile_id")
        return bool(
            fixture.get("task") == "compile-outcome"
            and isinstance(job, str)
            and re.fullmatch("[0-9a-f]{32}", job)
            and args.get("compile_id") == job
            and args.get("observe_source", False) is False
            and set(args) <= {"compile_id", "unity_instance", "observe_source"}
        )
    if route == POINTER or (fixture and route == "manage_ugui.diagnose_raycast"):
        position = args.get("screen_position")
        allowed = {"target", "screen_position", "unity_instance"}
        if route != POINTER:
            allowed.update({"action", "strict_read", "allow_custom_filters"})
            if (
                args.get("strict_read") is not True
                or args.get("allow_custom_filters", False) is not False
            ):
                return False
        return (
            fixture.get("task") == "pointer-order"
            and bool(fixture.get("target"))
            and type(args.get("target")) in (int, str)
            and str(args["target"]) == str(fixture["target"])
            and isinstance(position, list)
            and len(position) == 2
            and all(type(n) in (int, float) and math.isfinite(n) and n >= 0 for n in position)
            and position == fixture.get("point")
            and set(args) <= allowed
        )
    return None
