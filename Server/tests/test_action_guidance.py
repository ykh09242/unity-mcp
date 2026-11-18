"""Action cards expose actionable requirements and distinguish observation from mutation."""

import pytest

from services.registry import get_registered_tools
from services.registry.action_catalog import CatalogRequest, browse_catalog
from services.tools import manage_build, manage_editor, manage_scene, manage_shader  # noqa: F401


@pytest.mark.parametrize(
    ("tool", "action", "required", "read_only", "intent"),
    [
        ("manage_scene", "get_hierarchy", [], False, "observe"),
        ("manage_scene", "get_active", [], False, "observe"),
        ("manage_scene", "get_loaded_scenes", [], False, "observe"),
        ("manage_scene", "get_build_settings", [], False, "observe"),
        ("manage_scene", "load", [], False, "edit"),
        ("manage_scene", "close_scene", [], False, "edit"),
        ("manage_scene", "move_to_scene", ["target"], False, "edit"),
        ("manage_scene", "validate", [], False, None),
        ("manage_editor", "play", [], False, "interact"),
        ("manage_editor", "telemetry_status", [], False, "observe"),
        ("manage_editor", "cancel_play_mode_job", ["job_id"], False, None),
        ("manage_editor", "restore_game_view_size", ["restore_token"], False, None),
        ("manage_shader", "update", ["name", "contents"], False, "edit"),
        ("manage_build", "status", [], True, "observe"),
        ("manage_build", "platform", [], False, None),
        ("manage_build", "report", ["report_id"], True, "observe"),
        (
            "manage_build",
            "compare_reports",
            ["baseline_report_id", "candidate_report_id"],
            True,
            "observe",
        ),
    ],
)
def test_action_cards_distinguish_observation_from_mutation(
    tool, action, required, read_only, intent
):
    page = browse_catalog(CatalogRequest(tool=tool), get_registered_tools())
    card = next(card for card in page["actions"] if card["action"] == action)

    assert card["required_parameters"] == required
    assert card["effects"]["readOnlyHint"] is read_only
    assert card["intent"] == intent
    assert card["purpose_source"] == "declared"


def test_batch_build_card_exposes_effects_and_observation_limit():
    page = browse_catalog(CatalogRequest(tool="manage_build"), get_registered_tools())
    card = next(card for card in page["actions"] if card["action"] == "batch")

    assert card["required_parameters"] == []
    assert card["effects"]["readOnlyHint"] is False
    assert card["purpose_source"] == "declared"
    assert card["evidence_limit"]
