"""Controls for per-consumer balances at actual screen closure checkpoints."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from addressables_handle_lifecycle import verify_arm


def observation(arm):
    events, checkpoints = [], []

    def checkpoint(cycle, phase, loaded=False):
        checkpoints.append(
            {
                "cycle": cycle,
                "phase": phase,
                "scene_loaded": loaded,
                "event_count": len(events),
                "marker": "old-run" if loaded else None,
                "unity_allocated_bytes": None,
            }
        )

    def release(token):
        events.append({"Kind": "release", "Token": token})

    checkpoint(-1, "baseline")
    retained = None
    for index in range(4):
        tokens = []
        for consumer in ("First", "Second"):
            token = f"{len(events):032x}"
            events.append(
                {
                    "Kind": "acquire",
                    "Scheme": "addressables",
                    "Key": "marker-text",
                    "Token": token,
                    "OwnerPath": f"{arm}-{index}/{consumer}",
                }
            )
            tokens.append(token)
        checkpoint(index, "both_open", True)
        release(tokens[0])
        checkpoint(index, "first_closed", True)
        if arm == "omitted" and index == 1:
            retained = tokens[1]
        else:
            release(tokens[1])
        checkpoint(index, "screen_closed")
    if retained is not None:
        release(retained)
    checkpoint(3, "cleanup")
    return {"events": events, "checkpoints": checkpoints}


@pytest.mark.parametrize("arm", ["balanced", "omitted"])
def test_tracks_two_valid_owners_and_exact_missing_release_even_without_memory(arm):
    data = observation(arm)
    history = verify_arm(arm, data, "run")
    closed = [
        list(row["outstanding"].values()) for row in history if row["phase"] == "screen_closed"
    ]
    assert closed == (
        [[], [], [], []]
        if arm == "balanced"
        else [[], ["omitted-1/Second"], ["omitted-1/Second"], ["omitted-1/Second"]]
    )
    assert not history[-1]["outstanding"]


@pytest.mark.parametrize(
    "mutation",
    [
        "alias_token",
        "misattribute",
        "early_release",
        "replace_marker",
        "missing_cleanup",
        "fabricated_memory",
    ],
)
def test_rejects_counterfeit_ownership_or_lifecycle(mutation):
    data = observation("omitted")
    events, points = data["events"], data["checkpoints"]
    match mutation:
        case "alias_token":
            events[1]["Token"] = events[0]["Token"]
        case "misattribute":
            events[5]["OwnerPath"] = events[4]["OwnerPath"]
        case "early_release":
            points[4]["event_count"] += 1
        case "replace_marker":
            points[2]["marker"] = "new-run"
        case "missing_cleanup":
            points[-1]["event_count"] -= 1
        case "fabricated_memory":
            points[1]["unity_allocated_bytes"] = -1
    with pytest.raises(ValueError):
        verify_arm("omitted", data, "run")
