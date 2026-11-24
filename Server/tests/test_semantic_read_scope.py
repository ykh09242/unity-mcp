"""Scope transfer, filtered registrations and optional-asset fallbacks."""

from unittest.mock import patch
from dataclasses import replace

import pytest

from services.registry import semantic_read_scope as scope
from services.registry.semantic_operations import Operation

DETAIL = ("tool", "port_inspector", "get_active_port")
LIST = ("tool", "port_inspector", "list_ports")
OTHER = ("tool", "thermometer", "get_active_port")
EDIT = ("tool", "port_setup", "assign_port")
OPS = {
    DETAIL: Operation("hub", DETAIL[2], "Read the active port's name, address and rate.", ()),
    LIST: Operation("hub", LIST[2], "List all ports with their address and active flag.", ()),
    OTHER: Operation("thermometer", OTHER[2], "Read the active port's current temperature.", ()),
    EDIT: Operation("hub", EDIT[2], "Assign a port to a connection.", ()),
}
DOCS = {key: (op.purpose,) for key, op in OPS.items()}
EFFECTS = {key: "edit" if key == EDIT else "observe" for key in OPS}
MODEL = scope.metric.encoder.COMBINED_MODEL_ID


@pytest.mark.parametrize(
    ("leader", "query", "expected"),
    [
        (LIST, "De tous les ports disponibles, lequel est actif en ce moment ?", DETAIL),
        (DETAIL, "Quels sont tous les ports disponibles ?", LIST),
        (LIST, "Which port is active now?", DETAIL),
        (LIST, "Show all available ports and their active flags.", LIST),
        (
            LIST,
            "List the open ports. Mark the active port in the results. Leave the hub unchanged.",
            LIST,
        ),
    ],
)
def test_resolve_scope_on_a_new_family(leader, query, expected):
    baseline = (MODEL, {leader: 0.64, OTHER: 0.59})
    receipt, scores = scope.rank_observations(query, DOCS, OPS, EFFECTS, baseline)
    assert receipt == MODEL
    assert max(scores, key=scores.__getitem__) == expected
    assert scores[OTHER] == baseline[1][OTHER]
    assert EDIT not in scores and baseline[1] == {leader: 0.64, OTHER: 0.59}


def test_scoped_catalog_and_explicit_selector_keep_their_bindings():
    alias = ("tool", "hub", DETAIL[2])
    ops = {**OPS, alias: OPS[DETAIL]}
    effects = {**EFFECTS, alias: "observe"}
    ranked = (MODEL, {LIST: 0.62})
    query = "De tous les ports disponibles, lequel est actif en ce moment ?"
    # Only the forwarding entry is advertised; a hidden source and another owner's
    # identical selector are never introduced by scope calibration.
    _, scores = scope.rank_observations(query, DOCS, ops, effects, ranked)
    assert set(scores) == {LIST, DETAIL}
    filtered = {k: v for k, v in DOCS.items() if k != DETAIL}
    assert scope.rank_observations(query, filtered, ops, effects, ranked) is ranked
    assert scope.rank_observations("List hub ports", DOCS, ops, effects, ranked) is ranked
    assert scope.rank_observations(query, DOCS, ops, {**effects, DETAIL: "edit"}, ranked) is ranked


def test_a_weak_standalone_leader_can_reveal_a_better_scoped_read_in_its_neighborhood():
    resource = ("resource", "device://preferences", "")
    docs = {**DOCS, resource: ("Read the workspace preferences.",)}
    query = "Which port is active now?"
    for peak, expected in ((0.21, DETAIL), (0.60, resource)):
        ranked = (MODEL, {resource: peak, LIST: peak - 0.01})
        _, scores = scope.rank_observations(query, docs, OPS, EFFECTS, ranked)
        assert max(scores, key=scores.__getitem__) == expected


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_scope_weights_preserve_rank(tmp_path, monkeypatch, corrupt):
    artifact = tmp_path / "scope.q8"
    if corrupt:
        artifact.write_bytes(scope._HEADER + bytes(4 * scope._AXIS.size))
    monkeypatch.setattr(scope, "_ARTIFACT", artifact)
    scope._weights.cache_clear()
    try:
        ranked = (MODEL, {LIST: 0.64})
        assert (
            scope.rank_observations("Which port is active?", DOCS, OPS, EFFECTS, ranked) is ranked
        )
    finally:
        scope._weights.cache_clear()


@pytest.mark.parametrize("index", [0, 1])
def test_only_the_receipted_encoder_is_used(index):
    metric = scope.metric
    models = (metric.encoder.MODEL_ID, metric.coverage.MODEL_ID)
    excluded, method = (metric.coverage, "_load") if index == 0 else (metric.encoder, "_model")
    with patch.object(excluded, method, side_effect=AssertionError("unavailable encoder invoked")):
        receipt, scores = scope.rank_observations(
            "List the available ports.", DOCS, OPS, EFFECTS, (models[index], {DETAIL: 0.64})
        )
    assert receipt == models[index] and set(scores) <= {LIST, DETAIL}


def test_clause_boundaries_preserve_qualified_identifiers():
    assert scope._clauses("For com.example.device, which port is active?") == (
        "For com.example.device",
        "which port is active",
    )


@pytest.mark.parametrize(
    ("required", "recover"),
    [(("hub", "port_id"), True), (("port_id",), False), (("hub",), False)],
)
def test_only_a_nested_input_contract_can_recover_an_unranked_read_pair(
    monkeypatch, required, recover
):
    ops = {
        DETAIL: replace(OPS[DETAIL], required=required),
        LIST: replace(OPS[LIST], required=("hub",)),
        EDIT: OPS[EDIT],
    }
    # The ordinary retriever placed a different action first. No read from this pair
    # was in its leading neighborhood. Both purposes and the request prefer the list.
    ranked = (MODEL, {EDIT: 0.61})
    monkeypatch.setattr(scope, "_request_scope", lambda *args: (("request",), (), [-1.0]))
    monkeypatch.setattr(
        scope, "_evidence", lambda op, *args: ([1.0], 0.7) if op == ops[LIST] else ([-1.0], 0.5)
    )
    _, scores = scope.rank_observations("request", DOCS, ops, EFFECTS, ranked)
    assert (LIST in scores) == recover
    assert max(scores, key=scores.__getitem__) == EDIT
    assert DETAIL not in scores
