"""Comparison is evidence-based and duplicate-aware, never a line-number diff."""

from copy import deepcopy

import pytest

from services.script_quality.diagnostic_delta import compare_diagnostics


def diagnostic(*, line=10, anchor="a", path="Assets/A.cs", symbol="C.M", syntax="syntax"):
    return {
        "id": "UNT0008",
        "provider": "Unity",
        "provider_version": "1",
        "path": path,
        "line": line,
        "column": 2,
        "anchor": anchor,
        "syntax_anchor": syntax,
        "symbol": symbol,
        "message": "Unity null semantics",
        "severity": "warning",
        "suppressed": False,
    }


def result(*rows, path="Assets/A.cs", guid="a" * 32):
    return {
        "complete": True,
        "stale": False,
        "project_id": "project",
        "environment_signature": "environment",
        "source_token": "a" * 64,
        "selected_files": [{"path": path, "guid": guid, "bytes_sha256": "b" * 64}],
        "diagnostics": list(rows),
        "context_diagnostics": [],
        "coverage_gaps": [],
        "analyzer_errors": [],
    }


def test_line_insertion_does_not_introduce_or_resolve_a_diagnostic():
    delta = compare_diagnostics(result(diagnostic()), result(diagnostic(line=100)))
    assert delta["complete"] is True
    assert len(delta["existing"]) == 1
    assert delta["introduced"] == delta["resolved"] == []


def test_duplicate_counts_are_preserved():
    delta = compare_diagnostics(
        result(diagnostic(), diagnostic(line=20)), result(diagnostic(line=30))
    )
    assert len(delta["existing"]) == len(delta["resolved"]) == 1
    assert not delta["introduced"]


def test_guid_proven_rename_preserves_identity():
    delta = compare_diagnostics(
        result(diagnostic()), result(diagnostic(path="Assets/Moved.cs"), path="Assets/Moved.cs")
    )
    assert delta["complete"] is True
    assert len(delta["existing"]) == 1


def test_rename_without_guid_evidence_is_unclassified():
    delta = compare_diagnostics(
        result(diagnostic(), guid=None),
        result(diagnostic(path="Assets/Moved.cs"), path="Assets/Moved.cs", guid=None),
    )
    assert delta["complete"] is False
    assert delta["introduced"] == delta["resolved"] == []
    assert "selection_identity_changed" in delta["reasons"]


def test_added_and_resolved_rules_are_separate():
    delta = compare_diagnostics(
        result(diagnostic(anchor="old", syntax="old")),
        result(diagnostic(anchor="new", syntax="new")),
    )
    assert len(delta["introduced"]) == len(delta["resolved"]) == 1


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("complete", False, "analysis_incomplete"),
        ("stale", True, "source_stale"),
        ("environment_signature", "changed", "environment_changed"),
        ("project_id", "other", "project_changed"),
        ("analyzer_errors", ["crashed"], "analysis_incomplete"),
        ("coverage_gaps", ["missing"], "analysis_incomplete"),
    ],
)
def test_incomparable_runs_do_not_resolve_old_diagnostics(field, value, reason):
    after = result()
    after[field] = value
    delta = compare_diagnostics(result(diagnostic()), after)
    assert delta["complete"] is False
    assert delta["resolved"] == []
    assert reason in delta["reasons"]
    assert len(delta["unclassified"]) == 1


def test_missing_baseline_does_not_fabricate_a_clean_prior_state():
    delta = compare_diagnostics(None, result(diagnostic()))
    assert delta["reasons"] == ["baseline_missing"]
    assert len(delta["unclassified"]) == 1
    assert not delta["introduced"]


def test_ambiguous_symbol_relocation_is_not_reported_as_new_or_fixed():
    delta = compare_diagnostics(
        result(diagnostic()), result(diagnostic(anchor="changed", symbol="C.Renamed"))
    )
    assert not delta["complete"]
    assert delta["introduced"] == delta["resolved"] == []
    assert "ambiguous_relocation" in delta["reasons"]


def test_duplicate_file_guids_do_not_authorize_a_rename():
    before = result(diagnostic())
    before["selected_files"].append({"path": "Assets/B.cs", "guid": "a" * 32})
    after = deepcopy(before)
    assert "ambiguous_file_identity" in compare_diagnostics(before, after)["reasons"]
