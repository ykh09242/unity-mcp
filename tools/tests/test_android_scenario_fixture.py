"""Check Android pilot evidence admission without claiming a device observation."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_android_scenario_fixture import (  # noqa: E402
    AdbTarget,
    FixtureBuild,
    PilotError,
    read_fresh_result,
    select_device,
)

BUILD = FixtureBuild(
    app_id="org.example.readinessprobe",
    scenario="ready-check",
    build_id="b" * 32,
    source_revision="c" * 64,
    unity_version="6000.0.69f1",
    abi="arm64-v8a",
    apk_sha256="a" * 64,
    apk=Path("probe.apk"),
)
LAUNCH = "a" * 32
PID = "4921"


def record(launch: str, *, build: FixtureBuild = BUILD, pid: str = PID, ready: bool = True) -> str:
    data = {
        "schema_version": 1,
        "app_id": build.app_id,
        "scenario": build.scenario,
        "build_id": build.build_id,
        "source_revision": build.source_revision,
        "unity_version": build.unity_version,
        "launch_id": launch,
        "ready": ready,
    }
    return f"I/Unity   ({pid:>5}): MCP_ANDROID_FIXTURE_RESULT {json.dumps(data)}"


def test_result_matches_process_and_launch_among_stale_records():
    # Given stale results and a single exact witness.
    fresh = record(LAUNCH)
    lines = "\n".join((record("d" * 32), record(LAUNCH, pid="4922"), fresh))
    # When the device log is interpreted.
    observed = read_fresh_result(lines, BUILD, LAUNCH, PID)
    # Then only the fresh witness establishes readiness.
    assert observed is not None and observed.ready and observed.raw_evidence == fresh


def test_stale_only_cannot_pass_even_when_pid_and_build_match():
    # Given a ready result from the same build and a reused PID.
    log = record("d" * 32)
    # When a later launch selects the log.
    observed = read_fresh_result(log, BUILD, LAUNCH, PID)
    # Then its ready state remains unverified.
    assert observed is None


@pytest.mark.parametrize(
    "build",
    (
        replace(BUILD, source_revision="f" * 64),
        replace(BUILD, build_id="e" * 32),
        replace(BUILD, app_id="org.example.unrelated"),
        replace(BUILD, scenario="other-check"),
        replace(BUILD, unity_version="6000.0.57f1"),
    ),
)
def test_current_nonce_with_incompatible_build_is_rejected(build: FixtureBuild):
    # Given a current process and nonce but an incompatible source, build, or app identity.
    log = record(LAUNCH, build=build)
    # When the result is checked against the chosen build.
    with pytest.raises(PilotError, match="different build"):
        # Then no readiness verdict is available.
        read_fresh_result(log, BUILD, LAUNCH, PID)


def test_failed_state_is_a_valid_observation():
    # Given a matched current result from a fixture that stayed unready.
    log = record(LAUNCH, ready=False)
    # When the result is interpreted.
    observed = read_fresh_result(log, BUILD, LAUNCH, PID)
    # Then it propagates an explicit failure observation.
    assert observed is not None and observed.ready is False


def test_duplicate_current_result_is_ambiguous():
    # Given two records for the same launch.
    log = "\n".join((record(LAUNCH), record(LAUNCH, ready=False)))
    # When the result is interpreted.
    with pytest.raises(PilotError, match="duplicate result"):
        # Then the conflicting observations cannot pass.
        read_fresh_result(log, BUILD, LAUNCH, PID)


def test_selects_only_explicit_device_transport():
    # Given two online devices with distinct transports.
    devices = "List of devices attached\nA1 device transport_id:4\nB1 device transport_id:9\n"
    # When an explicit serial is selected.
    selected = select_device(devices, "B1")
    # Then only that transport is used.
    assert selected == AdbTarget("B1", "9")


@pytest.mark.parametrize(
    "device_rows",
    (
        "B1 device transport_id:1",
        "A1 unauthorized transport_id:2",
        "A1 offline transport_id:2",
        "A1 device transport_id:3\nA1 device transport_id:4",
    ),
)
def test_unknown_offline_or_ambiguous_device_is_rejected(device_rows: str):
    # Given an ADB inventory without a unique reachable selected device.
    devices = "List of devices attached\n" + device_rows
    # When the runner asks for that serial.
    with pytest.raises(PilotError, match="not uniquely present and online"):
        # Then no device routing identity is returned.
        select_device(devices, "A1")


def test_apk_must_match_receipt_bytes(tmp_path: Path):
    # Given a receipt with a previous build's digest and a different local APK.
    apk = tmp_path / "probe.apk"
    with ZipFile(apk, "w") as bundle:
        bundle.writestr("AndroidManifest.xml", b"example fixture manifest")
        bundle.writestr("lib/arm64-v8a/libil2cpp.so", b"example fixture payload")
    receipt = tmp_path / "build.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_id": BUILD.app_id,
                "scenario": BUILD.scenario,
                "build_id": BUILD.build_id,
                "source_revision": BUILD.source_revision,
                "unity_version": BUILD.unity_version,
                "abi": BUILD.abi,
                "apk_sha256": sha256(b"prior build bytes").hexdigest(),
                "apk_file": apk.name,
                "backend": "IL2CPP",
            }
        ),
        encoding="utf-8",
    )
    # When the runner opens the receipt.
    with pytest.raises(PilotError, match="APK digest does not match"):
        # Then it rejects the mismatched bytes before contacting the device.
        FixtureBuild.read(receipt)
