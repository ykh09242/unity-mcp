"""Verify the boundaries of the family-first experiment with new registrations."""

from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.registry.semantic_operations import Operation
from tools.experiments import family_first_search as search
from tools.experiments import family_first_metric as metric


def test_every_visible_sibling_is_compared_after_family_selection(monkeypatch):
    read = ("tool", "device_panel", "inspect_voltage")
    write = ("tool", "device_panel", "set_voltage")
    other = ("tool", "colour_panel", "list_swatches")
    hidden = ("tool", "device_panel", "erase")
    alias = ("tool", "electrical", "inspect_voltage")
    ops = {
        read: Operation("electrical", read[2], "Report the measured voltage.", (), ("port",)),
        write: Operation(
            "electrical", write[2], "Adjust the output voltage.", (), ("port", "value")
        ),
        other: Operation("colours", other[2], "Enumerate paint swatches.", ()),
        hidden: Operation("electrical", hidden[2], "Erase the device memory.", ()),
    }
    ops[alias] = ops[read]
    docs = {k: (v.purpose,) for k, v in ops.items() if k != hidden}
    effects = {k: "observe" if v in (ops[read], ops[other]) else "edit" for k, v in ops.items()}
    compared = []

    def shortlist(vectors, families):
        assert vectors
        assert set(families) == {"electrical", "colours"}
        return {"electrical": 0.6}

    def compare(vectors, cards):
        assert vectors
        compared.extend(card.selector for card in cards)
        return {card: 0.9 if card.selector == read[2] else 0.5 for card in cards}

    monkeypatch.setattr(search, "_select_families", shortlist)
    monkeypatch.setattr(search, "_compare_operations", compare)
    model, ranked = search.rank_families("meter request", docs, ops, effects)
    assert model and sorted(compared) == sorted((read[2], write[2]))
    assert max(ranked, key=ranked.get) == alias
    assert hidden not in ranked and other not in ranked


def test_a_standalone_resource_remains_an_eligible_family():
    key = ("resource", "meter://temperature", "")
    documents = {key: ("Temperature. Read the current room temperature.",)}
    _, scores = search.rank_families(
        "What is the room temperature?", documents, {}, {key: "observe"}
    )
    assert set(scores) == {key}


def test_forward_only_binding_uses_its_declared_owner():
    key = ("tool", "device_panel", "inspect_voltage")
    op = Operation("electrical", key[2], "Report the measured voltage.", (), ("port",))
    card = next(search._cards({key: (op.purpose,)}, {key: op}, {}))
    assert (card.family, card.bindings, card.required) == ("electrical", (key,), ("port",))


def test_empty_and_single_encoder_results(monkeypatch):
    from services.registry import semantic_coverage as coverage, semantic_encoder as encoder

    monkeypatch.setattr(encoder, "_model", lambda: ({}, b""))
    monkeypatch.setattr(metric, "encode", lambda *_args, **_kwargs: ())
    assert metric.query_vectors("") == (encoder.MODEL_ID, ())
    assert search.rank_families("", {}, {}, {}) == (encoder.MODEL_ID, {})

    def unigram_only(*_args):
        raise encoder.SemanticModelUnavailable

    monkeypatch.setattr(encoder, "_model", unigram_only)
    monkeypatch.setattr(metric, "encode", lambda *_args, **_kwargs: (1.0,))
    assert metric.query_vectors("meter") == (coverage.MODEL_ID, ((1, 1.0, (1.0,)),))


def test_corrupt_trial_metric_is_rejected(tmp_path):
    invalid = tmp_path / "damaged.q8"
    payload = bytearray(metric.ARTIFACT.read_bytes())
    payload[-1] ^= 1
    invalid.write_bytes(payload)
    with patch.object(metric, "ARTIFACT", invalid), pytest.raises(ValueError, match="Invalid"):
        metric.weights.__wrapped__()
