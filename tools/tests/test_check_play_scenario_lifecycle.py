"""Identity evidence boundary tests using explicitly synthetic local fixtures."""

import hashlib
import json

import pytest

from check_play_scenario_evidence import receipt_filename
from check_play_scenario_lifecycle import validate_identities

METHOD = "UnityMcpLifecycleSample.Tests.LifecycleScenarioTests.SyntheticBody"
JOB = "a" * 32
OWNER = "b" * 32


@pytest.fixture
def evidence(tmp_path):
    folder = tmp_path / "reports"
    identities = tmp_path / "identities"
    folder.mkdir()
    identities.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tests": [{"method": METHOD}]}))
    report = {
        "job_id": JOB,
        "scenario": {"name": "sample-normal"},
        "iteration_results": [{"iteration": 1}],
    }
    (folder / (JOB + ".json")).write_text(json.dumps(report))
    sidecar = identities / "SyntheticBody.json"
    sidecar.write_text(
        json.dumps(
            [
                {
                    "job_id": JOB,
                    "iteration": 1,
                    "owner_id": OWNER,
                    "native_clone_id": -42,
                    "resource_ids": [1, 2, 3],
                    "native_clone_retained": False,
                    "stream_open": False,
                    "event_delivered_after_return": False,
                    "source_unchanged": True,
                }
            ]
        )
    )
    receipt = {
        "reports": [{"id": JOB, "file": JOB + ".json"}],
        "identity_evidence_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
    }
    (folder / receipt_filename(METHOD)).write_text(json.dumps(receipt))
    return folder, identities, manifest


def resign(evidence):
    folder, identities, _ = evidence
    receipt_path = folder / receipt_filename(METHOD)
    receipt = json.loads(receipt_path.read_text())
    receipt["identity_evidence_sha256"] = hashlib.sha256(
        (identities / "SyntheticBody.json").read_bytes()
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt))


def test_valid_identity_is_accepted(evidence):
    validate_identities(*evidence)


@pytest.mark.parametrize("damage", ["missing", "rewritten", "missing_hash"])
def test_missing_or_rewritten_identity_is_rejected(evidence, damage):
    folder, identities, _ = evidence
    path = identities / "SyntheticBody.json"
    if damage == "missing":
        path.unlink()
    elif damage == "rewritten":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        receipt_path = folder / receipt_filename(METHOD)
        receipt = json.loads(receipt_path.read_text())
        del receipt["identity_evidence_sha256"]
        receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        validate_identities(*evidence)


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_job",
        "wrong_iteration",
        "duplicate",
        "empty",
        "wrong_shape",
        "duplicate_resource",
        "unreleased_stream",
        "source_changed",
    ],
)
def test_identity_must_match_observed_run_and_lifetime(evidence, damage):
    _, identities, _ = evidence
    path = identities / "SyntheticBody.json"
    items = json.loads(path.read_text())
    if damage == "wrong_job":
        items[0]["job_id"] = "c" * 32
    elif damage == "wrong_iteration":
        items[0]["iteration"] = 2
    elif damage == "duplicate":
        items.append(items[0])
    elif damage == "empty":
        items.clear()
    elif damage == "wrong_shape":
        items = {}
    elif damage == "duplicate_resource":
        items[0]["resource_ids"] = [1, 1, 3]
    elif damage == "unreleased_stream":
        items[0]["stream_open"] = True
    else:
        items[0]["source_unchanged"] = False
    path.write_text(json.dumps(items))
    resign(evidence)
    with pytest.raises(ValueError):
        validate_identities(*evidence)


def test_retention_requires_matching_owner_ids_and_explicit_release(evidence):
    folder, identities, _ = evidence
    report_path = folder / (JOB + ".json")
    report = json.loads(report_path.read_text())
    report["scenario"]["name"] = "sample-retained-teardown"
    report["resource_checks"] = [
        {
            "retained_resources": [
                {"id": value, "owner": "lifecycle-session:" + OWNER} for value in (1, 2, 3)
            ]
        }
    ]
    report_path.write_text(json.dumps(report))
    path = identities / "SyntheticBody.json"
    items = json.loads(path.read_text())
    for key in ("native_clone_retained", "stream_open", "event_delivered_after_return"):
        items[0][key] = True
    items[0]["explicit_release_verified"] = True
    path.write_text(json.dumps(items))
    resign(evidence)
    validate_identities(*evidence)
    items[0]["explicit_release_verified"] = False
    path.write_text(json.dumps(items))
    resign(evidence)
    with pytest.raises(ValueError):
        validate_identities(*evidence)
