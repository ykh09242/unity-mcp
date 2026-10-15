"""Meaningful declared-input cases against real program filesystem readers."""

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from tools.tests.test_feature_program_verification import fixture  # noqa: F401
from tools.tests.test_feature_program import fp
import feature_program_inputs as inputs
import feature_program_read_model as read_model


ENV = {"facts": {"runtime": "fixture-v1"}, "unknowns": []}
METHOD = {"procedure": "Fixture input inspection", "version": "1", "execution_kind": "manual"}


@pytest.mark.parametrize("change", ["add", "remove"])
def test_read_model_closes_separate_excluded_boundary_walk(fixture, change):
    # Given a source directory with a measured skipped-boundary inventory.
    program = fixture.program
    directory = program.repo / "checks"
    directory.mkdir()
    excluded = directory / "node_modules"
    if change == "remove":
        excluded.mkdir()
    manifest = declaration()
    manifest["groups"]["test"] = [{"path": "checks", "kind": "directory", "required": True}]
    parsed = inputs.normalize_manifest(manifest)
    state = program.load()
    body = fp.canonical(state)
    observation = fp.flow_module("storage").decode_registry(body)
    collector = read_model.Collector(
        program.repo,
        program.directory,
        program_id=state["program_id"],
        body_sha256=fp.sha(body),
        producer=read_model.producer_identity(program.repo),
        query=read_model.QUERY,
    )
    with read_model.collect(collector):
        inputs._exclusions(program, parsed)
    model = collector.finish({"status": "measured"})
    # When only a skipped directory boundary is added or removed.
    if change == "add":
        excluded.mkdir()
    else:
        excluded.rmdir()
    # Then fresh exclusion discovery rejects the old model without reading excluded bytes.
    assert (
        read_model.validate(
            model, program, observation=observation, observer=fp._ClosingObserver(program)
        )
        is None
    )


def test_read_model_exclusion_walk_does_not_read_sensitive_descendants(fixture):
    # Given an excluded directory containing bytes outside the declared readable scope.
    program = fixture.program
    directory = program.repo / "checks" / "node_modules"
    directory.mkdir(parents=True)
    (directory / "secret.env").write_bytes(b"excluded bytes must not enter model")
    manifest = declaration()
    manifest["groups"]["test"] = [{"path": "checks", "kind": "directory", "required": True}]
    state = program.load()
    body = fp.canonical(state)
    collector = read_model.Collector(
        program.repo,
        program.directory,
        program_id=state["program_id"],
        body_sha256=fp.sha(body),
        producer=read_model.producer_identity(program.repo),
        query=read_model.QUERY,
    )
    with read_model.collect(collector):
        inputs._exclusions(program, inputs.normalize_manifest(manifest))
    # When the boundary-only dependency image is retained.
    model = collector.finish({"status": "measured"})
    # Then the skipped directory is explicit but neither sensitive names nor bytes were read.
    exclusions = model["dependencies"]["exclusions"]
    assert exclusions[0]["entries"] == [
        {"group": "test", "path": "checks/node_modules", "kind": "excluded_directory"}
    ]
    assert model["dependencies"]["files"] == []


def declaration(*, complete=True):
    return {
        "schema_version": 1,
        "checks": ["scoped_tests"],
        "groups": {
            "source": [{"path": "src/S01.py", "kind": "file", "required": True}],
            "test": [],
            "config": [],
        },
        "artifact_refs": [],
        "completeness": {
            "status": "complete" if complete else "unknown",
            "reason": "Inspected fixture closure",
            "unknowns": [] if complete else ["dynamic_plugin"],
            "evidence_refs": [],
        },
    }


def capture(fixture, manifest=None, **facts):
    return inputs.capture_inputs(
        fixture.program,
        manifest or declaration(),
        environment=facts.get("environment", ENV),
        method=facts.get("method", METHOD),
    )


@pytest.mark.parametrize("group", ["source", "test", "config"])
def test_actual_dependency_changes_affect_declared_check(fixture, group):
    # Given exact production/test/config inputs, including shared unowned dependencies.
    manifest = declaration()
    if group != "source":
        path = group + ".txt"
        (fixture.program.repo / path).write_bytes(b"before")
        manifest["groups"][group].append({"path": path, "kind": "file", "required": True})
    else:
        path = "src/S01.py"
    before = capture(fixture, manifest)
    (fixture.program.repo / path).write_bytes(b"after")
    # When complete captured inputs are compared.
    result = inputs.compare_inputs(before, capture(fixture, manifest))
    # Then exactly the affected declared check and dependency group are reported.
    assert result["status"] == "changed"
    assert result["affected_checks"] == ["scoped_tests"]
    assert result["differences"][0]["group"] == group


