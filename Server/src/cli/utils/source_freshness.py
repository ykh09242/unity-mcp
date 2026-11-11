"""Bind native CLI test starts to the same source protocol as MCP verification."""

import asyncio
from dataclasses import replace
from time import monotonic

from cli.utils.connection import UnityCommandError
from cli.utils.project_context import exact_hash
from services.source_freshness import observe_source_freshness


async def prepare_test_start(params, config, *, deadline, send, inventory):
    selected = config
    if exact_hash(config.unity_instance) is None:
        try:
            response = await asyncio.wait_for(inventory(config), max(0, deadline - monotonic()))
        except TimeoutError:
            raise UnityCommandError(
                {
                    "success": False,
                    "code": "editor_selection_unavailable",
                    "error": "Editor selection deadline expired; no tests were started.",
                }
            ) from None
        matches = []
        for row in response.get("instances", []):
            name, digest = row.get("project"), row.get("hash")
            if not isinstance(name, str) or exact_hash(digest) is None:
                continue
            identity = name + "@" + digest
            if config.unity_instance is None or config.unity_instance in (name, digest, identity):
                matches.append(identity)
        if len(matches) != 1:
            raise UnityCommandError(
                {
                    "success": False,
                    "code": "exact_editor_required",
                    "error": "Select one exact Editor instance before starting source-guarded tests.",
                }
            )
        selected = replace(config, unity_instance=matches[0])

    async def dispatch(command, arguments):
        remaining = max(0, deadline - monotonic())
        return await asyncio.wait_for(
            send(command, arguments, selected, timeout=remaining), remaining
        )

    async def permitted(_command, _arguments):
        # The HTTP server checks the live grants and optional project-root guard
        # independently on every native command; the CLI cannot confer authority.
        return True

    evidence = await observe_source_freshness(
        dispatch, permitted, deadline=deadline, refresh_policy="if_needed"
    )
    if evidence.status != "observed_current":
        raise UnityCommandError(
            {
                "success": False,
                "code": "source_freshness_required",
                "error": "Current source compilation and loaded assemblies were not confirmed.",
                "data": {"source_freshness": evidence.model_dump(by_alias=True)},
                "hint": "Inspect the existing source/compile evidence before retrying; no tests were started.",
            }
        )
    return (
        {**params, "source_token": evidence.source_token, "require_source_freshness": True},
        selected,
        max(0, deadline - monotonic()),
    )
