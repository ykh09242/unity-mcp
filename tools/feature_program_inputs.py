"""Explicit, bounded verification input identities; no execution or publication.

The module parses versioned JSON at its public boundaries and uses the program's
existing safe readers. Captures identify declared inputs, never inferred closure.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path, PurePosixPath
import os
import re
import subprocess
from types import ModuleType
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

if TYPE_CHECKING:
    from feature_program import ReadBudget

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
GROUPS: Final = ("source", "test", "config")
LIMIT: Final = 128 * 1024 * 1024
HASH: Final = r"[a-f0-9]{64}"


class InputAccess(Protocol):
    """Read capabilities only; ownership and publication stay with the host."""

    repo: Path

    def path(self, relative: str) -> Path: ...
    def source_snapshot(self, scopes: list[str], budget: ReadBudget | None = None) -> Record: ...
    def verify_refs(self, refs: list[Json], budget: ReadBudget | None = None) -> None: ...


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _record(value: Json, label: str) -> Record:
    return _core().record(value, label)


def _refs(value: Json) -> list[Json]:
    """Parse references before a consumer may follow them."""
    core = _core()
    result = []
    for item in core.sequence(value, "input references"):
        ref = _record(item, "input reference")
        core.require(set(ref) == {"path", "sha256"}, "Input reference requires path/hash")
        name = core.safe_relative(core.string(ref["path"], "input reference path"))
        core.require(name.startswith("evidence/"), "Input references must be program evidence")
        core.require(bool(re.fullmatch(HASH, str(ref["sha256"]))), "Invalid input reference hash")
        result.append({"path": name, "sha256": ref["sha256"]})
    core.require(len(result) <= 1000, "Too many input references")
    core.require(len({core.digest(ref) for ref in result}) == len(result), "Duplicate references")
    return sorted(result, key=lambda ref: str(ref["path"]))


def normalize_manifest(value: Json) -> Record:
    """Parse a finite categorized declaration without inferring dependencies."""
    core = _core()
    manifest = _record(value, "input manifest")
    core.require(
        set(manifest) == {"schema_version", "checks", "groups", "artifact_refs", "completeness"}
        and type(manifest["schema_version"]) is int
        and manifest["schema_version"] == 1,
        "Invalid input manifest schema",
    )
    checks = sorted(core.strings(manifest["checks"], "manifest checks"))
    core.require(bool(checks), "Manifest needs check identities")
    groups = _record(manifest["groups"], "input groups")
    core.require(set(groups) == set(GROUPS), "Manifest requires source/test/config groups")
    normalized: Record = {}
    seen: dict[str, tuple[str, Record]] = {}
    for group in GROUPS:
        entries = []
        for raw in core.sequence(groups[group], "input selectors"):
            item = _record(raw, "input selector")
            core.require(
                {"path", "kind", "required"}
                <= set(item)
                <= {"path", "kind", "required", "check_ids"},
                "Invalid input selector fields",
            )
            path = core.safe_relative(core.string(item["path"], "input path"))
            core.require(
                ".work" not in {part.casefold() for part in PurePosixPath(path).parts},
                "Private paths cannot be input source selectors",
            )
            kind = core.string(item["kind"], "input selector kind")
            core.require(kind in {"file", "directory"}, "Invalid input selector kind")
            core.require(type(item["required"]) is bool, "Input required must be boolean")
            selected = sorted(core.strings(item.get("check_ids", checks), "selector checks"))
            core.require(bool(selected) and set(selected) <= set(checks), "Invalid selector checks")
            entry = {
                "path": path,
                "kind": item["kind"],
                "required": item["required"],
                "check_ids": selected,
            }
            key = path.casefold()
            previous = seen.get(key)
            if previous is not None:
                core.require(previous == (group, entry), "Conflicting input path declaration")
                continue
            seen[key] = (group, entry)
            entries.append(entry)
        core.require(len(entries) <= 10000, "Input selector count exceeds bound")
        normalized[group] = sorted(entries, key=lambda item: str(item["path"]))
    closure = _record(manifest["completeness"], "input completeness")
    core.require(
        set(closure) == {"status", "reason", "unknowns", "evidence_refs"},
        "Invalid input completeness fields",
    )
    unknowns = sorted(core.strings(closure["unknowns"], "unknown dependencies"))
    status = core.string(closure["status"], "input completeness status")
    core.require(status in {"complete", "unknown"}, "Invalid input completeness")
    core.require(
        closure["status"] != "complete" or not unknowns, "Complete inputs have unknown dependencies"
    )
    return {
        "schema_version": 1,
        "checks": checks,
        "groups": normalized,
        "artifact_refs": _refs(manifest["artifact_refs"]),
        "completeness": {
            "status": closure["status"],
            "reason": core.string(closure["reason"], "closure reason"),
            "unknowns": unknowns,
            "evidence_refs": _refs(closure["evidence_refs"]),
        },
    }


def manifest_scope(manifest: Json) -> list[str]:
    """Return the public union only; immutable artifacts are separate inputs."""
    parsed = normalize_manifest(manifest)
    return sorted(item["path"] for group in GROUPS for item in parsed["groups"][group])


def _environment(value: Json) -> Record:
    core = _core()
    env = _record(
        value if value is not None else {"facts": {}, "unknowns": ["environment"]}, "environment"
    )
    core.require(
        {"facts", "unknowns"} <= set(env) <= {"facts", "unknowns", "label"},
        "Invalid environment fields",
    )
    facts = _record(env["facts"], "environment facts")
    for name, fact in facts.items():
        core.require(
            bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name)),
            "Invalid environment fact name",
        )
        core.require(
            not re.search(r"secret|token|password|credential|api.?key", name, re.I),
            "Sensitive environment fact rejected",
        )
        core.require(
            len(core.string(fact, "environment fact")) <= 4096, "Oversized environment fact"
        )
    unknowns = sorted(core.strings(env["unknowns"], "environment unknowns"))
    if not facts and not unknowns:
        unknowns = ["environment"]
    result = {"facts": dict(sorted(facts.items())), "unknowns": unknowns}
    if "label" in env:
        result["label"] = core.string(env["label"], "environment label")
    return result


def _method(value: Json) -> Record:
    core = _core()
    method = _record(value if value is not None else {}, "input method")
    core.require(
        set(method) <= {"argv", "cwd", "procedure", "execution_kind", "version", "command"},
        "Invalid input method fields",
    )
    if not method:
        return {}
    core.require(
        core.string(method.get("execution_kind"), "method execution kind")
        in {"test", "static", "manual", "native", "compile"},
        "Invalid method kind",
    )
    core.require(
        bool(method.get("argv") or method.get("procedure") or method.get("command")),
        "Method identity missing",
    )
    if "argv" in method:
        argv = core.sequence(method["argv"], "method argv")
        core.require(
            bool(argv) and type(argv[0]) is str and bool(argv[0].strip()), "Empty method executable"
        )
        core.require(
            all(type(argument) is str for argument in argv),
            "Method argv must contain ordered strings",
        )
        core.string(method.get("cwd"), "method cwd")
    for name in ("cwd", "procedure", "version", "command"):
        if name in method:
            core.require(len(core.string(method[name], name)) <= 65536, "Oversized method identity")
    return deepcopy(method)


def _identity(capture: Record) -> Record:
    result = {
        key: (
            {name: value for name, value in capture[key].items() if name != "label"}
            if key == "environment"
            else capture[key]
        )
        for key in (
            "manifest_digest",
            "snapshots",
            "artifact_refs",
            "environment",
            "method",
            "checks",
            "completeness",
        )
    }
    if capture.get("exclusions"):
        result["exclusions"] = capture["exclusions"]
    return result


def _parse_exclusions(value: Json) -> list[Json]:
    core = _core()
    entries = []
    for value_entry in core.sequence(value, "excluded input boundaries"):
        entry = _record(value_entry, "excluded input boundary")
        core.require(
            set(entry) == {"group", "path", "kind"}
            and entry["group"] in GROUPS
            and core.string(entry["kind"], "exclusion kind")
            in {"excluded_directory", "excluded_path"},
            "Invalid exclusion boundary",
        )
        name = core.string(entry["path"], "excluded boundary path")
        path = PurePosixPath(name)
        core.require(
            not path.is_absolute()
            and ".." not in path.parts
            and ":" not in name
            and "\\" not in name
            and "\0" not in name,
            "Unsafe excluded boundary path",
        )
        entries.append(deepcopy(entry))
    core.require(len(entries) <= 10000, "Excluded boundary count exceeds bound")
    return sorted(entries, key=lambda entry: (str(entry["group"]), str(entry["path"])))


def _exclusions(program: InputAccess, manifest: Record) -> list[Json]:
    """Inventory skipped boundaries without descending or reading excluded bytes."""
    core = _core()
    entries = []
    visited = 0
    for group in GROUPS:
        for selector in manifest["groups"][group]:
            if selector["kind"] != "directory":
                continue
            root = core.safe_path(program.repo, str(selector["path"]))
            if not root.is_dir():
                continue
            for folder, dirs, files in os.walk(root, followlinks=False):
                visited += 1
                core.require(visited <= 10000, "Input directory inventory exceeds bound")
                retained = []
                for name in sorted(dirs):
                    relative = (Path(folder) / name).relative_to(program.repo).as_posix()
                    if name.casefold() in core.SKIP_DIRS:
                        entries.append(
                            {"group": group, "path": relative, "kind": "excluded_directory"}
                        )
                        continue
                    try:
                        core.safe_path(program.repo, relative)
                    except core.ProgramError:
                        entries.append({"group": group, "path": relative, "kind": "excluded_path"})
                        continue
                    retained.append(name)
                dirs[:] = retained
                for name in sorted(files):
                    relative = (Path(folder) / name).relative_to(program.repo).as_posix()
                    try:
                        core.safe_path(program.repo, relative)
                    except core.ProgramError:
                        boundary = (
                            str(PurePosixPath(relative).parent / ".env*")
                            if name.casefold().startswith(".env")
                            else relative
                        )
                        entries.append({"group": group, "path": boundary, "kind": "excluded_path"})
    result = _parse_exclusions(entries)
    collector = core._active_collector()
    if collector is not None:
        collector.observe_exclusions(program.repo, manifest["groups"], result, entry_limit=10000)
    return result


def _assemble(
    manifest: Record,
    snapshots: Record,
    environment: Json,
    method: Json,
    *,
    exclusions: list[Json] | None = None,
) -> Record:
    core = _core()
    env, procedure = _environment(environment), _method(method)
    unknowns = list(manifest["completeness"]["unknowns"]) + list(env["unknowns"])
    if exclusions:
        unknowns.append("excluded_boundaries")
    if not procedure or not (procedure.get("argv") or procedure.get("procedure")):
        unknowns.append("structured_method")
    for group in GROUPS:
        for item in manifest["groups"][group]:
            path = str(item["path"])
            found = (
                snapshots[group].get(path + "/")
                if item["kind"] == "directory"
                else snapshots[group].get(path)
            )
            wrong_kind = (
                item["kind"] == "file"
                and snapshots[group].get(path + "/") == "directory"
                or item["kind"] == "directory"
                and path in snapshots[group]
                and snapshots[group][path] != "missing"
            )
            if wrong_kind:
                unknowns.append("input_kind:" + path)
            elif item["required"] and (found is None or found == "missing"):
                unknowns.append("required_input:" + path)
            elif (
                found is not None and item["kind"] == "file" and not re.fullmatch(HASH, str(found))
            ):
                if found != "missing":
                    unknowns.append("input_kind:" + path)
    closure = {
        "status": "unknown"
        if unknowns or manifest["completeness"]["status"] != "complete"
        else "complete",
        "reason": manifest["completeness"]["reason"],
        "unknowns": sorted(set(unknowns)),
        "evidence_refs": deepcopy(manifest["completeness"]["evidence_refs"]),
    }
    capture = {
        "schema_version": 1,
        "manifest": manifest,
        "manifest_digest": core.digest(manifest),
        "snapshots": snapshots,
        "artifact_refs": deepcopy(manifest["artifact_refs"]),
        "environment": env,
        "method": procedure,
        "checks": deepcopy(manifest["checks"]),
        "completeness": closure,
        "origin": {"kind": "working_tree"},
    }
    if exclusions:
        capture["exclusions"] = exclusions
    capture["input_digest"] = core.digest(_identity(capture))
    return capture


def capture_inputs(
    program: InputAccess,
    manifest: Json,
    *,
    environment: Json,
    method: Json,
    budget: ReadBudget | None = None,
) -> Record:
    """Capture raw declared bytes twice to reject racing files; never execute probes."""
    parsed = normalize_manifest(manifest)
    refs = parsed["artifact_refs"] + parsed["completeness"]["evidence_refs"]
    program.verify_refs(refs, budget)
    source_budget = budget if budget is not None else _core().ReadBudget(LIMIT)
    snapshots = {}
    for group in GROUPS:
        scope = [str(item["path"]) for item in parsed["groups"][group]]
        snapshots[group] = program.source_snapshot(scope, source_budget) if scope else {}
    exclusions = _exclusions(program, parsed)
    result = _assemble(parsed, snapshots, environment, method, exclusions=exclusions)
    recheck_budget = _core().ReadBudget(LIMIT)
    for group in GROUPS:
        scope = [str(item["path"]) for item in parsed["groups"][group]]
        _core().require(
            not scope or program.source_snapshot(scope, recheck_budget) == snapshots[group],
            "Input source changed during capture",
        )
    program.verify_refs(refs)
    _core().require(
        _exclusions(program, parsed) == exclusions, "Input exclusions changed during capture"
    )
    return result


def validate_capture(value: Json) -> Record:
    """Parse a retained capture and reject malformed or rewritten identities."""
    core = _core()
    capture = _record(value, "input capture")
    core.require(
        {
            "schema_version",
            "manifest",
            "manifest_digest",
            "snapshots",
            "artifact_refs",
            "environment",
            "method",
            "checks",
            "completeness",
            "input_digest",
        }
        <= set(capture)
        <= {
            "schema_version",
            "manifest",
            "manifest_digest",
            "snapshots",
            "artifact_refs",
            "environment",
            "method",
            "checks",
            "completeness",
            "input_digest",
            "origin",
            "exclusions",
        },
        "Invalid input capture fields",
    )
    core.require(
        type(capture["schema_version"]) is int and capture["schema_version"] == 1,
        "Invalid capture version",
    )
    manifest = normalize_manifest(capture["manifest"])
    snapshots = _record(capture["snapshots"], "input snapshots")
    core.require(set(snapshots) == set(GROUPS), "Invalid snapshot groups")
    for group in GROUPS:
        for name, fingerprint in _record(snapshots[group], "input snapshot").items():
            core.safe_relative(name.rstrip("/"))
            core.string(fingerprint, "input fingerprint")
            core.require(
                fingerprint in {"missing", "directory"}
                or bool(re.fullmatch(HASH, str(fingerprint))),
                "Invalid fingerprint",
            )
    expected = _assemble(
        manifest,
        snapshots,
        capture["environment"],
        capture["method"],
        exclusions=_parse_exclusions(capture["exclusions"]) if "exclusions" in capture else None,
    )
    if "origin" in capture:
        origin = _record(capture["origin"], "input capture origin")
        match origin.get("kind"):
            case "working_tree":
                core.require(set(origin) == {"kind"}, "Invalid working input origin")
            case "git":
                core.require(
                    set(origin) == {"kind", "revision"}
                    and bool(re.fullmatch(r"[a-f0-9]{40}", str(origin["revision"]))),
                    "Invalid explicit Git input origin",
                )
            case _:
                raise core.ProgramError("Invalid capture origin kind")
        expected["origin"] = deepcopy(origin)
    else:
        expected.pop("origin")
    core.require(expected == capture, "Input capture identity differs from declared facts")
    return deepcopy(capture)


def compare_inputs(before: Json, after: Json) -> Record:
    """Compare declared execution facts; never task authority or acceptance."""
    core = _core()
    old, new = validate_capture(before), validate_capture(after)
    differences = []
    affected: set[str] = set()
    all_checks = sorted(set(old["checks"]) | set(new["checks"]))
    selector_indexes: dict[str, list[dict[str, set[str]]]] = {}
    for group in GROUPS:
        indexes = []
        for capture in (old, new):
            index: dict[str, set[str]] = {}
            for selector in capture["manifest"]["groups"][group]:
                path = str(selector["path"])
                checks = set(selector["check_ids"])
                index.setdefault(path, set()).update(checks)
                if selector["kind"] == "directory":
                    index.setdefault(path + "/", set()).update(checks)
            indexes.append(index)
        selector_indexes[group] = indexes
    for group in GROUPS:
        left, right = old["snapshots"][group], new["snapshots"][group]
        for path in sorted(set(left) | set(right)):
            if left.get(path) == right.get(path):
                continue
            kind = "added" if path not in left else "removed" if path not in right else "changed"
            if left.get(path) == "missing" or right.get(path) == "missing":
                kind = "missing"
            differences.append({"group": group, "path": path, "kind": kind})
            for index in selector_indexes[group]:
                normalized = path.rstrip("/")
                affected.update(index.get(normalized, ()))
                for parent in PurePosixPath(path).parents:
                    affected.update(index.get(parent.as_posix() + "/", ()))
    for key, group in (
        ("manifest_digest", "check"),
        ("artifact_refs", "artifact"),
        ("method", "method"),
    ):
        if old[key] != new[key]:
            differences.append({"group": group, "kind": "changed"})
            affected.update(all_checks)
    if _identity(old)["environment"] != _identity(new)["environment"]:
        differences.append({"group": "environment", "kind": "changed"})
        affected.update(all_checks)
    unknowns = sorted(set(old["completeness"]["unknowns"]) | set(new["completeness"]["unknowns"]))
    complete = old["completeness"]["status"] == new["completeness"]["status"] == "complete"
    if not complete:
        affected.update(all_checks)
    if old.get("exclusions", []) != new.get("exclusions", []):
        differences.append({"group": "check", "kind": "changed"})
        affected.update(all_checks)
    return {
        "schema_version": 1,
        "status": "not_assessed" if not complete else "changed" if differences else "unchanged",
        "advisory": True,
        "differences": differences,
        "affected_checks": sorted(affected),
        "unknowns": unknowns,
        "reasons": (["unknown_inputs"] if not complete else [])
        + (["changed_inputs"] if differences else []),
        "before_digest": old["input_digest"],
        "after_digest": new["input_digest"],
        "counts": {
            "known": sum(len(new["snapshots"][group]) for group in GROUPS)
            + len(new["artifact_refs"])
            + len(new["environment"]["facts"])
            + len(new["method"])
            + len(new["checks"]),
            "changed": len(differences),
            "missing": sum(
                value == "missing" for group in GROUPS for value in new["snapshots"][group].values()
            )
            + sum(row["kind"] == "removed" for row in differences),
            "unknown": len(unknowns) + (not complete and not unknowns),
        },
    }


def prepare_manifest(check_id: str, paths: list[str], *, complete: bool = False) -> Record:
    """Prepare one finite inspected check template; completeness is explicit."""
    core = _core()
    groups: Record = {group: [] for group in GROUPS}
    selectors = [{"path": path, "kind": "file", "required": True} for path in paths]
    match check_id:
        case "format_python":
            checks = ["format_lint"]
            groups["source"] = selectors + [
                {"path": "tools/format_scripts.py", "kind": "file", "required": True}
            ]
            groups["config"] = [
                {"path": path, "kind": "file", "required": True}
                for path in (
                    ".editorconfig",
                    "ruff.toml",
                    "Server/pyproject.toml",
                    "Server/uv.lock",
                )
            ]
        case "scoped_tests":
            checks = ["scoped_tests"]
            groups["test"] = selectors
            groups["config"] = [
                {"path": path, "kind": "file", "required": True}
                for path in ("Server/pyproject.toml", "Server/uv.lock")
            ]
        case "web_build":
            checks = ["scoped_tests"]
            groups["source"] = [
                {"path": "tools/feature_program_web/src", "kind": "directory", "required": True}
            ]
            groups["config"] = [
                {"path": "tools/feature_program_web/" + path, "kind": "file", "required": True}
                for path in (
                    "package.json",
                    "package-lock.json",
                    "vite.config.ts",
                    "tsconfig.json",
                    "index.html",
                    "style.css",
                )
            ]
            groups["test"] = selectors
        case "manual":
            checks = ["acceptance"]
            groups["source"] = selectors
        case _:
            raise core.ProgramError("Unknown finite input check template")
    return normalize_manifest(
        {
            "schema_version": 1,
            "checks": checks,
            "groups": groups,
            "artifact_refs": [],
            "completeness": {
                "status": "complete" if complete else "unknown",
                "reason": "Explicit known-check preparation; caller inspects shared and ambient dependencies",
                "unknowns": [] if complete else ["uninspected_dependencies"],
                "evidence_refs": [],
            },
        }
    )


def _git_read(repo: Path, arguments: list[str], data: bytes | None = None) -> bytes:
    """Use only fixed local object readers; no fetch, checkout or mutation."""
    core = _core()
    core.require(arguments[0] in {"ls-tree", "cat-file"}, "Unsupported Git input operation")
    result = subprocess.run(
        ["git", "-c", "safe.directory=" + repo.as_posix(), "-C", str(repo), *arguments],
        input=data,
        capture_output=True,
        check=False,
    )
    core.require(result.returncode == 0, "Local Git input objects unavailable")
    return result.stdout


def _git_snapshot(
    repo: Path, scope: list[str], revision: str, byte_limit: int
) -> tuple[Record, int, list[Json]]:
    core = _core()
    raw = _git_read(
        repo,
        ["ls-tree", "-r", "-t", "-z", revision, "--", *(f":(literal){path}" for path in scope)],
    )
    core.require(len(raw) <= 8 * 1024 * 1024, "Git tree metadata exceeds bound")
    entries, excluded = {}, {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        path = name.decode("utf-8", errors="surrogateescape")
        try:
            core.safe_relative(path)
        except core.ProgramError:
            boundary = path
            parts = PurePosixPath(path).parts
            for index in range(1, len(parts) + 1):
                prefix = PurePosixPath(*parts[:index]).as_posix()
                try:
                    core.safe_relative(prefix)
                except core.ProgramError:
                    boundary = prefix
                    break
            if PurePosixPath(boundary).name.casefold().startswith(".env"):
                boundary = str(PurePosixPath(boundary).parent / ".env*")
            excluded[boundary] = "excluded_path"
            continue
        entries[path] = (mode, kind, oid)
    core.require(len(entries) <= 10000, "Git tree exceeds input entry bound")
    selected, snapshot = {}, {}
    for path in scope:
        entry = entries.get(path)
        if entry is None:
            snapshot[path] = "missing"
            continue
        mode, kind, oid = entry
        core.require(mode not in {"120000", "160000"}, "Explicit Git link/submodule input rejected")
        match kind:
            case "blob":
                selected[path] = oid
            case "tree":
                snapshot[path + "/"] = "directory"
                for child, (child_mode, child_kind, child_oid) in entries.items():
                    if not child.startswith(path + "/") or child_kind == "tree":
                        continue
                    suffix = PurePosixPath(child[len(path) + 1 :])
                    skipped = next(
                        (
                            index
                            for index, part in enumerate(suffix.parts[:-1])
                            if part.casefold() in core.SKIP_DIRS
                        ),
                        None,
                    )
                    if skipped is not None:
                        boundary = PurePosixPath(path, *suffix.parts[: skipped + 1]).as_posix()
                        excluded[boundary] = "excluded_directory"
                        continue
                    if child_mode == "120000":
                        excluded[child] = "excluded_path"
                        continue
                    core.require(
                        child_kind == "blob" and child_mode != "160000",
                        "Git submodule input cannot establish equality",
                    )
                    selected[child] = child_oid
            case _:
                raise core.ProgramError("Unsupported Git input object")
    ids = sorted(set(selected.values()))
    diagnostics = [{"path": name, "kind": kind} for name, kind in sorted(excluded.items())]
    if not ids:
        return snapshot, 0, diagnostics
    request = ("\n".join(ids) + "\n").encode("ascii")
    sizes = _git_read(repo, ["cat-file", "--batch-check"], request).splitlines()
    core.require(len(sizes) == len(ids), "Incomplete Git input inventory")
    total = 0
    for oid, row in zip(ids, sizes, strict=True):
        found, kind, size = row.decode("ascii").split()
        core.require(found == oid and kind == "blob", "Unavailable Git input blob")
        total += int(size)
    core.require(total <= byte_limit, "Git input bytes exceed bound")
    hashes = {}
    with BytesIO(_git_read(repo, ["cat-file", "--batch"], request)) as content:
        for oid in ids:
            found, kind, size = content.readline().decode("ascii").strip().split()
            core.require(found == oid and kind == "blob", "Git blob identity changed")
            blob = content.read(int(size))
            core.require(
                len(blob) == int(size) and content.read(1) == b"\n", "Truncated Git input blob"
            )
            hashes[oid] = core.sha(blob)
        core.require(content.read(1) == b"", "Unexpected Git input tail")
    snapshot.update({name: hashes[oid] for name, oid in selected.items()})
    return snapshot, total, diagnostics


def capture_git_inputs(
    program: InputAccess,
    manifest: Json,
    *,
    revision: str,
    environment: Json = None,
    method: Json = None,
    budget: ReadBudget | None = None,
) -> Record:
    """Capture exact existing local Git bytes; supplied runtime facts are not inferred."""
    core = _core()
    core.require(
        bool(re.fullmatch(r"[a-f0-9]{40}", revision)), "Explicit full Git revision required"
    )
    parsed = normalize_manifest(manifest)
    snapshots = {}
    remaining = LIMIT
    exclusions = []
    for group in GROUPS:
        scope = [str(item["path"]) for item in parsed["groups"][group]]
        for path in scope:
            core.safe_path(program.repo, path)
        if scope:
            snapshots[group], used, diagnostics = _git_snapshot(
                program.repo, scope, revision, remaining
            )
            exclusions.extend({"group": group, **entry} for entry in diagnostics)
            remaining -= used
        else:
            snapshots[group] = {}
    program.verify_refs(parsed["artifact_refs"] + parsed["completeness"]["evidence_refs"], budget)
    result = _assemble(
        parsed, snapshots, environment, method, exclusions=_parse_exclusions(exclusions)
    )
    result["origin"] = {"kind": "git", "revision": revision}
    return result
