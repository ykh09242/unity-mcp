"""Bounded, cross-language pair reranking and truthful fallback receipts."""

import os

import pytest

from services.registry import semantic_catalog as catalog, semantic_profile as profile
from services.registry import semantic_worker_client as worker_client


def test_shortlist_caps_groups_and_shares_logits_across_bindings(monkeypatch):
    documents = {("tool", f"operation_{i:02}", ""): (f"passage {i:02}",) for i in range(30)}
    equivalent = ("resource", "mcpforunity://same", "")
    documents[equivalent] = ("passage 00",)
    expected = tuple(f"passage {i:02}" for i in range(24))
    monkeypatch.setattr(
        catalog, "encode_scores", lambda q, texts: (profile.MODEL_ID, (0.5,) * len(texts))
    )

    def rerank(query, texts):
        assert query == "find a passage" and texts == expected
        return tuple(float(i - 10) for i in range(len(texts)))

    monkeypatch.setattr(catalog, "rerank_scores", rerank)
    for ordering in (documents, dict(reversed(documents.items()))):
        model, scores = catalog.semantic_scores("find a passage", ordering)
        assert model == profile.COMBINED_MODEL_ID
        assert len(scores) == 25 and set(scores.values()) == {0.5}
        assert (
            scores.reranker_logits[equivalent]
            == scores.reranker_logits["tool", "operation_00", ""]
            == -10.0
        )
        assert scores.reranker_logits["tool", "operation_23", ""] == 13.0


def test_reranker_uses_two_closest_passages_and_preserves_encoder_only_neighborhood(monkeypatch):
    keys = [("tool", action, "") for action in ("read", "clear", "other", "distant")]
    documents = dict(
        zip(keys, (("read", "log", "unused"), ("clear",), ("other",), ("distant",)), strict=True)
    )
    raw = {"read": 0.6, "log": 0.65, "unused": 0.3, "clear": 0.5, "other": 0.39, "distant": 0.12}
    monkeypatch.setattr(
        catalog, "encode_scores", lambda q, texts: (profile.MODEL_ID, tuple(raw[t] for t in texts))
    )

    def score(query, texts):
        assert texts == ("log", "read", "clear")
        return 0.5, -1.0, 3.0

    monkeypatch.setattr(catalog, "rerank_scores", score)
    model, scores = catalog.semantic_scores("show me the logs", documents)
    assert model == profile.COMBINED_MODEL_ID and scores == {keys[0]: 0.65, keys[1]: 0.5}
    assert scores.reranker_logits == {keys[0]: 0.5, keys[1]: 3.0}
    monkeypatch.setattr(catalog, "rerank_scores", lambda *args: None)
    model, fallback = catalog.semantic_scores("show me the logs", documents)
    assert model == profile.MODEL_ID and fallback == {keys[0]: 0.65}
    assert not hasattr(fallback, "reranker_logits")


def test_weak_or_bundled_candidates_do_not_claim_reranking(monkeypatch):
    docs = {("tool", "read", ""): ("read",)}
    monkeypatch.setattr(catalog, "rerank_scores", lambda *args: pytest.fail("unexpected reranking"))
    monkeypatch.setattr(catalog, "encode_scores", lambda *args: (profile.MODEL_ID, (0.1,)))
    assert catalog.semantic_scores("some unknown thing", docs) == (profile.MODEL_ID, {})
    monkeypatch.setattr(catalog, "encode_scores", lambda *args: ("bundled-model", (0.8,)))
    assert catalog.semantic_scores("some familiar thing", docs) == (
        "bundled-model",
        {next(iter(docs)): 0.8},
    )


@pytest.mark.skipif(
    not os.environ.get("UNITY_MCP_TEST_RERANKER_PROFILE"), reason="requires provisioned reranker"
)
def test_provisioned_reranker_scores_real_pairs_offline(monkeypatch, tmp_path):
    monkeypatch.setenv(profile.PROFILE_ENV, os.environ["UNITY_MCP_TEST_RERANKER_PROFILE"])
    read, clear = ("tool", "read", ""), ("tool", "clear", "")
    docs = {
        read: ("Read the existing console log messages.",),
        clear: ("Delete all messages retained in the Unity console.",),
    }
    query = "Efface toutes les entrées de la console Unity."
    logits = catalog.rerank_scores(query, (docs[read][0], docs[clear][0]))
    assert logits is not None and logits[1] > logits[0]
    model, scores = catalog.semantic_scores(query, docs)
    assert model == profile.COMBINED_MODEL_ID
    assert max(scores.reranker_logits, key=scores.reranker_logits.get) == clear

    stopped_worker = tmp_path / "stopped_worker.py"
    stopped_worker.write_text("raise SystemExit(1)\n", encoding="utf-8")
    monkeypatch.setattr(worker_client, "_RERANKER_PATH", stopped_worker)
    monkeypatch.setattr(catalog, "rerank_scores", worker_client.WorkerSlot(reranker=True).score)
    model, scores = catalog.semantic_scores(query, docs)
    assert model == profile.MODEL_ID and clear in scores
    assert not hasattr(scores, "reranker_logits")
