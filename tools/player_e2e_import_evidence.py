"""Reconcile real concurrent importer receipts against independent native evidence."""

from pathlib import Path
from xml.etree import ElementTree

from player_e2e_artifacts import read_json
from player_e2e_imports import FIXTURE
from player_e2e_reports import require


def check_import_case(directory: Path) -> None:
    """Reject stale, post-exit-only, incomplete or unsuccessful Editor observations."""
    reader = directory / "editor-import"
    config = read_json(reader / "config.json", 16384)
    ready = read_json(reader / "ready.json", 16384)
    finished = read_json(reader / "finished.json", 16384)
    evidence = read_json(reader / "receipt.json", 131072)
    editor = read_json(reader / "launch.json", 65536)
    cli = read_json(directory / "launch.json")
    roots = list((directory / "cli-output").iterdir())
    require(len(roots) == 1 and roots[0].is_dir(), "Importer requires one native output root")
    native = read_json(roots[0] / "process.json", 65536)
    summary = read_json(roots[0] / "soak.json", 65536)
    require(
        config.get("schema_version") == evidence.get("schema_version") == 1
        and isinstance(config.get("nonce"), str)
        and len(config["nonce"]) == 32
        and all(value.get("nonce") == config["nonce"] for value in (ready, finished, evidence)),
        "Importer markers belong to another invocation",
    )
    require(
        editor["process_ended"] is True
        and editor["actual_exit_code"] == 0
        and editor["forced_termination"] is False
        and not editor["log_truncated"]
        and not editor["progress_read_errors"]
        and editor["process_id"] == ready["editor_process_id"] == evidence["editor_process_id"]
        and native["process_id"] != editor["process_id"],
        "Concurrent reader lacks a naturally completed distinct Editor process",
    )
    require(
        editor["started_unix_ms"]
        <= ready["ready_unix_ms"]
        <= cli["started_unix_ms"]
        <= native["started_unix_ms"]
        <= native["finished_unix_ms"]
        <= cli["finished_unix_ms"]
        <= finished["finished_unix_ms"]
        <= evidence["completed_unix_ms"]
        <= editor["finished_unix_ms"],
        "Editor import lifetime does not cover the owned Player lifetime",
    )
    xml = reader / "results.xml"
    require(xml.stat().st_size <= 2 * 1024 * 1024, "Importer NUnit evidence exceeds its bound")
    nunit = ElementTree.parse(xml).getroot()
    tests = nunit.findall(".//test-case")
    require(
        nunit.attrib.get("result") == "Passed"
        and len(tests) == 1
        and tests[0].attrib.get("fullname") == FIXTURE
        and tests[0].attrib.get("result") == "Passed",
        "Concurrent importer lacks its one actually passed NUnit case",
    )
    require(
        evidence["background_reads"] is True
        and evidence["main_thread_delivery"] is True
        and evidence["worker_disposed"] is True
        and evidence["pump_released"] is True
        and 10 <= evidence["successful_imports"] <= evidence["read_attempts"] <= 30000
        and evidence["case"] == directory.name
        and evidence["final_summary"] == summary
        and evidence["final_complete"] is True,
        "Importer lacks bounded background reads, main-thread delivery or verified final state",
    )
    observations = evidence["observations"]
    require(
        isinstance(observations, list) and 3 <= len(observations) <= 1001,
        "Importer lacks repeated journal prefixes",
    )
    prefixes = set()
    previous_end = 0
    previous_count = -1
    for observed in observations:
        require(
            observed["process_id"] == native["process_id"]
            and observed["job_id"] == summary["job_id"]
            and type(observed["rows"]) is int
            and previous_count < observed["rows"] <= summary["journal_rows"]
            and previous_end
            <= observed["started_unix_ms"]
            <= observed["finished_unix_ms"]
            <= evidence["completed_unix_ms"],
            "Importer prefix identity, ordering or interval differs",
        )
        previous_count = observed["rows"]
        previous_end = observed["finished_unix_ms"]
        if (
            native["started_unix_ms"] <= observed["started_unix_ms"]
            and observed["finished_unix_ms"] < native["finished_unix_ms"]
            and 0 < observed["rows"] < summary["journal_rows"]
        ):
            prefixes.add(observed["rows"])
    require(len(prefixes) >= 3, "Imports only observed the final file, not the live Player writer")
