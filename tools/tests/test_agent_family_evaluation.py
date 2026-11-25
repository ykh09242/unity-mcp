"""Protect the preregistered split and the actual two-stage decision receipts."""

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import agent_family_evaluation as trial


def test_held_out_originals_cross_new_languages_with_three_contrasting_families():
    existing_languages = set()
    for path in trial.FIXTURE.parent.glob("universal_discovery*.json"):
        existing_languages.update(json.loads(path.read_bytes()).get("queries", {}))
    cases = [c for c in trial.cases() if c.profile == "core"]
    published = [c.request for c in cases if c.panel == "existing"]
    held = [c.request for c in cases if c.panel == "held_out"]
    assert len(published) == 23 and len(held) == 36
    assert existing_languages.isdisjoint(r.language for r in held)
    assert {r.query for r in published}.isdisjoint(r.query for r in held)
    owners = defaultdict(Counter)
    for request in held:
        expected, opposite = request.expected, request.wrong_effect
        assert expected and opposite and expected.isdisjoint(opposite)
        routes = {name.partition(".")[0] for name in expected | opposite}
        assert len(routes) == 1
        owners[request.language][routes.pop()] += 1
    assert len(owners) == 6
    for counts in owners.values():
        assert counts == dict.fromkeys(
            ("manage_audio", "manage_input_actions", "manage_terrain"), 2
        )


def test_frozen_family_and_first_choice_receipts_cover_every_original_in_each_profile():
    saved = trial.read(trial.FIXTURE.with_name("agent_family_results.json"))
    cases = {c.key: c for c in trial.cases()}
    rows = saved["choices"]
    assert len(rows) == len(cases) == 118 and {r["id"] for r in rows} == cases.keys()
    assert trial.summarize(rows) == saved["summary"]
    assert saved["holdout_sha256"] == trial.HOLDOUT_SHA
    seen_by = defaultdict(set)
    for row in rows:
        c = cases[row["id"]]
        seen_by[c.request.query].add(c.actor)
        assert (row["actor"], row["profile"], row["panel"], row["task"], row["language"]) == (
            c.actor,
            c.profile,
            c.panel,
            c.request.task,
            c.request.language,
        )
        assert row["correct_first"] == (row["first"] in c.request.expected)
        assert row["opposite_first"] == (row["first"] in c.request.wrong_effect)
        assert row["abstained"] == (row["first"] is None)
        assert not row["correct_first"] or row["covered"]
    assert all(len(actors) == 2 for actors in seen_by.values())
    assert {p["actor"] for p in saved["packets"]} == set(range(4))
    for packet in saved["packets"]:
        assigned = [r for r in rows if r["actor"] == packet["actor"]]
        for key, digest_key in (("families", "families_sha256"), ("first", "choices_sha256")):
            assert trial.digest({r["id"]: r[key] for r in assigned}) == packet[digest_key]
