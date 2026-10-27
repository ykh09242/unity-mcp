"""Compiled, root-relative Gitignore matching without Git or optional native backends."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from functools import partial
import math
import os
import threading
from typing import Final

from pathspec import GitIgnoreSpec
from pathspec.pattern import RegexMatchResult
from pathspec.patterns.gitignore.spec import GitIgnoreSpecPattern
import regex

DIRECTORY_CACHE_SIZE: Final = 8192
PATTERN_SECONDS: Final = 2.0
PATTERN_SECONDS_ENV: Final = "UNITY_MCP_REFERENCE_GRAPH_PATTERN_SECONDS"


def _pattern_seconds() -> float:
    """The server operator can provision individual searches without extending a whole job."""
    try:
        seconds = float(os.environ.get(PATTERN_SECONDS_ENV, PATTERN_SECONDS))
    except ValueError as error:
        raise ValueError(f"{PATTERN_SECONDS_ENV} must be a positive finite number") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{PATTERN_SECONDS_ENV} must be a positive finite number")
    return seconds


class _BoundedPattern(GitIgnoreSpecPattern):
    """Keep pathspec syntax/priority while bounding searches through the existing regex dependency."""

    def __init__(self, pattern: str, *, seconds: float):
        super().__init__(pattern)
        self._safe_regex = regex.compile(self.regex.pattern, regex.VERSION0) if self.regex else None
        self._seconds = seconds

    def match_file(self, file: str):
        if self._safe_regex is None:
            return None
        try:
            match = self._safe_regex.search(file, timeout=self._seconds)
        except TimeoutError as error:
            raise ValueError(
                f"Ignore pattern matching exceeded {self._seconds} seconds; evidence is incomplete. Raise {PATTERN_SECONDS_ENV} to provision matching."
            ) from error
        return RegexMatchResult(match) if match is not None else None


def rule_directory(path: str) -> str:
    """Accept only collector/grant-contained logical .gitignore identities."""
    parts = path.split("/")
    if (
        not path
        or "\\" in path
        or ":" in path
        or any(not part or part in (".", "..") for part in parts)
        or any(ord(character) < 32 for character in path)
        or parts[-1] != ".gitignore"
    ):
        raise ValueError("Ignore rule files require root-relative .gitignore paths")
    return "/".join(parts[:-1])


class GitIgnoreRules:
    """Evaluate each ancestor before descendants, preserving Git's parent exclusion rule."""

    def __init__(self, files: Iterable[tuple[str, str]], *, cache_size: int = DIRECTORY_CACHE_SIZE):
        self._specs: dict[str, GitIgnoreSpec] = {}
        self._files: set[str] = set()
        self._directories: OrderedDict[str, bool] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_size = cache_size
        self._seconds = _pattern_seconds()
        for path, content in files:
            self.add(path, content)

    def add(self, path: str, content: str) -> None:
        """Add a safely read rule file; used lazily while walking an external grant."""
        directory = rule_directory(path)
        if directory in self._files:
            raise ValueError("Duplicate ignore rule file")
        self._files.add(directory)
        spec = GitIgnoreSpec.from_lines(
            content.splitlines(),
            pattern_factory=partial(_BoundedPattern, seconds=self._seconds),
            backend="simple",
        )
        if any(pattern.include is not None for pattern in spec.patterns):
            self._specs[directory] = spec
        with self._cache_lock:
            self._directories.clear()

    def _matched(self, path: str, *, directory: bool) -> bool:
        ignored = False
        parts = path.split("/")
        for depth in range(len(parts)):
            scope = "/".join(parts[:depth])
            spec = self._specs.get(scope)
            if spec is not None:
                relative = "/".join(parts[depth:]) + ("/" if directory else "")
                result = spec.check_file(relative, separators=("/",)).include
                if result is not None:
                    ignored = result
        return ignored

    def ignored(self, path: str, *, directory: bool = False) -> bool:
        """Match a logical POSIX/Windows path without reading the filesystem."""
        if not self._specs:
            return False
        path = path.replace("\\", "/").rstrip("/")
        parts = path.split("/")
        if any(not part or part in (".", "..") for part in parts) or ":" in path:
            raise ValueError("Ignore matching requires a root-relative logical path")
        # The cache contains only ancestor decisions, never the complete path inventory.
        for depth in range(1, len(parts) + int(directory)):
            parent = "/".join(parts[:depth])
            with self._cache_lock:
                excluded = self._directories.get(parent)
                if excluded is not None:
                    self._directories.move_to_end(parent)
            if excluded is None:
                excluded = self._matched(parent, directory=True)
                with self._cache_lock:
                    self._directories[parent] = excluded
                    if len(self._directories) > self._cache_size:
                        self._directories.popitem(last=False)
            if excluded:
                return True
        return False if directory else self._matched(path, directory=False)
