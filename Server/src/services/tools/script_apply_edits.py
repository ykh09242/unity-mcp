import asyncio
import base64
import hashlib
import re
from bisect import bisect_right
from pathlib import PurePosixPath, PureWindowsPath
from threading import BoundedSemaphore
from typing import Annotated, Any, Callable, TypeVar, Union

from fastmcp import Context
from mcp.types import ToolAnnotations
from core.config import config

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools import bounded_regex
from services.tools.manage_script import _lsp_position_to_line_col, _script_lines_and_starts, _split_uri
from services.tools.refresh_unity import send_mutation, verify_edit_by_sha
from services.tools.utils import parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


_REGEX_WORKERS = BoundedSemaphore(2)
_RegexResult = TypeVar("_RegexResult")


async def _run_regex_work(budget: bounded_regex.WorkBudget, work: Callable[[], _RegexResult]) -> _RegexResult:
    """Keep queued/running script regex work bounded until the worker exits."""
    budget.check()
    if not _REGEX_WORKERS.acquire(blocking=False):
        raise ValueError("Script regex workers are busy; retry later")

    def run() -> _RegexResult:
        try:
            budget.check()
            return work()
        finally:
            _REGEX_WORKERS.release()

    # Preserve this module's asyncio backend. Submit before awaiting so even a
    # queued cancelled request eventually releases admission in the worker.
    try:
        future = asyncio.get_running_loop().run_in_executor(None, run)
    except BaseException:
        _REGEX_WORKERS.release()
        raise
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        budget.cancelled.set()
        future.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
        raise