def test_unrelated_web_change_keeps_complete_scoped_inputs(fixture):
    # Given a complete scoped Python input declaration.
    before = capture(fixture)
    web = fixture.program.repo / "web"
    web.mkdir()
    (web / "display.tsx").write_text("display changed", encoding="utf-8")
    # When the declared inputs are captured after unrelated Web work.
    result = inputs.compare_inputs(before, capture(fixture))
    # Then execution input identity is unchanged, without a task acceptance claim.
    assert result["status"] == "unchanged"
    assert result["advisory"] is True


@pytest.mark.parametrize("change", ["add", "remove", "replace"])
def test_directory_membership_and_kind_are_inputs(fixture, change):
    # Given a recursive test discovery root with one file.
    directory = fixture.program.repo / "checks"
    directory.mkdir()
    (directory / "one.py").write_bytes(b"one")
    manifest = declaration()
    manifest["groups"]["test"] = [{"path": "checks", "kind": "directory", "required": True}]
    before = capture(fixture, manifest)
    match change:
        case "add":
            (directory / "two.py").write_bytes(b"two")
        case "remove":
            (directory / "one.py").unlink()
        case "replace":
            (directory / "one.py").unlink()
            directory.rmdir()
            directory.write_bytes(b"file")
    # When actual collection topology is compared.
    result = inputs.compare_inputs(before, capture(fixture, manifest))
    # Then membership changes invalidate the check and wrong-kind closure is unknown.
    assert result["status"] == ("not_assessed" if change == "replace" else "changed")
    assert result["differences"]


@pytest.mark.parametrize("required,expected", [(True, "unknown"), (False, "complete")])
def test_missing_dependency_policy_is_explicit(fixture, required, expected):
    # Given a deliberately absent input with explicit required/optional policy.
    manifest = declaration()
    manifest["groups"]["config"] = [{"path": "missing.json", "kind": "file", "required": required}]
    # When exact file absence is captured.
    result = capture(fixture, manifest)
    # Then required absence is unknown while intentional optional absence is identified.
    assert result["snapshots"]["config"]["missing.json"] == "missing"
    assert result["completeness"]["status"] == expected


@pytest.mark.parametrize("field", ["closure", "environment", "method"])
def test_unknown_facts_prevent_reuse_assessment(fixture, field):
    # Given a missing known closure/environment/method fact.
    manifest = declaration(complete=field != "closure")
    facts = {}
    if field == "environment":
        facts["environment"] = None
    if field == "method":
        facts["method"] = {"command": "inert description", "execution_kind": "test"}
    before = capture(fixture, manifest, **facts)
    # When byte-identical captures are compared.
    result = inputs.compare_inputs(before, before)
    # Then equality is insufficient for an unknown actual input closure.
    assert result["status"] == "not_assessed"
    assert result["unknowns"]


@pytest.mark.parametrize("field", ["runtime", "argv", "cwd", "procedure_version"])
def test_meaningful_environment_or_method_change_is_affected(fixture, field):
    # Given one complete structured runtime/method identity.
    before = capture(fixture)
    env, method = deepcopy(ENV), deepcopy(METHOD)
    match field:
        case "runtime":
            env["facts"]["runtime"] = "fixture-v2"
        case "argv":
            method = {"argv": ["python", "-W", "error"], "cwd": "fixture", "execution_kind": "test"}
        case "cwd":
            method = {"argv": ["python"], "cwd": "different", "execution_kind": "test"}
        case "procedure_version":
            method["version"] = "2"
    # When the changed observed facts are compared.
    result = inputs.compare_inputs(before, capture(fixture, environment=env, method=method))
    # Then the whole declared check requires recheck.
    assert result["status"] == "changed"
    assert result["affected_checks"] == ["scoped_tests"]


def test_environment_display_label_does_not_change_identity(fixture):
    # Given captured facts and a separate display label.
    before = capture(fixture, environment={**ENV, "label": "First"})
    # When only the human-readable label changes.
    after = capture(fixture, environment={**ENV, "label": "Second"})
    # Then meaningful observed environment identity remains unchanged.
    assert before["input_digest"] == after["input_digest"]
    assert inputs.compare_inputs(before, after)["status"] == "unchanged"


