"""Tests for the blender_bridge tool and CLI command.

Pass-through tool: NO API keys, NO file bytes. Unity transport fully mocked.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

from cli.commands.blender import blender
from cli.utils.config import CLIConfig
from services.registry import get_registered_tools

from services.tools import blender_bridge as mod
from services.tools.blender_bridge import blender_bridge
from transport.plugin_hub import PluginHub


COMMAND = "blender_bridge"


def _call_tool(**kwargs):
    ctx = MagicMock()
    with patch.object(
        mod, "get_unity_instance_from_context", new=AsyncMock(return_value="unity-1")
    ):
        with patch.object(
            mod,
            "send_with_unity_instance",
            new=AsyncMock(return_value={"success": True, "data": {}}),
        ) as mock_send:
            result = asyncio.run(blender_bridge(ctx, **kwargs))
    return result, mock_send.call_args.args


def _sent_command(sent_args):
    return sent_args[2]


def _sent_params(sent_args):
    return sent_args[3]


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config():
    return CLIConfig(host="127.0.0.1", port=8080, timeout=30, format="text", unity_instance=None)


@pytest.fixture
def cli_runner(runner, mock_config):
    def _invoke(args):
        with patch("cli.commands.blender.get_config", return_value=mock_config):
            with patch(
                "cli.commands.blender.run_command",
                return_value={"success": True, "message": "OK", "data": {}},
            ) as mock_run:
                result = runner.invoke(blender, args)
                return result, mock_run

    return _invoke


class TestBlenderBridgeRegistration:
    def test_tool_registered_under_asset_gen_group(self):
        tools = get_registered_tools()
        tool = next((t for t in tools if t["name"] == COMMAND), None)
        assert tool is not None
        assert tool["group"] == "asset_gen"


class TestBlenderBridgeRouting:
    def test_status_sends_action_only(self):
        _, sent = _call_tool(action="status")
        assert _sent_command(sent) == COMMAND
        assert _sent_params(sent) == {"action": "status"}

    def test_import_model_maps_snake_case_to_camel_case(self):
        _, sent = _call_tool(
            action="import_model",
            object_names=["Cube"],
            target_size=2.0,
            place_in_scene=False,
            apply_modifiers=False,
            position=[0.0, 1.0, 0.0],
            animation_type="generic",
            output_folder="Assets/Generated/Imported",
        )
        assert _sent_command(sent) == COMMAND
        assert _sent_params(sent) == {
            "action": "import_model",
            "objectNames": ["Cube"],
            "targetSize": 2.0,
            "placeInScene": False,
            "applyModifiers": False,
            "position": [0.0, 1.0, 0.0],
            "animationType": "generic",
            "outputFolder": "Assets/Generated/Imported",
        }

    def test_run_python_and_screenshot_params(self):
        _, sent = _call_tool(action="run_python", code="print(1)", timeout_seconds=30)
        assert _sent_params(sent) == {
            "action": "run_python",
            "code": "print(1)",
            "timeoutSeconds": 30,
        }

        _, sent = _call_tool(action="screenshot", max_size=800)
        assert _sent_params(sent) == {"action": "screenshot", "maxSize": 800}

    def test_finishing_touch_flags_and_compare_params(self):
        _, sent = _call_tool(
            action="import_model",
            selection_only=True,
            auto_animate=False,
            save_prefab=True,
            ensure_bloom=True,
        )
        assert _sent_params(sent) == {
            "action": "import_model",
            "selectionOnly": True,
            "autoAnimate": False,
            "savePrefab": True,
            "ensureBloom": True,
        }

        _, sent = _call_tool(action="compare_screenshot", game_object="House", max_size=600)
        assert _sent_params(sent) == {
            "action": "compare_screenshot",
            "gameObject": "House",
            "maxSize": 600,
        }

        _, sent = _call_tool(action="setup_bloom")
        assert _sent_params(sent) == {"action": "setup_bloom"}

    def test_non_dict_result_becomes_error(self):
        ctx = MagicMock()
        with patch.object(
            mod, "get_unity_instance_from_context", new=AsyncMock(return_value="unity-1")
        ):
            with patch.object(mod, "send_with_unity_instance", new=AsyncMock(return_value="boom")):
                result = asyncio.run(blender_bridge(ctx, action="status"))
        assert result == {"success": False, "message": "boom"}


class TestBlenderCli:
    def test_status(self, cli_runner):
        result, mock_run = cli_runner(["status"])
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[0] == COMMAND
        assert mock_run.call_args.args[1] == {"action": "status"}

    def test_import_model_flags(self, cli_runner):
        result, mock_run = cli_runner(
            [
                "import-model",
                "--object",
                "House",
                "--object",
                "Tree",
                "--format",
                "fbx",
                "--target-size",
                "2",
                "--position",
                "0",
                "1",
                "0",
                "--no-place",
                "--keep-modifiers",
                "--animation-type",
                "generic",
            ]
        )
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {
            "action": "import_model",
            "objectNames": ["House", "Tree"],
            "format": "fbx",
            "targetSize": 2.0,
            "position": [0.0, 1.0, 0.0],
            "placeInScene": False,
            "applyModifiers": False,
            "animationType": "generic",
        }

    def test_import_model_finishing_flags(self, cli_runner):
        result, mock_run = cli_runner(
            ["import-model", "--selection-only", "--no-animate", "--save-prefab", "--ensure-bloom"]
        )
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {
            "action": "import_model",
            "selectionOnly": True,
            "autoAnimate": False,
            "savePrefab": True,
            "ensureBloom": True,
        }

    def test_compare_and_bloom_commands(self, cli_runner):
        result, mock_run = cli_runner(
            ["compare-screenshot", "--game-object", "House", "--max-size", "600"]
        )
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {
            "action": "compare_screenshot",
            "gameObject": "House",
            "maxSize": 600,
        }
        result, mock_run = cli_runner(["setup-bloom"])
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {"action": "setup_bloom"}

    def test_read_only_commands(self, cli_runner):
        for args, expected in (
            (["scene-info"], {"action": "scene_info"}),
            (
                ["object-info", "--object-name", "Cube"],
                {"action": "object_info", "objectName": "Cube"},
            ),
            (["check-updates"], {"action": "check_updates"}),
            (["screenshot"], {"action": "screenshot"}),
            (
                ["screenshot", "--max-size", "400", "--output-folder", "Assets/Shots"],
                {"action": "screenshot", "maxSize": 400, "outputFolder": "Assets/Shots"},
            ),
        ):
            result, mock_run = cli_runner(args)
            assert result.exit_code == 0, result.output
            assert mock_run.call_args.args[0] == COMMAND
            assert mock_run.call_args.args[1] == expected

    def test_run_python_from_inline_code_and_from_file(self, cli_runner, tmp_path):
        result, mock_run = cli_runner(["run-python", "--code", "print(1)"])
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {"action": "run_python", "code": "print(1)"}

        script = tmp_path / "snippet.py"
        script.write_text("print(2)", encoding="utf-8")
        result, mock_run = cli_runner(["run-python", "--file", str(script)])
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {"action": "run_python", "code": "print(2)"}

    def test_sync_addon_without_force_omits_the_flag(self, cli_runner):
        result, mock_run = cli_runner(["sync-addon"])
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {"action": "sync_addon"}

    def test_run_python_requires_code_or_file(self, cli_runner):
        result, mock_run = cli_runner(["run-python"])
        assert result.exit_code != 0
        assert not mock_run.called

    def test_sync_addon_force(self, cli_runner):
        result, mock_run = cli_runner(["sync-addon", "--force"])
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[1] == {"action": "sync_addon", "force": True}


class TestBlenderTimeouts:
    @pytest.mark.parametrize("requested, expected", [(None, 210), (60, 90), (300, 330)])
    def test_mcp_import_budget_reaches_unity_and_server(self, requested, expected):
        async def exercise():
            websocket = MagicMock()
            messages = []
            waits = []
            real_wait_for = asyncio.wait_for

            async def send_json(message):
                messages.append(message)
                PluginHub._pending[message["id"]]["future"].set_result({"success": True})

            async def wait_for(future, timeout):
                waits.append(timeout)
                return await real_wait_for(future, timeout)

            async def route(_send, _instance, command, params):
                return await PluginHub.send_command("test-session", command, params)

            websocket.send_json = AsyncMock(side_effect=send_json)
            with (
                patch.object(PluginHub, "_get_connection", AsyncMock(return_value=websocket)),
                patch.object(PluginHub, "_lock", asyncio.Lock()),
                patch.object(PluginHub, "_registry", None),
                patch.object(PluginHub, "_connections", {"test-session": websocket}),
                patch.object(PluginHub, "_pending", {}),
                patch.object(
                    mod, "get_unity_instance_from_context", AsyncMock(return_value="unity-1")
                ),
                patch.object(mod, "send_with_unity_instance", route),
                patch("transport.plugin_hub.asyncio.wait_for", side_effect=wait_for),
            ):
                result = await blender_bridge(
                    MagicMock(), action="import_model", timeout_seconds=requested
                )
            assert result["success"]
            assert messages[0]["timeout"] == expected
            assert len(waits) <= 1
            if waits:
                assert expected + 4 < waits[0] <= expected + 5
            assert not PluginHub._pending

        asyncio.run(exercise())

    @pytest.mark.parametrize(
        "args, config_timeout, expected",
        [
            (["import-model"], 30, 220),
            (["import-model", "--timeout", "180"], 30, 220),
            (["import-model", "--timeout", "300"], 30, 340),
            (["import-model", "--timeout", "60"], 600, 600),
        ],
    )
    def test_cli_budget_reaches_http_client(self, args, config_timeout, expected):
        response = MagicMock()
        response.json.return_value = {"success": True}
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "cli.commands.blender.get_config", return_value=CLIConfig(timeout=config_timeout)
            ),
            patch("cli.utils.connection._auth_headers", return_value={}),
            patch("cli.utils.connection.httpx.AsyncClient", return_value=client),
        ):
            result = CliRunner().invoke(blender, args)
        assert result.exit_code == 0, result.output
        assert client.post.call_args.kwargs["timeout"] == expected
        assert client.post.call_args.kwargs["json"]["params"]["action"] == "import_model"

    @pytest.mark.parametrize(
        "params, expected",
        [
            ({"timeoutSeconds": 0}, 35),
            ({"timeout_seconds": 60, "timeoutSeconds": 300}, 90),
            ({"timeout_seconds": None, "timeoutSeconds": 5}, 210),
            ({"timeoutSeconds": 99999}, 3630),
            ({"timeoutSeconds": "invalid"}, 210),
            ({"timeoutSeconds": float("nan")}, 210),
            ({"timeoutSeconds": float("inf")}, 210),
            ({"timeoutSeconds": True}, 210),
            ({"timeoutSeconds": "5.5"}, 210),
            ({"timeoutSeconds": 5.5}, 210),
            ({"timeoutSeconds": 300.0}, 330),
            ({"timeoutSeconds": " +300 "}, 330),
            ({"timeoutSeconds": "00000000000000300"}, 330),
            ({"timeoutSeconds": 2**31}, 210),
        ],
    )
    def test_timeout_aliases_and_bounds(self, params, expected):
        from transport.blender_timeout import blender_command_timeout

        assert blender_command_timeout(params) == expected

    def test_blender_budget_is_isolated_from_other_commands_and_sessions(self):
        async def exercise():
            messages = []
            sockets = {session: MagicMock() for session in ("blender-session", "other-session")}

            async def send_json(message):
                messages.append(message)
                PluginHub._pending[message["id"]]["future"].set_result({"success": True})

            for sock in sockets.values():
                sock.send_json = AsyncMock(side_effect=send_json)
            with (
                patch.object(
                    PluginHub,
                    "_get_connection",
                    AsyncMock(side_effect=lambda session: sockets[session]),
                ),
                patch.object(PluginHub, "_connections", sockets),
                patch.object(PluginHub, "_registry", None),
                patch.object(PluginHub, "_lock", asyncio.Lock()),
                patch.object(PluginHub, "_pending", {}),
            ):
                await PluginHub.send_command("blender-session", COMMAND, {"timeoutSeconds": 900})
                await PluginHub.send_command("other-session", "manage_gameobject", {})
                assert not PluginHub._pending
            assert [message["timeout"] for message in messages] == [930, PluginHub.COMMAND_TIMEOUT]
            assert sockets["blender-session"].send_json.call_count == 1
            assert sockets["other-session"].send_json.call_count == 1

        asyncio.run(exercise())

    def test_cancelled_blender_command_releases_pending_capacity_and_send_task(self):
        async def exercise():
            started = asyncio.Event()
            cancelled = asyncio.Event()
            sock = MagicMock()

            async def blocked_send(_message):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            sock.send_json = AsyncMock(side_effect=blocked_send)
            with (
                patch.object(PluginHub, "_get_connection", AsyncMock(return_value=sock)),
                patch.object(PluginHub, "_connections", {"test-session": sock}),
                patch.object(PluginHub, "_registry", None),
                patch.object(PluginHub, "_lock", asyncio.Lock()),
                patch.object(PluginHub, "_pending", {}),
            ):
                task = asyncio.create_task(PluginHub.send_command("test-session", COMMAND, {}))
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not PluginHub._pending
                assert cancelled.is_set()

        asyncio.run(exercise())


class TestBlenderLegacyTimeouts:
    @pytest.mark.parametrize("requested, expected", [(None, 210), (900, 930), (99999, 3630)])
    def test_blender_socket_budget_restores_timeout_and_does_not_change_other_commands(
        self,
        monkeypatch,
        tmp_path,
        requested,
        expected,
    ):
        import transport.legacy.unity_connection as legacy

        state = {"timeout": 30.0}
        sock = MagicMock()
        sock.gettimeout.side_effect = lambda: state["timeout"]
        sock.settimeout.side_effect = lambda value: state.update(timeout=value)
        conn = legacy.UnityConnection(port=6400, sock=sock, instance_id="Test@abc")
        monkeypatch.setattr(legacy.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(conn, "_ensure_live_connection", lambda: None)
        monkeypatch.setattr(legacy.time, "monotonic", lambda: 100.0)
        monkeypatch.setattr(legacy.config, "command_total_timeout", 10.0)
        budgets = []

        def receive(_sock, deadline):
            budgets.append((state["timeout"], deadline))
            return b'{"status":"success","result":{"success":true}}'

        monkeypatch.setattr(conn, "receive_full_response", receive)
        monkeypatch.setattr(legacy, "get_unity_connection", lambda instance=None: conn)
        params = {"action": "run_python"}
        if requested is not None:
            params["timeoutSeconds"] = requested
        assert legacy.send_command_with_retry("blender_bridge", params)["success"]
        assert budgets == [(expected, 100.0 + expected + 5)]
        assert state["timeout"] == 30.0
        assert legacy.config.command_total_timeout == 10.0
        assert conn.send_command("get_editor_state", {})["success"]
        assert budgets[-1] == (10.0, 110.0)
        assert state["timeout"] == 30.0

    @pytest.mark.parametrize(
        "failure", [TimeoutError("response timeout"), ConnectionError("lost reply")]
    )
    def test_blender_failure_after_dispatch_never_replays_python(
        self,
        monkeypatch,
        tmp_path,
        failure,
    ):
        import transport.legacy.unity_connection as legacy

        sock = MagicMock()
        sock.gettimeout.return_value = 30.0
        conn = legacy.UnityConnection(port=6400, sock=sock, instance_id="Test@abc")
        monkeypatch.setattr(legacy.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(conn, "_ensure_live_connection", lambda: None)
        monkeypatch.setattr(legacy.time, "sleep", lambda _: None)
        receive = MagicMock(side_effect=failure)
        monkeypatch.setattr(conn, "receive_full_response", receive)

        def reconnect(*args, **kwargs):
            conn.sock = sock
            return True

        monkeypatch.setattr(conn, "connect", reconnect)
        monkeypatch.setattr(legacy.stdio_port_registry, "get_instances", lambda **_: [])
        monkeypatch.setattr(legacy.stdio_port_registry, "get_instance", lambda _: None)
        monkeypatch.setattr(legacy.stdio_port_registry, "get_port", lambda _: 6400)
        with pytest.raises(type(failure), match=str(failure)):
            conn.send_command(
                "blender_bridge",
                {"action": "run_python", "code": "create_object()"},
                max_attempts=2,
            )
        assert sock.sendall.call_count == 1
        assert receive.call_count == 1
        assert conn.sock is None

    def test_blender_retries_connection_failure_before_dispatch(self, monkeypatch, tmp_path):
        import transport.legacy.unity_connection as legacy

        sock = MagicMock()
        sock.gettimeout.return_value = 30.0
        conn = legacy.UnityConnection(port=6400, instance_id="Test@abc")
        monkeypatch.setattr(legacy.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(conn, "_ensure_live_connection", lambda: None)
        monkeypatch.setattr(legacy.time, "sleep", lambda _: None)
        receive = MagicMock(return_value=b'{"status":"success","result":{"success":true}}')
        monkeypatch.setattr(conn, "receive_full_response", receive)
        attempts = []

        def reconnect(*args, **kwargs):
            attempts.append(conn.port)
            if len(attempts) == 1:
                return False
            conn.sock = sock
            return True

        monkeypatch.setattr(conn, "connect", reconnect)
        monkeypatch.setattr(legacy.stdio_port_registry, "get_instances", lambda **_: [])
        monkeypatch.setattr(legacy.stdio_port_registry, "get_instance", lambda _: None)
        monkeypatch.setattr(legacy.stdio_port_registry, "get_port", lambda _: 6400)
        assert conn.send_command("blender_bridge", {"action": "run_python"}, max_attempts=1)[
            "success"
        ]
        assert attempts == [6400, 6400]
        assert sock.sendall.call_count == 1

    def test_blender_application_error_mentioning_reload_is_never_replayed(self, monkeypatch):
        import transport.legacy.unity_connection as legacy

        response = {
            "success": False,
            "error": "Python modified an object, then failed: reload the addon",
        }
        conn = MagicMock()
        conn.send_command.return_value = response
        monkeypatch.setattr(legacy, "get_unity_connection", lambda instance=None: conn)
        monkeypatch.setattr(legacy.time, "sleep", lambda _: None)
        assert (
            legacy.send_command_with_retry("blender_bridge", {"action": "run_python"}) == response
        )
        assert conn.send_command.call_count == 1

    def test_blender_preflight_reload_can_retry_before_execution(self, monkeypatch, tmp_path):
        import transport.legacy.unity_connection as legacy

        sock = MagicMock()
        sock.gettimeout.return_value = 30.0
        conn = legacy.UnityConnection(port=6400, instance_id="Test@abc")
        monkeypatch.setattr(legacy.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(conn, "_ensure_live_connection", lambda: None)
        status_dir = tmp_path / ".unity-mcp"
        status_dir.mkdir()
        status_file = status_dir / "unity-mcp-status-abc.json"
        status_file.write_text('{"reloading":true}', encoding="utf-8")
        monkeypatch.setattr(legacy.time, "sleep", lambda _: status_file.unlink())
        monkeypatch.setattr(legacy, "get_unity_connection", lambda instance=None: conn)

        def reconnect(*args, **kwargs):
            conn.sock = sock
            return True

        monkeypatch.setattr(conn, "connect", reconnect)
        monkeypatch.setattr(
            conn,
            "receive_full_response",
            lambda *args, **kwargs: (
                b'{"status":"success","result":{"success":false,"error":"reload addon"}}'
            ),
        )
        result = legacy.send_command_with_retry(
            "blender_bridge", {"action": "run_python"}, instance_id="Test@abc"
        )
        assert result == {"success": False, "error": "reload addon"}
        assert sock.sendall.call_count == 1
