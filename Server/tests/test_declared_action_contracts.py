"""Cross-check declared inputs and effects through the existing admission surfaces."""

from unittest.mock import Mock

import pytest
from fastmcp.tools import FunctionTool
from jsonschema import Draft202012Validator
from services.addressable_prefab import prefab_parameters
from services.declared_action_contracts import ACTION_CONTRACTS, PREFAB_CHECKPOINT_CONTRACTS
from services.execution_receipt_context import execution_owner
from services.operation_permissions import required_permissions
from services.registry.action_catalog import action_card
from services.registry.action_contracts import action_input_schema
from services.tools.manage_addressables import PREFAB_ACTIONS, manage_addressables


@pytest.mark.parametrize(
    "tool,action",
    [(tool, action) for tool, actions in ACTION_CONTRACTS.items() for action in actions],
)
def test_action_card_agrees_with_runtime_permissions(tool, action):
    declaration = ACTION_CONTRACTS[tool][action]
    card = action_card(
        {
            "name": tool,
            "kwargs": {},
            "input_schema": {"properties": {"action": {"enum": [action]}}},
        },
        action,
    )
    assert card["required_parameters"] == list(declaration.required), (tool, action, "required")
    assert card["forbidden_parameters"] == list(declaration.forbidden), (tool, action, "forbidden")
    assert card.get("alias_of") == declaration.alias_of, (tool, action, "alias_of")
    assert card["effects"]["readOnlyHint"] is declaration.read_only, (tool, action, "readOnlyHint")
    assert card["effects"]["destructiveHint"] is (not declaration.read_only), (
        tool,
        action,
        "destructiveHint",
    )
    assert card["required_permissions"] == list(declaration.permissions), (
        tool,
        action,
        "permissions",
    )
    assert sorted(
        permission.value
        for permission in required_permissions(tool, {"action": action}, native=True)
    ) == list(declaration.permissions), (tool, action, "native permissions")


def test_static_prefab_action_list_matches_checkpoint_admission():
    # This list stays literal because the distributed skill resolves its AST offline.
    assert PREFAB_ACTIONS == tuple(PREFAB_CHECKPOINT_CONTRACTS)


@pytest.mark.parametrize("action", PREFAB_CHECKPOINT_CONTRACTS)
def test_prefab_public_shapes_and_private_wire_names(action):
    tool = FunctionTool.from_function(manage_addressables)
    validator = Draft202012Validator(action_input_schema("manage_addressables", tool.parameters))
    declaration = PREFAB_CHECKPOINT_CONTRACTS[action]
    values = {
        "operation_id": "0" * 32,
        "previous_operation_id": "1" * 32,
        "expected_observation": "2" * 64,
        "settings_path": "Assets/ProjectSettings.asset",
        "group_guid": "3" * 32,
        "source_prefab": "Assets/Source.prefab",
        "prefab_path": "Assets/Destination.prefab",
    }
    arguments = {"action": action, **{name: values[name] for name in declaration.required}}
    validator.validate(arguments)
    # The MCP surface ignores explicitly null unused fields. The raw route
    # validates the supplied set before replacing owner-bound operation IDs.
    assert validator.is_valid({**arguments, "entry_guid": None})
    assert not validator.is_valid({**arguments, "entry_guid": "3" * 32})
    assert not validator.is_valid({**arguments, "page_size": 10})
    assert validator.is_valid({**arguments, "page_number": 1, "page_size": 50})
    owner = Mock()
    owner.prefab_checkpoint.side_effect = lambda value: value * 2
    token = execution_owner.set(owner)
    try:
        wire = prefab_parameters(arguments)
        assert wire.keys() == {"action", *declaration.native_required}, (action, "native fields")
        for public, native in declaration.wire_aliases:
            assert wire[native] == arguments[public] * 2, (action, public, native)
        for field in declaration.required:
            missing = {name: value for name, value in arguments.items() if name != field}
            assert not validator.is_valid(missing), (action, field, "missing")
            assert not validator.is_valid({**arguments, field: None}), (action, field, "null")
            with pytest.raises(ValueError, match=field):
                prefab_parameters(missing)
        with pytest.raises(ValueError, match="requires exactly"):
            prefab_parameters({**arguments, "checkpoint": "0" * 64})
    finally:
        execution_owner.reset(token)
