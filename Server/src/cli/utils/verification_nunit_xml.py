"""Bounded incremental decoding and hashing for retained NUnit XML."""

from codecs import getincrementaldecoder
from collections.abc import Iterator
from dataclasses import dataclass, field
from hashlib import sha256
from typing import BinaryIO
from xml.etree import ElementTree

from .play_scenario_analysis_io import AnalysisError

_CHUNK_SIZE = 64 * 1024
_FORBIDDEN_MARKERS = ("<!DOCTYPE", "<!ENTITY")
_MARKER_OVERLAP = max(map(len, _FORBIDDEN_MARKERS)) - 1


@dataclass(slots=True)
class NUnitXml:
    """Track the digest while yielding XML events from one admitted file."""

    file: BinaryIO
    size: int
    digest: str = field(init=False, default="")

    def events(self) -> Iterator[tuple[str, ElementTree.Element]]:
        parser = ElementTree.XMLPullParser(events=("start", "end"))
        decoder = getincrementaldecoder("utf-8-sig")()
        digest = sha256()
        remaining = self.size
        overlap = ""
        forbidden = False
        parse_error = None
        while True:
            chunk = self.file.read(min(_CHUNK_SIZE, remaining + 1))
            remaining -= len(chunk)
            if remaining < 0 or (not chunk and remaining):
                raise AnalysisError("Artifact changed while reading")
            digest.update(chunk)
            decoded = decoder.decode(chunk, final=not chunk)
            # Check even after a parse error, matching the original UTF-8 and marker
            # checks that ran before parsing. Never feed a completed marker to Expat.
            window = overlap + decoded
            forbidden = forbidden or any(marker in window for marker in _FORBIDDEN_MARKERS)
            overlap = window[-_MARKER_OVERLAP:]
            if not forbidden and parse_error is None:
                try:
                    parser.feed(decoded)
                    yield from parser.read_events()
                    if not chunk:
                        parser.close()
                except ElementTree.ParseError as exc:
                    parse_error = exc
            if not chunk:
                break
        if forbidden:
            raise ValueError("NUnit evidence must not contain a document type or entities")
        if parse_error is not None:
            raise ValueError("Invalid NUnit evidence") from parse_error
        self.digest = digest.hexdigest()