def _iter_csharp_tokens(text: str):
    """Iterate over C# source text yielding (position, char, is_code, interp_depth).

    A single-pass lexer that handles all C# string/comment variants:
    - Regular strings ("..." with \\ escaping)
    - Verbatim strings (@"..." with "" escaping)
    - Interpolated strings ($"..." with {/} depth tracking, {{/}} escapes)
    - Verbatim interpolated ($@"..." / @$"...")
    - Raw string literals (C# 11: triple+ quotes)
    - Char literals ('...')
    - Single-line comments (//...)
    - Multi-line comments (/*...*/)

    Yields (position, char, is_code, interp_depth) for every character.
    is_code is False inside strings/comments, True for real code.
    interp_depth tracks nesting inside interpolation holes (0 = string content).
    """
    i = 0
    end = len(text)
    dollar_run_end = 0
    while i < end:
        c = text[i]
        nxt = text[i + 1] if i + 1 < end else '\0'

        # Single-line comment
        if c == '/' and nxt == '/':
            yield (i, c, False, 0)
            i += 1
            while i < end and text[i] != '\n':
                yield (i, text[i], False, 0)
                i += 1
            if i < end:
                yield (i, text[i], True, 0)  # newline itself is code
                i += 1
            continue

        # Multi-line comment
        if c == '/' and nxt == '*':
            yield (i, c, False, 0)
            i += 1
            yield (i, text[i], False, 0)
            i += 1
            while i + 1 < end:
                yield (i, text[i], False, 0)
                if text[i] == '*' and text[i + 1] == '/':
                    i += 1
                    yield (i, text[i], False, 0)
                    i += 1
                    break
                i += 1
            else:
                if i < end:
                    yield (i, text[i], False, 0)
                    i += 1
            continue

        # Interpolated raw string: $"""...""" or $$"""...""" etc. (C# 11)
        # Must check BEFORE regular $" and BEFORE plain """
        if c == '$' and i >= dollar_run_end:
            dollar_count = 1
            while i + dollar_count < end and text[i + dollar_count] == '$':
                dollar_count += 1
            after_dollars = i + dollar_count
            # A non-raw dollar run still reaches the ordinary lexer below;
            # avoid probing the same remaining run again at every character.
            dollar_run_end = after_dollars
            if (after_dollars + 2 < end and text[after_dollars] == '"'
                    and text[after_dollars + 1] == '"' and text[after_dollars + 2] == '"'):
                q = 3
                while after_dollars + q < end and text[after_dollars + q] == '"':
                    q += 1
                # Yield all prefix chars ($s and quotes) as non-code
                for _ in range(dollar_count + q):
                    yield (i, text[i], False, 0)
                    i += 1
                # Scan body with interpolation tracking
                interp_depth = 0
                while i < end:
                    ch = text[i]
                    if interp_depth > 0:
                        # Inside interpolation hole — code
                        if ch == '{':
                            interp_depth += 1
                            yield (i, ch, True, interp_depth)
                            i += 1
                        elif ch == '}':
                            yield (i, ch, True, interp_depth)
                            interp_depth -= 1
                            i += 1
                        elif ch == '"':
                            yield (i, ch, False, interp_depth)
                            i += 1
                            while i < end:
                                yield (i, text[i], False, interp_depth)
                                if text[i] == '\\':
                                    i += 1
                                    if i < end:
                                        yield (i, text[i], False, interp_depth)
                                        i += 1
                                    continue
                                if text[i] == '"':
                                    i += 1
                                    break
                                i += 1
                        elif ch == '/' and i + 1 < end and text[i + 1] == '/':
                            yield (i, ch, False, interp_depth)
                            i += 1
                            while i < end and text[i] != '\n':
                                yield (i, text[i], False, interp_depth)
                                i += 1
                        elif ch == '/' and i + 1 < end and text[i + 1] == '*':
                            yield (i, ch, False, interp_depth)
                            i += 1
                            yield (i, text[i], False, interp_depth)
                            i += 1
                            while i + 1 < end and not (text[i] == '*' and text[i + 1] == '/'):
                                yield (i, text[i], False, interp_depth)
                                i += 1
                            if i + 1 < end:
                                yield (i, text[i], False, interp_depth)
                                i += 1
                                yield (i, text[i], False, interp_depth)
                                i += 1
                        else:
                            yield (i, ch, True, interp_depth)
                            i += 1
                        continue
                    # String content (interp_depth == 0)
                    # Check for closing quote sequence
                    if ch == '"':
                        qc = 1
                        while i + qc < end and text[i + qc] == '"':
                            qc += 1
                        if qc >= q:
                            for _ in range(q):
                                yield (i, text[i], False, 0)
                                i += 1
                            break
                        for _ in range(qc):
                            yield (i, text[i], False, 0)
                            i += 1
                        continue
                    # Check for interpolation hole: dollar_count consecutive {'s
                    if ch == '{':
                        bc = 1
                        while i + bc < end and text[i + bc] == '{':
                            bc += 1
                        if bc >= dollar_count:
                            for _ in range(dollar_count):
                                yield (i, text[i], True, 1)
                                i += 1
                            interp_depth = 1
                        else:
                            for _ in range(bc):
                                yield (i, text[i], False, 0)
                                i += 1
                        continue
                    # Closing braces — literal at depth 0
                    if ch == '}':
                        bc = 1
                        while i + bc < end and text[i + bc] == '}':
                            bc += 1
                        for _ in range(bc):
                            yield (i, text[i], False, 0)
                            i += 1
                        continue
                    yield (i, ch, False, 0)
                    i += 1
                continue

        # Raw string literal: """ ... """ (non-interpolated)
        if c == '"' and nxt == '"' and i + 2 < end and text[i + 2] == '"':
            q = 3
            while i + q < end and text[i + q] == '"':
                q += 1
            for _ in range(q):
                yield (i, text[i], False, 0)
                i += 1
            close_count = 0
            while i < end:
                yield (i, text[i], False, 0)
                if text[i] == '"':
                    close_count += 1
                    if close_count >= q:
                        i += 1
                        break
                else:
                    close_count = 0
                i += 1
            continue

        # Interpolated string: $"..." or $@"..." or @$"..."
        if (c == '$' and nxt == '"') or \
           (c == '$' and nxt == '@' and i + 2 < end and text[i + 2] == '"') or \
           (c == '@' and nxt == '$' and i + 2 < end and text[i + 2] == '"'):
            is_verbatim = (nxt == '@') or (c == '@')
            prefix_len = 2 if (c == '$' and nxt == '"') else 3
            for _ in range(prefix_len):
                yield (i, text[i], False, 0)
                i += 1
            interp_depth = 0
            while i < end:
                ch = text[i]
                if interp_depth > 0:
                    # Inside interpolation hole — this is code
                    if ch == '{':
                        interp_depth += 1
                        yield (i, ch, True, interp_depth)
                        i += 1
                    elif ch == '}':
                        yield (i, ch, True, interp_depth)
                        interp_depth -= 1
                        i += 1
                    elif ch == '"':
                        # Nested string inside interpolation hole
                        yield (i, ch, False, interp_depth)
                        i += 1
                        while i < end:
                            yield (i, text[i], False, interp_depth)
                            if text[i] == '\\':
                                i += 1
                                if i < end:
                                    yield (i, text[i], False, interp_depth)
                                    i += 1
                                continue
                            if text[i] == '"':
                                i += 1
                                break
                            i += 1
                    elif ch == '/' and i + 1 < end and text[i + 1] == '/':
                        yield (i, ch, False, interp_depth)
                        i += 1
                        while i < end and text[i] != '\n':
                            yield (i, text[i], False, interp_depth)
                            i += 1
                    elif ch == '/' and i + 1 < end and text[i + 1] == '*':
                        yield (i, ch, False, interp_depth)
                        i += 1
                        yield (i, text[i], False, interp_depth)
                        i += 1
                        while i + 1 < end and not (text[i] == '*' and text[i + 1] == '/'):
                            yield (i, text[i], False, interp_depth)
                            i += 1
                        if i + 1 < end:
                            yield (i, text[i], False, interp_depth)
                            i += 1
                            yield (i, text[i], False, interp_depth)
                            i += 1
                    else:
                        yield (i, ch, True, interp_depth)
                        i += 1
                    continue
                # interp_depth == 0: inside string content
                if ch == '{':
                    if i + 1 < end and text[i + 1] == '{':
                        yield (i, ch, False, 0)
                        i += 1
                        yield (i, text[i], False, 0)
                        i += 1
                        continue
                    interp_depth = 1
                    yield (i, ch, True, interp_depth)
                    i += 1
                    continue
                if ch == '}':
                    if i + 1 < end and text[i + 1] == '}':
                        yield (i, ch, False, 0)
                        i += 1
                        yield (i, text[i], False, 0)
                        i += 1
                        continue
                    yield (i, ch, False, 0)
                    i += 1
                    continue
                if ch == '"':
                    if is_verbatim and i + 1 < end and text[i + 1] == '"':
                        yield (i, ch, False, 0)
                        i += 1
                        yield (i, text[i], False, 0)
                        i += 1
                        continue
                    yield (i, ch, False, 0)
                    i += 1
                    break
                if not is_verbatim and ch == '\\':
                    yield (i, ch, False, 0)
                    i += 1
                    if i < end:
                        yield (i, text[i], False, 0)
                        i += 1
                    continue
                yield (i, ch, False, 0)
                i += 1
            continue

        # Verbatim string: @"..."
        if c == '@' and nxt == '"':
            yield (i, c, False, 0)
            i += 1
            yield (i, text[i], False, 0)
            i += 1
            while i < end:
                yield (i, text[i], False, 0)
                if text[i] == '"':
                    if i + 1 < end and text[i + 1] == '"':
                        i += 1
                        yield (i, text[i], False, 0)
                        i += 1
                        continue
                    i += 1
                    break
                i += 1
            continue

        # Regular string: "..."
        if c == '"':
            yield (i, c, False, 0)
            i += 1
            while i < end:
                yield (i, text[i], False, 0)
                if text[i] == '\\':
                    i += 1
                    if i < end:
                        yield (i, text[i], False, 0)
                        i += 1
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue

        # Char literal: '...'
        if c == '\'':
            yield (i, c, False, 0)
            i += 1
            while i < end:
                yield (i, text[i], False, 0)
                if text[i] == '\\':
                    i += 1
                    if i < end:
                        yield (i, text[i], False, 0)
                        i += 1
                    continue
                if text[i] == '\'':
                    i += 1
                    break
                i += 1
            continue

        # Real code character
        yield (i, c, True, 0)
        i += 1


def _is_in_string_context(text: str, position: int) -> bool:
    """Check if a position in C# source text is inside a string literal or comment."""
    for pos, _, is_code, _ in _iter_csharp_tokens(text):
        if pos == position:
            return not is_code
        if pos > position:
            break
    return False


