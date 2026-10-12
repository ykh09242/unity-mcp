"""Summarize existing JUnit reports and warn about comparable CI timing regressions."""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
from statistics import median
import subprocess
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
import zipfile

if __package__:
    from tools import ci_job_performance, ci_worker_timing
else:
    import ci_job_performance
    import ci_worker_timing


SCHEMA = 1
ARTIFACT = "python-test-timings"
MAX_REPORT_BYTES = 2 * 1024 * 1024
MIN_SAMPLES = 3
MAX_SAMPLES = 5
CSHARP_PHASES = ("sdk_discovery", "compile", "execution")


def execution_environment() -> dict:
    """Capture versions, without machine identities or an unbounded SDK command."""
    system = platform.system()
    if system == "Linux":
        try:
            release = platform.freedesktop_os_release()
            os_version = f"{release['ID']}-{release['VERSION_ID']}"
        except (OSError, KeyError):
            os_version = "Linux-unknown"
    elif system == "Darwin":
        os_version = f"macOS-{platform.mac_ver()[0]}"
    else:
        os_version = f"{system}-{platform.release()}"
    sdk = "unavailable"
    try:
        result = subprocess.run(
            ["dotnet", "--version"], capture_output=True, text=True, timeout=5, check=False
        )
        lines = result.stdout.strip().splitlines()
        if result.returncode == 0 and lines and re.fullmatch(r"[\w.+-]{1,100}", lines[-1]):
            sdk = lines[-1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "os": os_version,
        "imageOS": os.environ.get("ImageOS", "unavailable"),
        "imageVersion": os.environ.get("ImageVersion", "unavailable"),
        "python": platform.python_version(),
        "dotnetSdk": sdk,
    }


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
    module_identities = defaultdict(list)
    identities = []
    slowest = []
    csharp_phases, csharp_runtime = [], {}
    for case in cases:
        name, module = case.attrib["name"], case.attrib.get("classname", "unknown")
        elapsed = seconds(float(case.attrib["time"]))
        outcome = next(
            (tag for tag in ("error", "failure", "skipped") if case.find(tag) is not None), "passed"
        )
        identities.append((module, name, outcome))
        module_identities[module].append((name, outcome))
        modules[module] += elapsed
        slowest.append({"name": f"{module}::{name}", "seconds": elapsed})
        for prop in case.findall("properties/property"):
            prop_name = prop.get("name", "")
            for phase in CSHARP_PHASES:
                if prop_name == f"csharp_{phase}_seconds":
                    csharp_phases.append(
                        {
                            "case": f"{module}::{name}",
                            "phase": phase,
                            "seconds": seconds(float(prop.attrib["value"])),
                        }
                    )
            if prop_name in {"csharp_sdk_version", "csharp_target_framework"}:
                value = prop.attrib["value"]
                if prop_name in csharp_runtime and csharp_runtime[prop_name] != value:
                    raise ValueError("Inconsistent C# runtime metadata")
                csharp_runtime[prop_name] = value
    result = {
        "elapsed": sum(seconds(float(suite.attrib["time"])) for suite in suites),
        "tests": len(cases),
        "skipped": sum(outcome == "skipped" for _, _, outcome in identities),
        "successful": all(outcome in {"passed", "skipped"} for _, _, outcome in identities)
        and all(
            int(suite.get("failures", 0)) + int(suite.get("errors", 0)) == 0 for suite in suites
        ),
        "inventory": hashlib.sha256(json.dumps(sorted(identities)).encode()).hexdigest(),
        "modules": dict(sorted(modules.items())),
        "moduleInventories": {
            module: hashlib.sha256(json.dumps(sorted(cases)).encode()).hexdigest()
            for module, cases in sorted(module_identities.items())
        },
        "slowest": sorted(slowest, key=lambda item: item["seconds"], reverse=True)[:10],
    }
    if csharp_phases:
        result["csharpPhases"] = csharp_phases
    if csharp_runtime:
        result["csharpRuntime"] = csharp_runtime
    return result


