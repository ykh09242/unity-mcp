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


@pytest.mark.parametrize(
    ("votes", "direct_support", "should_promote"),
    [
        ((0.4, 0.6), False, True),
        ((0.9, -0.1), True, False),
        ((0.4,), False, False),
        ((0.4,), True, True),
        ((), True, False),
    ],
)
def test_read_and_edit_of_a_new_property_require_agreement(
    monkeypatch, votes, direct_support, should_promote
):
    read = ("tool", "channel_status", "inspect_default_channel")
    choose = ("tool", "channel_setup", "choose_default_channel")
    backup = ("tool", "channel_setup", "choose_backup_channel")
    hidden = ("tool", "channel_manager", "choose_default_channel")
    ops = {
        read: Operation("channel_manager", read[2], "Inspect the default channel.", ()),
        choose: Operation("channel_manager", choose[2], "Choose the default channel.", ()),
        backup: Operation("channel_manager", backup[2], "Choose a backup channel.", ()),
    }
    ops[hidden] = ops[choose]
    catalog = {key: (op.purpose,) for key, op in ops.items() if key != hidden}
    effects = {read: "observe", choose: "edit", backup: "edit", hidden: "edit"}
    leader = ("fixture", {read: 0.65})
    monkeypatch.setattr(
        metric,
        "_query_vectors",
        lambda *_: [(i, 1 / len(votes), (1.0,)) for i in range(len(votes))],
    )
    monkeypatch.setattr(
        metric, "_weights", lambda: metric.MetricWeights((), tuple(((v,), 0.0) for v in votes))
    )

    def vector(text):
        return (0.5 if "inspect" in text else float(direct_support),)

    monkeypatch.setattr(metric, "_document", lambda fields, _: (vector(fields[0]), ()))
    monkeypatch.setattr(metric, "_encode", lambda text, *a, **kw: vector(text))
    _, scores = metric._rank_effect("request", catalog, ops, effects, leader)
    assert (max(scores, key=scores.__getitem__) == choose) == should_promote
    assert set(scores) <= {read, choose}
    assert leader[1] == {read: 0.65}


def test_action_direction_cannot_introduce_an_edit_from_a_different_owner():
    from services.registry.semantic_operations import effect_pairs

    read = ("tool", "notebook", "inspect_default_format")
    edit = ("tool", "printer", "set_default_format")
    ops = {
        read: Operation("notebook", read[2], "Inspect the default format.", ()),
        edit: Operation("printer", edit[2], "Set the default format.", ()),
    }
    assert not effect_pairs(ops, ops, {read: "observe", edit: "edit"}, {read: 0.5}, "notebook")


def test_direction_alone_cannot_choose_between_opposite_edits_of_the_same_property():
    from services.registry.semantic_operations import effect_pairs

    selectors = ("inspect_labels", "attach_labels", "detach_labels")
    ops = {
        ("tool", "notebook", selector): Operation(
            "notebook", selector, selector.replace("_", " "), ()
        )
        for selector in selectors
    }
    read, attach, detach = ops
    effects = {read: "observe", attach: "edit", detach: "edit"}
    assert not effect_pairs(ops, ops, effects, {read: 0.7}, "notebook")
