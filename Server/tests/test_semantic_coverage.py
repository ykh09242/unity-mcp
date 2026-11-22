"""Unigram coverage under the default offline search profile."""

from services.registry import semantic_coverage as coverage
from services.registry import semantic_encoder as encoder


def test_coverage_selection_depends_on_recognized_wordpieces():
    vocab, _ = encoder._model()
    assert not coverage.needs_fallback("", vocab)
    assert not coverage.needs_fallback("!?", vocab)
    assert not coverage.needs_fallback("Où est la gare ?", vocab)
    assert not coverage.needs_fallback("深度の値が最も大きいカメラはどれですか？", vocab)
    assert coverage.needs_fallback("รถไฟกำลังเข้าสถานี", vocab)
    assert coverage.needs_fallback("รถไฟกำลังเข้าสถานี Platform", vocab)


def test_unigram_scores_arbitrary_new_documents_in_order():
    docs = ("Bake bread in an oven.", "The train arrives at the railway station.")
    train = coverage.similarities("รถไฟกำลังเข้าสถานี", docs)
    bread = coverage.similarities("อบขนมปังในเตาอบ", docs)
    assert train[1] > train[0] + 0.2
    assert bread[0] > bread[1] + 0.2
    assert coverage.similarities("รถไฟกำลังเข้าสถานี", docs[::-1]) == train[::-1]
    assert coverage.similarities("", docs) == (0.0, 0.0)
    assert coverage.similarities("anything", ()) == ()


def test_unigram_normalization_and_segmentation_match_pinned_tokenizer():
    model = coverage._load()
    assert list(model.tokenize("กล้องในฉาก")) == [408122, 1199, 136953]
    assert list(model.tokenize("  ＭＥＮＵ \t ")) == list(model.tokenize("MENU"))
    assert list(model.tokenize("ഡോ")) == [448268]
    assert list(model.tokenize("[UNK] [PAD]")) == []


def test_fallback_receipts_identify_the_model_that_actually_scored(monkeypatch):
    docs = ("Bake bread in an oven.", "The train arrives at the railway station.")
    model, scores = encoder.semantic_scores("รถไฟกำลังเข้าสถานี", docs)
    assert model == coverage.MODEL_ID and scores[1] > scores[0]

    def missing(*args):
        raise encoder.SemanticModelUnavailable("missing model file")

    with monkeypatch.context() as local:
        local.setattr(encoder, "_model", missing)
        model, scores = encoder.semantic_scores("Train station", docs)
        assert model == coverage.MODEL_ID and scores[1] > scores[0]
    monkeypatch.setattr(coverage, "similarities", missing)
    model, scores = encoder.semantic_scores("รถไฟกำลังเข้าสถานี train", docs)
    assert model == encoder.MODEL_ID and scores[1] > scores[0]
