"""Read-only JSON/CSV adapters for an explicitly granted local source.

The grant belongs to a resolved graph scope. Files are never executed, imported,
rewritten or copied to the graph. Callers must recheck the grant version before
publication and current access before exposing cached external evidence.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading

from services.reference_graph.analysis import Analysis, Catalog
from services.reference_graph.store import Edge, check_owned_path

_CSV_LIMIT_LOCK = threading.Lock()


def _relative(path: str, *, pattern: bool = False) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or "\\" in path or ":" in path or "\0" in path:
        raise ValueError("Use a relative path with forward slashes")
    parts = tuple(path.split("/"))
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise ValueError("Hidden or escaping paths are not admitted")
    if not pattern and any(char in path for char in "*?[]"):
        raise ValueError("File paths cannot contain patterns")
    if pattern and any(char in path for char in "[]"):
        raise ValueError("Patterns support *, ** and ?")
    return parts


def _pattern(value: str) -> re.Pattern:
    _relative(value, pattern=True)
    output, offset = [], 0
    while offset < len(value):
        if value.startswith("**/", offset):
            output.append("(?:.*/)?")
            offset += 3
        elif value.startswith("**", offset):
            output.append(".*")
            offset += 2
        else:
            char = value[offset]
            output.append("[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char))
            offset += 1
    return re.compile("".join(output) + r"\Z")


def validate_mappings(mappings: object) -> list[dict]:
    if not isinstance(mappings, list):
        raise ValueError("Mappings must be a list")
    for mapping in mappings:
        if (
            not isinstance(mapping, dict)
            or not isinstance(mapping.get("field"), str)
            or not mapping["field"]
        ):
            raise ValueError("Each mapping requires a JSON pointer pattern or CSV column")
        if mapping.get("scheme") not in (
            "resources",
            "addressables",
            "guid",
            "path",
        ) or mapping.get("mode", "single") not in ("single", "all"):
            raise ValueError("Invalid mapping scheme or mode")
        if not all(isinstance(mapping.get(key, ""), str) for key in ("prefix", "suffix")):
            raise ValueError("Mapping prefix and suffix must be strings")
    return mappings


@dataclass(frozen=True, slots=True)
class Document:
    text: str
    fingerprint: str


class ExternalSource:
    def __init__(self, config: dict):
        root = config.get("root")
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError("External source requires an absolute local root")
        self.root = check_owned_path(Path(root))
        self.include = self._patterns(config.get("include"))
        self.include_paths = tuple(config["include"])
        self.exclude = self._patterns(config.get("exclude", []), allow_empty=True)
        self.mappings = validate_mappings(config.get("mappings", []))
        self.check_access()

    @staticmethod
    def _patterns(values: object, *, allow_empty: bool = False) -> list[re.Pattern]:
        if (
            not isinstance(values, list)
            or (not values and not allow_empty)
            or not all(isinstance(value, str) for value in values)
        ):
            raise ValueError("Explicit include patterns are required")
        return [_pattern(value) for value in values]

    def check_access(self) -> None:
        info = check_owned_path(self.root).stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("External root is not a directory")
        # Unlike os.access, opening the directory really checks the current
        # effective credentials. Never cache this success as an authorization.
        with os.scandir(self.root):
            pass

    def admitted(self, relative: str) -> bool:
        _relative(relative)
        return (
            Path(relative).suffix.lower() in (".json", ".csv")
            and any(pattern.fullmatch(relative) for pattern in self.include)
            and not any(pattern.fullmatch(relative) for pattern in self.exclude)
        )

    def files(self) -> Iterator[str]:
        self.check_access()
        pending = [self.root]
        while pending:
            directory = pending.pop()
            check_owned_path(directory)
            with os.scandir(directory) as entries:
                items = sorted(entries, key=lambda entry: entry.name)
            for entry in items:
                if entry.name.startswith("."):
                    continue
                path = Path(entry.path)
                relative = path.relative_to(self.root).as_posix()
                if any(
                    pattern.fullmatch(relative) or pattern.fullmatch(relative + "/")
                    for pattern in self.exclude
                ):
                    continue
                info = entry.stat(follow_symlinks=False)
                linked = stat.S_ISLNK(info.st_mode) or getattr(
                    info, "st_file_attributes", 0
                ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if linked:
                    if self.admitted(relative) or entry.is_dir():
                        raise PermissionError(
                            "External source contains a linked path; exclude it explicitly"
                        )
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if self._could_descend(relative):
                        pending.append(path)
                elif self.admitted(relative):
                    yield relative

    def _could_descend(self, relative: str) -> bool:
        directory = relative.split("/")
        for pattern in self.include_paths:
            fixed = []
            wildcard = False
            for part in pattern.split("/"):
                if "*" in part or "?" in part:
                    wildcard = True
                    break
                fixed.append(part)
            if not wildcard:
                fixed = fixed[:-1]
            common = min(len(fixed), len(directory))
            if fixed[:common] == directory[:common] and (wildcard or len(directory) <= len(fixed)):
                return True
        return False

    def read(self, relative: str) -> Document:
        if not self.admitted(relative):
            raise PermissionError("File is outside the source include/exclude policy")
        self.check_access()
        path = check_owned_path(self.root.joinpath(*_relative(relative)))
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PermissionError("External input must be an unlinked regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise PermissionError("External input changed while opening")
            raw = stream.read()
            after = os.fstat(stream.fileno())
        current = check_owned_path(path).stat()
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_nlink,
        )
        # Windows path stat and handle fstat can report different ctime semantics
        # on supported Python versions. Compare ctime only within each API.
        if (
            identity(before) != identity(after)
            or identity(after) != identity(current)
            or before.st_ctime_ns != current.st_ctime_ns
            or opened.st_ctime_ns != after.st_ctime_ns
        ):
            raise PermissionError("External input changed while reading; retry")
        return Document(raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest())


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _values(relative: str, text: str) -> Iterator[tuple[str, str, int]]:
    if Path(relative).suffix.lower() == ".json":
        document = json.loads(text, object_pairs_hook=_unique_object)
        pending = [("", document)]
        while pending:
            path, value = pending.pop()
            if isinstance(value, dict):
                pending.extend(
                    (path + "/" + key.replace("~", "~0").replace("/", "~1"), child)
                    for key, child in reversed(value.items())
                )
            elif isinstance(value, list):
                pending.extend(
                    (path + "/" + str(i), child) for i, child in reversed(list(enumerate(value)))
                )
            elif isinstance(value, str):
                yield path, value, 0
    elif Path(relative).suffix.lower() == ".csv":
        # csv exposes only a process-wide field limit. Raise it monotonically
        # to the already-admitted document's size; restoring a lower value would
        # interrupt another scope's active reader. This allocates no buffer.
        with _CSV_LIMIT_LOCK:
            if len(text) > csv.field_size_limit():
                csv.field_size_limit(len(text))
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV requires distinct column names")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row does not match its header")
            for field_name, value in row.items():
                yield field_name, value, reader.line_num
    else:
        raise ValueError("Only JSON and CSV sources are supported")


def _field_matches(pattern: str, field_name: str) -> bool:
    expected, actual = pattern.split("/"), field_name.split("/")
    return len(expected) == len(actual) and all(
        left == "*" or left == right for left, right in zip(expected, actual)
    )


def analyze_document(relative: str, text: str, catalog: Catalog, mappings: list[dict]) -> Analysis:
    validate_mappings(mappings)
    result = Analysis(coverage="mapped_fields" if mappings else "exact_catalog_strings")
    seen = set()
    for field_name, value, line in _values(relative, text):
        matches = [
            (i, mapping)
            for i, mapping in enumerate(mappings)
            if _field_matches(mapping["field"], field_name)
        ]
        if not mappings:
            targets = set()
            for scheme in ("resources", "addressables", "addressables_label", "path", "guid"):
                targets.update(catalog.keys.get((scheme, value), ()))
            result.edges.extend(
                Edge(target, "external_key_candidate", value, line, "possible", field_name)
                for target in sorted(targets)
            )
        for i, mapping in matches:
            seen.add(i)
            key = mapping.get("prefix", "") + value + mapping.get("suffix", "")
            targets, reason = catalog.resolve(mapping["scheme"], key, mapping.get("mode", "single"))
            if reason:
                result.unresolved.append(
                    {"field": field_name, "line": line, "reason": reason, "key": key}
                )
            result.edges.extend(
                Edge(target, "external_mapped_key", key, line, "possible", field_name)
                for target in sorted(targets)
            )
    result.unresolved.extend(
        {"field": mapping["field"], "reason": "mapping_not_found"}
        for i, mapping in enumerate(mappings)
        if i not in seen
    )
    return result
