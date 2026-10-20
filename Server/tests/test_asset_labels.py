"""Asset-label public contracts. Native storage semantics live in the owned Unity fixture."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from services.tools import manage_asset as module


REVISION = "mcp-al-v1_" + "a" * 32 + "_" + "b" * 64 + "_" + "c" * 64


@pytest.mark.parametrize("action", ["get_labels", "set_labels", "add_labels", "remove_labels"])
def test_label_actions_use_only_label_wire_and_never_refresh_or_retry(monkeypatch, action):
    # Given: only the transport boundary is controlled; the public wrapper owns admission.
    context = AsyncMock(return_value="Owned@labels")
    gate = AsyncMock(return_value=None)
    reply = {"success": False, "code": "command_outcome_unknown", "data": {"dispatched": True}}
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    monkeypatch.setattr(module, "preflight", gate)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    extra = (
        {} if action == "get_labels" else {"labels": ["keep", "한글"], "label_revision": REVISION}
    )
    # When: invoking a valid new action through the actual wrapper.
    result = asyncio.run(module.manage_asset(None, action=action, path="Assets/a.txt", **extra))
    # Then: one non-retrying mutation and no dirty-refresh admission; failure stays opaque.
    assert result is reply
    context.assert_awaited_once()
    gate.assert_awaited_once_with(None, wait_for_no_compile=True, refresh_if_dirty=False)
    expected = {"action": action, "path": "Assets/a.txt"}
    if extra:
        expected.update(labels=extra["labels"], labelRevision=REVISION)
    assert send.await_count == 1
    assert send.await_args.args[2:] == ("manage_asset", expected)
    assert send.await_args.kwargs["retry_on_reload"] is (action == "get_labels")


@pytest.mark.parametrize(
    "extra",
    [
        {"labels": [None]},
        {"labels": [True]},
        {"labels": "x"},
        {"labels": [""]},
        {"labels": [" x"]},
        {"labels": ["x "]},
        {"labels": ["x\n"]},
        {"labels": ["x" * 257]},
        {"labels": ["x" + str(i) for i in range(129)]},
        {"labels": ["\U0001f600" * 129]},
        {"labels": [str(i) + "x" * 250 for i in range(66)]},
        {"label_revision": "invalid"},
        {"label_revision": REVISION.upper()},
        {"label_targets": []},
        {"label_targets": [{"path": "Assets/a.txt", "revision": REVISION, "extra": 1}]},
        {"label_targets": [{"path": True, "revision": REVISION}]},
        {"label_targets": [{"path": "Assets/a.txt", "revision": None}]},
        {"label_targets": [{"path": "Assets/a.txt", "revision": REVISION}] * 2},
        {"label_targets": [{"path": "Assets/" + str(i), "revision": REVISION} for i in range(33)]},
        {"properties": {}},
        {"generate_preview": True},
        {"page_number": 1},
    ],
)
def test_invalid_label_requests_refuse_before_context_preflight_or_transport(monkeypatch, extra):
    # Given: any attempted I/O is a failure, including resolving an Editor instance.
    context = AsyncMock(side_effect=AssertionError("context must not run"))
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    args = {"path": "Assets/a.txt", "labels": ["x"], "label_revision": REVISION, **extra}
    if "label_targets" in extra:
        args.pop("path")
        args.pop("label_revision")
    # When: malformed/over-budget inputs cross the direct-call boundary.
    result = asyncio.run(module.manage_asset(None, action="add_labels", **args))
    # Then: rejection is local, with a stable bounded diagnostic.
    assert result["success"] is False and result["code"] == "invalid_asset_labels"
    assert len(json.dumps(result)) < 1024
    context.assert_not_awaited()


def test_batch_32_and_empty_delta_preserve_exact_revision_rows(monkeypatch):
    # Given: the largest allowed common-operation batch and an explicit empty delta.
    rows = [{"path": f"Assets/{i}.txt", "revision": REVISION} for i in range(32)]
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owned"))
    monkeypatch.setattr(module, "preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When: admission succeeds without expanding targets or rewriting labels.
    result = asyncio.run(
        module.manage_asset(None, action="remove_labels", label_targets=rows, labels=[])
    )
    # Then: all 32 revisions and the intentional empty list reach exactly one command.
    assert result["success"] is True
    assert send.await_args.args[3] == {
        "action": "remove_labels",
        "labelTargets": rows,
        "labels": [],
    }


@pytest.mark.parametrize(
    "args",
    [
        {"action": "get_labels", "path": "Assets/a", "labels": []},
        {"action": "get_labels", "path": "Assets/a", "label_revision": REVISION},
        {"action": "set_labels", "path": "Assets/a", "labels": []},
        {"action": "get_info", "path": "Assets/a", "labels": []},
        {"action": "get_labels", "path": "Assets/a", "label_targets": [{"path": "Assets/b"}]},
    ],
)
def test_label_option_combinations_are_not_silently_ignored(monkeypatch, args):
    # Given: combinations whose omission could change the intended operation.
    context = AsyncMock(side_effect=AssertionError("unexpected context"))
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    # When: a direct caller bypasses SDK type checking.
    result = asyncio.run(module.manage_asset(None, **args))
    # Then: the wrapper refuses rather than sending a different operation.
    assert result["success"] is False
    context.assert_not_awaited()


def _child(program, mode, tmp_path):
    source = str((Path(__file__).resolve().parents[1] / "src").resolve())
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[key] = str(tmp_path / key)
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([source, *env.get("PYTHONPATH", "").split(os.pathsep)])
    )
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, mode, source],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


SDK_PROGRAM = r"""
import copy,json,socket,sys
from pathlib import Path
import anyio
from fastmcp import Client,FastMCP
from fastmcp.server.middleware import Middleware
from core.config import config
from services.tools import register_all_tools
from services.tools import manage_asset as module
from transport.plugin_hub import PluginHub
assert Path(module.__file__).resolve()==Path(sys.argv[2])/"services/tools/manage_asset.py"
requests=[]
reply={"success":False,"code":"command_outcome_unknown","data":{"dispatched":True,"optional":None}}
revision="mcp-al-v1_"+"a"*32+"_"+"b"*64+"_"+"c"*64
def deny(*args,**kwargs): raise AssertionError("network forbidden")
async def send(instance,command,params,**kwargs):
    requests.append((command,copy.deepcopy(params),copy.deepcopy(kwargs)))
    if command=="get_editor_state": return {"success":True,"data":{"compilation":{"is_compiling":False},"assets":{"external_changes_dirty":True}}}
    return copy.deepcopy(reply)