def snapshot(
    junit: Path,
    *,
    python: str,
    suite: str,
    runner: str,
    workers: int,
    coverage: bool,
    nested: Path | None = None,
    environment: dict | None = None,
    worker_timings: Path | None = None,
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
    if environment is not None:
        result["environment"] = environment
        # Image builds and patch updates remain visible without continually resetting history.
        # An OS release change starts a new baseline; legacy reports never guess their OS.
        result["context"]["os"] = environment["os"]
    if nested is not None and nested.is_file():
        # The wrapper already includes this time; retain detail without adding it twice.
        result["nestedHttp"] = junit_summary(nested)
    if worker_timings is not None and worker_timings.is_file():
        telemetry = ci_worker_timing.read_worker_timings(worker_timings)
        result["workerTimings"] = telemetry
        result["context"]["distribution"] = telemetry["distribution"]
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
    if "workerTimings" in value:
        telemetry = ci_worker_timing.validate_worker_timings(value["workerTimings"])
        if (
            value["context"].get("distribution") != telemetry["distribution"]
            or max(1, value["context"]["workers"]) != telemetry["expectedWorkers"]
            or telemetry["complete"]
            and sum(worker["testCount"] for worker in telemetry["workers"]) != value["tests"]
        ):
            raise ci_job_performance.WorkloadError("Inconsistent worker timing distribution")
    if "environment" in value:
        environment = value["environment"]
        if (
            not isinstance(environment, dict)
            or set(environment) != {"os", "imageOS", "imageVersion", "python", "dotnetSdk"}
            or any(
                not isinstance(item, str) or not 0 < len(item) <= 200
                for item in environment.values()
            )
            or environment["os"] != value["context"].get("os")
        ):
            raise ValueError("Invalid execution environment")
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
        if "moduleInventories" in summary:
            inventories = summary["moduleInventories"]
            if (
                not isinstance(inventories, dict)
                or inventories.keys() != summary["modules"].keys()
                or any(
                    not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                    for value in inventories.values()
                )
            ):
                raise ValueError("Invalid timing module inventory")
        for case in summary["slowest"]:
            if not isinstance(case, dict) or not isinstance(case["name"], str):
                raise ValueError("Invalid testcase timing")
            seconds(case["seconds"])
        if "csharpPhases" in summary:
            phases = summary["csharpPhases"]
            if not isinstance(phases, list) or len(phases) > 100:
                raise ValueError("Invalid C# timing phases")
            for phase in phases:
                if (
                    not isinstance(phase, dict)
                    or not isinstance(phase.get("case"), str)
                    or phase.get("phase") not in CSHARP_PHASES
                ):
                    raise ValueError("Invalid C# timing phase")
                seconds(phase["seconds"])
        if "csharpRuntime" in summary:
            runtime = summary["csharpRuntime"]
            if (
                not isinstance(runtime, dict)
                or not runtime.keys() <= {"csharp_sdk_version", "csharp_target_framework"}
                or any(
                    not isinstance(item, str) or not 0 < len(item) <= 200
                    for item in runtime.values()
                )
            ):
                raise ValueError("Invalid C# runtime metadata")
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


def github_history(
    repository: str,
    run_id: int,
    workload: ci_job_performance.WorkloadHistory | None = None,
) -> tuple[list[list[dict]], list[str]]:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or run_id <= 0:
        raise ValueError("Invalid GitHub repository or run ID")
    prefix = f"repos/{repository}/actions"
    current = (
        workload.current_run if workload else json.loads(github_api(f"{prefix}/runs/{run_id}"))
    )
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
            artifact_name = (
                "compile-stage-timings" if workload and workload.scope == "compile" else ARTIFACT
            )
            query = urlencode({"name": artifact_name, "per_page": 10})
            artifacts = json.loads(github_api(f"{prefix}/runs/{run['id']}/artifacts?{query}"))[
                "artifacts"
            ]
            artifact = next(
                (
                    item
                    for item in artifacts
                    if item["name"] == artifact_name and not item["expired"]
                ),
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
                report_content = archive.read(member)
                history.append(read_bundle(report_content))
                if workload:
                    try:
                        record = json.loads(report_content).get("workload")
                        if record is not None:
                            sample = ci_job_performance.read_workload(record)
                            if (
                                sample.run_id != run["id"]
                                or sample.run_attempt != run["run_attempt"]
                                or sample.context.workflow_id != run["workflow_id"]
                                or sample.context.event != run["event"]
                                or sample.context.branch != run["head_branch"]
                                or sample.context.scope != workload.scope
                            ):
                                raise ci_job_performance.WorkloadError(
                                    "Inconsistent workload history provenance"
                                )
                            workload.samples.append(sample)
                    except (ValueError, KeyError, TypeError, OverflowError):
                        notes.append(f"Skipped invalid workload history from run {run['id']}.")
            notes.append(
                f"History: [run {run['id']}](https://github.com/{repository}/actions/runs/{run['id']})."
            )
            if len(history) >= MAX_SAMPLES and (
                workload is None or len(workload.samples) >= MAX_SAMPLES
            ):
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


def module_comparable(current: dict, previous: dict, name: str) -> bool:
    if (
        not current["successful"]
        or not previous["successful"]
        or current["context"] != previous["context"]
        or name not in previous["modules"]
    ):
        return False
    if current.get("nestedHttp", {}).get("inventory") != previous.get("nestedHttp", {}).get(
        "inventory"
    ):
        # Preserve unrelated modules only when the enclosing HTTP wrapper is identifiable.
        wrapper = "test_http_security"
        if wrapper in name.split(".") or not all(
            any(wrapper in module.split(".") for module in value["modules"])
            for value in (current, previous)
        ):
            return False
    if "moduleInventories" not in current or "moduleInventories" not in previous:
        return comparable(current, previous)
    return current["moduleInventories"][name] == previous["moduleInventories"][name]


def github_jobs(repository: str, run_id: int) -> list[dict]:
    """Fetch one bounded, latest-attempt job inventory shared by stage/workload reporting."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or run_id <= 0:
        raise ValueError("Invalid GitHub repository or run ID")
    jobs, inventory_count = [], None
    for page in range(1, 4):
        query = urlencode({"filter": "latest", "per_page": 100, "page": page})
        response = json.loads(github_api(f"repos/{repository}/actions/runs/{run_id}/jobs?{query}"))
        count = response["total_count"]
        if type(count) is not int or not 0 <= count <= 300:
            raise ValueError("Stage job inventory exceeds size limit")
        if inventory_count is not None and inventory_count != count:
            raise ci_job_performance.WorkloadError("Job inventory changed during pagination")
        inventory_count = count
        page_jobs = response["jobs"]
        if (
            not isinstance(page_jobs, list)
            or len(page_jobs) > 100
            or any(not isinstance(job, dict) for job in page_jobs)
        ):
            raise ci_job_performance.WorkloadError("Invalid job inventory")
        jobs.extend(page_jobs)
        if page * 100 >= count:
            if len(jobs) != count:
                raise ci_job_performance.WorkloadError("Incomplete job inventory")
            return jobs
    raise ValueError("Stage job inventory exceeds page limit")


def github_stages(repository: str, run_id: int, jobs: list[dict] | None = None) -> list[dict]:
    """Observe completed steps from this run without waiting for unfinished jobs."""
    stages = []
    for job in github_jobs(repository, run_id) if jobs is None else jobs:
        for step in job.get("steps", []):
            name = step["name"]
            if (
                step["status"] != "completed"
                or step["conclusion"] not in {"success", "failure", "cancelled", "timed_out"}
                or not any(
                    word in name.lower()
                    for word in (
                        "pylint",
                        "cache",
                        "compile",
                        "prepare",
                        "optional dependency inputs",
                    )
                )
            ):
                continue
            if not all(isinstance(step[field], str) for field in ("started_at", "completed_at")):
                raise ValueError("Stage timestamps require ISO strings")
            start = datetime.fromisoformat(step["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(step["completed_at"].replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("Stage timestamps require timezone offsets")
            stages.append(
                {
                    "job": job["name"],
                    "name": name,
                    "seconds": seconds((end - start).total_seconds()),
                    "conclusion": step["conclusion"],
                }
            )
    return stages


def render_stages(stages: list[dict]) -> str:
    lines = [
        "",
        "## CI stage timings",
        "",
        "Completed Pylint, cache, preparation and compile steps from this workflow run. "
        "Durations have one-second API precision and are observations, not regression gates.",
        "",
        "| Job | Step | Duration | Result |",
        "| --- | --- | ---: | --- |",
    ]
    for stage in stages:
        lines.append(
            f"| {markdown(stage['job'])} | {markdown(stage['name'])} | "
            f"{stage['seconds']:.2f}s | {markdown(stage['conclusion'])} |"
        )
    return "\n".join([*lines, ""])


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
        "Comparable history requires the same workflow, branch, event, Python minor, runner, OS release, workers, coverage and test/skip inventory.",
        "Unchanged modules retain their own baseline when another module changes; legacy artifacts require a matching whole suite.",
        "OS releases start separate baselines; older reports without OS metadata are not mixed with observed environments.",
        "Image builds and Python/.NET patch versions are recorded below; their updates do not reset baselines.",
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
            ]
        )
        environment = entry.get("environment")
        if environment:
            details.extend(
                [
                    "| OS | Runner image | Image build | Python | Default .NET SDK |",
                    "| --- | --- | --- | --- | --- |",
                    "| "
                    + " | ".join(
                        markdown(environment[field])
                        for field in ("os", "imageOS", "imageVersion", "python", "dotnetSdk")
                    )
                    + " |",
                    "",
                ]
            )
        details.extend(
            [
                "| Slow module | Current | Baseline | Samples |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for name, elapsed in sorted(
            entry["modules"].items(), key=lambda pair: pair[1], reverse=True
        )[:5]:
            samples = [
                value["modules"][name]
                for run in history
                for value in run
                if value["id"] == entry["id"] and module_comparable(entry, value, name)
            ][:MAX_SAMPLES]
            module_base, module_slower = regression(elapsed, samples, 5)
            text = f"{module_base:.2f}s" if module_base is not None else "-"
            details.append(
                f"| {markdown(name)} | {elapsed:.2f}s | {text}{' (slower)' if module_slower else ''} | {len(samples)} |"
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
        if entry.get("csharpPhases"):
            runtime = entry.get("csharpRuntime", {})
            details.extend(
                [
                    "",
                    "C# subprocess phases are already included in testcase durations; compilation is shared once per module.",
                    f"Selected SDK: {markdown(runtime.get('csharp_sdk_version', 'unavailable'))}; target: {markdown(runtime.get('csharp_target_framework', 'unavailable'))}.",
                    "",
                    "| C# phase | Calls | Total | Longest |",
                    "| --- | ---: | ---: | ---: |",
                ]
            )
            for phase in CSHARP_PHASES:
                samples = [
                    sample["seconds"]
                    for sample in entry["csharpPhases"]
                    if sample["phase"] == phase
                ]
                if samples:
                    details.append(
                        f"| {phase} | {len(samples)} | {sum(samples):.3f}s | {max(samples):.3f}s |"
                    )
        if "workerTimings" in entry:
            if not entry["workerTimings"]["complete"]:
                details.append(
                    "Worker timing telemetry is incomplete; only available worker observations are shown."
                )
            details.append(ci_worker_timing.render_worker_timings(entry["workerTimings"]))
        else:
            details.append("Worker timing telemetry is unavailable for this suite.")
        details.extend(["", "</details>"])
    lines.extend(["", *notes, *details, ""])
    return "\n".join(lines), sorted(set(warnings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot")
    capture.add_argument("--junit", type=Path, required=True)
    capture.add_argument("--nested-junit", type=Path)
    capture.add_argument("--worker-timings", type=Path)
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
    report.add_argument("--workload", choices=("python", "compile"))
    report.add_argument("--output", type=Path, required=True)
    stages = commands.add_parser("stages")
    stages.add_argument("--repository", required=True)
    stages.add_argument("--run-id", type=int, required=True)
    stages.add_argument("--workload", choices=("python", "compile"))
    stages.add_argument("--output", type=Path, required=True)
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
            environment=execution_environment(),
            worker_timings=args.worker_timings,
        )
        validate_snapshot(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    current, history, notes, stage_timings = [], [], [], []
    workload_history, workload_observation, workload_run = [], None, None
    if args.command == "report":
        current = [
            validate_snapshot(json.loads(path.read_bytes()))
            for path in sorted(args.current.glob("*.json"))
        ]
        if not current or len({entry["id"] for entry in current}) != len(current):
            raise ValueError("Missing or duplicate current timing snapshots")
        for path in args.history[:10]:
            try:
                history_content = path.read_bytes()
                history.append(read_bundle(history_content))
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                notes.append("Skipped unavailable or invalid local timing history.")
                continue
            if args.workload:
                try:
                    record = json.loads(history_content).get("workload")
                    if record is not None:
                        workload_history.append(ci_job_performance.read_workload(record))
                except (OSError, ValueError, KeyError, TypeError, OverflowError):
                    notes.append("Skipped invalid local workload history.")
    if args.repository and (args.command == "report" or args.workload):
        try:
            if args.workload:
                if (
                    not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository)
                    or type(args.run_id) is not int
                    or args.run_id <= 0
                ):
                    raise ValueError("Invalid GitHub repository or run ID")
                candidate_run = json.loads(
                    github_api(f"repos/{args.repository}/actions/runs/{args.run_id}")
                )
                if candidate_run["id"] != args.run_id:
                    raise ci_job_performance.WorkloadError("Unexpected workload run ID")
                workload_run = candidate_run
                history, history_notes = github_history(
                    args.repository,
                    args.run_id,
                    ci_job_performance.WorkloadHistory(
                        args.workload, workload_run, workload_history
                    ),
                )
            else:
                history, history_notes = github_history(args.repository, args.run_id)
            notes.extend(history_notes)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            notes.append(
                "GitHub timing history is unavailable; reporting current measurements only."
            )
    if args.repository:
        jobs = None
        try:
            jobs = github_jobs(args.repository, args.run_id)
            stage_timings = github_stages(args.repository, args.run_id, jobs)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            notes.append("Stage timings are unavailable; test and compile gates are unaffected.")
        if args.workload and jobs is not None:
            try:
                if workload_run is None:
                    raise ci_job_performance.WorkloadError("Workload run metadata is unavailable")
                workload_observation = ci_job_performance.observe(
                    jobs, workload_run, (args.workload, current)
                )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                OverflowError,
                subprocess.TimeoutExpired,
            ):
                notes.append(
                    "Completed workload timings are unavailable or incomplete; test and compile gates are unaffected."
                )
    if args.command == "report":
        output, warnings = render_report(current, history, notes)
    else:
        output, warnings = "\n".join(notes) + "\n", []
    if args.repository:
        output += render_stages(stage_timings)
    if workload_observation is not None:
        detail, workload_warnings = ci_job_performance.render_workload(
            workload_observation, workload_history
        )
        output += detail
        warnings.extend(workload_warnings)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "timings.json").write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "runId": args.run_id,
                "repository": args.repository,
                "snapshots": current,
                "stages": stage_timings,
                **({"workload": workload_observation.record()} if workload_observation else {}),
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
