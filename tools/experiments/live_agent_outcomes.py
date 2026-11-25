"""Grade application outcomes from isolated native snapshots and actor-visible evidence.

The runner owns the inputs, including delivery status. Only the evaluator supplies a
review. A reviewer reads the original, the answer, the independent native observations
and the actual tool results. The reviewer must ignore tool-choice preferences and the
agent's own claims about what its tools returned. Indices are zero-based.
"""

from collections import Counter
from hashlib import sha256
import json


ACTIVE = {"current_scene", "short_active_scene", "short_current_scene"}
ACTIVATE = {"activate_scene", "activate_named_scene"}
EDITS = ACTIVATE | {
    "restore_clip_inheritance",
    "author_two_control_maps",
    "fill_surface_gap",
    "gate_sensor_trigger",
}

# Apply to the entire answer, including qualifications, negations, and extra claims.
# This rubric belongs to the grader. Actor prompts never receive its contents.
RUBRICS = {
    **dict.fromkeys(ACTIVE, "Identify the actual active scene unambiguously."),
    **dict.fromkeys(ACTIVATE, "Confirm the requested open scene is now the active scene."),
    "scene_list": "List every loaded scene, identify the active one, exclude unloaded scenes.",
    "selected_agent": (
        "Give the selected object's actual NavMeshAgent settings. Match field names to values "
        "for movement and agent dimensions, with no conflicting numbers or different agent."
    ),
    "agent_list": "List the complete set of objects with NavMeshAgent in the requested scene.",
    "installed_package": (
        "Report the requested installed package's registry details, including its identity, "
        "installed version and substantive metadata supported by the actual entry."
    ),
    "package_list": (
        "Report the installed inventory without extraneous or missing packages. For Japanese "
        "also match each installed package to its version. Check any voluntarily stated versions."
    ),
    "menu-path": (
        "Give the full declared Frame Debugger menu path. Check current declarations and "
        "leave the window closed. A catalog example or a similarly named menu is insufficient."
    ),
    "clip_platform_inheritance": (
        "Say whether the named clip inherits defaults or has a platform override. Compare "
        "the actual override flag for this platform, including negation; equal sample settings "
        "alone do not establish inheritance."
    ),
    "restore_clip_inheritance": "Confirm the named clip now inherits on the requested platform.",
    "saved_controls_identity": (
        "Give both the requested key binding's saved GUID and the checksum for that exact "
        "saved file version, distinguishing binding, action and map IDs."
    ),
    "author_two_control_maps": (
        "Confirm both requested empty maps were added to the existing input file together. "
        "Check a single import and preservation of existing actions, bindings and maps."
    ),
    "sample_surface_mask": (
        "Identify hole versus surface for the requested cells. Honor the exact question's "
        "direction and coordinate order, including implicit complements. Verify Unity hole "
        "mask polarity in the actual result."
    ),
    "fill_surface_gap": "Confirm surface restored at the requested coordinate only.",
    "lookout_fov": "Report the current vertical perspective field of view for Lookout in degrees.",
    "gate_sensor_trigger": "Confirm the existing GateSensor box collider is now a trigger.",
}


def _digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def _result(call):
    return call["result"] if call.get("result") is not None else call.get("excerpt")


def _discovery(call):
    name = call.get("name", "").split("__")[-1]
    return name.startswith("skill://") or name in {
        "search_tools",
        "search_unity_tools",
        "manage_tools",
        "list_tools",
        "list_resources",
        "list_resource_templates",
    }


def _eligible(call):
    result = _result(call)
    return (
        not _discovery(call)
        and call.get("success") is True
        and result is not None
        and not (isinstance(result, dict) and result.get("success") is False)
    )


def _review_key(row):
    fields = (
        "id",
        "task",
        "language",
        "query",
        "status",
        "answer",
        "calls",
        "host_calls",
        "before",
        "after",
    )
    return _digest({key: row.get(key) for key in fields})


def _approval_receipts(row):
    receipts = []
    for index, call in enumerate(row.get("host_calls", [])):
        error = str(call.get("error") or "").casefold()
        refused = "this action was rejected due to unacceptable risk." in error
        if refused or (
            "approval" in error
            and any(
                word in error
                for word in ("denied", "reject", "not allowed", "never", "unavailable", "required")
            )
        ):
            receipts.append({"index": index, "sha256": _digest(call)})
    return receipts


