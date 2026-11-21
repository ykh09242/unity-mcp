"""Clean UPM staging must exercise the published manifest, without curated overrides."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from tools import unity_upm_smoke as smoke
from tests.test_update_source_pins import git, put


ROOT = Path(__file__).resolve().parents[2]


def test_clean_staging_keeps_manifest_bytes_and_lets_upm_resolve_dependencies(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "MCPForUnity"
    package.mkdir(parents=True)
    original = b'{"name":"com.example.smoke","version":"1.0.0","dependencies":{"com.unity.test-framework":"1.4.6"}}\n'
    (package / "package.json").write_bytes(original)
    (package / "Fixture.cs").write_text("// package source", encoding="utf-8")
    output = repo / ".unity-ci/upm"
    project = smoke.prepare("2021.3.45f2", output, repo=repo)
    assert (output / "package/package.json").read_bytes() == original
    assert (package / "package.json").read_bytes() == original
    manifest = json.loads((project / "Packages/manifest.json").read_text())
    assert manifest == {"dependencies": {"com.example.smoke": "file:../../package"}}
    assert not (project / "Packages/packages-lock.json").exists()
    assert not (project / "Library").exists()
    assert not (output / "packages").exists()
    expected = json.loads((project / ".upm-smoke.json").read_text())
    assert expected["manifestSha256"] == hashlib.sha256(original).hexdigest()
    assert expected["dependencies"] == [{"name": "com.unity.test-framework", "version": "1.4.6"}]
    assert (project / "Assets/Tests/UntouchedPackageInstallTests.cs").is_file()


def test_smoke_matrix_uses_existing_floor_and_current_roles():
    selected = smoke.matrix(ROOT / "tools/unity-versions.json")
    config = json.loads((ROOT / "tools/unity-versions.json").read_text())
    assert [row["version"] for row in selected] == [
        row["id"] for row in config["versions"] if row["role"] in {"floor", "current"}
    ]
    assert len(selected) == 2


@pytest.fixture
def git_package(tmp_path):
    repo = tmp_path / "fixture with spaces"
    repo.mkdir()
    git(repo, "init", "-q")
    relative = "MCPForUnity/package.json"
    original = '{"name":"com.example.smoke","version":"1.0.0"}\n'
    put(repo, relative, original)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "candidate")
    revision = git(repo, "rev-parse", "HEAD")
    put(repo, relative, '{"name":"com.example.changed"}')
    return repo, revision, original.encode()


def test_mirror_url_resolves_only_the_committed_package(git_package, tmp_path):
    repo, revision, original = git_package
    project = smoke.prepare("2021.3.45f2", repo / ".unity-ci/upm", repo=repo, git_revision=revision)
    manifest = json.loads((project / "Packages/manifest.json").read_bytes())
    url = urlsplit(manifest["dependencies"]["com.example.smoke"])
    assert url.scheme == "file" and url.path.endswith("/source.git") and "%20" in url.path
    assert url.fragment == revision and parse_qs(url.query) == {"path": ["/MCPForUnity"]}
    clone = tmp_path / "consumer"
    git(tmp_path, "clone", url._replace(query="", fragment="").geturl(), str(clone))
    path = parse_qs(url.query)["path"][0].lstrip("/")
    assert smoke.git(clone, "show", f"{url.fragment}:{path}/package.json") == original
    for invalid in (f"{'a' * 40}:{path}/package.json", f"{revision}:wrong/package.json"):
        with pytest.raises(subprocess.CalledProcessError):
            git(clone, "show", invalid)
    expected = json.loads((project / ".upm-smoke.json").read_bytes())
    assert expected["gitRevision"] == revision
    assert expected["manifestSha256"] == hashlib.sha256(original).hexdigest()
    assert not (project / "Packages/packages-lock.json").exists()
    assert not (project.parent / "package").exists()


def test_staging_rejects_different_head_and_missing_committed_subfolder(git_package):
    repo, revision, _ = git_package
    output = repo / ".unity-ci/upm"
    with pytest.raises(smoke.packages.PreparationError, match="candidate commit"):
        smoke.prepare("2021.3.45f2", output, repo=repo, git_revision="a" * 40)
    git(repo, "mv", "MCPForUnity", "elsewhere")
    git(repo, "commit", "-qam", "move package")
    with pytest.raises(smoke.packages.PreparationError, match="Git show failed"):
        smoke.prepare("2021.3.45f2", output, repo=repo, git_revision=git(repo, "rev-parse", "HEAD"))
    assert not output.exists()


def test_git_install_can_clone_a_shallow_detached_ci_checkout(git_package, tmp_path):
    repo, revision, _ = git_package
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--depth=1", repo.as_uri(), str(checkout))
    git(checkout, "checkout", "--detach", revision)
    project = smoke.prepare("2021.3.45f2", checkout / "upm", repo=checkout, git_revision=revision)
    assert git(project.parent / "source.git", "rev-parse", "HEAD") == revision


@pytest.mark.parametrize(
    "fault",
    ["", "local", "wrong-folder", "unbound-request", "wrong-revision", "wrong-lock", "transitive"],
)
def test_git_lock_binds_requested_subfolder_and_actual_commit(tmp_path, fault):
    revision = "a" * 40
    other = "b" * 40
    url = f"file:///github/workspace/.unity-ci/upm/source.git?path=/MCPForUnity#{revision}"
    if fault == "wrong-folder":
        url = url.replace("/MCPForUnity", "/Other")
    if fault == "wrong-revision":
        url = url.replace(revision, other)
    entry = {
        "version": url if fault != "unbound-request" else url + "-other",
        "source": "local" if fault == "local" else "git",
        "hash": other if fault == "wrong-lock" else revision,
        "depth": 1 if fault == "transitive" else 0,
    }
    name = "com.example.smoke"
    put(tmp_path, ".upm-smoke.json", json.dumps({"packageName": name, "gitRevision": revision}))
    put(tmp_path, "Packages/manifest.json", json.dumps({"dependencies": {name: url}}))
    put(tmp_path, "Packages/packages-lock.json", json.dumps({"dependencies": {name: entry}}))
    if fault:
        with pytest.raises(smoke.packages.PreparationError, match="UPM lock"):
            smoke.check_git_lock(tmp_path, revision)
    else:
        smoke.check_git_lock(tmp_path, revision)


@pytest.mark.parametrize("roles", [["floor"], ["floor", "floor", "current"]])
def test_smoke_matrix_rejects_missing_or_ambiguous_support_roles(tmp_path, roles):
    config = json.loads((ROOT / "tools/unity-versions.json").read_text())
    config["versions"] = [dict(config["versions"][0], role=role) for role in roles]
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="one floor and one current"):
        smoke.matrix(path)


def test_smoke_workflow_requires_native_body_under_existing_license_gate():
    jobs = yaml.safe_load((ROOT / ".github/workflows/unity-upm-install.yml").read_text())["jobs"]
    job = jobs["untouchedUpmInstall"]
    assert job["needs"] == ["matrix", "license"]
    assert job["if"] == "needs.license.outputs.unity_ok == 'true'"
    assert (
        job["strategy"]["matrix"]["unity"] == "${{ fromJson(needs.matrix.outputs.upm_versions) }}"
    )
    runners = [step for step in job["steps"] if "unity-test-runner@" in step.get("uses", "")]
    assert len(runners) == 1
    assert runners[0]["with"]["testMode"] == "editmode"
    assert runners[0]["continue-on-error"] is True
    gate = next(
        step for step in job["steps"] if "--require-test UntouchedUpmSmoke." in step.get("run", "")
    )
    assert "--runner-outcome" in gate["run"]
    assert "check_unity_test_results.py" in gate["run"]
    assert not gate.get("continue-on-error")
    assert not any("actions/cache@" in step.get("uses", "") for step in job["steps"])
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    stage = next(step for step in job["steps"] if step.get("id") == "package")
    assert stage["env"]["REQUIRE_GIT"] == "${{ inputs.require_upm_install }}"
    assert stage["env"]["CANDIDATE_SHA"] == "${{ needs.matrix.outputs.checked_sha }}"
    assert '--git-revision "$CANDIDATE_SHA" --editor-workspace /github/workspace' in stage["run"]
    verify_lock = next(step for step in job["steps"] if "check-git-lock" in step.get("run", ""))
    assert verify_lock["if"] == "inputs.require_upm_install"
    assert verify_lock["env"]["CANDIDATE_SHA"] == stage["env"]["CANDIDATE_SHA"]
    assert not verify_lock.get("continue-on-error")
    assert "unity_ci_packages.py prepare" not in commands


@pytest.mark.parametrize("require_git", [False, True])
def test_workflow_stages_releases_as_git_and_integration_as_local(require_git):
    bash = shutil.which("bash")
    if os.name == "nt" and Path("C:/Program Files/Git/bin/bash.exe").is_file():
        bash = "C:/Program Files/Git/bin/bash.exe"
    if not bash:
        pytest.skip("Bash is required to exercise the hosted staging command")
    jobs = yaml.safe_load((ROOT / ".github/workflows/unity-upm-install.yml").read_text())["jobs"]
    stage = next(
        step for step in jobs["untouchedUpmInstall"]["steps"] if step.get("id") == "package"
    )
    version = "2021.3.45f2"
    sha = "a" * 40
    result = subprocess.run(
        [
            bash,
            "--noprofile",
            "--norc",
            "-c",
            "python3() { printf '%s\\0' \"$@\"; }\nsudo() { :; }\n" + stage["run"],
        ],
        env={
            **os.environ,
            "UNITY_VERSION": version,
            "CANDIDATE_SHA": sha,
            "REQUIRE_GIT": str(require_git).lower(),
        },
        capture_output=True,
        check=True,
        timeout=15,
    )
    args = result.stdout.decode().split("\0")[:-1]
    expected = [
        "tools/unity_upm_smoke.py",
        "prepare",
        "--unity-version",
        version,
        "--output",
        ".unity-ci/upm-" + version,
    ]
    if require_git:
        expected += ["--git-revision", sha, "--editor-workspace", "/github/workspace"]
    assert args == expected


@pytest.mark.parametrize("job", ["matrix", "license", "untouchedUpmInstall"])
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "missing", "success"])
def test_required_installation_only_exports_sha_after_all_jobs_pass(
    monkeypatch, tmp_path, job, outcome
):
    # Given a complete two-role native matrix with one varied job result.
    workflow = yaml.safe_load((ROOT / ".github/workflows/unity-upm-install.yml").read_text())
    gate = workflow["jobs"]["required"]
    results = {name: {"result": "success", "outputs": {}} for name in gate["needs"]}
    results["matrix"]["outputs"]["checked_sha"] = "a" * 40
    results["license"]["outputs"]["unity_ok"] = "true"
    if outcome == "missing":
        del results[job]
    else:
        results[job]["result"] = outcome
    output = tmp_path / "output"
    monkeypatch.setenv("JOB_RESULTS", json.dumps(results))
    monkeypatch.setenv("EXPECTED_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    program = gate["steps"][0]["run"]
    # When the reusable workflow evaluates its real evidence gate.
    if outcome == "success":
        exec(compile(program, "upm-gate", "exec"), {})
        # Then the contract exports the exact verified commit.
        assert output.read_text() == "checked_sha=" + "a" * 40 + "\n"
    else:
        with pytest.raises(SystemExit):
            exec(compile(program, "upm-gate", "exec"), {})
        assert not output.exists()


@pytest.mark.parametrize("actual", ["", "b" * 40, "not-a-sha"])
def test_required_installation_rejects_different_or_missing_candidate(
    monkeypatch, tmp_path, actual
):
    # Given successful jobs but no matching checked candidate.
    workflow = yaml.safe_load((ROOT / ".github/workflows/unity-upm-install.yml").read_text())
    gate = workflow["jobs"]["required"]
    results = {name: {"result": "success", "outputs": {}} for name in gate["needs"]}
    results["matrix"]["outputs"]["checked_sha"] = actual
    results["license"]["outputs"]["unity_ok"] = "true"
    output = tmp_path / "output"
    monkeypatch.setenv("JOB_RESULTS", json.dumps(results))
    monkeypatch.setenv("EXPECTED_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    # When the actual gate tries to publish the installation evidence identity.
    with pytest.raises(SystemExit):
        exec(compile(gate["steps"][0]["run"], "upm-gate", "exec"), {})
    # Then no candidate is exported.
    assert not output.exists()


@pytest.mark.parametrize(
    "delegate,event,ref,repository,licensed,same_sha,expected",
    [
        (True, "push", "refs/heads/beta", "fork/repo", "true", True, False),
        (False, "push", "refs/heads/beta", "fork/repo", "true", True, True),
        (True, "push", "refs/heads/beta", "fork/repo", "false", True, True),
        (True, "push", "refs/heads/beta", "fork/repo", "true", False, True),
        (True, "pull_request", "refs/heads/beta", "fork/repo", "true", True, True),
        (True, "workflow_dispatch", "refs/heads/beta", "fork/repo", "true", True, True),
        (True, "push", "refs/heads/main", "fork/repo", "true", True, True),
        (True, "push", "refs/heads/beta", "CoplayDev/unity-mcp", "true", True, True),
    ],
)
def test_upm_delegates_only_matching_licensed_beta_qualification(
    delegate, event, ref, repository, licensed, same_sha, expected
):
    # Given the actual reusable caller and a selected event context.
    workflow = yaml.safe_load((ROOT / ".github/workflows/unity-tests.yml").read_text())
    job = workflow["jobs"]["untouchedUpmInstall"]
    expression = job["if"]
    values = {
        "inputs.qualification_owns_upm": delegate,
        "github.event_name": event,
        "github.ref": ref,
        "github.repository": repository,
        "vars.UNITY_RUN_LICENSED_TESTS": licensed,
        "inputs.ref": "a" * 40 if same_sha else "b" * 40,
        "github.sha": "a" * 40,
    }
    for name, value in values.items():
        expression = expression.replace(name, repr(value))
    expression = expression.replace("&&", " and ").replace("!", " not ").replace(" not =", " !=")
    # When the job predicate chooses ownership.
    run_install = eval(expression, {"__builtins__": {}}, {})
    # Then only exact-SHA licensed fork beta pushes delegate the two native bodies.
    assert run_install is expected
    assert job["uses"] == "./.github/workflows/unity-upm-install.yml"
    assert job["with"]["ref"] == "${{ inputs.ref || github.sha }}"
