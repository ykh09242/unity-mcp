from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.script_quality.impact import declaration_changes, inspect_script_impact


def result(name="count", kind="field", type_name="int", former=None):
    return {
        "source_token": "a" * 64,
        "project_id": "project",
        "environment_signature": "env",
        "complete": True,
        "stale": False,
        "declarations_complete": True,
        "selected_files": [{"path": "Assets/C.cs", "guid": "b" * 32, "bytes_sha256": "c" * 64}],
        "declarations": [
            {
                "path": "Assets/C.cs",
                "container": "C",
                "kind": kind,
                "name": name,
                "type": type_name,
                "signature": name + "(" + type_name + ")",
                "serialized": kind == "field",
                "former_names": former or [],
            }
        ],
    }


def test_field_rename_with_migration_is_reported_without_claiming_safe_conversion():
    delta = declaration_changes(result(), result("total", former=["count"]))
    assert delta["complete"]
    assert delta["changes"][0]["change"] == "field_renamed_with_alias"
    assert delta["changes"][0]["name"] == "count"
    assert (
        declaration_changes(result(), result("total"))["changes"][0]["change"]
        == "field_removed_or_renamed"
    )
    assert (
        declaration_changes(result(), result(type_name="string"))["changes"][0]["change"]
        == "serialized_type_changed"
    )


def test_method_rename_and_signature_changes_are_not_assumed_to_migrate_events():
    assert (
        declaration_changes(result("Click", "method"), result("Other", "method"))["changes"][0][
            "change"
        ]
        == "method_removed_or_renamed"
    )
    assert (
        declaration_changes(result("Click", "method"), result("Click", "method", "string"))[
            "changes"
        ][0]["change"]
        == "method_signature_changed"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("complete", False),
        ("stale", True),
        ("declarations_complete", False),
        ("environment_signature", "other"),
    ],
)
def test_incomplete_or_incompatible_declarations_cannot_claim_changes(key, value):
    current = result("total")
    current[key] = value
    delta = declaration_changes(result(), current)
    assert not delta["complete"] and not delta["changes"]


def graph_page(revision=4, cursor=None):
    return {
        "success": True,
        "data": {
            "revision": revision,
            "scope": "graph-scope",
            "edges": [
                {
                    "id": "edge",
                    "owner": "asset:" + "d" * 32,
                    "target": "asset:" + "b" * 32,
                    "source_path": "Assets/C.prefab",
                    "kind": "unity_dependency",
                    "provider": "unity",
                }
            ],
            "next_cursor": cursor,
            "graph_status": {
                "current": True,
                "revision": revision,
                "consistency": "checked_snapshot",
                "providers": {"unity": {"current": True}},
            },
        },
    }


@pytest.mark.asyncio
async def test_dependency_is_only_potential_and_missing_baseline_still_returns_references():
    graph = SimpleNamespace(execute=AsyncMock(return_value=graph_page()))
    native = AsyncMock(
        return_value={"success": True, "data": {"assets": [], "coverage_gaps": ["binary_asset"]}}
    )
    page = await inspect_script_impact(
        None,
        result(),
        None,
        options={"analysis_id": "e" * 32},
        owner=("u", "p"),
        secret=b"secret",
        graph=graph,
        native=native,
    )
    assert "change_comparison_unavailable" in page["coverage_gaps"]
    assert page["findings"][0]["certainty"] == "potential_reference"
    assert page["findings"][0]["member_evidence"] == []
    assert not page["complete"]
    assert graph.execute.call_args_list[0].args[2]["consistency"] == "checked"


@pytest.mark.asyncio
async def test_graph_drift_during_native_observation_refuses_page():
    graph = SimpleNamespace(execute=AsyncMock(side_effect=[graph_page(), graph_page(5)]))
    with pytest.raises(ValueError, match="graph_revision_changed"):
        await inspect_script_impact(
            None,
            result("total"),
            result(),
            options={"analysis_id": "e" * 32},
            owner=("u", "p"),
            secret=b"secret",
            graph=graph,
            native=AsyncMock(return_value={"success": True, "data": {"assets": []}}),
        )


@pytest.mark.asyncio
async def test_cursor_is_bound_to_analysis_baseline_owner_and_graph_revision():
    graph = SimpleNamespace(
        execute=AsyncMock(side_effect=[graph_page(cursor="next"), graph_page()])
    )
    native = AsyncMock(return_value={"success": True, "data": {"assets": []}})
    options = {"analysis_id": "e" * 32, "baseline_id": "f" * 32}
    page = await inspect_script_impact(
        None,
        result("total"),
        result(),
        options=options,
        owner=("u", "p"),
        secret=b"secret",
        graph=graph,
        native=native,
    )
    assert page["next_cursor"]
    graph.execute.reset_mock()
    with pytest.raises(ValueError, match="invalid_impact_cursor"):
        await inspect_script_impact(
            None,
            result("total"),
            result(),
            options={**options, "cursor": page["next_cursor"]},
            owner=("other", "p"),
            secret=b"secret",
            graph=graph,
            native=native,
        )
    graph.execute.assert_not_awaited()
    graph.execute.side_effect = [graph_page(5)]
    with pytest.raises(ValueError, match="graph_revision_changed"):
        await inspect_script_impact(
            None,
            result("total"),
            result(),
            options={**options, "cursor": page["next_cursor"]},
            owner=("u", "p"),
            secret=b"secret",
            graph=graph,
            native=native,
        )


def test_guid_move_preserves_declaration_comparison_but_duplicate_guid_is_ambiguous():
    current = result("total")
    current["selected_files"][0]["path"] = "Assets/Moved.cs"
    current["declarations"][0]["path"] = "Assets/Moved.cs"
    assert declaration_changes(result(), current)["complete"]
    current["selected_files"].append(deepcopy(current["selected_files"][0]))
    assert not declaration_changes(result(), current)["complete"]
