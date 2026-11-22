"""Replay independently authored original requests against the advertised catalog."""

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from services.resources import register_all_resources
from services.tools import register_all_tools


TARGETS = {
    "menu-declaration": ("list_menu_declarations", None, "mcpforunity://menu-items"),
    "console-errors": ("read_console", "get", None),
    "camera-depth": ("inspect_scene_cameras", None, "mcpforunity://scene/cameras"),
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
async def test_heldout_original_text_ranks_the_observation_first(
    monkeypatch, profile, filename, locales, tasks, entry
):
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
    prompts = json.loads(
        (Path(__file__).resolve().parents[1] / "experiments" / filename).read_text(encoding="utf-8")
    )
    server = FastMCP("original-observation-requests")
    register_all_tools(server)
    register_all_resources(server)
    async with Client(server, mode="legacy", cache=False) as client:
        for task in tasks:
            tool, action, uri = TARGETS[task]
            for locale in locales:
                query = prompts[task][locale]
                for surface in ("tools", "both", "resources") if uri else ("tools", "both"):
                    args = {"query": query, "route_surfaces": surface, "limit": 3}
                    if entry == "manage_tools":
                        args["action"] = "search"
                    result = (await client.call_tool(entry, args)).structured_content
                    assert query == result["query"] == result["matched_query"]
                    first = result["route_groups"][0]["representative"]
                    if surface == "resources":
                        assert first["uri"] == uri, (task, locale, surface)
                    else:
                        assert (first["tool"], first.get("operation")) == (tool, action), (
                            task,
                            locale,
                            surface,
                        )
                    assert tool in [row["name"] for row in result["results"]]
