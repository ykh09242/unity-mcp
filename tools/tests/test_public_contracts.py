"""Machine schema drift reports exact paths while ignoring prose-only annotations."""

from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def test_contract_preserves_properties_named_title_and_description_and_literal_defaults():
    # Given: machine properties and defaults that share schema-annotation names.
    from public_contracts import schema_contract

    source = {
        "type": "object",
        "title": "prose",
        "description": "prose",
        "properties": {
            "title": {"type": "string"},
            "description": {
                "type": "object",
                "default": {"title": "literal", "description": "literal"},
            },
        },
    }

    # When: the advertised schema is projected to its machine contract.
    result = schema_contract(source)

    # Then: annotations are omitted but property identity and literal values survive.
    assert "title" not in result
    assert "description" not in result
    assert result["properties"] == source["properties"]


def test_breaking_drift_identifies_removed_tool_enum_and_added_required_property():
    # Given: previously advertised input choices and an optional field.
    from public_contracts import contract_changes

    before = {
        "modern": {
            "tools": {
                "probe": {
                    "inputSchema": {
                        "properties": {"action": {"enum": ["read", "write"]}},
                        "required": [],
                    }
                },
                "removed": {},
            }
        }
    }
    after = {
        "modern": {
            "tools": {
                "probe": {
                    "inputSchema": {
                        "properties": {"action": {"enum": ["read"]}},
                        "required": ["target"],
                    }
                }
            }
        }
    }

    # When: the new advertisement is compared to the committed one.
    changes = contract_changes(before, after)

    # Then: conservative breaking candidates point at actionable machine paths.
    assert {item.path for item in changes} == {
        "/modern/tools/removed",
        "/modern/tools/probe/inputSchema/properties/action/enum",
        "/modern/tools/probe/inputSchema/required",
    }
    assert all(item.kind == "potentially_breaking" for item in changes)


def test_additive_tool_is_classified_separately():
    # Given: an unchanged baseline and one new optional tool.
    from public_contracts import contract_changes

    # When: a catalog grows.
    changes = contract_changes({"tools": {}}, {"tools": {"probe": {}}})

    # Then: inventory additions are visible without pretending a break occurred.
    assert [(item.kind, item.path) for item in changes] == [("added", "/tools/probe")]


def test_new_required_constraint_is_a_breaking_candidate_when_previously_absent():
    # Given: a schema previously accepted calls without mandatory inputs.
    from public_contracts import contract_changes

    # When: a required constraint is added for the first time.
    changes = contract_changes({"type": "object"}, {"type": "object", "required": ["target"]})

    # Then: the new constraint is not mislabeled as a harmless inventory addition.
    assert [(item.kind, item.path) for item in changes] == [("potentially_breaking", "/required")]


def test_boolean_and_numeric_literals_remain_distinct_inside_objects_and_arrays():
    from public_contracts import contract_changes

    before = {"properties": {"option": {"const": True, "enum": [{"nested": [False]}]}}}
    after = {"properties": {"option": {"const": 1, "enum": [{"nested": [0]}]}}}
    for old, new in ((before, after), (after, before)):
        assert [(item.kind, item.path) for item in contract_changes(old, new)] == [
            ("potentially_breaking", "/properties/option/const"),
            ("potentially_breaking", "/properties/option/enum"),
        ]
    assert contract_changes({"enum": [True, 1]}, {"enum": [True, 1.0]}) == []


def test_snapshot_observes_real_modern_and_legacy_tools_list_in_isolated_process():
    # Given: a deterministic fixed-profile exporter that does not contact Unity.
    from public_contracts import capture_public_contracts

    # When: both actual SDK protocol advertisements are observed.
    result = capture_public_contracts()

    # Then: the public dispatch schema, output contract and hints are represented.
    assert result["scope"] == {
        "profile": "full",
        "transport": "in_process",
        "project": "none",
        "hosted": False,
    }
    assert set(result["protocols"]) == {"modern", "legacy"}
    for protocol in result["protocols"].values():
        probe = protocol["tools"]["manage_tools"]
        assert "search" in probe["inputSchema"]["properties"]["action"]["enum"]
        assert "outputSchema" in probe
        assert probe["annotations"]["destructiveHint"] is False
