"""Generation mode forwarding and failure preservation through the real MCP SDK."""

import os
import subprocess
import sys
import textwrap


def test_generation_modes_at_actual_sdk_boundary():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        modules = [importlib.import_module("services.tools." + name)
                   for name in ("generate_model", "generate_image")]
        sent = []
        failure = {"success": False, "error": "'mode' must be 'text' or 'image'."}
        async def instance(ctx): return None
        async def send(fn, instance, command, params):
            sent.append((command, params))
            return dict(failure)
        server = FastMCP("generation-contract")
        for module in modules:
            module.get_unity_instance_from_context = instance
            module.send_with_unity_instance = send
            name = module.__name__.split('.')[-1]
            wrapped = log_execution(name, "Tool")(getattr(module, name))
            server.tool(name=name)(telemetry_tool(name)(wrapped))
        async def main():
            for protocol in ("2026-07-28", "legacy"):
                async with Client(server, mode=protocol) as client:
                    for name in ("generate_model", "generate_image"):
                        for mode in ("garbage", "text_typo", "", "TeXt", "ImAgE", None):
                            payload = {"action": "generate", "mode": mode,
                                       "prompt": "fixture", "image_url": "https://fixture.invalid/source.png"}
                            payload.update({"texture": False, "target_size": 0} if name == "generate_model"
                                           else {"transparent": False, "width": 0, "height": 0})
                            result = await client.call_tool(name, payload)
                            assert result.structured_content == failure
                            command, wire = sent[-1]
                            assert command == name
                            assert wire["imageUrl"] == payload["image_url"]
                            assert wire.get("mode") == mode
                            assert ("mode" in wire) == (mode is not None)
                            if name == "generate_model":
                                assert wire["texture"] is False and wire["targetSize"] == 0
                            else:
                                assert wire["transparent"] is False and wire["width"] == wire["height"] == 0
                        result = await client.call_tool(name, {"action": "generate", "prompt": "fixture"})
                        assert "mode" not in sent[-1][1]
                        assert result.structured_content == failure
            assert len(sent) == 28
            print("real SDK generation mode forwarding and failures passed")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK generation mode forwarding and failures passed" in result.stdout
