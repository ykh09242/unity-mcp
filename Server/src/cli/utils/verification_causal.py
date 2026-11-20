"""Admit retained candidate, verification and native evidence for controlled trials."""

import re
from hashlib import sha256
from pathlib import Path

from cli.utils.play_scenario_analysis_io import read_bytes, read_json, safe_local_path
from cli.utils.verification_change import read_run, same_json
from services.verification.input_chunks import (
    decode_object,
    source_revision,
    validate_input_paths,
)
from services.verification.player_candidate import CANDIDATE_MANIFEST_LIMIT, parse_manifest


def inventory(value, *, allow_empty=False):
    if not isinstance(value, dict) or len(value) > 1024 or (not value and not allow_empty):
        raise ValueError("Expected a bounded path-to-SHA256 inventory")
    validate_input_paths(value)
    for path, digest in value.items():
        if (
            len(path) > 512
            or not isinstance(digest, str)
            or not re.fullmatch("[a-f0-9]{64}", digest)
        ):
            raise ValueError("Invalid source inventory path or digest")
    return value


def trial_manifest(path, schema, limit):
    path = safe_local_path(path)
    data = read_json(path)
    if data.get("schema") != schema:
        raise ValueError(f"Expected {schema}")
    trials = data.get("trials")
    if (
        not isinstance(trials, list)
        or len(trials) > limit
        or any(not isinstance(r, dict) for r in trials)
    ):
        raise ValueError(f"Expected at most {limit} explicit trial records")
    check = data.get("check")
    if not isinstance(check, dict) or check.get("kind") not in ("scenario", "test", "observation"):
        raise ValueError("Select a named scenario, test or state observation")
    if not isinstance(check.get("id"), str) or not 0 < len(check["id"]) <= 512:
        raise ValueError("Expected an exact bounded check ID")
    return data, (check["kind"], check["id"])


class TrialArchive:
    def __init__(self, path):
        self.root = safe_local_path(path).parent
        self.total = 0
        self.jobs = set()
        self.runs = set()
        self.builds = {}
        self.payloads = {}

    def artifact(self, name):
        if not isinstance(name, str) or not name.endswith(".json") or "\\" in name:
            raise ValueError("Expected a relative .json artifact path")
        if any(
            not p or p.startswith(".") or p.rstrip(" .") != p or ":" in p for p in name.split("/")
        ):
            raise ValueError("Artifact path must stay inside the selected trial archive")
        if "/assets/resources/gamedata/" in f"/{name.casefold()}/":
            raise ValueError("Artifact outside supported scope")
        path = safe_local_path(self.root / name)
        self.total += path.stat().st_size
        if self.total > 128 * 1024 * 1024:
            raise ValueError("Trial archive exceeds 128 MiB")
        return path

    def read(self, row, key, inputs):
        run = read_run(self.artifact(row.get("verification")), self.artifact(row.get("native")))
        path = self.artifact(row.get("candidate"))
        raw = read_bytes(path, CANDIDATE_MANIFEST_LIMIT)
        if sha256(raw).hexdigest() != run.manifest:
            raise ValueError("Candidate manifest differs from the verification report")
        project = decode_object(raw).get("project")
        if not isinstance(project, str) or not project:
            raise ValueError("Missing archived candidate project")

        # This path is the recorded identity only. parse_manifest resolves chunks beside the archive.
        def read_chunk(chunk_path, limit):
            self.total += safe_local_path(chunk_path).stat().st_size
            if self.total > 128 * 1024 * 1024:
                raise ValueError("Trial archive exceeds 128 MiB")
            return read_bytes(chunk_path, limit)

        candidate = parse_manifest(raw, path.parent, Path(project), read=read_chunk)
        if (
            candidate.inputs,
            candidate.source_revision,
            candidate.build_id,
            candidate.payload_hash,
            candidate.scenario,
        ) != (inputs, run.source, run.build_id, run.payload, run.scenario):
            raise ValueError("Trial inputs, scenario or build differ from the measured candidate")
        jobs = run.observed_jobs()
        if run.run_id in self.runs or jobs & self.jobs:
            raise ValueError("A trial reused a verification or native execution")
        self.runs.add(run.run_id)
        self.jobs.update(jobs)
        build = (run.source, run.payload)
        if self.builds.setdefault(run.build_id, build) != build:
            raise ValueError("One build ID has conflicting source or payload identities")
        if self.payloads.setdefault(run.source, run.payload) != run.payload:
            raise ValueError(
                "Trials for unchanged captured inputs must use the same Player payload"
            )
        check = run.checks.get(key, {})
        state = check.get("state")
        if (
            not run.inputs_confirmed
            or run.state not in ("passed", "failed")
            or state not in ("passed", "failed")
        ):
            raise ValueError("The selected check lacks a complete source-bound execution")
        if run.state != state:
            raise ValueError(
                "The selected check and the overall verification have different outcomes"
            )
        primary = ("scenario", run.scenario)
        if key != primary and run.checks[primary].get("state") != "passed":
            raise ValueError("The supporting Player scenario must have passed")
        conditions = dict(run.conditions)
        oracle = {
            "definition_hash": check.get("definition_hash"),
            "expected_state": None,
        }
        if key == primary:
            if state == "passed" and check.get("signature"):
                raise ValueError("A passing Player scenario contains a recorded native failure")
            oracle["definition_hash"] = conditions.pop("definition_hash")
        if key[0] == "observation":
            if candidate.state_check is None or not same_json(
                candidate.state_check.expected, check.get("expected_state")
            ):
                raise ValueError("Observed state lacks its registered oracle")
            oracle["expected_state"] = candidate.state_check.expected
            conditions["log_marker"] = candidate.state_check.log_marker
        conditions.update(check_target=check.get("target"), check_mode=check.get("mode"))
        receipt = {
            **run.identity(),
            "check_job_ids": check.get("child_job_ids", []),
            "manifest_sha256": run.manifest,
            "state": state,
            "signature": check.get("signature"),
        }
        return receipt, conditions, oracle


