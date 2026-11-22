"""The static model compares operation context without dropping the original request."""

import pytest

from services.registry import semantic_catalog as catalog
from services.registry import semantic_coverage, semantic_profile


@pytest.mark.parametrize(
    ("model", "context"),
    [
        (catalog.STATIC_MODEL_ID, "in find panel"),
        (semantic_coverage.MODEL_ID, "in Unity find ’s panel"),
        (semantic_profile.MODEL_ID, "in Unity find ’s panel"),
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
    monkeypatch.setattr(catalog, "rerank_scores", lambda *_: None)
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
