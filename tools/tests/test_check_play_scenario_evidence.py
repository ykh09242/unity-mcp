"""Required native evidence must prove body completion and fresh persisted outcomes."""

import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_play_scenario_evidence as evidence

METHOD = "MCPForUnityTests.PlayScenarios.Integration.PlayScenarioNativeFlowTests.Example"
JOB = "a" * 32
SUITE = "b" * 32


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def bundle(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    manifest = tmp_path / "manifest.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "tests": [{"method": METHOD, "reports": [{"kind": "run", "status": "timed_out"}]}],
        },
    )
    write_json(folder / ".session.json", {"session_id": "c" * 32, "started_unix_ms": 1000})
    report = {
        "job_id": JOB,
        "status": "timed_out",
        "started_unix_ms": 1100,
        "finished_unix_ms": 1200,
        "report_error": None,
        "runner_resources_released": True,
    }
    write_json(folder / (JOB + ".json"), report)
    receipt = {
        "schema_version": 1,
        "method": METHOD,
        "session_id": "c" * 32,
        "completed_unix_ms": 1300,
        "reports": [
            {
                "file": JOB + ".json",
                "id": JOB,
                "sha256": hashlib.sha256((folder / (JOB + ".json")).read_bytes()).hexdigest(),
            }
        ],
    }
    write_json(
        folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"), receipt
    )
    xml = tmp_path / "results.xml"
    xml.write_text(
        f'<test-run result="Passed" total="1" passed="1" failed="0" inconclusive="0" skipped="0"><test-case fullname="{METHOD}" result="Passed" /></test-run>',
        encoding="utf-8",
    )
    return folder, manifest, xml, report, receipt


def check(bundle):
    folder, manifest, xml, _, _ = bundle
    return evidence.check_evidence(folder, manifest, xml, "success")


def update_report(bundle):
    folder, _, _, report, receipt = bundle
    path = folder / (JOB + ".json")
    write_json(path, report)
    receipt["reports"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(
        folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"), receipt
    )


def test_expected_negative_outcome_passes(bundle):
    assert check(bundle) == 0


@pytest.mark.parametrize(
    "failure", ["missing_xml", "skipped", "zero", "unknown", "truncated_counts"]
)
def test_xml_must_record_real_passing_required_body(bundle, failure):
    xml = bundle[2]
    match failure:
        case "missing_xml":
            xml.unlink()
        case "skipped":
            xml.write_text(xml.read_text().replace('result="Passed" />', 'result="Skipped" />'))
        case "zero":
            xml.write_text(
                '<test-run result="Passed" total="0" passed="0" failed="0" inconclusive="0" skipped="0" />'
            )
        case "unknown":
            xml.write_text(xml.read_text().replace('result="Passed" />', 'result="Mystery" />'))
        case "truncated_counts":
            xml.write_text(
                xml.read_text()
                .replace('total="1"', 'total="2"')
                .replace('skipped="0"', 'skipped="1"')
            )
    assert check(bundle) == 1


@pytest.mark.parametrize("name", ["receipt", "report"])
def test_missing_required_evidence_fails(bundle, name):
    folder = bundle[0]
    (
        folder
        / (
            hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"
            if name == "receipt"
            else JOB + ".json"
        )
    ).unlink()
    assert check(bundle) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "d" * 32),
        ("status", "succeeded"),
        ("status", "running"),
        ("finished_unix_ms", None),
        ("started_unix_ms", 999),
        ("runner_resources_released", False),
        ("report_error", "write failed"),
    ],
)
def test_report_contract_mismatch_fails(bundle, field, value):
    bundle[3][field] = value
    update_report(bundle)
    assert check(bundle) == 1


