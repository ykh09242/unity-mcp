"""Verify blinding, paired outcome definitions and the two-profile acceptance gate."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import family_first_trial as trial
from tools.experiments.discovery_evaluation_fixture import EvaluationSuite, Request


def crossover():
    requests = tuple(
        Request(
            language,
            task,
            f"{language} original {task}",
            frozenset({"right"}),
            frozenset({"opposite"}),
            None,
        )
        for language in ("aa", "bb", "cc", "dd")
        for task in ("status", "list")
    )
    suite = EvaluationSuite(trial.HOLDOUT_SHA256, requests)
    captures = {}
    for arm in trial.ARMS:
        routes = ["opposite", "right"] if arm == "shipping" else ["right"]
        records = [
            {
                "language": request.language,
                "task": request.task,
                "groups": [[{"route": route, "purpose": "Declared purpose"} for route in routes]],
            }
            for request in requests
        ]
        captures[arm] = {
            "corpus": suite.sha256,
            "arm": arm,
            "profiles": {profile: records for profile in trial.PROFILES},
        }
    return suite, captures


def test_each_actor_sees_each_original_once_and_each_cell_equally():
    suite, captures = crossover()
    _, packets = trial.prepare(suite, captures)
    assignments = list(trial._schedule(suite.requests))
    for index, packet in enumerate(packets):
        assert {case["request"] for case in packet} == {r.query for r in suite.requests}
        assert all(set(case) == {"id", "request", "groups"} for case in packet)
        assert Counter(
            (arm, profile) for _, arm, profile, actor, _ in assignments if actor == index
        ) == dict.fromkeys(trial.CELLS, 2)
    assert len({row["id"] for packet in packets for row in packet}) == 4 * len(suite.requests)
    assert all(len(packet) == len(suite.requests) for packet in packets)


def test_first_choice_can_use_offered_alias_and_is_counted_once():
    suite, captures = crossover()
    _, packets = trial.prepare(suite, captures)
    choices = [{case["id"]: "right" for case in packet} for packet in packets]
    result = trial.report(suite, captures, choices, "frozen")
    for scores in result["metrics"].values():
        assert scores["shipping"] == {"count": 8, "top3": 0, "first_choice": 8, "opposite_first": 8}
        assert scores["candidate"] == {
            "count": 8,
            "top3": 8,
            "first_choice": 8,
            "opposite_first": 0,
        }
    assert not result["replace"]
    trial.verify_record(suite, result)
    result["rows"][0]["candidate"]["first_choice"] = None
    with pytest.raises(ValueError, match="Changed first choices"):
        trial.verify_record(suite, result)
    choices[0].pop(next(iter(choices[0])))
    with pytest.raises(ValueError, match="incomplete"):
        trial.report(suite, captures, choices, "frozen")


def test_unoffered_choice_and_abstention_are_misses():
    suite, _ = crossover()
    request = suite.requests[0]
    result = {"groups": [["other"]], "first_choice": "right"}
    row = {
        "profile": "core",
        "language": request.language,
        "task": request.task,
        "shipping": result,
        "candidate": {**result, "first_choice": None},
    }
    scores = trial.summarize(suite, [row])["core"]
    assert scores["shipping"]["first_choice"] == scores["candidate"]["first_choice"] == 0


def test_gate_requires_every_condition_in_both_profiles():
    good = {
        p: {
            "candidate": {"count": 8, "top3": 7, "first_choice": 6, "opposite_first": 2},
            "shipping": {"count": 8, "top3": 6, "first_choice": 5, "opposite_first": 2},
        }
        for p in trial.PROFILES
    }
    assert trial.passed(good)
    for profile in trial.PROFILES:
        for field, value in (("top3", 6), ("first_choice", 5), ("opposite_first", 3), ("count", 7)):
            bad = deepcopy(good)
            bad[profile]["candidate"][field] = value
            assert not trial.passed(bad)
