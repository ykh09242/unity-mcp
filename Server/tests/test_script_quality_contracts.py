"""Explicit diagnostic selections and public action contracts."""

import pytest
from pydantic import ValidationError

from models.script_quality import parse_quality_options
from services.operation_permissions import Permission, required_permissions


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"paths": []},
        {"paths": ["Assets/A.cs"], "change_set_id": "a" * 32},
        {"paths": ["Assets/A.cs", "Assets\\A.cs"]},
        {"paths": ["Assets/../Outside.cs"]},
        {"paths": ["C:/outside.cs"]},
        {"paths": ["Assets/Resources/GameData/Private.cs"]},
        {"paths": ["Assets/.env.cs"]},
        {"paths": ["Assets/Source~/Host.cs"]},
        {"paths": ["Library/Generated.cs"]},
        {"paths": ["Assets/file.txt"]},
        {"paths": [f"Assets/F{i}.cs" for i in range(257)]},
        {"paths": ["Assets/A.cs"], "format": True},
        {"paths": ["Assets/A.cs"], "owner": "another-user"},
    ],
)
def test_invalid_selection_cannot_become_whole_project_analysis(options):
    with pytest.raises((ValueError, ValidationError)):
        parse_quality_options("analyze", options)


def test_unicode_selection_normalizes_only_separators():
    request = parse_quality_options("analyze", {"paths": ["Assets\\한글.cs"]})
    assert request.paths == ["Assets/한글.cs"]
    assert request.include_suppressed is False


@pytest.mark.parametrize("action", ["capabilities", "status", "cancel"])
def test_read_controls_reject_write_and_selection_fields(action):
    with pytest.raises((ValueError, ValidationError)):
        parse_quality_options(action, {"paths": ["Assets/A.cs"], "job_id": "a" * 32})


@pytest.mark.parametrize("size", [0, 501, True, "10"])
def test_result_page_limits_are_strict(size):
    with pytest.raises((ValueError, ValidationError)):
        parse_quality_options("status", {"job_id": "a" * 32, "page_size": size})


def test_baseline_selection_does_not_accept_another_baseline():
    with pytest.raises((ValueError, ValidationError)):
        parse_quality_options(
            "capture_baseline", {"paths": ["Assets/A.cs"], "baseline_id": "b" * 32}
        )


def test_analysis_requires_read_and_run_without_implying_edit():
    assert required_permissions("manage_script_quality", {"action": "analyze"}) == frozenset(
        {Permission.READ, Permission.RUN}
    )
    assert required_permissions(
        "manage_script_quality", {"action": "capture_baseline"}
    ) == frozenset({Permission.READ, Permission.RUN})
    for action in ("capabilities", "status", "cancel", "impact"):
        assert required_permissions("manage_script_quality", {"action": action}) == frozenset(
            {Permission.READ}
        )
