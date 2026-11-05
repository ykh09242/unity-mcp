"""Read-only JSON/CSV adapters for an explicitly granted local source.

The grant belongs to a resolved graph scope. Files are never executed, imported,
rewritten or copied to the graph. Callers must recheck the grant version before
publication and current access before exposing cached external evidence.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import csv
import hashlib
from functools import lru_cache
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import heapq
from collections.abc import Callable
from typing import BinaryIO

from services.reference_graph.analysis import Analysis, Catalog
from services.reference_graph.store import Edge, RevisionConflict, check_owned_path
from services.reference_graph.ignore import GitIgnoreRules
from services.reference_graph.collection_protocol import CollectionBudget, CollectionLimits

_CSV_LIMIT_LOCK = threading.Lock()
# Match quoted spans without copying or unescaping them. Possessive runs also
# keep malformed unterminated strings linear; json.loads owns syntax errors.
_JSON_NODE_MARKERS = re.compile(
    r"""
        "(?:[^"\\]++|\\.)*+"?        # Skip a quoted key or value.
        | ,                         # A sibling follows.
        | [\[{](?![ \t\r\n]*+[\]}]) # A nonempty container has a first child.
    """,
    re.DOTALL | re.VERBOSE,
)


def _relative(path: str, *, pattern: bool = False) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or "\\" in path or ":" in path or "\0" in path:
        raise ValueError("Use a relative path with forward slashes")
    parts = tuple(path.split("/"))
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise ValueError("Hidden or escaping paths are not admitted")
    if pattern and any(char in path for char in "[]"):
        raise ValueError("Patterns support *, ** and ?")
    return parts


class _Pattern:
    """A small NFA for the source glob language, without regex backtracking."""

    def __init__(self, value: str):
        _relative(value, pattern=True)
        self.tokens = []
        offset = 0
        while offset < len(value):
            if value.startswith("**/", offset):
                self.tokens.extend(("globdir", "globdir_body"))
                offset += 3
            elif value.startswith("**", offset):
                self.tokens.append("globstar")
                offset += 2
            elif value[offset] == "*":
                self.tokens.append("star")
                offset += 1
            elif value[offset] == "?":
                self.tokens.append("question")
                offset += 1
            else:
                self.tokens.append(("literal", value[offset]))
                offset += 1

    def _closure(self, states: set[int]) -> set[int]:
        pending = list(states)
        while pending:
            state = pending.pop()
            if state == len(self.tokens):
                continue
            token = self.tokens[state]
            target = state + 2 if token == "globdir" else state + 1
            if token in ("globdir", "globstar", "star") and target not in states:
                states.add(target)
                pending.append(target)
        return states

    def _states(self, value: str) -> set[int]:
        states = self._closure({0})
        for char in value:
            next_states = set()
            for state in states:
                if state == len(self.tokens):
                    continue
                token = self.tokens[state]
                if token == "globdir" and char != "\n":
                    next_states.add(state + 1)
                    if char == "/":
                        next_states.add(state + 2)
                elif token == "globdir_body" and char != "\n":
                    next_states.add(state)
                    if char == "/":
                        next_states.add(state + 1)
                elif (token == "globstar" and char != "\n") or (token == "star" and char != "/"):
                    next_states.add(state)
                elif token == "question" and char != "/" or token == ("literal", char):
                    next_states.add(state + 1)
            states = self._closure(next_states)
            if not states:
                break
        return states

    def fullmatch(self, value: str) -> bool:
        return len(self.tokens) in self._states(value)

    def could_descend(self, directory: str) -> bool:
        return bool(self._states(directory + "/"))


def _pattern(value: str) -> _Pattern:
    return _Pattern(value)


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
            "addressables_label",
            "guid",
            "path",
        ) or mapping.get("mode", "single") not in ("single", "all"):
            raise ValueError("Invalid mapping scheme or mode")
        if not all(isinstance(mapping.get(key, ""), str) for key in ("prefix", "suffix")):
            raise ValueError("Mapping prefix and suffix must be strings")
        values = mapping.get("values", {})
        if not isinstance(values, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in values.items()
        ):
            raise ValueError("Mapping values must explicitly map string IDs to asset keys")
    return mappings


@dataclass(frozen=True, slots=True)
class Document:
    text: str
    fingerprint: str


def _require_regular(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError("External input must be a single-link regular file")


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


@contextmanager
def _regular_descriptor(path: Path, before: os.stat_result) -> Iterator[int]:
    """Reject raced special files before reading, including FIFOs without a writer."""
    _require_regular(before)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
    try:
        opened = os.fstat(descriptor)
        _require_regular(opened)
        if _file_identity(before) != _file_identity(opened):
            raise PermissionError("External input changed while opening")
        yield descriptor
        after = os.fstat(descriptor)
        current = check_owned_path(path).stat()
        # Windows path stat and handle fstat can report different ctime semantics.
        if (
            _file_identity(opened) != _file_identity(after)
            or _file_identity(after) != _file_identity(current)
            or opened.st_ctime_ns != after.st_ctime_ns
            or before.st_ctime_ns != current.st_ctime_ns
        ):
            raise PermissionError("External input changed while checking access")
    finally:
        os.close(descriptor)


class ExternalSource:
    def __init__(
        self,
        config: dict,
        *,
        respect_gitignore: bool = True,
        exclude: list[str] | None = None,
        budget: CollectionBudget | None = None,
    ):
        root = config.get("root")
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError("External source requires an absolute local root")
        self.root = check_owned_path(Path(root))
        self.budget = budget or CollectionBudget(CollectionLimits.from_environment())
        self.include = self._patterns(config.get("include"))
        self.exclude = self._patterns(config.get("exclude", []), allow_empty=True)
        self.exclude.extend(_pattern(pattern) for pattern in (exclude or []))
        self.respect_gitignore = respect_gitignore
        self._ignore = GitIgnoreRules([])
        self._ignore_checked: dict[str, bytes | None] = {}
        self._changed_identities = {}
        self._event_check = None
        self._event_directories = {}
        self.mappings = validate_mappings(config.get("mappings", []))
        self.adapter = config.get("adapter", {"name": "json-csv", "version": 1})
        if (
            self.adapter != {"name": "json-csv", "version": 1}
            or type(self.adapter.get("version")) is not int
        ):
            raise ValueError("Unsupported external adapter name or version")
        self.git_revision = config.get("git_revision", False)
        if type(self.git_revision) is not bool:
            raise ValueError("git_revision must be boolean")
        self.check_access()

    def _ignore_directory(self, directory: str) -> None:
        """Read only rule files within this grant; cache missing candidates for this scan."""
        if not self.respect_gitignore or directory in self._ignore_checked:
            return
        raw = self._read_ignore(directory)
        self._ignore_checked[directory] = hashlib.sha256(raw).digest() if raw is not None else None
        if raw is not None:
            relative = (directory + "/" if directory else "") + ".gitignore"
            self._ignore.add(relative, raw.decode("utf-8-sig"))

    def _read_ignore(self, directory):
        relative = (directory + "/" if directory else "") + ".gitignore"
        path = check_owned_path(self.root.joinpath(*relative.split("/")))
        try:
            before = path.stat()
        except FileNotFoundError:
            return None
        self.budget.check_source(before.st_size, 0)
        with _regular_descriptor(path, before) as descriptor:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return self._read_bytes(stream)

    def _read_bytes(self, stream: BinaryIO) -> bytearray:
        """Bound each read, including the one-byte overflow probe, before retaining it."""
        raw = bytearray()
        while True:
            self.budget.remaining()
            size = min(
                65536,
                self.budget.limits.max_source_bytes - len(raw) + 1,
                self.budget.limits.max_bytes - self.budget.decoded_bytes + 1,
            )
            chunk = stream.read(size)
            if not chunk:
                return raw
            self.budget.source(len(chunk), len(raw))
            raw.extend(chunk)

    def _ignored(self, relative: str, *, directory: bool = False) -> bool:
        if not self.respect_gitignore:
            return False
        parts = relative.split("/")
        for depth in range(len(parts)):
            parent = "/".join(parts[:depth])
            if parent and self._ignore.ignored(parent, directory=True):
                return True
            self._ignore_directory(parent)
        return self._ignore.ignored(relative, directory=directory)

    def _git_lines(self, relative: str) -> Iterator[str]:
        """Read only explicitly opted-in, in-root Git identity metadata."""
        path = check_owned_path(self.root / ".git" / relative)
        before = path.stat()
        with _regular_descriptor(path, before) as descriptor:
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
                yield from stream

    def repository_revision(self) -> dict:
        if not self.git_revision:
            return {"state": "disabled"}
        try:
            self.check_access()
            if not stat.S_ISDIR(check_owned_path(self.root / ".git").stat().st_mode):
                return {"state": "unavailable", "reason": "gitdir_outside_source_not_followed"}
            head = "".join(self._git_lines("HEAD")).strip()
            commit = head
            if head.startswith("ref: "):
                reference = head[5:]
                if (
                    not reference.startswith("refs/")
                    or any(part in ("", ".", "..") for part in reference.split("/"))
                    or re.fullmatch(r"[A-Za-z0-9_./-]+", reference) is None
                ):
                    return {"state": "unavailable", "reason": "invalid_ref"}
                try:
                    commit = "".join(self._git_lines(reference)).strip()
                except FileNotFoundError:
                    commit = ""
                    for line in self._git_lines("packed-refs"):
                        fields = line.strip().split(" ")
                        if len(fields) == 2 and fields[1] == reference:
                            commit = fields[0]
            if "".join(self._git_lines("HEAD")).strip() != head:
                return {"state": "unavailable", "reason": "head_changed"}
            if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit) is None:
                return {"state": "unavailable", "reason": "missing_or_invalid_commit"}
            return {"state": "observed_head", "commit": commit.lower()}
        except (OSError, ValueError):
            return {"state": "unavailable", "reason": "metadata_inaccessible"}

    def provenance(self, path: str, fingerprint: str, repository: dict) -> dict:
        return {
            "schema_version": 1,
            "adapter": self.adapter,
            "path": path,
            "sha256": fingerprint,
            "repository": repository,
        }

    @staticmethod
    def _patterns(values: object, *, allow_empty: bool = False) -> list[_Pattern]:
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
        return self._matches_file(relative) and not self._ignored(relative)

    def _matches_file(self, relative: str) -> bool:
        return (
            Path(relative).suffix.lower() in (".json", ".csv")
            and any(pattern.fullmatch(relative) for pattern in self.include)
            and not any(pattern.fullmatch(relative) for pattern in self.exclude)
        )

    def watch_filter(self, relative: str) -> bool:
        """Use the registration's rule snapshot without filesystem reads on its thread."""
        _relative(relative)
        return self._matches_file(relative) and not self._ignore.ignored(relative)

    def watch_rule(self, directory: Path) -> bytes | None:
        relative = directory.relative_to(self.root).as_posix()
        key = "" if relative == "." else relative
        self._ignore_directory(key)
        return self._ignore_checked.get(key)

    def directory_identity(self, directory: Path) -> tuple[int, int]:
        """Revalidate each admitted ancestor before registering a directory handle."""
        parts = directory.relative_to(self.root).parts
        for end in range(1, len(parts) + 1):
            relative = "/".join(parts[:end])
            _relative(relative)
            if (
                not self._could_descend(relative)
                or any(p.fullmatch(relative) or p.fullmatch(relative + "/") for p in self.exclude)
                or self._ignored(relative, directory=True)
            ):
                raise PermissionError("Directory is outside the external source policy")
        info = check_owned_path(directory).stat()
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(directory)
        return info.st_dev, info.st_ino

    def _entry_inodes(self, directory, wanted, check):
        found = {}
        with os.scandir(directory) as entries:
            for entry in entries:
                check()
                self.budget.admit_items(1)
                if entry.name in wanted:
                    found[entry.name] = entry.inode()
                    if len(found) == len(wanted):
                        break
        return found

    def changed_files(self, paths, directories, previous, check):
        """Certify literal spellings by listing only affected admitted ancestor directories."""
        wanted = {}
        for relative in paths:
            parts = _relative(relative)
            if len(parts) > len(directories):
                raise ValueError("External directory registration changed")
            for depth, name in enumerate(parts):
                wanted.setdefault(parts[:depth], set()).add(name)

        observed = {}
        for parent in sorted(wanted, key=len):
            check()
            directory = self.root.joinpath(*parent)
            expected = directories.get(str(directory))
            if expected is None or (
                parent and observed[parent[:-1]].get(parent[-1]) != expected[1]
            ):
                raise ValueError("External directory spelling or identity changed")
            if self.directory_identity(directory) != expected:
                raise ValueError("External directory identity changed")
            observed[parent] = self._entry_inodes(directory, wanted[parent], check)
            if self.directory_identity(directory) != expected:
                raise ValueError("External directory changed during enumeration")

        files, identities = [], {}
        for relative in paths:
            check()
            parts = _relative(relative)
            path = self.root.joinpath(*parts)
            if parts[-1] not in observed[parts[:-1]]:
                # A tombstone can retire only an exact former publication. An
                # unknown spelling, or a live alias, needs canonical full traversal.
                if not (previous.get(relative) or {}).get("seen"):
                    raise ValueError("Unverified external tombstone")
                try:
                    check_owned_path(path).lstat()
                except FileNotFoundError:
                    continue
                raise ValueError("External filename spelling changed")
            if self.admitted(relative):
                info = check_owned_path(path).stat()
                if stat.S_ISDIR(info.st_mode):
                    raise IsADirectoryError(path)
                inode = observed[parts[:-1]][parts[-1]]
                if not inode or inode != info.st_ino:
                    raise ValueError("External file identity changed during enumeration")
                identities[relative] = (directories[str(path.parent)], (info.st_dev, inode))
                files.append(relative)
        self._changed_identities = identities
        self.guard_event_reads(directories, check)
        return files

    def guard_event_reads(self, directories, check):
        self._event_directories, self._event_check = directories, check

    def confirm_event_entry(self, relative, identity):
        """Bind an event-driven read/publication to current literal entries and rule bytes."""
        if self._event_check is None:
            return
        parts = _relative(relative)
        directory = self.root
        try:
            for depth, name in enumerate(parts):
                self._event_check()
                before = check_owned_path(directory).stat()
                parent_id = (before.st_dev, before.st_ino)
                if self._event_directories.get(str(directory), parent_id) != parent_id:
                    raise RevisionConflict("External ancestor changed during admission")
                if self.respect_gitignore:
                    parent = "/".join(parts[:depth])
                    raw = self._read_ignore(parent)
                    digest = hashlib.sha256(raw).digest() if raw is not None else None
                    if parent not in self._ignore_checked or self._ignore_checked[parent] != digest:
                        raise RevisionConflict("External ignore rules changed during admission")
                inode = self._entry_inodes(directory, {name}, self._event_check).get(name)
                if not inode:
                    raise RevisionConflict("External directory entry spelling changed")
                after = check_owned_path(directory).stat()
                directory = directory / name
                child = check_owned_path(directory).stat()
                if parent_id != (after.st_dev, after.st_ino) or child.st_ino != inode:
                    raise RevisionConflict("External directory entry changed during admission")
            if identity is not None and (child.st_dev, child.st_ino) != identity:
                raise RevisionConflict("Opened external file differs from its directory entry")
        except RevisionConflict:
            raise
        except (OSError, ValueError) as error:
            raise RevisionConflict("External directory entry cannot be verified") from error

    def hint_matcher(self, hints: tuple[str, ...]):
        """Match alternate event spellings against files already admitted by traversal."""

        exact = set(hints)
        dirty_directories = {
            "" if hint == ".//" else hint[:-2] for hint in exact if hint.endswith("//")
        }
        by_parent = {}
        for hint in exact:
            if not hint.endswith("/"):
                parts = _relative(hint)
                by_parent.setdefault("/".join(parts[:-1]), []).append(parts[-1])

        @lru_cache(maxsize=512)
        def identities(parent):
            # A short name or other filesystem alias need not resemble the
            # canonical filename. Probe only bounded hints in an admitted parent.
            observed = set()
            for name in by_parent[parent]:
                try:
                    info = check_owned_path(self.root / parent / name).lstat()
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    return None
                if not info.st_ino:
                    return None
                observed.add((info.st_dev, info.st_ino))
            return observed

        def matches(relative, stamp):
            parent = relative.rpartition("/")[0]
            if relative in exact or parent in dirty_directories or ".///" in exact:
                return True
            if parent not in by_parent:
                return False
            observed = identities(parent)
            return observed is None or not stamp[1] or stamp[:2] in observed

        return matches

    def files(
        self,
        *,
        check: Callable[[], None] | None = None,
        on_directory: Callable[[Path, int], None] | None = None,
    ) -> Iterator[str]:
        def check_scan():
            self.budget.remaining()
            if check is not None:
                check()

        check_scan()
        self.check_access()
        pending = [self.root]
        while pending:
            check_scan()
            directory = pending.pop()
            check_owned_path(directory)
            with os.scandir(directory) as entries:
                items = []
                for entry in entries:
                    check_scan()
                    # Nonmatching and hidden entries still cost enumeration work.
                    self.budget.admit_items(1)
                    self.budget.directory_entries(len(pending) + len(items) + 1)
                    # Incremental heap ordering keeps cancellation checkpoints
                    # between bounded operations instead of one uninterruptible sort.
                    heapq.heappush(items, (entry.name, entry))
            if on_directory is not None:
                on_directory(directory, len(items))
            while items:
                check_scan()
                _, entry = heapq.heappop(items)
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
                if self._ignored(relative, directory=stat.S_ISDIR(info.st_mode)):
                    continue
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
        return any(pattern.could_descend(relative) for pattern in self.include)

    def _file_stat(self, relative):
        path = check_owned_path(self.root.joinpath(*_relative(relative)))
        info = path.stat()
        known = self._changed_identities.get(relative)
        if known:
            parent = path.parent.stat()
            if ((parent.st_dev, parent.st_ino), (info.st_dev, info.st_ino)) != known:
                raise RevisionConflict(
                    "External path changed since its directory entry was observed"
                )
        return path, info

    def read(self, relative: str) -> Document:
        if not self.admitted(relative):
            raise PermissionError("File is outside the source include/exclude policy")
        self.check_access()
        path, before = self._file_stat(relative)
        self.budget.check_source(before.st_size, 0)
        with _regular_descriptor(path, before) as descriptor:
            if self._event_check is not None:
                opened = os.fstat(descriptor)
                self.confirm_event_entry(relative, (opened.st_dev, opened.st_ino))
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = self._read_bytes(stream)
        return Document(raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest())

    def stamp(self, relative: str) -> tuple[int, ...]:
        """Recheck current file access without loading unchanged configuration."""
        if not self.admitted(relative):
            raise PermissionError("File is outside the source include/exclude policy")
        self.check_access()
        path, before = self._file_stat(relative)
        with _regular_descriptor(path, before):
            pass
        return (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )


