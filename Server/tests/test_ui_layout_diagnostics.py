"""Closed current-panel layout diagnostics and actual SDK publication."""

import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def sample():
    """Small unavailable response; actual native artifacts are verified separately."""
    return {
        "kind": "uitoolkit_layout_diagnostics",
        "schema_version": 1,
        "framework": "uitoolkit",
        "mode": "edit",
        "component_type": "ui_document",
        "component_instance_id": 2,
        "target_instance_id": 1,
        "metadata_truncated_fields": [],
        "game_object": "Owner",
        "geometry_space": "panel_aabb",
        "evidence": {
            "path_domain": "physical_indices",
            "layout_method": "current_observation",
            "clip_method": "rectangular_engine_style_v1",
            "overlap_method": "sibling_aabb_candidates",
            "flat_panel": None,
        },
        "layout_status": "panel_unavailable",
        "status": "unavailable",
        "nodes": [],
        "findings": [],
        "traversal": {
            "max_depth": 10,
            "max_nodes": 200,
            "max_evaluations": 5000,
            "max_characters": 65536,
            "max_pairs": 2000,
            "max_findings": 200,
            "returned_nodes": 0,
            "evaluations": 0,
            "characters": 5,
            "ancestor_inspections": 0,
            "node_admissions": 0,
            "child_fetches": 0,
            "style_probes": 0,
            "pairs_considered": 0,
            "findings_retained": 0,
            "complete": False,
            "truncated": False,
            "reasons": [],
        },
        "stages": {"clipping": "unavailable", "overlap": "unavailable"},
        "limitations": ["Current observation only."],
    }


def test_closed_diagnostics_response_and_constant_errors():
    module = importlib.import_module("models.ui_layout_diagnostics")
    data = sample()
    assert module.validate_ui_layout_diagnostics(data) == data
    for mutate in (
        lambda d: d.update(schema_version=True),
        lambda d: d.update(schema_version=2),
        lambda d: d.update(kind="snapshot"),
        lambda d: d.update(status="complete"),
        lambda d: d["traversal"].update(evaluations=1),
        lambda d: d["traversal"].update(characters=999999),
        lambda d: d.update(PRIVATE_SENTINEL="value"),
    ):
        invalid = copy.deepcopy(data)
        mutate(invalid)
        with pytest.raises(ValueError) as error:
            module.validate_ui_layout_diagnostics(invalid)
        assert str(error.value) == "unsupported_diagnostics_response"


def test_diagnose_rejects_wrong_old_peer_success(monkeypatch):
    module = importlib.import_module("services.tools.manage_ui")

    async def instance(_ctx):
        return None

    async def send(*_args, **_kwargs):
        return {"success": True, "data": {"tree": {"text": "PRIVATE_SENTINEL"}}}

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    result = asyncio.run(module.manage_ui(ctx=None, action="diagnose", target="123"))
    assert result == {"success": False, "message": "unsupported_diagnostics_response"}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_pairs", -1),
        ("max_pairs", True),
        ("max_pairs", 20001),
        ("max_findings", 0),
        ("max_findings", 2001),
        ("element_path", ""),
        ("text", "PRIVATE_SENTINEL"),
    ],
)
def test_diagnostics_invalid_request_never_reaches_io(monkeypatch, key, value):
    module = importlib.import_module("services.tools.manage_ui")

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("invalid request reached I/O")

    monkeypatch.setattr(module, "get_unity_instance_from_context", unexpected)
    result = asyncio.run(
        module.manage_ui(ctx=None, action="diagnose", target="123", **{key: value})
    )
    assert result["success"] is False
    assert "PRIVATE_SENTINEL" not in json.dumps(result)