async def _apply_edits_locally(original_text: str, edits: list[dict[str, Any]]) -> str:
    if len(edits or []) > 32:
        raise ValueError("At most 32 edits are permitted per request")
    text = original_text
    budget = bounded_regex.WorkBudget()
    for edit in edits or []:
        budget.consume(len(text))
        op = (
            (edit.get("op")
             or edit.get("operation")
             or edit.get("type")
             or edit.get("mode")
             or "")
            .strip()
            .lower()
        )

        if not op:
            allowed = "anchor_insert, prepend, append, replace_range, regex_replace"
            raise RuntimeError(
                f"op is required; allowed: {allowed}. Use 'op' (aliases accepted: type/mode/operation)."
            )

        if op == "prepend":
            prepend_text = edit.get("text", "")
            budget.consume(len(prepend_text))
            text = prepend_text + text
        elif op == "append":
            append_text = edit.get("text", "")
            budget.consume(len(append_text))
            text += append_text
        elif op == "anchor_insert":
            anchor = edit.get("anchor", "")
            position = (edit.get("position") or "before").lower()
            insert_text = edit.get("text", "")
            flags = re.MULTILINE | (
                re.IGNORECASE if edit.get("ignore_case") else 0)

            # Find the best match using improved heuristics
            match = await _run_regex_work(budget, lambda: _find_best_anchor_match(
                anchor, text, flags, bool(edit.get("prefer_last", True)), budget=budget))
            if not match:
                if edit.get("allow_noop", True):
                    continue
                raise RuntimeError(f"anchor not found: {anchor}")
            idx = match.start() if position == "before" else match.end()
            budget.consume(len(text) + len(insert_text))
            text = text[:idx] + insert_text + text[idx:]
        elif op == "replace_range":
            start_line = int(edit.get("startLine", 1))
            start_col = int(edit.get("startCol", 1))
            end_line = int(edit.get("endLine", start_line))
            end_col = int(edit.get("endCol", 1))
            replacement = edit.get("text", "")
            budget.consume(len(replacement))
            lines = text.splitlines(keepends=True)
            max_line = len(lines) + 1  # 1-based, exclusive end
            if (start_line < 1 or end_line < start_line or end_line > max_line
                    or start_col < 1 or end_col < 1):
                raise RuntimeError("replace_range out of bounds")

            def index_of(line: int, col: int) -> int:
                if line <= len(lines):
                    return sum(len(l) for l in lines[: line - 1]) + (col - 1)
                return sum(len(l) for l in lines)
            a = index_of(start_line, start_col)
            b = index_of(end_line, end_col)
            text = text[:a] + replacement + text[b:]
        elif op == "regex_replace":
            pattern = edit.get("pattern", "")
            repl = edit.get("replacement", "")
            budget.consume(len(repl))
            # Translate $n backrefs (our input) to Python \g<n>
            repl_py = re.sub(r"\$(\d+)", r"\\g<\1>", repl)
            count = int(edit.get("count", 0))  # 0 = replace all
            flags = re.MULTILINE
            if edit.get("ignore_case"):
                flags |= re.IGNORECASE
            text = await _run_regex_work(budget, lambda: bounded_regex.substitute(
                pattern, repl_py, text, count, flags, budget=budget))
        else:
            allowed = "anchor_insert, prepend, append, replace_range, regex_replace"
            raise RuntimeError(
                f"unknown edit op: {op}; allowed: {allowed}. Use 'op' (aliases accepted: type/mode/operation).")
    budget.consume(len(text))
    return text


class _TextEditError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def _text_edit_spans(contents: str, edits: list[dict[str, Any]], *, mixed: bool = False) -> list[dict[str, Any]]:
    """Build literal edits against the original buffer for write and preview."""
    budget = bounded_regex.WorkBudget()
    budget.consume(len(contents))
    lines, offsets = _script_lines_and_starts(contents)

    def line_col(index: int) -> tuple[int, int]:
        line = bisect_right(offsets, index) - 1
        col = index - offsets[line]
        if col > len(lines[line]):
            raise _TextEditError("invalid_range", "Text edit boundary is inside a CRLF newline")
        return line + 1, col + 1

    spans = []
    for edit in edits:
        budget.check()
        op = edit.get("op", "")
        payload = next((edit[field] for field in ("text", "insert", "content", "replacement")
                        if edit.get(field) is not None), "")
        if op == "replace_range":
            fields = ("startLine", "startCol", "endLine", "endCol")
            if not all(field in edit for field in fields):
                raise _TextEditError("missing_field", "replace_range requires startLine/startCol/endLine/endCol")
            span = {field: int(edit[field]) for field in fields}
        elif op in ("prepend", "append"):
            line, col = line_col(0 if op == "prepend" else len(contents))
            span = {"startLine": line, "startCol": col, "endLine": line, "endCol": col}
        elif op == "regex_replace":
            pattern = edit.get("pattern") or ""
            flags = re.MULTILINE | (re.IGNORECASE if edit.get("ignore_case") else 0)
            # Preserve each existing write route's selection: mixed first match;
            # pure text uses the established best/last anchor selection.
            def select_and_expand():
                if mixed:
                    match = bounded_regex.search(pattern, contents, flags, budget=budget)
                else:
                    match = _find_best_anchor_match(pattern, contents, flags, True, budget=budget)
                if match is None:
                    return match, payload
                budget.consume(len(payload))
                output_length = len(payload)
                if output_length > bounded_regex.MAX_TEXT_CHARS:
                    raise ValueError("Regex replacement exceeds the output size limit")

                def expand(group):
                    nonlocal output_length
                    value = match.group(int(group.group(1))) or ""
                    output_length += len(value) - len(group.group())
                    budget.consume(len(value))
                    if output_length > bounded_regex.MAX_TEXT_CHARS:
                        raise ValueError("Regex replacement exceeds the output size limit")
                    return value

                expanded = re.sub(r"\$(\d+)", expand, payload)
                budget.check()
                return match, expanded

            try:
                match, payload = await _run_regex_work(budget, select_and_expand)
            except Exception as exc:
                raise _TextEditError("bad_regex", f"Invalid regex pattern: {exc}") from exc
            if not match:
                continue
            start_line, start_col = line_col(match.start())
            end_line, end_col = line_col(match.end())
            span = {"startLine": start_line, "startCol": start_col, "endLine": end_line, "endCol": end_col}
        else:
            raise _TextEditError("unsupported_op", f"Unsupported text edit op: {op}")
        budget.consume(len(payload))
        spans.append({**span, "newText": payload})
    budget.check()
    return spans


