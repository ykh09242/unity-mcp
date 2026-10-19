"""InputAction authoring crosses the real registered SDK with strict bounded wires."""

import os
from pathlib import Path
import subprocess
import sys


PROGRAM = r"""
import copy
import importlib
import json
import anyio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from core.config import config
import services.tools as tools

config.transport_mode = "stdio"
config.http_remote_hosted = False
server = FastMCP("input-action-authoring")
tools.register_all_tools(server)
calls, cases = [], []

def check(label, condition):
    cases.append({"case":label,"passed":bool(condition)})

async def main():
    names = {item.name for item in await server.list_tools()}
    check("registered", "manage_input_actions" in names)
    assert cases[-1]["passed"], cases
    module = importlib.import_module("services.tools.manage_input_actions")
    async def selected(*args, **kwargs): return "Owned@fixture"
    async def transport(sender, instance, command, params, **kwargs):
        calls.append((instance, command, copy.deepcopy(params), kwargs))
        return {"success":True,"data":{"revision":"a"*64,"created_id":"00112233-4455-6677-8899-aabbccddeeff"}}
    module.get_unity_instance_from_context = selected
    module.send_with_unity_instance = transport
    guid = "00112233-4455-6677-8899-AABBCCDDEEFF"
    path = "Assets/Input/한글.inputactions"
    revision = "a"*64
    valid = [
        {"action":"capabilities"},
        {"action":"read","path":path,"page_size":1,"cursor":0},
        {"action":"read","path":path,"cursor":100,"expected_revision":revision},
        {"action":"create_asset","path":path},
        {"action":"add_map","path":path,"name":"Gameplay","expected_revision":revision},
        {"action":"add_action","path":path,"map_id":guid,"name":"Fire","action_type":"Button","expected_revision":revision},
        {"action":"add_binding","path":path,"map_id":guid,"action_id":guid,"binding_path":"<Keyboard>/space","groups":"","interactions":"tap(duration=0.2)","expected_revision":revision},
        {"action":"set_binding","path":path,"map_id":guid,"binding_id":guid,"binding_path":"<Gamepad>/buttonSouth","processors":"","expected_revision":revision},
        {"action":"remove_binding","path":path,"map_id":guid,"binding_id":guid,"expected_revision":revision},
    ]
    invalid = [
        {"action":"add_map","path":path,"name":"Gameplay"},
        {"action":"add_map","path":path,"name":"   ","expected_revision":revision},
        {"action":"add_map","path":path,"name":"Map/Action","expected_revision":revision},
        {"action":"read","path":path,"cursor":1},
        {"action":"read","path":path,"page_size":0},
        {"action":"read","path":path,"page_size":101},
        {"action":"read","path":path,"cursor":True},
        {"action":"read","path":path,"page_size":"1"},
        {"action":"read","path":path,"cursor":-1},
        {"action":"read","path":path,"cursor":2049},
        {"action":"create_asset","path":"Packages/Input/Controls.inputactions"},
        {"action":"create_asset","path":"Assets/../Controls.inputactions"},
        {"action":"create_asset","path":"Assets/Input/Controls.asset"},
        {"action":"create_asset","path":path,"expected_revision":revision},
        {"action":"capabilities","path":path},
        {"action":"add_action","path":path,"map_id":"not-an-id","name":"Fire","action_type":"Button","expected_revision":revision},
        {"action":"add_action","path":path,"map_id":guid,"name":"Fire","action_type":"button","expected_revision":revision},
        {"action":"add_binding","path":path,"map_id":guid,"action_id":guid,"binding_path":"","expected_revision":revision},
        {"action":"set_binding","path":path,"map_id":guid,"binding_id":12,"binding_path":"<Keyboard>/a","expected_revision":revision},
        {"action":"remove_binding","path":path,"map_id":guid,"binding_id":guid,"expected_revision":"old"},
        {"action":"remove_binding","path":path,"map_id":guid,"binding_id":guid,"binding_path":"ignored","expected_revision":revision},
    ]
    for mode in ("2026-07-28","legacy"):
        async with Client(server, mode=mode) as client:
            schema = next(item for item in await client.list_tools() if item.name == "manage_input_actions").input_schema
            check("closed_schema_"+mode, schema.get("additionalProperties") is False)
            check("all_actions_"+mode, set(schema["properties"]["action"]["enum"]) == {item["action"] for item in valid})
            for payload in valid:
                before = len(calls)
                result = await client.call_tool("manage_input_actions", payload)
                expected = ("Owned@fixture","manage_input_actions",payload,{"retry_on_reload":False})
                check("wire_"+mode+payload["action"]+str(before), len(calls)==before+1 and calls[-1]==expected and not result.is_error)
            for index, payload in enumerate(invalid):
                before = len(calls)
                try:
                    result = await client.call_tool("manage_input_actions", payload)
                    refused = result.is_error or result.structured_content.get("success") is False
                except ToolError:
                    refused = True
                check("refuse_"+mode+str(index), refused and len(calls)==before)
    print(json.dumps({"cases":cases,"dispatches":len(calls)}))
    assert all(item["passed"] for item in cases), [item for item in cases if not item["passed"]]

anyio.run(main)
"""


def test_registered_input_action_authoring_has_exact_wires_and_refuses_invalid_inputs(tmp_path):
    # Given a fresh real SDK registry and only the Unity transport controlled.
    server = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(server / "src"),
        "UNITY_MCP_DISABLE_TELEMETRY": "1",
        "UNITY_MCP_LOG_DIR": str(tmp_path),
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
    }
    # When all authoring actions and invalid request classes cross each SDK protocol.
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROGRAM],
        cwd=server,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    # Then exact GUID strings/empty optionals survive and invalid requests never dispatch.
    assert result.returncode == 0, result.stdout + result.stderr
