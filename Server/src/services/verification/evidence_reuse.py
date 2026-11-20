"""Assess a retained Player observation against two operator-reviewed Git scopes."""

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from time import time_ns

from cli.utils.play_scenario_analysis_io import read_bytes, read_json, safe_local_path, write_new
from cli.utils.play_scenario_player import MANIFEST_LIMIT, PlayerBundle, PlayerLaunchError
from cli.utils.play_scenario_player_payload import PlayerPayloadError
from cli.utils.play_scenario_player_platform import (
    PlayerPlatformError,
    validate_runtime_environment,
)
from cli.utils.play_scenario_soak_environment import ENVIRONMENT_FIELDS, RecordedEnvironment
from cli.utils.verification_change import read_run, same_json
from services.verification.git_draft import _Git, _blob_ids, _resolve_commit, _snapshot
from services.verification.input_chunks import decode_object, source_revision, validate_input_paths
from services.verification.loader_observations import import_player_loader_capture
from services.verification.player_candidate import CANDIDATE_MANIFEST_LIMIT, parse_manifest

SCOPE_KEY = "verification_scope_sha256"
SECTIONS = ("source", "dependencies", "settings")
UNITY_TREES = ("Assets", "Packages", "ProjectSettings")


def _capture(project, revision, files, external, unresolved, environment):
    paths = []
    if not isinstance(files, dict) or set(files) != set(SECTIONS):
        raise ValueError("Expected source, dependencies and settings inventories")
    for field in SECTIONS:
        names = files[field]
        if not isinstance(names, (list, tuple)) or any(not isinstance(p, str) for p in names):
            raise ValueError("Invalid scope paths")
        paths.extend(names)
    if not files["source"] or len(paths) > 1024 or any(len(p) > 512 for p in paths):
        raise ValueError("Select 1 to 1024 admitted input paths including a source")
    validate_input_paths(paths)
    if external not in ("self_contained", "unknown"):
        raise ValueError("Invalid external input coverage")
    if (
        not isinstance(unresolved, (tuple, list))
        or len(unresolved) > 128
        or any(not isinstance(v, str) or not 0 < len(v) <= 256 for v in unresolved)
    ):
        raise ValueError("Invalid unresolved dependency reasons")
    if environment is not None:
        RecordedEnvironment.model_validate(environment).require_budget_support()
    git = _Git(safe_local_path(project))
    prefix = git.read("rev-parse", "--show-prefix", limit=4096).decode().removesuffix("\n")
    commit = _resolve_commit(git, revision, "scope")
    ids = _blob_ids(git, commit, sorted(paths))
    if set(ids) != set(paths):
        raise ValueError("A selected input is absent from the specified Git commit")
    with git.blobs(ids) as blobs:
        inputs, guids, config = _snapshot(blobs)
    trees = {}
    raw = git.read("--literal-pathspecs", "ls-tree", "-z", commit, "--", *UNITY_TREES)
    for record in raw.split(b"\0"):
        if record:
            header, name = record.split(b"\t", 1)
            mode, kind, oid = header.split()
            folder = name.decode("ascii")
            if folder not in UNITY_TREES or mode != b"040000" or kind != b"tree":
                raise ValueError("Expected ordinary Unity project directories")
            trees[folder] = oid.decode("ascii")
    return {
        "schema": "verification_reuse_scope@1",
        "git_commit": commit,
        "project_prefix": prefix,
        "unity_trees": trees,
        "asset_guids": guids,
        "configuration": config.registration(inputs),
        "inputs": {field: {p: inputs[p] for p in sorted(files[field])} for field in SECTIONS},
        "source_revision": source_revision(inputs),
        "runtime_inputs": external,
        "environment": environment,
        "unresolved": sorted(set(unresolved)),
    }


