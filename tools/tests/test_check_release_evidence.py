"""Validate notes against synthetic commits and saved records; never call providers."""

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
BASE = "https://github.com/ykh09242/unity-mcp"


def checker() -> ModuleType:
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    path = ROOT / "tools/check_release_evidence.py"
    assert path.is_file(), "The approved check_release_notes API is not implemented"
    spec = importlib.util.spec_from_file_location("release_checker_fixture", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, (
        f"Synthetic Git {args[0]} failed: {result.stdout} {result.stderr}"
    )
    return result.stdout.strip()


@dataclass
class Bundle:
    repo: Path
    evidence: Path
    package: str
    server: str
    skill: str

    def file(self, role: str, value: str | dict) -> dict[str, str]:
        path = self.evidence.parent / (role + ".txt")
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def index(self, entry: dict) -> None:
        files = entry["files"]
        kind = entry["kind"]
        ready = (
            kind == "compile"
            or kind == "package-preparation"
            and "preparation" in files
            or kind in {"python", "native-editmode", "native-playmode"}
            and "results" in files
        )
        if (
            entry["origin"] == "github-actions"
            and "log" in files
            and "execution-log" not in files
            and "association_limit" not in entry
            and ready
        ):
            self.associate(entry)
        self.evidence.write_text(
            json.dumps({"schema_version": 1, "evidence": {"proof": entry}}), encoding="utf-8"
        )

    def notes(
        self,
        kind: str = "compile",
        result: str = "passed",
        tested: str | None = None,
        component: str = "unity",
        url: str | None = None,
    ) -> str:
        return f"""Ordinary release prose is not executed.
<!-- release-evidence:start -->
| Component | Version | Immutable input |
| --- | --- | --- |
| Unity package | 1.2.1 | [{self.package}]({BASE}/commit/{self.package}) |
| Python server | 1.2.0 | [{self.server}]({BASE}/archive/{self.server}.zip#subdirectory=Server) |
| Client skill | not separately versioned | [{self.skill}]({BASE}/commit/{self.skill}) |

| Capability / scope | Component | Verification kind | Actual result | Tested input | Evidence |
| --- | --- | --- | --- | --- | --- |
| Fixture capability | {component} | {kind} | {result} | {tested or "commit:" + self.package} | [proof]({url or BASE + "/actions/runs/123/job/456"}) |
<!-- release-evidence:end -->
"""

    def ci(
        self, kind: str = "compile", conclusion: str = "success", revision: str | None = None
    ) -> dict:
        files = {
            "workflow": self.file(
                "workflow",
                {
                    "id": 123,
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": self.package,
                    "run_attempt": 2,
                    "run_started_at": "2026-10-16T12:00:00Z",
                    "event": "push",
                    "html_url": BASE + "/actions/runs/123",
                    "repository": {"full_name": "ykh09242/unity-mcp"},
                },
            ),
            "job": self.file(
                "job",
                {
                    "id": 456,
                    "run_id": 123,
                    "status": "completed",
                    "conclusion": conclusion,
                    "html_url": BASE + "/actions/runs/123/job/456",
                    "started_at": "2026-10-16T12:00:00Z",
                    "completed_at": "2026-10-16T12:00:05Z",
                },
            ),
        }
        if conclusion != "skipped":
            files["checkout"] = self.file(
                "checkout",
                "\n".join(
                    [
                        "2026-10-16T12:00:00Z ##[group]Run actions/checkout@fixture",
                        "2026-10-16T12:00:00Z   repository: ykh09242/unity-mcp",
                        "2026-10-16T12:00:00Z ##[group]Checking out the ref",
                        "2026-10-16T12:00:00Z [command]/usr/bin/git log -1 --format=%H",
                        "2026-10-16T12:00:00Z " + (revision or self.package),
                        "2026-10-16T12:00:00Z ##[group]Removing auth",
                    ]
                ),
            )
            files["log"] = self.file(
                "log",
                "Compiler      : /fixture/csc.dll\n--- MCPForUnity.Editor [win] : 42 sources, 20 refs ---\n",
            )
        entry = {
            "origin": "github-actions",
            "kind": kind,
            "public_url": BASE + "/actions/runs/123/job/456",
            "files": files,
        }
        if conclusion == "skipped":
            entry["reason"] = "Licensed native execution disabled"
        if conclusion != "skipped":
            self.execution(
                entry,
                "Compiler      : /fixture/csc.dll\n--- MCPForUnity.Editor [win] : 42 sources, 20 refs ---\n",
            )
        self.index(entry)
        return entry

    def execution(self, entry: dict, body: str) -> None:
        entry.pop("execution_id", None)
        entry["files"].pop("execution-log", None)
        source = self.evidence.parent / entry["files"]["checkout"]["path"]
        combined = (
            source.read_text(encoding="utf-8")
            + "\n"
            + "\n".join("2026-10-16T12:00:01Z " + line for line in body.splitlines())
        )
        entry["files"]["log"] = self.file("log", combined)
        entry["files"]["checkout"] = dict(entry["files"]["log"])

    def associate(self, entry: dict) -> None:
        files = entry["files"]
        original = (self.evidence.parent / files["log"]["path"]).read_text(encoding="utf-8")
        # Keep this fixture's original checkout section; append one execution with exact step metadata.
        checkout = original.split("2026-10-16T12:00:00Z ##[group]Removing auth")[0]
        checkout += "2026-10-16T12:00:00Z ##[group]Removing auth\n"
        body_lines = [
            line.split("Z ", 1)[1]
            for line in original.splitlines()
            if "12:00:01Z " in line and "UNITY_MCP_EXECUTION " not in line
        ]
        body = "\n".join(body_lines) + "\n"
        files["execution-log"] = self.file("execution-log", body)
        execution_id = "f" * 32
        entry["execution_id"] = execution_id
        kind = entry["kind"]
        native = kind.startswith("native-")
        name = "Execute fixture [fixture:default]"
        root = ".release-evidence/" + execution_id
        outputs = {
            "execution-log": {
                "path": root + "/execution.log",
                "sha256": files["execution-log"]["sha256"],
            }
        }
        role = "preparation" if kind == "package-preparation" else "results"
        if role in files:
            outputs[role] = {"path": role + ".xml", "sha256": files[role]["sha256"]}
        if native:
            mode = kind.removeprefix("native-")
            outputs["results"]["path"] = root + "/" + mode + "-results.xml"
            outputs["execution-log"]["path"] = root + "/" + mode + ".log"
        try:
            revision = checker().checkout_revision(checkout.encode())
        except ValueError:
            return
        common = {
            "schema_version": 1,
            "repository": "ykh09242/unity-mcp",
            "run_id": 123,
            "run_attempt": 2,
            "job_key": "fixture",
            "checkout_sha": revision,
            "execution_id": execution_id,
            "kind": kind,
            "step_name": name,
            "output_root": root,
            "record_type": "action-observation" if native else "subprocess-completion",
        }
        start = {
            **common,
            "event": "reserved" if native else "started",
            "emitter_step_name": "Reserve fixture [fixture:default]" if native else name,
            "expected_outputs": {key: pointer["path"] for key, pointer in outputs.items()},
        }
        end = {
            **common,
            "event": "observed" if native else "completed",
            "emitter_step_name": "Observe fixture [fixture:default]" if native else name,
            "outputs": outputs,
        }
        if native:
            start["observer_step_name"] = end["emitter_step_name"]
            end["runner_outcome"] = "success"
        else:
            end["exit_code"] = 0
        steps = []
        for number, step_name, begin, finish in (
            (
                (1, start["emitter_step_name"], "00", "01"),
                (2, name, "01", "02"),
                (3, end["emitter_step_name"], "03", "04"),
            )
            if native
            else [(1, name, "01", "02")]
        ):
            steps.append(
                {
                    "number": number,
                    "name": step_name,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": f"2026-10-16T12:00:{begin}Z",
                    "completed_at": f"2026-10-16T12:00:{finish}Z",
                }
            )
        job = json.loads((self.evidence.parent / files["job"]["path"]).read_bytes())
        job["steps"] = steps
        files["job"] = self.file("job", job)
        start_time = "00.5" if native else "01"
        end_time = "03" if native else "01.5"
        combined = (
            checkout
            + f"2026-10-16T12:00:{start_time}Z UNITY_MCP_EXECUTION "
            + json.dumps(start)
            + "\n"
        )
        if native:
            combined += (
                "2026-10-16T12:00:01Z ##[group]Run game-ci/unity-test-runner@" + "a" * 40 + "\n"
            )
            combined += "2026-10-16T12:00:01Z   artifactsPath: " + root + "\n"
        combined += "\n".join("2026-10-16T12:00:01Z " + line for line in body.splitlines()) + "\n"
        combined += f"2026-10-16T12:00:{end_time}Z UNITY_MCP_EXECUTION " + json.dumps(end) + "\n"
        files["log"] = self.file("log", combined)
        files["checkout"] = dict(files["log"])

    def check(self, notes: str | None = None) -> list[str]:
        return checker().check_release_notes(
            self.repo, self.package, notes or self.notes(), self.evidence
        )


def native_xml(result: str = "Passed") -> str:
    counts = {name: int(name == result) for name in ("Passed", "Failed", "Skipped", "Inconclusive")}
    return (
        f'<test-run total="1" passed="{counts["Passed"]}" failed="{counts["Failed"]}" '
        f'skipped="{counts["Skipped"]}" inconclusive="{counts["Inconclusive"]}" result="{result}">'
        f'<test-suite><test-case fullname="Fixture.NativeCase" result="{result}" /></test-suite></test-run>'
    )


def native_log(platform: str = "EditMode", exit_code: int = 0) -> str:
    return (
        "Built from '6000.0/staging' branch; Version is '6000.0.69f1 (fixture)'\n"
        f"COMMAND LINE ARGUMENTS:\nUnity.exe\n-runTests\n-testPlatform\n{platform}\n"
        f"Test run completed. Exiting with code {exit_code} (fixture).\n"
    )


def add_native(
    bundle: Bundle, entry: dict, result: str = "Passed", platform: str = "EditMode"
) -> None:
    entry["kind"] = "native-editmode" if platform == "EditMode" else "native-playmode"
    entry["files"]["results"] = bundle.file("results", native_xml(result))
    bundle.execution(entry, native_log(platform, int(result != "Passed")))
    bundle.index(entry)


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bundle:
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    for folder in (".claude/skills/unity-mcp-skill", "unity-mcp-skill"):
        directory = repo / folder
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("# Synthetic skill\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "skill content")
    skill = git(repo, "rev-parse", "HEAD")
    (repo / "Server").mkdir()
    (repo / "Server/pyproject.toml").write_text(
        '[project]\nname="ykh09242-unity-mcp-server"\nversion="1.2.0"\n', encoding="utf-8"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "server content")
    server = git(repo, "rev-parse", "HEAD")
    (repo / "MCPForUnity").mkdir()
    source = f"{BASE}/archive/{server}.zip#subdirectory=Server"
    (repo / "MCPForUnity/package.json").write_text(
        json.dumps(
            {
                "name": "com.ykh09242.unity-mcp",
                "version": "1.2.1",
                "mcpServerSource": source,
                "mcpSkillRevision": skill,
            }
        ),
        encoding="utf-8",
    )
    (repo / "manifest.json").write_text(
        json.dumps({"version": "1.2.1", "server": {"mcp_config": {"args": ["--from", source]}}}),
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "package selects earlier independent commits")
    result = Bundle(repo, tmp_path / "evidence.json", git(repo, "rev-parse", "HEAD"), server, skill)
    result.ci()
    return result


def test_valid_independent_components_use_committed_metadata_not_dirty_worktree(
    bundle: Bundle,
) -> None:
    # Given a prepared candidate; dirty metadata must not change the selected committed P.
    (bundle.repo / "MCPForUnity/package.json").write_text("{}", encoding="utf-8")
    # When validating the exact candidate, then the real independent commit inputs pass.
    assert bundle.check() == []


@pytest.mark.parametrize("platform", ["win", "osx", "linux"])
@pytest.mark.parametrize("profile", ["", "/editor", "/player", "/player-scenarios"])
def test_compiler_execution_accepts_emitted_platform_profiles(platform: str, profile: str) -> None:
    module = checker()
    log = (
        "2026-11-09T04:24:29Z Compiler      : /fixture/csc.dll\n"
        f"2026-11-09T04:24:30Z --- MCPForUnity.Runtime [{platform}{profile}] : "
        "42 sources, 20 refs ---\n"
    ).encode()
    assert module.check_execution(module.Kind.COMPILE, {"log": log}) == (True, False)


@pytest.mark.parametrize("profile", ["/editor", "/player", "/player-scenarios"])
def test_profiled_compiler_errors_remain_failed(profile: str) -> None:
    module = checker()
    log = (
        "Compiler      : /fixture/csc.dll\n"
        f"--- MCPForUnity.Runtime [win{profile}] : 42 sources, 20 refs ---\n"
        "Fixture.cs(1,1): error CS0103: Fixture symbol does not exist\n"
    ).encode()
    assert module.check_execution(module.Kind.COMPILE, {"log": log}) == (False, True)


@pytest.mark.parametrize(
    "stage",
    [
        "MCPForUnity.Runtime [webgl/editor] : 42 sources, 20 refs",
        "MCPForUnity.Runtime [win/unknown] : 42 sources, 20 refs",
        "MCPForUnity.Runtime [win/editor/extra] : 42 sources, 20 refs",
        "MCPForUnity.Runtime [win/editor] : 0 sources, 20 refs",
        "MCPForUnity.Runtime [win/editor] : 42 sources, 0 refs",
        "Unrelated.Runtime [win/editor] : 42 sources, 20 refs",
    ],
)
def test_unsupported_or_empty_compiler_stages_are_rejected(stage: str) -> None:
    module = checker()
    with pytest.raises(module.EvidenceError, match="actual compiler-stage execution"):
        module.check_execution(
            module.Kind.COMPILE,
            {"log": f"Compiler      : /fixture/csc.dll\n--- {stage} ---\n".encode()},
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("mcpSkillRevision", None),
        ("mcpSkillRevision", "0" * 40),
        ("mcpSkillRevision", "a" * 39),
        ("mcpServerSource", "https://example.invalid/beta"),
    ],
)
def test_missing_malformed_pins_are_rejected(bundle: Bundle, field: str, value: str | None) -> None:
    # Given a committed malformed pin.
    path = bundle.repo / "MCPForUnity/package.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    git(bundle.repo, "add", ".")
    git(bundle.repo, "commit", "-m", "malformed pin fixture")
    bundle.package = git(bundle.repo, "rev-parse", "HEAD")
    # When checking, then failure identifies the pin rather than executing fallback.
    assert field in "\n".join(bundle.check())


@pytest.mark.parametrize("old", [True, False])
def test_old_head_or_pr_merge_checkout_cannot_be_current_pass(bundle: Bundle, old: bool) -> None:
    # Given matching API head but an actual checkout of another revision.
    bundle.ci(revision=bundle.server if old else "a" * 40)
    # When notes claim the current candidate, then actual checkout identity wins.
    assert "checkout" in "\n".join(bundle.check()).lower()


@pytest.mark.parametrize("field", ["source", "test", "meta", "config", "empty"])
def test_deleted_or_empty_partial_maps_never_authorize_current_pass(
    bundle: Bundle, field: str
) -> None:
    # Given an apparently matching partial source map with an omitted required category.
    values = {name: "a" * 64 for name in ("source", "test", "meta", "config")}
    if field == "empty":
        values.clear()
    else:
        values.pop(field)
    inputs = bundle.file("inputs", values)
    entry = {
        "origin": "local-native",
        "kind": "native-editmode",
        "public_url": "https://example.invalid/retained/native",
        "files": {
            "inputs": inputs,
            "invocation": bundle.file(
                "invocation", {"args": ["-runTests", "-testPlatform", "EditMode"]}
            ),
            "log": bundle.file("log", native_log()),
            "results": bundle.file("results", native_xml()),
        },
    }
    bundle.index(entry)
    # When passed/current is claimed, then even a remaining matching map cannot prove completeness.
    assert bundle.check(
        bundle.notes(
            "native-editmode", tested="partial:" + inputs["sha256"], url=entry["public_url"]
        )
    )


def test_supported_local_native_is_historical_only_with_actual_records(bundle: Bundle) -> None:
    # Given real-format local records and a nonempty, explicitly incomplete identity map.
    inputs = bundle.file("inputs", {"Editor/Fixture.cs": "a" * 64})
    url = "https://example.invalid/retained/native"
    bundle.index(
        {
            "origin": "local-native",
            "kind": "native-editmode",
            "public_url": url,
            "files": {
                "inputs": inputs,
                "invocation": bundle.file(
                    "invocation", {"args": ["-runTests", "-testPlatform", "EditMode"]}
                ),
                "log": bundle.file("log", native_log()),
                "results": bundle.file("results", native_xml()),
            },
        }
    )
    # When described as historical, then no completeness/current-release pass is inferred.
    assert (
        bundle.check(
            bundle.notes("native-editmode", "historical", "partial:" + inputs["sha256"], url=url)
        )
        == []
    )


@pytest.mark.parametrize("state", ["Skipped", "Inconclusive", "Failed"])
def test_nonpassing_native_cases_cannot_be_passed(bundle: Bundle, state: str) -> None:
    # Given a green outer workflow and actual nonpassing native XML.
    entry = bundle.ci()
    add_native(bundle, entry, state)
    # When a native pass is declared, then actual records reject it.
    assert bundle.check(bundle.notes("native-editmode"))


def test_successful_native_whole_run_is_supported(bundle: Bundle) -> None:
    # Given a successful job, actual Editor invocation/log and coherent NUnit records.
    entry = bundle.ci()
    add_native(bundle, entry)
    # When the exact candidate is declared, then that bounded native record is valid.
    assert bundle.check(bundle.notes("native-editmode")) == []


def test_skipped_job_is_not_inferred_from_green_workflow(bundle: Bundle) -> None:
    # Given a skipped native job inside a successful workflow.
    bundle.ci("native-editmode", "skipped")
    # When honestly labelled skipped with no tested input, then validation succeeds.
    assert bundle.check(bundle.notes("native-editmode", "skipped", "none")) == []


@pytest.mark.parametrize("value", ["passed 60/60", "Passed", "success", "passed;0skip"])
def test_result_cell_is_exact_enum(bundle: Bundle, value: str) -> None:
    # Given a result label outside the frozen grammar; when checked, then reject it.
    assert bundle.check(bundle.notes(result=value))


@pytest.mark.parametrize(
    "tested",
    ["commit:abc", "beta", "commit:" + "0" * 40, "commit:" + "A" * 40, "unity@" + "a" * 40],
)
def test_tested_input_has_exact_grammar(bundle: Bundle, tested: str) -> None:
    # Given an abbreviated/floating/compound input; when checked, then reject it.
    assert bundle.check(bundle.notes(tested=tested))


@pytest.mark.parametrize("role", ["workflow", "job", "checkout", "log"])
def test_each_required_role_is_mandatory(bundle: Bundle, role: str) -> None:
    # Given a supported record with one required saved file removed.
    entry = bundle.ci()
    del entry["files"][role]
    bundle.index(entry)
    # When checked, then no missing receipt gets silently replaced by a generic boolean.
    assert role in "\n".join(bundle.check())


@pytest.mark.parametrize("kind", ["manual", "live-e2e"])
@pytest.mark.parametrize("status", ["passed", "historical", "not-run"])
def test_unsupported_executions_cannot_be_relabelled(
    bundle: Bundle, kind: str, status: str
) -> None:
    # Given an unsupported reader, including attempts to hide it behind policy/history.
    bundle.index(
        {
            "origin": "policy",
            "kind": kind,
            "public_url": "https://example.invalid/policy",
            "files": {},
            "reason": "Actual unsupported execution exists elsewhere",
        }
    )
    # When placed in the checked block, then reject every result spelling.
    assert bundle.check(bundle.notes(kind, status, "none", url="https://example.invalid/policy"))


def test_policy_not_run_cannot_carry_executed_records(bundle: Bundle) -> None:
    # Given a policy row pretending an executed record is not-run.
    entry = bundle.ci()
    entry.update(origin="policy", reason="Not run")
    bundle.index(entry)
    # When checked, then extra executed files are rejected.
    assert bundle.check(bundle.notes(result="not-run", tested="none"))


@pytest.mark.parametrize(
    "tamper",
    ["digest", "url", "duplicate-json", "missing-block", "extra-role", "fake-verdict", "counts"],
)
def test_malformed_or_mismatched_evidence_is_rejected(bundle: Bundle, tamper: str) -> None:
    # Given a single inconsistent record or grammar boundary.
    entry = bundle.ci()
    notes = bundle.notes()
    match tamper:
        case "digest":
            entry["files"]["log"]["sha256"] = "b" * 64
        case "url":
            notes = notes.replace("/job/456", "/job/999")
        case "duplicate-json":
            bundle.evidence.write_text(
                '{"schema_version":1,"schema_version":1,"evidence":{}}', encoding="utf-8"
            )
        case "missing-block":
            notes = notes.replace("<!-- release-evidence:end -->", "")
        case "extra-role":
            entry["files"]["universal"] = bundle.file("unknown", {})
        case "fake-verdict":
            entry["passed"] = True
        case "counts":
            add_native(bundle, entry)
            entry["files"]["results"] = bundle.file(
                "results", native_xml().replace('passed="1"', 'passed="2"')
            )
            notes = bundle.notes("native-editmode")
    if tamper != "duplicate-json":
        bundle.index(entry)
    # When checked, then the mismatch fails closed.
    assert bundle.check(notes)


def test_cli_read_only_boundary_has_no_network_or_payload_execution(bundle: Bundle) -> None:
    # Given shell-like prose outside the checked block and an ordinary CLI call.
    notes = bundle.evidence.parent / "notes.md"
    marker = bundle.evidence.parent / "never-created"
    notes.write_text(bundle.notes() + f"\n$(touch {marker})\n", encoding="utf-8")
    # When the real CLI validates the saved files, then it succeeds without interpreting prose.
    assert (
        checker().main(
            [
                "--repo",
                str(bundle.repo),
                "--package-commit",
                bundle.package,
                "--notes",
                str(notes),
                "--evidence-index",
                str(bundle.evidence),
            ]
        )
        == 0
    )
    assert not marker.exists()


def test_git_replacement_objects_cannot_substitute_selected_immutable_commit(
    bundle: Bundle,
) -> None:
    # Given a local replace ref pointing the selected P at a different metadata commit.
    for relative in ("MCPForUnity/package.json", "manifest.json"):
        path = bundle.repo / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = "9.9.9"
        path.write_text(json.dumps(data), encoding="utf-8")
    git(bundle.repo, "add", ".")
    git(bundle.repo, "commit", "-m", "replacement fixture")
    git(bundle.repo, "replace", bundle.package, git(bundle.repo, "rev-parse", "HEAD"))
    # When P is validated, then its real immutable object, not its replacement, is read.
    assert bundle.check() == []


def test_checkout_hash_outside_source_checkout_context_is_rejected(bundle: Bundle) -> None:
    # Given a full hash/log command after source checkout has already removed its auth.
    entry = bundle.ci()
    entry["files"]["checkout"] = bundle.file(
        "checkout",
        "\n".join(
            [
                "2026-10-16T12:00:00Z ##[group]Run actions/checkout@fixture",
                "2026-10-16T12:00:00Z   repository: ykh09242/unity-mcp",
                "2026-10-16T12:00:00Z ##[group]Checking out the ref",
                "2026-10-16T12:00:00Z ##[group]Removing auth",
                "2026-10-16T12:00:00Z [command]/usr/bin/git log -1 --format=%H",
                "2026-10-16T12:00:00Z " + bundle.package,
            ]
        ),
    )
    bundle.execution(
        entry,
        "Compiler      : /fixture/csc.dll\n--- MCPForUnity.Editor [win] : 42 sources, 20 refs ---\n",
    )
    bundle.index(entry)
    # When checked, then another step's hash is not promoted to actual source identity.
    assert bundle.check()


@pytest.mark.parametrize("mode", ["python", "package-preparation", "native-playmode"])
def test_other_supported_actual_record_formats(bundle: Bundle, mode: str) -> None:
    # Given actual-format saved records for each explicitly supported reader.
    entry = bundle.ci(mode)
    match mode:
        case "python":
            bundle.execution(entry, "platform win32 -- Python 3.14.0, pytest-8.0\n")
            entry["files"]["results"] = bundle.file(
                "results",
                '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="tests.fixture" name="case" /></testsuite></testsuites>',
            )
        case "package-preparation":
            bundle.execution(entry, "Prepared Unity package profile fixture for 6000.0.69f1\n")
            entry["files"]["preparation"] = bundle.file(
                "preparation",
                {
                    "unityVersion": "6000.0.69f1",
                    "profile": "base",
                    "packages": [
                        {
                            "name": "com.unity.fixture",
                            "version": "1.0.0",
                            "source": "builtin",
                            "minimumUnity": None,
                            "dependencies": {},
                            "integrity": {"archiveSha256": None, "packageMetadataSha256": "a" * 64},
                        }
                    ],
                },
            )
        case "native-playmode":
            add_native(bundle, entry, platform="PlayMode")
    bundle.index(entry)
    # When checked, then the exact declared record kind is accepted.
    assert bundle.check(bundle.notes(mode)) == []


@pytest.mark.parametrize(
    ("published", "accepted"),
    [
        ("1 passed, 1 skipped", True),
        ("passed", False),
        ("2 passed, 1 skipped", False),
        ("0 passed, 2 skipped", False),
    ],
)
def test_mixed_python_run_discloses_verified_counts(
    bundle: Bundle, published: str, accepted: bool
) -> None:
    entry = bundle.ci("python")
    bundle.execution(entry, "Python 3.14.0 pytest\n")
    entry["files"]["results"] = bundle.file(
        "results",
        '<testsuite tests="2" failures="0" errors="0" skipped="1">'
        '<testcase name="common"/>'
        '<testcase name="windows"><skipped message="Windows only"/></testcase></testsuite>',
    )
    bundle.index(entry)
    issues = bundle.check(bundle.notes("python", result=published))
    assert (issues == []) is accepted


def test_mixed_python_row_cannot_hide_errors_or_an_entirely_skipped_run():
    module = checker()
    skipped = b'<testcase name="windows"><skipped/></testcase>'
    all_skipped = (
        b'<testsuite tests="1" errors="0" failures="0" skipped="1">' + skipped + b"</testsuite>"
    )
    with pytest.raises(module.EvidenceError, match="counts differ"):
        module.junit_result(all_skipped, (1, 1))
    mixed_error = (
        b'<testsuite tests="3" errors="1" failures="0" skipped="1">'
        b'<testcase name="common"/><testcase name="breaks"><error/></testcase>'
        + skipped
        + b"</testsuite>"
    )
    assert module.junit_result(mixed_error, (1, 1)) == (False, True)


def test_duplicate_junit_case_records_cannot_fake_execution_counts(bundle: Bundle) -> None:
    # Given two duplicate case identities pretending to be two passing cases.
    entry = bundle.ci("python")
    bundle.execution(entry, "Python 3.14.0 pytest\n")
    entry["files"]["results"] = bundle.file(
        "results",
        '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0"><testcase classname="tests.fixture" name="case" /><testcase classname="tests.fixture" name="case" /></testsuite></testsuites>',
    )
    bundle.index(entry)
    # When checked, then duplicate actual-case attribution is rejected.
    assert bundle.check(bundle.notes("python"))


def test_nonfinite_json_is_not_raw_rest_evidence(bundle: Bundle) -> None:
    # Given a non-standard JSON token in a purported raw workflow record.
    entry = bundle.ci()
    path = bundle.evidence.parent / entry["files"]["workflow"]["path"]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["unrelated"] = float("nan")
    entry["files"]["workflow"] = bundle.file("workflow", value)
    bundle.index(entry)
    # When checked, then permissive Python JSON extensions cannot enter the boundary.
    assert bundle.check()


def test_empty_historical_input_map_is_rejected(bundle: Bundle) -> None:
    # Given the required actual native records but no source identity history.
    inputs = bundle.file("inputs", {})
    url = "https://example.invalid/retained/native"
    bundle.index(
        {
            "origin": "local-native",
            "kind": "native-editmode",
            "public_url": url,
            "files": {
                "inputs": inputs,
                "invocation": bundle.file(
                    "invocation", {"args": ["-runTests", "-testPlatform", "EditMode"]}
                ),
                "log": bundle.file("log", native_log()),
                "results": bundle.file("results", native_xml()),
            },
        }
    )
    # When labelled historical, then an empty map still does not establish its stated identity.
    assert bundle.check(
        bundle.notes("native-editmode", "historical", "partial:" + inputs["sha256"], url=url)
    )


@pytest.mark.parametrize(
    "path", ["../outside.txt", "C:/outside.txt", "https://example.invalid/file"]
)
def test_evidence_pointers_cannot_read_outside_local_index(bundle: Bundle, path: str) -> None:
    # Given an untrusted file pointer trying to leave the explicit local evidence root.
    entry = bundle.ci()
    entry["files"]["log"]["path"] = path
    bundle.index(entry)
    # When checked, then the pointer is rejected before any external read or network request.
    assert bundle.check()


def test_honest_policy_not_run_has_no_execution_files(bundle: Bundle) -> None:
    # Given an unexecuted supported native scenario with an explicit policy reason.
    url = "https://example.invalid/retained/policy"
    bundle.index(
        {
            "origin": "policy",
            "kind": "native-editmode",
            "public_url": url,
            "files": {},
            "reason": "Native scenario was not run",
        }
    )
    # When labelled not-run with no tested input, then no fabricated execution is needed.
    assert bundle.check(bundle.notes("native-editmode", "not-run", "none", url=url)) == []


def test_actual_earlier_ci_execution_can_remain_historical(bundle: Bundle) -> None:
    # Given an actual checkout of an earlier source despite a newer workflow API head.
    bundle.ci(revision=bundle.server)
    # When its original identity is preserved as historical, then it supplies no current pass.
    assert bundle.check(bundle.notes(result="historical", tested="commit:" + bundle.server)) == []


def test_current_execution_requires_originating_association(bundle: Bundle) -> None:
    # Prior-format compile output is real execution, but has no step/run-bound output record.
    entry = json.loads(bundle.evidence.read_bytes())["evidence"]["proof"]
    entry.pop("execution_id")
    entry["files"].pop("execution-log")
    bundle.evidence.write_text(json.dumps({"schema_version": 1, "evidence": {"proof": entry}}))
    assert bundle.check(), "Markerless evidence must not establish a current execution"


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", 999),
        ("run_attempt", 1),
        ("job_key", "another-job"),
        ("step_name", "Other step [fixture:default]"),
        ("checkout_sha", "b" * 40),
        ("emitter_step_name", "Later finalizer [fixture:default]"),
    ],
)
def test_execution_record_must_match_real_run_attempt_step_and_checkout(bundle, field, value):
    entry = bundle.ci()
    path = bundle.evidence.parent / entry["files"]["log"]["path"]
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if "UNITY_MCP_EXECUTION " in line:
            prefix, raw = line.split("UNITY_MCP_EXECUTION ", 1)
            record = json.loads(raw)
            record[field] = value
            lines[index] = prefix + "UNITY_MCP_EXECUTION " + json.dumps(record)
    entry["files"]["log"] = bundle.file("log", "\n".join(lines))
    entry["files"]["checkout"] = dict(entry["files"]["log"])
    bundle.index(entry)
    assert bundle.check()


