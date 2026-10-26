"""Audited policy eligibility never trusts same-assembly or namespace alone."""

from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import generate_operation_identity as mod  # noqa: E402


def test_canonical_resource_and_generated_partition_owners_are_exact():
    # Given / When
    owners = mod.canonical_owners(mod.ROOT)
    # Then
    assert owners["get_selection"] == "MCPForUnity.Editor.Resources.Editor.Selection"
    assert owners["query_physics"] == "MCPForUnity.Editor.Tools.Capabilities.QueryPhysics"
    assert owners["manage_physics"] == "MCPForUnity.Editor.Tools.Physics.ManagePhysics"
    assert owners["batch_execute"] == "MCPForUnity.Editor.Tools.BatchExecute"
    assert "execute_code" not in owners
    assert "capture_ui_snapshot" not in owners


def test_generated_identity_table_is_current():
    # Given / When
    expected = mod.render()
    # Then
    assert mod.TARGET.read_text(encoding="utf-8") == expected


def test_source_scan_never_uses_attribute_lookalikes_inside_comments_or_strings():
    # Given
    source = r"""namespace Owned.Package {
        // [McpForUnityTool("get_selection")] public static class CommentSpoof {}
        public class Text { string value = "[McpForUnityTool(\"get_selection\")] public static class StringSpoof {}"; string @namespace; }
        [McpForUnityResource("get_selection")]
        public static class Actual {}
    }"""
    # When / Then
    assert mod.declarations(source) == {"get_selection": "Owned.Package.Actual"}


def test_ambiguous_or_nested_declarations_cannot_generate_trusted_owners():
    # Given
    duplicate = """namespace Owned.Package {
        [McpForUnityResource("get_selection")] public static class First {}
        [McpForUnityResource("get_selection")] public static class Second {}
    }"""
    nested = """namespace Owned.Package { public static class Outer {
        [McpForUnityResource("get_selection")] public static class Nested {}
    }}"""
    # When / Then
    with pytest.raises(ValueError, match="Duplicate command"):
        mod.declarations(duplicate)
    with pytest.raises(ValueError, match="top-level"):
        mod.declarations(nested)