class State(Middleware):
    async def on_call_tool(self,context,call_next):
        await context.fastmcp_context.set_state("unity_instance","Owned@labels")
        return await call_next(context)
async def main():
    socket.socket.connect=deny; socket.create_connection=deny
    config.transport_mode="http"; config.http_remote_hosted=False
    PluginHub.send_command_for_instance=staticmethod(send)
    server=FastMCP("labels-contract"); server.add_middleware(State()); register_all_tools(server)
    server.enable(names={'manage_asset'}, components={"tool"})
    async with Client(server,mode=sys.argv[1]) as client:
        tool=next(t for t in await client.list_tools() if t.name=="manage_asset")
        annotations=tool.annotations.model_dump(by_alias=True)
        assert annotations["destructiveHint"] is True
        schema=tool.input_schema
        assert all(key in schema["properties"] for key in ("labels","label_targets","label_revision"))
        for action in ("get_labels","set_labels","add_labels","remove_labels"):
            args={"action":action,"path":"Assets/a.txt"}
            wire={"action":action,"path":"Assets/a.txt"}
            if action!="get_labels": args.update(labels=["keep"],label_revision=revision); wire.update(labels=["keep"],labelRevision=revision)
            before=len(requests)
            result=await client.call_tool("manage_asset",args)
            rows=requests[before:]; domain=[r for r in rows if r[0]=="manage_asset"]
            assert len(domain)==1 and domain[0][1]==wire,rows
            assert domain[0][2]["retry_on_reload"] is (action=="get_labels")
            assert not any(r[0]=="refresh_unity" for r in rows)
            assert result.structured_content==reply
        for bad in ({"label_targets":[{"path":True}]},{"label_targets":[{"path":"Assets/a","extra":1}]},{"labels":[False]}):
            before=len(requests)
            result=await client.call_tool("manage_asset",{"action":"get_labels",**bad},raise_on_error=False)
            assert result.is_error or result.structured_content.get("success") is False
            assert requests[before:]==[]
    print("LABEL_SDK_PASS",sys.argv[1])
