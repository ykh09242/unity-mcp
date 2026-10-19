"""Reject skipped/stale PlayMode matrix rows rather than promoting them to native passes."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_unity_playmode_evidence import REQUIRED_TESTS, check

SESSION = "a" * 32
VERSION = "6000.0.69f1"


def results(tmp_path: Path, mode: str = "on") -> Path:
    root = ET.Element(
        "test-run",
        result="Passed",
        total="5",
        passed="5",
        failed="0",
        skipped="0",
        inconclusive="0",
    )
    for name in REQUIRED_TESTS:
        case = ET.SubElement(root, "test-case", fullname=name, result="Passed")
        if name == REQUIRED_TESTS[0]:
            ET.SubElement(
                case, "output"
            ).text = f"MCP_PLAYMODE_BODY session={SESSION} reload={mode} unity={VERSION}\n"
    path = tmp_path / "playmode.xml"
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["on", "off"])
def test_accepts_required_bodies_with_exact_attribution(tmp_path, mode):
    path = results(tmp_path, mode)
    assert check(path, "success", SESSION, mode, VERSION) == 0


@pytest.mark.parametrize(
    "mutation", ["skip", "missing", "session", "reload", "version", "duplicate", "failed-runner"]
)
def test_rejects_non_evidence_rows(tmp_path, mutation):
    path = results(tmp_path)
    tree = ET.parse(path)
    root = tree.getroot()
    case = root.find("test-case")
    outcome = "success"
    match mutation:
        case "skip":
            case.set("result", "Skipped")
            root.set("passed", "4")
            root.set("skipped", "1")
        case "missing":
            root.remove(case)
            root.set("passed", "4")
        case "session":
            case.find("output").text = case.findtext("output").replace(SESSION, "b" * 32)
        case "reload":
            case.find("output").text = case.findtext("output").replace("reload=on", "reload=off")
        case "version":
            case.find("output").text = case.findtext("output").replace(VERSION, "2022.3.69f1")
        case "duplicate":
            ET.SubElement(root, "test-case", fullname=REQUIRED_TESTS[0], result="Passed")
            root.set("passed", "6")
        case "failed-runner":
            outcome = "failure"
    tree.write(path, encoding="utf-8")
    assert check(path, outcome, SESSION, "on", VERSION) == 1
