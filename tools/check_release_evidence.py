"""Check a prepared fork release block using local commits and saved actual records.

No network, checkout, test execution or publication is performed. See the maintainer
release guide for the deliberately limited table and evidence formats.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib
from typing import Final, Never, TypeAlias
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET


Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
JsonMap: TypeAlias = dict[str, Json]
BASE: Final = "https://github.com/ykh09242/unity-mcp"
START: Final = "<!-- release-evidence:start -->"
END: Final = "<!-- release-evidence:end -->"
COMPONENT_HEADER: Final = ("Component", "Version", "Immutable input")
CHECK_HEADER: Final = (
    "Capability / scope",
    "Component",
    "Verification kind",
    "Actual result",
    "Tested input",
    "Evidence",
)
SKILLS: Final = (".claude/skills/unity-mcp-skill", "unity-mcp-skill")
MAX_FILE: Final = 64 * 1024 * 1024
LOG_LINE: Final = re.compile(r"(?:[^\n]*\t)?\ufeff?\d{4}-\d\d-\d\dT\S+Z (.*)")


class Kind(str, Enum):
    PYTHON = "python"
    COMPILE = "compile"
    PREPARATION = "package-preparation"
    EDIT = "native-editmode"
    PLAY = "native-playmode"


class Result(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_RUN = "not-run"
    HISTORICAL = "historical"


class Origin(str, Enum):
    CI = "github-actions"
    LOCAL = "local-native"
    POLICY = "policy"


class EvidenceError(ValueError):
    """A supplied record cannot support its declared release row."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def fail(message: str) -> Never:
    raise EvidenceError(message)


def digest(value: str, length: int) -> bool:
    return re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None and any(
        c != "0" for c in value
    )


def json_pairs(pairs: list[tuple[str, Json]]) -> JsonMap:
    result: JsonMap = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def mapping(value: Json, label: str) -> JsonMap:
    match value:
        case dict():
            return value
        case _:
            return fail(f"{label} must be an object")


def text(value: Json, label: str) -> str:
    match value:
        case str() if value.strip():
            return value
        case _:
            return fail(f"{label} must be a nonempty string")


def integer(value: Json, label: str) -> int:
    require(type(value) is int and value >= 0, f"{label} must be a nonnegative integer")
    return value


def load_json(data: bytes) -> JsonMap:
    return mapping(
        json.loads(
            data,
            object_pairs_hook=json_pairs,
            parse_constant=lambda value: fail("Non-standard JSON constant: " + value),
        ),
        "JSON document",
    )


def read_bytes(path: Path) -> bytes:
    require(
        path.is_file() and not path.is_symlink(), f"Missing or linked evidence file: {path.name}"
    )
    require(path.stat().st_size <= MAX_FILE, f"Evidence file exceeds 64 MiB: {path.name}")
    return path.read_bytes()


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "--no-lazy-fetch",
            "--no-replace-objects",
            "--no-optional-locks",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    require(
        result.returncode == 0,
        "Required local Git object/path unavailable; provide its history. No fetch attempted.",
    )
    return result.stdout


def committed(repo: Path, revision: str, path: str) -> bytes:
    return git(repo, "show", f"{revision}:{path}")