def review_packet(row):
    """Prepare a private, profile-blind packet. Do not publish raw answers or call bodies."""
    return {
        "evidence_sha256": _review_key(row),
        "original": row["query"],
        "answer": row.get("answer"),
        "rubric": RUBRICS[row["task"]],
        "before": row.get("before"),
        "after": row.get("after"),
        "failed_host_calls": [
            {"index": i, **{k: c.get(k) for k in ("server", "tool", "status", "error")}}
            for i, c in enumerate(row.get("host_calls", []))
            if c.get("error")
        ],
        "calls": [
            {
                "index": i,
                "eligible": _eligible(c),
                **{
                    k: c[k] for k in ("name", "arguments", "success", "result", "excerpt") if k in c
                },
            }
            for i, c in enumerate(row.get("calls", []))
            if not _discovery(c)
        ],
    }


def _review_verdict(row, eligible, key):
    review = row.get("review") or {}
    if not review:
        return "review", "answer_review", "semantic_review_required", []
    if review.get("evidence_sha256") != key:
        return "review", "answer_review", "stale_review", []
    checks = ("answer_correct", "evidence_supports", "constraints_respected")
    indices = review.get("call_indices", [])
    if (
        not isinstance(indices, list)
        or any(type(i) is not int or i not in eligible for i in indices)
        or len(set(indices)) != len(indices)
        or not isinstance(review.get("reason"), str)
        or not review["reason"].strip()
        or any(type(review.get(field)) is not bool for field in checks)
    ):
        return "review", "answer_review", "invalid_review", []
    if not review["constraints_respected"]:
        return "fail", "constraints", "review_found_constraint_violation", indices
    if not review["answer_correct"]:
        return "fail", "answer", "incorrect_or_incomplete_answer", indices
    if not review["evidence_supports"]:
        return "fail", "evidence", "claimed_facts_not_observed", indices
    if not indices:
        return "review", "answer_review", "missing_positive_receipt", []
    return "pass", "complete", "verified_by_review", indices


COMMON = {
    "activeScene",
    "loadedScenes",
    "sceneIdentities",
    "sceneCollateral",
    "assetCollateral",
    "selection",
    "frameDebuggerWindows",
}
REQUIRED = {
    **dict.fromkeys(ACTIVE | {"scene_list"}, set()),
    **dict.fromkeys(ACTIVATE, {"targetScene"}),
    "agent_list": {"agentNames"},
    "selected_agent": {"selectedAgent"},
    "package_list": {"packages"},
    "installed_package": {"targetPackage"},
    "menu-path": {"menuPaths"},
    **dict.fromkeys(
        {"clip_platform_inheritance", "restore_clip_inheritance"},
        {"audioPath", "audioPlatform", "audioOverride", "audioCollateral"},
    ),
    "saved_controls_identity": {"inputPath", "fileHash", "bindingId"},
    "author_two_control_maps": {
        "inputPath",
        "inputMaps",
        "targetMaps",
        "fileHash",
        "importCount",
        "inputCollateral",
    },
    **dict.fromkeys(
        {"sample_surface_mask", "fill_surface_gap"}, {"surfaceSamples", "terrainCollateral"}
    ),
    "lookout_fov": {"cameraFov", "cameraOrthographic"},
    "gate_sensor_trigger": {"boxTrigger", "boxSize", "boxCenter", "boxEnabled", "boxId"},
}
EDITABLE = {
    **dict.fromkeys(ACTIVATE, {"activeScene"}),
    "restore_clip_inheritance": {"audioOverride"},
    "author_two_control_maps": {"inputMaps", "fileHash", "importCount"},
    "fill_surface_gap": {"surfaceSamples"},
    "gate_sensor_trigger": {"boxTrigger"},
}
SURFACE_CELL = {
    "lv": (18, 24),
    "sk": (33, 21),
    "ur": (17, 22),
    "te": (23, 19),
    "ca": (35, 42),
    "nb": (26, 32),
}
SAMPLED_CELLS = {
    "lv": {(18, 24), (19, 24)},
    "sk": {(40, 36), (41, 36), (40, 37), (41, 37)},
    "ur": {(27, 18), (28, 18)},
    "te": {(31, 26), (32, 26), (31, 27), (32, 27)},
    "ca": {(14, 28), (15, 28)},
    "nb": {(20, 30), (21, 30), (22, 30)},
}
SCENE_TARGETS = {
    ("activate_scene", "ja"): "朝霧の埠頭",
    ("activate_scene", "es"): "Cantera",
    ("activate_named_scene", "ja"): "潮だまり",
    ("activate_named_scene", "es"): "Estación del Norte",
}
MAP_TARGETS = {
    "lv": ["Laiva", "Krasts"],
    "sk": ["Pešo", "Vozidlo"],
    "ur": ["کشتی", "ساحل"],
    "te": ["నడక", "ఈత"],
    "ca": ["Rem", "Vela"],
    "nb": ["Kjøring", "Gange"],
}


