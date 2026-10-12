"""Summarize existing JUnit reports and warn about comparable CI timing regressions."""

import argparse
from collections import defaultdict
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import subprocess
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
import zipfile


SCHEMA = 1
ARTIFACT = "python-test-timings"
MAX_REPORT_BYTES = 2 * 1024 * 1024
MIN_SAMPLES = 3
MAX_SAMPLES = 5


def seconds(value) -> float:
    if type(value) not in (int, float):
        raise ValueError("Timing must be a JSON number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError("Timing must be a finite nonnegative number") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError("Timing must be a finite nonnegative number")
    return number


def junit_summary(path: Path) -> dict:
    root = ET.parse(path).getroot()
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Expected a JUnit testsuite or testsuites document")
    suites = [suite for suite in root.iter("testsuite") if not suite.findall("testsuite")]
    cases = list(root.iter("testcase"))
    if not cases or not suites:
        raise ValueError("JUnit report contains no test cases")
    modules = defaultdict(float)
    identities = []
    slowest = []
    for case in cases:
        name, module = case.attrib["name"], case.attrib.get("classname", "unknown")
        elapsed = seconds(float(case.attrib["time"]))
        outcome = next(
            (tag for tag in ("error", "failure", "skipped") if case.find(tag) is not None), "passed"
        )
        identities.append((module, name, outcome))
        modules[module] += elapsed
        slowest.append({"name": f"{module}::{name}", "seconds": elapsed})
    return {
        "elapsed": sum(seconds(float(suite.attrib["time"])) for suite in suites),
        "tests": len(cases),
        "skipped": sum(outcome == "skipped" for _, _, outcome in identities),
        "successful": all(outcome in {"passed", "skipped"} for _, _, outcome in identities)
        and all(
            int(suite.get("failures", 0)) + int(suite.get("errors", 0)) == 0 for suite in suites
        ),
        "inventory": hashlib.sha256(json.dumps(sorted(identities)).encode()).hexdigest(),
        "modules": dict(sorted(modules.items())),
        "slowest": sorted(slowest, key=lambda item: item["seconds"], reverse=True)[:10],
    }


def snapshot(
    junit: Path,
    *,
    python: str,
    suite: str,
    runner: str,
    workers: int,
    coverage: bool,
    nested: Path | None = None,
) -> dict:
    result = {
        "schema": SCHEMA,
        "id": f"{python}-{suite}",
        "context": {
            "python": python,
            "suite": suite,
            "runner": runner,
            "workers": workers,
            "coverage": coverage,
        },
        **junit_summary(junit),
    }
    if nested is not None and nested.is_file():
        # The wrapper already includes this time; retain detail without adding it twice.
        result["nestedHttp"] = junit_summary(nested)
    return result


def validate_snapshot(value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or value["schema"] != SCHEMA
        or not isinstance(value["context"], dict)
    ):
        raise ValueError("Unsupported timing snapshot")
    if not re.fullmatch(r"3\.\d+-(server|tools)", value["id"]):
        raise ValueError("Invalid timing snapshot identity")
    if value["id"] != f"{value['context']['python']}-{value['context']['suite']}":
        raise ValueError("Inconsistent timing snapshot identity")
    summaries = [value, value["nestedHttp"]] if "nestedHttp" in value else [value]
    for summary in summaries:
        if not isinstance(summary, dict) or not re.fullmatch(r"[a-f0-9]{64}", summary["inventory"]):
            raise ValueError("Invalid timing test inventory")
        if (
            type(summary["successful"]) is not bool
            or type(summary["tests"]) is not int
            or summary["tests"] <= 0
            or type(summary["skipped"]) is not int
            or not 0 <= summary["skipped"] <= summary["tests"]
            or not isinstance(summary["modules"], dict)
            or not isinstance(summary["slowest"], list)
        ):
            raise ValueError("Invalid timing result")
        seconds(summary["elapsed"])
        for duration in summary["modules"].values():
            seconds(duration)
        for case in summary["slowest"]:
            if not isinstance(case, dict) or not isinstance(case["name"], str):
                raise ValueError("Invalid testcase timing")
            seconds(case["seconds"])
    return value


def read_bundle(content: bytes) -> list[dict]:
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("Timing report exceeds size limit")
    bundle = json.loads(content)
    if (
        not isinstance(bundle, dict)
        or bundle["schema"] != SCHEMA
        or not isinstance(bundle["snapshots"], list)
    ):
        raise ValueError("Unsupported timing bundle")
    entries = [validate_snapshot(value) for value in bundle["snapshots"]]
    if len({value["id"] for value in entries}) != len(entries):
        raise ValueError("Duplicate timing snapshot identity")
    return entries


def github_api(endpoint: str) -> bytes:
    # gh handles authentication and signed artifact redirects without exposing tokens.
    result = subprocess.run(
        ["gh", "api", "--method", "GET", "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError("GitHub history request failed; check actions:read access")
    return result.stdout


def github_history(repository: str, run_id: int) -> tuple[list[list[dict]], list[str]]:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or run_id <= 0:
        raise ValueError("Invalid GitHub repository or run ID")
    prefix = f"repos/{repository}/actions"
    current = json.loads(github_api(f"{prefix}/runs/{run_id}"))
    query = urlencode(
        {
            "branch": current["head_branch"],
            "event": current["event"],
            "status": "success",
            "per_page": 20,
        }
    )
    runs = json.loads(github_api(f"{prefix}/workflows/{int(current['workflow_id'])}/runs?{query}"))[
        "workflow_runs"
    ]
    history, notes = [], []
    # Bound both API work and downloads; histories before rollout have no timing artifact.
    candidates = [
        run
        for run in runs
        if run["id"] < run_id
        and run["conclusion"] == "success"
        and run["head_branch"] == current["head_branch"]
        and run["event"] == current["event"]
        and run["workflow_id"] == current["workflow_id"]
    ][:10]
    for run in candidates:
        try:
            query = urlencode({"name": ARTIFACT, "per_page": 10})
            artifacts = json.loads(github_api(f"{prefix}/runs/{run['id']}/artifacts?{query}"))[
                "artifacts"
            ]
            artifact = next(
                (item for item in artifacts if item["name"] == ARTIFACT and not item["expired"]),
                None,
            )
            if artifact is None:
                continue
            if artifact["size_in_bytes"] > MAX_REPORT_BYTES:
                raise ValueError("Timing artifact exceeds size limit")
            content = github_api(f"{prefix}/artifacts/{int(artifact['id'])}/zip")
            if len(content) > MAX_REPORT_BYTES:
                raise ValueError("Timing archive exceeds size limit")
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                member = archive.getinfo("timings.json")
                if member.file_size > MAX_REPORT_BYTES:
                    raise ValueError("Timing report exceeds size limit")
                history.append(read_bundle(archive.read(member)))
            notes.append(
                f"History: [run {run['id']}](https://github.com/{repository}/actions/runs/{run['id']})."
            )
            if len(history) == MAX_SAMPLES:
                break
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            zipfile.BadZipFile,
            subprocess.TimeoutExpired,
        ):
            notes.append(f"Skipped unavailable or invalid timing history from run {run['id']}.")
    return history, notes


def comparable(current: dict, previous: dict) -> bool:
    return (
        current["successful"]
        and previous["successful"]
        and current["context"] == previous["context"]
        and current["inventory"] == previous["inventory"]
        and current.get("nestedHttp", {}).get("inventory")
        == previous.get("nestedHttp", {}).get("inventory")
    )


def regression(
    current: float, samples: list[float], minimum_seconds: float
) -> tuple[float | None, bool]:
    if len(samples) < MIN_SAMPLES:
        return None, False
    baseline = median(samples[:MAX_SAMPLES])
    delta = current - baseline
    # Both thresholds must hold; one slow runner or tiny fast test should not warn.
    return baseline, delta >= minimum_seconds and current >= baseline * 1.25


def markdown(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def render_report(
    current: list[dict], history: list[list[dict]], notes: list[str]
) -> tuple[str, list[str]]:
    lines = [
        "# Python test performance",
        "",
        "Warning-only observations from existing JUnit reports; no extra test runs.",
        "Comparable history requires the same workflow, branch, event, Python minor, runner, workers, coverage and test/skip inventory.",
        "Baselines use the median of up to five successful runs, with at least three samples.",
        "",
        "| Suite | Tests | Elapsed | Baseline | Samples | Status |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    warnings, details = [], []
    for entry in sorted(current, key=lambda value: value["id"]):
        prior = [
            value
            for run in history
            for value in run
            if value["id"] == entry["id"] and comparable(entry, value)
        ][:MAX_SAMPLES]
        baseline, slower = regression(entry["elapsed"], [value["elapsed"] for value in prior], 10)
        status = (
            "Slower" if slower else "Observed" if baseline is not None else "Collecting baseline"
        )
        if not entry["successful"]:
            status = "Failed test run; excluded from comparison"
        base_text = f"{baseline:.2f}s" if baseline is not None else "-"
        lines.append(
            f"| {entry['id']} | {entry['tests']} | {entry['elapsed']:.2f}s | {base_text} | {len(prior)} | {status} |"
        )
        if slower:
            warnings.append(
                f"{entry['id']} test elapsed time increased at least 25% and 10s versus its comparable median."
            )
        details.extend(
            [
                "",
                f"<details><summary>{entry['id']} timing detail</summary>",
                "",
                "Module values sum testcase durations, including setup/teardown; parallel sums are not wall time.",
                "",
                "| Slow module | Current | Baseline |",
                "| --- | ---: | ---: |",
            ]
        )
        for name, elapsed in sorted(
            entry["modules"].items(), key=lambda pair: pair[1], reverse=True
        )[:5]:
            samples = [value["modules"][name] for value in prior if name in value["modules"]]
            module_base, module_slower = regression(elapsed, samples, 5)
            text = f"{module_base:.2f}s" if module_base is not None else "-"
            details.append(
                f"| {markdown(name)} | {elapsed:.2f}s | {text}{' (slower)' if module_slower else ''} |"
            )
            if module_slower:
                warnings.append(
                    f"{entry['id']} has a module at least 25% and 5s slower; see the timing report."
                )
        details.extend(["", "| Slow testcase | Duration |", "| --- | ---: |"])
        for case in entry["slowest"][:3]:
            details.append(f"| {markdown(case['name'])} | {case['seconds']:.2f}s |")
        nested = entry.get("nestedHttp")
        if nested:
            details.extend(
                [
                    "",
                    f"Nested HTTP: {nested['tests']} cases, {nested['elapsed']:.2f}s; already included in its wrapper.",
                ]
            )
        details.extend(["", "</details>"])
    lines.extend(["", *notes, *details, ""])
    return "\n".join(lines), sorted(set(warnings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot")
    capture.add_argument("--junit", type=Path, required=True)
    capture.add_argument("--nested-junit", type=Path)
    capture.add_argument("--python", required=True)
    capture.add_argument("--suite", choices=("server", "tools"), required=True)
    capture.add_argument(
        "--runner",
        default=f"{os.environ.get('RUNNER_OS', 'local')}-{os.environ.get('RUNNER_ARCH', 'unknown')}",
    )
    capture.add_argument("--workers", type=int, default=0)
    capture.add_argument("--coverage", action="store_true")
    capture.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("report")
    report.add_argument("--current", type=Path, required=True)
    report.add_argument("--history", type=Path, nargs="*", default=[])
    report.add_argument("--repository")
    report.add_argument("--run-id", type=int)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        result = snapshot(
            args.junit,
            python=args.python,
            suite=args.suite,
            runner=args.runner,
            workers=args.workers,
            coverage=args.coverage,
            nested=args.nested_junit,
        )
        validate_snapshot(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    current = [
        validate_snapshot(json.loads(path.read_bytes()))
        for path in sorted(args.current.glob("*.json"))
    ]
    if not current or len({entry["id"] for entry in current}) != len(current):
        raise ValueError("Missing or duplicate current timing snapshots")
    history = [read_bundle(path.read_bytes()) for path in args.history]
    notes = []
    if args.repository:
        try:
            history, notes = github_history(args.repository, args.run_id)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            notes.append(
                "GitHub timing history is unavailable; reporting current measurements only."
            )
    output, warnings = render_report(current, history, notes)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "timings.json").write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "runId": args.run_id,
                "repository": args.repository,
                "snapshots": current,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.output / "report.md").write_text(output, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(output)
    for warning in warnings:
        print(f"::warning::{warning}")
    for note in notes:
        print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
