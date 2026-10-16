"""Actual public GameObject reads; only outbound Unity/HTTP is controlled."""

import os
from pathlib import Path
import subprocess
import sys


def _run(program, tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program], env=env, capture_output=True, text=True, timeout=90
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


SDK_PROGRAM = r"""
import copy
import json
import anyio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from services.resources import register_all_resources
from core.config import config
from transport.plugin_hub import PluginHub

config.transport_mode = 'http'
config.http_remote_hosted = False
requests, failures = [], []
checks = 0
raw = {}
def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)
async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    return copy.deepcopy(raw)
PluginHub.send_command_for_instance = staticmethod(send)
class FixtureState(Middleware):
    async def on_read_resource(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)
server = FastMCP('public-component-read-contracts')
server.add_middleware(FixtureState())
register_all_resources(server)
base = 'mcpforunity://scene/gameobject/'
cases = [
    ('42', 'get_gameobject', {'instanceID': 42}),
    ('-81840', 'get_gameobject', {'instanceID': -81840}),
    ('42/components', 'get_gameobject_components', {'instanceID': 42, 'pageSize': 25, 'cursor': 0, 'includeProperties': True}),
    ('-81840/components?page_size=0&cursor=0&include_properties=false', 'get_gameobject_components',
     {'instanceID': -81840, 'pageSize': 0, 'cursor': 0, 'includeProperties': False}),
    ('42/components?cursor=1', 'get_gameobject_components', {'instanceID': 42, 'pageSize': 25, 'cursor': 1, 'includeProperties': True}),
    ('42/components?page_size=2147483647&cursor=2147483647&include_properties=true', 'get_gameobject_components',
     {'instanceID': 42, 'pageSize': 2147483647, 'cursor': 2147483647, 'includeProperties': True}),
    ('42/component/Transform', 'get_gameobject_component', {'instanceID': 42, 'componentName': 'Transform'}),
    ('-81840/component/UnityEngine.Transform', 'get_gameobject_component', {'instanceID': -81840, 'componentName': 'UnityEngine.Transform'}),
    ('42/component/UnityEngine%2ETransform', 'get_gameobject_component', {'instanceID': 42, 'componentName': 'UnityEngine.Transform'}),
    ('42/component/Fixture.Outer+Inner', 'get_gameobject_component', {'instanceID': 42, 'componentName': 'Fixture.Outer+Inner'}),
    ('42/component/Fixture.Outer%2BInner', 'get_gameobject_component', {'instanceID': 42, 'componentName': 'Fixture.Outer+Inner'}),
]
def expected(reply):
    return {'success': reply['success'], 'message': reply.get('message'), 'error': reply.get('error'),
            'data': reply.get('data'), 'hint': reply.get('hint')}
async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            templates = [item.model_dump(mode='json', by_alias=False) for item in await client.list_resource_templates()]
            print('FULL_TEMPLATES', mode, json.dumps(templates, sort_keys=True))
            for suffix, command, params in cases:
                for success in (False, True):
                    raw = {'success': success, 'message': 'Controlled native read',
                           'data': {'instanceID': params['instanceID'], 'properties': {'unsigned': 18446744073709551615, 'signed': -9223372036854775808,
                                    'fraction': 3.25, 'enabled': False, 'zero': 0, 'optional': None, 'empty': []}}}
                    if not success:
                        raw['error'] = 'Controlled native rejection'
                    before = len(requests)
                    content = await client.read_resource(base + suffix)
                    decoded = json.loads(content[0].text)
                    sent = requests[before:]
                    check(sent == [('Project@fixture', command, params)], 'exact URI-derived native request')
                    check(json.dumps(decoded, sort_keys=True) == json.dumps(expected(raw), sort_keys=True), 'resource envelope and largeinteger/falsey fidelity')
                    print('SDK_WIRE', json.dumps({'mode': mode, 'uri': base + suffix, 'wires': sent, 'response': decoded}))
            for value in (0, False, None, [], {}):
                raw = {'success': True, 'data': value}
                decoded = json.loads((await client.read_resource(base + '42'))[0].text)
                check(json.dumps(decoded['data']) == json.dumps(value), 'resource falsey JSON type preserved')
            for suffix in ('bad', 'bad/components?cursor=0', 'bad/component/Transform'):
                before = len(requests)
                decoded = json.loads((await client.read_resource(base + suffix))[0].text)
                check(decoded['success'] is False and len(requests) == before, 'invalid string ID before native transport')
            for query in ('?page_size=bad', '?cursor=bad', '?include_properties=bad'):
                before = len(requests)
                try:
                    await client.read_resource(base + '42/components' + query)
                except (ResourceError, MCPError):
                    check(len(requests) == before, 'existing query schema failure before native transport')
                else:
                    check(False, 'malformed query must reject')
            raw = {'success': False, 'error': 'Controlled invalid native ID', 'data': None}
            for identifier in ('0', '-1', '2147483648'):
                before = len(requests)
                decoded = json.loads((await client.read_resource(base + identifier))[0].text)
                check(requests[before:] == [('Project@fixture', 'get_gameobject', {'instanceID': int(identifier)})]
                      and decoded == expected(raw), 'ID wire/native rejection separate from allocation')
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures
anyio.run(main)
"""