def skill_files(repo: Path, revision: str, folder: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for record in git(repo, "ls-tree", "-rz", "--full-tree", revision, "--", folder).split(b"\0"):
        if not record:
            continue
        metadata, raw = record.split(b"\t", 1)
        mode, kind, _ = metadata.split(b" ")
        path = raw.decode("utf-8")
        require(
            mode in (b"100644", b"100755") and kind == b"blob",
            "Skill source must be a regular committed file",
        )
        result[path.removeprefix(folder + "/")] = committed(repo, revision, path).replace(
            b"\r\n", b"\n"
        )
    require("SKILL.md" in result, f"mcpSkillRevision skill tree lacks SKILL.md: {folder}")
    return result


@dataclass(frozen=True, slots=True)
class Components:
    package: str
    server: str
    skill: str
    unity_version: str
    server_version: str
    server_url: str

    def revision(self, name: str) -> str:
        return {"unity": self.package, "server": self.server, "skill": self.skill}[name]


def components(repo: Path, package_commit: str) -> Components:
    require(
        digest(package_commit, 40), "package commit must be a nonzero full lowercase 40-hex SHA"
    )
    require(
        git(repo, "cat-file", "-t", package_commit).strip() == b"commit",
        "Package input is not a commit",
    )
    package = load_json(committed(repo, package_commit, "MCPForUnity/package.json"))
    require(package.get("name") == "com.ykh09242.unity-mcp", "Wrong Unity package identity")
    unity_version = text(package.get("version"), "package version")
    server_url = text(package.get("mcpServerSource"), "mcpServerSource")
    source = re.fullmatch(
        re.escape(BASE) + r"/archive/([0-9a-f]{40})\.zip#subdirectory=Server", server_url
    )
    require(
        source is not None and digest(source[1], 40),
        "mcpServerSource must be a canonical full immutable archive URL",
    )
    server = source[1]
    skill = text(package.get("mcpSkillRevision"), "mcpSkillRevision")
    require(digest(skill, 40), "mcpSkillRevision must be a nonzero full lowercase 40-hex commit")
    for revision in (server, skill):
        require(
            git(repo, "cat-file", "-t", revision).strip() == b"commit",
            "Selected pin is not an available local commit",
        )
    manifest = load_json(committed(repo, package_commit, "manifest.json"))
    require(manifest.get("version") == unity_version, "Manifest/package version mismatch")
    config = mapping(
        mapping(manifest.get("server"), "manifest server").get("mcp_config"), "manifest mcp_config"
    )
    args = config.get("args")
    require(
        isinstance(args, list) and args.count("--from") == 1, "Manifest requires exactly one --from"
    )
    position = args.index("--from") + 1
    require(
        position < len(args) and args[position] == server_url,
        "Manifest --from differs from mcpServerSource",
    )
    project = mapping(
        tomllib.loads(committed(repo, server, "Server/pyproject.toml").decode("utf-8")).get(
            "project"
        ),
        "pinned server project",
    )
    require(project.get("name") == "ykh09242-unity-mcp-server", "Wrong pinned server distribution")
    server_version = text(project.get("version"), "pinned server version")
    expected = skill_files(repo, skill, SKILLS[0])
    for revision in (skill, package_commit):
        for folder in SKILLS:
            require(
                skill_files(repo, revision, folder) == expected,
                "mcpSkillRevision differs from committed package skill mirrors",
            )
    if git(
        repo, "ls-tree", "--name-only", package_commit, "--", "release-source-inputs.json"
    ).strip():
        source_inputs = load_json(committed(repo, package_commit, "release-source-inputs.json"))
        require(
            source_inputs
            == {
                "schema_version": 1,
                "repository": BASE,
                "server_commit": server,
                "skill_commit": skill,
                "server_source": server_url,
            },
            "Generated release source inputs differ from package pins",
        )
    return Components(package_commit, server, skill, unity_version, server_version, server_url)


def https(value: str) -> None:
    parsed = urlsplit(value)
    require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None,
        "Evidence link must be an HTTPS URL without credentials",
    )


def link(value: str) -> tuple[str, str]:
    found = re.fullmatch(r"\[([^\[\]\n]+)\]\((https://[^\s()]+)\)", value)
    require(found is not None, "Expected exactly one labelled HTTPS evidence link")
    https(found[2])
    return found[1], found[2]


def table(lines: list[str], header: tuple[str, ...]) -> list[list[str]]:
    require(len(lines) >= 3, "Missing table header/separator/rows")
    rows = []
    for line in lines:
        require(
            line.startswith("|") and line.endswith("|"),
            "Only the two contract tables are allowed inside the block",
        )
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        require(
            len(cells) == len(header) and all(cells), "Malformed table cell count or empty cell"
        )
        rows.append(cells)
    require(tuple(rows[0]) == header, "Unexpected or duplicate table header")
    require(
        all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]),
        "Invalid Markdown table separator",
    )
    return rows[2:]


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    component: str
    kind: Kind
    result: Result
    tested: str
    evidence: str
    url: str