def _preview_text_spans(contents: str, spans: list[dict[str, Any]]) -> str:
    """Replay atomic codepoint spans without rewriting any requested payload."""
    lines, offsets = _script_lines_and_starts(contents)

    def index(line: int, col: int) -> int:
        if line < 1 or line > len(lines) or col < 1 or col > len(lines[line - 1]) + 1:
            raise ValueError("replace_range out of bounds")
        return offsets[line - 1] + col - 1

    replacements = []
    for span in spans:
        start = index(span["startLine"], span["startCol"])
        end = index(span["endLine"], span["endCol"])
        if end < start:
            raise ValueError("replace_range end precedes start")
        replacements.append((start, end, span["newText"]))
    ordered = sorted(replacements, key=lambda item: item[0], reverse=True)
    for previous, current in zip(ordered, ordered[1:]):
        if current[1] > previous[0]:
            raise ValueError("Text edit spans overlap")
    text = contents
    # Unity's stable descending-start ordering processes same-position inserts
    # in input order, so each subsequent insert appears before its predecessor.
    for start, end, payload in ordered:
        text = text[:start] + payload + text[end:]
    return text


def _find_best_anchor_match(pattern: str, text: str, flags: int, prefer_last: bool = True, *, budget: bounded_regex.WorkBudget | None = None):
    """
    Find the best anchor match using improved heuristics.

    For patterns like \\s*}\\s*$ that are meant to find class-ending braces,
    this function uses heuristics to choose the most semantically appropriate match:

    1. If prefer_last=True, prefer the last match (common for class-end insertions)
    2. Use indentation levels to distinguish class vs method braces
    3. Consider context to avoid matches inside strings/comments

    Args:
        pattern: Regex pattern to search for
        text: Text to search in  
        flags: Regex flags
        prefer_last: If True, prefer the last match over the first

    Returns:
        Match object of the best match, or None if no match found
    """

    # Find all matches
    budget = budget or bounded_regex.WorkBudget()
    matches = bounded_regex.find_matches(pattern, text, flags, budget=budget)
    if not matches:
        return None

    # If only one match, return it
    if len(matches) == 1:
        return matches[0]

    # For patterns that look like they're trying to match closing braces at end of lines
    is_closing_brace_pattern = '}' in pattern and (
        '$' in pattern or pattern.endswith(r'\s*'))

    if is_closing_brace_pattern and prefer_last:
        # Use heuristics to find the best closing brace match
        return _find_best_closing_brace_match(matches, text, budget=budget)

    # Default behavior: use last match if prefer_last, otherwise first match
    return matches[-1] if prefer_last else matches[0]


def _brace_depth_at_positions(text: str, positions: set[int], budget: bounded_regex.WorkBudget | None = None) -> dict[int, int]:
    """Compute the brace depth just before each requested position.

    For every ``}`` in real code at a position in *positions*, stores the
    depth **before** that ``}`` is applied (i.e. the depth it decrements from).

    Returns a dict mapping position -> depth-before.
    """
    depths: dict[int, int] = {}
    budget = budget or bounded_regex.WorkBudget()
    budget.consume(len(text))
    depth = 0
    for pos, c, is_code, _ in _iter_csharp_tokens(text):
        if pos % 1024 == 0:
            budget.check()
        if not is_code:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            if pos in positions:
                depths[pos] = depth
            depth = max(0, depth - 1)
    budget.check()
    return depths


def _find_best_closing_brace_match(matches, text: str, *, budget: bounded_regex.WorkBudget | None = None):
    """
    Find the best closing brace match using brace-depth analysis.

    Scans the text once to compute the actual brace nesting depth at each
    candidate ``}`` position (skipping strings/comments).  Prefers the
    shallowest (outermost) brace — typically the class-closing brace.
    Among equal-depth candidates, prefers the last one (closest to EOF).

    Args:
        matches: List of regex match objects
        text: The full text being searched

    Returns:
        The best match object
    """
    if not matches:
        return None

    budget = budget or bounded_regex.WorkBudget()
    # Find the position of the '}' character within each match, filtering out
    # braces inside strings/comments
    brace_positions: dict[int, object] = {}  # brace_pos → match
    for m in matches:
        budget.consume(m.end() - m.start() + 1)
        offset = text.find('}', m.start(), m.end())
        if offset >= 0:
            brace_positions[offset] = m

    if not brace_positions:
        return None

    # This one lexer pass both filters non-code candidates and records depth.
    depths = _brace_depth_at_positions(text, set(brace_positions), budget)

    # Score: prefer shallowest depth (outermost brace), then latest position
    best_match = None
    best_key = (float('inf'), -1)  # (depth, -position) — lower is better
    for pos, m in brace_positions.items():
        budget.consume(1)
        if pos not in depths:
            continue
        d = depths[pos]
        key = (d, -pos)  # lower depth wins, then later position wins
        if key < best_key:
            best_key = key
            best_match = m

    budget.check()
    return best_match


def _infer_class_name(script_name: str) -> str:
    # Default to script name as class name (common Unity pattern)
    return (script_name or "").strip()


def _extract_code_after(keyword: str, request: str) -> str:
    # Deprecated with NL removal; retained as no-op for compatibility
    idx = request.lower().find(keyword)
    if idx >= 0:
        return request[idx + len(keyword):].strip()
    return ""
# Removed _is_structurally_balanced - validation now handled by C# side using Unity's compiler services


