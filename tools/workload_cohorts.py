"""Capture and compare existing optional probes. Never dispatch Unity or change product limits."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/large_workload_cohorts.json"
HARNESS = (
    "tools/workload_cohorts.py",
    "tools/experiments/service_optimizations/shared_read.py",
    "tools/experiments/service_optimizations/ServiceOptimizationProbes.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Helpers/WorkloadProbe.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Helpers/WorkloadObservations.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/BatchTargetPreviewTests.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/ManageSceneHierarchyPagingTests.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/ReferenceDiagnosticsTests.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/BuildReportPersistenceTests.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Windows/OperationActivityTests.cs",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/ScriptChangeSetReloadTests.cs",
)
ENVIRONMENT = {"editor", "backend", "os", "cpu", "processors", "memory_mb", "gpu"}
SHA = re.compile(r"[0-9a-f]{64}\Z")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def harness_fingerprint():
    return hashlib.sha256("".join(p + digest(ROOT / p) for p in HARNESS).encode()).hexdigest()


def server_fingerprint():
    paths = sorted((ROOT / "Server/src").rglob("*.py"))
    return hashlib.sha256(
        "".join(str(p.relative_to(ROOT)) + digest(p) for p in paths).encode()
    ).hexdigest()


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def number(value, *, positive=False, signed=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Expected finite numeric measurement")
    if not signed and (value < 0 or (positive and value == 0)):
        raise ValueError("Invalid measurement range")
    return value


def validate_memory_point(point):
    if not isinstance(point, dict) or not re.fullmatch(r"[0-9a-f]{32}", point.get("domain", "")):
        raise ValueError("Missing memory observation domain")
    number(point.get("post_idle_managed_bytes"))
    owners = point.get("owner_counts")
    if not isinstance(owners, dict) or not all(
        isinstance(k, str) and type(v) is int and v >= 0 for k, v in owners.items()
    ):
        raise ValueError("Invalid owner counters")
    for key in ("working_set_bytes", "process_handle_count"):
        if point.get(key) is not None:
            number(point[key])


def validate_observations(row):
    if (
        row.get("observation_schema") != "editor-update-retention-v1"
        or row.get("observer_released") is not True
    ):
        raise ValueError("Unknown or unreleased observation owner")
    baseline = row.get("baseline")
    validate_memory_point(baseline)
    points = row.get("observations")
    if not isinstance(points, list) or len(points) != len(row["samples"]):
        raise ValueError("Missing repeated run/idle observations")
    for i, point in enumerate(points):
        validate_memory_point(point)
        if (
            type(point.get("run_index")) is not int
            or point["run_index"] != i
            or point["domain"] != baseline["domain"]
        ):
            raise ValueError("Observation order or domain differs")
        if type(point.get("editor_update_count")) is not int or point["editor_update_count"] < 1:
            raise ValueError("No real Editor update observed")
        number(point.get("editor_update_gap_ms"), positive=True)
        peak = number(point.get("sampled_peak_managed_bytes"))
        after = number(point.get("after_work_managed_bytes"))
        if peak < max(after, point["post_idle_managed_bytes"]):
            raise ValueError("Sampled peak is below its observed points")
        if set(point["owner_counts"]) != set(baseline["owner_counts"]):
            raise ValueError("Owner counter coverage changed")
    if row.get("main_thread_stall_ms") != max(p["editor_update_gap_ms"] for p in points):
        raise ValueError("Update-gap summary does not match observed intervals")
    if (
        row.get("retained_managed_delta_bytes")
        != points[-1]["post_idle_managed_bytes"] - baseline["post_idle_managed_bytes"]
    ):
        raise ValueError("Retained delta disagrees with observation series")


def retention_summary(points, baseline=None):
    retained = [point["post_idle_managed_bytes"] for point in points]
    tail = retained[-3:]
    band = max(1_048_576, statistics.median(tail) * 0.01)
    owners = baseline["owner_counts"] if baseline else points[0]["owner_counts"]
    return {
        "post_idle_managed_bytes": retained,
        "sampled_peak_managed_bytes": max(
            (p.get("sampled_peak_managed_bytes", 0) for p in points), default=None
        )
        or None,
        "process_handle_counts": [p.get("process_handle_count") for p in points],
        "working_set_bytes": [p.get("working_set_bytes") for p in points],
        "owner_counts": [p["owner_counts"] for p in points],
        "owner_counts_unchanged": all(p["owner_counts"] == owners for p in points),
        "plateau": "within_observed_band"
        if len(tail) == 3 and max(tail) - min(tail) <= band
        else "not_established",
        "advisory_band_bytes": band,
        "meaning": "Last three observed post-idle samples only; not proof of leak absence.",
    }


def validate_raw(row, manifest):
    if row.get("schema") != "unity-mcp-workload-raw-v1" or row.get("completed") is not True:
        raise ValueError("Workload did not complete")
    scenario = manifest["scenarios"].get(row.get("scenario"))
    expected = scenario and scenario["variants"].get(row.get("variant"))
    if (
        expected is None
        or type(row.get("expected_units")) is not int
        or row["expected_units"] != expected
    ):
        raise ValueError("Workload identity or size differs from the manifest")
    env = row.get("environment", {})
    if set(env) != ENVIRONMENT or any(value is None or value == "" for value in env.values()):
        raise ValueError("Missing complete execution environment")
    number(env["processors"], positive=True)
    number(env["memory_mb"], positive=True)
    if row["scenario"] == "repeated_reload":
        if (
            row.get("verified_reloads") != expected
            or len(row.get("evidence_sha256", [])) != expected
        ):
            raise ValueError("Missing actual reload evidence")
        if len(set(row["evidence_sha256"])) != expected or not all(
            SHA.fullmatch(s) for s in row["evidence_sha256"]
        ):
            raise ValueError("Reload evidence is duplicated or invalid")
        if row.get("samples") != []:
            raise ValueError("Reload XML is correctness evidence, not latency")
        if "reload_observations" in row:
            points = row["reload_observations"]
            if len(points) != expected:
                raise ValueError("Missing repeated reload memory observations")
            for pair in points:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Incomplete reload observation pair")
                for point in pair:
                    validate_memory_point(point)
                if [p.get("phase") for p in pair] != [
                    "before_reload",
                    "after_reload_cleanup",
                ] or pair[0]["domain"] == pair[1]["domain"]:
                    raise ValueError("Reload observation did not cross a real domain")
        return
    samples = row.get("samples", [])
    if sum(s.get("preparation") == "warm" for s in samples) < 3:
        raise ValueError("At least three warm samples are required")
    hashes = set()
    for sample in samples:
        if sample.get("preparation") not in {"first_call_warm_library", "warm"}:
            raise ValueError("Unknown preparation; warm Library is not a cold machine")
        if type(sample.get("completed_units")) is not int or sample["completed_units"] != expected:
            raise ValueError("Partial or refused large work cannot be faster")
        if not isinstance(sample.get("output_sha256"), str) or not SHA.fullmatch(
            sample["output_sha256"]
        ):
            raise ValueError("Missing ordered output evidence")
        hashes.add(sample["output_sha256"])
        number(sample.get("elapsed_ms"), positive=True)
        if sample.get("main_thread_work_ms") is not None:
            number(sample["main_thread_work_ms"])
    if len(hashes) != 1:
        raise ValueError("Sample output or ordering changed")
    for key in ("allocation_bytes", "traced_peak_bytes", "main_thread_stall_ms"):
        if row.get(key) is not None:
            number(row[key])
    if row.get("retained_managed_delta_bytes") is not None:
        number(row["retained_managed_delta_bytes"], signed=True)
    if any(
        key in row
        for key in ("observation_schema", "observations", "baseline", "observer_released")
    ):
        validate_observations(row)


def summarize(row):
    result = {"scenario": row["scenario"], "variant": row["variant"]}
    for preparation in ("first_call_warm_library", "warm"):
        samples = [s for s in row["samples"] if s["preparation"] == preparation]
        if not samples:
            continue
        times = sorted(s["elapsed_ms"] for s in samples)
        result[preparation] = {
            "count": len(times),
            "p50_ms": statistics.median(times),
            "p95_ms": times[math.ceil(0.95 * len(times)) - 1],
            "throughput_units_per_second": sum(s["completed_units"] for s in samples)
            * 1000
            / sum(times),
        }
    for key in (
        "allocation_bytes",
        "traced_peak_bytes",
        "retained_managed_delta_bytes",
        "main_thread_stall_ms",
    ):
        result[key] = row.get(key)
    work = [
        s["main_thread_work_ms"] for s in row["samples"] if s.get("main_thread_work_ms") is not None
    ]
    result["max_main_thread_work_ms"] = max(work, default=None)
    result["retention"] = (
        retention_summary(row["observations"], row["baseline"]) if "observations" in row else None
    )
    if row.get("reload_observations"):
        result["reload_retention"] = retention_summary(
            [pair[-1] for pair in row["reload_observations"]]
        )
    return result


def compare(before, after):
    manifest = load_manifest()
    for report in (before, after):
        if report.get("schema") != "unity-mcp-workload-capture-v1":
            raise ValueError("Unknown capture schema")
        if not report.get("cases"):
            raise ValueError("Empty captures cannot establish performance")
        for row in report["cases"]:
            validate_raw(row, manifest)
    if any(before.get(k) != after.get(k) for k in ("manifest_sha256", "harness_sha256")):
        return {"status": "not_comparable", "reason": "Workload or measurement harness differs"}

    def keyed(report):
        rows = {(r["scenario"], r["variant"]): r for r in report["cases"]}
        if len(rows) != len(report["cases"]):
            raise ValueError("Duplicate workload cases")
        return rows

    old, new = keyed(before), keyed(after)
    if old.keys() != new.keys():
        return {"status": "not_comparable", "reason": "Selected cases differ"}
    comparisons = []
    for key, current in new.items():
        previous = old[key]
        if previous["environment"] != current["environment"]:
            return {
                "status": "not_comparable",
                "reason": "Execution environments differ",
                "case": key,
            }
        if current["scenario"] == "repeated_reload":
            continue
        if previous["samples"][0]["output_sha256"] != current["samples"][0]["output_sha256"]:
            return {"status": "not_comparable", "reason": "Verified outputs differ", "case": key}
        a, b = summarize(previous)["warm"], summarize(current)["warm"]
        ratio = b["p50_ms"] / a["p50_ms"]
        # Noisy timings are advisory. Both relative and absolute change must be meaningful.
        regression = ratio > 1.2 and b["p50_ms"] - a["p50_ms"] > 5
        comparisons.append(
            {
                "scenario": key[0],
                "variant": key[1],
                "ratio": ratio,
                "advisory_regression": regression,
            }
        )
    return {
        "status": "advisory_regression"
        if any(r["advisory_regression"] for r in comparisons)
        else "comparable",
        "cases": comparisons,
    }


def source_provenance(entry):
    """Keep staged bytes exact; allow only Git's CRLF checkout conversion for text."""
    source = Path(entry.get("source", ""))
    if not source.is_file() or not source.resolve().is_relative_to(ROOT):
        return "prepared_overlay"
    if digest(source) == entry["sha256"]:
        return "exact"
    if source.suffix.lower() in {".cs", ".meta", ".json", ".uxml", ".uss", ".asmdef", ".asmref"}:
        original, staged = source.read_bytes(), Path(entry["path"]).read_bytes()
        if original.replace(b"\r\n", b"\n") == staged.replace(b"\r\n", b"\n"):
            return "crlf_only"
    raise ValueError("Native staged source differs from the selected repository")


