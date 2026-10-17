"""Closed current-camera projection publication, without a Unity process."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def sample():
    return {
        "kind": "ugui_projection_diagnostics",
        "schema_version": 1,
        "framework": "ugui",
        "mode": "edit",
        "frame": 0,
        "status": "unavailable",
        "unavailable_reason": "camera_unassigned",
        "evidence": {
            "observation": "current_camera_properties",
            "geometry_space": "camera_viewport_normalized",
            "pixel_origin": "bottom_left",
            "quad_source": "current_world_corners",
            "layout_method": "current_unforced",
            "matrix_order": "column_major",
            "rasterization": "not_observed",
            "mask_clipping": "not_evaluated",
            "occlusion": "not_evaluated",
            "interaction": "not_evaluated",
            "profile": None,
        },
        "context": None,
        "nodes": [],
        "traversal": {
            "max_nodes": 200,
            "max_depth": 10,
            "max_evaluations": 20000,
            "max_characters": 65536,
            "nodes_visited": 0,
            "nodes_emitted": 0,
            "evaluations": 0,
            "evaluation_counts": {
                "camera_observations": 0,
                "ancestor_steps": 0,
                "node_admissions": 0,
                "child_fetches": 0,
                "world_corners": 0,
                "clip_distances": 0,
                "intersections": 0,
                "projected_vertices": 0,
            },
            "characters": 0,
            "truncated": False,
            "reasons": [],
        },
        "limitations": [],
    }


def complete_sample():
    result = sample()
    result.update(status="complete", unavailable_reason=None)
    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    result["context"] = {
        "target_id": 1,
        "canvas_id": 2,
        "root_canvas_id": 2,
        "render_mode": "world_space",
        "camera_id": 3,
        "assigned_camera_id": None,
        "camera_selection": "explicit_world_view",
        "camera_enabled": False,
        "camera_active": True,
        "canvas_enabled": True,
        "culling_mask": -1,
        "allow_dynamic_resolution": False,
        "non_jittered_projection_mismatch": False,
        "orthographic": True,
        "stereo_enabled": False,
        "near_clip": 0.3,
        "far_clip": 30.0,
        "scale_factor": 1.0,
        "reference_pixels_per_unit": 100.0,
        "view_matrix": identity.copy(),
        "projection_matrix": identity.copy(),
        "camera_rect": [0.0, 0.0, 1.0, 1.0],
        "camera_pixel_rect": [0.0, 0.0, 320.0, 240.0],
        "pixel_width": 320,
        "pixel_height": 240,
        "scaled_pixel_width": 320,
        "scaled_pixel_height": 240,
        "target_name": "Root",
        "canvas_name": "Root",
        "camera_name": "View",
        "metadata_truncated_fields": [],
        "output": {
            "kind": "render_texture",
            "route_basis": "camera_target_properties",
            "target_display": 0,
            "width": 320,
            "height": 240,
            "viewport_pixels": [0.0, 0.0, 320.0, 240.0],
            "camera_rect_ignored": True,
            "texture_id": 4,
            "active": True,
            "dimension_source": "attached_render_texture",
            "pixel_status": "available",
            "texture_dimension": "Tex2D",
            "volume_depth": 1,
            "dynamic_scale": False,
        },
    }
    result["nodes"] = [
        {
            "diagnostic_path": "",
            "parent_index": None,
            "sibling_index": 0,
            "instance_id": 1,
            "name": "Root",
            "truncated_fields": [],
            "active": True,
            "layer_in_culling_mask": True,
            "world_corners": [
                [-1.0, -1.0, 5.0],
                [-1.0, 1.0, 5.0],
                [1.0, 1.0, 5.0],
                [1.0, -1.0, 5.0],
            ],
            "depth_range": [5.0, 5.0],
            "camera_plane_relation": "in_front",
            "frustum_relation": "inside",
            "cut_planes": [],
            "rejected_by": None,
            "clipped_viewport_polygon": [[0.25, 0.25], [0.25, 0.75], [0.75, 0.75], [0.75, 0.25]],
            "unclipped_viewport_bounds": [0.25, 0.25, 0.5, 0.5],
            "clipped_target_pixel_bounds": [80.0, 60.0, 160.0, 120.0],
            "pixel_status": "available",
            "boundary_clamped": False,
            "reasons": [],
        }
    ]
    result["traversal"].update(nodes_visited=1, nodes_emitted=1, characters=16)
    result["traversal"]["evaluation_counts"]["node_admissions"] = 1
    result["traversal"]["evaluation_counts"]["world_corners"] = 4
    result["traversal"]["evaluations"] = 5
    return result


def nonfull_texture_sample():
    data = complete_sample()
    data["status"] = "partial"
    context = dict(data["context"])
    data["context"] = context
    context["camera_rect"] = [0.1, 0.2, 0.4, 0.5]
    context["output"].update(
        pixel_status="unavailable", viewport_pixels=None, camera_rect_ignored=None
    )
    data["nodes"][0].update(pixel_status="unavailable", clipped_target_pixel_bounds=None)
    return data


def decoder():
    return importlib.import_module(
        "models.ugui_projection_diagnostics"
    ).validate_ugui_projection_diagnostics


def test_valid_complete_and_unavailable_contracts():
    for value in (sample(), complete_sample()):
        assert decoder()(value) == value


def test_nonfull_texture_preserves_normalized_geometry_without_pixel_claim():
    data = nonfull_texture_sample()
    result = decoder()(data)
    assert result == data
    assert result["nodes"][0]["clipped_viewport_polygon"]
    assert result["context"]["output"]["camera_rect_ignored"] is None


@pytest.mark.parametrize("claim", [True, False, 0, "unknown"])
def test_nonfull_texture_refuses_an_invented_camera_rect_ignore_claim(claim):
    data = nonfull_texture_sample()
    context = dict(data["context"])
    data["context"] = context
    context["output"]["camera_rect_ignored"] = claim
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


def test_nonfull_texture_cannot_publish_full_target_pixels_as_available():
    data = complete_sample()
    context = dict(data["context"])
    data["context"] = context
    context["camera_rect"] = [0.1, 0.2, 0.4, 0.5]
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


def test_full_texture_cannot_use_the_unknown_ignore_flag_escape():
    data = complete_sample()
    context = dict(data["context"])
    data["context"] = context
    context["output"]["camera_rect_ignored"] = None
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


def test_unknown_ignore_flag_is_not_accepted_for_display_routes():
    data = nonfull_texture_sample()
    context = dict(data["context"])
    data["context"] = context
    context["output"].update(
        kind="display",
        texture_id=None,
        texture_dimension=None,
        volume_depth=None,
        dynamic_scale=None,
        dimension_source="current_screen",
    )
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("kind", "old_preview"),
        ("framework", "uitoolkit"),
        ("status", "complete"),
        ("frame", True),
        ("PRIVATE_SENTINEL", "PRIVATE_SENTINEL"),
    ],
)
def test_closed_marker_and_readiness_refusal(field, value):
    data = sample()
    data[field] = value
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


@pytest.mark.parametrize(
    "change",
    [
        "behind_bounds",
        "polygon",
        "parent",
        "counts",
        "characters",
        "pixels",
        "unknown_complete",
        "profile",
    ],
)
def test_complete_evidence_cannot_contradict_geometry(change):
    data = complete_sample()
    node = data["nodes"][0]
    if change == "behind_bounds":
        node.update(camera_plane_relation="behind", depth_range=[-5.0, -5.0])
    elif change == "polygon":
        node["clipped_viewport_polygon"][0][0] = 2.0
    elif change == "parent":
        node["parent_index"] = 0
    elif change == "counts":
        data["traversal"]["evaluations"] += 1
    elif change == "characters":
        data["traversal"]["characters"] -= 1
    elif change == "pixels":
        node["clipped_target_pixel_bounds"][0] += 5
    elif change == "unknown_complete":
        node["frustum_relation"] = "unknown"
        node["clipped_viewport_polygon"] = []
        node["clipped_target_pixel_bounds"] = None
        node["pixel_status"] = "unavailable"
    else:
        data["evidence"]["profile"] = {"width": 320}
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


def test_new_action_old_peer_response_is_refused_before_publication(monkeypatch):
    module = importlib.import_module("services.tools.manage_ugui")

    async def preflight(*_args, **_kwargs):
        return None

    async def instance(_ctx):
        return None

    async def read(*_args, **_kwargs):
        return {"success": True, "data": {"PRIVATE_SENTINEL": "old preview"}}

    monkeypatch.setattr(module, "preflight", preflight)
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", read)
    result = asyncio.run(
        module.manage_ugui(ctx=None, action="diagnose_projection", target="Root", camera="View")
    )
    assert result == {"success": False, "message": "unsupported_projection_response"}


@pytest.mark.parametrize(
    "options",
    [
        {"target": None},
        {"target": True},
        {"camera": False},
        {"camera": ""},
        {"camera": "\ud800"},
        {"target": "n" * 4097},
        {"max_depth": True},
        {"max_depth": 25},
        {"max_evaluations": 0},
        {"max_characters": 262145},
        {"parent": "PRIVATE_SENTINEL"},
        {"properties": {"text": "PRIVATE_SENTINEL"}},
        {"resolutions": []},
    ],
)
def test_new_action_rejects_invalid_options_before_any_io(monkeypatch, options):
    module = importlib.import_module("services.tools.manage_ugui")

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("invalid projection request reached I/O")

    for name in (
        "preflight",
        "get_unity_instance_from_context",
        "send_with_unity_instance",
        "send_mutation",
    ):
        monkeypatch.setattr(module, name, unexpected)
    request = {"ctx": None, "action": "diagnose_projection", "target": "Root", **options}
    response = asyncio.run(module.manage_ugui(**request))
    assert response == {"success": False, "message": "invalid_projection_request"}
    assert "PRIVATE_SENTINEL" not in json.dumps(response)


@pytest.mark.parametrize("payload_kind", ["camera_unassigned", "nonfull_texture"])
def test_actual_sdk_projection_schema_no_io_and_closed_publication(tmp_path, payload_kind):
    program = textwrap.dedent(r"""
        import asyncio, importlib, json, os
        from pathlib import Path
        from fastmcp import FastMCP, Client
        from mcp.types import CallToolResult
        from models.response_limits import response_size
        from services.registry import get_registered_tools
        module=importlib.import_module("services.tools.manage_ugui")
        payload=json.loads(Path(os.environ["UI07_PAYLOAD_PATH"]).read_text())
        calls=[]; reply={"value":None}
        async def preflight(*args,**kwargs): return None
        async def instance(ctx): return None
        async def send(*args,**kwargs):
            calls.append(args[3]); return reply["value"] if reply["value"] is not None else {"success":True,"data":payload}
        async def mutation(*args,**kwargs): raise AssertionError("projection used mutation transport")
        module.preflight=preflight;module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send;module.send_mutation=mutation
        entry=next(e for e in get_registered_tools() if e["name"]=="manage_ugui")
        assert entry["group"]=="ui"
        assert entry["kwargs"]["annotations"].destructive_hint is True
        assert entry["kwargs"]["annotations"].read_only_hint is False
        server=FastMCP("ui07")
        server.tool(name="manage_ugui",description=entry["description"],**entry["kwargs"])(module.manage_ugui)
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    for disabled in ("group","tool"):
                        if disabled=="group":server.disable(tags={"group:ui"},components={"tool"})
                        else:server.disable(names={"manage_ugui"},components={"tool"})
                        assert "manage_ugui" not in {t.name for t in await client.list_tools()}
                        before=len(calls)
                        try:r=await client.call_tool("manage_ugui",{"action":"diagnose_projection","target":"Root"})
                        except Exception:pass
                        else:assert r.is_error
                        assert len(calls)==before
                        if disabled=="group":server.enable(tags={"group:ui"},components={"tool"})
                        else:server.enable(names={"manage_ugui"},components={"tool"})
                    tool=next(t for t in await client.list_tools() if t.name=="manage_ugui")
                    schema=tool.input_schema if hasattr(tool,"input_schema") else tool.inputSchema
                    assert "diagnose_projection" in schema["properties"]["action"]["enum"]
                    assert "World Space" in schema["properties"]["camera"]["description"]
                    for key,low,high in (("max_depth",0,24),("max_evaluations",1,200000),("max_characters",1,262144)):
                        prop=schema["properties"][key]
                        integer=next(p for p in prop.get("anyOf",[prop]) if p.get("type")=="integer")
                        assert integer["minimum"]==low and integer["maximum"]==high
                        assert "diagnose_projection" in prop["description"]
                        for value in (low-1,high+1,True,1.5,"2",[]):
                            before=len(calls)
                            try:await client.call_tool("manage_ugui",{"action":"diagnose_projection","target":"Root",key:value})
                            except Exception:pass
                            else:raise AssertionError((key,value))
                            assert len(calls)==before
                    request={"action":"diagnose_projection","target":"Root","camera":"View"}
                    for raw,expected in (({"success":True,"data":{"PRIVATE_SENTINEL":"old"}},"unsupported_projection_response"),({"success":False,"error":"PRIVATE_SENTINEL","data":{"private":"PRIVATE_SENTINEL"}},"projection_unavailable")):
                        reply["value"]=raw
                        result=await client.call_tool("manage_ugui",request)
                        assert result.structured_content=={"success":False,"message":expected}
                        wire=CallToolResult(content=result.content,structuredContent=result.structured_content,isError=result.is_error,_meta=result.meta).model_dump(mode="json",exclude_none=True)
                        assert "PRIVATE_SENTINEL" not in json.dumps(wire)
                    reply["value"]=None
                    result=await client.call_tool("manage_ugui",request)
                    assert result.structured_content=={"success":True,"data":payload}
                    assert calls[-1]=={"action":"diagnose_projection","target":"Root","camera":"View","include_inactive":False,"max_nodes":200,"max_depth":10,"max_evaluations":20000,"max_characters":65536}
                    wire=CallToolResult(content=result.content,structuredContent=result.structured_content,isError=result.is_error,_meta=result.meta).model_dump(mode="json",exclude_none=True)
                    assert response_size(wire) is not None
                    print("UI07_SDK_WIRE",mode)
        asyncio.run(main())
    """)
    payload_path = tmp_path / "payload.json"
    payload = sample() if payload_kind == "camera_unassigned" else nonfull_texture_sample()
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "UI07_PAYLOAD_PATH": str(payload_path),
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("UI07_SDK_WIRE") == 2


@pytest.mark.parametrize(
    "code",
    ["camera_required", "camera_canvas_mismatch", "invalid_projection_request", "PRIVATE_SENTINEL"],
)
def test_projection_peer_errors_are_closed_constant_codes(monkeypatch, code):
    module = importlib.import_module("services.tools.manage_ugui")

    async def preflight(*_args, **_kwargs):
        return None

    async def instance(_ctx):
        return None

    async def read(*_args, **_kwargs):
        return {"success": False, "error": code, "data": {"private": "PRIVATE_SENTINEL"}}

    monkeypatch.setattr(module, "preflight", preflight)
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", read)
    result = asyncio.run(module.manage_ugui(ctx=None, action="diagnose_projection", target="Root"))
    assert result == {
        "success": False,
        "message": "projection_unavailable" if code == "PRIVATE_SENTINEL" else code,
    }


def test_projection_peer_cannot_ignore_requested_work_budget(monkeypatch):
    module = importlib.import_module("services.tools.manage_ugui")

    async def preflight(*_args, **_kwargs):
        return None

    async def instance(_ctx):
        return None

    async def read(*_args, **_kwargs):
        return {"success": True, "data": sample()}

    monkeypatch.setattr(module, "preflight", preflight)
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", read)
    result = asyncio.run(
        module.manage_ugui(ctx=None, action="diagnose_projection", target="Root", max_depth=0)
    )
    assert result == {"success": False, "message": "unsupported_projection_response"}


@pytest.mark.parametrize(
    "change",
    [
        "stereo_true",
        "stereo_integer",
        "singular_matrix",
        "noncanonical_matrix",
        "duplicate_path",
        "concave_polygon",
        "infinite_depth",
    ],
)
def test_decoder_refuses_unsupported_conventions_and_malformed_geometry(change):
    data = complete_sample()
    context = dict(data["context"])
    data["context"] = context
    if change == "stereo_true":
        context["stereo_enabled"] = True
    elif change == "stereo_integer":
        context["stereo_enabled"] = 0
    elif change == "singular_matrix":
        context["projection_matrix"] = [0.0] * 16
    elif change == "noncanonical_matrix":
        context["view_matrix"][3] = 1.0
    elif change == "duplicate_path":
        data["nodes"].append(data["nodes"][0].copy())
        data["traversal"].update(nodes_visited=2, nodes_emitted=2, characters=20)
        data["traversal"]["evaluation_counts"]["node_admissions"] = 2
        data["traversal"]["evaluations"] += 1
    elif change == "concave_polygon":
        data["nodes"][0]["clipped_viewport_polygon"][2] = [0.3, 0.3]
    else:
        data["nodes"][0]["depth_range"][1] = float("inf")
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


def hierarchy_sample():
    data = complete_sample()
    child = json.loads(json.dumps(data["nodes"][0]))
    child.update(diagnostic_path="/0", parent_index=0, sibling_index=0, instance_id=10)
    grandchild = json.loads(json.dumps(child))
    grandchild.update(diagnostic_path="/0/0", parent_index=1, instance_id=11)
    data["nodes"].extend([child, grandchild])
    data["traversal"].update(nodes_visited=3, nodes_emitted=3, characters=30, evaluations=17)
    data["traversal"]["evaluation_counts"].update(
        node_admissions=3, child_fetches=2, world_corners=12
    )
    return data


@pytest.mark.parametrize("change", ["behind_survivor", "root_id", "nearest_emitted_parent"])
def test_review_contradictions_are_refused(change):
    data = hierarchy_sample() if change == "nearest_emitted_parent" else complete_sample()
    if change == "behind_survivor":
        data["nodes"][0].update(
            depth_range=[-2.0, -1.0], camera_plane_relation="behind", unclipped_viewport_bounds=None
        )
    elif change == "root_id":
        data["nodes"][0]["instance_id"] = 99
    else:
        data["nodes"][2]["parent_index"] = 0
    with pytest.raises(ValueError, match="^unsupported_projection_response$"):
        decoder()(data)


def test_nearest_emitted_parent_allows_a_real_plain_transform_gap():
    data = hierarchy_sample()
    del data["nodes"][1]
    data["nodes"][1]["parent_index"] = 0
    data["traversal"].update(nodes_emitted=2, characters=24)
    assert decoder()(data) == data
