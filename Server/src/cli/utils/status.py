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
    """Require a unique discovered target; names and hash prefixes must be unambiguous."""
    if selector is None:
        return len(instances) == 1
    selector = selector.strip()
    if not selector:
        return False
    matches = []
    for instance in instances:
        name = instance.get("project")
        instance_hash = instance.get("hash")
        if not isinstance(name, str) or not isinstance(instance_hash, str) or not instance_hash:
            continue
        if (
            selector in (name, f"{name}@{instance_hash}", instance.get("session_id"))
            or instance_hash.startswith(selector)
            or "@" in selector
            and name == selector.split("@", 1)[0]
            and instance_hash.startswith(selector.split("@", 1)[1])
        ):
            matches.append(instance)
    return len(matches) == 1


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
    except (httpx.HTTPError, ValueError, OSError):
        # Do not echo response bodies, authentication material or rejected selectors.
        pass
    return result


def run_readiness(config: CLIConfig) -> ReadinessResult:
    """Check health, authenticated discovery and target availability without dispatching work."""
    return asyncio.run(_readiness(config))