def parse_notes(notes: str, selected: Components) -> list[Row]:
    require(len(notes) <= 1024 * 1024, "Release notes exceed 1 MiB")
    require(
        notes.count(START) == notes.count(END) == 1, "Missing or duplicate release evidence block"
    )
    before, block = notes.split(START)
    del before
    body, _ = block.split(END)
    groups = [group.splitlines() for group in re.split(r"\n\s*\n", body.strip())]
    require(len(groups) == 2, "Exactly two contract tables are required")
    inputs = table(groups[0], COMPONENT_HEADER)
    expected = {
        "Unity package": (
            selected.unity_version,
            selected.package,
            BASE + "/commit/" + selected.package,
        ),
        "Python server": (selected.server_version, selected.server, selected.server_url),
        "Client skill": (
            "not separately versioned",
            selected.skill,
            BASE + "/commit/" + selected.skill,
        ),
    }
    require(
        len(inputs) == 3 and {row[0] for row in inputs} == set(expected),
        "Exactly the three component rows are required",
    )
    for name, version, raw in inputs:
        label, url = link(raw)
        require(
            (version, label, url) == expected[name],
            f"{name} version/pin differs from selected committed metadata",
        )
    records = table(groups[1], CHECK_HEADER)
    require(len(records) <= 128, "Too many evidence rows")
    rows = []
    used: set[str] = set()
    for label, component, kind, result, tested, evidence in records:
        require(component in ("unity", "server", "skill"), "Unsupported component; use one per row")
        eid, url = link(evidence)
        require(
            re.fullmatch(r"[a-z][a-z0-9-]{0,63}", eid) is not None and eid not in used,
            "Invalid or duplicate evidence ID",
        )
        used.add(eid)
        require(
            tested == "none"
            or tested.startswith("commit:")
            and digest(tested[7:], 40)
            or tested.startswith("partial:")
            and digest(tested[8:], 64),
            "Invalid Tested input grammar",
        )
        rows.append(Row(label, component, Kind(kind), Result(result), tested, eid, url))
    return rows


def entry_files(index_path: Path, entry: JsonMap, required: set[str]) -> dict[str, bytes]:
    files = mapping(entry.get("files"), "files")
    require(set(files) == required, "Required saved file roles: " + ", ".join(sorted(required)))
    result = {}
    root = index_path.parent.resolve()
    for role, value in files.items():
        ref = mapping(value, role)
        require(set(ref) == {"path", "sha256"}, f"Invalid file pointer fields: {role}")
        relative = text(ref.get("path"), role + " path")
        path = Path(relative)
        require(
            not path.is_absolute() and ".." not in path.parts and ":" not in relative,
            "Evidence paths must stay under the index directory",
        )
        target = root / path
        require(
            not any(
                parent.is_symlink() for parent in (target, *target.parents) if parent != root.parent
            ),
            "Linked evidence paths are unsupported",
        )
        require(target.resolve().is_relative_to(root), "Evidence path escapes index directory")
        data = read_bytes(target)
        checksum = text(ref.get("sha256"), role + " digest")
        require(
            digest(checksum, 64) and hashlib.sha256(data).hexdigest() == checksum,
            f"Evidence SHA256 mismatch: {role}",
        )
        result[role] = data
    return result


def checkout_revision(data: bytes) -> str:
    lines = []
    for raw in data.decode("utf-8-sig").splitlines():
        found = LOG_LINE.fullmatch(raw)
        require(found is not None, "Unsupported checkout log line format")
        lines.append(found[1])
    require(
        sum(line.startswith("##[group]Run actions/checkout@") for line in lines) == 1,
        "Missing or ambiguous source checkout step",
    )
    require("  repository: ykh09242/unity-mcp" in lines, "Wrong checkout repository")
    commands = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(r"\[command\].*[/\\]git(?:\.exe)? log -1 --format=%H", line)
    ]
    require(len(commands) == 1, "Missing or ambiguous actual checkout git log record")
    source = next(
        i for i, line in enumerate(lines) if line.startswith("##[group]Run actions/checkout@")
    )
    checked = [
        i for i, line in enumerate(lines) if line == "##[group]Checking out the ref" and i > source
    ]
    removed = [i for i, line in enumerate(lines) if line == "##[group]Removing auth" and i > source]
    require(
        len(checked) == len(removed) == 1 and source < checked[0] < commands[0] < removed[0],
        "Actual commit record is outside the source checkout context",
    )
    position = commands[0] + 1
    require(
        position < len(lines) and digest(lines[position], 40),
        "Actual checkout requires the full printed commit",
    )
    return lines[position]


def xml_root(data: bytes) -> ET.Element:
    document = data.decode("utf-8-sig")
    require("\x00" not in document, "XML evidence must use UTF-8")
    require(
        "<!DOCTYPE" not in document.upper() and "<!ENTITY" not in document.upper(),
        "XML declarations/entities are unsupported",
    )
    return ET.fromstring(document)


