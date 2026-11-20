"""Reconcile the registered static impact with two native Player loader captures."""

from collections import defaultdict
from hashlib import sha256

from cli.utils.play_scenario_analysis_io import read_bytes, read_json, safe_local_path
from cli.utils.play_scenario_player import PlayerFinalReport
from cli.utils.play_scenario_soak_reports import _ProcessReceipt
from models.play_scenarios import PlayScenario, normalize_native_definition
from services.verification.input_chunks import decode_object
from services.verification.loader_observations import import_player_loader_capture
from services.verification.player_candidate import (
    CANDIDATE_MANIFEST_LIMIT,
    PreparedCandidate,
    parse_manifest,
)
from services.verification.player_runner import _reference_evidence, _resource_key


def _capture(directory, candidate):
    request_raw = read_bytes(directory / "request.json", 64 * 1024)
    request = decode_object(request_raw)
    raw = read_json(directory / "run.json")
    report = PlayerFinalReport.model_validate(raw)
    definition = (
        read_bytes(directory / "definition.json", 1024 * 1024)
        .removesuffix(b"\n")
        .removesuffix(b"\r")
    )
    scenario = PlayScenario.model_validate(normalize_native_definition(decode_object(definition)))
    process = _ProcessReceipt.model_validate(read_json(directory / "process.json"))
    diagnostics = read_json(directory / "diagnostics.json")
    identity = dict(
        job_id=directory.name,
        build_id=candidate.build_id,
        payload_hash=candidate.payload_hash,
        build_source_revision=candidate.source_revision,
    )
    if (
        process.request_sha256 != sha256(request_raw).hexdigest()
        or any(request.get(k) != value for k, value in identity.items())
        or process.job_id != directory.name
        or diagnostics.get("job_id") != directory.name
        or diagnostics.get("process_id") != process.process_id
        or diagnostics.get("process_exit_code") != process.actual_exit_code
        or not process.process_ended
        or process.forced_termination
        or process.actual_exit_code != report.exit_code
        or report.status != "succeeded"
        or report.exit_code != 0
        or request.get("repeat_count") != report.repeat_count
        or request.get("timeout_seconds") != report.timeout_seconds
        or diagnostics.get("cleanup_state") != "native_finalized"
        or diagnostics.get("payload_verification") != "launcher_admission"
        or report.reproduction.get("payload_verification") != "launcher_admission"
        or report.player_schema_version != 2
        or request.get("schema_version") != 2
        or report.report_error
        or report.progress_error
        or request.get("scenario_name") != candidate.scenario
        or report.scenario.name != candidate.scenario
        or scenario != report.scenario
        or request.get("definition_hash") != sha256(definition).hexdigest()
        or request["definition_hash"] != report.reproduction.get("definition_hash")
    ):
        raise ValueError("Player job, launch evidence and registered candidate disagree")
    conditions = (request["definition_hash"], report.repeat_count, report.timeout_seconds)
    return import_player_loader_capture(raw, expected=identity), conditions


def _predictions(registration, evidence):
    analyzed = set(evidence["analyzed_scripts"])
    catalog = defaultdict(list)
    for path, guid in registration.assets.items():
        key = _resource_key(path)
        if key:
            catalog[("resources", key)].append((guid, path))
    predictions = set()
    comparable = set()
    for guid, asset in evidence["assets"].items():
        covered = set(asset.get("coverage", []))
        if not evidence["coverage"] and "finding_limit" not in covered:
            comparable.add(guid)
        for row in asset.get("findings", []):
            if row.get("kind") == "loader_key" and row.get("state") == "catalog_match":
                predictions.add((row["scheme"], row["key"], row["path"]))
    return catalog, analyzed, predictions, comparable


def _summarize(imported, static):
    catalog, analyzed, predictions, comparable = static
    events = []
    observed = set()
    for row in imported["observations"]:
        edge = (row["scheme"], row["key"], row["owner_path"])
        matches = catalog[edge[:2]]
        guid, asset_path = matches[0] if len(matches) == 1 else (None, None)
        if row["outcome"] != "loaded":
            state = row["outcome"]
        else:
            observed.add(edge)
            if edge in predictions:
                state = "predicted_load"
            elif guid in comparable and edge[2] in analyzed:
                state = "observed_outside_prediction"
            else:
                state = "static_coverage_unknown"
        events.append({**row, "registered_asset_path": asset_path, "comparison": state})
    return {
        "identity": imported["identity"],
        "capture_sha256": imported["capture_sha256"],
        "coverage": imported["coverage"],
        "events": events,
        "unobserved_predictions": [
            {"scheme": scheme, "key": key, "owner_path": path}
            for scheme, key, path in sorted(predictions - observed)
        ],
    }, observed


def compare_player_loads(project, registration_path, before, after):
    project = safe_local_path(project)
    raw = read_bytes(registration_path, CANDIDATE_MANIFEST_LIMIT)
    directory = registration_path.parent
    candidate = parse_manifest(raw, directory, project)
    digest = sha256(raw).hexdigest()
    if before.name == after.name:
        raise ValueError("Select two independent Player executions")
    first_capture, first_conditions = _capture(before, candidate)
    second_capture, second_conditions = _capture(after, candidate)
    if first_conditions != second_conditions:
        raise ValueError(
            "Player scenario definition, repetition or timeout differs between executions"
        )
    prepared = PreparedCandidate(
        directory.parent, project, directory.name, None, candidate, None, digest, None, {}
    )
    evidence = _reference_evidence(prepared, include_script_paths=True)
    static = _predictions(candidate, evidence)
    first, first_seen = _summarize(first_capture, static)
    second, _ = _summarize(second_capture, static)
    comparable_captures = all(
        capture["coverage"]["state"] == "instrumented_hooks_only"
        and capture["coverage"]["dropped"] == 0
        for capture in (first_capture, second_capture)
    )
    return {
        "schema": "player_loader_comparison@1",
        "scope": "selected_static_sources_and_instrumented_hooks",
        "build_id": candidate.build_id,
        "manifest_sha256": digest,
        "static_impact": evidence,
        "before": first,
        "after": second,
        "newly_observed_loads": [
            row
            for row in second["events"]
            if row["outcome"] == "loaded"
            and (row["scheme"], row["key"], row["owner_path"]) not in first_seen
        ]
        if comparable_captures
        else None,
    }
