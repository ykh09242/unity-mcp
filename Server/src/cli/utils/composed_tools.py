"""Bounded public MCP calls for server-owned script quality and verification handles."""

import json
import re

import anyio
import click
from pydantic import ValidationError

from cli.utils.config import get_config
from cli.utils.connection import UnityCommandError, UnityConnectionError, _auth_headers
from cli.utils.doctor_connection import DiagnosticClient, DoctorHttpFactory, _endpoint
from cli.utils.input_errors import CLIInputError
from cli.utils.output import format_output
from cli.utils.parsers import admit_json_inputs
from cli.utils.tool_description import _checked_json
from models.doctor import EXACT_TARGET_PATTERN
from models.script_quality import parse_quality_options
from models.verification import parse_verification_options


def _options(name, action, options):
    parser = {
        "manage_script_quality": parse_quality_options,
        "manage_verification": parse_verification_options,
    }.get(name)
    if parser is None:
        raise CLIInputError("invalid_composition_tool", "Unsupported composed tool.")
    try:
        return parser(action, options).model_dump(exclude_none=True)
    except (ValidationError, ValueError):
        raise CLIInputError(
            "invalid_composition_options", "Options do not match this action's schema."
        ) from None


def echo_composed_tool(name, action, options):
    options = admit_json_inputs(options=options)["options"]
    try:
        parsed = json.loads(options)
    except (ValueError, RecursionError):
        raise CLIInputError(
            "invalid_composition_options", "Options must be one JSON object."
        ) from None
    validated = _options(name, action, parsed)
    config = get_config()
    result = run_composed_tool(name, action, validated, config)
    click.echo(format_output(result, config.format))


async def send_composed_tool(name, action, options, config):
    options = _options(name, action, options)
    if config.expected_project_root is not None:
        raise CLIInputError(
            "composition_project_guard_unsupported",
            "Project context guards are not supported by this MCP surface. Use an explicitly selected instance with --no-project-context.",
        )
    if (
        not re.fullmatch(EXACT_TARGET_PATTERN, config.unity_instance or "")
        or _endpoint(config) is None
    ):
        raise CLIInputError(
            "invalid_composition_target",
            "Use --instance with the exact Name@hash target and a valid server address.",
        )
    deadline = anyio.current_time() + min(config.timeout, 300)
    factory = DoctorHttpFactory(deadline=deadline)
    try:
        with anyio.fail_after(max(0.001, deadline - anyio.current_time())):
            headers = await anyio.to_thread.run_sync(_auth_headers, config, abandon_on_cancel=True)
        with anyio.fail_after(max(0.001, deadline + 1 - anyio.current_time())):
            # Legacy MCP wire avoids automatic schema negotiation re-dispatch.
            async with DiagnosticClient(config, "legacy", factory, headers) as client:
                with anyio.fail_after(max(0.001, deadline - anyio.current_time())):
                    result = await client.call_tool_mcp(
                        name,
                        {"action": action, "options": options},
                        meta={"unity_instance": config.unity_instance},
                    )
        wire = result.structured_content
        if (
            wire is None
            and len(result.content) == 1
            and getattr(result.content[0], "type", None) == "text"
        ):
            wire = json.loads(result.content[0].text)
        _checked_json(wire)
        if not isinstance(wire, dict):
            raise ValueError("invalid_composition_result")
        if result.is_error or wire.get("success") is False:
            raise UnityCommandError(
                wire
                if wire.get("success") is False
                else {"success": False, "error": "MCP tool rejected the request."}
            )
        return wire
    except UnityCommandError:
        raise
    except Exception as error:  # noqa: BROAD_EXCEPT_OK -- redact untrusted transport failures at the CLI boundary.
        raise UnityConnectionError(
            "MCP request outcome is unknown. Inspect the selected server and existing job IDs before any retry."
        ) from None


def run_composed_tool(name, action, options, config):
    return anyio.run(send_composed_tool, name, action, options, config)