def count(element: ET.Element, name: str) -> int:
    value = element.get(name, "")
    require(re.fullmatch(r"[0-9]+", value) is not None, f"Invalid XML count: {name}")
    return int(value)


def native_result(data: bytes) -> tuple[bool, bool]:
    root = xml_root(data)
    require(root.tag == "test-run", "Native result must be NUnit test-run XML")
    cases = list(root.iter("test-case"))
    values = {
        name: count(root, name) for name in ("total", "passed", "failed", "inconclusive", "skipped")
    }
    require(
        values["total"] > 0
        and sum(values[name] for name in values if name != "total")
        == values["total"]
        == len(cases),
        "NUnit total/case counts disagree or are empty",
    )
    names = [case.get("fullname") for case in cases]
    require(
        all(names) and len(set(names)) == len(names), "Missing or duplicate NUnit case identity"
    )
    for attribute, result in (
        ("passed", "Passed"),
        ("failed", "Failed"),
        ("inconclusive", "Inconclusive"),
        ("skipped", "Skipped"),
    ):
        require(
            sum(case.get("result") == result for case in cases) == values[attribute],
            "NUnit result counts differ from actual cases",
        )
    status = root.get("result", "").split(":")[0].split("(")[0]
    failures = bool(values["failed"] or values["inconclusive"])
    require(
        status in ("Passed", "Failed", "Skipped") and (status == "Failed") == failures,
        "NUnit root status contradicts cases",
    )
    return values["passed"] == values["total"] and status == "Passed", failures


def junit_result(data: bytes) -> tuple[bool, bool]:
    root = xml_root(data)
    require(root.tag in ("testsuites", "testsuite"), "Python results must be pytest JUnit XML")
    suites = [root] if root.tag == "testsuite" else list(root)
    require(
        bool(suites) and all(suite.tag == "testsuite" for suite in suites),
        "Unsupported JUnit suite shape",
    )
    passed = total = 0
    failures = 0
    identities: set[tuple[str, str]] = set()
    skipped_total = errors_total = failed_total = 0
    for suite in suites:
        cases = list(suite.findall("testcase"))
        tests, failed, errors, skipped = (
            count(suite, name) for name in ("tests", "failures", "errors", "skipped")
        )
        for case in cases:
            identity = (case.get("classname", ""), case.get("name", ""))
            require(
                bool(identity[1]) and identity not in identities,
                "Missing or duplicate JUnit case identity",
            )
            identities.add(identity)
            require(
                sum(case.find(tag) is not None for tag in ("failure", "error", "skipped")) <= 1,
                "Ambiguous JUnit case outcome",
            )
        require(tests > 0 and tests == len(cases), "JUnit total/case counts disagree or are empty")
        for tag, expected in (("failure", failed), ("error", errors), ("skipped", skipped)):
            require(
                sum(case.find(tag) is not None for case in cases) == expected,
                "JUnit result counts differ from actual cases",
            )
        require(failed + errors + skipped <= tests, "Invalid JUnit result counts")
        total += tests
        failures += failed + errors
        passed += tests - failed - errors - skipped
        failed_total += failed
        errors_total += errors
        skipped_total += skipped
    if root.tag == "testsuites":
        for name, expected in (
            ("tests", total),
            ("failures", failed_total),
            ("errors", errors_total),
            ("skipped", skipped_total),
        ):
            require(
                name not in root.attrib or count(root, name) == expected,
                "JUnit root/suite totals disagree",
            )
    return total == passed, bool(failures)


def native_log(data: bytes, kind: Kind) -> int:
    log = "\n".join(
        found[1] if (found := LOG_LINE.fullmatch(line)) else line
        for line in data.decode("utf-8-sig").splitlines()
    )
    require(
        re.search(r"Version is '[0-9]+\.[0-9]+\.[^']+'", log) is not None,
        "Native log lacks actual Editor version",
    )
    platform = "EditMode" if kind == Kind.EDIT else "PlayMode"
    require(
        re.search(r"-testPlatform(?:\s+|=)" + platform + r"\b", log) is not None
        and "-runTests" in log,
        "Native log lacks matching test invocation/platform",
    )
    endings = re.findall(r"Test run completed\. Exiting with code ([0-9]+)\b", log)
    require(len(endings) == 1, "Native log lacks unambiguous runner ending")
    return int(endings[0])


