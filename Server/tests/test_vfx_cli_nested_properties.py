"""Nested VFX properties contracts through the registered CLI and JSON transport."""

import os
import subprocess
import sys
import textwrap


def test_vfx_raw_nested_properties_before_transport(tmp_path):
    code = textwrap.dedent(r"""
        import json
        import socket
        import httpx
        from types import SimpleNamespace
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection

        requests = []
        failures = []
        raw = {"success": True, "data": {"enabled": False, "count": 0, "reference": None}}
        client_type = httpx.AsyncClient

        def denied(*args, **kwargs):
            raise AssertionError("Network or credential access is forbidden")

        socket.socket = denied
        socket.create_connection = denied
        socket.getaddrinfo = denied
        httpx.HTTPTransport.handle_request = denied
        httpx.AsyncHTTPTransport.handle_async_request = denied
        connection.read_local_auth_token = denied
        connection._auth_headers = lambda config: {}

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=raw)

        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))

        def run_inline(coroutine):
            try:
                coroutine.send(None)
            except StopIteration as completed:
                return completed.value
            coroutine.close()
            raise AssertionError("In-memory transport unexpectedly suspended")

        connection.asyncio = SimpleNamespace(run=run_inline)
        runner = CliRunner()
        invalid = [[], [1], False, True, 0, 1, "", "[]", "null", "false", "1", '{"broken":']
        for value in invalid:
            for flattened in ({}, {"duration": 5}):
                before = len(requests)
                result = runner.invoke(cli, ["--format", "json", "vfx", "raw", "particle_set_main", "Fire", "--params", json.dumps({"properties": value, **flattened})])
                if not (result.exit_code != 0 and "properties" in result.output and len(requests) == before):
                    failures.append({"value": value, "flattened": flattened, "exit": result.exit_code, "output": result.output, "sent": requests[before:]})

        valid = [
            ({}, None),
            ({"properties": None}, None),
            ({"properties": {}}, {}),
            ({"properties": '{}'}, '{}'),
            ({"properties": '{"enabled":false,"count":0,"reference":null}'}, '{"enabled":false,"count":0,"reference":null}'),
            ({"properties": None, "duration": 5}, {"duration": 5}),
            ({"duration": 5, "properties": {"duration": 0, "looping": False, "reference": None}}, {"duration": 0, "looping": False, "reference": None}),
            ({"duration": 5, "properties": '{"duration":0,"looping":false,"reference":null}'}, {"duration": 0, "looping": False, "reference": None}),
        ]
        for failed in (False, True):
            expected = {"success": not failed, "data": {"enabled": False, "count": 0, "reference": None}}
            raw = {"status": "success", "result": expected}
            for payload, properties in valid:
                result = runner.invoke(cli, ["--format", "json", "--instance", "Project@fixture", "vfx", "raw", "particle_set_main", "0", "--component-index", "0", "--params", json.dumps(payload)])
                wire = {"action": "particle_set_main", "target": "0", "componentIndex": 0}
                if properties is not None:
                    wire["properties"] = properties
                if not (result.exit_code == int(failed) and json.loads(result.stdout) == expected and requests[-1] == {"type": "manage_vfx", "params": wire, "unity_instance": "Project@fixture"}):
                    failures.append({"valid": payload, "exit": result.exit_code, "output": result.output, "sent": requests[-1]})

        print(json.dumps({"cases": len(invalid) * 2 + len(valid) * 2, "failures": failures}, indent=2))
        assert not failures
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        env={
            **os.environ,
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_vfx_public_coroutine_rejects_properties_before_instance_discovery(tmp_path):
    code = textwrap.dedent(r"""
        import importlib
        import json
        import socket
        import httpx

        module = importlib.import_module("services.tools.manage_vfx")
        discovered = []
        sent = []
        raw = {"success": False, "code": "native_fixture", "data": {"enabled": False, "count": 0, "reference": None}}

        def denied(*args, **kwargs):
            raise AssertionError("Network or credential access is forbidden")

        socket.socket = denied
        socket.create_connection = denied
        socket.getaddrinfo = denied
        httpx.HTTPTransport.handle_request = denied
        httpx.AsyncHTTPTransport.handle_async_request = denied
        module.async_send_command_with_retry = denied

        async def instance(ctx):
            discovered.append(ctx)
            return "Project@fixture"

        async def send(fn, instance, command, params):
            sent.append(json.loads(json.dumps({"instance": instance, "command": command, "params": params})))
            return raw

        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send

        def run_inline(coroutine):
            try:
                coroutine.send(None)
            except StopIteration as completed:
                return completed.value
            coroutine.close()
            raise AssertionError("In-memory coroutine unexpectedly suspended")

        for properties in ([], False, 0, "[]", "null", "false", "1", '{"broken":'):
            result = run_inline(module.manage_vfx(None, "particle_set_main", properties=properties))
            assert result["success"] is False and "properties" in result["message"], result
            assert discovered == [] and sent == [], (discovered, sent)
        result = run_inline(module.manage_vfx(None, "particle_unknown"))
        assert result["success"] is False and discovered == [] and sent == [], result

        payload = {"enabled": False, "count": 0, "reference": None}
        for properties, normalized in ((None, None), ({}, {}), (json.dumps(payload), payload)):
            result = run_inline(module.manage_vfx(None, "PARTICLE_SET_MAIN", target="0", search_method="by_name", component_index=0, properties=properties))
            expected = {"action": "particle_set_main", "target": "0", "searchMethod": "by_name", "componentIndex": 0}
            if normalized is not None:
                expected["properties"] = normalized
            assert sent[-1] == {"instance": "Project@fixture", "command": "manage_vfx", "params": expected}, sent[-1]
            assert result == raw, result
        print("public VFX coroutine: 9 early rejections, 3 exact encoded requests and responses")
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        env={
            **os.environ,
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
