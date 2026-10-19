"""Frozen multi-definition Player suite admission and real owned process orchestration."""

from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

from click.testing import CliRunner
from cli.main import cli
from cli.utils import play_scenario_player as player
from models.play_scenarios import PlayScenario
import pytest


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@pytest.fixture
def suite_bundle(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    executable = b"controlled process fixture; not a Unity Player"
    (build / "MCPScenarioPlayer.exe").write_bytes(executable)
    children = []
    for name in ("first", "second", "third"):
        definition = PlayScenario.model_validate(
            {
                "name": name,
                "steps": [
                    {"name": "load", "action": "load_scene", "scene": f"Assets/{name}.unity"}
                ],
            }
        ).model_dump(mode="json", exclude_none=True)
        encoded = canonical(definition)
        children.append(
            {
                "scenario_name": name,
                "definition": definition,
                "definition_json": encoded,
                "definition_hash": sha256(encoded.encode()).hexdigest(),
            }
        )
    inventory = [
        {
            "path": "MCPScenarioPlayer.exe",
            "size_bytes": len(executable),
            "sha256": sha256(executable).hexdigest(),
        }
    ]
    manifest = {
        "schema_version": 3,
        "suite_name": "three-frozen",
        "failure_policy": "continue",
        "isolation": "fresh-process",
        "children": children,
        "build_id": "a" * 32,
        "build_source_revision": None,
        "unity_version": "controlled-fixture",
        "package_version": "controlled-fixture",
        "executable": "MCPScenarioPlayer.exe",
        "scene_paths": [f"Assets/{name}.unity" for name in ("first", "second", "third")],
        "payload_inventory": inventory,
        "payload_inventory_json": canonical(inventory),
        "payload_hash": sha256(canonical(inventory).encode()).hexdigest(),
    }
    manifest["suite_hash"] = framed_suite_hash(manifest)
    (build / "scenario-bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    return build, tmp_path / "output", manifest


@pytest.fixture
def owned_children(monkeypatch, tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        r"""
import json, os, pathlib, sys, time
request_path=pathlib.Path(sys.argv[1]); request=json.loads(request_path.read_text())
manifest=json.loads(pathlib.Path(sys.argv[2]).read_text()); modes=json.loads(sys.argv[3])
index=next(i for i,c in enumerate(manifest['children']) if c['scenario_name']==request['scenario_name'])
child=manifest['children'][index]; mode=modes.get(child['scenario_name'],'succeeded')
wrong_suite=mode=='wrong-suite'
evidence_mode=mode if mode in ('progress-error','missing-heartbeat','invalid-heartbeat','report-error') else None
if evidence_mode: mode='failed' if evidence_mode=='report-error' else 'succeeded'
if wrong_suite: mode='succeeded'
progress={'schema_version':1,'job_id':request['job_id'],'definition_hash':request['definition_hash'],'build_id':request['build_id'],'payload_hash':request['payload_hash'],'build_source_revision':request['build_source_revision'],'process_id':os.getpid(),'sequence':1,'main_loop_sequence':1,'heartbeat_unix_ms':int(time.time()*1000),'elapsed_ms':1,'phase':'running','iteration':1,'stage':'steps','step_index':0,'status':'running'}
if evidence_mode=='invalid-heartbeat': progress['job_id']='f'*32
if evidence_mode!='missing-heartbeat': (request_path.parent/'progress.json').write_text(json.dumps(progress))
if mode=='wait_cancel':
 while not (request_path.parent/'cancel').exists(): time.sleep(.01)
 mode='cancelled'
code=0 if mode=='succeeded' else 1
report={'job_id':request['job_id'],'scenario':child['definition'],'status':mode,'repeat_count':request['repeat_count'],'timeout_seconds':request['timeout_seconds'],'execution_environment':'player','finalization_state':'completed','runner_resources_released':True,'exit_code':code,'started_unix_ms':1,'finished_unix_ms':2,'player_schema_version':2,'player_suite':{'name':manifest['suite_name'],'suite_hash':manifest['suite_hash'],'child_index':index,'isolation':'fresh-process'},'reproduction':{'definition_hash':child['definition_hash'],'source_revision':request.get('source_revision'),'unity_version':manifest['unity_version'],'package_version':manifest['package_version'],'build_id':request['build_id'],'payload_hash':request['payload_hash'],'build_source_revision':request['build_source_revision'],'payload_verification':'launcher_admission'}}
if wrong_suite: report['player_suite']['child_index']=(index+1)%3
if evidence_mode=='progress-error': report['progress_error']='controlled progress publication failure'
if evidence_mode=='report-error': report['report_error']='Failure diagnostics unavailable: IOException: controlled capture failure'
(request_path.parent/'run.json').write_text(json.dumps(report)); sys.exit(code)
""",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen
    modes, processes = {}, []

    def launch(arguments, **kwargs):
        assert Path(arguments[0]).name == "MCPScenarioPlayer.exe"
        assert kwargs["shell"] is False
        process = real_popen(
            [
                sys._base_executable,
                str(script),
                arguments[2],
                str(Path(kwargs["cwd"]) / "scenario-bundle.json"),
                json.dumps(modes),
            ],
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(player.subprocess, "Popen", launch)
    return modes, processes


def api():
    return importlib.import_module("cli.utils.play_scenario_player_suite")


def test_three_frozen_children_get_distinct_processes_reports_identities_and_junit(
    suite_bundle, owned_children
):
    build, output, manifest = suite_bundle
    module = api()
    result = module.run_player_suite(
        module.PlayerSuiteOptions(build_directory=build, output_directory=output)
    )
    assert result.exit_code == 0
    assert [entry["name"] for entry in result.report["scenarios"]] == ["first", "second", "third"]
    assert len({process.pid for process in owned_children[1]}) == 3
    assert all(process.poll() == 0 for process in owned_children[1])
    assert len({entry["job_id"] for entry in result.report["scenarios"]}) == 3
    assert [entry["definition_hash"] for entry in result.report["scenarios"]] == [
        child["definition_hash"] for child in manifest["children"]
    ]
    xml = ElementTree.parse(result.directory / "junit.xml").getroot()
    assert xml.attrib["tests"] == "3" and xml.attrib["errors"] == "0"
    assert len(list(result.directory.glob("*/junit.xml"))) == 3
    assert json.loads((build / "scenario-bundle.json").read_text()) == manifest


@pytest.mark.parametrize(
    "policy,expected",
    [
        ("stop", ["succeeded", "failed", "skipped"]),
        ("continue", ["succeeded", "failed", "succeeded"]),
    ],
)
def test_child_failure_policy_does_not_replay_or_invent_unstarted_results(
    suite_bundle, owned_children, policy, expected
):
    build, output, _manifest = suite_bundle
    owned_children[0]["second"] = "failed"
    module = api()
    result = module.run_player_suite(
        module.PlayerSuiteOptions(
            build_directory=build, output_directory=output, failure_policy=policy
        )
    )
    assert result.exit_code == 1 and result.report["status"] == "failed"
    assert [entry["status"] for entry in result.report["scenarios"]] == expected
    assert len(owned_children[1]) == (2 if policy == "stop" else 3)
    assert all(process.poll() is not None for process in owned_children[1])


def test_cancelled_child_stops_even_continue_policy(suite_bundle, owned_children):
    build, output, _manifest = suite_bundle
    owned_children[0]["first"] = "cancelled"
    module = api()
    result = module.run_player_suite(
        module.PlayerSuiteOptions(
            build_directory=build, output_directory=output, failure_policy="continue"
        )
    )
    assert result.report["status"] == "cancelled" and result.exit_code == 1
    assert [entry["status"] for entry in result.report["scenarios"]] == [
        "cancelled",
        "skipped",
        "skipped",
    ]
    assert len(owned_children[1]) == 1 and owned_children[1][0].poll() == 1


@pytest.mark.parametrize("tamper", ["definition", "scene", "isolation", "duplicate", "schema"])
def test_suite_admission_refuses_drift_before_any_process(suite_bundle, owned_children, tamper):
    build, output, manifest = suite_bundle
    if tamper == "definition":
        manifest["children"][1]["definition"]["name"] = "other"
    elif tamper == "scene":
        manifest["scene_paths"].pop()
    elif tamper == "isolation":
        manifest["isolation"] = "shared-process"
    elif tamper == "duplicate":
        manifest["children"][1] = manifest["children"][0]
    else:
        manifest["schema_version"] = 3.0
    (build / "scenario-bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    module = api()
    with pytest.raises((player.PlayerLaunchError, ValueError)):
        module.run_player_suite(
            module.PlayerSuiteOptions(build_directory=build, output_directory=output)
        )
    assert owned_children[1] == [] and not output.exists()


def test_cli_suite_run_reaches_real_owned_child_launcher(suite_bundle, owned_children):
    build, output, _manifest = suite_bundle
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "player-suite-run",
            str(build),
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(owned_children[1]) == 3
    assert json.loads(result.stdout)["data"]["status"] == "succeeded"


def test_ctrl_c_requests_owned_child_cleanup_and_never_launches_remaining_children(
    suite_bundle, owned_children, monkeypatch
):
    build, output, _manifest = suite_bundle
    owned_children[0]["first"] = "wait_cancel"
    real_sleep = player.time.sleep
    interrupted = False

    def interrupt_once(seconds):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr(player.time, "sleep", interrupt_once)
    module = api()
    result = module.run_player_suite(
        module.PlayerSuiteOptions(
            build_directory=build, output_directory=output, failure_policy="continue"
        )
    )
    assert result.exit_code == 130 and result.report["status"] == "cancelled"
    assert len(owned_children[1]) == 1 and owned_children[1][0].poll() == 1
    assert len(list(result.directory.glob("*/cancel"))) == 1
    assert [entry["status"] for entry in result.report["scenarios"]] == [
        "cancelled",
        "skipped",
        "skipped",
    ]


def test_manifest_drift_between_children_refuses_next_launch(
    suite_bundle, owned_children, monkeypatch
):
    build, output, manifest = suite_bundle
    module = api()
    real_run = module.run_player

    def drift_after_first(options):
        outcome = real_run(options)
        manifest["build_source_revision"] = "external-change"
        (build / "scenario-bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
        return outcome

    monkeypatch.setattr(module, "run_player", drift_after_first)
    result = module.run_player_suite(
        module.PlayerSuiteOptions(build_directory=build, output_directory=output)
    )
    assert result.exit_code == 1 and result.report["status"] == "failed"
    assert [entry["status"] for entry in result.report["scenarios"]] == [
        "succeeded",
        "failed",
        "skipped",
    ]
    assert len(owned_children[1]) == 1 and owned_children[1][0].poll() == 0
    assert "manifest changed" in result.report["scenarios"][1]["client_error"]


def test_cli_suite_build_dispatches_one_validated_build_and_rejects_shared_process(
    tmp_path, monkeypatch
):
    from cli.commands import play_scenario as commands

    calls = []

    def dispatch(command, parameters, config):
        calls.append((command, parameters, config.unity_instance))
        return {"success": True, "data": {"suite_name": "three-frozen"}}

    monkeypatch.setattr(commands, "run_command", dispatch)
    arguments = [
        "--instance",
        "Owned@fixture",
        "--format",
        "json",
        "play-scenario",
        "player-suite-build",
        "three-frozen",
        "--output-dir",
        str(tmp_path / "build"),
        "--source-revision",
        "revision-fixture",
    ]
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "manage_play_scenario",
            {
                "action": "player_suite_build",
                "name": "three-frozen",
                "output_directory": str((tmp_path / "build").resolve()),
                "build_target": "windows64",
                "scripting_backend": "mono",
                "isolation": "fresh-process",
                "source_revision": "revision-fixture",
            },
            "Owned@fixture",
        )
    ]
    refused = CliRunner().invoke(cli, [*arguments, "--isolation", "shared-process"])
    assert refused.exit_code == 2 and len(calls) == 1


def test_interrupt_during_export_preserves_already_executed_child_report(
    suite_bundle, owned_children, monkeypatch
):
    build, output, _manifest = suite_bundle
    module = api()

    def interrupt_export(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "write_player_artifacts", interrupt_export)
    result = module.run_player_suite(
        module.PlayerSuiteOptions(build_directory=build, output_directory=output)
    )
    assert result.exit_code == 130 and result.report["status"] == "cancelled"
    assert [entry["status"] for entry in result.report["scenarios"]] == [
        "succeeded",
        "skipped",
        "skipped",
    ]
    assert result.report["scenarios"][0]["report"]["status"] == "succeeded"
    assert len(owned_children[1]) == 1 and owned_children[1][0].poll() == 0


def test_wrong_native_suite_child_identity_is_infrastructure_failure(suite_bundle, owned_children):
    build, output, _manifest = suite_bundle
    owned_children[0]["first"] = "wrong-suite"
    module = api()
    result = module.run_player_suite(
        module.PlayerSuiteOptions(
            build_directory=build, output_directory=output, failure_policy="continue"
        )
    )
    assert result.exit_code == 1 and result.report["status"] == "failed"
    assert [entry["status"] for entry in result.report["scenarios"]] == [
        "failed",
        "skipped",
        "skipped",
    ]
    assert result.report["scenarios"][0]["client_error"] == "player_report_invalid"
    assert len(owned_children[1]) == 1 and owned_children[1][0].poll() == 0


@pytest.mark.parametrize(
    "mode,expected_error,native_status",
    [
        ("progress-error", "controlled progress publication failure", "succeeded"),
        ("missing-heartbeat", "player_heartbeat_missing", "succeeded"),
        ("invalid-heartbeat", "player_progress_identity_mismatch", "succeeded"),
        (
            "report-error",
            "Failure diagnostics unavailable: IOException: controlled capture failure",
            "failed",
        ),
    ],
)
def test_continue_policy_stops_after_child_evidence_failure_and_preserves_report(
    suite_bundle, owned_children, mode, expected_error, native_status
):
    build, output, _manifest = suite_bundle
    owned_children[0]["first"] = mode
    module = api()
    result = module.run_player_suite(
        module.PlayerSuiteOptions(
            build_directory=build, output_directory=output, failure_policy="continue"
        )
    )
    assert result.exit_code == 1 and result.report["status"] == "failed"
    assert [entry["status"] for entry in result.report["scenarios"]] == [
        "failed",
        "skipped",
        "skipped",
    ]
    first = result.report["scenarios"][0]
    assert first["client_error"] == result.report["client_error"] == expected_error
    child_directory = result.directory / first["report_directory"]
    actual_report = json.loads((child_directory / "run.json").read_text())
    assert first["report"] == actual_report and actual_report["status"] == native_status
    assert ElementTree.parse(child_directory / "junit.xml").getroot().attrib["errors"] == "1"
    assert ElementTree.parse(result.directory / "junit.xml").getroot().attrib["skipped"] == "2"
    assert len(owned_children[1]) == 1 and owned_children[1][0].poll() == (
        0 if native_status == "succeeded" else 1
    )


def test_payload_tamper_after_completed_child_exports_aggregate_and_skips_rest(
    suite_bundle, owned_children, monkeypatch
):
    build, output, _manifest = suite_bundle
    module = api()
    real_run = module.run_player

    def tamper_after_first(options):
        outcome = real_run(options)
        (build / "MCPScenarioPlayer.exe").write_bytes(b"owned external payload edit")
        return outcome

    monkeypatch.setattr(module, "run_player", tamper_after_first)
    result = module.run_player_suite(
        module.PlayerSuiteOptions(build_directory=build, output_directory=output)
    )
    assert result.exit_code == 1 and result.report["status"] == "failed"
    assert [entry["status"] for entry in result.report["scenarios"]] == [
        "succeeded",
        "failed",
        "skipped",
    ]
    first = result.report["scenarios"][0]
    actual_report = json.loads(
        (result.directory / first["report_directory"] / "run.json").read_text()
    )
    assert first["report"] == actual_report and actual_report["status"] == "succeeded"
    assert len(owned_children[1]) == 1 and owned_children[1][0].poll() == 0
    assert json.loads((result.directory / "suite.json").read_text())["status"] == "failed"
    assert ElementTree.parse(result.directory / "junit.xml").getroot().attrib["skipped"] == "1"


def framed_suite_hash(manifest):
    """Independent fixture producer for the version-three UTF8 length-prefixed identity contract."""
    fields = [
        manifest["suite_name"],
        manifest["failure_policy"],
        manifest["isolation"],
        str(len(manifest["children"])),
    ]
    for child in manifest["children"]:
        fields.extend((child["scenario_name"], child["definition_hash"], child["definition_json"]))
    payload = bytearray(b"MCPForUnity.PlayerSuite/3\n")
    for value in fields:
        encoded = value.encode("utf-8")
        payload.extend(str(len(encoded)).encode("ascii") + b":" + encoded)
    return sha256(payload).hexdigest()


@pytest.mark.parametrize("exponent", ["1E-06", "1e-06"])
def test_suite_identity_uses_exact_frozen_exponent_json_without_numeric_reencoding(
    suite_bundle, exponent
):
    _build, _output, manifest = suite_bundle
    child = manifest["children"][0]
    child["definition"]["steps"][0]["scene"] = "Assets/Numeric_\u00e9_\U0001f600.unity"
    child["definition"]["steps"].append(
        {"name": "numeric", "action": "wait_state", "state_id": "numeric", "state_equals": 1e-6}
    )
    child["definition_json"] = canonical(child["definition"]).replace("1e-06", exponent)
    child["definition_hash"] = sha256(child["definition_json"].encode("utf-8")).hexdigest()
    manifest["scene_paths"] = sorted(
        ["Assets/Numeric_\u00e9_\U0001f600.unity", "Assets/second.unity", "Assets/third.unity"]
    )
    manifest["suite_hash"] = framed_suite_hash(manifest)
    parsed = api().PlayerSuiteBundle.model_validate(manifest)
    assert exponent in parsed.children[0].definition_json
    assert parsed.child_manifest("first")["definition_json"] == child["definition_json"]


def test_native_exported_exponent_suite_manifest_admits_in_python():
    """Root sets the actual native fixture export path; a controlled stand-in is not native proof."""
    path = os.environ.get("MCP_PLAY14_NATIVE_SUITE_MANIFEST")
    if not path:
        pytest.skip("Root-owned native exponent manifest export is pending")
    raw = Path(path).read_text(encoding="utf-8-sig")
    parsed = api().PlayerSuiteBundle.model_validate_json(raw)
    assert "1E-06" in parsed.children[0].definition_json
    assert parsed.children[0].definition["steps"][-1]["state_equals"] == 1e-6
