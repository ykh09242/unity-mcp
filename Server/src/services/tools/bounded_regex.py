"""Budgets for caller-controlled patterns used by script search and editing."""

from itertools import islice
import re

import regex

MAX_PATTERN_CHARS = 2048
MAX_TEXT_CHARS = 2_000_000
MAX_MATCHES = 10_000
TIMEOUT_SECONDS = 0.1


def _compile(pattern: str, text: str, flags: int):
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"Regex pattern exceeds {MAX_PATTERN_CHARS} characters")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"Regex input exceeds {MAX_TEXT_CHARS} characters")
    repetitions = re.findall(r"\{(\d*)(?:,(\d*))?\}", pattern)
    if any(int(value) > MAX_TEXT_CHARS for bounds in repetitions for value in bounds if value):
        raise ValueError("Regex repetition exceeds the input size limit")
    return regex.compile(pattern, flags | regex.VERSION0)


def find_matches(pattern: str, text: str, flags: int = 0, limit: int = MAX_MATCHES):
    compiled = _compile(pattern, text, flags)
    matches = list(islice(compiled.finditer(text, timeout=TIMEOUT_SECONDS), limit + 1))
    if len(matches) > limit:
        raise ValueError(f"Regex exceeds the {limit} match limit; use a narrower pattern")
    return matches


def search(pattern: str, text: str, flags: int = 0):
    return _compile(pattern, text, flags).search(text, timeout=TIMEOUT_SECONDS)


def substitute(pattern: str, replacement: str, text: str, count: int, flags: int = 0):
    if count < 0:
        raise ValueError("Regex replacement count must be nonnegative")
    if len(replacement) + replacement.count("\\") * len(text) > MAX_TEXT_CHARS:
        raise ValueError("Regex replacement expansion exceeds the output size limit")
    compiled = _compile(pattern, text, flags)
    # Bound replacement expansion before allocating a potentially enormous result.
    pieces = []
    cursor = 0
    length = 0
    for i, match in enumerate(compiled.finditer(text, timeout=TIMEOUT_SECONDS)):
        if count and i >= count:
            break
        if i >= MAX_MATCHES:
            raise ValueError("Regex replacement exceeds the match limit")
        part = text[cursor:match.start()] + match.expand(replacement)
        length += len(part)
        if length > MAX_TEXT_CHARS:
            raise ValueError("Regex replacement exceeds the output size limit")
        pieces.append(part)
        cursor = match.end()
    length += len(text) - cursor
    if length > MAX_TEXT_CHARS:
        raise ValueError("Regex replacement exceeds the output size limit")
    pieces.append(text[cursor:])
    return "".join(pieces)
