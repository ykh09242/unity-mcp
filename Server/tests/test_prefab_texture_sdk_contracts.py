"""Fresh real SDK checks with actual preflight and controlled Editor boundaries."""
import os
import subprocess
import sys
import textwrap


def test_prefab_texture_validation_and_wire_at_real_sdk_boundary():
    code = textwrap.dedent('''
        import asyncio, copy, importlib
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from services.tools.preflight import preflight as actual_preflight

        texture = importlib.import_module("services.tools.manage_texture")
        prefabs = importlib.import_module("services.tools.manage_prefabs")
        state_module = importlib.import_module("services.resources.editor_state")
        refresh_module = importlib.import_module("services.tools.refresh_unity")
        counts = dict(state=0, refresh=0, preflight=0, send=0)
        sent, errors = [], []
        checks = 0
        compiling = False
        reply = {"success": True, "data": {"count": 0, "modified": False, "reference": None}}
        def check(condition, label):
            global checks
            checks += 1
            if not condition:
                errors.append(label)
                print("FAIL", label, "counts", counts)
        async def editor_state(ctx):
            counts["state"] += 1
            return {"success": True, "data": {"assets": {"external_changes_dirty": True}, "compilation": {"is_compiling": compiling}}}
        async def refresh(ctx, **kwargs):
            counts["refresh"] += 1
            assert kwargs == {"mode": "if_dirty", "scope": "all", "compile": "request", "wait_for_ready": True}
            return {"success": True}
        async def preflight(ctx, **kwargs):
            counts["preflight"] += 1
            return await actual_preflight(ctx, max_wait_s=0, **kwargs)
        async def send(fn, instance, command, params):
            counts["send"] += 1
            assert instance == "Project@fixture"
            sent.append((command, copy.deepcopy(params)))
            return copy.deepcopy(reply)
        for module in (texture, prefabs):
            module.preflight = preflight
            module.send_with_unity_instance = send
        state_module.get_editor_state = editor_state
        refresh_module.refresh_unity = refresh
        class FixtureUnityState(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state("unity_instance", "Project@fixture")
                return await call_next(context)
        server = FastMCP("prefab-texture-contracts")
        server.add_middleware(FixtureUnityState())
        register_all_tools(server)

        invalid_texture = [
            {"width": 0}, {"fill_color": [1, 2]}, {"palette": "not-json"},
            {"image_path": "fixture.png", "fill_color": [255, 0, 0]},
            {"set_pixels": {"width": 0, "height": 1, "pixels": []}},
            {"set_pixels": {"width": 2, "height": 1, "pixels": [[255, 0, 0, 255]]}},
            {"as_sprite": False},
            {"as_sprite": {"pivot": ["bad", 0]}},
            {"as_sprite": {"pivot": [None, 0]}},
            {"as_sprite": {"pixels_per_unit": "bad"}},
            {"as_sprite": {"pixelsPerUnit": None}},
            {"import_settings": {"sprite_pivot": ["bad", 0]}},
            {"import_settings": {"sprite_pivot": [None, 0]}},
            {"import_settings": {"sprite_pixels_per_unit": "bad"}},
        ]
        enum_fields = ("texture_type", "texture_shape", "alpha_source", "wrap_mode", "wrap_mode_u", "wrap_mode_v", "filter_mode", "mipmap_filter", "compression", "sprite_mode", "sprite_mesh_type")
        invalid_texture += [{"import_settings": {field: value}} for field in enum_fields for value in ([], {})]

        async def invalid(client, tool, payload, label):
            for key in counts: counts[key] = 0
            result = None
            try:
                result = await client.call_tool(tool, payload)
            except ToolError:
                pass
            structured = result.structured_content if result is not None else None
            check(isinstance(structured, dict) and structured.get("success") is False, "structured local failure " + label)
            check(all(value == 0 for value in counts.values()), "local validation before Editor IO " + label)

        async def main():
            global compiling, reply
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for i, args in enumerate(invalid_texture):
                        await invalid(client, "manage_texture", {"action": "create", "path": "Assets/Fixture.png", **args}, mode + " texture " + str(i))
                    for i, args in enumerate(({"position": [1, 2]}, {"create_child": {"name": "Child", "scale": [1, 2]}})):
                        await invalid(client, "manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", **args}, mode + " prefab " + str(i))
                    for tool, payload in (("manage_texture", {"action": "modify", "set_pixels": []}), ("manage_prefabs", {"action": "modify_contents", "target": 0})):
                        for key in counts: counts[key] = 0
                        rejected = False
                        try: await client.call_tool(tool, payload)
                        except ToolError: rejected = True
                        check(rejected and all(value == 0 for value in counts.values()), "SDK invalid shape avoids IO " + tool)
                    texture_controls = [
                        {"action": "create", "path": "Assets/Fixture.png", "width": 1, "height": 1, "fill_color": [0, 0, 0, 0], "import_settings": {"srgb": False, "readable": False, "generate_mipmaps": False, "aniso_level": 0, "compression_quality": 0, "sprite_extrude": 0}},
                        {"action": "modify", "path": "Assets/Fixture.png", "set_pixels": {"x": -1, "y": 0, "width": 2, "height": 1, "color": [1.0, 0.0, 0.0, 0.0]}},
                        {"action": "create", "path": "Assets/Fixture.png", "width": 1, "height": 1, "pixels": "AAAAAA==", "gradient_angle": 0, "noise_scale": 0, "as_sprite": None, "import_settings": None},
                        {"action": "create", "path": "Assets/Fixture.png", "as_sprite": {"pivot": [0, 0], "pixels_per_unit": 100}},
                    ]
                    for i, payload in enumerate(texture_controls):
                        for key in counts: counts[key] = 0
                        result = await client.call_tool("manage_texture", payload)
                        check(result.structured_content["success"] is True, "valid texture " + str(i))
                        check(counts == {"state": 1, "refresh": 1, "preflight": 1, "send": 1}, "valid keeps preflight policy")
                        wire = sent[-1][1]
                        if i == 0: check(wire["fillColor"] == [0,0,0,0] and wire["importSettings"] == {"sRGBTexture": False,"isReadable": False,"mipmapEnabled": False,"anisoLevel": 0,"compressionQuality": 0,"spriteExtrude": 0}, "texture false/zero")
                        if i == 1: check(wire["setPixels"] == {"x": -1,"y": 0,"width": 2,"height": 1,"color": [255,0,0,0]}, "region clipping inputs/color preserved")
                        if i == 2: check(wire["pixels"] == "base64:AAAAAA==" and wire["gradientAngle"] == wire["noiseScale"] == 0 and "spriteSettings" not in wire and "importSettings" not in wire, "base64/zero/null preserved")
                        if i == 3: check(wire["spriteSettings"] == {"pivot": [0.0,0.0],"pixelsPerUnit": 100.0}, "sprite numeric controls")
                    for payload in (
                        {"action": "create_from_gameobject", "prefab_path": "Assets/Fixture.prefab", "target": "0", "allow_overwrite": False, "search_inactive": False, "unlink_if_instance": False},
                        {"action": "create_from_gameobject", "prefab_path": "Assets/Fixture.prefab", "name": "NameAlias"},
                        {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "target": "Parent/Child", "set_active": False, "position": [0,0,0], "component_properties": {"MyComponent": {"enabled": False,"count": 0,"reference": None}}},
                    ):
                        result = await client.call_tool("manage_prefabs", payload)
                        check(result.structured_content == reply, "prefab response false/zero/null")
                        wire = sent[-1][1]
                        if "allow_overwrite" in payload: check(wire["target"] == "0" and wire["allowOverwrite"] is wire["searchInactive"] is wire["unlinkIfInstance"] is False, "prefab numeric name/false flags")
                        elif "name" in payload: check(wire["target"] == "NameAlias", "prefab name compatibility")
                        else: check(wire["target"] == "Parent/Child" and wire["setActive"] is False and wire["position"] == [0.0,0.0,0.0] and wire["componentProperties"] == payload["component_properties"], "prefab path/false/zero/null")
                    reply = {"success": False, "error": "Native operation failed", "data": {"modified": False,"count": 0,"reference": None}}
                    for tool, payload in (("manage_texture", {"action": "delete", "path": "Assets/Fixture.png"}), ("manage_prefabs", {"action": "close_prefab_stage"})):
                        result = await client.call_tool(tool, payload)
                        response = dict(result.structured_content)
                        response.pop("_debug_params", None)
                        check(response == reply, "native failure preserved " + tool)
                    compiling = True
                    for key in counts: counts[key] = 0
                    result = await client.call_tool("manage_texture", {"action": "create", "path": "Assets/Fixture.png"})
                    check(result.structured_content["success"] is False and result.structured_content["error"] == "busy" and counts["send"] == 0 and counts["preflight"] == 1, "valid request preserves compilation gate")
                    compiling = False
                    reply = {"success": True,"data": {"count": 0,"modified": False,"reference": None}}
            print(f"actual SDK/preflight checks={checks} failures={len(errors)}")
            assert not errors, errors
        asyncio.run(main())
    ''')
    env = {name: value for name, value in os.environ.items() if name != "PYTEST_CURRENT_TEST"}
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    result = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "actual SDK/preflight checks=" in result.stdout
