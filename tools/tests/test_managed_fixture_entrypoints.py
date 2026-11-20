"""Guard the managed process boundary for source-linked and SDK test executables."""

import os
import re
from pathlib import Path

FIXTURES = Path(__file__).with_name("fixtures")
ENTRY_POINT = re.compile(
    r"^(?P<indent>[ \t]*)[^\n]*\bstatic\s+(?:async\s+)?"
    r"(?:int|void|Task(?:<int>)?)\s+Main\([^)]*\)\s*\{(?P<body>.*?)^(?P=indent)\}",
    re.MULTILINE | re.DOTALL,
)


def test_managed_fixture_entrypoints_have_an_exception_boundary() -> None:
    # Include the entry points in loose source fixtures compiled by csc or Mono runners.
    unguarded = []
    for directory, children, names in os.walk(FIXTURES):
        children[:] = [child for child in children if child not in {"bin", "obj"}]
        for name in names:
            if not name.endswith(".cs"):
                continue
            path = Path(directory) / name
            source = path.read_text(encoding="utf-8-sig")
            for entry in ENTRY_POINT.finditer(source):
                indent = re.escape(entry["indent"] + "    ")
                catch = rf"^{indent}catch\s*\((?:System\.)?Exception\s+\w+\)\s*\{{"
                body = entry["body"]
                if not re.match(r"\s*try\s*\{", body) or not re.search(catch, body, re.MULTILINE):
                    line = source.count("\n", 0, entry.start()) + 1
                    unguarded.append(f"{path.relative_to(FIXTURES)}:{line}")
    assert not unguarded, "Managed exceptions can escape Main in:\n" + "\n".join(sorted(unguarded))