def test_actual_sdk_layout_diagnostics(tmp_path):
    program = textwrap.dedent(r"""
        import asyncio, importlib, json, os
        from pathlib import Path
        from fastmcp import FastMCP, Client
        from mcp.types import CallToolResult
        from models.response_limits import response_size, bounded_json_text, MAX_RESPONSE_BYTES, MAX_RESPONSE_DEPTH, MAX_RESPONSE_NODES
        from services.registry import get_registered_tools
        module = importlib.import_module("services.tools.manage_ui")
        payload = json.loads(Path(os.environ["UI06_PAYLOAD_PATH"]).read_text(encoding="utf-8"))
        calls=[]
        reply={"override": None}
        async def instance(ctx): return None
        async def send(*args, **kwargs):
            calls.append(args[3]); return reply["override"] if reply["override"] is not None else {"success":True,"data":payload}
        async def mutation(*args, **kwargs): raise AssertionError("diagnose used mutation transport")
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        module.send_mutation=mutation
        entry=next(e for e in get_registered_tools() if e["name"]=="manage_ui")
        assert entry["group"]=="ui" and entry["kwargs"]["annotations"].destructive_hint is True
        server=FastMCP("ui06")
        server.tool(name="manage_ui",description=entry["description"],**entry["kwargs"])(module.manage_ui)
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    for disabled in ("group", "tool"):
                        if disabled == "group": server.disable(tags={"group:ui"},components={"tool"})
                        else: server.disable(names={"manage_ui"},components={"tool"})
                        assert "manage_ui" not in {t.name for t in await client.list_tools()}
                        before=len(calls)
                        try:
                            refused=await client.call_tool("manage_ui",{"action":"diagnose","target":"123"})
                        except Exception: pass
                        else: assert refused.is_error
                        assert len(calls)==before
                        if disabled == "group": server.enable(tags={"group:ui"},components={"tool"})
                        else: server.enable(names={"manage_ui"},components={"tool"})
                    t=next(t for t in await client.list_tools() if t.name=="manage_ui")
                    schema=t.input_schema if hasattr(t,"input_schema") else t.inputSchema
                    assert "diagnose" in schema["properties"]["action"]["enum"]
                    for key,low,high in (("max_pairs",0,20000),("max_findings",1,2000)):
                        prop=schema["properties"][key]
                        integer=next(p for p in prop.get("anyOf",[prop]) if p.get("type")=="integer")
                        assert integer["minimum"]==low and integer["maximum"]==high
                        assert "diagnose" in prop["description"]
                        for value in (low-1,high+1,True,1.5,"2",[]):
                            before=len(calls)
                            try: await client.call_tool("manage_ui",{"action":"diagnose","target":"123",key:value})
                            except Exception: pass
                            else: raise AssertionError((key,value))
                            assert len(calls)==before
                    request={"action":"diagnose","target":"123",**{key:payload["traversal"][key] for key in ("max_depth","max_nodes","max_evaluations","max_characters","max_pairs","max_findings")}}
                    for rejected, expected in (
                        ({"success":True,"data":{"kind":"old","PRIVATE_SENTINEL":"not published"}}, "unsupported_diagnostics_response"),
                        ({"success":True,"data":{**payload,"schema_version":2,"PRIVATE_SENTINEL":"not published"}}, "unsupported_diagnostics_response"),
                        ({"success":False,"error":"PRIVATE_SENTINEL","data":{"private":"PRIVATE_SENTINEL"}}, "diagnostics_unavailable"),
                        ({"success":False,"error":"unsupported_ui_component"}, "unsupported_ui_component"),
                    ):
                        reply["override"]=rejected
                        before=len(calls)
                        refusal=await client.call_tool("manage_ui",request)
                        assert len(calls)==before+1
                        assert refusal.structured_content=={"success":False,"message":expected}
                        refused_wire=CallToolResult(content=refusal.content,structuredContent=refusal.structured_content,isError=refusal.is_error,_meta=refusal.meta).model_dump(mode="json",exclude_none=True)
                        assert "PRIVATE_SENTINEL" not in json.dumps(refused_wire)
                    reply["override"]=None
                    result=await client.call_tool("manage_ui",request)
                    assert result.structured_content=={"success":True,"data":payload}
                    assert calls[-1]["maxPairs"]==payload["traversal"]["max_pairs"]
                    wire=CallToolResult(content=result.content,structuredContent=result.structured_content,isError=result.is_error,_meta=result.meta).model_dump(mode="json",exclude_none=True)
                    assert response_size(wire) is not None
                    assert bounded_json_text(json.dumps(wire,ensure_ascii=False),max_bytes=MAX_RESPONSE_BYTES,max_depth=MAX_RESPONSE_DEPTH,max_nodes=MAX_RESPONSE_NODES) is not None
                    print("UI06_SDK_WIRE",mode,len(json.dumps(wire,ensure_ascii=False).encode()))
        asyncio.run(main())
    """)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(sample()), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "UI06_PAYLOAD_PATH": str(payload_path),
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("UI06_SDK_WIRE") == 2


def test_diagnostics_response_cannot_ignore_requested_budget(monkeypatch):
    module = importlib.import_module("services.tools.manage_ui")

    async def instance(_ctx):
        return None

    async def read(*_args, **_kwargs):
        return {"success": True, "data": sample()}

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", read)
    result = asyncio.run(module.manage_ui(ctx=None, action="diagnose", target="123", max_nodes=1))
    assert result == {"success": False, "message": "unsupported_diagnostics_response"}


def complete_sample():
    data = sample()
    data.update(layout_status="current", status="complete")
    data["evidence"]["flat_panel"] = True
    data["stages"] = {"clipping": "complete", "overlap": "complete"}
    data["traversal"].update(
        returned_nodes=1,
        evaluations=2,
        characters=9,
        node_admissions=1,
        style_probes=1,
        complete=True,
    )
    data["nodes"] = [
        {
            "diagnostic_path": "",
            "parent_index": None,
            "sibling_index": 0,
            "name": "R",
            "type": "Box",
            "truncated_fields": [],
            "layout_status": "current",
            "bounds": [0, 0, 100, 100],
            "displayed": True,
            "visibility": "visible",
            "effective_opacity": 1,
            "suppression": [],
            "overflow": "visible",
            "clips_children": False,
            "clipping": "not_clipped",
            "effective_clip": [0, 0, 100, 100],
        }
    ]
    return data


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("overflow", "unknown"),
        ("clips_children", None),
        ("displayed", None),
        ("visibility", "unknown"),
        ("effective_opacity", None),
        ("bounds", None),
    ],
)
def test_complete_diagnostics_cannot_hide_required_unknown_evidence(key, value):
    module = importlib.import_module("models.ui_layout_diagnostics")
    valid = complete_sample()
    assert module.validate_ui_layout_diagnostics(valid) == valid
    valid["nodes"][0][key] = value
    with pytest.raises(ValueError, match="^unsupported_diagnostics_response$"):
        module.validate_ui_layout_diagnostics(valid)


@pytest.mark.parametrize(
    "code",
    [
        "unsupported_ui_component",
        "unsupported_diagnostics_parameter",
        "invalid_or_ambiguous_diagnostics_target",
        "PRIVATE_SENTINEL",
    ],
)
def test_native_refusals_keep_only_known_bounded_codes(monkeypatch, code):
    module = importlib.import_module("services.tools.manage_ui")

    async def instance(_ctx):
        return None

    async def read(*_args, **_kwargs):
        return {"success": False, "error": code, "data": {"private": "PRIVATE_SENTINEL"}}

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", read)
    result = asyncio.run(module.manage_ui(ctx=None, action="diagnose", target="123"))
    expected = "diagnostics_unavailable" if code == "PRIVATE_SENTINEL" else code
    assert result == {"success": False, "message": expected}
