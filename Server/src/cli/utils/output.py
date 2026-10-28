"""Output formatting utilities for CLI."""

import json
from itertools import islice
from typing import Any

import click

_TABLE_ROW_LIMIT = 50


def format_output(data: Any, format_type: str = "text") -> str:
    """Format output based on requested format type.

    Args:
        data: Data to format
        format_type: One of 'text', 'json', 'table'

    Returns:
        Formatted string
    """
    if format_type == "json":
        return format_as_json(data)
    elif format_type == "table":
        return format_as_table(data)
    else:
        return format_as_text(data)


def format_as_json(data: Any) -> str:
    """Format data as pretty-printed JSON."""
    try:
        return json.dumps(data, indent=2, default=str)
    except (TypeError, ValueError) as e:
        return json.dumps({"error": f"JSON serialization failed: {e}", "raw": str(data)})


def format_as_text(data: Any, indent: int = 0) -> str:
    """Format data as human-readable text."""
    prefix = "  " * indent

    if data is None:
        return f"{prefix}(none)"

    if isinstance(data, dict):
        # Check for error response
        if "success" in data and not data.get("success"):
            error = data.get("error") or data.get("message") or "Unknown error"
            lines = [f"{prefix}❌ Error: {_bounded_detail(error)}"]
            details = {key: value for key, value in data.items() if key not in ("success", "error")}
            rendered = list(islice(_failure_detail_lines(details, indent), 81))
            lines.extend(rendered[:80])
            if len(rendered) > 80:
                lines.append(f"{prefix}... (more details omitted; use --format json)")
            text = "\n".join(lines)
            if len(text) > 12000:
                text = text[:12000] + "\n... (more details omitted; use --format json)"
            return text

        # Check for success response with data
        if "success" in data and data.get("success"):
            result = data.get("data") or data.get("result") or data
            if result != data:
                return format_as_text(result, indent)

        lines = []
        for key, value in data.items():
            if key in ("success", "error", "message") and "success" in data:
                continue  # Skip meta fields
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.append(format_as_text(value, indent + 1))
            elif isinstance(value, list):
                lines.append(f"{prefix}{key}: [{len(value)} items]")
                if len(value) <= 10:
                    for i, item in enumerate(value):
                        lines.append(f"{prefix}  [{i}] {_format_list_item(item)}")
                else:
                    for i, item in enumerate(value[:5]):
                        lines.append(f"{prefix}  [{i}] {_format_list_item(item)}")
                    lines.append(f"{prefix}  ... ({len(value) - 10} more)")
                    for i, item in enumerate(value[-5:], len(value) - 5):
                        lines.append(f"{prefix}  [{i}] {_format_list_item(item)}")
            else:
                lines.append(f"{prefix}{key}: {value}")
        return "\n".join(lines)

    if isinstance(data, list):
        if not data:
            return f"{prefix}(empty list)"
        lines = [f"{prefix}[{len(data)} items]"]
        for i, item in enumerate(data[:20]):
            lines.append(f"{prefix}  [{i}] {_format_list_item(item)}")
        if len(data) > 20:
            lines.append(f"{prefix}  ... ({len(data) - 20} more)")
        return "\n".join(lines)

    return f"{prefix}{data}"


def _bounded_detail(value: Any) -> str:
    """Keep human-readable diagnostic values finite; JSON stays lossless."""
    text = str(value)
    return text if len(text) <= 300 else text[:300] + "... (truncated; use --format json)"


