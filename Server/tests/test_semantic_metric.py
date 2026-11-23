"""Verify shared metric inference on new declarations, degraded encoders and filtered catalogs."""

import pytest
from services.registry import semantic_metric as metric
from services.registry.semantic_operations import Operation

SEAL = ("tool", "records", "seal_record")
INSPECT = ("tool", "records", "inspect_record")
TOOLS = {SEAL: ("records seal record. Seal an existing record.",), INSPECT: ("Inspect a record.",)}
OPS = {
    SEAL: Operation("record_manager", "seal_record", "Seal an existing record.", ()),
    INSPECT: Operation("record_manager", "inspect_record", "Inspect a record.", ()),
}


def test_document_and_query_views_preserve_local_fallback_and_literals():
    op = Operation("record_manager", "seal_record", "", ())
    assert metric.document_fields(SEAL, TOOLS[SEAL], op) == (
        "seal record",
        "Seal an existing record.",
        ("Seal an existing record.",),
        "record",
    )
    raw, context = metric.query_views("Verrouillez «Dossier» maintenant.")
    assert "Dossier" in raw and "Dossier" not in context and "Verrouillez" in context


@pytest.mark.parametrize("kind", ["missing", "corrupt"])
def test_missing_or_corrupt_metric_preserves_canonical_results(kind, monkeypatch, tmp_path):
    path = tmp_path / "metric"
    if kind == "corrupt":
        path.write_bytes(bytes(metric._SIZE))
    monkeypatch.setattr(metric, "_ARTIFACT", path)
    base = metric.encoder.COMBINED_MODEL_ID, {INSPECT: 0.55}
    metric._weights.cache_clear()
    try:
        assert metric.recall_operation("fermez le dossier", TOOLS, OPS, base, base) is base
    finally:
        metric._weights.cache_clear()


@pytest.mark.parametrize(
    "index,model", [(0, metric.encoder.MODEL_ID), (1, metric.coverage.MODEL_ID)]
)
def test_single_encoder_receipt_scores_new_tools_without_reading_the_other(
    monkeypatch, index, model
):
    def unavailable():
        pytest.fail("Loaded an encoder excluded by the receipt")

    if index == 0:
        monkeypatch.setattr(metric.coverage, "_load", unavailable)
    else:
        monkeypatch.setattr(metric.encoder, "_model", unavailable)
    ordered, supported = metric._adapted_scores("Seal the record.", TOOLS, OPS, (model, {}))
    assert set(ordered) == set(TOOLS) and supported == set(OPS.values())
    assert ordered[SEAL] > ordered[INSPECT]


def test_alternate_slot_uses_only_visible_evidence_and_keeps_winning_score(monkeypatch):
    baseline = "fixture", {INSPECT: 0.65}
    monkeypatch.setattr(
        metric, "_adapted_scores", lambda *args: ({INSPECT: 0.30, SEAL: 0.70}, {OPS[SEAL]})
    )
    _, result = metric.recall_operation("fermez le dossier", TOOLS, OPS, baseline, baseline)
    assert set(result) == set(TOOLS) and result[INSPECT] > result[SEAL]
    assert baseline[1] == {INSPECT: 0.65}
    monkeypatch.setattr(
        metric, "_adapted_scores", lambda *args: ({INSPECT: 0.65, SEAL: 0.70}, {OPS[INSPECT]})
    )
    assert metric.recall_operation("fermez le dossier", TOOLS, OPS, baseline, baseline) is baseline


@pytest.mark.parametrize("learned, expected", [(0.61, False), (0.95, True)])
def test_independent_metric_must_overcome_the_best_siblings_measured_lead(
    monkeypatch, learned, expected
):
    unrelated = ("tool", "unrelated", "")
    docs = {**TOOLS, unrelated: ("List other items.",)}
    base = "fixture", {unrelated: 0.75, INSPECT: 0.70, SEAL: 0.58}
    revised = {unrelated: 0.22, INSPECT: 0.55, SEAL: learned}
    monkeypatch.setattr(metric, "_adapted_scores", lambda *args: (revised, set()))
    _, scores = metric.recall_operation("fermez le dossier", docs, OPS, base, base)
    assert (scores[SEAL] > scores[INSPECT]) is expected
    assert scores[unrelated] > scores[SEAL]