def _normalize_script_locator(name: str, path: str) -> tuple[str, str]:
    """Best-effort normalization of script "name" and "path".

    Accepts any of:
    - name = "SmartReach", path = "Assets/Scripts/Interaction"
    - name = "SmartReach.cs", path = "Assets/Scripts/Interaction"
    - name = "Assets/Scripts/Interaction/SmartReach.cs", path = ""
    - path = "Assets/Scripts/Interaction/SmartReach.cs" (name empty)
    - name or path using uri prefixes: mcpforunity://path/..., file://...
    - accidental duplicates like "Assets/.../SmartReach.cs/SmartReach.cs"

    Returns (name_without_extension, directory_path_under_Assets).
    """
    n = (name or "").strip().replace("\\", "/")
    p = (path or "").strip().replace("\\", "/")
    if "/" in n:
        candidate = n
    elif p.lower().endswith(".cs"):
        candidate = p
    else:
        candidate = f"{p.rstrip('/') or 'Assets'}/{n}"

    # Retain tolerance for an accidentally repeated file name, then use the
    # same URI decoding and Assets-relative locator as the other script tools.
    parts = candidate.split("/")
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        candidate = "/".join(parts[:-1])
    if not candidate.lower().endswith(".cs"):
        candidate += ".cs"
    return _split_uri(candidate)


def _with_norm(resp: dict[str, Any] | Any, edits: list[dict[str, Any]], routing: str | None = None) -> dict[str, Any] | Any:
    if not isinstance(resp, dict):
        return resp
    data = resp.setdefault("data", {})
    data.setdefault("normalizedEdits", edits)
    if routing:
        data["routing"] = routing
    return resp


def _err(code: str, message: str, *, expected: dict[str, Any] | None = None, rewrite: dict[str, Any] | None = None,
         normalized: list[dict[str, Any]] | None = None, routing: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"success": False,
                               "code": code, "message": message}
    data: dict[str, Any] = {}
    if expected:
        data["expected"] = expected
    if rewrite:
        data["rewrite_suggestion"] = rewrite
    if normalized is not None:
        data["normalizedEdits"] = normalized
    if routing:
        data["routing"] = routing
    if extra:
        data.update(extra)
    if data:
        payload["data"] = data
    return payload


def _prepared_handoff(response: Any, unity_instance: str | None, expected_path: str) -> dict[str, Any]:
    """Verify a complete Unity snapshot; local native eligibility remains unproven."""
    if isinstance(response, dict) and not response.get("success"):
        return response
    try:
        data = dict(response["data"])
        if (data.get("preview") is not True or data.get("complete") is not True
                or data.get("truncated") is not False or data.get("editsApplied") != 0
                or data.get("scheduledRefresh") is not False):
            raise ValueError("Response is not a complete read-only preparation")
        original, candidate = data["original_contents"], data["new_contents"]
        if not isinstance(original, str) or not isinstance(candidate, str):
            raise ValueError("Complete original and proposed contents are required")
        original_bytes = original.encode("utf-8")
        original_sha = hashlib.sha256(original_bytes).hexdigest()
        candidate_bytes = candidate.encode("utf-8")
        candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
        if len(original_bytes) + len(candidate_bytes) > 1024 * 1024:
            raise ValueError("Complete original/candidate exceeds the 1 MiB preparation payload limit")
        if (data.get("original_sha256") != original_sha or data.get("sha256") != original_sha
                or data.get("candidate_sha256") != candidate_sha
                or data.get("candidate_bytes_sha256") != candidate_sha
                or data.get("encoding") != "utf-8" or data.get("bom") is not False):
            raise ValueError("Candidate contents, hashes or encoding are inconsistent")
        path_type = PureWindowsPath if PureWindowsPath(data["absolute_path"]).drive else PurePosixPath
        absolute = path_type(data["absolute_path"])
        root = path_type(data["project_root"])
        relative = path_type(data["path"])
        if (not absolute.is_absolute() or not root.is_absolute() or relative.is_absolute()
                or ".." in relative.parts or ".." in root.parts or ".." in absolute.parts
                or not relative.parts or path_type(relative.parts[0]) != path_type("Assets")
                or absolute != root / relative or relative != path_type(expected_path)):
            raise ValueError("Unity target identity does not match the requested Assets path")
        no_op = original == candidate
        if data.get("no_op") is not no_op:
            raise ValueError("No-op flag does not match the complete candidate")
        prepared_count = data.get("editsPrepared")
        if type(prepared_count) is not int or prepared_count < 0 or (prepared_count == 0) is not no_op:
            raise ValueError("Prepared edit count does not match the no-op status")
        import difflib
        from itertools import islice
        diff = list(islice(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True), fromfile="before", tofile="after", n=3), 2001))
        data["diff_truncated"] = len(diff) > 2000
        data["diff"] = "".join(diff[:2000]) + ("... (diff truncated) ..." if data["diff_truncated"] else "")
        data["unity_instance"] = unity_instance
        status = "not_needed" if no_op else "unavailable_remote" if config.http_remote_hosted else "verification_required"
        data["native_apply"] = {
            "status": status,
            "requirements": ["same local Unity host and permitted workspace", "original logical SHA matches immediately before writing", "native tool preserves exact candidate bytes", "candidate raw byte SHA matches before Unity validation/refresh"],
            "fallback": "Use the original edit request without options.preview for a direct Unity edit when native application cannot preserve exact bytes or local provenance is unproven.",
        }
        return {**response, "message": "Prepared only; no changes applied or refresh scheduled.", "data": data}
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        return _err("invalid_preview", f"No usable native proposal: {exc}; no changes were applied.")

