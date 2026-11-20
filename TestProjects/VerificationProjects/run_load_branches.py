"""Visit an optional load in the already-built Harbor Player on the hosted builder."""

import argparse
import json
import os
from pathlib import Path
import re

from run_projects import PlayerRunOptions, project_inputs, run_player, source_revision
from services.verification.loader_compare import compare_player_loads


def run(control):
    project = control / "distribution/Harbor"
    build = control / "Harbor/build"
    output = control / "load-branches"
    output.mkdir()
    bundle = json.loads((build / "scenario-bundle.json").read_bytes())
    inputs = project_inputs(project)
    if bundle["build_source_revision"] != source_revision(inputs):
        raise ValueError("The Harbor build and checkout differ")
    assets = {}
    for path in inputs:
        if path.endswith(".prefab"):
            metadata = (project / (path + ".meta")).read_text(encoding="utf-8")
            (guid,) = re.findall(r"^guid: ([a-f0-9]{32})$", metadata, re.MULTILINE)
            assets[path] = guid
    registration = output / "candidate.json"
    registration.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": str(project),
                "inputs": inputs,
                "assets": assets,
                "scenario": bundle["scenario_name"],
                "build_id": bundle["build_id"],
                "payload_hash": bundle["payload_hash"],
                "source_revision": source_revision(inputs),
            }
        ),
        encoding="utf-8",
    )
    jobs = {}
    os.environ["UNITY_MCP_PLAYER_REFERENCE_CAPTURE"] = "1"
    for phase, route in (("before", ""), ("after", "Harbor/Ferry")):
        os.environ["MCP_HARBOR_ROUTE"] = route
        outcome = run_player(
            PlayerRunOptions(
                build_directory=build,
                output_directory=output / "runs",
                expected_build_id=bundle["build_id"],
                expected_payload_hash=bundle["payload_hash"],
                expected_build_revision=source_revision(inputs),
                source_revision=source_revision(inputs),
                expected_scenario=bundle["scenario_name"],
                timeout_seconds=120,
                require_verified_payload=True,
            )
        )
        if outcome.exit_code or outcome.report.get("status") != "succeeded":
            raise ValueError(f"Harbor execution failed; see {outcome.directory}")
        jobs[phase] = outcome.directory
    comparison = compare_player_loads(project, registration, jobs["before"], jobs["after"])
    (output / "comparison.json").write_text(
        json.dumps(comparison, indent=2) + "\n", encoding="utf-8"
    )
    new = comparison["newly_observed_loads"]
    if not new or not any(
        row["comparison"] == "observed_outside_prediction"
        and row["registered_asset_path"] == "Assets/Resources/Harbor/Ferry.prefab"
        and row["owner_path"] == "Assets/HarborRouteLoads.cs"
        for row in new
    ):
        raise ValueError("Missing the actual optional load in the selected static comparison")
    for side in ("before", "after"):
        if not any(
            row["key"] == "Harbor/Maintenance" for row in comparison[side]["unobserved_predictions"]
        ):
            raise ValueError("Unvisited maintenance path must remain unobserved")
        if not any(
            row["key"] == "Harbor/Guide" and row["comparison"] == "predicted_load"
            for row in comparison[side]["events"]
        ):
            raise ValueError("Missing the unconditional load")
    return comparison


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-directory", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().control_directory.resolve()), indent=2))