def check_execution(kind: Kind, files: dict[str, bytes]) -> tuple[bool, bool]:
    log = files["log"].decode("utf-8-sig")
    match kind:
        case Kind.COMPILE:
            require(
                "Compiler      :" in log
                and re.search(
                    r"--- MCPForUnity\.\w+ \[(?:win|osx|linux)\] : [1-9][0-9]* sources, [1-9][0-9]* refs ---",
                    log,
                )
                is not None,
                "Log lacks actual compiler-stage execution",
            )
            failed = re.search(r"\berror (?:CS|BC|FS|NETSDK|MSB)[0-9]+\b", log) is not None
            return not failed, failed
        case Kind.PREPARATION:
            receipt = load_json(files["preparation"])
            text(receipt.get("unityVersion"), "prepared Unity version")
            text(receipt.get("profile"), "prepared profile")
            packages = receipt.get("packages")
            require(
                isinstance(packages, list) and bool(packages), "Preparation receipt lacks packages"
            )
            for value in packages:
                package = mapping(value, "prepared package")
                for key in ("name", "version", "source"):
                    text(package.get(key), "prepared " + key)
                integrity = mapping(package.get("integrity"), "package integrity")
                require(
                    digest(text(integrity.get("packageMetadataSha256"), "metadata digest"), 64),
                    "Invalid package metadata digest",
                )
                archive = integrity.get("archiveSha256")
                require(
                    archive is None or isinstance(archive, str) and digest(archive, 64),
                    "Invalid archive integrity digest",
                )
            require(bool(log.strip()), "Missing preparation execution log")
            return True, False
        case Kind.PYTHON:
            require(
                re.search(r"Python [0-9]+\.[0-9]+", log) is not None and "pytest" in log,
                "Python log lacks actual invocation/version",
            )
            return junit_result(files["results"])
        case Kind.EDIT | Kind.PLAY:
            complete, failed = native_result(files["results"])
            exit_code = native_log(files["log"], kind)
            require((exit_code != 0) == failed, "Native runner ending contradicts NUnit failures")
            return complete and exit_code == 0, failed
    return fail("Unsupported execution kind")


