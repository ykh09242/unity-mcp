"""Audio registration and scalar contracts in a fresh, real FastMCP process."""

import os
import subprocess
import sys
import textwrap


def test_audio_at_actual_registry_and_sdk_boundary():
    code = textwrap.dedent('''
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from fastmcp.server.middleware import Middleware
        from services.registry import get_registered_tools
        from services.tools import register_all_tools

        module = importlib.import_module("services.tools.manage_audio")
        sent, errors = [], []
        reply = {"success": True, "data": {"action": "play", "instanceID": 0}}
        checks = 0
        def check(condition, label):
            global checks
            checks += 1
            if not condition:
                errors.append(label)
                print("FAIL", label)
        async def send(fn, instance, command, params):
            sent.append((instance, command, params))
            return reply
        module.send_with_unity_instance = send

        class FixtureUnityState(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state("unity_instance", "Project@audio")
                return await call_next(context)

        server = FastMCP("audio-contract")
        server.add_middleware(FixtureUnityState())
        register_all_tools(server)
        metadata = next(t for t in get_registered_tools() if t["name"] == "manage_audio")
        check(metadata["group"] == "core", "core registry group")
        check(metadata["unity_target"] == "manage_audio", "native target metadata")

        async def main():
            global reply
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    tool = next(t for t in await client.list_tools() if t.name == "manage_audio")
                    check(set(tool.inputSchema["required"]) == {"action", "target"}, "required schema " + mode)
                    for target in ("Speaker", "42", "/Root/Speaker", -42, 0):
                        for action in ("play", "stop"):
                            result = await client.call_tool("manage_audio", {"action": action, "target": target})
                            check(result.structured_content == reply, "success shape " + mode)
                            check(sent[-1] == ("Project@audio", "manage_audio", {"action": action, "target": target}), "target/context/wire " + repr(target))
                    for selector in (None, "by_id", "by_name", "by_path", "by_id_or_name_or_path"):
                        result = await client.call_tool("manage_audio", {"action": "play", "target": "42", "clip": None, "search_method": selector})
                        expected = {"action": "play", "target": "42"}
                        if selector is not None: expected["searchMethod"] = selector
                        check(sent[-1][2] == expected, "null/selector " + repr(selector))
                    failure = {"success": False, "error": "Invalid or unavailable AudioClip", "data": {"clip": None, "isPlaying": False}}
                    reply = failure
                    for clip in ("Assets/Audio/Click.wav", "Packages/audio/Click.wav", "", " "):
                        result = await client.call_tool("manage_audio", {"action": "play", "target": "Speaker", "clip": clip})
                        check(sent[-1][2]["clip"] == clip, "explicit clip " + repr(clip))
                        check(result.structured_content == failure, "native failure retained")
                    result = await client.call_tool("manage_audio", {"action": "stop", "target": "Speaker", "clip": "Assets/Audio/Click.wav"})
                    check(sent[-1][2]["clip"] == "Assets/Audio/Click.wav" and result.structured_content == failure, "native stop validation remains authoritative")
                    for malformed in (None, [], "malformed transport response"):
                        reply = malformed
                        result = await client.call_tool("manage_audio", {"action": "stop", "target": 0})
                        check(result.structured_content == {"success": False, "message": str(malformed)}, "malformed response fails closed")
                    for args in ({"action": "pause", "target": "Speaker"}, {"action": "play", "target": "Speaker", "search_method": "by_tag"},
                                 *({"action": "play", "target": value} for value in (True, False, 1.0, 1.5, {}, [], None))):
                        before = len(sent)
                        rejected = False
                        try:
                            await client.call_tool("manage_audio", args)
                        except ToolError:
                            rejected = True
                        check(rejected and len(sent) == before, "reject before transport " + repr(args))
                    reply = {"success": True, "data": {"action": "play", "instanceID": 0}}
            print(f"real SDK audio checks={checks} failures={len(errors)} transport_calls={len(sent)}")
            assert not errors, errors
        asyncio.run(main())
    ''')
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK audio checks=" in result.stdout
