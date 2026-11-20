"""Run two selected Edit Mode tests alone and in both orders from independent project copies."""

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4
from xml.etree.ElementTree import ParseError

from check_release_evidence import count, xml_root
from native_test_inputs import checked_path, digest, tree_files
from player_e2e_artifacts import read_json, write_json
from player_e2e_process import Invocation, execute

ORDERS = ("A", "B", "AB", "BA")
XML_LIMIT = 4 * 1024 * 1024


def observe(report, session, expected, process):
    """Require actual ordered callbacks, enabled controls, restored settings and a settled child."""
    before, after = report.get("before"), report.get("after")
    if (
        report.get("session") != session
        or not isinstance(before, dict)
        or type(before.get("enabled")) is not bool
        or type(before.get("options")) is not int
        or before != after
        or report.get("during", {}).get("domainReloadDisabled") is not True
        or process["forced_termination"]
        or process["process_ended"] is not True
    ):
        raise ValueError(
            "Session, domain reload, settings restoration or process evidence is incomplete"
        )
    transitions = report["transitions"]
    if transitions:
        states = [item["state"] for item in transitions]
        cycle = ["ExitingEditMode", "EnteredPlayMode", "ExitingPlayMode", "EnteredEditMode"]
        if (
            states != cycle * (len(states) // 4)
            or len({item["domain"] for item in transitions}) != 1
            or any(item["settings"] != report["during"] for item in transitions)
        ):
            raise ValueError(
                "Play Mode transition, domain identity or settings changed within the trial"
            )
    events = report["events"]
    if len(events) != 2 * len(expected):
        raise ValueError("Unexpected number of test callbacks")
    results = []
    for index, name in enumerate(expected):
        start, end = events[2 * index : 2 * index + 2]
        if (
            (start["phase"], end["phase"]) != ("started", "finished")
            or (start["test"], end["test"]) != (name, name)
            or start.get("settings") != report["during"]
            or end.get("result") not in {"Passed", "Failed"}
        ):
            raise ValueError("Unexpected test order, reload settings or inconclusive leaf result")
        results.append(end["result"])
    failed = "Failed" in results
    if report.get("runResult") != ("Failed" if failed else "Passed") or process[
        "actual_exit_code"
    ] != (2 if failed else 0):
        raise ValueError("Suite or Editor exit disagrees with leaf results")
    return results


def confirm_xml(path, names, results, failures):
    """Match the complete NUnit tree to the independently recorded callback inventory."""
    path = checked_path(path)
    if not path.is_file() or not 0 < (size := path.stat().st_size) <= XML_LIMIT:
        raise ValueError("Expected a nonempty NUnit XML file of at most 4 MiB")
    with path.open("rb") as stream:
        payload = stream.read(size + 1)
    if len(payload) != size:
        raise ValueError("NUnit XML size changed during capture")
    try:
        root = xml_root(payload)
    except ParseError as error:
        raise ValueError("Incomplete or malformed NUnit XML") from error
    run_results = {"Failed", "Failed(Child)"} if "Failed" in results else {"Passed"}
    if root.tag != "test-run" or root.get("result") not in run_results:
        raise ValueError("NUnit root result disagrees with observed run")
    totals = {
        "total": len(names),
        "passed": results.count("Passed"),
        "failed": results.count("Failed"),
        "skipped": 0,
        "inconclusive": 0,
    }
    if any(count(root, key) != value for key, value in totals.items()):
        raise ValueError("NUnit aggregate counts disagree with observed results")
    expected = dict(zip(names, results, strict=True))
    for case in root.iter("test-case"):
        name = case.get("fullname")
        if name not in expected or expected.pop(name) != case.get("result"):
            raise ValueError("NUnit case is duplicated, unexpected or disagrees with its callback")
        entries = case.findall("failure")
        if name in failures:
            messages = case.findall("failure/message")
            if (
                len(entries) != 1
                or len(messages) != 1
                or not (message := messages[0].text)
                or not message.strip()
            ):
                raise ValueError("NUnit failed case requires a single failure message")
            callback = failures[name]
            if not isinstance(callback, str) or message.replace("\r\n", "\n").replace(
                "\r", "\n"
            ) != callback.replace("\r\n", "\n").replace("\r", "\n"):
                raise ValueError("NUnit failure message disagrees with its callback")
        elif entries:
            raise ValueError("NUnit passing case contains failure evidence")
    if expected:
        raise ValueError("NUnit XML omits requested tests")
    return hashlib.sha256(payload).hexdigest()


def classify(cells, repeats):
    """Compare stable within-test outcomes across the four fully observed conditions."""
    if (
        len(cells) != len(ORDERS) * repeats
        or any("results" not in cell for cell in cells)
        or len({cell["session"] for cell in cells}) != len(cells)
        or len({cell["unity_version"] for cell in cells}) != 1
        or any(cell["initial_settings"] != cells[0]["initial_settings"] for cell in cells)
    ):
        return "incomplete"
    observed = {}
    for order in ORDERS:
        group = [cell for cell in cells if cell["order"] == order]
        if {cell["repeat"] for cell in group} != set(range(1, repeats + 1)):
            return "incomplete"
        outcomes = {tuple(cell["results"]) for cell in group}
        if len(outcomes) != 1 or any(cell["failures"] != group[0]["failures"] for cell in group):
            return "inconsistent"
        observed[order] = outcomes.pop()
    if observed["A"] != ("Passed",) or observed["B"] != ("Passed",):
        return "baseline_failure"
    if any(observed[order][0] != "Passed" for order in ("AB", "BA")):
        return "inconsistent"
    return (
        "order_dependency_observed"
        if any(observed[order][1] == "Failed" for order in ("AB", "BA"))
        else "not_observed"
    )


def baseline(prepared):
    manifest = read_json(prepared / "project/Packages/manifest.json")["dependencies"]
    for name, version in manifest.items():
        if version.startswith("file:") and version not in {
            f"file:../../packages/{name}",
            "file:../../package",
        }:
            raise ValueError("Prepared local dependencies must resolve within the copied bundle")
    if (prepared / "project/Library").exists():
        raise ValueError("Use a freshly prepared, unopened QA bundle")
    files = {}
    for name in ("project", "package", "packages"):
        files.update({f"{name}/{path}": sha for path, sha in tree_files(prepared / name).items()})
    if "project/.mcp-order-session" in files:
        raise ValueError("The prepared baseline already belongs to an order trial")
    return files


def run_cell(editor, prepared, files, directory, names):
    directory.mkdir()
    order_file = directory / "tests.txt"
    order_file.write_text("\n".join(names) + "\n", encoding="utf-8")
    session = uuid4().hex
    with tempfile.TemporaryDirectory(prefix="order-") as tmp:
        scratch = Path(tmp).resolve()
        for relative, expected in files.items():
            destination = scratch / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checked_path(prepared / relative), destination)
            if digest(destination) != expected:
                raise ValueError("Prepared inputs changed during copy: " + relative)
        project = scratch / "project"
        (project / ".mcp-order-session").write_text(session, encoding="ascii")
        command = [
            str(editor),
            "-batchmode",
            "-nographics",
            "-forgetProjectPath",
            "-projectPath",
            str(project),
            "-runTests",
            "-testPlatform",
            "EditMode",
            "-testFilter",
            r"\A(?:" + "|".join(re.escape(name).replace(";", r"\u003b") for name in names) + r")\z",
            "-orderedTestListFile",
            str(order_file),
            "-testResults",
            str(directory / "results.xml"),
            "-mcpOrderSession",
            session,
            "-logFile",
            str(directory / "editor.log"),
        ]
        process = execute(Invocation(command, directory, timeout=1200))
        report = read_json(project / "Library/McpTestOrder.json")
        write_json(directory / "observer.json", report)
        results = observe(report, session, names, process)
        if not report.get("unityVersion"):
            raise ValueError("Missing observed Editor version")
        failures = {
            event["test"]: event["message"]
            for event in report["events"]
            if event.get("result") == "Failed"
        }
        xml_sha256 = confirm_xml(directory / "results.xml", names, results, failures)
        return {
            "results": results,
            "session": session,
            "unity_version": report["unityVersion"],
            "initial_settings": report["before"],
            "failures": failures,
            "nunit_sha256": xml_sha256,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared",
        type=Path,
        required=True,
        help="Disposable bundle from unity_ci_packages.py prepare.",
    )
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--a", required=True, help="Exact NUnit full test name.")
    parser.add_argument("--b", required=True, help="Second distinct full test name.")
    parser.add_argument("--repeats", type=int, choices=range(2, 11), default=2)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory.")
    args = parser.parse_args()
    prepared, editor, output = map(checked_path, (args.prepared, args.editor, args.output))
    if (
        output.is_relative_to(prepared)
        or args.a == args.b
        or any(
            not n.strip() or len(n.splitlines()) != 1 or n != n.strip() for n in (args.a, args.b)
        )
    ):
        parser.error(
            "Choose two distinct single-line names and a new output outside the prepared bundle"
        )
    files = baseline(prepared)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "input-files.json", files)
    report = {
        "input_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "editor_sha256": digest(editor),
        "tests": {"A": args.a, "B": args.b},
        "repeats": args.repeats,
        "cells": [],
    }
    for repeat in range(args.repeats):
        for order in ORDERS:
            directory = output / f"{repeat + 1}-{order}"
            cell = {"order": order, "repeat": repeat + 1, "artifacts": directory.name}
            try:
                cell.update(
                    run_cell(
                        editor, prepared, files, directory, [report["tests"][key] for key in order]
                    )
                )
            except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
                cell["unavailable"] = str(error)
            report["cells"].append(cell)
    report["state"] = classify(report["cells"], args.repeats)
    write_json(output / "order-report.json", report)
    print(report["state"], output / "order-report.json")
    return {"not_observed": 0, "order_dependency_observed": 1}.get(report["state"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
