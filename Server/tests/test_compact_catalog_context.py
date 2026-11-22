"""The static model compares operation context without dropping the original request."""

import json

from click.testing import CliRunner
import pytest

from services.registry import semantic_catalog as catalog
from services.registry import semantic_coverage


@pytest.mark.parametrize(
    ("model", "context"),
    [
        (catalog.STATIC_MODEL_ID, "in find panel"),
        (semantic_coverage.MODEL_ID, "in Unity find ’s panel"),
    ],
)
def test_platform_and_attached_name_suffix_are_neutral_only_for_static_model(
    monkeypatch, model, context
):
    query = "in Unity find Silver Loom’s panel"
    calls = []

    def encode(text, documents):
        calls.append(" ".join(text.split()))
        return model, (0.7,) * len(documents)

    monkeypatch.setattr(catalog, "encode_scores", encode)
    key = ("tool", "inspect_panel", "")
    actual_model, scores = catalog.semantic_scores(query, {key: ("inspect the panel",)})
    assert actual_model == model and scores[key] == pytest.approx(0.7)
    assert calls == [query, context]


@pytest.mark.parametrize(
    "query",
    ["Unity", "show warnings in Unity", "  show warnings in Unity  ", "find Unity input.actions"],
)
def test_simple_platform_request_retains_original_embedding(monkeypatch, query):
    calls = []

    def encode(text, documents):
        calls.append(text)
        return catalog.STATIC_MODEL_ID, (0.7,) * len(documents)

    monkeypatch.setattr(catalog, "encode_scores", encode)
    catalog.semantic_scores(query, {("tool", "sample", ""): ("sample",)})
    assert calls == [query]


@pytest.mark.parametrize("separator", [". ", "。", "؟ ", "；", "।", "።"])
def test_compound_request_uses_surrounding_operation_context(separator):
    query = f"show the Unity warnings{separator}copy the complete messages"
    assert "Unity" not in catalog._request_context(query, compact=True)
    assert catalog._request_context(query) == query


@pytest.mark.parametrize("tag", ["_foo._bar", "Assets/रंग/नीला.asset"])
def test_cli_keeps_console_read_first_with_literal_log_tag(tag):
    from cli.main import cli

    query = f"Get warnings from the console in Unity; {tag} is the log tag"
    outcome = CliRunner().invoke(cli, ["--format", "json", "tool", "search", query])
    assert outcome.exit_code == 0, outcome.output
    result = json.loads(outcome.stdout)
    assert result["query"] == result["matched_query"] == query
    assert result["semantic_model"] == catalog.STATIC_MODEL_ID
    first = result["route_groups"][0]["representative"]
    assert (first["tool"], first["operation"]) == ("read_console", "get")
    assert " ".join(catalog._request_context(query, compact=True).split()) == (
        "Get warnings from the console in ; is the log tag"
    )


def test_platform_name_is_not_removed_from_a_file_extension_or_part_of_a_qualified_name():
    simple = "find Unity input.actions with the .unity extension"
    assert catalog._request_context(simple, compact=True) == simple
    compound = "list the .unity scenes in unity-mcp; unity.inputactions is the tag"
    assert " ".join(catalog._request_context(compound, compact=True).split()) == (
        "list the .unity scenes in unity-mcp; is the tag"
    )
