"""Commit-scoped reuse through the full CLI, using independent archived artifacts."""

from hashlib import sha256
import json

from click.testing import CliRunner
import pytest

from cli.main import cli
from services.verification import evidence_reuse
from services.verification.loader_observations import import_player_loader_capture
from .test_play_scenario_player import bundle
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_git_draft import git
from .test_verification_player_candidates import registered
from .verification_artifact_fixtures import make_capture


FINISHED = 1_800_000_000_000
FILES = {
    "source": ["Assets/Wind.cs"],
    "dependencies": ["Assets/Shared.prefab", "Assets/Shared.prefab.meta"],
    "settings": ["ProjectSettings/QualitySettings.asset"],
}


def write(path, document):
    raw = json.dumps(document).encode()
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


def invoke(command, *arguments):
    return CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "verification-evidence",
            command,
            *map(str, arguments),
        ],
    )


@pytest.fixture
def pair(tmp_path, registered, monkeypatch):
    project, _, register = registered
    git(tmp_path, "init")
    git(tmp_path, "config", "core.autocrlf", "false")
    for name in (
        "Assets/Shared.prefab",
        "Assets/Other.asset",
        "Packages/manifest.json",
        "ProjectSettings/QualitySettings.asset",
    ):
        path = project / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"{}")
    (project / "Assets/Shared.prefab.meta").write_bytes(b"guid: " + b"d" * 32 + b"\n")
    git(tmp_path, "add", "project")
    git(tmp_path, "commit", "-m", "original project")
    report_path, native_path = make_capture(tmp_path, 1)
    report = json.loads(report_path.read_bytes())["data"]
    native = json.loads(native_path.read_bytes())
    env = {"schema_version": 1, **native["reproduction"]["environment"]}
    clock = FINISHED + 500
    monkeypatch.setattr(evidence_reuse, "time_ns", lambda: clock * 1_000_000)

    def prepare(
        *,
        change="notes.md",
        external="self_contained",
        gaps=(),
        state="passed",
        environment=None,
        registration=None,
    ):
        old_commit = git(tmp_path, "rev-parse", "HEAD").decode().strip()
        changed = project / change
        changed.write_bytes(b"changed")
        git(tmp_path, "add", "project")
        git(tmp_path, "commit", "-m", "target")
        # A teammate verifies both commits in a separate checkout. The builder's
        # absolute registration paths cannot be used as local input roots.
        checkout = tmp_path / "teammate"
        git(tmp_path, "clone", "--local", str(tmp_path), str(checkout))
        target_project = checkout / "project"
        artifacts = tmp_path / "registrations"
        scopes = []
        manifests = []
        for name, commit in (("archived", old_commit), ("target", "HEAD")):
            scope = tmp_path / f"{name}-scope.json"
            expected_env = (
                env if name == "archived" or environment is None else {**env, **environment}
            )
            env_file = tmp_path / f"{name}-environment.json"
            write(env_file, expected_env)
            args = [
                "--project",
                project if name == "archived" else target_project,
                "--revision",
                commit,
                "--environment",
                env_file,
                "--runtime-inputs",
                "self_contained" if name == "archived" else external,
                "--output",
                scope,
            ]
            for field, option in (
                ("source", "--source"),
                ("dependencies", "--dependency"),
                ("settings", "--setting"),
            ):
                for path in FILES[field]:
                    args.extend([option, path])
            if name == "target":
                for gap in gaps:
                    args.extend(["--unresolved", gap])
            result = invoke("scope", *args)
            assert result.exit_code == 0, (result.output, result.exception)
            binding = json.loads(result.output)
            scope_data = json.loads(scope.read_bytes())
            assert scope_data["project_prefix"] == "project/"
            inputs = {k: v for section in scope_data["inputs"].values() for k, v in section.items()}
            manifest = register(
                name,
                scenario="doors",
                inputs=inputs,
                assets=scope_data["asset_guids"],
                source_revision=binding["source_revision"],
                observations=binding["observations"],
            )
            if name == "target" and registration:
                manifest.update(registration)
            manifest["project"] = "/ci/game"
            manifest_path = artifacts / name / "candidate.json"
            write(manifest_path, manifest)
            manifests.append(manifest_path)
            scopes.append(scope)

        old = json.loads(manifests[0].read_bytes())
        bundle_path = artifacts / "target/build/scenario-bundle.json"
        original_bundle = json.loads(
            (artifacts / "archived/build/scenario-bundle.json").read_bytes()
        )
        identity = {
            "job_id": "b" * 32,
            "build_id": old["build_id"],
            "payload_hash": old["payload_hash"],
            "build_source_revision": old["source_revision"],
        }
        report["stages"][0]["evidence"].update(identity)
        for row in (report["children"][0], report["executed_checks"][0], native):
            row["job_id"] = identity["job_id"]
        report["comparison"].update(
            manifest_sha256=sha256(manifests[0].read_bytes()).hexdigest(),
            source_revision=old["source_revision"],
        )
        report.update(
            source_token=old["source_revision"], finished_at_unix_ms=FINISHED, state=state
        )
        report["started_at_unix_ms"] = FINISHED - 8000
        native.update(started_unix_ms=FINISHED - 7000, finished_unix_ms=FINISHED - 250)
        report["stages"][0]["state"] = state
        if state == "failed":
            for row in (report["stages"][0]["evidence"], native):
                row.update(status="failed", exit_code=1)
            report["executed_checks"][0]["status"] = "failed"
        for key in ("build_id", "payload_hash", "build_source_revision"):
            native["reproduction"][key] = identity[key]
        for key in ("definition_hash", "package_version", "unity_version"):
            native["reproduction"][key] = original_bundle[key]
        native["scenario"] = original_bundle["definition"]
        report["installation"]["unity_version"] = original_bundle["unity_version"]
        native["loader_capture"] = {
            **identity,
            "schema_version": 1,
            "observations": [],
            "dropped": 0,
        }
        report["stages"].extend(
            [
                {
                    "name": "reference_impact",
                    "required": False,
                    "state": "passed",
                    "evidence": {
                        "assets": {
                            "d" * 32: {
                                "coverage": ["partial_static", "selected_sources_only"],
                                "findings": [],
                            }
                        },
                        "coverage": [],
                    },
                },
                {
                    "name": "loader_observations",
                    "required": False,
                    "state": "passed",
                    "evidence": import_player_loader_capture(native, expected=identity),
                },
            ]
        )
        write(report_path, {"success": True, "data": report})
        write(native_path, native)
        return dict(
            project=target_project,
            verification=report_path,
            native=native_path,
            archived=manifests[0],
            target=manifests[1],
            archive_scope=scopes[0],
            target_scope=scopes[1],
            bundle=bundle_path,
            max_age_seconds=5,
        )

    return prepare


