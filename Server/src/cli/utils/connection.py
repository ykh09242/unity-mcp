"""Connection utilities for CLI to communicate with Unity via MCP server."""

import asyncio
import functools
import json
import math
import sys
from typing import Any, Callable, Dict, Final, Optional, TypeVar

import click
import httpx

from cli.utils.config import get_config, CLIConfig
from cli.utils.project_context import exact_hash, is_loopback
from core.local_auth import LOCAL_AUTH_HEADER, read_local_auth_token
from models.unity_response import normalize_unity_response
from models.connection_diagnosis import no_unity_instances_response
from services.animator_actions import guarded_animator_batch_member, guarded_animator_parameters
from services.execution_receipt_identity import ReceiptOptions


class UnityConnectionError(Exception):
    """Raised when connection to Unity fails."""

    pass


class UnityCommandError(Exception):
    """A completed Unity command failed, retaining its diagnostic payload."""

    def __init__(self, response: Dict[str, Any]):
        self.response = response
        super().__init__(response.get("error") or response.get("message") or "Unity command failed")


F = TypeVar("F", bound=Callable[..., Any])

PUBLIC_CONNECTION_ERROR: Final = "Unity MCP connection failed. Verify the server is running, reachable, and its local authentication token is available."


def print_connection_error(error: UnityConnectionError, format_type: str) -> None:
    """Render setup/transport failures without echoing credentials in JSON mode."""
    from cli.utils.output import format_output, print_error

    if format_type == "json":
        # Exceptions and HTTP bodies can echo credentials. Keep the public
        # diagnostic fixed instead of serializing untrusted transport text.
        click.echo(
            format_output(
                {
                    "success": False,
                    "code": "connection_error",
                    "error": PUBLIC_CONNECTION_ERROR,
                },
                "json",
            )
        )
    else:
        print_error(str(error))


def handle_unity_errors(func: F) -> F:
    """Handle local input, failed Unity commands and transport errors consistently.

    Preserve completed command responses in the requested output format.
    JSON transport failures omit untrusted exception/response diagnostics;
    text transport failures retain their human stderr output. Both exit with code 1.
    Typed command input failures emit one JSON error with a stable code, or retain
    their human stderr diagnostic; JSON diagnostic text is capped at 1000 characters.

    Usage:
        @scene.command("active")
        @handle_unity_errors
        def active():
            config = get_config()
            result = run_command("manage_scene", {"action": "get_active"}, config)
            click.echo(format_output(result, config.format))
    """
    import click
    from cli.utils.input_errors import CLIInputError
    from cli.utils.output import format_output, print_error, print_info

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except CLIInputError as e:
            if get_config().format == "json":
                click.echo(
                    format_output(
                        {"success": False, "code": e.code, "error": str(e)[:1000]}, "json"
                    )
                )
            else:
                print_error(str(e))
                for hint in e.hints:
                    print_info(hint, err=True)
            sys.exit(1)
        except UnityCommandError as e:
            click.echo(format_output(e.response, get_config().format))
            sys.exit(1)
        except UnityConnectionError as e:
            print_connection_error(e, get_config().format)
            sys.exit(1)

    return wrapper  # type: ignore[return-value]


def warn_if_remote_host(config: CLIConfig) -> None:
    """Warn user if connecting to a non-localhost server.

    This is a security measure to alert users that connecting to remote
    servers exposes Unity control to potential network attacks.

    Args:
        config: CLI configuration with host setting
    """
    import click

    local_hosts = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
    if config.host.lower() not in local_hosts:
        click.echo(
            "⚠️  Security Warning: Connecting to non-localhost server.\n"
            "   HTTP does not encrypt the launch token or Unity commands.\n"
            "   Use a trusted tunnel when connecting across machines.\n"
            "   Only proceed if you trust this network.\n",
            err=True,
        )


def _server_url(config: CLIConfig, path: str) -> str:
    """Build an HTTP endpoint authority, including raw IPv6 host literals."""
    host = config.host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{config.port}{path}"