CLI_PROGRAM = r"""
import copy
import json
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection

requests, failures = [], []
checks = 0
reply = {}
wrapped = False
client_type = httpx.AsyncClient
def respond(request):
    requests.append(json.loads(request.content))
    return httpx.Response(200, json={'status': 'success', 'result': copy.deepcopy(reply)} if wrapped else copy.deepcopy(reply))
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()
def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)
for command, params in (
    ('get_gameobject', {'instanceID': -81840}),
    ('get_gameobject_components', {'instanceID': 42, 'pageSize': 0, 'cursor': 0, 'includeProperties': False}),
    ('get_gameobject_component', {'instanceID': 42, 'componentName': 'UnityEngine.Transform'}),
):
    for success, wrapped in ((True, False), (True, True), (False, False), (False, True)):
        reply = {'success': success, 'data': {'large': 18446744073709551615, 'fraction': 3.25, 'enabled': False, 'zero': 0, 'optional': None, 'empty': []}}
        if not success:
            reply['error'] = 'Controlled native read rejection'
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'raw', command, json.dumps(params)])
        sent = requests[before:]
        check(sent == [{'type': command, 'params': params, 'unity_instance': 'Project@fixture'}], 'exact raw CLI native request')
        check(result.exit_code == (0 if success else 1), 'CLI native rejection exit')
        check(json.dumps(json.loads(result.output), sort_keys=True) == json.dumps(reply, sort_keys=True), 'CLI one JSON/native falsey and integer fidelity')
        print('CLI_WIRE', json.dumps({'command': command, 'params': params, 'requests': sent, 'response': reply}))
help_result = runner.invoke(cli, ['raw', '--help'])
check(help_result.exit_code == 0, 'existing raw read surface')
print('FULL_HELP', json.dumps(help_result.output))
print('CLI_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
assert not failures, failures
"""


def test_gameobject_resources_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)


def test_gameobject_reads_top_level_raw_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


SELECTION_READ_PROGRAM = r"""
import copy
import json
import anyio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from services.resources import register_all_resources
from core.config import config
from transport.plugin_hub import PluginHub

config.transport_mode = "http"
config.http_remote_hosted = False
requests = []
options = []
raise_transport = False
reply = {"success": False, "error": "unknown_command", "data": {"reason": "unsupported_selection"}}
async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    options.append(copy.deepcopy(kwargs))
    if raise_transport:
        raise TimeoutError("Controlled selected-command response loss")
    return copy.deepcopy(reply)
PluginHub.send_command_for_instance = staticmethod(send)
class State(Middleware):
    async def on_read_resource(self, context, call_next):
        await context.fastmcp_context.set_state("unity_instance", "Project@fixture")
        return await call_next(context)
server = FastMCP("selected-component-read-contract")
server.add_middleware(State())
register_all_resources(server)
uri = "mcpforunity://scene/gameobject/-321/component/UnityEngine.AudioSource"
base = {"instanceID": -321, "componentName": "UnityEngine.AudioSource"}
async def main():
    global raise_transport
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            reply.clear()
            reply.update({"success": False, "error": "unknown_command", "data": {"reason": "unsupported_selection"}})
            templates = [str(item.uri_template) for item in await client.list_resource_templates()]
            assert any("component_instance_id" in item and "strict_selection" in item for item in templates), (mode, templates)
            for query, extra, command in (
                ("", {}, "get_gameobject_component"),
                ("?strict_selection=false", {}, "get_gameobject_component"),
                ("?component_index=0", {"componentIndex": 0}, "get_gameobject_component_selected"),
                ("?component_index=1", {"componentIndex": 1}, "get_gameobject_component_selected"),
                ("?component_index=00000000001", {"componentIndex": 1}, "get_gameobject_component_selected"),
                ("?component_instance_id=-00000000432", {"componentInstanceID": -432}, "get_gameobject_component_selected"),
                ("?component_instance_id=-432", {"componentInstanceID": -432}, "get_gameobject_component_selected"),
                ("?strict_selection=true", {"strictSelection": True}, "get_gameobject_component_selected"),
                ("?strict_selection=true&component_index=1", {"strictSelection": True, "componentIndex": 1}, "get_gameobject_component_selected"),
            ):
                before = len(requests)
                actual = json.loads((await client.read_resource(uri + query))[0].text)
                assert requests[before:] == [("Project@fixture", command, {**base, **extra})], (mode, query, requests[before:])
                assert actual["success"] is False and actual["error"] == "unknown_command" and actual["data"] == reply["data"], actual
                if command.endswith("_selected"):
                    assert options[-1]["retry_on_reload"] is False
            for query in ("?component_index=-1", "?component_index=true", "?component_index=1.5", "?component_index=2147483648", "?component_index=null", "?component_instance_id=0", "?component_instance_id=false", "?component_instance_id=2147483648", "?component_index=0&component_instance_id=12", "?strict_selection=1", "?strict_selection=null"):
                before = len(requests)
                try:
                    actual = json.loads((await client.read_resource(uri + query))[0].text)
                except (ResourceError, MCPError):
                    actual = {"success": False}
                assert actual["success"] is False, (mode, query, actual)
                assert requests[before:] == [], (mode, query, requests[before:])
            reply.clear()
            reply.update({"success": True, "data": {"selection": {"contractVersion": 1, "gameObjectInstanceID": -321, "componentInstanceID": -432, "componentIndex": 1}, "component": {"instanceID": -432, "enabled": False, "volume": 0, "clip": None}}})
            before = len(requests)
            actual = json.loads((await client.read_resource(uri + "?component_instance_id=-432"))[0].text)
            assert actual["success"] is True and actual["data"] == reply["data"], (mode, actual)
            assert len(requests[before:]) == 1
            raise_transport = True
            try:
                before = len(requests)
                actual = json.loads((await client.read_resource(uri + "?component_index=1"))[0].text)
                assert actual["success"] is False
                assert len(requests[before:]) == 1
            finally:
                raise_transport = False
    print("SELECTION_READ_PASS")
anyio.run(main)
"""


def test_component_selection_resource_uri_routes_without_legacy_fallback(tmp_path):
    assert "SELECTION_READ_PASS" in _run(SELECTION_READ_PROGRAM, tmp_path)
