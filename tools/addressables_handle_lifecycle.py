"""Verify ownership checkpoints for the controlled Addressables screen fixture."""

import re


def verify_arm(arm, data, session):
    events = data["events"]
    points = data["checkpoints"]
    expected_points = [(-1, "baseline", False)]
    for cycle in range(4):
        expected_points.extend(
            [
                (cycle, "both_open", True),
                (cycle, "first_closed", True),
                (cycle, "screen_closed", False),
            ]
        )
    expected_points.append((3, "cleanup", False))
    if [(p["cycle"], p["phase"], p["scene_loaded"]) for p in points] != expected_points:
        raise ValueError("Missing required screen lifecycle checkpoints")
    owners, live = {}, {}
    cursor = 0
    history = []
    for point in points:
        cycle, phase, end = point["cycle"], point["phase"], point["event_count"]
        if type(end) is not int or not cursor <= end <= len(events):
            raise ValueError("Invalid handle acquisition/release sequence")
        for event in events[cursor:end]:
            token = event["Token"]
            if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
                raise ValueError("Missing native wrapper token")
            if event["Kind"] == "acquire":
                if (
                    token in owners
                    or event["Scheme"] != "addressables"
                    or event["Key"] != "marker-text"
                ):
                    raise ValueError("Duplicate acquisition or wrong resource")
                live[token] = owners[token] = event["OwnerPath"]
            elif event["Kind"] == "release" and token in live:
                del live[token]
            else:
                raise ValueError("Unmatched release or unknown observation")
        expected = []
        if phase in ("both_open", "first_closed"):
            expected.append(f"{arm}-{cycle}/Second")
            if phase == "both_open":
                expected.append(f"{arm}-{cycle}/First")
            if point["marker"] != "old-" + session:
                raise ValueError("The surviving screen consumer lost its actual marker")
        if (
            arm == "omitted"
            and phase != "cleanup"
            and (cycle > 1 or (cycle == 1 and phase == "screen_closed"))
        ):
            expected.append("omitted-1/Second")
        if sorted(live.values()) != sorted(expected):
            raise ValueError(f"Incorrect outstanding owner after {arm}/{cycle}/{phase}")
        allocated = point["unity_allocated_bytes"]
        if allocated is not None and (type(allocated) is not int or allocated <= 0):
            raise ValueError("Invalid Unity allocated memory sample")
        history.append(
            {
                "cycle": cycle,
                "phase": phase,
                "outstanding": dict(live),
                "unity_allocated_bytes": allocated,
            }
        )
        cursor = end
    all_consumers = {f"{arm}-{i}/{consumer}" for i in range(4) for consumer in ("First", "Second")}
    if cursor != len(events) or len(owners) != 8 or set(owners.values()) != all_consumers:
        raise ValueError("Missing consumer or unaccounted handle event")
    return history


def verify_lifecycle(receipt, session, build_guid, unity_version):
    if (
        receipt["session"],
        receipt["build_guid"],
        receipt["unity_version"],
        receipt["initialization"],
    ) != (session, build_guid, unity_version, "screen-handles"):
        raise ValueError("Handle evidence must come from the original installed Player")
    return {
        arm: verify_arm(arm, receipt["lifecycle"][arm], session) for arm in ("balanced", "omitted")
    }