def capture_scope(project, revision, files, external, unresolved, output, environment=None):
    scope = _capture(project, revision, files, external, unresolved, environment)
    raw = (json.dumps(scope, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    write_new(output, raw)
    return {
        "source_revision": scope["source_revision"],
        "git_commit": scope["git_commit"],
        "observations": {SCOPE_KEY: sha256(raw).hexdigest()},
    }


def _registration(path):
    raw = read_bytes(path, CANDIDATE_MANIFEST_LIMIT)
    recorded = decode_object(raw).get("project")
    if not isinstance(recorded, str) or not (
        PurePosixPath(recorded).is_absolute() or PureWindowsPath(recorded).is_absolute()
    ):
        raise ValueError("Missing absolute project provenance in candidate registration")
    # The recorded path identifies the capture machine. Only archived chunks beside
    # this registration and Git objects from the caller's checkout are opened.
    return parse_manifest(raw, path.parent, Path(recorded)), sha256(raw).hexdigest()


def _checked_scope(project, candidate, path):
    expected = candidate.observations.get(SCOPE_KEY)
    if path is None or expected is None:
        return None
    raw = read_bytes(path, 2 * 1024 * 1024)
    if sha256(raw).hexdigest() != expected:
        raise ValueError("Scope digest differs from the candidate registration")
    scope = decode_object(raw)
    sections = scope.get("inputs")
    if (
        not isinstance(sections, dict)
        or set(sections) != set(SECTIONS)
        or any(not isinstance(v, dict) for v in sections.values())
        or not isinstance(scope.get("git_commit"), str)
        or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", scope["git_commit"])
    ):
        raise ValueError("Invalid registered scope")
    fresh = _capture(
        project,
        scope["git_commit"],
        {key: list(names) for key, names in sections.items()},
        scope.get("runtime_inputs"),
        scope.get("unresolved"),
        scope.get("environment"),
    )
    all_inputs = {p: h for group in sections.values() for p, h in group.items()}
    if (
        not same_json(fresh, scope)
        or all_inputs != candidate.inputs
        or any(fresh["asset_guids"].get(p) != guid for p, guid in candidate.assets.items())
        or (
            candidate.package_lock_evidence
            and fresh["configuration"].get("package_lock_evidence")
            != candidate.package_lock_evidence.model_dump()
        )
    ):
        raise ValueError("Registered inputs, Git objects and scope disagree")
    return scope


def _references_confirmed(stage, candidate):
    if not stage or stage.get("state") != "passed":
        return False
    data = stage.get("evidence")
    if not isinstance(data, dict) or data.get("coverage") != []:
        return False
    assets = data.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(candidate.assets.values()):
        return False
    for asset in assets.values():
        if not isinstance(asset, dict):
            return False
        coverage, findings = asset.get("coverage"), asset.get("findings")
        if (
            coverage != ["partial_static", "selected_sources_only"]
            or not isinstance(findings, list)
            or any(
                not isinstance(row, dict)
                or (row.get("kind"), row.get("state"))
                not in (
                    ("serialized_guid", "target_guid_retained"),
                    ("loader_key", "catalog_match"),
                )
                for row in findings
            )
        ):
            return False
    return True


def _runtime_confirmed(run, candidate):
    stage = run.loader_observations
    if not stage or stage.get("state") != "passed" or not run.native_loader_capture:
        return False
    keys = {
        ("resources", path.rsplit("/Resources/", 1)[1].rsplit(".", 1)[0])
        for path in candidate.assets
        if "/Resources/" in path
    }
    identity = {
        "job_id": run.job_id,
        "build_id": run.build_id,
        "payload_hash": run.payload,
        "build_source_revision": run.source,
    }
    native = {
        "job_id": run.job_id,
        "execution_environment": "player",
        "reproduction": identity,
        "loader_capture": run.native_loader_capture,
    }
    imported = import_player_loader_capture(native, expected=identity, expected_keys=keys)
    coverage = imported["coverage"]
    return (
        same_json(stage.get("evidence"), imported)
        and coverage["dropped"] == 0
        and not coverage["unobserved_keys"]
        and all(
            row["outcome"] == "loaded" and (row["scheme"], row["key"]) in keys
            for row in imported["observations"]
        )
    )


def assess_reuse(
    project,
    verification,
    native,
    archived,
    target,
    archive_scope,
    target_scope,
    bundle_path,
    *,
    max_age_seconds,
    closure=None,
):
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("A positive maximum evidence age in seconds is required")
    project = safe_local_path(project)
    run = read_run(verification, native)
    old, digest = _registration(archived)
    new, target_digest = _registration(target)
    if (digest, old.source_revision, old.build_id, old.payload_hash, old.scenario) != (
        run.manifest,
        run.source,
        run.build_id,
        run.payload,
        run.scenario,
    ):
        raise ValueError("The retained execution does not belong to the archived registration")
    old_scope = _checked_scope(project, old, archive_scope)
    new_scope = _checked_scope(project, new, target_scope)
    try:
        bundle = PlayerBundle.model_validate_json(read_bytes(bundle_path, MANIFEST_LIMIT))
    except (PlayerLaunchError, PlayerPayloadError) as error:
        raise ValueError(str(error)) from error
    if (
        bundle.build_id,
        bundle.payload_hash,
        bundle.build_source_revision,
        bundle.scenario_name,
    ) != (
        new.build_id,
        new.payload_hash,
        new.source_revision,
        new.scenario,
    ):
        raise ValueError("Target bundle and registration disagree")
    reasons = []
    changed = {}
    states = {check["state"] for check in run.checks.values()}
    expected_state = "failed" if "failed" in states else "passed"
    if not run.inputs_confirmed or states - {"passed", "failed"} or run.state != expected_state:
        reasons.append("incomplete_execution")
    if old.state_check:
        observation = run.checks.get(("observation", old.scenario), {})
        if not same_json(observation.get("expected_state"), old.state_check.expected):
            reasons.append("unconfirmed_state_observation")
    if not run.finished_at_unix_ms or not run.native_finished_unix_ms:
        reasons.append("missing_execution_time")
    else:
        now_ms = time_ns() // 1_000_000
        age = now_ms - run.native_finished_unix_ms
        if age < 0 or age > max_age_seconds * 1000 or run.finished_at_unix_ms > now_ms:
            reasons.append("execution_time_outside_window")
        if not run.timestamps_ordered:
            reasons.append("inconsistent_execution_time")
    if run.selection_gaps:
        reasons.append("outstanding_selection")
    if not _references_confirmed(run.reference_impact, old):
        reasons.append("unresolved_reference_impact")
    if not _runtime_confirmed(run, old):
        reasons.append("unconfirmed_runtime_dependencies")
    if not old_scope or not new_scope:
        reasons.append("unbound_dependency_scope")
    else:
        for scope in (old_scope, new_scope):
            if scope["runtime_inputs"] != "self_contained" or scope["unresolved"]:
                reasons.append("unresolved_dependencies")
                break
        observed_environment = {key: run.conditions[key] for key in ENVIRONMENT_FIELDS}
        for scope in (old_scope, new_scope):
            expected = scope["environment"]
            if expected is None or any(expected[k] != v for k, v in observed_environment.items()):
                reasons.append("unconfirmed_target_environment")
                break
        if any(set(scope["unity_trees"]) != set(UNITY_TREES) for scope in (old_scope, new_scope)):
            reasons.append("incomplete_project_trees")
        for field in SECTIONS:
            before, after = old_scope["inputs"][field], new_scope["inputs"][field]
            differences = sorted(
                p for p in before.keys() | after.keys() if before.get(p) != after.get(p)
            )
            if differences:
                changed[field] = differences
        roots = [
            k
            for k in UNITY_TREES
            if old_scope["unity_trees"].get(k) != new_scope["unity_trees"].get(k)
        ]
        if roots:
            changed["unity_trees"] = roots
        if changed:
            reasons.append("changed_project_inputs")
    if closure:
        graph = read_json(closure)
        if (
            not new_scope
            or graph.get("kind") != "verification_dependency_closure"
            or type(graph.get("schema_version")) is not int
            or graph["schema_version"] != 1
            or graph.get("candidate_commit") != new_scope["git_commit"]
            or graph.get("unresolved") != []
            or not isinstance(graph.get("input_hashes"), dict)
            or any(
                p not in new.inputs or new.inputs[p] != h for p, h in graph["input_hashes"].items()
            )
            or graph.get("expanded_source_revision") != new.source_revision
            or graph.get("candidate_source_revision")
            != source_revision(
                {p: h for p, h in new.inputs.items() if p not in graph["input_hashes"]}
            )
        ):
            reasons.append("unconfirmed_graph_dependencies")
    if (old.scenario, run.conditions["definition_hash"]) != (
        new.scenario,
        bundle.definition_hash,
    ) or not same_json(
        old.state_check.model_dump() if old.state_check else None,
        new.state_check.model_dump() if new.state_check else None,
    ):
        reasons.append("changed_test_definition")
    if (run.build_id, run.payload) != (new.build_id, new.payload_hash):
        reasons.append("changed_player_build")
    try:
        validate_runtime_environment(
            {"environment": run.conditions}, bundle.build_target, bundle.scripting_backend
        )
    except PlayerPlatformError:
        reasons.append("changed_player_platform")
    if (bundle.unity_version, bundle.package_version) != (
        run.conditions["unity_version"],
        run.conditions["package_version"],
    ):
        reasons.append("changed_build_environment")
    observations = [{k: v for k, v in c.observations.items() if k != SCOPE_KEY} for c in (old, new)]
    if not same_json(*observations):
        reasons.append("changed_registered_observations")
    if (
        old.assets != new.assets
        or old.mappings != new.mappings
        or old.broader_check != new.broader_check
    ):
        reasons.append("changed_registration_scope")
    return {
        "schema": "verification_evidence_reuse@1",
        "state": "new_execution_required" if reasons else "retained_observation",
        "native_executed": False,
        "original": {
            **run.identity(),
            "git_commit": old_scope["git_commit"] if old_scope else None,
            "manifest_sha256": digest,
            "verification_state": run.state,
            "verification_finished_at_unix_ms": run.finished_at_unix_ms,
            "native_finished_unix_ms": run.native_finished_unix_ms,
            "scenario_state": run.checks[("scenario", run.scenario)]["state"],
            "observed_conditions": run.conditions,
        },
        "target": {
            "git_commit": new_scope["git_commit"] if new_scope else None,
            "source_revision": new.source_revision,
            "manifest_sha256": target_digest,
        },
        "maximum_age_seconds": max_age_seconds,
        "changes": changed,
        "reasons": reasons,
    }
