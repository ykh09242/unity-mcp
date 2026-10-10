"""Owned standalone process, bundle integrity and truthful finalization regressions."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

from cli.main import cli
from cli.utils import play_scenario_player as player
from cli.utils.play_scenario_player import PlayerLaunchError, PlayerRunOptions, run_player
from click.testing import CliRunner
from models.play_scenarios import PlayScenario
from pydantic import ValidationError
import pytest

from .test_manage_play_scenario import DEFINITION


@pytest.fixture
def bundle(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "MCPScenarioPlayer.exe").write_bytes(b"explicit fixture placeholder")
    definition = PlayScenario.model_validate(DEFINITION).model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(definition, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    manifest = {
        "schema_version": 1,
        "scenario_name": definition["name"],
        "definition_hash": sha256(canonical.encode("utf-8")).hexdigest(),
        "executable": "MCPScenarioPlayer.exe",
        "scene_paths": ["Assets/Scenes/Menu.unity"],
        "definition": definition,
        "definition_json": canonical,
        "unity_version": "fixture",
        "package_version": "fixture",
    }
    # Keep the fixture scene list aligned with the authored load_scene.
    manifest["scene_paths"] = sorted(
        {step["scene"] for step in definition["steps"] if "scene" in step}
    )
    (build / "scenario-bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    return build, tmp_path / "output", manifest


@pytest.fixture
def child(monkeypatch, tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        "import json, pathlib, sys, time\n"
        "request_path=pathlib.Path(sys.argv[1])\n"
        "request=json.loads(request_path.read_text())\n"
        "manifest=json.loads(pathlib.Path(sys.argv[2]).read_text())\n"
        "mode=sys.argv[3]\n"
        "if request['schema_version'] == 2:\n"
        " import os\n"
        " progress={'schema_version':1,'job_id':request['job_id'],'definition_hash':request['definition_hash'],'build_id':request['build_id'],'payload_hash':request['payload_hash'],'build_source_revision':request['build_source_revision'],'process_id':os.getpid(),'sequence':1,'main_loop_sequence':1,'heartbeat_unix_ms':int(time.time()*1000),'elapsed_ms':1,'phase':'running','iteration':1,'stage':'steps','step_index':0,'status':'running'}\n"
        " (request_path.parent/'progress.json').write_text(json.dumps(progress))\n"
        "print('owned output', flush=True)\n"
        "if mode == 'loud': sys.stdout.write('x'*2000000);sys.stdout.flush()\n"
        "if mode == 'crash': sys.exit(7)\n"
        "if mode == 'missing': sys.exit(0)\n"
        "if mode == 'hang': time.sleep(30)\n"
        "if mode == 'cancel':\n"
        " while not (request_path.parent/'cancel').exists(): time.sleep(.02)\n"
        "status='cancelled' if mode == 'cancel' else ('failed' if mode == 'failed' else 'succeeded')\n"
        "code=0 if status == 'succeeded' else 1\n"
        "report={**request,'scenario':manifest['definition'],'status':status,'execution_environment':'player',\n"
        " 'finalization_state':'completed','runner_resources_released':True,'exit_code':code,'started_unix_ms':1,'finished_unix_ms':2,\n"
        " 'reproduction':{'definition_hash':manifest['definition_hash'],'source_revision':request.get('source_revision'),'unity_version':manifest['unity_version'],'package_version':manifest['package_version']}}\n"
        "report['player_schema_version']=request['schema_version']\n"
        "if request['schema_version'] == 2:\n"
        " report['reproduction'].update({key:request[key] for key in ('build_id','payload_hash','build_source_revision')})\n"
        " report['reproduction']['payload_verification']='launcher_admission'\n"
        "report.pop('schema_version');report.pop('scenario_name');report.pop('definition_hash')\n"
        "if mode == 'wrong-id': report['job_id']='f'*32\n"
        "if mode == 'wrong-hash': report['reproduction']['definition_hash']='f'*64\n"
        "if mode == 'wrong-finalization': report['finalization_state']='finalizing'\n"
        "if mode == 'wrong-exit': report['exit_code']=1\n"
        "if mode == 'unreleased': report['runner_resources_released']=False\n"
        "if mode == 'wrong-revision': report['reproduction']['source_revision']='other'\n"
        "if mode == 'export-error': report['report_error']='disk full'\n"
        "(request_path.parent/'run.json').write_text(json.dumps(report))\n"
        "sys.exit(code)\n",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen
    modes = ["success"]
    owned = []

    def start(arguments, **kwargs):
        assert Path(arguments[0]).name == "MCPScenarioPlayer.exe"
        assert arguments[1] == "--mcp-scenario-request"
        assert kwargs["shell"] is False
        process = real_popen(
            [
                sys._base_executable,
                str(script),
                arguments[2],
                str(Path(kwargs["cwd"]) / "scenario-bundle.json"),
                modes[0],
            ],
            **kwargs,
        )
        owned.append(process)
        return process

    monkeypatch.setattr(player.subprocess, "Popen", start)
    return modes, owned


def test_foreground_player_owns_unique_output_and_real_success_report(bundle, child):
    build, output, _manifest = bundle
    first = run_player(PlayerRunOptions(build, output, source_revision="revision-fixture"))
    second = run_player(PlayerRunOptions(build, output))
    assert first.exit_code == second.exit_code == 0
    assert first.directory != second.directory
    request = json.loads((first.directory / "request.json").read_text())
    assert request["repeat_count"] == 1 and request["timeout_seconds"] == 300
    assert request["source_revision"] == "revision-fixture"
    assert first.report["job_id"] == request["job_id"]
    assert (first.directory / "definition.json").is_file()
    assert b"owned output" in (first.directory / "player.log").read_bytes()
    assert all(process.poll() == 0 for process in child[1])


@pytest.mark.parametrize(
    "mode",
    [
        "crash",
        "missing",
        "wrong-id",
        "wrong-hash",
        "wrong-finalization",
        "wrong-exit",
        "wrong-revision",
        "unreleased",
    ],
)
def test_missing_mismatched_or_crashed_player_never_succeeds(bundle, child, mode):
    build, output, _manifest = bundle
    child[0][0] = mode
    with pytest.raises((PlayerLaunchError, ValidationError)):
        run_player(PlayerRunOptions(build, output))
    assert child[1][0].poll() is not None


@pytest.mark.parametrize("mode", ["failed", "export-error"])
def test_actual_failure_or_report_persistence_error_returns_nonzero(bundle, child, mode):
    build, output, _manifest = bundle
    child[0][0] = mode
    outcome = run_player(PlayerRunOptions(build, output))
    assert outcome.exit_code == 1
    assert outcome.report["status"] == ("failed" if mode == "failed" else "succeeded")


def test_timeout_cancels_once_and_waits_for_actual_native_finalization(bundle, child):
    build, output, _manifest = bundle
    child[0][0] = "cancel"
    outcome = run_player(PlayerRunOptions(build, output, timeout_seconds=1, cleanup_wait_seconds=1))
    assert outcome.exit_code == 1 and outcome.report["status"] == "cancelled"
    assert (outcome.directory / "cancel").read_text() == "cancel\n"
    assert child[1][0].poll() == 1


def test_unresponsive_owned_player_is_killed_after_bounded_cleanup(bundle, child):
    build, output, _manifest = bundle
    child[0][0] = "hang"
    with pytest.raises(PlayerLaunchError):
        run_player(PlayerRunOptions(build, output, timeout_seconds=1, cleanup_wait_seconds=0.1))
    assert child[1][0].poll() is not None


def test_ctrl_c_requires_actual_cleanup_report_before_return(bundle, child, monkeypatch):
    build, output, _manifest = bundle
    child[0][0] = "cancel"
    real_sleep = player.time.sleep
    interrupted = []

    def interrupt_once(seconds):
        if not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr(player.time, "sleep", interrupt_once)
    outcome = run_player(PlayerRunOptions(build, output, cleanup_wait_seconds=1))
    assert outcome.exit_code == 130 and outcome.report["status"] == "cancelled"
    assert child[1][0].poll() == 1


@pytest.mark.parametrize("change", ["hash", "executable", "scenes", "capabilities", "unknown"])
def test_invalid_bundle_is_rejected_before_launch(bundle, child, change):
    build, output, manifest = bundle
    if change == "hash":
        manifest["definition_hash"] = "f" * 64
    elif change == "executable":
        manifest["executable"] = "../outside.exe"
    elif change == "scenes":
        manifest["scene_paths"] = ["Assets/Unrelated.unity"]
    elif change == "unknown":
        manifest["unexpected"] = True
    else:
        manifest["definition"]["resources"]["enabled"] = True
        manifest["definition_json"] = json.dumps(
            manifest["definition"], sort_keys=True, separators=(",", ":")
        )
        manifest["definition_hash"] = sha256(manifest["definition_json"].encode()).hexdigest()
    (build / "scenario-bundle.json").write_text(json.dumps(manifest))
    with pytest.raises((PlayerLaunchError, ValidationError)):
        run_player(PlayerRunOptions(build, output))
    assert child[1] == [] and not output.exists()


def test_cli_exports_actual_native_report_without_editor_connection(bundle, child):
    build, output, _manifest = bundle
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "player-run",
            str(build),
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["data"]
    directory = output / report["job_id"]
    assert json.loads((directory / "run.json").read_text()) == report
    xml = ElementTree.parse(directory / "junit.xml").getroot()
    assert xml.attrib["tests"] == "1" and xml.attrib["errors"] == "0"
    assert xml.find("testcase").attrib["name"] == report["scenario"]["name"]


def test_player_output_is_drained_after_retained_log_limit(bundle, child):
    build, output, _manifest = bundle
    child[0][0] = "loud"
    outcome = run_player(PlayerRunOptions(build, output))
    assert outcome.exit_code == 0
    assert (outcome.directory / "player.log").stat().st_size == player.PROCESS_LOG_LIMIT


def test_player_export_failure_never_returns_cli_success(bundle, child, monkeypatch):
    from cli.commands import play_scenario as commands

    def fail_export(*_arguments):
        raise OSError("fixture export failure")

    monkeypatch.setattr(commands, "write_player_artifacts", fail_export)
    build, output, _manifest = bundle
    result = CliRunner().invoke(
        cli, ["play-scenario", "player-run", str(build), "--output-dir", str(output)]
    )
    assert result.exit_code == 1
    assert "export failure" in result.output


def test_existing_job_directory_is_never_overwritten_or_launched(bundle, child, monkeypatch):
    from types import SimpleNamespace

    build, output, _manifest = bundle
    stale = output / ("a" * 32)
    stale.mkdir(parents=True)
    (stale / "run.json").write_text("stale evidence")
    monkeypatch.setattr(player, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    with pytest.raises(FileExistsError):
        run_player(PlayerRunOptions(build, output))
    assert (stale / "run.json").read_text() == "stale evidence"
    assert child[1] == []


def test_large_or_missing_manifest_is_rejected_before_process_start(bundle, child):
    build, output, _manifest = bundle
    manifest_path = build / "scenario-bundle.json"
    manifest_path.write_bytes(b"x" * (player.MANIFEST_LIMIT + 1))
    with pytest.raises(PlayerLaunchError):
        run_player(PlayerRunOptions(build, output))
    manifest_path.unlink()
    with pytest.raises(FileNotFoundError):
        run_player(PlayerRunOptions(build, output))
    assert child[1] == []


def test_player_junit_records_client_deadline_error_without_rewriting_native_json(bundle, child):
    from cli.utils.play_scenario_reports import write_player_artifacts

    build, output, _manifest = bundle
    outcome = run_player(PlayerRunOptions(build, output))
    original = (outcome.directory / "run.json").read_bytes()
    _json_path, xml_path = write_player_artifacts(
        outcome.report, outcome.directory, "Player launcher deadline expired"
    )
    xml = ElementTree.parse(xml_path).getroot()
    assert xml.attrib["errors"] == "1"
    assert (outcome.directory / "run.json").read_bytes() == original


@pytest.mark.parametrize("capability", ["metrics", "screenshot", "property"])
def test_each_unsupported_capability_is_rejected_before_launch(bundle, child, capability):
    build, output, manifest = bundle
    if capability == "metrics":
        manifest["definition"]["metrics"]["enabled"] = True
    elif capability == "screenshot":
        manifest["definition"]["diagnostics"]["screenshot_on_failure"] = True
    else:
        manifest["definition"]["steps"].append(
            {
                "name": "Scalar property",
                "action": "wait_object",
                "target": "Player",
                "component": "State",
                "property": {"path": "counter", "equals": 1},
            }
        )
    manifest["definition_json"] = json.dumps(
        manifest["definition"], sort_keys=True, separators=(",", ":")
    )
    manifest["definition_hash"] = sha256(manifest["definition_json"].encode()).hexdigest()
    (build / "scenario-bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(PlayerLaunchError, match="unsupported runtime capabilities"):
        run_player(PlayerRunOptions(build, output))
    assert child[1] == []


def test_native_null_step_options_round_trip_without_weakening_authored_schema(bundle, child):
    build, output, manifest = bundle
    optionals = {
        "scene",
        "target",
        "target_id",
        "reset_ids",
        "click_mode",
        "count",
        "active",
        "component",
        "property",
        "stable_for_ms",
    }
    for step in manifest["definition"]["steps"]:
        for field in optionals - set(step):
            step[field] = None
    manifest["definition_json"] = json.dumps(
        manifest["definition"], sort_keys=True, separators=(",", ":")
    )
    manifest["definition_hash"] = sha256(manifest["definition_json"].encode()).hexdigest()
    (build / "scenario-bundle.json").write_text(json.dumps(manifest))
    outcome = run_player(PlayerRunOptions(build, output))
    assert outcome.exit_code == 0
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(manifest["definition"])


def test_report_resource_release_proof_rejects_bool_coercion(bundle):
    _build, _output, manifest = bundle
    with pytest.raises(ValidationError):
        player.PlayerFinalReport.model_validate(
            {
                "job_id": "a" * 32,
                "scenario": manifest["definition"],
                "status": "succeeded",
                "execution_environment": "player",
                "finalization_state": "completed",
                "exit_code": 0,
                "repeat_count": 1,
                "timeout_seconds": 300,
                "reproduction": {},
                "runner_resources_released": 1,
                "started_unix_ms": 1,
                "finished_unix_ms": 2,
            }
        )


@pytest.mark.parametrize("status", ["succeeded", "failed", "timed_out"])
def test_player_junit_preserves_native_interval_metadata_and_json_bytes(tmp_path, status):
    from cli.utils.play_scenario_reports import write_player_artifacts

    raw = {
        "job_id": "a" * 32,
        "scenario": {"name": "repeat-duration"},
        "status": status,
        "started_unix_ms": 1791580000123,
        "finished_unix_ms": 1791580005610,
        "repeat_count": 2,
        "timeout_seconds": 60,
        "reproduction": {
            "definition_hash": "b" * 64,
            "source_revision": "duration-fixture",
            "unity_version": "6000.0.69f1",
            "package_version": "fixture-version",
        },
        "unknown_native_evidence": {"preserved": True},
    }
    original = (json.dumps(raw, indent=3) + "\n").encode("utf-8")
    (tmp_path / "run.json").write_bytes(original)
    json_path, xml_path = write_player_artifacts(raw, tmp_path)
    xml = ElementTree.parse(xml_path).getroot()
    case = xml.find("testcase")
    assert xml.attrib["time"] == case.attrib["time"] == "5.487"
    suite_properties = {
        entry.attrib["name"]: entry.attrib["value"] for entry in xml.findall("properties/property")
    }
    assert suite_properties["repeat_count"] == "2"
    assert suite_properties["timeout_seconds"] == "60"
    assert suite_properties["source_revision"] == "duration-fixture"
    case_properties = {
        entry.attrib["name"]: entry.attrib["value"] for entry in case.findall("properties/property")
    }
    assert case_properties["job_id"] == "a" * 32
    assert case_properties["unity_version"] == "6000.0.69f1"
    assert case_properties["package_version"] == "fixture-version"
    assert xml.attrib["failures"] == ("1" if status == "failed" else "0")
    assert xml.attrib["errors"] == ("1" if status == "timed_out" else "0")
    assert json_path.read_bytes() == original