def _unique_object(
    pairs: list[tuple[str, object]], check: Callable[[], None] = lambda: None
) -> dict:
    value = {}
    for key, item in pairs:
        check()
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _admit_json_nodes(text: str, check: Callable[[], None], visit: Callable[[], None]) -> None:
    """Charge the root, the first child of each nonempty container, and every sibling."""
    visit()
    for marker in _JSON_NODE_MARKERS.finditer(text):
        check()
        if text[marker.start()] != '"':
            visit()


def _values(
    relative: str,
    text: str,
    check: Callable[[], None] = lambda: None,
    visit: Callable[[], None] = lambda: None,
) -> Iterator[tuple[str, str | int, int, str]]:
    check()
    if Path(relative).suffix.lower() == ".json":

        def number(value: str) -> int:
            check()
            return int(value)

        def unique_object(pairs: list[tuple[str, object]]) -> dict:
            check()
            return _unique_object(pairs, check)

        def float_number(value: str) -> float:
            check()
            return float(value)

        _admit_json_nodes(text, check, visit)
        document = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_int=number,
            parse_float=float_number,
            parse_constant=float_number,
        )
        check()
        pending = [("", iter(((None, document),)))]
        while pending:
            check()
            parent, children = pending[-1]
            try:
                key, value = next(children)
            except StopIteration:
                pending.pop()
                continue
            path = (
                parent
                if key is None
                else parent + "/" + str(key).replace("~", "~0").replace("/", "~1")
            )
            if isinstance(value, dict):
                pending.append((path, iter(value.items())))
            elif isinstance(value, list):
                pending.append((path, iter(enumerate(value))))
            elif isinstance(value, str) or type(value) is int:
                yield path, value, 0, path
    elif Path(relative).suffix.lower() == ".csv":
        # csv exposes only a process-wide field limit. Raise it monotonically
        # to the already-admitted document's size; restoring a lower value would
        # interrupt another scope's active reader. This allocates no buffer.
        with _CSV_LIMIT_LOCK:
            if len(text) > csv.field_size_limit():
                csv.field_size_limit(len(text))
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        check()
        fieldnames = reader.fieldnames
        check()
        if fieldnames is None:
            raise ValueError("CSV requires distinct column names")
        names = set()
        for name in fieldnames:
            check()
            visit()
            if name in names:
                raise ValueError("CSV requires distinct column names")
            names.add(name)
        for ordinal, row in enumerate(reader):
            check()
            visit()
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row does not match its header")
            for field_name, value in row.items():
                check()
                visit()
                pointer = (
                    "/rows/" + str(ordinal) + "/" + field_name.replace("~", "~0").replace("/", "~1")
                )
                yield field_name, value, reader.line_num, pointer
    else:
        raise ValueError("Only JSON and CSV sources are supported")


