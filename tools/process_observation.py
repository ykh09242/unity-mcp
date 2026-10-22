"""Validate streamed, identity-bound OS samples without retaining their history."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path

SCHEMA = "unity-mcp-owned-process-v1"


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Invalid process counter or identity")
    return value


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("Missing UTC observation time")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("Observation time must be UTC")
    return result


def summarize_process_observation(path):
    """Return checked scope/coverage; empty or lost identity is never zero memory."""
    header = footer = first = last = None
    count = 0
    previous_elapsed = -1
    supported = {"working_set": 0, "handle": 0}
    peaks = {"working_set": None, "handle": None}
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError("Empty process observation record")
            row = json.loads(line)
            if not isinstance(row, dict) or footer is not None:
                raise ValueError("Unexpected process observation record")
            kind = row.get("type")
            if header is None:
                if (
                    kind != "header"
                    or row.get("schema") != SCHEMA
                    or row.get("scope") != "one_owned_process_excluding_children"
                ):
                    raise ValueError("Missing process observation identity/scope")
                _integer(row.get("pid"), 1)
                _integer(row.get("creation_time_utc_ticks"), 1)
                _utc(row.get("started_utc"))
                header = row
                continue
            if kind == "summary":
                footer = row
                continue
            if kind != "sample" or row.get("index") != count or type(row.get("index")) is not int:
                raise ValueError("Process sample order differs")
            if any(
                row.get(key) != header[key] or type(row.get(key)) is not int
                for key in ("pid", "creation_time_utc_ticks")
            ):
                raise ValueError("Process sample identity differs")
            now = _utc(row.get("utc"))
            if now < _utc(header["started_utc"]) or (last is not None and now < _utc(last)):
                raise ValueError("Process sample time regressed")
            elapsed = row.get("elapsed_ms")
            if (
                type(elapsed) not in (int, float)
                or not math.isfinite(elapsed)
                or elapsed < previous_elapsed
                or elapsed < 0
            ):
                raise ValueError("Process sample elapsed time regressed")
            previous_elapsed = elapsed
            for counter, value_key, status_key in (
                ("working_set", "working_set_bytes", "working_set_status"),
                ("handle", "process_handle_count", "process_handle_status"),
            ):
                value, status = row.get(value_key), row.get(status_key)
                if status == "supported":
                    _integer(value, 1)
                    supported[counter] += 1
                    peaks[counter] = max(peaks[counter] or 0, value)
                elif status != "unavailable" or value is not None:
                    raise ValueError("Unknown counters must be unavailable/null")
            first = first or row["utc"]
            last = row["utc"]
            count += 1
    if header is None or footer is None or footer.get("schema") != SCHEMA:
        raise ValueError("Incomplete process observation")
    if {"pid", "creation_time_utc_ticks", "scope", "started_utc"}.intersection(footer):
        raise ValueError("Process summary cannot replace validated sample identity")
    if footer.get("reason") not in {"completed", "failed", "timeout", "controller_cleanup"}:
        raise ValueError("Missing controller completion reason")
    if footer.get("stop_reason") not in {
        None,
        "process_exited",
        "identity_unavailable",
        "identity_mismatch",
        "process_exited_or_identity_changed",
    }:
        raise ValueError("Unknown process observation stop reason")
    if _utc(footer.get("closed_utc")) < _utc(last or header["started_utc"]):
        raise ValueError("Process observation closed before its samples")
    expected = {
        "sample_count": count,
        "first_sample_utc": first,
        "last_sample_utc": last,
        "working_set_supported_samples": supported["working_set"],
        "handle_supported_samples": supported["handle"],
        "peak_working_set_bytes": peaks["working_set"],
        "peak_process_handle_count": peaks["handle"],
        "coverage": "sampled_window" if count else "empty",
    }
    if any(
        footer.get(key) != value or (type(value) is int and type(footer.get(key)) is not int)
        for key, value in expected.items()
    ):
        raise ValueError("Process observation summary disagrees with actual points")
    duration = footer.get("observation_duration_ms")
    if (
        type(duration) not in (int, float)
        or not math.isfinite(duration)
        or duration < max(0, previous_elapsed)
    ):
        raise ValueError("Invalid process observation window")
    return {
        "schema": SCHEMA,
        "pid": header["pid"],
        "creation_time_utc_ticks": header["creation_time_utc_ticks"],
        "scope": header["scope"],
        "started_utc": header["started_utc"],
        **footer,
        "complete_controller_run": footer["reason"] == "completed"
        and footer["stop_reason"] in {None, "process_exited"}
        and count > 0,
        "meaning": "Samples inside this process/window only; excludes children and does not prove leak absence.",
    }


def read_owned_process_run(directory, *, inputs_sha256, nunit_sha256):
    """Bind the OS window to the successful native invocation and frozen inputs."""
    import hashlib

    directory = Path(directory)

    def file_hash(name):
        with (directory / name).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    invocation = json.loads((directory / "invocation.json").read_text(encoding="utf-8-sig"))
    result = json.loads((directory / "process-result.json").read_text(encoding="utf-8-sig"))
    if (
        not invocation.get("runId")
        or result.get("runId") != invocation["runId"]
        or invocation.get("cacheReused") is not True
    ):
        raise ValueError("Process observation invocation differs")
    if (
        type(result.get("exitCode")) is not int
        or result["exitCode"] != 0
        or result.get("timeout") is not False
        or result.get("ownedSurvivors") != 0
        or result.get("protectedPreserved") is not True
        or result.get("processObservationError") is not None
    ):
        raise ValueError("Native process did not finish with observed cleanup")
    if file_hash("inputs.json") != inputs_sha256 or file_hash("results.xml") != nunit_sha256:
        raise ValueError("Process observation source/result binding differs")
    summary = summarize_process_observation(directory / "process-observation.jsonl")
    if (
        invocation.get("pid") != summary["pid"]
        or invocation.get("creationTimeUtcTicks") != summary["creation_time_utc_ticks"]
    ):
        raise ValueError("Process observation PID or creation time differs")
    if not summary["complete_controller_run"]:
        raise ValueError("Process observation has no complete sampled window")
    return {
        "run_id": invocation["runId"],
        "observation": summary,
        "evidence_sha256": {
            name: file_hash(name)
            for name in (
                "invocation.json",
                "process-result.json",
                "process-observation.jsonl",
                "inputs.json",
                "results.xml",
            )
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize_process_observation(args.path), indent=2))


if __name__ == "__main__":
    main()
