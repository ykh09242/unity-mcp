"""The order verdict needs independent repetitions and actual native callback order."""

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    import run_test_order

    return run_test_order


def test_classification_requires_all_independent_repeats(operator):
    cells = [
        {
            "order": order,
            "repeat": repeat,
            "session": f"{repeat}-{order}",
            "unity_version": "fixture",
            "initial_settings": {"enabled": False, "options": 0},
            "failures": {"B": "retained"} if order == "AB" else {},
            "results": ["Failed" if order == "AB" and test == "B" else "Passed" for test in order],
        }
        for repeat in (1, 2)
        for order in operator.ORDERS
    ]
    assert operator.classify(cells, 2) == "order_dependency_observed"
    assert operator.classify(cells[1:], 2) == "incomplete"
    cells[-1]["session"] = cells[0]["session"]
    assert operator.classify(cells, 2) == "incomplete"
    cells[-1]["session"] = "other"
    cells[-1]["initial_settings"] = {"enabled": True, "options": 1}
    assert operator.classify(cells, 2) == "incomplete"
    cells[-1]["initial_settings"] = cells[0]["initial_settings"]
    cells[-1]["results"] = ["Passed", "Failed"]
    assert operator.classify(cells, 2) == "inconsistent"


def test_callbacks_must_match_order_exit_and_restored_settings(operator):
    before = {"enabled": False, "options": 0, "domainReloadDisabled": False}
    during = {"enabled": True, "options": 1, "domainReloadDisabled": True}
    events = [
        event
        for name in ["A", "B"]
        for event in [
            {"phase": "started", "test": name, "settings": during},
            {"phase": "finished", "test": name, "result": "Passed" if name == "A" else "Failed"},
        ]
    ]
    report = {
        "session": "match",
        "before": before,
        "after": before,
        "during": during,
        "events": events,
        "transitions": [],
        "runResult": "Failed",
    }
    process = {"process_ended": True, "forced_termination": False, "actual_exit_code": 2}
    assert operator.observe(report, "match", ["A", "B"], process) == ["Passed", "Failed"]
    with pytest.raises(ValueError):
        operator.observe(report, "match", ["B", "A"], process)
    with pytest.raises(ValueError):
        operator.observe({**report, "after": during}, "match", ["A", "B"], process)
    with pytest.raises(ValueError):
        operator.observe(report, "match", ["A", "B"], {**process, "actual_exit_code": 1})


def nunit_pair():
    # NUnit serializes its suite tree independently of callback execution order.
    root = ElementTree.Element(
        "test-run",
        result="Failed(Child)",
        total="2",
        passed="1",
        failed="1",
        skipped="0",
        inconclusive="0",
    )
    suite = ElementTree.SubElement(root, "test-suite")
    failed = ElementTree.SubElement(suite, "test-case", fullname="B", result="Failed")
    ElementTree.SubElement(
        ElementTree.SubElement(failed, "failure"), "message"
    ).text = "retained\nactual 1"
    ElementTree.SubElement(suite, "test-case", fullname="A", result="Passed")
    return root


def test_complete_native_xml_matches_callback_cases_in_any_xml_order(operator, tmp_path):
    payload = ElementTree.tostring(nunit_pair())
    path = tmp_path / "results.xml"
    path.write_bytes(payload)
    assert (
        operator.confirm_xml(path, ["A", "B"], ["Passed", "Failed"], {"B": "retained\r\nactual 1"})
        == hashlib.sha256(payload).hexdigest()
    )


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "truncated",
        "duplicate",
        "wrong_name",
        "wrong_result",
        "wrong_failure",
        "missing_case",
        "missing_message",
        "wrong_total",
        "wrong_root",
        "oversized",
    ],
)
def test_native_order_rejects_unconfirmed_xml(operator, tmp_path, fault):
    root = nunit_pair()
    failed, passed = list(root.iter("test-case"))
    match fault:
        case "duplicate":
            passed.set("fullname", "B")
            passed.set("result", "Failed")
        case "wrong_name":
            passed.set("fullname", "Other")
        case "wrong_result":
            failed.set("result", "Passed")
        case "wrong_failure":
            failed.find("failure/message").text = "another failure"
        case "missing_case":
            root.find("test-suite").remove(passed)
        case "missing_message":
            failed.remove(failed.find("failure"))
        case "wrong_total":
            root.set("passed", "2")
        case "wrong_root":
            root.set("result", "Passed")
    payload = ElementTree.tostring(root)
    if fault == "empty":
        payload = b""
    elif fault == "truncated":
        payload = payload[:-8]
    elif fault == "oversized":
        payload += b" " * operator.XML_LIMIT
    path = tmp_path / "results.xml"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        operator.confirm_xml(path, ["A", "B"], ["Passed", "Failed"], {"B": "retained\nactual 1"})


def test_run_cell_never_accepts_good_callbacks_with_empty_xml(operator, tmp_path, monkeypatch):
    source = tmp_path / "baseline"
    (source / "project").mkdir(parents=True)
    sentinel = source / "project/fixture"
    sentinel.write_bytes(b"test fixture")

    def capture(invocation):
        args = invocation.command
        project = Path(args[args.index("-projectPath") + 1])
        args_session = args[args.index("-mcpOrderSession") + 1]
        settings = {"enabled": True, "options": 1, "domainReloadDisabled": True}
        report = {
            "session": args_session,
            "before": settings,
            "during": settings,
            "after": settings,
            "unityVersion": "fixture",
            "transitions": [],
            "runResult": "Passed",
            "events": [
                {"phase": "started", "test": "A", "settings": settings},
                {"phase": "finished", "test": "A", "result": "Passed"},
            ],
        }
        (project / "Library").mkdir()
        (project / "Library/McpTestOrder.json").write_text(json.dumps(report), encoding="utf-8")
        Path(args[args.index("-testResults") + 1]).write_bytes(b"")
        return {"forced_termination": False, "process_ended": True, "actual_exit_code": 0}

    monkeypatch.setattr(operator, "execute", capture)
    with pytest.raises(ValueError, match="nonempty NUnit XML"):
        operator.run_cell(
            Path("fixture-editor"),
            source,
            {"project/fixture": operator.digest(sentinel)},
            tmp_path / "cell",
            ["A"],
        )
