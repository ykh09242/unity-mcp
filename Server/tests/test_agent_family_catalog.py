"""Agent selection depends only on public declarations, including every sibling operation."""

import pytest

from services.registry import task_search


def tool(name, actions):
    return {
        "name": name,
        "group": None,
        "description": "Read or adjust instrument ports.",
        "kwargs": {},
        "input_schema": {"properties": {"action": {"enum": actions}}},
    }


def test_agent_can_compare_every_public_binding_without_a_ranker(monkeypatch):
    entries = [tool("ports", ["status", "list", "set", "reset"]), tool("private_port", ["erase"])]
    monkeypatch.setattr(task_search, "_term_evidence", lambda *_: pytest.fail("invoked ranker"))
    query = "  Which port is selected?  "

    def request(**extra):
        return task_search.search_catalog(
            task_search.SearchRequest(selection="agent", query=query, limit=2, **extra),
            entries,
            visible_names={"ports"},
        )

    index = request()
    assert index["query"] == query and index["matching"] == "catalog"
    assert [f["family"] for f in index["families"]] == ["ports"]
    first = request(family="ports")
    last = request(family="ports", offset=first["next_offset"])
    assert last["next_offset"] is None and first["total"] == 4
    assert {op["action"] for op in first["operations"] + last["operations"]} == {
        "status",
        "list",
        "set",
        "reset",
    }
    assert all(
        op["tool"] == "ports" and op["advertised"]
        for op in first["operations"] + last["operations"]
    )
    assert request(family="private_port").get("success") is False
    assert request(host_tool_names=[])["families"] == []


def test_resources_and_templates_present_independent_public_read_contracts():
    resources = [
        {
            "uri": "mcpforunity://meters/summary",
            "name": "Summary",
            "description": "Read meter overview.",
            "kwargs": {},
        },
        {
            "uri": "mcpforunity://meters/{meter}",
            "name": "Meter",
            "description": "Read a named meter.",
            "kwargs": {},
        },
    ]

    def run(**kw):
        r = task_search.SearchRequest(
            selection="agent", query="meter", route_surfaces="resources", **kw
        )
        return task_search.search_catalog(
            r, [tool("ports", ["status"])], resources=resources, visible_names={"ports"}
        )

    index = run()
    assert {f["family"] for f in index["families"]} == {r["uri"] for r in resources}
    (fixed,) = run(family=resources[0]["uri"])["operations"]
    (template,) = run(family=resources[1]["uri"])["operations"]
    assert fixed["uri"] == resources[0]["uri"] and fixed["intent"] == "observe"
    assert template["uri_template"] == resources[1]["uri"] and template["required_parameters"] == [
        "meter"
    ]


def test_visible_forwarders_share_a_family_with_hidden_owner_and_keep_distinct_wire_contracts(
    monkeypatch,
):
    from services.registry import family_catalog

    entries = [
        tool("native_meter", ["status", "calibrate"]),
        tool("meter_reads", ["status"]),
        tool("meter_changes", ["calibrate"]),
    ]
    monkeypatch.setattr(family_catalog, "operation_owner", lambda name, selector: "native_meter")
    visible = {"meter_reads", "meter_changes"}

    def run(**options):
        return task_search.search_catalog(
            task_search.SearchRequest(selection="agent", query="read", **options),
            entries,
            visible_names=visible,
        )

    (family,) = run()["families"]
    assert "native_meter" not in str(family)
    page = run(family=family["family"])
    assert [(r["tool"], r["action"]) for r in page["operations"]] == [
        ("meter_changes", "calibrate"),
        ("meter_reads", "status"),
    ]
    assert (
        run(host_tool_evidence={"candidates": ["meter_changes"], "present": [], "complete": True})[
            "families"
        ][0]["count"]
        == 1
    )


def test_discriminated_wire_schema_preserves_declared_purposes_and_conditional_inputs():
    entry = tool("ports", [])
    entry["input_schema"] = {
        "oneOf": [
            {
                "properties": {"action": {"const": "status"}},
                "description": "Read the current port.",
            },
            {
                "properties": {"action": {"const": "set"}},
                "description": "Change the current port.",
                "required": ["action", "port"],
            },
        ]
    }
    response = task_search.search_catalog(
        task_search.SearchRequest(selection="agent", family="ports", query="request"), [entry]
    )
    observed = {
        row["action"]: (row["purpose"], row["required_parameters"])
        for row in response["operations"]
    }
    assert observed == {
        "status": ("Read the current port.", []),
        "set": ("Change the current port.", ["port"]),
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"selection": "agent", "intent": "edit"},
        {"selection": "agent", "catalog_query": "ports"},
        {"selection": "agent", "observation_source": "editor_live"},
        {"family": "ports"},
        {"offset": 3},
    ],
)
def test_family_selection_never_silently_filters_away_siblings(arguments):
    with pytest.raises(ValueError):
        task_search.SearchRequest(query="request", **arguments)
