"""CLI comparison of independent native-shaped jobs and actual parsed project sources."""

from hashlib import sha256
import json

from click.testing import CliRunner
import pytest

from cli.main import cli
from cli.utils.play_scenario_player import PlayerRunOptions, run_player
from services.verification.input_chunks import source_revision
from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_player_candidates import registered


@pytest.fixture
def load_runs(registered, child, tmp_path):
    project, script, register = registered
    script.write_text(
        'using UnityEngine; class Wind { Object One() => Resources.Load("Guide"); '
        'Object Two(string key) => Resources.Load(key); Object Three() => Resources.Load("Skipped"); }'
    )
    assets = {}
    for i, key in enumerate(("Guide", "Optional", "Skipped"), 1):
        path = project / f"Assets/Resources/{key}.prefab"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"%YAML 1.1\n")
        guid = f"{i:032x}"
        path.with_suffix(".prefab.meta").write_text("guid: " + guid)
        assets[path.relative_to(project).as_posix()] = guid
    inputs = {
        p.relative_to(project).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in (project / "Assets").rglob("*")
        if p.is_file()
    }
    doc = register("wind", inputs=inputs, source_revision=source_revision(inputs), assets=assets)
    directory = tmp_path / "registrations/wind"
    jobs = []
    for keys in (["Guide"], ["Guide", "Optional"]):
        outcome = run_player(
            PlayerRunOptions(
                build_directory=directory / "build",
                output_directory=directory / "jobs",
                expected_build_id=doc["build_id"],
                expected_payload_hash=doc["payload_hash"],
                require_verified_payload=True,
            )
        )
        assert outcome.exit_code == 0
        job = outcome.directory
        raw = json.loads((job / "run.json").read_bytes())
        raw["loader_capture"] = {
            "schema_version": 1,
            "job_id": job.name,
            "build_id": doc["build_id"],
            "build_source_revision": doc["source_revision"],
            "payload_hash": doc["payload_hash"],
            "observations": [
                {
                    "sequence": index,
                    "kind": "load",
                    "scheme": "resources",
                    "key": key,
                    "owner_path": "Assets/Wind.cs",
                    "outcome": "loaded",
                    "asset_name": key,
                }
                for index, key in enumerate(keys, 1)
            ],
            "dropped": 0,
        }
        (job / "run.json").write_text(json.dumps(raw))
        jobs.append(job)
    return project, directory / "candidate.json", *jobs


def invoke(project, registration, before, after):
    return CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "verification-candidate",
            "compare-loads",
            "--project",
            str(project),
            "--registration",
            str(registration),
            "--before",
            str(before),
            "--after",
            str(after),
        ],
    )


def test_observed_branch_and_unvisited_prediction(load_runs):
    result = invoke(*load_runs)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    (addition,) = report["newly_observed_loads"]
    assert addition["comparison"] == "observed_outside_prediction"
    assert addition["registered_asset_path"] == "Assets/Resources/Optional.prefab"
    assert addition["owner_path"] == "Assets/Wind.cs"
    for name in ("before", "after"):
        assert report[name]["events"][0]["comparison"] == "predicted_load"
        assert [row["key"] for row in report[name]["unobserved_predictions"]] == ["Skipped"]


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "dropped",
        "foreign",
        "wrong_request",
        "stale_source",
        "failed",
        "cancelled",
        "timed_out",
        "wrong_repeats",
    ],
)
def test_partial_coverage_and_foreign_jobs(load_runs, change):
    project, registration, before, after = load_runs
    path = before / "run.json"
    raw = json.loads(path.read_bytes())
    if change == "missing":
        del raw["loader_capture"]
    elif change == "dropped":
        raw["loader_capture"]["dropped"] = 3
    elif change == "foreign":
        raw["loader_capture"]["build_id"] = "f" * 32
    elif change == "wrong_request":
        with (before / "request.json").open("ab") as stream:
            stream.write(b" ")
    elif change == "wrong_repeats":
        raw["repeat_count"] += 1
    elif change in ("failed", "cancelled", "timed_out"):
        raw.update(status=change, exit_code=1)
        for file, key in (
            ("process.json", "actual_exit_code"),
            ("diagnostics.json", "process_exit_code"),
        ):
            receipt = json.loads((before / file).read_bytes())
            (before / file).write_text(json.dumps({**receipt, key: 1}))
    else:
        (project / "Assets/Wind.cs").write_text("class Changed {}")
    path.write_text(json.dumps(raw))
    result = invoke(project, registration, before, after)
    if change in ("foreign", "wrong_request", "failed", "cancelled", "timed_out", "wrong_repeats"):
        assert result.exit_code != 0
    elif change == "stale_source":
        assert result.exit_code == 0, result.output
        assert (
            json.loads(result.output)["newly_observed_loads"][0]["comparison"]
            == "static_coverage_unknown"
        )
    else:
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["newly_observed_loads"] is None