def _field_matches(pattern: str, field_name: str) -> bool:
    expected, actual = pattern.split("/"), field_name.split("/")
    return len(expected) == len(actual) and all(
        left == "*" or left == right for left, right in zip(expected, actual)
    )


def document_events(
    relative: str,
    text: str,
    catalog: Catalog,
    mappings: list[dict],
    *,
    check: Callable[[], None] = lambda: None,
    visit: Callable[[], None] = lambda: None,
) -> Iterator[Edge | dict]:
    """Charge catalog expansion before retaining targets, and stream per-occurrence evidence."""
    validate_mappings(mappings)
    seen = set()

    def visit_match():
        check()
        visit()

    for field_name, value, line, pointer in _values(relative, text, check, visit):
        if not mappings and isinstance(value, str):
            targets = set()
            for scheme in ("resources", "addressables", "addressables_label", "path", "guid"):
                for target in catalog.keys.get((scheme, catalog.canonical_key(scheme, value)), ()):
                    visit_match()
                    targets.add(target)
            for target in sorted(targets):
                check()
                yield Edge(
                    target,
                    "external_key_candidate",
                    value,
                    line,
                    "possible",
                    field_name,
                    source_pointer=pointer,
                )
        for i, mapping in enumerate(mappings):
            visit_match()
            if not _field_matches(mapping["field"], field_name):
                continue
            seen.add(i)
            mapped = mapping.get("values", {}).get(str(value)) if "values" in mapping else value
            if not isinstance(mapped, str):
                yield {
                    "field": field_name,
                    "line": line,
                    "reason": "unmapped_value" if "values" in mapping else "non_string_value",
                    "source_pointer": pointer,
                }
                continue
            key = mapping.get("prefix", "") + mapped + mapping.get("suffix", "")
            targets, reason, subobject = catalog.resolve_detail(
                mapping["scheme"], key, mapping.get("mode", "single"), visit=visit_match
            )
            if reason and reason != "excluded":
                yield {"field": field_name, "line": line, "reason": reason, "key_redacted": True}
            for target in sorted(targets):
                check()
                yield Edge(
                    target,
                    "external_mapped_key",
                    key,
                    line,
                    "possible",
                    field_name,
                    source_pointer=pointer,
                    subobject=subobject,
                )
    for i, mapping in enumerate(mappings):
        if i not in seen:
            visit_match()
            yield {"field": mapping["field"], "reason": "mapping_not_found"}


def document_coverage(mappings: list[dict]) -> str:
    return "mapped_fields" if mappings else "exact_catalog_strings"


def analyze_document(
    relative: str,
    text: str,
    catalog: Catalog,
    mappings: list[dict],
    *,
    check: Callable[[], None] = lambda: None,
    visit: Callable[[], None] = lambda: None,
) -> Analysis:
    result = Analysis(coverage=document_coverage(mappings))
    for event in document_events(relative, text, catalog, mappings, check=check, visit=visit):
        if isinstance(event, Edge):
            result.edges.append(event)
        else:
            result.unresolved.append(event)
    return result