def _oracle(value, kind):
    if not isinstance(value, dict) or not value:
        raise ValueError("Expected a nonempty named oracle revision")
    inputs = inventory(value.get("inputs", {}), allow_empty=True)
    definition, expected = value.get("definition_hash"), value.get("expected_state")
    if kind == "observation":
        if not isinstance(expected, dict) or not expected or definition is not None or inputs:
            raise ValueError("State oracles must specify their exact expected_state")
    elif (
        not isinstance(definition, str)
        or not re.fullmatch("[a-f0-9]{64}", definition)
        or expected is not None
    ):
        raise ValueError("A scenario or test oracle requires a definition_hash")
    if kind == "test" and not any(p.endswith(".cs") for p in inputs):
        raise ValueError("An NUnit oracle requires captured test source inputs")
    return inputs, {"definition_hash": definition, "expected_state": expected}


def oracle_matrix(path):
    data, key = trial_manifest(path, "verification_oracle_matrix@1", 4)
    games, oracles = data.get("games"), data.get("oracles")
    axes = {"old", "new"}
    if any(not isinstance(axis, dict) or set(axis) != axes for axis in (games, oracles)):
        raise ValueError("Declare old and new game and oracle revisions")
    games = {name: inventory(value) for name, value in games.items()}
    oracles = {name: _oracle(value, key[0]) for name, value in oracles.items()}
    if games["old"] == games["new"] or same_json(oracles["old"], oracles["new"]):
        raise ValueError("Both axes must have distinct revisions")
    game_paths = set(games["old"]) | set(games["new"])
    oracle_paths = set(oracles["old"][0]) | set(oracles["new"][0])
    if {p.casefold().removesuffix(".meta") for p in game_paths} & {
        p.casefold().removesuffix(".meta") for p in oracle_paths
    }:
        raise ValueError("Game and oracle source ownership overlaps")
    if key[0] == "test" and oracles["old"][0] == oracles["new"][0]:
        raise ValueError("NUnit test revisions must have distinct source inputs")
    archive = TrialArchive(path)
    cells = {}
    for row in data["trials"]:
        cell = (row.get("game"), row.get("oracle"))
        if not all(isinstance(axis, str) and axis in axes for axis in cell) or cell in cells:
            raise ValueError("Unexpected or duplicate matrix cell")
        cells[cell] = row
    reference = None
    outputs = []
    for game in ("old", "new"):
        for oracle in ("old", "new"):
            oracle_inputs, expected = oracles[oracle]
            inputs = games[game] | oracle_inputs
            inventory(inputs)
            output = {
                "game": game,
                "oracle": oracle,
                "source_revision": source_revision(inputs),
                "state": "missing",
            }
            if row := cells.get((game, oracle)):
                try:
                    receipt, conditions, actual = archive.read(row, key, inputs)
                    if not same_json(actual, expected):
                        raise ValueError("Executed oracle differs from this column")
                    if reference is not None and not same_json(reference, conditions):
                        raise ValueError("Execution conditions differ across cells")
                    reference = conditions
                    output.update(receipt)
                except (OSError, ValueError) as exc:
                    output.update(state="incomplete", reason=str(exc))
            outputs.append(output)
    states = [r["state"] for r in outputs]
    result = {
        "schema": "verification_oracle_matrix_result@1",
        "check": data["check"],
        "cells": outputs,
    }
    if any(state not in ("passed", "failed") for state in states):
        return {**result, "state": "incomplete"}, 2
    old_old, old_new, new_old, new_new = states
    if old_old != "failed" or new_new != "passed":
        return {**result, "state": "repair_not_demonstrated"}, 1
    repaired, weakened = new_old == "passed", old_new == "passed"
    state = {
        (True, False): "repair_supported",
        (True, True): "repair_and_weakened_oracle",
        (False, True): "weakened_oracle",
        (False, False): "joint_change_required",
    }[repaired, weakened]
    return {**result, "state": state}, 0 if state == "repair_supported" else 1