@pytest.mark.parametrize("mutation", ["path", "session", "hash", "duplicate", "size", "symlink"])
def test_stale_forged_or_unbounded_receipts_fail(bundle, mutation, tmp_path):
    folder, _, _, _, receipt = bundle
    match mutation:
        case "path":
            receipt["reports"][0]["file"] = "../outside.json"
        case "session":
            receipt["session_id"] = "d" * 32
        case "hash":
            receipt["reports"][0]["sha256"] = "0" * 64
        case "duplicate":
            receipt["reports"].append(receipt["reports"][0])
        case "size":
            (folder / (JOB + ".json")).write_bytes(b" " * (evidence.MAX_FILE_BYTES + 1))
        case "symlink":
            original = folder / (JOB + ".json")
            outside = tmp_path / "outside.json"
            outside.write_bytes(original.read_bytes())
            original.unlink()
            try:
                original.symlink_to(outside)
            except OSError:
                pytest.skip("Symlink creation is unavailable")
    write_json(
        folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"), receipt
    )
    assert check(bundle) == 1


def suite_bundle(bundle):
    folder, manifest, _, report, receipt = bundle
    report["status"] = "timed_out"
    report["reproduction"] = {"definition_hash": "e" * 64}
    update_report(bundle)
    suite = {
        "suite_id": SUITE,
        "status": "failed",
        "started_unix_ms": 1100,
        "finished_unix_ms": 1250,
        "report_error": None,
        "scenarios": [
            {"job_id": JOB, "status": "timed_out", "definition_hash": "e" * 64, "report": report},
            {"job_id": "f" * 32, "status": "skipped", "report": None},
        ],
    }
    path = folder / (SUITE + ".suite.json")
    write_json(path, suite)
    receipt["reports"] = [
        {"id": SUITE, "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    ]
    write_json(
        folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"), receipt
    )
    write_json(
        manifest,
        {
            "schema_version": 1,
            "tests": [
                {
                    "method": METHOD,
                    "reports": [
                        {"kind": "suite", "status": "failed", "children": ["timed_out", "skipped"]}
                    ],
                }
            ],
        },
    )
    return suite


def test_suite_negative_outcome_and_skipped_queue_pass(bundle):
    suite_bundle(bundle)
    assert check(bundle) == 0


@pytest.mark.parametrize("failure", ["missing_child", "hash", "status"])
def test_suite_children_must_match_real_exports(bundle, failure):
    suite = suite_bundle(bundle)
    folder, _, _, _, receipt = bundle
    if failure == "missing_child":
        (folder / (JOB + ".json")).unlink()
    else:
        suite["scenarios"][0]["definition_hash" if failure == "hash" else "status"] = "wrong"
        path = folder / (SUITE + ".suite.json")
        write_json(path, suite)
        receipt["reports"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(
            folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"), receipt
        )
    assert check(bundle) == 1


def test_initializer_replaces_only_owned_cached_evidence(tmp_path):
    folder = tmp_path / "PlayScenarioIntegrationEvidence"
    folder.mkdir()
    write_json(folder / (JOB + ".json"), {"stale": True})
    write_json(
        folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"),
        {"stale": True},
    )
    write_json(folder / ".session.json", {"session_id": "stale"})
    other = folder / "preserve.txt"
    other.write_text("external evidence", encoding="utf-8")
    session_id = evidence.initialize(folder)
    assert other.read_text(encoding="utf-8") == "external evidence"
    assert not (folder / (JOB + ".json")).exists()
    assert not (
        folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json")
    ).exists()
    assert json.loads((folder / ".session.json").read_text())["session_id"] == session_id
    assert len(session_id) == 32


def test_initializer_rejects_arbitrary_directory(tmp_path):
    with pytest.raises(ValueError):
        evidence.initialize(tmp_path / "unowned")


@pytest.mark.parametrize(
    "malformed",
    [
        "json",
        "duplicate_keys",
        "schema",
        "empty_manifest",
        "duplicate_methods",
        "oversized_manifest",
    ],
)
def test_malformed_or_ambiguous_manifest_fails_closed(bundle, malformed):
    manifest = bundle[1]
    match malformed:
        case "json":
            manifest.write_text("{truncated", encoding="utf-8")
        case "duplicate_keys":
            manifest.write_text(
                '{"schema_version":1,"schema_version":2,"tests":[]}', encoding="utf-8"
            )
        case "schema":
            value = json.loads(manifest.read_text())
            value["schema_version"] = 2
            write_json(manifest, value)
        case "empty_manifest":
            write_json(manifest, {"schema_version": 1, "tests": []})
        case "duplicate_methods":
            value = json.loads(manifest.read_text())
            value["tests"].append(value["tests"][0])
            write_json(manifest, value)
        case "oversized_manifest":
            manifest.write_bytes(b" " * (evidence.MAX_FILE_BYTES + 1))
    assert check(bundle) == 1


def test_cli_rejects_an_entire_stale_bundle_with_a_different_execution_nonce(bundle, monkeypatch):
    folder, manifest, xml, _, _ = bundle
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "checker",
            str(folder),
            "--manifest",
            str(manifest),
            "--results",
            str(xml),
            "--runner-outcome",
            "success",
            "--session-id",
            "d" * 32,
        ],
    )
    with pytest.raises(SystemExit) as result:
        evidence.main()
    assert result.value.code == 2


def test_suite_timeout_allows_honest_cancelled_child_at_shared_deadline(bundle):
    suite = suite_bundle(bundle)
    folder, manifest, _, report, receipt = bundle
    report["status"] = "cancelled"
    update_report(bundle)
    suite["status"] = "timed_out"
    suite["scenarios"][0]["status"] = "cancelled"
    suite["scenarios"][0]["report"] = report
    path = folder / (SUITE + ".suite.json")
    write_json(path, suite)
    receipt["reports"] = [
        {"id": SUITE, "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    ]
    write_json(
        folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json"), receipt
    )
    write_json(
        manifest,
        {
            "schema_version": 1,
            "tests": [
                {
                    "method": METHOD,
                    "reports": [
                        {
                            "kind": "suite",
                            "status": "timed_out",
                            "children": [["cancelled", "timed_out"], "skipped"],
                        }
                    ],
                }
            ],
        },
    )
    assert check(bundle) == 0


def test_long_method_names_use_bounded_receipt_paths_and_keep_exact_identity(bundle):
    folder, manifest, xml, _, receipt = bundle
    method = (
        METHOD.removesuffix("Example")
        + "SuiteCancellationAndTotalTimeoutWaitForChildReportAndSkipRemainingQueue"
    )
    filename = hashlib.sha256(method.encode("utf-8")).hexdigest() + ".receipt.json"
    prefix = "E:/Projects/DemonCastleWorkspace/unity-mcp/.unity-ci/CS-20261010-scenario-execution/project/Library/MCPForUnity/PlayScenarioIntegrationEvidence/"
    assert len(prefix + method + ".receipt.json") > 260
    assert len(prefix + evidence.receipt_filename(method)) < 260
    assert evidence.receipt_filename(method) == filename
    old = folder / (hashlib.sha256(METHOD.encode("utf-8")).hexdigest() + ".receipt.json")
    if old.exists():
        old.unlink()
    receipt["method"] = method
    write_json(folder / filename, receipt)
    value = json.loads(manifest.read_text())
    value["tests"][0]["method"] = method
    write_json(manifest, value)
    xml.write_text(xml.read_text().replace(METHOD, method), encoding="utf-8")
    assert check(bundle) == 0
    receipt["method"] = METHOD
    write_json(folder / filename, receipt)
    assert check(bundle) == 1


@pytest.mark.parametrize("clock_advance", [1, 50])
def test_suite_final_timestamp_cannot_precede_synchronously_cancelled_child(bundle, clock_advance):
    suite = suite_bundle(bundle)
    folder, _, _, _, receipt = bundle
    suite["finished_unix_ms"] = suite["scenarios"][0]["report"]["finished_unix_ms"] - clock_advance
    path = folder / (SUITE + ".suite.json")
    write_json(path, suite)
    receipt["reports"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(folder / evidence.receipt_filename(METHOD), receipt)
    assert check(bundle) == 1
