"""Import original official-provider artifacts with a trusted session recorder's bindings."""

import re
from hashlib import sha256

from .play_scenario_analysis_io import read_bytes, read_json
from .play_scenario_report_json import REPORT_LIMIT, parse_report
from .verification_nunit_xml import read_test_run


class MissingField(ValueError):
    """An admitted native artifact did not contain the declared field."""


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object for {label}")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {label}")
    return value


def _pointer(document, pointer):
    """Resolve an explicit RFC 6901 pointer inside the selected in-memory capture."""
    if (
        not isinstance(pointer, str)
        or not pointer.startswith("/")
        or re.search("~(?![01])", pointer)
    ):
        raise ValueError("Expected a non-root JSON pointer")
    value = document
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif (
            isinstance(value, list)
            and re.fullmatch("0|[1-9][0-9]{0,8}", key)
            and int(key) < len(value)
        ):
            value = value[int(key)]
        else:
            raise MissingField("Native Player capture is missing a declared field")
    return value


def _binding(session, kind):
    entry = _object(session.get(kind), kind)
    _text(entry.get("execution_id"), f"{kind} execution ID")
    _text(entry.get("producer_id"), f"{kind} producer")
    if not re.fullmatch("[0-9a-f]{64}", _text(entry.get("sha256"), f"{kind} raw digest")):
        raise ValueError(f"Invalid raw {kind} SHA-256")
    return entry


def _nunit(session, path):
    if path is None:
        return {"state": "unavailable", "reason": "nunit_xml_not_supplied"}
    entry = _binding(session, "nunit")
    run = read_test_run(path)
    if run.digest != entry["sha256"]:
        raise ValueError("NUnit XML differs from the official session recorder's artifact")
    return {
        "state": "observed",
        "producer_id": entry["producer_id"],
        "execution_id": entry["execution_id"],
        "sha256": run.digest,
        "result": run.result,
        "seconds": run.seconds,
        "cases": [
            {"fullname": case.name, "result": case.result, "seconds": case.duration}
            for case in run.cases.values()
        ],
    }


def _player(session, producer, path):
    if path is None:
        return {"state": "unsupported", "reason": "independent_player_observation_not_supplied"}
    entry = _binding(session, "player")
    pid = entry.get("process_id")
    if type(pid) is not int or pid <= 0 or entry["producer_id"] == producer["operator_id"]:
        raise ValueError("An independently observed standalone Player process is required")
    raw = read_bytes(path, REPORT_LIMIT)
    digest = sha256(raw).hexdigest()
    if digest != entry["sha256"]:
        raise ValueError("Player bytes differ from the session recorder's capture")
    native = parse_report(raw)
    receipt = {
        "sha256": digest,
        "producer_id": entry["producer_id"],
        "execution_id": entry["execution_id"],
        "process_id": pid,
    }
    mapping = _object(entry.get("observations", {}), "game-state observation pointers")
    if not mapping:
        return {**receipt, "state": "unsupported", "reason": "no_independent_game_state"}
    expected = {
        "session_id": producer["session_id"],
        "project_guid": producer["project_guid"],
        "source_revision": producer["source_revision"],
        "producer_id": entry["producer_id"],
        "execution_id": entry["execution_id"],
        "process_id": pid,
        "environment": "player",
    }
    bindings = _object(entry.get("bindings"), "Player binding pointers")
    if set(bindings) != set(expected):
        raise ValueError("Incomplete Player identity bindings")
    for key, wanted in expected.items():
        value = _pointer(native, bindings[key])
        if type(value) is not type(wanted) or value != wanted:
            raise ValueError(f"Native Player {key} differs from the recorded process")
    observations = {}
    missing = []
    for label, pointer in mapping.items():
        _text(label, "game-state observation name")
        _text(pointer, "game-state JSON pointer")
        if any(
            pointer == bound or pointer.startswith(bound + "/") or bound.startswith(pointer + "/")
            for bound in bindings.values()
        ):
            raise ValueError("Player identity fields cannot serve as game-state observations")
        try:
            observations[label] = _pointer(native, pointer)
        except MissingField:
            missing.append(label)
    if missing:
        return {
            **receipt,
            "state": "unsupported",
            "reason": "declared_game_state_not_observed",
            "unobserved": missing,
            "observations": observations,
        }
    return {
        "state": "observed",
        **receipt,
        "observations": observations,
    }


def import_session(session_path, nunit_path=None, player_path=None):
    session = read_json(session_path)
    if session.get("schema") != "official_unity_session@1":
        raise ValueError("Expected official_unity_session@1")
    fields = (
        "provider_command",
        "unity_cli",
        "pipeline_package",
        "connected_pipeline",
        "session_id",
        "project_guid",
        "source_revision",
        "unity_version",
        "collector_id",
        "operator_id",
    )
    recorded = _object(session.get("producer"), "official provider")
    producer = {key: _text(recorded.get(key), key) for key in fields}
    if (
        producer["provider_command"] != "unity mcp"
        or producer["pipeline_package"] != producer["connected_pipeline"]
        or producer["collector_id"] == producer["operator_id"]
        or not re.fullmatch("[0-9a-f]{32}", producer["project_guid"])
        or not re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", producer["source_revision"])
    ):
        raise ValueError("Unbound official Unity provider, project or independent recorder")
    nunit = _nunit(session, nunit_path)
    player = _player(session, producer, player_path)
    return {
        "schema": "official_unity_evidence@1",
        "state": "observed" if nunit["state"] == player["state"] == "observed" else "incomplete",
        "producer": producer,
        "nunit": nunit,
        "player": player,
    }
