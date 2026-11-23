"""Offline model integrity, inference and tokenizer compatibility regressions."""

from pathlib import Path
import socket

import pytest

from services.registry import semantic_coverage
from services.registry import semantic_encoder as encoder


def test_bundled_weights_rank_unseen_domain_without_network(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Catalog inference opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    docs = ("The train arrives at the station.", "Bake a chocolate cake in the oven.")
    model, scores = encoder.semantic_scores("Faire cuire un gâteau au chocolat.", docs)
    assert model == encoder.COMBINED_MODEL_ID
    assert scores[1] > scores[0]
    assert encoder.semantic_similarities("🔥", docs) == (0, 0)


def test_overlapping_catalogs_reuse_declarations_and_encode_each_request(monkeypatch):
    from unittest.mock import Mock

    encode = Mock(wraps=encoder._encode)
    monkeypatch.setattr(encoder, "_encode", encode)
    query = "switch the signal at the railway station"
    common = "set the railway signal"
    first = encoder._pooled_scores(query, (common, "inspect the signal"))
    second = encoder._pooled_scores(query, ("remove the signal", common))

    assert first[0] == second[1]
    texts = [call.args[0] for call in encode.call_args_list]
    assert texts.count(common) == 1
    assert texts.count(query) == 2


@pytest.mark.parametrize("module", [encoder, semantic_coverage])
def test_verify_checks_current_bytes_after_warming_inference(module, tmp_path, monkeypatch):
    encoder.semantic_scores("railway station", ("ride the train", "bake a cake"))
    with pytest.raises(encoder.SemanticModelUnavailable, match="unavailable"):
        with monkeypatch.context() as local:
            local.setattr(module, "_ARTIFACT", tmp_path / "absent")
            module.verify_model()
    damaged = tmp_path / "corrupt"
    damaged.write_bytes(b"truncated")
    monkeypatch.setattr(module, "_ARTIFACT", damaged)
    with pytest.raises(encoder.SemanticModelUnavailable, match="integrity"):
        module.verify_model()


def test_bundled_model_does_not_require_a_home_directory(monkeypatch):
    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", no_home)
    model, scores = encoder.semantic_scores("Find trains", ("railway train", "bake bread"))
    assert model == encoder.COMBINED_MODEL_ID and scores[0] > scores[1]


def test_pinned_uncased_wordpiece_preserves_boundaries_and_ignores_unknown_words():
    vocab, _ = encoder._model()
    # Token strings from the upstream tokenizer.json with add_special_tokens=False.
    text = "Café! 콘솔에서 오류. [CLS]अन्दर 深度 coöperateƛ"
    expected = [
        "cafe",
        "!",
        "ᄏ",
        "##ᅩᆫ",
        "##소",
        "##ᆯ에",
        "##서",
        "ᄋ",
        "##ᅩ",
        "##류",
        ".",
        "अ",
        "##नद",
        "##र",
        "深",
        "度",
    ]
    actual = [
        offset for word in encoder._words(text) for offset in encoder._piece_offsets(word, vocab)
    ]
    assert actual == [vocab[token] for token in expected]


def test_ideographs_and_nonideographic_word_continuations_follow_the_vocabulary():
    vocab, _ = encoder._model()
    text = "A深B A〆B A〇B"
    expected = ["a", "深", "b", "a", "##〆", "##b", "a", "##〇", "##b"]
    actual = [i for word in encoder._words(text) for i in encoder._piece_offsets(word, vocab)]
    assert actual == [vocab[token] for token in expected]