def validate_row(row: Row, entry: JsonMap, index_path: Path, selected: Components) -> None:
    require(
        set(entry) <= {"origin", "kind", "public_url", "files", "reason"}
        and {"origin", "kind", "public_url", "files"} <= set(entry),
        "Unsupported evidence entry fields",
    )
    require(
        entry["kind"] == row.kind.value and entry["public_url"] == row.url,
        "Evidence kind/URL differs from linked row",
    )
    origin = Origin(text(entry.get("origin"), "origin"))
    match origin:
        case Origin.POLICY:
            require(
                row.result == Result.NOT_RUN and row.tested == "none",
                "Policy is not-run only with no tested input",
            )
            text(entry.get("reason"), "not-run reason")
            entry_files(index_path, entry, set())
            return
        case Origin.LOCAL:
            require(
                row.kind in (Kind.EDIT, Kind.PLAY)
                and row.result == Result.HISTORICAL
                and row.tested.startswith("partial:"),
                "Partial local-native inputs are historical-only, never current pass/failure",
            )
            require("reason" not in entry, "Unexpected local-native reason field")
            files = entry_files(index_path, entry, {"invocation", "inputs", "log", "results"})
            require(
                hashlib.sha256(files["inputs"]).hexdigest() == row.tested[8:],
                "Partial input map digest mismatch",
            )
            inputs = load_json(files["inputs"])
            require(bool(inputs), "Empty historical partial input map")
            for path, value in inputs.items():
                require(
                    not PurePosixPath(path).is_absolute()
                    and ".." not in PurePosixPath(path).parts
                    and "\\" not in path
                    and ":" not in path,
                    "Invalid partial input path",
                )
                require(digest(text(value, "input digest"), 64), "Invalid partial input digest")
            args = load_json(files["invocation"]).get("args")
            require(
                isinstance(args, list) and "-runTests" in args,
                "Local invocation lacks actual test arguments",
            )
            mode = "EditMode" if row.kind == Kind.EDIT else "PlayMode"
            require(
                args.count("-testPlatform") == 1
                and args.index("-testPlatform") + 1 < len(args)
                and args[args.index("-testPlatform") + 1] == mode,
                "Local invocation platform mismatch",
            )
            check_execution(row.kind, files)
            return
        case Origin.CI:
            require(row.result != Result.NOT_RUN, "CI record cannot be relabelled not-run")
    roles = {"workflow", "job"}
    if row.result != Result.SKIPPED:
        roles.update(("checkout", "log"))
        match row.kind:
            case Kind.PYTHON | Kind.EDIT | Kind.PLAY:
                roles.add("results")
            case Kind.PREPARATION:
                roles.add("preparation")
            case Kind.COMPILE:
                pass
    files = entry_files(index_path, entry, roles)
    workflow, job = (load_json(files[name]) for name in ("workflow", "job"))
    run_id, job_id = integer(workflow.get("id"), "run id"), integer(job.get("id"), "job id")
    require(
        job.get("run_id") == run_id and workflow.get("status") == job.get("status") == "completed",
        "Workflow/job incomplete or run ID mismatch",
    )
    require(
        workflow.get("html_url") == f"{BASE}/actions/runs/{run_id}"
        and job.get("html_url") == row.url == f"{BASE}/actions/runs/{run_id}/job/{job_id}",
        "Workflow/job URL association mismatch",
    )
    require(
        mapping(workflow.get("repository"), "workflow repository").get("full_name")
        == "ykh09242/unity-mcp",
        "Wrong workflow repository",
    )
    require(
        digest(text(workflow.get("head_sha"), "workflow head_sha"), 40), "Invalid workflow head_sha"
    )
    conclusion = job.get("conclusion")
    if row.result == Result.SKIPPED:
        require(
            conclusion == "skipped" and row.tested == "none",
            "Skipped row requires an actually skipped job and no tested input",
        )
        text(entry.get("reason"), "skip reason")
        return
    require("reason" not in entry, "Unexpected executed-record reason field")
    require(conclusion in ("success", "failure"), "Unsupported or unexecuted job conclusion")
    require(
        files["checkout"] == files["log"],
        "Checkout and execution must be the same saved full job log",
    )
    revision = checkout_revision(files["log"])
    require(
        row.tested == "commit:" + revision, "Tested input differs from actual checkout revision"
    )
    require(
        row.result == Result.HISTORICAL or revision == selected.revision(row.component),
        "Actual checkout is a different component input; retain it as historical",
    )
    complete, failed = check_execution(row.kind, files)
    require(not failed or conclusion == "failure", "Actual failed cases contradict successful job")
    match row.result:
        case Result.PASSED:
            require(
                conclusion == "success" and complete,
                "Actual executed records do not establish a passed row",
            )
        case Result.FAILED:
            require(conclusion == "failure", "Actual job did not fail")
        case Result.HISTORICAL:
            pass
        case Result.NOT_RUN | Result.SKIPPED:
            fail("Invalid executed result")


def check_release_notes(
    repo: Path, package_commit: str, notes: str, evidence_index: Path
) -> list[str]:
    """Return contradictions; success is consistency, not release publication or coverage."""
    try:
        selected = components(repo, package_commit)
        rows = parse_notes(notes, selected)
        index = load_json(read_bytes(evidence_index))
        require(
            set(index) == {"schema_version", "evidence"}
            and type(index["schema_version"]) is int
            and index["schema_version"] == 1,
            "Unsupported evidence index schema",
        )
        evidence = mapping(index["evidence"], "evidence index")
        require(set(evidence) == {row.evidence for row in rows}, "Missing or unlinked evidence IDs")
    except (OSError, ValueError, KeyError, RecursionError, subprocess.TimeoutExpired) as error:
        return [str(error)]
    issues = []
    for row in rows:
        try:
            validate_row(
                row, mapping(evidence[row.evidence], row.evidence), evidence_index, selected
            )
        except (OSError, ValueError, KeyError, RecursionError, ET.ParseError) as error:
            issues.append(f"{row.evidence}: {error}")
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--package-commit", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--evidence-index", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        notes = read_bytes(args.notes).decode("utf-8")
    except (EvidenceError, OSError, UnicodeError) as error:
        print(error)
        return 1
    issues = check_release_notes(args.repo, args.package_commit, notes, args.evidence_index)
    for issue in issues:
        print(issue)
    if not issues:
        print(
            "Release evidence block matches the supplied committed inputs and saved records; no release was published."
        )
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
