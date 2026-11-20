"""Diagnostic connection and scene identity are independently required."""

import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import first_scene_read as benchmark


@pytest.mark.parametrize(
    ("arm", "comparison", "scene_path", "target_matches", "outcome", "scene_reads"),
    [
        ("cold", "matched", "Assets/Fixture.unity", True, "scene_verified", 1),
        ("warm", "unknown", "Assets/Fixture.unity", True, "scene_verified_source_unknown", 1),
        ("warm", "matched", "Assets/Wrong.unity", True, "scene_unverified", 1),
        ("warm", "matched", "Assets/Fixture.unity", False, "connection_unverified", 0),
        ("mismatched-pin", "mismatch", "Assets/Fixture.unity", True, "mismatch_diagnosed", 0),
        ("mismatched-pin", "unknown", "Assets/Fixture.unity", True, "mismatch_unverified", 0),
    ],
)
def test_scene_success_requires_native_binding_and_specific_scene(
    monkeypatch, tmp_path, arm, comparison, scene_path, target_matches, outcome, scene_reads
):
    target = "Fixture@1234567812345678"
    arguments = Namespace(
        cli="unity-mcp",
        port=61110,
        instance=target,
        project=tmp_path,
        scene="Assets/Fixture.unity",
        arm=arm,
        setup_command=None,
        timeout=5,
    )
    calls = []

    def cli(argv, timeout, *, json_reply):
        assert json_reply and timeout == arguments.timeout
        expected_index = argv.index("--expect-project") + 1
        assert argv[expected_index] == str(tmp_path.resolve())
        calls.append(argv[-1])
        if argv[-1] == "doctor":
            return {
                "exit_code": 1 if comparison == "mismatch" else 0,
                "payload": {
                    "schema_version": "unity-mcp/doctor-cli@1",
                    "requested_target": target,
                    "connection_verified": True,
                    "reason": "source_mismatch" if comparison == "mismatch" else None,
                    "next_action": "restart_client_server" if comparison == "mismatch" else None,
                    "data": {
                        "connection_verified": True,
                        "target": {
                            "instance_id": target if target_matches else "Another@1234567812345678",
                            "execution_environment": "editor",
                        },
                        "checks": {"native_read": {"status": "passed"}},
                        "server": {"source_commit": "a" * 40},
                        "package": {
                            "effective_server_commit": ("b" if comparison == "mismatch" else "a")
                            * 40
                        },
                        "source_comparison": comparison,
                    },
                },
            }
        return {
            "exit_code": 0,
            "payload": {"success": True, "data": {"path": scene_path, "isLoaded": True}},
        }

    monkeypatch.setattr(benchmark, "invoke", cli)
    receipt = benchmark.measure(arguments)
    assert receipt["outcome"] == outcome
    assert receipt["seconds_to_outcome"] >= 0
    assert calls.count("active") == scene_reads
    assert receipt["setup_included"] is False