def test_immutable_fixture_input_is_checked_against_bytes(fixture):
    # Given an actual frozen fixture input reference.
    name = fixture.evidence("S01", label="fixture-input")
    path = fixture.program.path(name)
    manifest = declaration()
    manifest["artifact_refs"] = [{"path": name, "sha256": fp.sha(path.read_bytes())}]
    capture(fixture, manifest)
    path.write_bytes(b"tampered")
    # When a caller tries to recapture the modified fixture under its old identity.
    with pytest.raises(fp.ProgramError):
        capture(fixture, manifest)
    # Then hash validation prevents a misleading current input capture.


@pytest.mark.parametrize(
    "path", [".work/private.json", ".env", "Assets/Resources/GameData/raw.json", "../escape"]
)
def test_prohibited_inputs_fail_before_source_reads(fixture, path, monkeypatch):
    # Given a prohibited explicit dependency and a reader that records unexpected access.
    manifest = declaration()
    manifest["groups"]["config"] = [{"path": path, "kind": "file", "required": True}]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Prohibited bytes read")

    monkeypatch.setattr(fp.Program, "source_snapshot", forbidden)
    # When the manifest crosses the input boundary.
    with pytest.raises(fp.ProgramError):
        capture(fixture, manifest)
    # Then no source read occurred.


def test_malformed_and_rewritten_capture_fails_boundary(fixture):
    # Given a previously captured immutable input identity.
    value = capture(fixture)
    value["snapshots"]["source"]["src/S01.py"] = "a" * 64
    # When retained capture facts are rewritten without their original identity.
    with pytest.raises(fp.ProgramError):
        inputs.compare_inputs(value, value)
    # Then explicit new malformed metadata never downgrades to legacy absence.


def test_dependency_selector_order_is_canonical(fixture):
    # Given two declared source files in an explicit inspected closure.
    manifest = declaration()
    manifest["groups"]["source"].append({"path": "src/S02.py", "kind": "file", "required": True})
    before = capture(fixture, manifest)
    manifest["groups"]["source"].reverse()
    # When only selector presentation order changes.
    after = capture(fixture, manifest)
    # Then declaration and raw input identity remain equal.
    assert before["input_digest"] == after["input_digest"]


def test_distinct_check_scopes_only_affect_matching_checks(fixture):
    # Given two exact checks with distinct source dependencies.
    manifest = declaration()
    manifest["checks"] = ["scoped_tests", "format_lint"]
    manifest["groups"]["source"][0]["check_ids"] = ["scoped_tests"]
    manifest["groups"]["source"].append(
        {"path": "src/S02.py", "kind": "file", "required": True, "check_ids": ["format_lint"]}
    )
    before = capture(fixture, manifest)
    (fixture.program.repo / "src/S02.py").write_bytes(b"format-change")
    # When the changed production file belongs to only one declared check.
    result = inputs.compare_inputs(before, capture(fixture, manifest))
    # Then unrelated check identity does not acquire that source difference.
    assert result["affected_checks"] == ["format_lint"]


def test_finite_preparation_keeps_ambient_dependencies_unknown():
    # Given explicit selected Python test files, without a closure attestation.
    # When a known check template prepares its concrete config dependencies.
    result = inputs.prepare_manifest("scoped_tests", ["tools/tests/test_x.py"])
    # Then lock/config inputs are included while uninspected dependencies remain unknown.
    assert result["completeness"]["status"] == "unknown"
    assert {item["path"] for item in result["groups"]["config"]} == {
        "Server/pyproject.toml",
        "Server/uv.lock",
    }