def _http_client(config: CLIConfig) -> httpx.AsyncClient:
    # A loopback launch token must never reach an intermediary selected by HTTP_PROXY.
    if is_loopback(config.host.strip("[]").lower().removesuffix(".")):
        return httpx.AsyncClient(trust_env=False, follow_redirects=False)
    return httpx.AsyncClient()


def _raise_structured_target_refusal(response: httpx.Response) -> None:
    """Preserve completed target-selection refusals for commands and inventory."""
    if response.status_code not in (404, 409):
        return
    try:
        failure = response.json()
    except ValueError:
        return
    if isinstance(failure, dict) and failure.get("success") is False:
        raise UnityCommandError(failure)


def _raise_known_no_editor(response: httpx.Response) -> None:
    """Admit only the bounded, fixed no-session 503 contract; never echo HTTP diagnostics."""
    if response.status_code != 503 or len(response.content) > 4096:
        return
    try:
        failure = response.json()
    except ValueError:
        return
    expected = no_unity_instances_response()
    if (
        isinstance(failure, dict)
        and failure.get("success") is False
        and all(failure.get(key) == value for key, value in expected.items())
    ):
        raise UnityCommandError(expected)


def _validate_finite_numbers(value: object, path: str = "params") -> None:
    """Reject non-JSON numeric values locally without restricting signed finite values."""
    from cli.utils.input_errors import CLIInputError

    if isinstance(value, float) and not math.isfinite(value):
        raise CLIInputError("invalid_numeric_input", f"{path} must be a finite number.")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_numbers(item, f"{path}.{str(key)[:100]}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_finite_numbers(item, f"{path}[{index}]")


async def send_command(
    command_type: str,
    params: Dict[str, Any],
    config: Optional[CLIConfig] = None,
    timeout: Optional[int] = None,
    *,
    execution: ReceiptOptions | None = None,
) -> Dict[str, Any]:
    """Send a command to Unity via the MCP HTTP server.

    Args:
        command_type: The command type (e.g., 'manage_gameobject', 'manage_scene')
        params: Command parameters
        config: Optional CLI configuration
        timeout: Optional timeout override

    Returns:
        Response dict from Unity

    Raises:
        UnityConnectionError: If connection fails
    """
    _validate_finite_numbers(params)
    cfg = config or get_config()
    guarded = cfg.expected_project_root is not None
    url = _server_url(cfg, "/api/context-command" if guarded else "/api/command")

    # All local CLI surfaces, including raw and project-guarded dispatch, send
    # native commands directly. Explicit runtime setters need a fail-closed wire action.
    native_command = command_type.lower()
    try:
        if native_command == "manage_animation":
            params = guarded_animator_parameters(params)
        elif native_command in {
            "batch_execute",
            "batch_validate",
            "batch_apply_preview",
        } and isinstance(params.get("commands"), list):
            params = {
                **params,
                "commands": [
                    guarded_animator_batch_member(member) if isinstance(member, dict) else member
                    for member in params["commands"]
                ],
            }
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    payload = {
        "type": command_type,
        "params": params,
    }

    if cfg.unity_instance:
        payload["unity_instance"] = cfg.unity_instance

    if execution is not None:
        from cli.utils.input_errors import CLIInputError

        execution.validate()
        if exact_hash(cfg.unity_instance) != execution.project_hash:
            raise CLIInputError("invalid_execution_options", "Execution receipt target mismatch.")
        if native_command == "get_execution_receipt" or (
            native_command == "run_tests"
            and not params.get("clear_stuck")
            and "source_token" not in params
        ):
            raise CLIInputError(
                "invalid_execution_options",
                "Receipt control cannot create receipts; protected test starts require an explicit source_token.",
            )
        payload["execution"] = {
            "version": 1,
            "store_id": execution.store_id,
            "project_hash": execution.project_hash,
            "retain_result": execution.retain_result,
        }
        if execution.idempotency_key is not None:
            payload["execution"]["idempotency_key"] = execution.idempotency_key

    if guarded:
        # Receipt controls authorize stored records without dispatching to a live Editor.
        if not is_loopback(cfg.host) or (
            command_type != "get_execution_receipt" and exact_hash(cfg.unity_instance) is None
        ):
            raise click.UsageError(
                "Project context requires a local server and a complete explicit instance hash."
            )
        payload["expected_project"] = {"root": cfg.expected_project_root}

    source_admitted = False
    if (
        native_command == "run_tests"
        and not params.get("clear_stuck")
        and "source_token" not in params
    ):
        from time import monotonic
        from cli.utils.source_freshness import prepare_test_start

        params, cfg, remaining = await prepare_test_start(
            params,
            cfg,
            deadline=monotonic() + min(timeout or cfg.timeout, 300),
            send=send_command,
            inventory=list_unity_instances,
        )
        if remaining <= 0:
            raise UnityConnectionError("Source admission deadline expired; no tests were started.")
        timeout = remaining
        payload["params"] = params
        payload["unity_instance"] = cfg.unity_instance
        source_admitted = True

    if cfg.verbose:
        visible = {**payload, "expected_project": "<redacted>"} if guarded else payload
        click.echo(f"> POST {url} {json.dumps(visible)}", err=True)

    try:
        async with _http_client(cfg) as client:
            response = await client.post(
                url,
                json=payload,
                headers=_auth_headers(cfg),
                timeout=timeout or cfg.timeout,
            )
            if cfg.verbose:
                click.echo(
                    f"< {response.status_code}"
                    if guarded or (cfg.format == "json" and not 200 <= response.status_code < 300)
                    else f"< {response.status_code} {response.text}",
                    err=True,
                )
            response.raise_for_status()
            raw_result = response.json()
            result = normalize_unity_response(raw_result)
            if execution is not None and isinstance(raw_result, dict) and isinstance(result, dict):
                if "execution_receipt" in raw_result:
                    result = {**result, "execution_receipt": raw_result["execution_receipt"]}
            if source_admitted and isinstance(result, dict):
                result["unity_instance"] = cfg.unity_instance
            return result
    except httpx.ConnectError as e:
        raise UnityConnectionError(
            f"Cannot connect to Unity MCP server at {cfg.host}:{cfg.port}. "
            f"Make sure the server is running and Unity is connected.\n"
            f"Error: {e}"
        )
    except httpx.TimeoutException:
        raise UnityConnectionError(
            f"Connection to Unity timed out after {timeout or cfg.timeout}s. "
            f"Unity may be busy or unresponsive."
        )
    except httpx.HTTPStatusError as e:
        _raise_known_no_editor(e.response)
        if guarded:
            raise UnityConnectionError(
                "Project context request refused; verify the selected local server supports guarded commands."
            ) from None
        _raise_structured_target_refusal(e.response)
        raise UnityConnectionError(
            f"HTTP error from server: {e.response.status_code} - {e.response.text}"
        )
    except Exception as e:
        if guarded:
            raise UnityConnectionError(
                "Project context request failed; inspect the selected server before retrying."
            ) from None
        raise UnityConnectionError(f"Unexpected error: {e}")


def run_command(
    command_type: str,
    params: Dict[str, Any],
    config: Optional[CLIConfig] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Synchronous wrapper for send_command.

    Args:
        command_type: The command type
        params: Command parameters
        config: Optional CLI configuration
        timeout: Optional timeout override

    Returns:
        Response dict from Unity

    Raises:
        UnityCommandError: If Unity explicitly reports a failed operation.
        UnityConnectionError: If the request cannot complete.
    """
    response = asyncio.run(send_command(command_type, params, config, timeout))
    if isinstance(response, dict) and response.get("success") is False:
        raise UnityCommandError(response)
    return response


async def check_connection(config: Optional[CLIConfig] = None) -> bool:
    """Check if we can connect to the Unity MCP server.

    Args:
        config: Optional CLI configuration

    Returns:
        True if connection successful, False otherwise
    """
    cfg = config or get_config()
    url = _server_url(cfg, "/health")

    try:
        async with _http_client(cfg) as client:
            response = await client.get(url, timeout=min(cfg.timeout, 5))
            return response.status_code == 200
    except Exception:
        return False


def run_check_connection(config: Optional[CLIConfig] = None) -> bool:
    """Synchronous wrapper for check_connection."""
    return asyncio.run(check_connection(config))


async def list_unity_instances(config: Optional[CLIConfig] = None) -> Dict[str, Any]:
    """List available Unity instances.

    Args:
        config: Optional CLI configuration

    Returns:
        Dict with list of Unity instances
    """
    cfg = config or get_config()

    url = _server_url(cfg, "/api/instances")

    try:
        async with _http_client(cfg) as client:
            response = await client.get(
                url, headers=_auth_headers(cfg), timeout=min(cfg.timeout, 10)
            )
            response.raise_for_status()
            data = response.json()
            if "instances" in data:
                return data
    except httpx.ConnectError as e:
        raise UnityConnectionError(
            f"Cannot connect to Unity MCP server at {cfg.host}:{cfg.port}. "
            f"Make sure the server is running and Unity is connected.\n"
            f"Error: {e}"
        )
    except httpx.TimeoutException:
        raise UnityConnectionError(
            "Connection to Unity timed out while listing instances. "
            "Unity may be busy or unresponsive."
        )
    except httpx.HTTPStatusError as e:
        raise UnityConnectionError(
            f"HTTP error from server: {e.response.status_code} - {e.response.text}"
        )
    except Exception as e:
        raise UnityConnectionError(f"Unexpected error: {e}")

    raise UnityConnectionError("Failed to list Unity instances")


def run_list_instances(config: Optional[CLIConfig] = None) -> Dict[str, Any]:
    """Synchronous wrapper for list_unity_instances."""
    return asyncio.run(list_unity_instances(config))


async def list_custom_tools(config: Optional[CLIConfig] = None) -> Dict[str, Any]:
    """List custom tools registered for the active Unity project."""
    cfg = config or get_config()
    url = _server_url(cfg, "/api/custom-tools")
    params: Dict[str, Any] = {}
    if cfg.unity_instance:
        params["instance"] = cfg.unity_instance

    try:
        async with _http_client(cfg) as client:
            response = await client.get(
                url, params=params, headers=_auth_headers(cfg), timeout=cfg.timeout
            )
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError as e:
        raise UnityConnectionError(
            f"Cannot connect to Unity MCP server at {cfg.host}:{cfg.port}. "
            f"Make sure the server is running and Unity is connected.\n"
            f"Error: {e}"
        )
    except httpx.TimeoutException:
        raise UnityConnectionError(
            f"Connection to Unity timed out after {cfg.timeout}s. "
            f"Unity may be busy or unresponsive."
        )
    except httpx.HTTPStatusError as e:
        _raise_structured_target_refusal(e.response)
        _raise_known_no_editor(e.response)
        raise UnityConnectionError(
            f"HTTP error from server: {e.response.status_code} - {e.response.text}"
        )
    except Exception as e:
        raise UnityConnectionError(f"Unexpected error: {e}")


def run_list_custom_tools(config: Optional[CLIConfig] = None) -> Dict[str, Any]:
    """Synchronous wrapper for list_custom_tools."""
    return asyncio.run(list_custom_tools(config))


def _auth_headers(config: CLIConfig) -> dict[str, str]:
    """Resolve the current launch credential immediately before each request."""
    token = read_local_auth_token(config.host, config.port)
    if not token:
        raise UnityConnectionError(
            "Local authentication token not found. Start the local server first, or set "
            "UNITY_MCP_LOCAL_AUTH_TOKEN_FILE to its token file."
        )
    return {LOCAL_AUTH_HEADER: token}
