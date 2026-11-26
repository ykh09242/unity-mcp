"""Verify paired, single-attempt native edit evidence without an Editor or model client."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.verify_agent_outcome_holdout import canonical, digest, require, summary
from tools.experiments.verify_agent_outcome_holdout_confirmation import (
    CORRECTION_PINS,
    PROFILES,
    STRICT_ZERO,
    get_catalogs,
    transitions,
    verify_panel,
)

SEAL = "b276cd63779999819cd94720762f90efaedd6733607b5d4fd4119d9ff83acc30"
PROJECTION = "8c8f87c8e9621e731a8f594cbd821c12f64c96f0c613e8a7cc430ac9e1482966"
BASE = CORRECTION_PINS | {"package": "02cf9924bb49b923a6c1a8a371d3b302eedbafe6"}
CANDIDATE = {
    "package": "46cee3cd3547ec980226e30fcfbf9395f306bda3",
    "runtime": "d14f29070e812854a9f518da3b46d94c46ae604e",
    "skill": "bd207b749f1593fb657171ee62eb3dcc2e662e7a",
}
REFERENCE_PREFERENCES = "5ab447bb3881745f00a37d8290018c33a1d238d17391edd6bbcd99c9267b6ffb"
HISTORICAL = "274f9520df149838e5960f6198400eacc9b408177f13845ba0c11be41d09b330"
PUBLISHED = "a129b88b8d2c5dea4df0f8029d36518d2932706e8ddfda8315f3255e8a5f7e58"


def read(path):
    return json.loads(path.read_bytes())


def saved_queue(source):
    match = list(re.finditer(rb"(?m)^  m_CustomRenderQueue: (-?\d+)\r?$", source))
    require(len(match) == 1, "Serialized queue field missing or ambiguous")
    field = match[0]
    return {
        "value": int(field[1]),
        "sha256": digest(source),
        "other_bytes_sha256": digest(
            source[: field.start(1)] + b"<target>" + source[field.end(1) :]
        ),
    }


def public_binding(name):
    if not isinstance(name, str):
        return "unobserved_binding"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    try:
        uri = urlsplit(name)
    except ValueError:
        return "redacted_binding"
    if uri.scheme != "mcpforunity" or not re.fullmatch(r"[\w-]+", uri.netloc):
        return "redacted_binding"
    segments = [
        segment if re.fullmatch(r"[A-Za-z][\w-]{0,48}", segment) else "{id}"
        for segment in uri.path.split("/")
        if segment
    ]
    return "/".join([f"mcpforunity://{uri.netloc}", *segments])


def verify_wire(attempt, row):
    calls = []
    for path in attempt.glob("wire-*.jsonl"):
        pending = {}
        for line in path.read_bytes().splitlines():
            event = json.loads(line)
            kind = event["event"]
            if kind == "request":
                observation = {
                    "time": event["time_ns"],
                    "binding": public_binding(event["name"]),
                    "raw_call": {
                        "time_ns": event["time_ns"],
                        "name": event["name"],
                        "arguments": event["arguments"],
                        "success": None,
                        "result": None,
                    },
                    "method": "resources/read" if "://" in event["name"] else "tools/call",
                    "reply": "missing",
                    "mcp_error": False,
                    "domain_error": False,
                }
                pending[event["id"]] = observation
                calls.append(observation)
            elif kind == "reply_layers":
                observation = pending[event["id"]]
                observation["mcp_error"] = event["rpc_error"] or event["mcp_is_error"]
                observation["domain_error"] = event["domain_error"]
            elif kind == "reply":
                observation = pending.pop(event["id"])
                observation["reply"] = "success" if event["success"] else "failure"
                observation["raw_call"].update(success=event["success"], result=event["result"])
    ordered = sorted(calls, key=lambda call: call["time"])
    require(len(ordered) == len(row["calls"]), "Wire request count differs")
    for observed, published in zip(ordered, row["calls"], strict=True):
        require(
            all(
                observed[key] == published[key]
                for key in ("binding", "method", "reply", "mcp_error", "domain_error")
            )
            and canonical(observed["raw_call"]) == published["sha256"],
            "Wire binding, original call, reply status, or protocol error layer differs",
        )


def expected_schedule(specs, panel):
    expected = {}
    for index, spec in enumerate(item for item in specs if item["panel"] == panel):
        original = spec["original_sha256"]
        for profile in PROFILES if index % 2 == 0 else reversed(PROFILES):
            identity = digest((profile + "\0" + original).encode("ascii"))[:16]
            expected[identity] = (spec, profile, original)
    require(len(expected) == 12, "Expected six distinct originals in both catalogs")
    return expected


def historical_comparison(path, specs):
    require(digest(path.read_bytes()) == HISTORICAL, "Archived comparison changed")
    archive = read(path)
    expected = expected_schedule(specs, "old")
    rows = [
        row
        for group in archive["panels"].values()
        for row in group["cases"]
        if row["effect"] == "edit"
    ]
    require(
        {row["id"] for row in rows} == expected.keys()
        and all(row["original_sha256"] == expected[row["id"]][2] for row in rows),
        "Historical edit originals differ",
    )
    return {"sha256": HISTORICAL, "product_pins": archive["product_pins"], "summary": summary(rows)}


def verify_reviewed_verdict(row, catalog):
    review = row["review"]
    require(
        row["host_final_correct"] is review["answer_accurate"]
        and row["fingerprint"] == review["fingerprint"],
        "Published answer or review fingerprint differs from the adjudication",
    )
    for call in row["calls"]:
        if call["method"] == "tools/call":
            require(
                "unityMCP/" + call["binding"] in row["host_routes"],
                "Tool binding absent from the recorded host routes",
            )
    evidence = review["evidence"]
    indices = [item["index"] for item in evidence]
    require(
        len(set(indices)) == len(indices)
        and all(
            type(item["index"]) is int
            and 0 <= item["index"] < len(row["calls"])
            and row["calls"][item["index"]]["reply"] == "success"
            and row["calls"][item["index"]]["sha256"] == item["sha256"]
            for item in evidence
        ),
        "Review evidence differs from the successful call ledger",
    )
    # Each scheduled slot in this fixed historical report reached the model and returned
    # native observations and a final answer. Apply the frozen grading order to every row.
    require(
        row["prompt_reached_model"] and row["host_final_present"] and row["observed_native_state"],
        "Missing observed historical outcome",
    )
    if not row["observed_target_matches"]:
        verdict = "incomplete_native_target"
    elif row["protected_changes"] or row["persisted_scope_valid"] is False:
        verdict = "incomplete_native_preservation"
    elif not review["constraints_respected"]:
        verdict = "incomplete_constraints"
    elif row["persisted_scope_valid"] is None:
        verdict = "unverifiable_persistence"
    elif not review["answer_accurate"]:
        verdict = "incomplete_answer"
    elif not evidence:
        verdict = "incomplete_evidence"
    elif (
        row["actor_first_tools_list_complete"] is not True
        or row["actor_first_list_delta"] is None
        or row["actor_first_tools_list_sha256"] != catalog["tools_sha256"]
    ):
        verdict = "unverifiable_initial_catalog"
    else:
        verdict = "completed"
    require(row["verdict"] == verdict, f"Verdict differs from observed evidence for {row['id']}")


def verify_run(run, specs):
    require(run["owned_editor_stopped"] and run["passed_native_controls"] == 12, "Native controls")
    require(
        run["local_skill_bundle"]["revision"] == run["product_pins"]["skill"],
        "The source of agent-visible skill resources differs from the package pin",
    )
    prefs = run["shared_preferences"]
    require(
        prefs["before_sha256"] == prefs["after_sha256"] == REFERENCE_PREFERENCES
        and prefs["count"] == 130
        and prefs["private_scope"]["initialized"] == prefs["private_scope"]["inherited"] == 130
        and prefs["private_scope"]["selected"] == ["ui_source"],
        "Preference controls differ from the matched native trial",
    )
    catalogs = get_catalogs(run)
    require(
        catalogs["core"]["signatures"].keys() < catalogs["full"]["signatures"].keys(),
        "The compact catalog must be a strict subset",
    )
    for name, catalog in catalogs.items():
        tools = sorted(catalog["signatures"])
        require(
            set(tools) == catalog["input_schemas"].keys()
            and all(
                len(sha) == 64
                for sha in (*catalog["signatures"].values(), *catalog["input_schemas"].values())
            )
            and len(tools) == catalog["counts"]["tools"]
            and canonical({"tools": tools}) == catalog["tools_sha256"],
            f"Names differ from the observed {name} inventory",
        )
    baseline = run["product_pins"] == BASE
    if baseline:
        require(
            [len(catalogs[name]["signatures"]) for name in PROFILES] == [31, 91],
            "Shipping baseline tool inventories changed",
        )
    for label, panel in run["panels"].items():
        expected = expected_schedule(specs, label)
        require(list(expected) == [row["id"] for row in panel["cases"]], "Paired order changed")
        verify_panel(panel, expected, catalogs)
        for profile in PROFILES:
            rows = [row for row in panel["cases"] if row["profile"] == profile]
            edits = {
                row["id"]
                for row in rows
                if any(
                    change["status"] == "completed" for change in row.get("host_file_changes", [])
                )
            }
            require(
                panel["host_route_summary"][profile]
                == {
                    "completed_host_file_change_slots": len(edits),
                    "native_targets_without_host_file_change": sum(
                        row["observed_target_matches"] and row["id"] not in edits for row in rows
                    ),
                },
                "Host-side edits differ from native targets",
            )
        for row in panel["cases"]:
            verify_reviewed_verdict(row, catalogs[row["profile"]])
            delta = row.get("actor_first_list_delta")
            if delta:
                expected_names = set(catalogs[row["profile"]]["signatures"])
                actual_names = (expected_names - set(delta["absent"])) | set(delta["added"])
                require(
                    len(actual_names) == delta["count"]
                    and canonical({"tools": sorted(actual_names)})
                    == row["actor_first_tools_list_sha256"],
                    "First host-visible tool list differs from the published inventory delta",
                )
            if row["verdict"] == "completed":
                require(
                    not row["other_integrations"]
                    and not (
                        set(row["host_item_types"])
                        - {"agent_message", "mcp_tool_call", "reasoning", "todo_list"}
                    )
                    and all(
                        route.startswith("unityMCP/")
                        or route
                        in {
                            "codex/list_mcp_resources",
                            "codex/list_mcp_resource_templates",
                            "codex/read_mcp_resource",
                        }
                        for route in row["host_routes"]
                    ),
                    "Successful verdict used an unverified route",
                )
            if row["observed_native_state"]:
                case = expected[row["id"]][0]
                require(
                    row["observed_target_matches"]
                    == (
                        row["before_facts"] == case["before"]
                        and row["after_facts"] == case["after"]
                    ),
                    "Native target verdict disagrees with independent reads",
                )


def verify(data, candidate):
    require(
        data["schema"] == 1
        and data["preseal_sha256"] == SEAL
        and canonical(data["specs"]) == PROJECTION,
        "Preregistered native edit projection differs",
    )
    runs = data["runs"]
    require(set(runs) == {"baseline", "candidate"}, "Missing revision")
    require(runs["baseline"]["product_pins"] == BASE, "Baseline differs from the freeze")
    require(runs["candidate"]["product_pins"] == candidate, "Candidate differs from the freeze")
    for run in runs.values():
        require(set(run["panels"]) == {"old", "new"}, "Missing cohort")
        verify_run(run, data["specs"])
    for key in ("actor", "unity_editor", "native_observer_sha256"):
        require(runs["candidate"][key] == runs["baseline"][key], f"Unmatched {key}")
    require(
        runs["candidate"]["shared_preferences"] == runs["baseline"]["shared_preferences"],
        "Native permission scopes differ",
    )
    spec = next(item for item in data["specs"] if item["task"] == "n02")
    supplemental = runs["candidate"]["supplemental_saved_material"]
    require(not runs["baseline"]["supplemental_saved_material"], "Unexpected baseline disk copy")
    require(
        [item["id"] for item in supplemental]
        == [
            digest((profile + "\0" + spec["original_sha256"]).encode())[:16]
            for profile in reversed(PROFILES)
        ],
        "Supplemental byte captures differ from the two original candidate slots",
    )
    for disk in supplemental:
        before, after = disk["before"], disk["after"]
        expected = (
            None
            if not (before and after)
            else (
                before["value"] == spec["before"]["queue"]
                and after["value"] == spec["after"]["queue"]
                and before["other_bytes_sha256"] == after["other_bytes_sha256"]
            )
        )
        require(disk["saved_target_and_neighbors_match"] == expected, "Supplemental bytes verdict")
    for label, result in data["paired_transitions"].items():
        require(
            result
            == transitions(
                runs["baseline"]["panels"][label]["cases"],
                runs["candidate"]["panels"][label]["cases"],
            ),
            "Paired transitions differ",
        )
    require(set(data["paired_transitions"]) == {"old", "new"}, "Missing matched transition group")
    require(canonical(data) == PUBLISHED, "Frozen public study payload differs")


def verify_native_receipt(row, raw, sealed, spec):
    for phase in ("before", "after"):
        if row["observed_native_state"]:
            require(
                canonical(raw[phase]) == row[phase + "_sha256"]
                and raw[phase]["facts"] == row[phase + "_facts"]
                and all(raw[phase][k] == v for k, v in row[phase + "_persistence"].items()),
                "Native state differs",
            )
    if row["observed_native_state"]:
        before, after = raw["before"], raw["after"]
        rule = sealed["scene_save_rules"].get(raw["task"], sealed["scene_save_rules"]["others"])
        exempt = {"facts"}
        valid = True
        if rule.startswith(("original optional", "required save")):
            exempt.update(("savedSortingOrder", "sceneDirty"))
            valid = (
                not before["sceneDirty"]
                and before["savedSortingOrder"] == before["facts"]["sortingOrder"]
            )
            target = spec["after"]["sortingOrder"]
            if rule.startswith("required save"):
                valid = valid and not after["sceneDirty"] and after["savedSortingOrder"] == target
            else:
                valid = valid and after["savedSortingOrder"] in (
                    before["savedSortingOrder"],
                    target,
                )
        elif rule.startswith("forbidden save"):
            exempt.add("sceneDirty")
            valid = not before["sceneDirty"] and after["sceneDirty"]
        elif raw["task"] == "n02":
            # Native readback observes the loaded material; its permitted persisted
            # field was masked in the protected-asset comparison on both revisions.
            valid = None
        require(
            valid == row["persisted_scope_valid"]
            and sorted(key for key in before.keys() - exempt if before[key] != after[key])
            == row["protected_changes"],
            "Private persistence or protected-state comparison differs",
        )


def verify_host_receipts(row, folder, work):
    attempt = folder / "attempt-1"
    verify_wire(attempt, row)
    host_events = attempt / "host.events.jsonl"
    if host_events.is_file():
        events = [json.loads(line) for line in host_events.read_bytes().splitlines()]
        event_types = Counter(event["type"] for event in events)
        items = [event["item"] for event in events if event["type"] == "item.completed"]
        file_events = [item for item in items if item["type"] == "file_change"]
        require(
            row["host_file_changes"]
            == [
                {
                    "status": item.get("status"),
                    "paths": len(item.get("changes", [])),
                    "all_paths_owned": all(
                        Path(change["path"])
                        .resolve()
                        .is_relative_to((work / "lane-0/project").resolve())
                        for change in item.get("changes", [])
                    ),
                }
                for item in file_events
            ],
            "Host file-change dispositions differ",
        )
        require(
            row["model_turns_started"] == event_types["turn.started"]
            and row["model_turns_completed"] == event_types["turn.completed"]
            and row["catalog_child_turns_started"] == event_types["unity.catalog.turn.started"]
            and row["host_item_types"] == dict(Counter(item["type"] for item in items)),
            "Ordinary host turns or item types differ from the original event stream",
        )
    files = [
        *(
            attempt / name
            for name in (
                "initial-catalog-readiness.json",
                "host.events.jsonl",
                "host.stderr",
            )
        ),
        *attempt.glob("wire-*.jsonl"),
    ]
    require(
        sorted(digest(path.read_bytes()) for path in files if path.is_file())
        == row["private_artifact_sha256"],
        "Catalog, model host or MCP journal changed",
    )


def verify_private_row(work, panel, row, review, entries, sealed, spec):
    folder = work / ("receipts-" + panel) / row["id"]
    raw_path = folder / "row.json"
    if not raw_path.is_file():
        require(row["verdict"] == "missing_receipt", "Unreported missing receipt")
        return 0
    require(digest(raw_path.read_bytes()) == row["receipt_sha256"], "Raw receipt hash")
    skill_reads = 0
    raw = read(raw_path)
    first = next(
        (entry for entry in raw.get("inventory", []) if entry.get("surface") == "tools/list"),
        None,
    )
    if first:
        require(
            canonical({"tools": sorted(first["names"])}) == row["actor_first_tools_list_sha256"]
            and (first["more"] is False) == row["actor_first_tools_list_complete"],
            "Actor first list differs from the original receipt",
        )
    for call in raw["calls"]:
        prefix = "skill://unity-mcp-orchestrator/"
        if call["success"] and call["name"].startswith(prefix):
            require(
                "sha256:" + digest(call["result"].encode())
                == entries[call["name"].removeprefix(prefix)],
                "Actor-visible resource differs from the pinned packaged skill",
            )
            skill_reads += 1
    require(
        row["host_item_types"] == raw.get("host_items", {})
        and row["other_integrations"] == raw.get("other_integrations", [])
        and row["host_routes"]
        == sorted(
            {str(call["server"]) + "/" + str(call["tool"]) for call in raw.get("host_calls", [])}
        ),
        "Host execution route differs",
    )
    require(digest(raw["query"].encode()) == row["original_sha256"], "Actor original")
    require(digest(raw["answer"].encode()) == row["answer_sha256"], "Actor answer")
    fingerprint_fields = (
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
    require(
        canonical({key: raw.get(key) for key in fingerprint_fields}) == row["fingerprint"],
        "Review fingerprint differs from the original actor and Unity receipts",
    )
    require(
        review is not None and row["host_final_correct"] is review["answer_accurate"],
        "Answer verdict differs from the private review",
    )
    if review:
        expected = review | {
            "evidence": [
                {"index": i, "sha256": row["calls"][i]["sha256"]} for i in review["evidence"]
            ]
        }
        require(expected == row["review"], "Semantic review changed")
    require(
        [canonical(call) for call in raw["calls"]] == [c["sha256"] for c in row["calls"]],
        "Actor-observed public call ledger differs",
    )
    for public, call in zip(row["calls"], raw["calls"], strict=True):
        if "action" in public:
            require(public["action"] == call["arguments"]["action"], "Failed action differs")
        if "error_code" in public:
            require(public["error_code"] == call["result"]["code"], "Failed error code differs")
    verify_native_receipt(row, raw, sealed, spec)
    verify_host_receipts(row, folder, work)
    return skill_reads


def private_check(data, root):
    seal_file = root / "seal/cases.json"
    require(digest(seal_file.read_bytes()) == SEAL, "Private source manifest changed")
    sealed = read(seal_file)
    specs = [
        {key: case[key] for key in ("task", "panel", "effect", "before", "after")}
        | {"original_sha256": digest(case["query"].encode())}
        for case in sealed["cases"]
    ]
    require(specs == data["specs"], "Private requests or expected states differ from publication")
    require(
        data["languages"] == {case["task"]: case["language"] for case in sealed["cases"]},
        "Original language assignments differ",
    )
    for label, run in data["runs"].items():
        work = root / label
        if label == "candidate":
            for disk in run["supplemental_saved_material"]:
                folder = work / "persisted-readback" / disk["id"]
                raw = read(work / "receipts-new" / disk["id"] / "row.json")
                write = raw["calls"][disk["native_write"]["index"]]
                payload = write["result"]["data"]
                material = payload["material"]
                identity = disk["identity"]
                require(
                    write["success"]
                    and write["name"] == disk["native_write"]["binding"]
                    and write["arguments"]["action"] == disk["native_write"]["action"]
                    and write["result"]["message"] == disk["native_write"]["message"]
                    and payload["render_queue"] == disk["native_write"]["reported_queue"]
                    and material["persistent"]
                    == disk["native_write"]["material_is_persistent_asset"]
                    and ("saved" in payload) == disk["native_write"]["explicit_saved_flag"]
                    and material["asset_path"]
                    == write["arguments"]["material_path"]
                    == raw["target"]
                    and identity["matching_path_from_fixture_and_native_reply"]
                    and digest(material["asset_guid"].encode()) == identity["native_guid_sha256"]
                    and identity["protected_asset_digest_unchanged"]
                    == (raw["before"]["assetCollateral"] == raw["after"]["assetCollateral"]),
                    "Supplemental physical identity or material write receipt differs",
                )
                for phase in ("before", "after"):
                    path = folder / (phase + ".bin")
                    require(
                        (saved_queue(path.read_bytes()) if path.is_file() else None) == disk[phase],
                        "Supplemental serialized field differs from the read-only byte copy",
                    )
                    if path.is_file():
                        require(
                            re.findall(rb"(?m)^  m_Name: ([^\r\n]+)", path.read_bytes())
                            == [material["name"].encode()]
                            and identity["matching_yaml_name"],
                            "Serialized material identity differs",
                        )
        native = work / "native-preflight.json"
        require(
            digest(native.read_bytes()) == run["native_preflight_sha256"]
            and digest((work / "bin/BlindWorld.dll").read_bytes()) == run["native_observer_sha256"],
            "Native controls or observer changed",
        )
        signatures = read(work / "catalog-signatures.json")
        bundle_path = work / "product/Server/src/services/skills_bundle.json"
        bundle = read(bundle_path)["payload"]
        entries = {item["path"]: item["digest"] for item in bundle["files"]}
        skill_reads = 0
        require(
            digest(bundle_path.read_bytes()) == run["local_skill_bundle"]["sha256"]
            and bundle["revision"] == run["product_pins"]["skill"],
            "Packaged offline skills differ",
        )
        before_prefs = read(work / "shared-tool-preferences.json")
        cleanup = read(work / "cleanup.json")
        require(
            before_prefs["sha256"] == run["shared_preferences"]["before_sha256"]
            and cleanup["shared_prefs"] == run["shared_preferences"]["after_sha256"]
            and cleanup["owned_editor_stopped"] == run["owned_editor_stopped"],
            "Private preference or process cleanup receipt differs",
        )
        for profile, items in signatures.items():
            require(
                {t["name"]: canonical(t) for t in items} == run["catalogs"][profile]["signatures"]
                and {t["name"]: canonical(t["inputSchema"]) for t in items}
                == run["catalogs"][profile]["input_schemas"]
                and len(
                    json.dumps(
                        items, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                )
                == run["catalogs"][profile]["canonical_descriptors_utf8_bytes"],
                "Native-facing tool signatures differ",
            )
        for panel, group in run["panels"].items():
            reviews = read(work / ("reviews-" + panel + ".json"))
            for row in group["cases"]:
                spec = next(item for item in specs if item["task"] == row["task"])
                verify_reviewed_verdict(row, run["catalogs"][row["profile"]])
                skill_reads += verify_private_row(
                    work, panel, row, reviews.get(row["id"]), entries, sealed, spec
                )
        require(
            skill_reads == run["local_skill_bundle"]["confirmed_actor_reads"], "Skill read count"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    directory = Path(__file__).resolve().parent
    parser.add_argument(
        "report", type=Path, nargs="?", default=directory / "compact_edit_results.json"
    )
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    data = read(args.report)
    verify(data, CANDIDATE)
    require(
        historical_comparison(directory / "agent_outcome_holdout_confirmation.json", data["specs"])
        == data["historical_edits"],
        "Archived edit comparison differs",
    )
    if args.private_root:
        private_check(data, args.private_root)
    counts = {
        revision: {label: summary(panel["cases"]) for label, panel in run["panels"].items()}
        for revision, run in data["runs"].items()
    }
    print(json.dumps(counts, indent=2))
    if args.require_perfect and not all(
        item["scheduled"] == item["completed"] == 6 and all(item[k] == 0 for k in STRICT_ZERO)
        for panel in counts["candidate"].values()
        for item in panel.values()
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