def _samples(state):
    samples = {(p["x"], p["y"]): p["surface"] for p in state["surfaceSamples"]}
    if len(samples) != len(state["surfaceSamples"]) or any(
        type(v) is not bool for v in samples.values()
    ):
        raise ValueError("Malformed mask observations")
    return samples


def _registry_record(state, language):
    package = state.get("targetPackage") or {}
    record = state.get("registryRecord") or {}
    requested = {"ko": "com.unity.cinemachine", "ja": "com.unity.inputsystem"}.get(language)
    if not isinstance(package, dict) or not isinstance(record, dict):
        return False
    digests = [record.get(field) for field in ("metadataSha256", "tarballSha256")]
    return (
        package.get("source") == "Registry"
        and requested is not None
        and package.get("name") == record.get("name") == requested
        and bool(package.get("version"))
        and package["version"] == record.get("version")
        and all(record.get(k) for k in ("displayName", "description", "registry"))
        and all(
            isinstance(digest, str)
            and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
            for digest in digests
        )
    )


def _valid_baseline(task, language, before):
    if (
        not before["sceneCollateral"]
        or not before["assetCollateral"]
        or not before["loadedScenes"]
        or before["activeScene"] not in before["loadedScenes"]
    ):
        return False
    if task in ACTIVATE:
        target = SCENE_TARGETS.get((task, language))
        return (
            target == before["targetScene"]
            and target in before["loadedScenes"]
            and before["activeScene"] != target
        )
    if task == "author_two_control_maps":
        names = MAP_TARGETS[language]
        return (
            set(before["targetMaps"]) == set(names)
            and len(before["targetMaps"]) == 2
            and set(names).isdisjoint(before["inputMaps"])
            and bool(before["inputMaps"])
            and bool(before["fileHash"])
            and type(before["importCount"]) is int
            and bool(before["inputCollateral"])
        )
    if task in {"clip_platform_inheritance", "restore_clip_inheritance"}:
        read = task == "clip_platform_inheritance"
        android = (language in {"lv", "ur", "te", "nb"}) == read
        platform = str(before["audioPlatform"]).casefold()
        return (
            platform in ({"android"} if android else {"ios", "iphone"})
            and type(before["audioOverride"]) is bool
            and (read or before["audioOverride"])
            and bool(before["audioPath"])
            and bool(before["audioCollateral"])
        )
    if task == "fill_surface_gap":
        return _samples(before).get(SURFACE_CELL[language]) is False and bool(
            before["terrainCollateral"]
        )
    if task == "sample_surface_mask":
        return SAMPLED_CELLS[language] <= _samples(before).keys() and bool(
            before["terrainCollateral"]
        )
    if task == "gate_sensor_trigger":
        return before["boxTrigger"] is False and bool(before["boxId"])
    if task == "lookout_fov":
        return before["cameraOrthographic"] is False and 0 < before["cameraFov"] < 180
    if task == "menu-path":
        return bool(before["menuPaths"]) and before["frameDebuggerWindows"] == 0
    if task == "selected_agent":
        return bool(before["selection"]) and before["selectedAgent"]["name"] == before["selection"]
    if task == "package_list":
        return bool(before["packages"])
    if task == "saved_controls_identity":
        return bool(before["inputPath"] and before["bindingId"] and before["fileHash"])
    return True


