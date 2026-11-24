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


def passing_checks():
    return {
        arm: {
            "arm": arm,
            "test_ids_sha256": "identical-case-set",
            "suites": {Path(p).stem: {"count": 1, "unsuccessful": {}} for p in trial.CONTRACTS},
        }
        for arm in trial.ARMS
    }


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
    checks = passing_checks()
    result = trial.report(suite, captures, choices, "frozen", checks)
    for scores in result["metrics"].values():
        assert scores["shipping"] == {"count": 8, "top3": 0, "first_choice": 8, "opposite_first": 8}
        assert scores["candidate"] == {
            "count": 8,
            "top3": 8,
            "first_choice": 8,
            "opposite_first": 0,
        }
    assert not result["replace"]
    trial.verify_replay(suite, captures, result, checks)
    changed = deepcopy(captures)
    changed["candidate"]["profiles"]["core"][0]["groups"][0][0]["purpose"] = "Changed purpose"
    with pytest.raises(ValueError, match="packet contents"):
        trial.verify_replay(suite, changed, result, checks)
    result["rows"][0]["candidate"]["first_choice"] = None
    with pytest.raises(ValueError, match="Changed first choices"):
        trial.verify_record(suite, result)
    choices[0].pop(next(iter(choices[0])))
    with pytest.raises(ValueError, match="incomplete"):
        trial.report(suite, captures, choices, "frozen", checks)


def test_compatibility_failure_blocks_an_improving_challenger():
    suite, captures = crossover()
    checks = passing_checks()
    _, packets = trial.prepare(suite, captures)
    choices = [{row["id"]: row["groups"][0][0]["route"] for row in packet} for packet in packets]
    assert trial.report(suite, captures, choices, "frozen", checks)["replace"]
    suite_name = Path(trial.CONTRACTS[0]).stem
    checks["candidate"]["suites"][suite_name]["unsuccessful"]["regression"] = "failure"
    result = trial.report(suite, captures, choices, "frozen", checks)
    assert result["heldout_gate_passed"] and not result["replace"]
    checks["shipping"]["test_ids_sha256"] = "different"
    with pytest.raises(ValueError, match="different compatibility cases"):
        trial.report(suite, captures, choices, "frozen", checks)


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


def test_published_trial_reconstructs_the_blinded_decisions_and_development_split():
    from tools.experiments.discovery_evaluation_fixture import load_suite
    from tools.experiments.discovery_family_split import belongs_to, query_identity
    from tools.experiments.fit_family_first import HELD_FAMILIES, HELD_LANGUAGES, SOURCES

    suite = load_suite(trial.HOLDOUT)
    records = trial.read_json(trial.HOLDOUT.with_name("family_first_results.json"))
    assert suite.sha256 == trial.HOLDOUT_SHA256
    assert {r.language for r in suite.requests} == HELD_LANGUAGES
    assert len(suite.requests) == len({query_identity(r.query) for r in suite.requests}) == 48
    families = Counter(
        next(f for f in HELD_FAMILIES if any(belongs_to(k, f) for k in r.expected))
        for r in suite.requests
    )
    assert families == dict.fromkeys(HELD_FAMILIES, 16)
    for name, checksum in SOURCES.items():
        source = load_suite(trial.HOLDOUT.with_name(name))
        assert source.sha256 == checksum
        assert all(r.language not in HELD_LANGUAGES for r in source.requests)
        assert not any(
            belongs_to(k, f)
            for r in source.requests
            for k in r.expected | r.wrong_effect
            for f in HELD_FAMILIES
        )
    assert records["candidate_revision"] == "3d6bba60a033e4877b8792ef4aa25be4a70acaad"
    assert len({*records["packet_sha256"]}) == 4
    choices = trial.verify_record(suite, records)
    assert len(choices) == 4 and all(len(answers) == 48 for answers in choices)
