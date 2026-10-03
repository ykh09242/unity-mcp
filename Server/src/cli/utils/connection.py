"""Connection utilities for CLI to communicate with Unity via MCP server."""

import asyncio
import functools
import sys
from typing import Any, Callable, Dict, Optional, TypeVar

import httpx

from cli.utils.config import get_config, CLIConfig
from core.local_auth import LOCAL_AUTH_HEADER, read_local_auth_token
from models.unity_response import normalize_unity_response


class UnityConnectionError(Exception):
    """Raised when connection to Unity fails."""
    pass


class UnityCommandError(Exception):
    """A completed Unity command failed, retaining its diagnostic payload."""

    def __init__(self, response: Dict[str, Any]):
        self.response = response
        super().__init__(response.get("error") or response.get("message") or "Unity command failed")


F = TypeVar("F", bound=Callable[..., Any])


def handle_unity_errors(func: F) -> F:
    """Handle failed Unity commands and transport errors consistently.

    Preserve completed command responses in the requested output format;
    print transport errors separately. Both exit with code 1.

    Usage:
        @scene.command("active")
        @handle_unity_errors
        def active():
            config = get_config()
            result = run_command("manage_scene", {"action": "get_active"}, config)
            click.echo(format_output(result, config.format))
    """
    import click
    from cli.utils.output import format_output, print_error

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except UnityCommandError as e:
            click.echo(format_output(e.response, get_config().format))
            sys.exit(1)
        except UnityConnectionError as e:
            print_error(str(e))
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
            err=True
        )


def _server_url(config: CLIConfig, path: str) -> str:
    """Build an HTTP endpoint authority, including raw IPv6 host literals."""
    host = config.host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{config.port}{path}"


async def send_command(
    command_type: str,
    params: Dict[str, Any],
    config: Optional[CLIConfig] = None,
    timeout: Optional[int] = None,
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
    cfg = config or get_config()
    url = _server_url(cfg, "/api/command")

    payload = {
        "type": command_type,
        "params": params,
    }

    if cfg.unity_instance:
        payload["unity_instance"] = cfg.unity_instance

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers=_auth_headers(cfg),
                timeout=timeout or cfg.timeout,
            )
            response.raise_for_status()
            return normalize_unity_response(response.json())
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
        raise UnityConnectionError(
            f"HTTP error from server: {e.response.status_code} - {e.response.text}"
        )
    except Exception as e:
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
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=5)
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
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=_auth_headers(cfg), timeout=10)
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
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url, params=params, headers=_auth_headers(cfg), timeout=cfg.timeout)
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