@mcp_for_unity_tool(
    name="script_apply_edits",
    unity_target="manage_script",
    description=(
        """Structured C# edits (methods/classes) with safer boundaries - prefer this over raw text.
    Best practices:
    - Method/class edits adapt to local indentation and line endings without changing string values.
    - Range, anchor, regex, prepend and append payloads are literal: include all desired whitespace/newlines.
    - options.preview=true prepares complete proposed contents, target paths and hashes without applying changes.
    - Mixed text/structured preview is unsupported. Direct edits keep their existing behavior.
    - For Codex-native file visibility, verify local target/workspace and original SHA, use an exposed native file tool only if it preserves exact candidate bytes, then verify candidate_bytes_sha256 before Unity validate/refresh.
    - Native patch capabilities vary: CRLF, BOM and missing final newline may require a byte-preserving tool or direct Unity edit fallback. Never normalize string bytes or fabricate file events.
    - Prefer anchor_* ops for pattern-based insert/replace near stable markers
    - Use replace_method/delete_method for whole-method changes (keeps signatures balanced)
    - Avoid whole-file regex deletes; validators will guard unbalanced braces
    - For tail insertions, prefer anchor/regex_replace on final brace (class closing)
    - Pass options.validate='standard' for structural checks; 'basic' for interior-only edits
    Canonical fields (use these exact keys):
    - op: replace_method | insert_method | delete_method | anchor_insert | anchor_delete | anchor_replace
    - className: string (defaults to 'name' if omitted on method/class ops)
    - methodName: string (required for replace_method, delete_method)
    - replacement: string (required for replace_method, insert_method)
    - position: start | end | after | before (insert_method only)
    - afterMethodName / beforeMethodName: string (required when position='after'/'before')
    - anchor: regex string (for anchor_* ops)
    - text: string (for anchor_insert/anchor_replace)
    Examples:
    1) Replace a method:
    {
        "name": "SmartReach",
        "path": "Assets/Scripts/Interaction",
        "edits": [
        {
        "op": "replace_method",
        "className": "SmartReach",
        "methodName": "HasTarget",
        "replacement": "public bool HasTarget(){ return currentTarget!=null; }"
        }
    ],
    "options": {"validate": "standard", "refresh": "immediate"}
    }
    "2) Insert a method after another:
    {
        "name": "SmartReach",
        "path": "Assets/Scripts/Interaction",
        "edits": [
        {
        "op": "insert_method",
        "className": "SmartReach",
        "replacement": "public void PrintSeries(){ Debug.Log(seriesName); }",
        "position": "after",
        "afterMethodName": "GetCurrentTarget"
        }
    ],
    }
    ]"""
    ),
    annotations=ToolAnnotations(
        title="Script Apply Edits",
        destructiveHint=True,
    ),
)
async def script_apply_edits(
    ctx: Context,
    name: Annotated[str, "Name of the script to edit"],
    path: Annotated[str, "Path to the script to edit under Assets/ directory"],
    edits: Annotated[Union[list[dict[str, Any]], str], "List of edits to apply to the script (JSON list or stringified JSON)"],
    options: Annotated[dict[str, Any],
                       "Options for the script edit"] | None = None,
    script_type: Annotated[str,
                           "Type of the script to edit"] = "MonoBehaviour",
    namespace: Annotated[str,
                         "Namespace of the script to edit"] | None = None,
) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    await ctx.info(
        f"Processing script_apply_edits: {name} (unity_instance={unity_instance or 'default'})")

    # Parse edits if they came as a stringified JSON
    edits = parse_json_payload(edits)
    if not isinstance(edits, list):
        return {"success": False, "message": f"Edits must be a list or JSON string of a list, got {type(edits)}"}
    if len(edits) > 32:
        return {"success": False, "message": "At most 32 edits are permitted per request"}

    # Normalize locator first so downstream calls target the correct script file.
    name, path = _normalize_script_locator(name, path)
    # Normalize unsupported or aliased ops to known structured/text paths

    def _unwrap_and_alias(edit: dict[str, Any]) -> dict[str, Any]:
        # Unwrap single-key wrappers like {"replace_method": {...}}
        for wrapper_key in (
            "replace_method", "insert_method", "delete_method",
            "replace_class", "delete_class",
            "anchor_insert", "anchor_replace", "anchor_delete",
        ):
            if wrapper_key in edit and isinstance(edit[wrapper_key], dict):
                inner = dict(edit[wrapper_key])
                inner["op"] = wrapper_key
                edit = inner
                break

        e = dict(edit)
        op = (e.get("op") or e.get("operation") or e.get(
            "type") or e.get("mode") or "").strip().lower()
        if op:
            e["op"] = op

        # Common field aliases
        if "class_name" in e and "className" not in e:
            e["className"] = e.pop("class_name")
        if "class" in e and "className" not in e:
            e["className"] = e.pop("class")
        if "method_name" in e and "methodName" not in e:
            e["methodName"] = e.pop("method_name")
        # Some clients use a generic 'target' for method name
        if "target" in e and "methodName" not in e:
            e["methodName"] = e.pop("target")
        if "method" in e and "methodName" not in e:
            e["methodName"] = e.pop("method")
        if "new_content" in e and "replacement" not in e:
            e["replacement"] = e.pop("new_content")
        if "newMethod" in e and "replacement" not in e:
            e["replacement"] = e.pop("newMethod")
        if "new_method" in e and "replacement" not in e:
            e["replacement"] = e.pop("new_method")
        if "content" in e and "replacement" not in e:
            e["replacement"] = e.pop("content")
        if "after" in e and "afterMethodName" not in e:
            e["afterMethodName"] = e.pop("after")
        if "after_method" in e and "afterMethodName" not in e:
            e["afterMethodName"] = e.pop("after_method")
        if "before" in e and "beforeMethodName" not in e:
            e["beforeMethodName"] = e.pop("before")
        if "before_method" in e and "beforeMethodName" not in e:
            e["beforeMethodName"] = e.pop("before_method")
        # anchor_method → before/after based on position (default after)
        if "anchor_method" in e:
            anchor = e.pop("anchor_method")
            pos = (e.get("position") or "after").strip().lower()
            if pos == "before" and "beforeMethodName" not in e:
                e["beforeMethodName"] = anchor
            elif "afterMethodName" not in e:
                e["afterMethodName"] = anchor
        if "anchorText" in e and "anchor" not in e:
            e["anchor"] = e.pop("anchorText")
        if "pattern" in e and "anchor" not in e and e.get("op") and e["op"].startswith("anchor_"):
            e["anchor"] = e.pop("pattern")
        if "newText" in e and "text" not in e:
            e["text"] = e.pop("newText")

        # CI compatibility (T‑A/T‑E):
        # Accept method-anchored anchor_insert and upgrade to insert_method
        # Example incoming shape:
        #   {"op":"anchor_insert","afterMethodName":"GetCurrentTarget","text":"..."}
        if (
            e.get("op") == "anchor_insert"
            and not e.get("anchor")
            and (e.get("afterMethodName") or e.get("beforeMethodName"))
        ):
            e["op"] = "insert_method"
            if "replacement" not in e:
                e["replacement"] = e.get("text", "")

        # LSP-like range edit -> replace_range
        if "range" in e and isinstance(e["range"], dict):
            # Keep UTF-16 positions until file contents are available to convert.
            e["op"] = "replace_range"
            if "newText" in edit and "text" not in e:
                e["text"] = edit.get("newText", "")
        return e

    normalized_edits: list[dict[str, Any]] = []
    for raw in edits or []:
        e = _unwrap_and_alias(raw)
        op = (e.get("op") or e.get("operation") or e.get(
            "type") or e.get("mode") or "").strip().lower()

        # Default className to script name if missing on structured method/class ops
        if op in ("replace_class", "delete_class", "replace_method", "delete_method", "insert_method") and not e.get("className"):
            e["className"] = name

        # Map common aliases for text ops
        if op in ("text_replace",):
            e["op"] = "replace_range"
            normalized_edits.append(e)
            continue
        if op in ("regex_delete",):
            e["op"] = "regex_replace"
            e.setdefault("text", "")
            normalized_edits.append(e)
            continue
        if op == "regex_replace" and ("replacement" not in e):
            if "text" in e:
                e["replacement"] = e.get("text", "")
            elif "insert" in e or "content" in e:
                e["replacement"] = e.get(
                    "insert") or e.get("content") or ""
        if op == "anchor_insert" and not (e.get("text") or e.get("insert") or e.get("content") or e.get("replacement")):
            e["op"] = "anchor_delete"
            normalized_edits.append(e)
            continue
        normalized_edits.append(e)

    edits = normalized_edits
    normalized_for_echo = edits

    # Validate required fields and produce machine-parsable hints
    def error_with_hint(message: str, expected: dict[str, Any], suggestion: dict[str, Any]) -> dict[str, Any]:
        return _err("missing_field", message, expected=expected, rewrite=suggestion, normalized=normalized_for_echo)

    for e in edits or []:
        op = e.get("op", "")
        if op == "replace_method":
            if not e.get("methodName"):
                return error_with_hint(
                    "replace_method requires 'methodName'.",
                    {"op": "replace_method", "required": [
                        "className", "methodName", "replacement"]},
                    {"edits[0].methodName": "HasTarget"}
                )
            if not (e.get("replacement") or e.get("text")):
                return error_with_hint(
                    "replace_method requires 'replacement' (inline or base64).",
                    {"op": "replace_method", "required": [
                        "className", "methodName", "replacement"]},
                    {"edits[0].replacement": "public bool X(){ return true; }"}
                )
        elif op == "insert_method":
            if not (e.get("replacement") or e.get("text")):
                return error_with_hint(
                    "insert_method requires a non-empty 'replacement'.",
                    {"op": "insert_method", "required": ["className", "replacement"], "position": {
                        "after_requires": "afterMethodName", "before_requires": "beforeMethodName"}},
                    {"edits[0].replacement": "public void PrintSeries(){ Debug.Log(\"1,2,3\"); }"}
                )
            pos = (e.get("position") or "").lower()
            if pos == "after" and not e.get("afterMethodName"):
                return error_with_hint(
                    "insert_method with position='after' requires 'afterMethodName'.",
                    {"op": "insert_method", "position": {
                        "after_requires": "afterMethodName"}},
                    {"edits[0].afterMethodName": "GetCurrentTarget"}
                )
            if pos == "before" and not e.get("beforeMethodName"):
                return error_with_hint(
                    "insert_method with position='before' requires 'beforeMethodName'.",
                    {"op": "insert_method", "position": {
                        "before_requires": "beforeMethodName"}},
                    {"edits[0].beforeMethodName": "GetCurrentTarget"}
                )
        elif op == "delete_method":
            if not e.get("methodName"):
                return error_with_hint(
                    "delete_method requires 'methodName'.",
                    {"op": "delete_method", "required": [
                        "className", "methodName"]},
                    {"edits[0].methodName": "PrintSeries"}
                )
        elif op in ("anchor_insert", "anchor_replace", "anchor_delete"):
            if not e.get("anchor"):
                return error_with_hint(
                    f"{op} requires 'anchor' (regex).",
                    {"op": op, "required": ["anchor"]},
                    {"edits[0].anchor": "(?m)^\\s*public\\s+bool\\s+HasTarget\\s*\\("}
                )
            if op in ("anchor_insert", "anchor_replace") and not (e.get("text") or e.get("replacement")):
                return error_with_hint(
                    f"{op} requires 'text'.",
                    {"op": op, "required": ["anchor", "text"]},
                    {"edits[0].text": "/* comment */\n"}
                )

    # Decide routing: structured vs text vs mixed
    STRUCT = {"replace_class", "delete_class", "replace_method", "delete_method",
              "insert_method", "anchor_delete", "anchor_replace", "anchor_insert"}
    TEXT = {"prepend", "append", "replace_range", "regex_replace"}
    ops_set = {(e.get("op") or "").lower() for e in edits or []}
    unsupported = ops_set - STRUCT - TEXT
    if unsupported:
        return _err("unsupported_op", f"Unsupported edit op: {', '.join(sorted(unsupported))}", normalized=normalized_for_echo)
    all_struct = ops_set.issubset(STRUCT)
    all_text = ops_set.issubset(TEXT)
    mixed = not (all_struct or all_text)
    preview = bool((options or {}).get("preview"))
    if preview and mixed:
        return _err("unsupported_preview", "Mixed text/structured preview is unsupported; no changes were made.")

    # If everything is structured (method/class/anchor ops), forward directly to Unity's structured editor.
    if all_struct:
        if preview:
            response = await send_with_unity_instance(async_send_command_with_retry, unity_instance, "manage_script", {
                "action": "preview_edit", "name": name, "path": path,
                "namespace": namespace, "scriptType": script_type,
                "edits": edits, "options": dict(options or {}),
            })
            prepared = _prepared_handoff(response, unity_instance, f"{path}/{name}.cs")
            return _with_norm(prepared, normalized_for_echo, routing="structured/preview") if prepared.get("success") else prepared
        # Get pre-edit SHA for disconnect verification
        pre_sha = None
        try:
            sha_resp = await send_with_unity_instance(async_send_command_with_retry, unity_instance,
                "manage_script", {"action": "get_sha", "name": name, "path": path},
            )
            if isinstance(sha_resp, dict) and sha_resp.get("success"):
                pre_sha = (sha_resp.get("data") or {}).get("sha256")
        except Exception:
            pass
        opts2 = dict(options or {})
        # For structured edits, prefer immediate refresh to avoid missed reloads when Editor is unfocused
        opts2.setdefault("refresh", "immediate")
        params_struct: dict[str, Any] = {
            "action": "edit",
            "name": name,
            "path": path,
            "namespace": namespace,
            "scriptType": script_type,
            "edits": edits,
            "options": opts2,
        }

        async def _verify():
            if await verify_edit_by_sha(unity_instance, name, path, pre_sha):
                return {"success": True, "message": "Edit applied (verified after domain reload)."}
            return None

        resp_struct = await send_mutation(ctx, unity_instance, "manage_script", params_struct, verify_after_disconnect=_verify)
        return _with_norm(resp_struct if isinstance(resp_struct, dict) else {"success": False, "message": str(resp_struct)}, normalized_for_echo, routing="structured")

    # 1) read from Unity
    read_resp = await send_with_unity_instance(async_send_command_with_retry, unity_instance, "manage_script", {
        "action": "read",
        "name": name,
        "path": path,
        "namespace": namespace,
        "scriptType": script_type,
    })
    if not isinstance(read_resp, dict) or not read_resp.get("success"):
        return read_resp if isinstance(read_resp, dict) else {"success": False, "message": str(read_resp)}

    data = read_resp.get("data") or read_resp.get(
        "result", {}).get("data") or {}
    contents = data.get("contents")
    if contents is None and data.get("contentsEncoded") and data.get("encodedContents"):
        contents = base64.b64decode(
            data["encodedContents"]).decode("utf-8")
    if contents is None:
        return {"success": False, "message": "No contents returned from Unity read."}

    lsp_edits = [e for e in edits if isinstance(e.get("range"), dict)]
    if lsp_edits:
        source_lines, _ = _script_lines_and_starts(contents)
        try:
            for edit in lsp_edits:
                rng = edit.pop("range")
                edit["startLine"], edit["startCol"] = _lsp_position_to_line_col(source_lines, rng.get("start", {}))
                edit["endLine"], edit["endCol"] = _lsp_position_to_line_col(source_lines, rng.get("end", {}))
        except (ValueError, TypeError, OverflowError) as exc:
            return _err("invalid_range", str(exc))

    # Compute text spans once: preview and both write routes preserve the same payloads.
    routing = "mixed/text-first" if mixed else "text"
    text_edits = [edit for edit in edits if edit.get("op", "") in TEXT]
    struct_edits = [edit for edit in edits if edit.get("op", "") in STRUCT]
    try:
        at_edits = await _text_edit_spans(contents, text_edits, mixed=mixed)
    except _TextEditError as exc:
        return _with_norm(_err(exc.code, str(exc)), normalized_for_echo, routing=routing)
    except Exception as exc:
        return _with_norm(_err("conversion_failed", f"Text edit conversion failed: {exc}"), normalized_for_echo, routing=routing)

    if preview:
        response = await send_with_unity_instance(async_send_command_with_retry, unity_instance, "manage_script", {
            "action": "preview_text_edits", "name": name, "path": path,
            "edits": at_edits, "precondition_sha256": hashlib.sha256(contents.encode("utf-8")).hexdigest(),
            "options": {**dict(options or {}), "preview": True, "applyMode": "atomic" if len(at_edits) > 1 else (options or {}).get("applyMode", "sequential")},
        })
        prepared = _prepared_handoff(response, unity_instance, f"{path}/{name}.cs")
        return _with_norm(prepared, normalized_for_echo, routing="text/preview") if prepared.get("success") else prepared

    if not at_edits and not mixed:
        return _with_norm(_err("no_spans", "No applicable text edit spans computed (anchor not found or zero-length)."), normalized_for_echo, routing=routing)

    sha = hashlib.sha256(contents.encode("utf-8")).hexdigest()
    if at_edits:
        params_text: dict[str, Any] = {
            "action": "apply_text_edits", "name": name, "path": path,
            "namespace": namespace, "scriptType": script_type,
            "edits": at_edits, "precondition_sha256": sha,
            "options": {
                "refresh": (options or {}).get("refresh", "debounced"),
                "validate": (options or {}).get("validate", "standard"),
                "applyMode": "atomic" if len(at_edits) > 1 else (options or {}).get("applyMode", "sequential"),
            },
        }

        async def _verify_text():
            if await verify_edit_by_sha(unity_instance, name, path, sha):
                return {"success": True, "message": "Text edits applied (verified after domain reload)."}
            return None

        response = await send_mutation(ctx, unity_instance, "manage_script", params_text, verify_after_disconnect=_verify_text)
        if not (isinstance(response, dict) and response.get("success")):
            return _with_norm(response if isinstance(response, dict) else {"success": False, "message": str(response)}, normalized_for_echo, routing=routing)
        if not mixed:
            return _with_norm(response, normalized_for_echo, routing=routing)
        # The structural phase must not verify against changes from the text phase.
        text_data = response.get("data")
        sha = text_data.get("sha256") if isinstance(text_data, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            sha = None
            try:
                sha_response = await send_with_unity_instance(async_send_command_with_retry, unity_instance,
                    "manage_script", {"action": "get_sha", "name": name, "path": path})
                sha_data = sha_response.get("data") if isinstance(sha_response, dict) and sha_response.get("success") else None
                candidate_sha = sha_data.get("sha256") if isinstance(sha_data, dict) else None
                if isinstance(candidate_sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", candidate_sha):
                    sha = candidate_sha
            except Exception:
                pass
        if sha is not None:
            sha = sha.lower()

    if struct_edits:
        opts2 = dict(options or {})
        opts2.setdefault("refresh", "debounced")
        params_struct: dict[str, Any] = {
            "action": "edit", "name": name, "path": path,
            "namespace": namespace, "scriptType": script_type,
            "edits": struct_edits, "options": opts2,
        }

        async def _verify_struct():
            if await verify_edit_by_sha(unity_instance, name, path, sha):
                return {"success": True, "message": "Edit applied (verified after domain reload)."}
            return None

        response = await send_mutation(ctx, unity_instance, "manage_script", params_struct, verify_after_disconnect=_verify_struct)
        return _with_norm(response if isinstance(response, dict) else {"success": False, "message": str(response)}, normalized_for_echo, routing=routing)

    return _with_norm({"success": True, "message": "Applied text edits (no structured ops)"}, normalized_for_echo, routing=routing)
