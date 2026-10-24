"""Native receipts require observed lifecycle results and real process identities."""

from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from tools import native_assurance as assurance


def receipt():
    identities = [
        {"peer": peer, "pid": (index + 1) * 101, "started_ticks": (index + 1) * 1001}
        for index, peer in enumerate(("a", "b", "a", "a", "b"))
    ]
    actors = (0, 1, 0, 1, 2, 1, 1, 1, 1, 3, 3, 3, 3, 4, 3, 4, 3, 4, 3, 3, 3, 3, 3)
    owners = (
        (0,),
        (0, 1),
        (1,),
        (),
        (2,),
        (1, 2),
        (1,),
        (),
        (1,),
        (),
        (3,),
        (),
        (3,),
        (3, 4),
        (3, 4),
        (3, 4),
        (4,),
        (),
        (),
        (3,),
        (),
        (3,),
        (),
    )
    steps = []
    for (name, idle, mode, _), actor, live in zip(assurance.EXPECTATIONS, actors, owners):
        steps.append(
            {
                "case": name,
                **identities[actor],
                "preferences": {"ApplicationIdleTime": idle, "InteractionMode": mode},
                "owners": [
                    {"Pid": row["pid"], "Started": row["started_ticks"]}
                    for row in (identities[index] for index in live)
                ],
                "error": None,
            }
        )
    return {
        "schema": "unity-mcp-native-preference-lease-v1",
        "native": True,
        "platform": "win32",
        "editor_version": "6000.0.69f1",
        "editor_sha256": "a" * 64,
        "inputs_verified_before_after": True,
        "launches": identities,
        "terminations": [
            {
                **row,
                "identity_verified": True,
                "exited": True,
                "mode": "forced" if index in (1, 2) else "graceful",
            }
            for index, row in enumerate(identities)
        ],
        "steps": steps,
        "cleanup": {"owned_survivors": 0, "protected_preserved": True},
    }


def check(tmp_path, data):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return assurance.check_receipt(path)


def test_complete_native_observations_pass(tmp_path):
    assert check(tmp_path, receipt())["steps"] == len(assurance.EXPECTATIONS)


@pytest.mark.parametrize(
    "field,value",
    [("native", False), ("platform", "linux"), ("inputs_verified_before_after", False)],
)
def test_compile_simulated_platform_or_unverified_inputs_never_pass(tmp_path, field, value):
    data = receipt()
    data[field] = value
    with pytest.raises(ValueError):
        check(tmp_path, data)


def test_missing_required_observation_does_not_pass(tmp_path):
    data = receipt()
    data["steps"].pop()
    with pytest.raises(ValueError, match="inventory"):
        check(tmp_path, data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("started_ticks", 9999),
        ("preferences", {"ApplicationIdleTime": 8, "InteractionMode": 2}),
        ("error", "lock remained"),
    ],
)
def test_wrong_process_identity_preferences_or_error_fail(tmp_path, key, value):
    data = receipt()
    data["steps"][0][key] = value
    with pytest.raises(ValueError):
        check(tmp_path, data)


def test_unverified_termination_and_surviving_owned_process_fail(tmp_path):
    for variant in ("termination", "survivor", "protected"):
        data = deepcopy(receipt())
        if variant == "termination":
            data["terminations"][0]["identity_verified"] = False
        elif variant == "survivor":
            data["cleanup"]["owned_survivors"] = 1
        else:
            data["cleanup"]["protected_preserved"] = False
        with pytest.raises(ValueError):
            check(tmp_path, data)


def test_survivor_must_own_the_remaining_lease(tmp_path):
    data = receipt()
    # A valid but already released owner's identity cannot stand in for the survivor.
    data["steps"][2]["owners"] = data["steps"][0]["owners"]
    with pytest.raises(ValueError, match="owner"):
        check(tmp_path, data)


def test_every_launched_editor_requires_observed_owned_termination(tmp_path):
    data = receipt()
    data["terminations"].pop()
    with pytest.raises(ValueError, match="termination"):
        check(tmp_path, data)


@pytest.mark.parametrize("name", ["NativePreferenceLeasePeer", "NativeVersionAssuranceTests"])
def test_native_fixture_metadata_has_a_valid_unity_guid(name):
    directory = (
        Path(__file__).parents[2] / "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services"
    )
    assert re.search(
        r"^guid: [0-9a-f]{32}$", (directory / f"{name}.cs.meta").read_text(), re.MULTILINE
    )


def client_receipt():
    data = receipt()
    data.update(
        schema="unity-mcp-native-client-window-v1",
        mode="client-only",
        lease_observations_requested=False,
        client_phase_succeeded=True,
        steps=[],
        failure=None,
    )
    data["launches"] = data["launches"][:2]
    data["terminations"] = [
        {**row, "identity_verified": True, "exited": True, "mode": "graceful"}
        for row in data["launches"]
    ]
    return data


def check_client(tmp_path, data):
    path = tmp_path / "client-receipt.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return assurance.check_client_receipt(path)


def test_client_only_replay_has_distinct_zero_lease_scope(tmp_path):
    assert check_client(tmp_path, client_receipt())["editor_processes"] == 2
    with pytest.raises(ValueError):
        check(tmp_path, client_receipt())
    with pytest.raises(ValueError):
        check_client(tmp_path, receipt())


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_phase_succeeded", False),
        ("inputs_verified_before_after", False),
        ("lease_observations_requested", True),
        ("failure", "atomic command replacement failed"),
    ],
)
def test_client_only_replay_rejects_incomplete_or_mislabeled_scope(tmp_path, field, value):
    data = client_receipt()
    data[field] = value
    with pytest.raises(ValueError):
        check_client(tmp_path, data)


def test_client_only_replay_requires_both_observed_exits(tmp_path):
    data = client_receipt()
    data["terminations"].pop()
    with pytest.raises(ValueError):
        check_client(tmp_path, data)
