"""Explicit discovery readiness, separate from the compatible health-only status."""

import asyncio
from typing import Literal, TypedDict

import httpx

from cli.utils import connection
from cli.utils.config import CLIConfig

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class ReadinessResult(TypedDict):
    success: bool
    server_reachable: bool
    authentication: Literal["accepted", "rejected", "unknown"]
    instance_discovery: bool
    target_ready: bool
    host: str
    port: int
    instances: list[dict[str, JsonValue]]
    error: str


def _target_available(instances: list[dict[str, JsonValue]], selector: str | None) -> bool:
    """Mirror local command selection after the CLI omits an empty selector."""
    if not selector:
        return len(instances) == 1
    # Inventory hash is the registered project_hash, not the transport session_id.
    # Keep this precedence aligned with main._select_local_session without importing
    # the server application (and its process-wide initialization) into the CLI.
    instance_hash = selector.rpartition("@")[2] if "@" in selector else selector
    matches = [instance for instance in instances if instance.get("hash") == instance_hash]
    if matches:
        return len(matches) == 1
    if "@" in selector:
        return False
    return sum(instance.get("project") == selector for instance in instances) == 1


async def _readiness(config: CLIConfig) -> ReadinessResult:
    result: ReadinessResult = {
        "success": False,
        "server_reachable": await connection.check_connection(config),
        "authentication": "unknown",
        "instance_discovery": False,
        "target_ready": False,
        "host": config.host,
        "port": config.port,
        "instances": [],
        "error": "Server health check failed.",
    }
    if not result["server_reachable"]:
        return result
    result["error"] = "Instance discovery failed; verify the server and authentication."
    try:
        async with connection._http_client(config) as client:
            response = await client.get(
                connection._server_url(config, "/api/instances"),
                headers=connection._auth_headers(config),
                timeout=min(config.timeout, 10),
            )
        if response.status_code in (401, 403):
            result["authentication"] = "rejected"
            result["error"] = "Authentication rejected; verify the selected server credentials."
            return result
        response.raise_for_status()
        result["authentication"] = "accepted"
        payload = response.json()
        instances = payload.get("instances") if isinstance(payload, dict) else None
        if not isinstance(instances, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("hash"), str) or not row["hash"]
            for row in instances
        ):
            result["error"] = "Instance discovery returned an invalid inventory."
            return result
        result["instances"] = instances
        result["instance_discovery"] = True
        result["target_ready"] = _target_available(instances, config.unity_instance)
        result["success"] = result["target_ready"]
        result["error"] = (
            ""
            if result["success"]
            else "No unique Unity target is ready; verify --instance and discovery."
        )
    except (connection.UnityConnectionError, httpx.HTTPError, ValueError, OSError):
        # Do not echo response bodies, authentication material or rejected selectors.
        pass
    return result


def run_readiness(config: CLIConfig) -> ReadinessResult:
    """Check health, authenticated discovery and target availability without dispatching work."""
    return asyncio.run(_readiness(config))
