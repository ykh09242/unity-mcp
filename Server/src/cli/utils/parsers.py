"""JSON and value parsing utilities for CLI commands."""

import json
import math
import re
import sys
from typing import Any, BinaryIO, Final, NoReturn

import click

from cli.utils.output import print_error, print_info


_JSON_COMPAT_TOKENS = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:True|False)\b""")
MAX_JSON_INPUT_BYTES: Final = 4 * 1024 * 1024
_STDIN_CLAIM: Final = "unity_mcp.json_stdin_claim"


class _ExternalJsonText(str):
    """Validated external JSON text, retained only to redact downstream value diagnostics."""


def _json_input_error(message: str) -> NoReturn:
    print_error(message)
    sys.exit(1)


def _source_marker(value: str | None) -> bool:
    return value is not None and (value == "-" or value.startswith("@"))


def _read_bounded_json(stream: BinaryIO) -> bytes:
    """Handle short reads without allocating beyond the byte limit plus one sentinel."""
    content = bytearray()
    while len(content) <= MAX_JSON_INPUT_BYTES:
        remaining = MAX_JSON_INPUT_BYTES + 1 - len(content)
        chunk = stream.read(min(65536, remaining))
        if not chunk:
            break
        if len(chunk) > remaining:
            _json_input_error("JSON input exceeds the 4 MiB byte limit.")
        content.extend(chunk)
    if len(content) > MAX_JSON_INPUT_BYTES:
        _json_input_error("JSON input exceeds the 4 MiB byte limit.")
    return bytes(content)


def _reject_json_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def admit_json_inputs(**values: str | None) -> dict[str, str | None]:
    """Resolve declared CLI argument sources once, before the existing command action path.

    Call once at the callback boundary with every eligible JSON input. Nested
    parsers deliberately never call this function or expand source markers.
    """
    stdin_names = [name for name, value in values.items() if value in ("-", "@-")]
    ctx = click.get_current_context(silent=True)
    root = ctx.find_root() if ctx is not None else None
    if len(stdin_names) > 1 or stdin_names and root is not None and root.meta.get(_STDIN_CLAIM):
        _json_input_error("Only one JSON input may consume stdin per invocation.")
    # Validate every source before opening even the first file or stdin stream.
    if any(value == "@" for value in values.values()):
        _json_input_error("A JSON @file source requires a file path.")
    result = dict(values)
    for name, value in values.items():
        if not _source_marker(value):
            continue
        stdin = value in ("-", "@-")
        if stdin and root is not None:
            root.meta[_STDIN_CLAIM] = name
        try:
            with click.open_file("-" if stdin else value[1:], "rb") as stream:
                encoded = _read_bounded_json(stream)
        except OSError:
            _json_input_error(f"Cannot read JSON input for {name}.")
        try:
            text = encoded.decode("utf-8-sig")
        except UnicodeError:
            _json_input_error(f"JSON input for {name} must be UTF-8 (one leading BOM is allowed).")
        try:
            json.loads(text, parse_constant=_reject_json_constant, parse_float=_finite_json_float)
        except json.JSONDecodeError as error:
            _json_input_error(
                f"Invalid JSON input for {name} at line {error.lineno}, column {error.colno}."
            )
        except (ValueError, RecursionError):
            _json_input_error(f"Invalid JSON input for {name}.")
        result[name] = _ExternalJsonText(text)
    return result


def admit_raw_json_args(params: tuple[str, ...]) -> str:
    """Preserve inline Windows argument joining while excluding mixed external sources."""
    if len(params) > 1 and any(_source_marker(value) for value in params):
        _json_input_error("Use one JSON @file or stdin source, without additional params.")
    return admit_json_inputs(params=" ".join(params) if params else "{}")["params"]


def json_input_diagnostic(value: str | None, context: str, error: ValueError) -> str:
    """Keep existing inline errors but never echo values read from an external source."""
    return (
        f"Invalid JSON value for {context}." if isinstance(value, _ExternalJsonText) else str(error)
    )


def _repair_json_token(match: re.Match[str]) -> str:
    token = match.group()
    if token in ("True", "False"):
        return token.lower()
    if token.startswith('"'):
        return token

    # Convert single-quoted strings without changing their JSON escape sequences.
    contents = re.sub(
        r'\\.|"',
        lambda part: {r"\'": "'", '"': r"\""}.get(part.group(), part.group()),
        token[1:-1],
    )
    return f'"{contents}"'


def parse_value_safe(value: str) -> Any:
    """Parse a value, trying JSON → float → string fallback.

    This is used for property values that could be JSON objects/arrays,
    numbers, or strings. Never raises an exception.

    Args:
        value: The string value to parse

    Returns:
        Parsed JSON object/array, float, or original string

    Examples:
        >>> parse_value_safe('{"x": 1}')
        {'x': 1}
        >>> parse_value_safe('3.14')
        3.14
        >>> parse_value_safe('hello')
        'hello'
    """
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # Try to parse as number
        try:
            return float(value)
        except ValueError:
            # Keep as string
            return value


def parse_json_or_exit(value: str, context: str = "parameter") -> Any:
    """Parse JSON string, trying to fix common issues, or exit with error.

    Attempts to parse JSON with automatic fixes for:
    - Single quotes instead of double quotes
    - Python-style True/False instead of true/false

    Args:
        value: The JSON string to parse
        context: Description of what's being parsed (for error messages)

    Returns:
        Parsed JSON object

    Exits:
        Calls sys.exit(1) if JSON is invalid after attempting fixes
    """
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # Try to fix common shell quoting issues (single quotes, Python bools)
        try:
            fixed = _JSON_COMPAT_TOKENS.sub(_repair_json_token, value)
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            print_error(f"Invalid JSON for {context}: {e}")
            print_info('Example: --params \'{"key":"value"}\'', err=True)
            print_info("Tip: wrap JSON in single quotes to avoid shell escaping issues.", err=True)
            sys.exit(1)


def parse_json_dict_or_exit(value: str, context: str = "parameter") -> dict[str, Any]:
    """Parse JSON object (dict), or exit with error.

    Like parse_json_or_exit, but ensures result is a dictionary.

    Args:
        value: The JSON string to parse
        context: Description of what's being parsed (for error messages)

    Returns:
        Parsed JSON object as dictionary

    Exits:
        Calls sys.exit(1) if JSON is invalid or not an object
    """
    result = parse_json_or_exit(value, context)
    if not isinstance(result, dict):
        print_error(f"Invalid JSON for {context}: expected an object, got {type(result).__name__}")
        sys.exit(1)
    return result


def parse_json_list_or_exit(value: str, context: str = "parameter") -> list[Any]:
    """Parse JSON array (list), or exit with error.

    Like parse_json_or_exit, but ensures result is a list.

    Args:
        value: The JSON string to parse
        context: Description of what's being parsed (for error messages)

    Returns:
        Parsed JSON array as list

    Exits:
        Calls sys.exit(1) if JSON is invalid or not an array
    """
    result = parse_json_or_exit(value, context)
    if not isinstance(result, list):
        print_error(f"Invalid JSON for {context}: expected an array, got {type(result).__name__}")
        sys.exit(1)
    return result
