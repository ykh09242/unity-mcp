"""The compact profile reranks candidate wording using its existing learned vectors."""

from services.registry import semantic_alignment as alignment
from services.registry import semantic_encoder


def test_alignment_recovers_matching_verb_and_object_without_raising_confidence():
    documents = ("Erase the audio clip.", "Play the audio clip.", "Create a camera.")
    scores = alignment.rerank(
        "play the audio clip", documents, (0.42, 0.41, 0.30), semantic_encoder._model()
    )

    assert scores[1] > scores[0] > scores[2]
    assert max(scores) == 0.42


def test_alignment_preserves_no_evidence_and_strong_sentence_match():
    documents = ("Inspect the file.", "Delete the file.")
    for query, pooled in (
        ("", (0.0, 0.0)),
        ("\ufffd", (0.31, 0.28)),
        ("Inspect the file.", (1.0, 0.3)),
    ):
        assert alignment.rerank(query, documents, pooled, semantic_encoder._model()) == pooled


def test_alignment_limits_word_count_and_keeps_candidates_outside_shortlist_in_order(monkeypatch):
    monkeypatch.setattr(alignment, "_CANDIDATES", 2)
    monkeypatch.setattr(alignment, "_MAX_WORDS", 3)
    documents = ("Duplicate mesh", "Inspect mesh", "Duplicate", "Inspect")
    model = semantic_encoder._model()
    first = alignment.rerank("inspect the mesh", documents, (0.55, 0.54, 0.53, 0.52), model)
    longer = alignment.rerank(
        "inspect the mesh " + "copy " * 200, documents, (0.55, 0.54, 0.53, 0.52), model
    )

    assert first == longer
    assert min(first[:2]) >= first[2] > first[3]


def test_close_joint_ranking_uses_independent_word_evidence():
    scores = {0: 0.55, 1: 0.54, 2: 0.45}
    primary = (0.4, 0.6, 0.2)
    alignment._corroborate(scores, [1, 0, 2], primary, {})
    assert scores[1] > scores[0] > scores[2]
    assert scores[0] - scores[2] > 0.1


def test_single_encoder_cannot_promote_a_distant_passage():
    scores = {0: 0.7, 1: 0.45, 2: 0.44}
    primary = (0.4, 0.7, 0.3)
    alignment._corroborate(scores, [1, 0, 2], primary, {})
    assert scores[0] > scores[1] > scores[2]


def test_single_model_agreement_preserves_the_peak_and_separates_weak_alternatives():
    pooled = (0.7, 0.6, 0.3)
    agreed = alignment._single_model_scores(pooled, (0.7, 0.65, 0.4))
    assert agreed[0] == max(pooled)
    assert agreed[0] - agreed[1] > pooled[0] - pooled[1]
    assert agreed[2] < agreed[1]
