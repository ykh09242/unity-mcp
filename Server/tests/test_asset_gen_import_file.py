"""Tests for the import_model_file asset-gen tool and CLI command (local model import).

Pass-through tool: NO API keys, NO file bytes. Unity transport fully mocked.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner
from fastmcp import Client, FastMCP

from cli.commands.asset_gen import asset_gen
from cli.utils.config import CLIConfig
from services.registry import get_registered_tools
from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool

from services.tools import import_model_file as mod
from services.tools.import_model_file import import_model_file


COMMAND = "import_model_file"
ALLOWED_KEYS = {
    "sourcePath",
    "name",
    "outputFolder",
    "targetSize",
    "animationType",
    "archiveModelPath",
}


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
            result = asyncio.run(import_model_file(ctx, **kwargs))
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
        with patch("cli.commands.asset_gen.get_config", return_value=mock_config):
            with patch(
                "cli.commands.asset_gen.run_command",
                return_value={"success": True, "message": "OK", "data": {}},
            ) as mock_run:
                result = runner.invoke(asset_gen, args)
                return result, mock_run

    return _invoke


class TestImportModelFileRegistration:
    def test_tool_registered_under_asset_gen_group(self):
        tools = get_registered_tools()
        tool = next((t for t in tools if t["name"] == "import_model_file"), None)
        assert tool is not None
        assert tool["group"] == "asset_gen"


class TestImportModelFileRouting:
    @pytest.mark.parametrize(
        "source",
        [
            "",
            " ",
            "relative.obj",
            "Assets/../external.obj",
            "Assets\\..\\external.obj",
            "C:external.obj",
            "file:///Assets/source.obj",
            "Assets/source.obj\x00",
        ],
    )
    def test_invalid_source_rejected_before_context_or_transport(self, source):
        ctx = MagicMock()
        with patch.object(mod, "get_unity_instance_from_context", new=AsyncMock()) as instance:
            with patch.object(mod, "send_with_unity_instance", new=AsyncMock()) as send:
                result = asyncio.run(import_model_file(ctx, source_path=source))
        assert result["success"] is False
        instance.assert_not_awaited()
        send.assert_not_awaited()

    @pytest.mark.parametrize(
        "source",
        [
            "Assets/source.obj",
            "Assets\\source.obj",
            "C:/Project/Assets/source.obj",
            "/Project/Assets/source.obj",
        ],
    )
    def test_admissible_source_forwarded_for_unity_physical_validation(self, source):
        _, sent = _call_tool(source_path=source)
        assert _sent_params(sent) == {"sourcePath": source}

    def test_routes_to_command_with_param_mapping(self):
        _, sent = _call_tool(
            source_path="Assets/cube.fbx",
            name="Cube",
            output_folder="Assets/Generated/Imported",
            target_size=2.0,
        )
        assert _sent_command(sent) == COMMAND
        params = _sent_params(sent)
        assert params["sourcePath"] == "Assets/cube.fbx"
        assert params["name"] == "Cube"
        assert params["outputFolder"] == "Assets/Generated/Imported"
        assert params["targetSize"] == 2.0
        assert (
            "source_path" not in params
            and "output_folder" not in params
            and "target_size" not in params
        )

    def test_none_values_stripped(self):
        _, sent = _call_tool(source_path="Assets/x.obj")
        assert _sent_params(sent) == {"sourcePath": "Assets/x.obj"}

    def test_animation_type_mapped(self):
        _, sent = _call_tool(source_path="Assets/rig.fbx", animation_type="generic")
        params = _sent_params(sent)
        assert params["animationType"] == "generic"
        assert "animation_type" not in params
        assert set(params.keys()).issubset(ALLOWED_KEYS)

    def test_no_secret_keys_in_payload(self):
        _, sent = _call_tool(
            source_path="Assets/a.glb",
            name="N",
            output_folder="Assets/Generated/Imported",
            target_size=1.0,
        )
        params = _sent_params(sent)
        assert set(params.keys()).issubset(ALLOWED_KEYS)
        joined = " ".join(params.keys()).lower()
        for forbidden in ("key", "secret", "token", "apikey", "password"):
            assert forbidden not in joined

    def test_non_dict_response_guarded(self):
        ctx = MagicMock()
        with patch.object(mod, "get_unity_instance_from_context", new=AsyncMock(return_value="u")):
            with patch.object(mod, "send_with_unity_instance", new=AsyncMock(return_value=None)):
                result = asyncio.run(import_model_file(ctx, source_path="Assets/x.obj"))
        assert result["success"] is False


class TestImportModelFileCLI:
    def test_import_model_file_cli(self, cli_runner):
        result, mock_run = cli_runner(
            [
                "import-model-file",
                "--source-path",
                "Assets/cube.fbx",
                "--name",
                "Cube",
                "--output-folder",
                "Assets/Props",
                "--target-size",
                "1.5",
            ]
        )
        assert result.exit_code == 0
        command = mock_run.call_args.args[0]
        params = mock_run.call_args.args[1]
        assert command == COMMAND
        assert params["sourcePath"] == "Assets/cube.fbx"
        assert params["name"] == "Cube"
        assert params["outputFolder"] == "Assets/Props"
        assert params["targetSize"] == 1.5
        assert set(params.keys()).issubset(ALLOWED_KEYS)

    def test_import_model_file_cli_animation_type(self, cli_runner):
        result, mock_run = cli_runner(
            [
                "import-model-file",
                "--source-path",
                "Assets/rig.fbx",
                "--animation-type",
                "humanoid",
            ]
        )
        assert result.exit_code == 0
        params = mock_run.call_args.args[1]
        assert params["animationType"] == "humanoid"
        assert set(params.keys()).issubset(ALLOWED_KEYS)


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
def test_import_file_contract_through_registered_sdk_tool(protocol):
    async def exercise():
        entry = next(tool for tool in get_registered_tools() if tool["name"] == COMMAND)
        server = FastMCP("import-file-contract")
        wrapped = log_execution(COMMAND, "Tool")(entry["func"])
        server.tool(name=entry["name"], description=entry["description"], **entry["kwargs"])(
            telemetry_tool(COMMAND)(wrapped)
        )
        failure = {"success": False, "error": "Unity fixture rejects an external source"}
        with patch.object(
            mod, "get_unity_instance_from_context", new=AsyncMock(return_value="unity-fixture")
        ) as instance:
            with patch.object(
                mod, "send_with_unity_instance", new=AsyncMock(return_value=failure)
            ) as send:
                async with Client(server, mode=protocol) as client:
                    for source in (
                        "Assets/source.obj",
                        "Assets\\source.obj",
                        "/Project/Assets/source.obj",
                        "C:/Project/Assets/source.obj",
                        "/External/source.obj",
                    ):
                        result = await client.call_tool(
                            COMMAND,
                            {
                                "source_path": source,
                                "name": "Model",
                                "output_folder": "Assets/Models",
                                "target_size": 2,
                                "animation_type": "humanoid",
                            },
                        )
                        assert result.structured_content == failure
                        assert send.call_args.args[2:] == (
                            COMMAND,
                            {
                                "sourcePath": source,
                                "name": "Model",
                                "outputFolder": "Assets/Models",
                                "targetSize": 2,
                                "animationType": "humanoid",
                            },
                        )
                    instance.reset_mock()
                    send.reset_mock()
                    for source in (
                        "relative.obj",
                        "Assets/../external.obj",
                        "C:external.obj",
                        "file:///source.obj",
                        "",
                    ):
                        result = await client.call_tool(COMMAND, {"source_path": source})
                        assert result.structured_content["success"] is False
                    instance.assert_not_awaited()
                    send.assert_not_awaited()

    asyncio.run(exercise())


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("import_stage", ["partial", "saved", "registered"])
def test_dependency_bundle_failure_evidence_survives_registered_sdk_tool(protocol, import_stage):
    async def exercise():
        entry = next(tool for tool in get_registered_tools() if tool["name"] == COMMAND)
        server = FastMCP("import-bundle-evidence")
        wrapped = log_execution(COMMAND, "Tool")(entry["func"])
        server.tool(name=entry["name"], description=entry["description"], **entry["kwargs"])(
            telemetry_tool(COMMAND)(wrapped)
        )
        partial = import_stage == "partial"
        data = {
            "dependency_bundle": {
                "inspection": "complete",
                "inspection_scope": "supported_local_dependencies",
                "stage": "partial" if partial else "staged",
                "root_path": "Assets/Models/OwnedBundle",
                "planned_file_count": 3,
                "staged_file_count": 1 if partial else 3,
                "staged_bytes": 14 if partial else 256,
            }
        }
        if partial:
            data["dependency_bundle"]["error_code"] = "dependency_stage_failed"
            data["dependency_bundle"]["reference"] = "Textures/owned.png"
        else:
            data.update(
                {
                    "asset_path": "Assets/Models/OwnedBundle/model.gltf",
                    "asset_guid": "owned-guid" if import_stage == "registered" else "",
                    "model_import_stage": import_stage,
                    "model_import_error_code": (
                        "missing_gltf_importer"
                        if import_stage == "registered"
                        else "model_not_registered"
                    ),
                }
            )
            data["dependency_bundle"]["main_asset_path"] = data["asset_path"]
        failure = {
            "success": False,
            "error": "Owned fixture failure; output retained.",
            "data": data,
        }
        with patch.object(
            mod, "get_unity_instance_from_context", new=AsyncMock(return_value="unity-fixture")
        ):
            with patch.object(
                mod, "send_with_unity_instance", new=AsyncMock(return_value=failure)
            ) as send:
                async with Client(server, mode=protocol) as client:
                    result = await client.call_tool(
                        COMMAND,
                        {
                            "source_path": "Assets/Source/model.gltf",
                            "name": "OwnedBundle",
                            "output_folder": "Assets/Models",
                        },
                    )
                assert result.structured_content == failure
                send.assert_awaited_once()
                assert send.call_args.args[2:] == (
                    COMMAND,
                    {
                        "sourcePath": "Assets/Source/model.gltf",
                        "name": "OwnedBundle",
                        "outputFolder": "Assets/Models",
                    },
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("selector", ["main/model.obj", r"main\model.obj", "模型/model.FBX"])
def test_archive_selector_mapping(selector):
    _, sent = _call_tool(source_path="Assets/source.zip", archive_model_path=selector)
    assert _sent_params(sent) == {
        "sourcePath": "Assets/source.zip",
        "archiveModelPath": selector.replace("\\", "/"),
    }


@pytest.mark.parametrize(
    "selector",
    [
        "",
        " ",
        "../model.obj",
        "./model.obj",
        "a//model.obj",
        "/model.obj",
        "C:/model.obj",
        "//host/model.obj",
        "model.obj/",
        "model.png",
        "a\n.obj",
        "a" * 509 + ".obj",
        "/".join(["a"] * 32) + "/model.obj",
    ],
)
def test_archive_selector_invalid_before_transport(selector):
    with patch.object(mod, "get_unity_instance_from_context", new=AsyncMock()) as context:
        with patch.object(mod, "send_with_unity_instance", new=AsyncMock()) as send:
            result = asyncio.run(
                import_model_file(
                    MagicMock(), source_path="Assets/source.zip", archive_model_path=selector
                )
            )
    assert result["success"] is False
    assert result["data"]["model_import_error_code"] == "archive_model_path_invalid"
    context.assert_not_awaited()
    send.assert_not_awaited()


def test_archive_selector_nonarchive_rejected_before_transport():
    with patch.object(mod, "send_with_unity_instance", new=AsyncMock()) as send:
        result = asyncio.run(
            import_model_file(
                MagicMock(), source_path="Assets/model.obj", archive_model_path="model.obj"
            )
        )
    assert result["data"]["model_import_error_code"] == "archive_selector_requires_zip"
    send.assert_not_awaited()


def test_archive_selector_cli(cli_runner):
    result, command = cli_runner(
        [
            "import-model-file",
            "--source-path",
            "Assets/source.zip",
            "--archive-model-path",
            "main/model.obj",
        ]
    )
    assert result.exit_code == 0, result.output
    assert command.call_args.args[:2] == (
        COMMAND,
        {"sourcePath": "Assets/source.zip", "archiveModelPath": "main/model.obj"},
    )


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("success", [True, False])
def test_archive_selection_evidence_through_registered_sdk(protocol, success):
    async def exercise():
        entry = next(tool for tool in get_registered_tools() if tool["name"] == COMMAND)
        server = FastMCP("archive-selection-contract")
        wrapped = log_execution(COMMAND, "Tool")(entry["func"])
        server.tool(name=entry["name"], description=entry["description"], **entry["kwargs"])(
            telemetry_tool(COMMAND)(wrapped)
        )
        response = {
            "success": success,
            "data": {
                "asset_path": "Assets/Output/model.obj" if success else "Assets/Output/archive.zip",
                "asset_guid": "owned-guid",
                "model_import_stage": "usable" if success else "registered",
                "archive_selection": {
                    "archive_path": "Assets/Output/archive.zip",
                    "requested_path": "main/model.obj",
                    "selected_path": "main/model.obj" if success else None,
                    "candidates": ["lod/model.obj", "main/model.obj"],
                    "candidate_count": 2,
                    "candidates_complete": True,
                    "extraction": "complete" if success else "not_started",
                },
            },
        }
        if not success:
            response["error"] = "Owned missing selection"
            response["data"]["model_import_error_code"] = "archive_model_not_found"
        with patch.object(
            mod, "get_unity_instance_from_context", new=AsyncMock(return_value="owned")
        ):
            with patch.object(
                mod, "send_with_unity_instance", new=AsyncMock(return_value=response)
            ) as send:
                async with Client(server, mode=protocol) as client:
                    schemas = await client.list_tools()
                    schema = next(tool for tool in schemas if tool.name == COMMAND).input_schema
                    assert "archive_model_path" in schema["properties"]
                    result = await client.call_tool(
                        COMMAND,
                        {
                            "source_path": "Assets/source.zip",
                            "archive_model_path": "main/model.obj",
                        },
                    )
                assert result.structured_content == response
                send.assert_awaited_once()
                assert send.call_args.args[3] == {
                    "sourcePath": "Assets/source.zip",
                    "archiveModelPath": "main/model.obj",
                }

    asyncio.run(exercise())