def test_markerless_legacy_is_explicit_historical_only(bundle):
    entry = bundle.ci()
    entry.pop("execution_id")
    entry["files"].pop("execution-log")
    entry["association_limit"] = "Execution-to-output association was not recorded."
    bundle.index(entry)
    notes = bundle.notes(result="historical").replace(
        "Fixture capability", "Fixture capability (legacy unbound)"
    )
    assert bundle.check(notes) == []
    assert bundle.check(bundle.notes())
    assert bundle.check(bundle.notes(result="historical"))


@pytest.mark.parametrize("kind", ["python", "package-preparation", "native-editmode"])
def test_attached_outputs_copied_from_another_execution_are_rejected(bundle, kind):
    entry = bundle.ci(kind)
    if kind == "native-editmode":
        add_native(bundle, entry)
    elif kind == "python":
        bundle.execution(entry, "Python 3.14 pytest\n")
        entry["files"]["results"] = bundle.file(
            "results",
            '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite>',
        )
        bundle.index(entry)
    else:
        entry["files"]["preparation"] = bundle.file(
            "preparation",
            {
                "unityVersion": "6000.0.69f1",
                "profile": "fixture",
                "packages": [
                    {
                        "name": "com.fixture.test",
                        "version": "1.0.0",
                        "source": "registry",
                        "integrity": {"packageMetadataSha256": "a" * 64},
                    }
                ],
            },
        )
        bundle.index(entry)
    role = "preparation" if kind == "package-preparation" else "results"
    raw = (bundle.evidence.parent / entry["files"][role]["path"]).read_text()
    entry["files"][role] = bundle.file(role, raw + "\n")
    bundle.index(entry)
    assert bundle.check(bundle.notes(kind))