def _native_change(task, language, before, after):
    """Return a failure category, or None when the legal transition was observed."""
    if task in ACTIVATE:
        achieved = after["activeScene"] == before["targetScene"]
    elif task == "restore_clip_inheritance":
        achieved = after["audioOverride"] is False
    elif task == "gate_sensor_trigger":
        achieved = after["boxTrigger"] is True
    elif task == "fill_surface_gap":
        expected = _samples(before)
        target = SURFACE_CELL[language]
        expected[target] = True
        actual = _samples(after)
        if {k: v for k, v in actual.items() if k != target} != {
            k: v for k, v in expected.items() if k != target
        }:
            return "collateral_change"
        achieved = actual == expected
    elif task == "author_two_control_maps":
        old, new = before["inputMaps"], after["inputMaps"]
        names = set(before["targetMaps"])
        if (
            any(new.get(name) != data for name, data in old.items())
            or set(new) - old.keys() - names
        ):
            return "collateral_change"
        if set(new) != old.keys() | names:
            return "native_effect_missing"
        if any(new[name]["actions"] != [] or new[name]["bindings"] != [] for name in names):
            return "maps_not_empty"
        ids = [m["id"] for m in new.values()]
        if not all(ids) or len(set(ids)) != len(ids):
            return "invalid_map_identity"
        if after["importCount"] - before["importCount"] != 1:
            return "single_import_not_observed"
        achieved = after["fileHash"] != before["fileHash"]
    else:
        return None
    return None if achieved else "native_effect_missing"


def _target_reached(task, language, before, after):
    """Measure the requested field or object independently of preservation and import count."""
    if task in ACTIVATE:
        return after.get("activeScene") == before["targetScene"]
    if task == "restore_clip_inheritance":
        return after.get("audioOverride") is False
    if task == "gate_sensor_trigger":
        return after.get("boxTrigger") is True and after.get("boxId") == before["boxId"]
    if task == "fill_surface_gap":
        return _samples(after).get(SURFACE_CELL[language])
    if task == "author_two_control_maps":
        maps = after["inputMaps"]
        return all(
            name in maps and maps[name].get("actions") == [] and maps[name].get("bindings") == []
            for name in before["targetMaps"]
        )
    return None


def _active_receipt(call, scene):
    result = _result(call)
    if not isinstance(result, dict) or result.get("success") is False:
        return False
    data = result.get("data", result)
    if not isinstance(data, dict):
        return False
    name = call["name"].split("__")[-1]
    action = call.get("arguments", {}).get("action")
    if name in {"scene_inspect", "manage_scene"}:
        if action == "get_active":
            return data.get("name") == scene and data.get("isLoaded") is True
        if action == "get_loaded_scenes":
            return [
                item.get("name")
                for item in data.get("scenes", [])
                if item.get("isActive") is True and item.get("isLoaded") is True
            ] == [scene]
    return False


def _state_verdict(row):
    task = row["task"]
    before, after = row.get("before"), row.get("after")
    if (row.get("availability") or {}).get("ready") is not True:
        return "unobserved", "precondition", "fixture_unavailable"
    if task not in REQUIRED or not isinstance(before, dict) or not isinstance(after, dict):
        return "unobserved", "harness", "missing_snapshot"
    required = REQUIRED[task] | COMMON
    if not required <= before.keys() or not COMMON <= after.keys():
        return "unobserved", "harness", "missing_native_field"
    if task == "installed_package" and not _registry_record(before, row["language"]):
        return "unobserved", "precondition", "registry_record_unavailable"
    try:
        if not _valid_baseline(task, row["language"], before):
            return "unobserved", "precondition", "invalid_starting_state"
        if task == "gate_sensor_trigger":
            # The v1 scene digest included the authorized trigger change and omitted
            # the runtime shared-material reference. Its preservation result is unusable.
            needed = {"boxSharedMaterialId", "boxSharedMaterial"}
            if any(
                state.get("gateObserverVersion") != 2 or not needed <= state.keys()
                for state in (before, after)
            ):
                return "unobserved", "harness", "incomplete_gate_observer"
        changed = {k for k in before.keys() & after.keys() if before[k] != after[k]}
        if changed - EDITABLE.get(task, set()):
            return "fail", "constraints", "collateral_change"
        if before.keys() != after.keys():
            return "unobserved", "harness", "inconsistent_snapshot_fields"
        change = _native_change(task, row["language"], before, after)
    except (KeyError, TypeError, ValueError):
        return "unobserved", "harness", "malformed_native_field"
    if change == "collateral_change":
        return "fail", "constraints", "collateral_change"
    if change:
        return "fail", "execution", change
    return None