def _failure_detail_lines(value: Any, indent: int, depth: int = 0):
    """Render structured outcomes with indices instead of list-item summaries."""
    prefix = "  " * indent
    if depth >= 8:
        yield f"{prefix}... (nested details omitted; use --format json)"
    elif isinstance(value, dict):
        # Keep execution/rollback summaries visible before large collections.
        items = sorted(
            value.items(),
            key=lambda pair: isinstance(pair[1], dict) + 2 * isinstance(pair[1], list),
        )
        for key, item in islice(items, 30):
            label = _bounded_detail(key)
            if isinstance(item, (dict, list)):
                yield f"{prefix}{label}:"
                yield from _failure_detail_lines(item, indent + 1, depth + 1)
            else:
                yield f"{prefix}{label}: {_bounded_detail(item)}"
        if len(value) > 30:
            yield f"{prefix}... ({len(value) - 30} fields omitted; use --format json)"
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            if isinstance(item, (dict, list)):
                yield f"{prefix}[{index}]:"
                yield from _failure_detail_lines(item, indent + 1, depth + 1)
            else:
                yield f"{prefix}[{index}]: {_bounded_detail(item)}"
        if len(value) > 20:
            yield f"{prefix}... ({len(value) - 20} items omitted; use --format json)"
    else:
        yield f"{prefix}{_bounded_detail(value)}"


def _format_list_item(item: Any) -> str:
    """Format a single list item."""
    if isinstance(item, dict):
        # Try to find a name/id field for display
        name = item.get("name") or item.get("Name") or item.get("id") or item.get("Id")
        if name:
            extra = ""
            if "instanceID" in item:
                extra = f" (ID: {item['instanceID']})"
            elif "path" in item:
                extra = f" ({item['path']})"
            return f"{name}{extra}"
        # Fallback to compact representation
        return json.dumps(item, default=str)[:80]
    return str(item)[:80]


def format_as_table(data: Any) -> str:
    """Format data as an ASCII table."""
    if isinstance(data, dict):
        # Check for success response with data
        if "success" in data and data.get("success"):
            result = data.get("data") or data.get("result") or data.get("items")
            if isinstance(result, list):
                return _build_table(result)

        # Single dict as key-value table
        rows = [[str(k), str(v)[:60]] for k, v in islice(data.items(), _TABLE_ROW_LIMIT)]
        rendered = _build_table(rows, headers=["Key", "Value"], total_rows=len(data))
        if data.get("success") is False:
            rendered += "\n\n" + format_as_text(data)
        return rendered

    if isinstance(data, list):
        return _build_table(data)

    return str(data)


def _build_table(
    data: list[Any], headers: list[str] | None = None, *, total_rows: int | None = None
) -> str:
    """Build an ASCII table from list data."""
    if not data:
        return "(no data)"

    total_rows = len(data) if total_rows is None else total_rows
    data = data[:_TABLE_ROW_LIMIT]

    # Convert list of dicts to rows
    if all(isinstance(item, dict) for item in data):
        if headers is None:
            headers = list(dict.fromkeys(key for item in data for key in item))
        rows = [[str(item.get(h, ""))[:40] for h in headers] for item in data]
    elif all(isinstance(item, (list, tuple)) for item in data):
        rows = [[str(cell)[:40] for cell in row] for row in data]
        if headers is None:
            headers = [f"Col{i}" for i in range(max(len(row) for row in data))]
    else:
        rows = [[str(item)[:60]] for item in data]
        headers = headers or ["Value"]

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))

    # Build table
    lines = []

    # Header
    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("-+-".join("-" * w for w in col_widths))

    # Rows
    for row in rows:
        row_line = " | ".join(
            (row[i] if i < len(row) else "").ljust(col_widths[i]) for i in range(len(headers))
        )
        lines.append(row_line)

    if total_rows > len(rows):
        lines.append(f"... ({total_rows - len(rows)} more rows)")

    return "\n".join(lines)


def print_success(message: str) -> None:
    """Print a success message."""
    click.echo(f"✅ {message}")


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    click.echo(f"❌ {message}", err=True)


def print_warning(message: str) -> None:
    """Print a warning message."""
    click.echo(f"⚠️  {message}")


def print_info(message: str, *, err: bool = False) -> None:
    """Print an info message."""
    click.echo(f"ℹ️  {message}", err=err)