def test_native_observation_cannot_claim_originating_step_or_reused_output_root(bundle):
    entry = bundle.ci()
    add_native(bundle, entry)
    path = bundle.evidence.parent / entry["files"]["log"]["path"]
    original = path.read_text()
    for old, new in [
        ("Observe fixture [fixture:default]", "Execute fixture [fixture:default]"),
        ("  artifactsPath: .release-evidence/", "  artifactsPath: artifacts/old/"),
    ]:
        raw = original.replace(old, new)
        entry["files"]["log"] = bundle.file("log", raw)
        entry["files"]["checkout"] = dict(entry["files"]["log"])
        bundle.index(entry)
        assert bundle.check(bundle.notes("native-editmode"))


def test_compiler_only_execution_cannot_be_relabelled_preparation(bundle):
    entry = bundle.ci("compile")
    entry["kind"] = "package-preparation"
    entry["files"]["preparation"] = bundle.file(
        "preparation",
        {
            "unityVersion": "6000.0.69f1",
            "profile": "fixture",
            "packages": [
                {
                    "name": "com.fixture.test",
                    "version": "1.0.0",
                    "source": "registry",
                    "integrity": {"packageMetadataSha256": "a" * 64},
                }
            ],
        },
    )
    bundle.index(entry)
    assert bundle.check(bundle.notes("package-preparation"))