def assess(options):
    arguments = []
    for key, value in options.items():
        arguments.extend(["--" + key.replace("_", "-"), value])
    result = invoke("reuse", *arguments)
    assert result.exit_code in (0, 2), (result.output, result.exception)
    return result.exit_code, json.loads(result.output)


@pytest.mark.parametrize("state", ["passed", "failed"])
def test_docs_commit_retains_original_observation_across_checkouts(pair, state):
    code, result = assess(pair(state=state))
    assert code == 0 and result["state"] == "retained_observation"
    assert result["original"]["git_commit"] != result["target"]["git_commit"]
    assert result["original"]["verification_finished_at_unix_ms"] == FINISHED
    assert result["original"]["native_finished_unix_ms"] == FINISHED - 250
    assert result["original"]["scenario_state"] == result["original"]["verification_state"] == state
    assert result["native_executed"] is False and result["reasons"] == []


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"change": "Assets/Wind.cs"}, "changed_project_inputs"),
        ({"change": "Assets/Shared.prefab"}, "changed_project_inputs"),
        ({"change": "Assets/Other.asset"}, "changed_project_inputs"),
        ({"change": "Packages/manifest.json"}, "changed_project_inputs"),
        ({"change": "ProjectSettings/QualitySettings.asset"}, "changed_project_inputs"),
        ({"gaps": ["runtime-config"]}, "unresolved_dependencies"),
        ({"external": "unknown"}, "unresolved_dependencies"),
        ({"environment": {"architecture": "arm64"}}, "unconfirmed_target_environment"),
        ({"registration": {"broader_check": "extra-check"}}, "changed_registration_scope"),
    ],
)
def test_changed_or_unknown_scope_requires_execution(pair, options, reason):
    code, report = assess(pair(**options))
    assert code == 2 and report["state"] == "new_execution_required"
    assert reason in report["reasons"]


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("missing_scope", "unbound_dependency_scope"),
        ("missing_time", "missing_execution_time"),
        ("boolean_time", "missing_execution_time"),
        ("future", "execution_time_outside_window"),
        ("expired_native_fresh_parent", "execution_time_outside_window"),
        ("missing_native_time", "missing_execution_time"),
        ("native_after_parent", "inconsistent_execution_time"),
        ("native_started_after_finish", "inconsistent_execution_time"),
        ("partial_static", "unresolved_reference_impact"),
        ("dynamic_key", "unresolved_reference_impact"),
        ("unknown_hook", "unconfirmed_runtime_dependencies"),
        ("dropped_hooks", "unconfirmed_runtime_dependencies"),
        ("definition", "changed_test_definition"),
        ("backend", "changed_player_platform"),
        ("build", "changed_player_build"),
        ("closure_revision", "unconfirmed_graph_dependencies"),
    ],
)
def test_missing_or_incompatible_artifacts_cannot_create_a_pass(pair, fault, reason, tmp_path):
    options = pair()
    verification = json.loads(options["verification"].read_bytes())["data"]
    native = json.loads(options["native"].read_bytes())
    bundle_doc = json.loads(options["bundle"].read_bytes())
    if fault == "missing_scope":
        del options["archive_scope"]
    elif fault in ("missing_time", "boolean_time", "future"):
        verification["finished_at_unix_ms"] = {
            "missing_time": None,
            "boolean_time": True,
            "future": FINISHED + 1000,
        }[fault]
    elif fault == "expired_native_fresh_parent":
        native["finished_unix_ms"] = FINISHED - 5000
    elif fault == "missing_native_time":
        del native["finished_unix_ms"]
    elif fault == "native_after_parent":
        native["finished_unix_ms"] = FINISHED + 1
    elif fault == "native_started_after_finish":
        native["started_unix_ms"] = FINISHED
    elif fault == "partial_static":
        verification["stages"][-2]["evidence"]["coverage"].append(
            {"reason": "source_analysis_limit"}
        )
    elif fault == "dynamic_key":
        asset = verification["stages"][-2]["evidence"]["assets"]["d" * 32]
        asset["findings"].append({"kind": "dynamic_key", "state": "unresolved"})
    elif fault in ("unknown_hook", "dropped_hooks"):
        capture = native["loader_capture"]
        if fault == "dropped_hooks":
            capture["dropped"] = 1
        else:
            capture["observations"].append(
                {
                    "sequence": 1,
                    "kind": "load",
                    "scheme": "remote",
                    "key": "live",
                    "owner_path": "",
                    "outcome": "loaded",
                }
            )
        verification["stages"][-1]["evidence"] = import_player_loader_capture(
            native, expected=capture
        )
    elif fault == "definition":
        bundle_doc["definition_json"] += " "
        bundle_doc["definition_hash"] = sha256(bundle_doc["definition_json"].encode()).hexdigest()
    elif fault == "backend":
        bundle_doc["scripting_backend"] = "il2cpp"
    elif fault == "build":
        manifest = json.loads(options["target"].read_bytes())
        bundle_doc["build_id"] = manifest["build_id"] = "c" * 32
        write(options["target"], manifest)
    elif fault == "closure_revision":
        options["closure"] = tmp_path / "closure.json"
        scope = json.loads(options["target_scope"].read_bytes())
        write(
            options["closure"],
            {
                "kind": "verification_dependency_closure",
                "schema_version": 1,
                "candidate_commit": scope["git_commit"],
                "input_hashes": {},
                "expanded_source_revision": "f" * 64,
                "candidate_source_revision": scope["source_revision"],
                "unresolved": [],
            },
        )
    write(options["verification"], {"success": True, "data": verification})
    write(options["native"], native)
    write(options["bundle"], bundle_doc)
    code, result = assess(options)
    assert code == 2 and reason in result["reasons"]


def test_scope_digest_cannot_be_replaced_after_execution(pair):
    options = pair()
    scope = json.loads(options["archive_scope"].read_bytes())
    scope["unresolved"] = ["archival mutation"]
    write(options["archive_scope"], scope)
    code, result = assess(options)
    assert code == 2 and result["state"] == "incomplete"
    assert "digest differs" in result["error"]


def test_rechecks_target_scope_against_the_selected_git_subproject(pair):
    options = pair()
    scope = json.loads(options["target_scope"].read_bytes())
    scope["project_prefix"] = "another-game/"
    manifest = json.loads(options["target"].read_bytes())
    manifest["observations"][evidence_reuse.SCOPE_KEY] = write(options["target_scope"], scope)
    write(options["target"], manifest)
    code, result = assess(options)
    assert code == 2 and result["state"] == "incomplete"
    assert "Git objects and scope disagree" in result["error"]
