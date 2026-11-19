from concurrent.futures import ThreadPoolExecutor

import pytest

from tools.experiments.blind_tool_pair import PreparedPair, preparation_issues


def surface(arm):
    return {
        "model": "model",
        "effort": "medium",
        "status": "observed",
        "permissions": {"approval": "never", "sandbox": "readOnly", "digest": "grant"},
        "source": {"server_commit": arm},
        "competition_observed": True,
        "tool_schema_digest": arm,
        "competing_servers": {"docs": ["find"]},
        "competitor_digest": "stable",
        "project_verified": True,
        "namespace": "trial",
    }


def run(order, mutate=None, second_state="scene", competitors=None):
    pair = PreparedPair(order, 2, competitors)
    receipts = {}

    def arm(name):
        observed = surface(name)
        if mutate and name == "candidate":
            observed.update(mutate)
        metadata = {
            "model": "model",
            "effort": "medium",
            "approval_policy": "never",
            "editor_start_state": "scene",
            "host_binary_digest": "binary",
        }
        try:
            if pair.arrive(name, name + "-thread", observed, metadata):
                if receipt := pair.start(name, "scene" if name == order[0] else second_state):
                    receipts[name] = {"prepared_pair": receipt}
        finally:
            pair.finish(name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(arm, name) for name in reversed(order)]
        for future in futures:
            future.result()
    return pair, receipts


@pytest.mark.parametrize("order", [("baseline", "candidate"), ("candidate", "baseline")])
def test_both_hosts_must_be_ready_before_counterbalanced_delivery(order):
    pair, result = run(order, competitors=["docs"])
    assert list(pair.started) == list(order)
    assert preparation_issues(result["baseline"], result["candidate"]) == []
    assert result["baseline"]["prepared_pair"]["cohort"] == "fixed_competitors"
    result["baseline"]["prepared_pair"]["prompt_ns"] = 1
    assert preparation_issues(result["baseline"], result["candidate"])


@pytest.mark.parametrize(
    "change",
    [
        {"permissions": {"sandbox": "workspaceWrite", "approval": "never"}},
        {"competitor_digest": "different"},
        {"status": "incomplete"},
        {"source": {}},
        {"competition_observed": False},
        {"project_verified": False},
        {"model": "other"},
    ],
)
def test_mismatch_blocks_both_prompts(change):
    pair, result = run(("baseline", "candidate"), change)
    assert pair.reasons and not result


def test_drift_before_second_prompt_and_missing_peer():
    pair, result = run(("baseline", "candidate"), second_state="other")
    assert list(result) == ["baseline"] and "different:editor_before_delivery" in pair.reasons
    pair = PreparedPair(("baseline", "candidate"), 1)
    pair.finish("candidate")
    assert pair.reasons == ["unverified:candidate_preparation"]
    assert not preparation_issues({}, {}) == []


def test_extra_competitor_blocks_fixed_cohort():
    pair, result = run(("baseline", "candidate"), competitors=["other"])
    assert not result and "different:declared_competitors" in pair.reasons
