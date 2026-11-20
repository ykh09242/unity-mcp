"""Measure the streamed NUnit reader against a retained baseline implementation."""

import argparse
import json
import sys
import tracemalloc
from collections.abc import Callable
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from statistics import median
from time import perf_counter

from check_offline_evidence import _write_capture
from cli.utils.verification_selection import read_cohort

_LIMIT = 16 * 1024 * 1024


def _legacy_reader(source: Path) -> Callable:
    spec = spec_from_file_location("nunit_reader_before_streaming", source)
    if spec is None or spec.loader is None:
        raise ValueError("Expected the previous NUnit reader as a Python source file")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.read_cohort


def _classification(reader: Callable, path: Path) -> dict:
    try:
        cohort = reader(path)
    except UnicodeDecodeError:
        return {"state": "incomplete", "error": "unreadable UTF-8"}
    except (OSError, ValueError) as error:
        return {"state": "incomplete", "error": str(error)}
    fixture = cohort.fixture
    return {
        "state": "read",
        "digest": cohort.digest,
        "seconds": cohort.seconds,
        "cases": list(cohort.cases.items()),
        "fixture": {
            "execution_id": fixture.execution_id,
            "context": fixture.context,
            "expected_cases": sorted(fixture.expected_cases),
            "inputs": fixture.inputs,
        },
    }


def _measure(reader: Callable, path: Path) -> dict:
    samples = []
    for _ in range(5):
        start = perf_counter()
        reader(path)
        samples.append(perf_counter() - start)
    tracemalloc.start()
    try:
        reader(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "median_seconds": median(samples),
        "samples_seconds": samples,
        "traced_peak_bytes": peak,
    }


def _examples(folder: Path) -> dict[str, Path]:
    small = _write_capture(folder / "small.xml", 1)
    xml = small.read_bytes()
    fixtures = {"small": small}
    # Extra Unity diagnostics count against the byte limit but do not contribute
    # case evidence. Create many sibling nodes so the benchmark measures retained
    # tree memory, including UTF-8 data and text crossing reads.
    diagnostic = (
        b"<property name='diagnostic'>" + ("Hello, Unity ユニティ\n" * 40).encode() + b"</property>"
    )
    padded = xml.replace(b"<test-case", diagnostic * 12_000 + b"<test-case", 1)
    exact = xml + b"<!--" + b"x" * (_LIMIT - len(xml) - 7) + b"-->"
    split_marker = b"<!--" + b"x" * (64 * 1024 - 7) + b"<!DOCTYPE-->" + xml
    for name, data in {
        "large": b"\xef\xbb\xbf" + padded,
        "truncated": padded[:-6],
        "invalid_utf8": xml + b"\xff",
        "split_marker": split_marker,
        "at_limit": exact,
        "over_limit": exact + b" ",
    }.items():
        fixtures[name] = folder / f"{name}.xml"
        fixtures[name].write_bytes(data)
    fixtures["linked_path"] = folder / "linked.xml"
    fixtures["linked_path"].symlink_to(small)
    return fixtures


def benchmark(legacy_source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    fixtures = output / "nunit-fixtures"
    fixtures.mkdir()
    legacy = _legacy_reader(legacy_source)
    examples = _examples(fixtures)
    equivalent = {}
    for name, path in examples.items():
        before, after = _classification(legacy, path), _classification(read_cohort, path)
        expected_state = "read" if name in {"small", "large", "at_limit"} else "incomplete"
        if after != before or after["state"] != expected_state:
            raise RuntimeError(f"NUnit parity failed for {name}: {before} != {after}")
        equivalent[name] = {"bytes": path.lstat().st_size, "outcome": after}
    evidence = {
        "parity_examples": equivalent,
        "legacy": _measure(legacy, examples["large"]),
        "streaming": _measure(read_cohort, examples["large"]),
    }
    (output / "nunit-reader.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if evidence["streaming"]["traced_peak_bytes"] >= evidence["legacy"]["traced_peak_bytes"]:
        raise RuntimeError(
            "Streaming did not reduce traced peak memory for the retained NUnit fixture"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark(args.legacy_source, args.output.resolve())


if __name__ == "__main__":
    main()