def test_failed_native_raw_outcome_is_not_promoted_by_continue_on_error(bundle):
    entry = bundle.ci(conclusion="failure")
    add_native(bundle, entry, result="Failed")
    raw = (bundle.evidence.parent / entry["files"]["log"]["path"]).read_text()
    raw = raw.replace('"runner_outcome": "success"', '"runner_outcome": "failure"')
    entry["files"]["log"] = bundle.file("log", raw)
    entry["files"]["checkout"] = dict(entry["files"]["log"])
    bundle.index(entry)
    # Policy can keep the runner step green; the raw outcome and actual NUnit/log still fail.
    assert bundle.check(bundle.notes("native-editmode", result="failed")) == []
    assert bundle.check(bundle.notes("native-editmode", result="passed"))


@pytest.mark.parametrize(
    "damage", ["missing-completion", "outside-step", "wrong-job-step", "old-attempt-job"]
)
def test_completion_requires_actual_step_and_attempt_boundaries(bundle, damage):
    entry = bundle.ci()
    if damage in {"missing-completion", "outside-step"}:
        raw = (bundle.evidence.parent / entry["files"]["log"]["path"]).read_text()
        if damage == "missing-completion":
            raw = "\n".join(line for line in raw.splitlines() if '"event": "completed"' not in line)
        else:
            raw = raw.replace("12:00:01.5Z", "12:00:04Z")
        entry["files"]["log"] = bundle.file("log", raw)
        entry["files"]["checkout"] = dict(entry["files"]["log"])
    else:
        job = json.loads((bundle.evidence.parent / entry["files"]["job"]["path"]).read_bytes())
        if damage == "wrong-job-step":
            job["steps"][0]["name"] = "Execute another row [another:default]"
        else:
            workflow = json.loads(
                (bundle.evidence.parent / entry["files"]["workflow"]["path"]).read_bytes()
            )
            workflow["run_started_at"] = "2026-10-16T12:01:00Z"
            entry["files"]["workflow"] = bundle.file("workflow", workflow)
        entry["files"]["job"] = bundle.file("job", job)
    bundle.index(entry)
    assert bundle.check()