def score(row):
    """Grade one original. target_reached measures the edit itself; native_effect also
    requires preservation and other requested native constraints (e.g. a single import).
    Null means the native edit measure was unavailable or the original was a read.
    """
    task = row["task"]
    calls = row.get("calls", [])
    eligible = [i for i, call in enumerate(calls) if _eligible(call)]
    key = _review_key(row)
    native_effect = None
    target_reached = None
    state_result = _state_verdict(row)
    if row.get("delivered") is True and task in EDITS:
        if not state_result or state_result[0] != "unobserved":
            native_effect = state_result is None
            try:
                target_reached = _target_reached(task, row["language"], row["before"], row["after"])
            except (KeyError, TypeError, ValueError):
                pass
        elif (
            state_result[2] == "incomplete_gate_observer"
            and {"boxId", "boxTrigger"} <= row["after"].keys()
        ):
            target_reached = _target_reached(task, row["language"], row["before"], row["after"])

    def finish(outcome, stage, reason, indices=()):
        # Set by the evaluator only when the host independently confirms a denial
        # that prevented this outcome. Agent prose alone cannot establish the cause.
        if (
            row.get("stage") == "approval"
            and outcome == "fail"
            and stage in {"answer", "evidence", "execution", "discovery"}
        ):
            stage = "approval"
        return {
            **{k: row[k] for k in ("id", "profile", "panel", "task", "language")},
            "delivered": row.get("delivered") is True,
            "observed": outcome in {"pass", "fail", "review"},
            "outcome": outcome,
            "stage": stage,
            "reason": reason,
            "native_effect": native_effect,
            "target_reached": target_reached,
            "evidence_sha256": key,
            "answer_sha256": _digest(row.get("answer")),
            "positive_receipts": [{"index": i, "sha256": _digest(calls[i])} for i in indices],
            "host_approval_receipts": _approval_receipts(row),
            "review_sha256": _digest(row["review"]) if row.get("review") else None,
        }

    status = row.get("status")
    if status != "completed" or row.get("delivered") is not True:
        stage = {
            "model_error": "model",
            "host_error": "harness",
            "timeout": "timeout",
            "precondition": "precondition",
            "not_started": "delivery",
        }.get(status, "delivery")
        if row.get("stage") in {
            "precondition",
            "harness",
            "model",
            "approval",
            "delivery",
            "timeout",
        }:
            stage = row["stage"]
        attempted = row.get("delivered") is True and (
            any(not _discovery(call) for call in calls) or bool(row.get("answer"))
        )
        return finish("incomplete" if attempted else "unobserved", stage, "turn_not_completed")
    if state_result and state_result[:2] != ("fail", "execution"):
        return finish(*state_result)
    if not eligible:
        business = [call for call in calls if not _discovery(call)]
        if calls and not business:
            return finish("fail", "discovery", "no_native_dispatch")
        if any(call.get("success") is None for call in business):
            return finish("unobserved", "harness", "missing_wire_reply")
        if any(call.get("success") is False for call in business):
            return finish("fail", "execution", "no_successful_native_receipt")
        return finish("fail", "evidence", "no_native_receipt")
    if state_result:
        return finish(*state_result)
    answer = row.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return finish("fail", "answer", "missing_answer")
    if task in ACTIVE:
        scene = row["after"]["activeScene"]
        if answer.strip() == scene and len([c for c in calls if not _discovery(c)]) == 1:
            for i in eligible:
                if _active_receipt(calls[i], scene):
                    return finish("pass", "complete", "exact_active_scene", [i])
    return finish(*_review_verdict(row, eligible, key))


def aggregate(rows):
    """Summarize raw scheduled rows, keeping unresolved and undelivered cases visible."""
    cells = {}
    exceptions = []
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise ValueError(f"Duplicate case ID {row['id']}")
        seen.add(row["id"])
        result = score(row)
        cell = cells.setdefault(row["profile"], {}).setdefault(row["panel"], Counter())
        cell["scheduled"] += 1
        cell["delivered"] += result["delivered"]
        cell["observed"] += result["observed"]
        cell["resolved"] += result["outcome"] in {"pass", "fail"}
        cell["passed"] += result["outcome"] == "pass"
        cell["failed"] += result["outcome"] == "fail"
        cell["review"] += result["outcome"] == "review"
        cell["attempted_incomplete"] += result["outcome"] == "incomplete"
        cell["unobserved"] += result["outcome"] == "unobserved"
        cell["native_effects_measured"] += result["native_effect"] is not None
        cell["native_effects_verified"] += result["native_effect"] is True
        cell["targets_measured"] += result["target_reached"] is not None
        cell["targets_reached"] += result["target_reached"] is True
        if result["outcome"] != "pass":
            exceptions.append(result)
    for panels in cells.values():
        for cell in panels.values():
            cell["pass_fraction_of_resolved"] = (
                cell["passed"] / cell["resolved"] if cell["resolved"] else None
            )
    return {"by_profile_panel": cells, "exceptions": exceptions}