@pytest.mark.parametrize("include_cache", [False, True])
def test_raw_git_blob_bytes_and_membership_are_captured(tmp_path, include_cache):
    # Given a synthetic local object database, without staging or product repository writes.
    repo = tmp_path / "git-fixture"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

    def git(*args, data=None):
        return subprocess.run(
            ["git", "-c", "safe.directory=" + repo.as_posix(), "-C", str(repo), *args],
            input=data,
            check=True,
            capture_output=True,
        ).stdout.strip()

    oid = git("hash-object", "-w", "--stdin", data=b"raw\r\n")
    if include_cache:
        cache_oid = git("hash-object", "-w", "--stdin", data=b"excluded-cache")
        cache_tree = git("mktree", data=b"100644 blob " + cache_oid + b"\tcache.bin\n")
        source_tree = git(
            "mktree",
            data=b"100644 blob "
            + oid
            + b"\tfile.py\n040000 tree "
            + cache_tree
            + b"\tnode_modules\n",
        )
        tree = git("mktree", data=b"040000 tree " + source_tree + b"\tsrc\n")
    else:
        tree = git("mktree", data=b"100644 blob " + oid + b"\tfile.py\n")
    env = {
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    import os

    commit = (
        subprocess.run(
            [
                "git",
                "-c",
                "safe.directory=" + repo.as_posix(),
                "-C",
                str(repo),
                "commit-tree",
                tree.decode(),
                "-m",
                "synthetic",
            ],
            env={**os.environ, **env},
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )
    program = fp.Program(repo, repo / ".work/programs/fixture")
    manifest = declaration()
    path = "src/file.py" if include_cache else "file.py"
    manifest["groups"]["source"][0]["path"] = "src" if include_cache else "file.py"
    manifest["groups"]["source"][0]["kind"] = "directory" if include_cache else "file"
    if include_cache:
        (repo / "src/node_modules").mkdir(parents=True)
        (repo / "src/node_modules/cache.bin").write_bytes(b"excluded-cache")
    (repo / path).write_bytes(b"raw\n")
    before = inputs.capture_git_inputs(
        program, manifest, revision=commit, environment=ENV, method=METHOD
    )
    # When exact Git object bytes are compared with the working file.
    result = inputs.compare_inputs(
        before, inputs.capture_inputs(program, manifest, environment=ENV, method=METHOD)
    )
    # Then EOL-only differences stay real, without checkout or normalization.
    assert before["snapshots"]["source"][path] == fp.sha(b"raw\r\n")
    assert before["origin"] == {"kind": "git", "revision": commit}
    assert result["status"] == ("not_assessed" if include_cache else "changed")
    if include_cache:
        assert before["exclusions"] == [
            {"group": "source", "path": "src/node_modules", "kind": "excluded_directory"}
        ]
        assert "src/node_modules/cache.bin" not in before["snapshots"]["source"]


def test_source_race_is_rejected(fixture, monkeypatch):
    # Given a source reader that observes a change between capture and recheck.
    real = fp.Program.source_snapshot
    reads = 0

    def drifting(program, scope, budget=None):
        nonlocal reads
        reads += 1
        if reads == 2:
            (program.repo / "src/S01.py").write_bytes(b"raced")
        return real(program, scope, budget)

    monkeypatch.setattr(fp.Program, "source_snapshot", drifting)
    # When actual inputs are captured.
    with pytest.raises(fp.ProgramError):
        capture(fixture)
    # Then no stable input identity is claimed across the race.


def test_ordered_argv_keeps_duplicates_and_empty_arguments(fixture):
    # Given a real method argument order with intentional repeated/empty values.
    method = {"argv": ["python", "-x", "-x", ""], "cwd": "fixture", "execution_kind": "test"}
    # When the declaration is captured without shell parsing.
    result = capture(fixture, method=method)
    # Then exact argv is retained and considered known structured method identity.
    assert result["method"]["argv"] == ["python", "-x", "-x", ""]
    assert result["completeness"]["status"] == "complete"


def test_recursive_exclusions_are_visible_unknown_without_reading_cache_bytes(fixture):
    # Given a declared recursive source root containing an excluded cache subtree.
    cache = fixture.program.repo / "src/node_modules"
    cache.mkdir()
    (cache / "cache.bin").write_bytes(b"excluded-cache-bytes")
    manifest = declaration()
    manifest["groups"]["source"] = [{"path": "src", "kind": "directory", "required": True}]
    # When allowed source bytes and skipped boundaries are captured.
    result = capture(fixture, manifest)
    # Then excluded bytes are never fingerprints and closure is conservatively unknown.
    assert "src/node_modules/cache.bin" not in result["snapshots"]["source"]
    assert result["exclusions"] == [
        {"group": "source", "path": "src/node_modules", "kind": "excluded_directory"}
    ]
    assert result["completeness"]["status"] == "unknown"
    assert inputs.compare_inputs(result, result)["status"] == "not_assessed"


@pytest.mark.parametrize("field", ["selector_kind", "closure_status", "method_kind", "fingerprint"])
def test_malformed_json_variants_are_controlled_boundary_errors(fixture, field):
    # Given explicit malformed new JSON at a variant boundary.
    manifest = declaration()
    method = deepcopy(METHOD)
    if field == "selector_kind":
        manifest["groups"]["source"][0]["kind"] = []
    if field == "closure_status":
        manifest["completeness"]["status"] = {}
    if field == "method_kind":
        method["execution_kind"] = {}
    # When a new declared/captured variant crosses the checked boundary.
    with pytest.raises(fp.ProgramError):
        if field == "fingerprint":
            retained = capture(fixture)
            retained["snapshots"]["source"]["src/S01.py"] = {}
            inputs.validate_capture(retained)
        else:
            capture(fixture, manifest, method=method)
    # Then invalid variants fail explicitly instead of throwing unhandled type errors.


@pytest.mark.parametrize("declared_kind", ["file", "directory"])
def test_optional_input_wrong_kind_is_unknown(fixture, declared_kind):
    # Given intentional optional absence semantics but an incompatible present object.
    manifest = declaration()
    manifest["groups"]["config"] = [{"path": "optional", "kind": declared_kind, "required": False}]
    path = fixture.program.repo / "optional"
    if declared_kind == "file":
        path.mkdir()
    else:
        path.write_bytes(b"actual-file")
    # When the optional declaration observes that present object.
    result = capture(fixture, manifest)
    # Then absence policy never certifies a different object kind as complete inputs.
    assert result["completeness"]["status"] == "unknown"


@pytest.mark.parametrize(
    "paths", [["tools/format_scripts.py"], ["tools/format_scripts.py", "tools/format_scripts.py"]]
)
def test_formatter_self_path_is_coalesced_with_template_dependency(paths):
    # Given an explicit formatter-self check, possibly repeated by the live CLI.
    # When the finite format template adds the same inspected checker dependency.
    result = inputs.prepare_manifest("format_python", paths)
    # Then the exact declaration appears once without dropping its dependency policy.
    assert result["groups"]["source"] == [
        {
            "path": "tools/format_scripts.py",
            "kind": "file",
            "required": True,
            "check_ids": ["format_lint"],
        }
    ]
    assert result["completeness"]["status"] == "unknown"


def test_identical_normalized_selectors_coalesce_without_changing_input_identity(fixture):
    # Given equivalent implicit/explicit sorted check mappings for the same source.
    manifest = declaration()
    manifest["checks"] = ["scoped_tests", "format_lint"]
    before = capture(fixture, manifest)
    manifest["groups"]["source"].append(
        {
            "path": "src/S01.py",
            "kind": "file",
            "required": True,
            "check_ids": ["format_lint", "scoped_tests"],
        }
    )
    # When the redundant declaration is normalized and captured.
    after = capture(fixture, manifest)
    # Then the duplicate contributes neither an extra path nor a different check input identity.
    assert inputs.manifest_scope(after["manifest"]) == ["src/S01.py"]
    assert before["input_digest"] == after["input_digest"]


@pytest.mark.parametrize("variation", ["category", "kind", "required", "checks"])
def test_conflicting_redundant_selector_definitions_still_fail(variation):
    # Given two declarations that collide at one path but disagree on a meaningful field.
    manifest = declaration()
    manifest["checks"] = ["scoped_tests", "format_lint"]
    conflicting = deepcopy(manifest["groups"]["source"][0])
    group = "source"
    match variation:
        case "category":
            group = "test"
        case "kind":
            conflicting["kind"] = "directory"
        case "required":
            conflicting["required"] = False
        case "checks":
            conflicting["check_ids"] = ["scoped_tests"]
    manifest["groups"][group].append(conflicting)
    # When the declarations cross the manifest boundary.
    with pytest.raises(fp.ProgramError):
        inputs.normalize_manifest(manifest)
    # Then coalescing never hides conflicting category/kind/policy/check obligations.


def test_overlapping_directory_and_exact_selectors_keep_check_specific_changes(fixture):
    # Given one directory-wide check and an overlapping exact-file check.
    manifest = declaration()
    manifest["checks"] = ["scoped_tests", "format_lint"]
    manifest["groups"]["source"] = [
        {"path": "src", "kind": "directory", "required": True, "check_ids": ["scoped_tests"]},
        {"path": "src/S01.py", "kind": "file", "required": True, "check_ids": ["format_lint"]},
    ]
    before = capture(fixture, manifest)
    (fixture.program.repo / "src/S02.py").write_bytes(b"changed sibling")
    # When a sibling belongs to only the directory selector.
    result = inputs.compare_inputs(before, capture(fixture, manifest))
    # Then the exact-file check does not inherit unrelated sibling changes.
    assert result["status"] == "changed"
    assert result["affected_checks"] == ["scoped_tests"]
    assert any(row["path"] == "src/S02.py" for row in result["differences"])