anyio.run(main)
"""


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_registered_label_sdk_strict_schema_exact_wire_and_unknown(mode, tmp_path):
    # Given/When: actual registration and both SDK protocols run in guarded fresh children.
    output = _child(SDK_PROGRAM, mode, tmp_path)
    # Then: schema, early refusal, real preflight, no reload retry and opaque unknown are observed.
    assert "LABEL_SDK_PASS" in output


CLI_PROGRAM = r"""
import copy,json,sys
from pathlib import Path
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection
from cli.commands import asset as module
assert Path(module.__file__).resolve()==Path(sys.argv[2])/"cli/commands/asset.py"
requests=[]; reply={"success":True,"data":{"results":[{"status":"unchanged","revision":None}]}}
config_calls=[]; confirmation_calls=[]
original_config=module.get_config
original_confirmation=module.confirm_destructive_action
def config():
    config_calls.append(True); return original_config()
def confirm(*args,**kwargs):
    confirmation_calls.append(True); return original_confirmation(*args,**kwargs)
module.get_config=config
module.confirm_destructive_action=confirm
def respond(request):
    requests.append(json.loads(request.content)); return httpx.Response(200,json=copy.deepcopy(reply))
client_type=httpx.AsyncClient
connection.httpx.AsyncClient=lambda:client_type(transport=httpx.MockTransport(respond))
connection._auth_headers=lambda config:{}
runner=CliRunner(); prefix=["--format","json","--instance","Owned@labels","asset","labels"]
revision="mcp-al-v1_"+"a"*32+"_"+"b"*64+"_"+"c"*64
for verb,action in (("get","get_labels"),("set","set_labels"),("add","add_labels"),("remove","remove_labels")):
    args=[verb,"Assets/a.txt"]; wire={"action":action,"path":"Assets/a.txt"}
    if verb!="get": args += ["--labels-json","[]","--revision",revision,"--force"]; wire.update(labels=[],labelRevision=revision)
    before=len(requests); result=runner.invoke(cli,prefix+args)
    assert result.exit_code==0,(result.output,result.exception)
    assert json.loads(result.stdout)==reply and len(requests[before:])==1
    assert requests[-1]["params"]==wire
for args in (["set","Assets/a","--labels-json","[]","--force"],["get","--targets-json",'[{"path":true}]'],["add","Assets/a","--labels-json",'[" x"]',"--revision",revision,"--force"],
             ["set","Assets/a.txt","--revision",revision,"--labels-json","[]","--targets-json","null","--force"],
             ["set","Assets/a.txt","--revision",revision,"--labels-json","null","--force"],
             ["get","Assets/a.txt","--labels-json","null"]):
    before=len(requests); configs_before=len(config_calls); confirms_before=len(confirmation_calls)
    result=runner.invoke(cli,prefix+args)
    assert result.exit_code!=0 and requests[before:]==[],result.output
    assert len(config_calls)==configs_before and len(confirmation_calls)==confirms_before,(args,config_calls,confirmation_calls)
before=len(requests)
result=runner.invoke(cli,prefix+["set","Assets/a","--labels-json","[]","--revision",revision],input="n\n")
assert result.exit_code!=0 and requests[before:]==[]
reply.clear(); reply.update({"success":False,"code":"command_outcome_unknown","data":{"dispatched":True}})
before=len(requests)
result=runner.invoke(cli,prefix+["remove","Assets/a","--labels-json","[]","--revision",revision,"--force"])
assert result.exit_code==1 and json.loads(result.stdout)==reply and len(requests[before:])==1
print("LABEL_CLI_PASS")
"""


def test_real_central_cli_labels_confirm_once_and_preserve_unknown(tmp_path):
    # Given/When: central Click registration and real connection serialize to an HTTP-level fake.
    output = _child(CLI_PROGRAM, "cli", tmp_path)
    # Then: each operation posts once; rejection/declined confirmation post nothing.
    assert "LABEL_CLI_PASS" in output
