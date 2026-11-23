"""Direction evidence follows visible runtime declarations and the active encoder."""

import pytest

from services.registry import semantic_catalog, semantic_direction as direction, task_search


def loom_documents():
    item = {
        "name": "loom_station",
        "description": "Operate the fibers on the loom.",
        "kwargs": {},
        "input_schema": {
            "properties": {"action": {"enum": ["braid_strand", "unbraid_strand"]}},
            "oneOf": [
                {
                    "properties": {"action": {"const": "braid_strand"}},
                    "description": "Braid the fibers into a strand.",
                },
                {
                    "properties": {"action": {"const": "unbraid_strand"}},
                    "description": "Unbraid the strand and separate its fibers.",
                },
            ],
        },
    }
    return task_search.semantic_documents(
        [(item, task_search.tool_capability(item))], [], set(), None
    )


@pytest.mark.parametrize(
    "model",
    [direction.encoder.MODEL_ID, direction.coverage.MODEL_ID, direction.encoder.COMBINED_MODEL_ID],
)
def test_purpose_only_runtime_dispatcher_resolves_close_opposites(monkeypatch, model):
    catalog = loom_documents()
    braid = ("tool", "loom_station", "braid_strand")
    unbraid = ("tool", "loom_station", "unbraid_strand")
    scores = {braid: 0.69, unbraid: 0.67}
    assert set(catalog) == set(scores)
    assert all(len(views) == 1 for views in catalog.values())

    def unavailable():
        pytest.fail("Sibling contrast loaded an encoder outside the active receipt")

    if model == direction.encoder.MODEL_ID:
        monkeypatch.setattr(direction.coverage, "_load", unavailable)
    elif model == direction.coverage.MODEL_ID:
        monkeypatch.setattr(direction.encoder, "_model", unavailable)
    adjusted = direction.contrast_siblings(
        "Unbraid the strand and separate its fibers.",
        model,
        catalog,
        {catalog[key][0]: score for key, score in scores.items()},
        scores,
        neighborhood=0.12,
    )
    assert adjusted[unbraid] == max(scores.values())
    assert adjusted[unbraid] > adjusted[braid]
    assert all(abs(adjusted[key] - scores[key]) <= 0.1200001 for key in scores)


def test_competing_read_requires_same_subject_and_stronger_action_evidence():
    catalog = loom_documents()
    braid, unbraid = sorted(catalog)
    catalog[braid] += ("Weave the strands together.",)
    catalog[unbraid] += ("Take this strand apart.",)
    read = ("resource", "lab://loom/strand", "")
    catalog[read] = ("loom strand. Count the strands on the loom.",)
    raw = {
        catalog[braid][0]: 0.41,
        catalog[braid][1]: 0.42,
        catalog[unbraid][0]: 0.56,
        catalog[unbraid][1]: 0.54,
        catalog[read][0]: 0.60,
    }
    scores = {key: max(raw[t] for t in texts) for key, texts in catalog.items()}

    def winner(query):
        adjusted = direction.contrast_siblings(
            query, direction.encoder.MODEL_ID, catalog, raw, scores, neighborhood=0.12
        )
        return max(adjusted, key=adjusted.__getitem__)

    assert winner("Unbraid this strand of fibers") == unbraid
    assert winner("Count the strands") == read
    catalog[read] = ("loom cylinder. Count all cylinders on this loom.",)
    raw[catalog[read][0]] = scores[read]
    assert winner("Unbraid this strand of fibers") == read


def test_sibling_evidence_is_applied_before_catalog_cutoff(monkeypatch):
    catalog = loom_documents()
    braid, unbraid = sorted(catalog)
    catalog[braid] += ("Weave the strands together.",)
    catalog[unbraid] += ("Take this strand apart.",)
    read = ("resource", "lab://loom/strand", "")
    catalog[read] = ("loom strand. Count the strands on the loom.",)
    raw = dict(
        zip(
            (*catalog[braid], *catalog[unbraid], *catalog[read]),
            (0.30, 0.31, 0.46, 0.45, 0.60),
            strict=True,
        )
    )
    monkeypatch.setattr(
        semantic_catalog,
        "encode_scores",
        lambda query, docs: (direction.encoder.MODEL_ID, tuple(raw[text] for text in docs)),
    )
    _, scores = semantic_catalog.semantic_scores("Unbraid the strand", catalog)
    assert scores[read] == 0.60
    assert scores[unbraid] == pytest.approx(0.58)
    assert braid not in scores


def test_disagreement_or_hidden_sibling_cannot_supply_a_margin(monkeypatch):
    first, second = ("tool", "cabinet", "lowerDrawer"), ("tool", "cabinet", "raiseDrawer")
    catalog = {
        first: ("cabinet lower drawer. Lower a drawer.", "Slide it down."),
        second: ("cabinet raise drawer. Raise a drawer.", "Slide it up."),
    }
    raw = dict(zip((*catalog[first], *catalog[second]), (0.55, 0.40, 0.50, 0.56), strict=True))
    scores = {first: 0.55, second: 0.56}

    def unexpected(*args):
        pytest.fail("No competing views justify an encoder call")

    monkeypatch.setattr(direction, "_matches", unexpected)
    for visible in (scores, {first: scores[first]}):
        adjusted = direction.contrast_siblings(
            "move the drawer", direction.encoder.MODEL_ID, catalog, raw, visible, neighborhood=0.12
        )
        assert adjusted == visible