def capture(
    paths,
    *,
    inputs=None,
    nunit=None,
    reload_runs=(),
    reload_results_name="results.xml",
    allow_partial=False,
):
    if reload_results_name not in {"results.xml", "lifecycle-results.xml"}:
        raise ValueError("Unsupported reload result filename")
    manifest = load_manifest()
    provenance = {"exact": 0, "crlf_only": [], "prepared_overlays": []}
    rows, evidence = [], []
    for path in paths:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        rows.extend(value if isinstance(value, list) else [value])
        evidence.append({"name": Path(path).name, "sha256": digest(path)})
    native = [r for r in rows if r.get("scenario") != "concurrent_readers"]
    input_hash = None
    if native:
        if inputs is None or not nunit:
            raise ValueError("Native capture requires verified source inputs and NUnit evidence")
        frozen = json.loads(Path(inputs).read_text(encoding="utf-8-sig"))
        if not frozen.get("files") or len({f["path"] for f in frozen["files"]}) != len(
            frozen["files"]
        ):
            raise ValueError("Native input inventory is empty or duplicated")
        for entry in frozen["files"]:
            if digest(entry["path"]) != entry["sha256"]:
                raise ValueError("Native inputs changed after measurement")
            kind = source_provenance(entry)
            if kind == "exact":
                provenance["exact"] += 1
            else:
                provenance["crlf_only" if kind == "crlf_only" else "prepared_overlays"].append(
                    {
                        "path": entry["path"],
                        "source": entry.get("source"),
                        "sha256": entry["sha256"],
                    }
                )
        input_hash = digest(inputs)
        if any(r.get("inputs_sha256") != input_hash for r in native):
            raise ValueError("Native raw samples are bound to different inputs")
        from tools.check_unity_test_results import check_results

        required = []
        for scenario in {r["scenario"] for r in native}:
            required.extend(manifest["scenarios"][scenario]["producer"])
        # One ordinary grouped run, or explicitly selected native cases only.
        errors = check_results(Path(nunit), "success", required)
        if errors:
            raise ValueError("Native required inventory failed: " + str(errors))
        evidence.append({"name": Path(nunit).name, "sha256": digest(nunit)})
    for row in rows:
        if row.get("scenario") == "concurrent_readers" and (
            row.get("server_sha256") != server_fingerprint()
            or row.get("harness_sha256") != harness_fingerprint()
        ):
            raise ValueError("Python probe source changed after measurement")
        validate_raw(row, manifest)
    if reload_runs:
        if not native or len(reload_runs) != 3:
            raise ValueError("Reload capture requires three runs and a native environment cohort")
        required = manifest["scenarios"]["repeated_reload"]["producer"]
        identities, hashes, projects = set(), [], set()
        reload_observations = []
        expected_files = {f["path"]: f["sha256"] for f in frozen["files"]}
        for directory in reload_runs:
            directory = Path(directory)
            invocation = json.loads((directory / "invocation.json").read_text(encoding="utf-8-sig"))
            process = json.loads(
                (directory / "process-result.json").read_text(encoding="utf-8-sig")
            )
            observed = json.loads((directory / "inputs.json").read_text(encoding="utf-8-sig"))
            if {f["path"]: f["sha256"] for f in observed["files"]} != expected_files:
                raise ValueError("Reload source inputs differ from the native cohort")
            if invocation.get("filters") != required or invocation.get("cacheReused") is not True:
                raise ValueError("Reload run must use the actual recovery fixture and reused cache")
            if (
                process.get("exitCode") != 0
                or process.get("timeout") is not False
                or process.get("ownedSurvivors") != 0
                or process.get("protectedPreserved") is not True
            ):
                raise ValueError("Reload did not finish with owned cleanup")
            if process.get("runId") != invocation.get("runId") or not invocation.get("runId"):
                raise ValueError("Reload invocation identity differs")
            if check_results(directory / reload_results_name, "success", required):
                raise ValueError("Actual reload verification failed")
            observation_path = directory / "workload/reload-retention.json"
            if observation_path.exists():
                pair = json.loads(observation_path.read_text(encoding="utf-8"))
                if not isinstance(pair, list) or any(
                    p.get("inputs_sha256") != digest(directory / "inputs.json") for p in pair
                ):
                    raise ValueError("Reload observations are bound to different inputs")
                reload_observations.append(pair)
                evidence.append(
                    {
                        "name": directory.name + "/workload/reload-retention.json",
                        "sha256": digest(observation_path),
                    }
                )
            identities.add(invocation["runId"])
            projects.add(invocation["project"])
            hashes.append(digest(directory / reload_results_name))
            evidence.append(
                {
                    "name": directory.name,
                    "invocation_sha256": digest(directory / "invocation.json"),
                    "nunit_sha256": hashes[-1],
                    "nunit_name": reload_results_name,
                    "inputs_sha256": digest(directory / "inputs.json"),
                }
            )
        if len(identities) != 3 or len(projects) != 1:
            raise ValueError("Reloads must be distinct invocations of one isolated project")
        reload = {
            "schema": "unity-mcp-workload-raw-v1",
            "scenario": "repeated_reload",
            "variant": "actual_domain",
            "expected_units": 3,
            "verified_reloads": 3,
            "completed": True,
            "environment": native[0]["environment"],
            "evidence_sha256": hashes,
            "samples": [],
        }
        if reload_observations:
            reload["reload_observations"] = reload_observations
        validate_raw(reload, manifest)
        rows.append(reload)
    present = {(r["scenario"], r["variant"]) for r in rows}
    expected = {(s, v) for s, info in manifest["scenarios"].items() for v in info["variants"]}
    if len(present) != len(rows) or (not allow_partial and present != expected):
        raise ValueError(
            "Missing or duplicate cohort variants; use --allow-partial for an explicitly partial capture"
        )

    def git(*args):
        return subprocess.check_output(["git", "-C", str(ROOT), *args])

    source = {
        "commit": git("rev-parse", "HEAD").decode("ascii").strip(),
        "tracked_diff_sha256": hashlib.sha256(
            git("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--binary")
        ).hexdigest(),
    }
    harness = harness_fingerprint()
    return {
        "schema": "unity-mcp-workload-capture-v1",
        "source": source,
        "manifest_sha256": digest(MANIFEST),
        "harness_sha256": harness,
        "native_inputs_sha256": input_hash,
        "native_source_provenance": provenance,
        "evidence": evidence,
        "partial": present != expected,
        "cases": rows,
        "summaries": [summarize(r) for r in rows],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("capture")
    record.add_argument("files", type=Path, nargs="+")
    record.add_argument("--inputs", type=Path)
    record.add_argument("--nunit", type=Path)
    record.add_argument("--allow-partial", action="store_true")
    record.add_argument("--reload-run", action="append", type=Path, default=[])
    record.add_argument(
        "--reload-results-name",
        choices=("results.xml", "lifecycle-results.xml"),
        default="results.xml",
    )
    record.add_argument("--output", type=Path, required=True)
    diff = sub.add_parser("compare")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        result = capture(
            args.files,
            inputs=args.inputs,
            nunit=args.nunit,
            reload_runs=args.reload_run,
            reload_results_name=args.reload_results_name,
            allow_partial=args.allow_partial,
        )
    else:
        result = compare(
            json.loads(args.before.read_text(encoding="utf-8")),
            json.loads(args.after.read_text(encoding="utf-8")),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    main()