def test_association_parser_bounds_only_marker_payloads():
    checker()
    from release_association import records

    unrelated = b"2026-10-16T12:00:02Z unrelated " + b"x" * (2 * 1024 * 1024)
    assert records(unrelated) == []
    oversized = b"2026-10-16T12:00:02Z UNITY_MCP_EXECUTION " + b"x" * 32769
    with pytest.raises(ValueError, match="exceeds 32 KiB"):
        records(oversized)


def test_only_nonfetching_nonreplaced_explicit_git_object_reads_are_used(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a valid bundle and a recording guard on the helper's sole subprocess boundary.
    module = checker()
    actual_run = subprocess.run
    commands = []

    def guarded(command, **kwargs):
        assert command[:4] == [
            "git",
            "--no-lazy-fetch",
            "--no-replace-objects",
            "--no-optional-locks",
        ]
        assert command[4:6] == ["-C", str(bundle.repo)]
        assert command[6] in ("cat-file", "show", "ls-tree")
        assert not kwargs.get("shell", False)
        commands.append(command)
        return actual_run(command, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", guarded)
    # When validating notes, then no command derived from prose, checkout or fetch is executed.
    assert (
        module.check_release_notes(bundle.repo, bundle.package, bundle.notes(), bundle.evidence)
        == []
    )
    assert commands


@pytest.mark.parametrize("old_execution", [False, True])
def test_checkout_cannot_be_grafted_onto_another_execution_log(
    bundle: Bundle, old_execution: bool
) -> None:
    # Given a current-P checkout pointer plus an independent old/same-source execution log.
    entry = bundle.ci()
    original = (bundle.evidence.parent / entry["files"]["checkout"]["path"]).read_text(
        encoding="utf-8"
    )
    if old_execution:
        original = original.replace(bundle.package, bundle.server)
    execution = (
        original
        + "\n2026-10-16T12:00:01Z Compiler      : /fixture/csc.dll\n2026-10-16T12:00:01Z --- MCPForUnity.Editor [win] : 42 sources, 20 refs ---\n"
    )
    entry["files"]["log"] = bundle.file("separate-execution", execution)
    bundle.index(entry)
    # When the pointers are mixed, then notes cannot claim a current release pass.
    assert bundle.check()


def test_real_gh_prefix_bom_form_is_supported(bundle: Bundle) -> None:
    # Given the BOM-after-tab prefix actually found in the saved PR55 raw job log.
    entry = bundle.ci()
    original = (bundle.evidence.parent / entry["files"]["checkout"]["path"]).read_text(
        encoding="utf-8"
    )
    combined = (
        original.rstrip("\n")
        + "\n2026-10-16T12:00:01Z Compiler      : /fixture/csc.dll\n2026-10-16T12:00:01Z --- MCPForUnity.Editor [win] : 42 sources, 20 refs ---\n"
    )
    combined = "Compile fixture\tUNKNOWN STEP\t\ufeff" + combined
    entry["files"]["checkout"] = bundle.file("checkout", combined)
    entry["files"]["log"] = bundle.file("log", combined)
    bundle.index(entry)
    # When checked, then this real encoding wrapper does not erase the actual tested SHA.
    assert bundle.check() == []


def test_legitimate_pr_checkout_remains_authoritative_over_trigger_head(bundle: Bundle) -> None:
    # Given a PR run whose trigger head differs from its actual selected checkout.
    entry = bundle.ci()
    path = bundle.evidence.parent / entry["files"]["workflow"]["path"]
    workflow = json.loads(path.read_text(encoding="utf-8"))
    workflow.update(head_sha=bundle.server, event="pull_request")
    entry["files"]["workflow"] = bundle.file("workflow", workflow)
    bundle.index(entry)
    # When one full job log binds checkout and execution, then API head inequality is not a refusal.
    assert bundle.check() == []


def test_compiler_error_cannot_be_hidden_by_green_job_metadata(bundle: Bundle) -> None:
    # Given an actual compiler error even though the saved enclosing job says success.
    entry = bundle.ci()
    bundle.execution(
        entry,
        "Compiler      : /fixture/csc.dll\n--- MCPForUnity.Editor [win] : 42 sources, 20 refs ---\nFixture.cs(1,1): error CS0619: obsolete fixture API\n",
    )
    bundle.index(entry)
    # When a passed compile row is declared, then the actual diagnostic prevents it.
    assert bundle.check()


def test_utf16_entity_declaration_cannot_bypass_xml_boundary() -> None:
    # Given an entity-bearing UTF-16 document hiding declaration bytes behind NULs.
    document = (
        '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE test-run [<!ENTITY evidence "Fixture.NativeCase">]>'
        + native_xml().replace('fullname="Fixture.NativeCase"', 'fullname="&evidence;"')
    )
    # When parsed as saved native evidence, then unsupported encoding/declarations fail closed.
    with pytest.raises(ValueError):
        checker().native_result(document.encode("utf-16"))


def multiline_checkout_log(prefix: str = "") -> bytes:
    stamp = "2026-10-25T10:55:01.7309637Z "
    records = [
        stamp + "##[group]Run actions/checkout@fixture",
        stamp + "  repository: ykh09242/unity-mcp",
        stamp + "##[group]Checking out the ref",
        stamp + "[command]/usr/bin/git log -1 --format=%H",
        stamp + "a" * 40,
        stamp + "##[group]Removing auth",
        stamp + "##[endgroup]",
        stamp + "##[group]Run astral-sh/setup-uv@fixture",
        stamp + "with:",
        stamp + "  cache-dependency-glob: Server/pyproject.toml",
        "Server/uv.lock",
        "",
        stamp + "  save-cache: false",
        stamp + "##[endgroup]",
        stamp + "##[group]Run actions/upload-artifact@fixture",
        stamp + "with:",
        stamp + "  path: Server/test-results.xml",
        "Server/test-results-http.xml",
        "Server/test-worker-timings.json",
        "Server/coverage.xml",
        "Server/htmlcov/",
        "",
        stamp + "  retention-days: 7",
        stamp + "##[endgroup]",
    ]
    return ("\n".join(prefix + line for line in records) + "\n").encode()


@pytest.mark.parametrize("prefix", ["", "Python tests\tUNKNOWN STEP\t"])
@pytest.mark.parametrize("bom", [False, True])
def test_checkout_accepts_recorded_multiline_action_path_inputs(prefix: str, bom: bool) -> None:
    # GitHub emits one timestamp for a multiline action input, even in full job logs.
    raw = multiline_checkout_log(prefix)
    if bom:
        raw = raw.replace(b"2026-10-25", "\ufeff2026-10-25".encode(), 1)
    assert checker().checkout_revision(raw) == "a" * 40


@pytest.mark.parametrize(
    "old,new",
    [
        ("Server/uv.lock", "[command]/usr/bin/git log -1 --format=%H"),
        ("Server/uv.lock", "##[group]Run actions/checkout@injected"),
        ("Server/uv.lock", "b" * 40),
        ("Server/uv.lock", "unattributed output"),
        ("Server/uv.lock", "../outside.txt"),
        ("Server/uv.lock", "/absolute/file"),
        ("Server/uv.lock", "Server/\tuv.lock"),
        ("Server/uv.lock", "Server/\x1buv.lock"),
        ("Server/uv.lock\n\n", "Server/uv.lock\n\nServer/extra.lock\n"),
        ("astral-sh/setup-uv@fixture", "other/setup-uv@fixture"),
        ("cache-dependency-glob:", "unknown-input:"),
        ("Z with:", "Z env:"),
        ("Server/uv.lock", "2026-10-25T10:55:02Z ##[group]Run actions/checkout@second"),
        ("Z " + "a" * 40, "Z Server/input\n" + "a" * 40),
    ],
)
def test_multiline_inputs_cannot_hide_checkout_evidence_or_arbitrary_output(
    old: str, new: str
) -> None:
    raw = multiline_checkout_log().replace(old.encode(), new.encode())
    with pytest.raises(ValueError):
        checker().checkout_revision(raw)


def test_multiline_input_cannot_cross_gh_job_or_step_wrapper() -> None:
    raw = multiline_checkout_log("Python tests\tUNKNOWN STEP\t").replace(
        b"Python tests\tUNKNOWN STEP\tServer/uv.lock",
        b"Other job\tUNKNOWN STEP\tServer/uv.lock",
    )
    with pytest.raises(ValueError):
        checker().checkout_revision(raw)


@pytest.mark.parametrize("position", ["before", "after"])
def test_unmatched_path_outside_action_input_is_rejected(position: str) -> None:
    raw = multiline_checkout_log()
    raw = b"Server/extra.lock\n" + raw if position == "before" else raw + b"Server/extra.lock\n"
    with pytest.raises(ValueError):
        checker().checkout_revision(raw)
