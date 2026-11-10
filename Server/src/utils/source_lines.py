"""Locate search offsets without normalizing Unity's source-line boundaries."""

from collections.abc import Iterable
from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class SourceLine:
    number: int
    start: int
    end: int


def source_line_locations(contents: str, offsets: Iterable[int]) -> dict[int, SourceLine]:
    """Scan boundaries once for selected offsets, including reverse regex results."""
    selected = sorted(set(offsets))
    if not selected:
        return {}

    # Match ManageScript.SourceLineWindow. CRLF is one boundary; offsets still
    # address the original source, including positions within a separator.
    boundaries = re.finditer(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", contents)
    boundary = next(boundaries, None)
    line_number = 1
    line_start = 0
    locations = {}
    for offset in selected:
        while boundary is not None and boundary.end() <= offset:
            line_number += 1
            line_start = boundary.end()
            boundary = next(boundaries, None)
        line_end = boundary.start() if boundary is not None else len(contents)
        locations[offset] = SourceLine(line_number, line_start, line_end)
    return locations
