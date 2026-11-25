"""Browse advertised observations with original independent fixture requests."""

import json
from pathlib import Path

import pytest

from tools.tests.discovery_agent_contract import (
    AgentBrowse,
    ObservationBinding,
    agent_catalog,
    assert_observation_browsable,
)


TARGETS = {
    "menu-declaration": ObservationBinding(
        "list_menu_declarations", "menu.declared_paths", uri="mcpforunity://menu-items"
    ),
    "console-errors": ObservationBinding("read_console", "console.entries", action="get"),
    "camera-depth": ObservationBinding(
        "inspect_scene_cameras", "unityCameras", uri="mcpforunity://scene/cameras"
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["core", "full"])
@pytest.mark.parametrize(
    ("filename", "locales", "tasks", "entry"),
    [
        (
            "blind_tool_tasks_holdout.json",
            ("ko", "ja", "es", "ar", "tr"),
            tuple(TARGETS),
            "search_unity_tools",
        ),
        (
            "blind_tool_tasks_followup_tr.json",
            ("tr",),
            ("menu-declaration", "console-errors"),
            "manage_tools",
        ),
    ],
)
async def test_agent_catalog_exposes_heldout_observation_bindings(
    profile, filename, locales, tasks, entry
):
    prompts = json.loads(
        (Path(__file__).resolve().parents[1] / "experiments" / filename).read_text(encoding="utf-8")
    )
    async with agent_catalog(profile, {TARGETS[task].tool for task in tasks}) as catalog:
        for task in tasks:
            target = TARGETS[task]
            for locale in locales:
                for surface in ("tools", "both", "resources") if target.uri else ("tools", "both"):
                    await assert_observation_browsable(
                        catalog, AgentBrowse(prompts[task][locale], surface, entry), target
                    )
