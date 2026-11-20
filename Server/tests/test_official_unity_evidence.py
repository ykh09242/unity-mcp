"""Synthetic admission controls for original official-provider NUnit and Player bytes."""

import hashlib
import json

from click.testing import CliRunner
import pytest

from unity_mcp_evidence.commands import offline_main
from unity_mcp_evidence.official_evidence import import_session


@pytest.fixture
def archive(tmp_path):
    producer = {
        "provider_command": "unity mcp",
        "unity_cli": "pinned-cli",
        "pipeline_package": "pipeline",
        "connected_pipeline": "pipeline",
        "unity_version": "6000.0.69f1",
        "project_guid": "a" * 32,
        "source_revision": "b" * 40,
        "session_id": "session",
        "collector_id": "broker",
        "operator_id": "actor",
    }
    nunit = tmp_path / "tests.xml"
    nunit.write_bytes(
        b'<test-run result="Failed" total="1" duration="2"><test-suite type="Assembly" name="GameTests"><test-case fullname="Game.Open(1)" result="Failed" duration="1"/></test-suite></test-run>'
    )
    binding = {key: producer[key] for key in ("project_guid", "source_revision", "session_id")}
    binding.update(environment="player", producer_id="probe", execution_id="job", process_id=42)
    player = tmp_path / "player.json"
    player.write_text(json.dumps({"native": binding, "state": {"open": False}}), encoding="utf-8")
    metadata = {
        "schema": "official_unity_session@1",
        "producer": producer,
        "nunit": {
            "sha256": hashlib.sha256(nunit.read_bytes()).hexdigest(),
            "execution_id": "test-job",
            "producer_id": "test-runner",
        },
        "player": {
            "sha256": hashlib.sha256(player.read_bytes()).hexdigest(),
            "bindings": {key: "/native/" + key for key in binding},
            "observations": {"door_open": "/state/open"},
            **{key: binding[key] for key in ("execution_id", "process_id", "producer_id")},
        },
    }
    session = tmp_path / "session.json"
    session.write_text(json.dumps(metadata), encoding="utf-8")
    return session, nunit, player


def test_official_import_preserves_native_results_and_partial_coverage(archive):
    session, nunit, player = archive
    result = import_session(session, nunit, player)
    assert result["producer"]["provider_command"] == "unity mcp"
    assert result["state"] == "observed"
    assert result["nunit"]["cases"] == [
        {"fullname": "Game.Open(1)", "result": "Failed", "seconds": 1}
    ]
    assert result["player"]["observations"] == {"door_open": False}
    cli = CliRunner().invoke(
        offline_main, ["--format", "json", "import-official", str(session), "--nunit", str(nunit)]
    )
    assert cli.exit_code == 2
    partial = json.loads(cli.output)
    assert partial["nunit"] == result["nunit"]
    assert partial["player"]["state"] == "unsupported"


@pytest.mark.parametrize(
    "field,value", [("process_id", 43), ("environment", "editor"), ("session_id", "another")]
)
def test_reject_another_process_even_with_recaptured_digest(archive, field, value):
    session, nunit, player = archive
    metadata = json.loads(session.read_bytes())
    native = json.loads(player.read_bytes())
    native["native"][field] = value
    player.write_text(json.dumps(native), encoding="utf-8")
    metadata["player"]["sha256"] = hashlib.sha256(player.read_bytes()).hexdigest()
    session.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="recorded process"):
        import_session(session, nunit, player)


def test_reject_unbound_xml_and_actor_observations(archive):
    session, nunit, player = archive
    nunit.write_bytes(nunit.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="NUnit XML differs"):
        import_session(session, nunit, player)
    metadata = json.loads(session.read_bytes())
    metadata["player"]["producer_id"] = metadata["producer"]["operator_id"]
    session.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="independently"):
        import_session(session, None, player)


def test_absent_game_state_never_becomes_a_pass(archive):
    session, nunit, player = archive
    metadata = json.loads(session.read_bytes())
    metadata["player"]["observations"]["score"] = "/state/score"
    session.write_text(json.dumps(metadata), encoding="utf-8")
    result = import_session(session, nunit, player)
    assert result["state"] == "incomplete"
    assert result["nunit"]["state"] == "observed"
    assert result["player"]["state"] == "unsupported"
    assert result["player"]["observations"] == {"door_open": False}
    assert result["player"]["unobserved"] == ["score"]


@pytest.mark.parametrize("pointer", ["/native", "/native/session_id", "/native/session_id/child"])
def test_player_identity_subtrees_cannot_supply_game_state(archive, pointer):
    session, nunit, player = archive
    metadata = json.loads(session.read_bytes())
    metadata["player"]["observations"] = {"claimed_state": pointer}
    session.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="identity fields"):
        import_session(session, nunit, player)


@pytest.mark.parametrize("field,value", [("selectedTarget", None), ("inventory", [])])
def test_explicit_empty_or_null_game_state_is_observed(archive, field, value):
    session, nunit, player = archive
    metadata = json.loads(session.read_bytes())
    native = json.loads(player.read_bytes())
    metadata["player"]["observations"] = {field: "/state/" + field}
    native["state"][field] = value
    player.write_text(json.dumps(native), encoding="utf-8")
    metadata["player"]["sha256"] = hashlib.sha256(player.read_bytes()).hexdigest()
    session.write_text(json.dumps(metadata), encoding="utf-8")
    result = import_session(session, nunit, player)
    assert result["state"] == "observed"
    assert result["player"]["observations"] == {field: value}
