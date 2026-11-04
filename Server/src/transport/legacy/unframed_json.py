"""Incrementally locate one legacy JSON boundary without building a value graph."""

import re

_STRING_SPECIAL = re.compile(rb'["\\]')
_SPACE = b" \t\r\n"


class UnframedJsonBoundary:
    """Visit only each new byte slab; full syntax/UTF-8 validation happens once."""

    def __init__(self, max_depth: int):
        self.max_depth = max_depth
        self.stack = []
        self.root = None
        self.quoted = False
        self.escaped = False
        self.complete = False

    def feed(self, chunk: bytes) -> bool:
        index = 0
        while index < len(chunk):
            if self.quoted:
                if self.escaped:
                    index += 1
                    self.escaped = False
                    continue
                match = _STRING_SPECIAL.search(chunk, index)
                if match is None:
                    break
                index = match.end()
                if match[0] == b"\\":
                    self.escaped = True
                else:
                    self.quoted = False
                    if self.root == "string":
                        self.complete = True
                continue
            char = chunk[index]
            index += 1
            if char in _SPACE:
                if self.root == "scalar":
                    self.complete = True
                continue
            if self.complete:
                raise ValueError("Trailing data after unframed JSON response")
            if self.root is None:
                self.root = "string" if char == 34 else "container" if char in b"[{" else "scalar"
            if self.root == "scalar":
                continue  # A number can grow across slabs; require whitespace/EOF.
            if char == 34:
                self.quoted = True
            elif char in b"[{":
                self.stack.append(char)
                if len(self.stack) > self.max_depth:
                    raise ValueError("Unity response exceeds structural limits")
            elif char in b"]}":
                if not self.stack or self.stack.pop() != (91 if char == 93 else 123):
                    raise ValueError("Mismatched unframed JSON container")
                if not self.stack:
                    self.complete = True
        return self.complete

    def at_eof(self) -> bool:
        return self.complete or self.root == "scalar"
