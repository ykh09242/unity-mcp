"""Exercise read recall on a registration absent from calibration and the Unity catalog."""

import pytest

from services.registry import semantic_family_recall as recall
from services.registry.semantic_operations import Operation

OWNER = "port_hub"
DETAIL = ("tool", "hub_api", "get_active_port")
PORTS = ("tool", "hub_api", "list_ports")
CABLES = ("tool", "hub_api", "list_cables")
EDIT = ("tool", "hub_api", "assign_port")
RESOURCE = ("resource", "machine://hues", "")
OPS = {
    DETAIL: Operation(
        OWNER, DETAIL[2], "Read the current active port and its name.", (), ("port_id",)
    ),
    PORTS: Operation(OWNER, PORTS[2], "List each port, its name and active flag.", ()),
    CABLES: Operation(OWNER, CABLES[2], "List each cable and its length.", ()),
    EDIT: Operation(OWNER, EDIT[2], "Assign a port to a cable.", ()),
}
DOCS = {
    **{k: (op.purpose,) for k, op in OPS.items()},
    RESOURCE: ("Palette. Read the ambient light color.",),
}
EFFECTS = {k: "edit" if k == EDIT else "observe" for k in DOCS}
MODEL = recall.metric.encoder.COMBINED_MODEL_ID


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Which port is currently active?", DETAIL),
        ("Show all available ports.", PORTS),
    ],
)
def test_an_unretrieved_family_can_contribute_one_visible_read(query, expected):
    ranked = (MODEL, {RESOURCE: 0.42})
    alias = ("tool", OWNER, expected[2])
    ops = {**OPS, alias: OPS[expected]}
    effects = {**EFFECTS, alias: "observe"}
    for docs, wanted in ((DOCS, expected), ({**DOCS, alias: DOCS[expected]}, alias)):
        model, scores = recall.rank_observations(query, docs, ops, effects, ranked)
        assert model == MODEL and list(sorted(scores, key=scores.get, reverse=True)) == [
            RESOURCE,
            wanted,
        ]
        assert ranked[1] == {RESOURCE: 0.42}
    filtered = {k: v for k, v in DOCS.items() if k != expected}
    assert recall.rank_observations(query, filtered, ops, effects, ranked) is ranked
    assert (
        recall.rank_observations(query, DOCS, ops, {**effects, expected: "edit"}, ranked) is ranked
    )


def test_subject_and_intent_can_rule_out_an_unrelated_pair():
    ranked = (MODEL, {RESOURCE: 0.42})
    for query in ("List the cables.", "Set this port active."):
        assert recall.rank_observations(query, DOCS, OPS, EFFECTS, ranked) is ranked
    unknown = ("unknown-model", {})
    assert recall.rank_observations("ports", DOCS, OPS, EFFECTS, unknown) is unknown


def test_compound_read_titles_distinguish_inventory_subjects():
    ranked = (MODEL, {CABLES: 0.64})
    _, scores = recall.rank_observations("List the ports.", DOCS, OPS, EFFECTS, ranked)
    assert max(scores, key=scores.get) == PORTS
    assert CABLES in scores and EDIT not in scores
