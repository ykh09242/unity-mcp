"""Match exact Git spellings to entries enumerated by the host filesystem."""

import os
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from cli.utils.play_scenario_analysis_io import safe_local_path


class CheckoutPathAlias(ValueError):
    """A host lookup would open an entry under a different spelling."""


def _stamp(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


class CheckoutPaths:
    """Resolve preselected source names with one bounded-memory scan per parent."""

    def __init__(self, project: Path, paths: Iterable[str]) -> None:
        self.project = safe_local_path(project)
        self._wanted: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for path in paths:
            parts = tuple(path.split("/"))
            for index, part in enumerate(parts):
                self._wanted[parts[:index]].add(part)
        self._entries: dict[tuple[str, ...], set[str]] = {}
        self._stamps: dict[tuple[str, ...], tuple[int, int, int, int]] = {}

    def resolve(self, path: str) -> Path:
        parts = tuple(path.split("/"))
        for index, part in enumerate(parts):
            prefix = parts[:index]
            parent = self.project.joinpath(*prefix)
            if prefix not in self._entries:
                # A child may be a symlink even if its directory entry matches.
                safe_local_path(parent)
                self._stamps[prefix] = _stamp(parent)
                wanted = self._wanted[prefix]
                found = set()
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if entry.name in wanted:
                            found.add(entry.name)
                            if len(found) == len(wanted):
                                break
                self._entries[prefix] = found
            if part not in self._entries[prefix]:
                # lstat follows each filesystem's actual aliasing rules, including
                # case folding, Unicode normalization, and Windows short names.
                # A failed lookup propagates FileNotFoundError as ordinary absence.
                (parent / part).lstat()
                raise CheckoutPathAlias(f"candidate_checkout_path_alias: {path}")
        return self.project.joinpath(*parts)

    def finish(self) -> None:
        if any(
            _stamp(safe_local_path(self.project.joinpath(*prefix))) != before
            for prefix, before in self._stamps.items()
        ):
            raise ValueError("candidate_checkout_directory_changed_during_sample")
