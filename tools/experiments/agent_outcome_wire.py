"""Verify the sanitized MCP replay report or rebuild it from reviewed private evidence."""

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from collections import Counter
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = HERE / "agent_outcome_wire_results.json"
SHIPPING = HERE / "catalog_outcome_shipping_results.json"
PROTOCOL = "2025-06-18"
BASELINE = "4f454080c0b95ae27b8510f74610200da81833cc"
TRANSPORTS = ("stdio", "http")
COUNTS = {"search_limit": 8, "search_query": 5, "inspection_page": 3}
CAPS = {"search_limit": ("limit", 20), "inspection_page": ("page_size", 64)}
ALIASES = ("search_unity_tools", "manage_tools")


def require(condition, label):
    if not condition:
        raise ValueError(f"Unexpected wire replay evidence: {label}")


def read(path):
    return json.loads(path.read_bytes())


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )


def file_sha256(path):
    return sha256(path.read_bytes())


def identity(call):
    return call["case"], call["call_index_zero_based"]


def index(calls):
    indexed = {identity(call): call for call in calls}
    require(len(indexed) == len(calls), "duplicate original call")
    return indexed


def count_calls(calls):
    require(Counter(call["category"] for call in calls) == COUNTS, "original call counts")


def verify_source(manifest, scope="Server/src"):
    """Bind the copied runtime source to one Git commit without changing Git state."""
    revision = manifest["base_git_revision"]
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "candidate revision")
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", revision, scope],
        cwd=HERE.parents[1],
    )
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tree:
        files = {
            entry.name: sha256(tree.extractfile(entry).read()) for entry in tree if entry.isfile()
        }
    require(
        manifest["files"] == files and manifest["tree_content_sha256"] == digest(files),
        "candidate executable source differs from its Git revision",
    )


def summarize_call(original, baseline, prepared, transport):
    key = identity(original)
    category = original["category"]
    expected_structured = category == "search_query"
    require(
        baseline["original_payload_equal"]
        and baseline["arguments_sha256"] == original["arguments_sha256"]
        and baseline["observed"]["is_error"]
        and baseline["observed"]["has_structured_content"] is expected_structured
        and digest(baseline["observed"]["result"]) == original["original_result_sha256"],
        f"{transport} baseline response {key}",
    )
    arguments = prepared["prepared_call"]["arguments"]
    require(
        prepared["raw_arguments_sha256"] == original["arguments_sha256"]
        and prepared["prepared_call"]["tool"] == original["operation"]
        and prepared["raw_call_reproduced"]
        and prepared["adjustment_count"] == 1,
        f"{transport} preparation {key}",
    )
    result = prepared["follow_up"]
    if category == "inspection_page":
        require(
            result["is_error"] and result["has_structured_content"], f"{transport} inspection {key}"
        )
        if transport == "stdio":
            require(result["data_reason"] == "no_unity_session", f"stdio inspection result {key}")
            follow_up = "no_unity_session"
        else:
            outgoing = [
                frame
                for frame in prepared["follow_up_native_wire"]
                if frame["command"] == "inspect_serialized_object_v1"
            ]
            require(len(outgoing) == 1, f"HTTP inspection command {key}")
            (frame,) = outgoing
            require(
                frame["target"] == original["arguments"]["unity_instance"]
                and frame["params"]
                == {
                    "instance_id": original["arguments"]["instance_id"],
                    "mode": original["arguments"].get("mode", "fields"),
                    "property_path": original["arguments"].get("property_path", ""),
                    "include_values": original["arguments"].get("include_values", True),
                    "page_size": 64,
                }
                and result["error"] == "binding-only replay fixture",
                f"HTTP inspection target/arguments {key}",
            )
            follow_up = "editor_command_observed_on_binding_fixture"
    else:
        require(
            not result["is_error"]
            and result["has_structured_content"]
            and result.get("success") is not False
            and prepared["follow_up_execute_frames"] == 0,
            f"{transport} search response {key}",
        )
        follow_up = "catalog_result"

    summary = {
        "direct_error": "handler_validation" if expected_structured else "input_validation",
        "prepared_arguments_sha256": prepared["prepared_arguments_sha256"],
        "follow_up": follow_up,
    }
    if category in CAPS:
        name, maximum = CAPS[category]
        require(arguments[name] == maximum, f"{transport} page bound {key}")
        summary["adjustment"] = {
            "field": name,
            "received": original["arguments"][name],
            "prepared": maximum,
        }
    else:
        archived_text = arguments["query"]["archived_request_sha256"]
        summary["adjustment"] = {"field": "query", "archived_request_sha256": archived_text}
    return summary


def summarize_native(path):
    observation = read(path)
    source = read(path.with_name("source-manifest.json"))
    fixture_path = path.with_name("fixture-source-manifest.json")
    fixture = read(fixture_path)
    verify_source(source)
    verify_source(fixture, "MCPForUnity")
    require(
        observation["protocol"] == PROTOCOL
        and observation["source_revision"] == source["base_git_revision"]
        and observation["source_tree_sha256"] == source["tree_content_sha256"]
        and observation["fixture_git_revision"] == fixture["fixture_baseline_git_revision"],
        "native source and protocol binding",
    )
    camera = observation["component_control"]
    rejected = observation["gameobject_control"]
    before = observation["components_inventory_before_sha256"]
    after = observation["components_inventory_after_sha256"]
    require(
        observation["transport"] in TRANSPORTS
        and camera["prepared_call"]["arguments"]["page_size"] == 64
        and camera["observation"]["success"] is True
        and not camera["observation"]["is_error"]
        and camera["data"]["target"]["type"] == "UnityEngine.Camera"
        and 0 < camera["data"]["returned"] == len(camera["data"]["records"]) <= 64
        and rejected["prepared_call"]["arguments"]["page_size"] == 64
        and rejected["observation"]["is_error"]
        and rejected["observation"]["code"] == "invalid_target"
        and before == after,
        "native Camera and GameObject controls",
    )
    return {
        "transport": observation["transport"],
        "server_git_revision": observation["source_revision"],
        "server_src_sha256": observation["source_tree_sha256"],
        "fixture_base_git_revision": observation["fixture_git_revision"],
        "fixture_plugin_git_revision": fixture["base_git_revision"],
        "fixture_plugin_tree_sha256": fixture["tree_content_sha256"],
        "fixture_source_receipt_sha256": file_sha256(fixture_path),
        "camera_type": camera["data"]["target"]["type"],
        "camera_records": camera["data"]["returned"],
        "gameobject_error": rejected["observation"]["code"],
        "component_inventory_sha256_before": before,
        "component_inventory_sha256_after": after,
        "artifact_sha256": file_sha256(path),
    }


def verify_node(node):
    require(
        node["client"]["name"] == "unity-mcp-node-wire-control"
        and node["runtime"].startswith("v")
        and node["transport"] == "stdio"
        and node["protocol"] == PROTOCOL
        and node["target"] == "isolated_reloading_status_fixture"
        and node["prepared_limit"] == 20
        and node["scoped_resource"] == "mcpforunity://capabilities"
        and node["target_selector_preserved"]
        and not node["server_initiated_requests"]
        and node["model_processes_launched"] == 0,
        "independent Node JSON-RPC control",
    )


def verify_package(package_revision, server_revision):
    require(re.fullmatch(r"[0-9a-f]{40}", package_revision), "package revision")
    metadata = {}
    for path in ("release-source-inputs.json", "MCPForUnity/package.json"):
        metadata[path] = json.loads(
            subprocess.check_output(
                ["git", "show", f"{package_revision}:{path}"], cwd=HERE.parents[1]
            )
        )
    inputs = metadata["release-source-inputs.json"]
    package = metadata["MCPForUnity/package.json"]
    require(
        inputs["server_commit"] == server_revision
        and package["mcpServerSource"] == inputs["server_source"]
        and package["mcpSkillRevision"] == inputs["skill_commit"],
        "the packaged server and skill pins",
    )
    return inputs["skill_commit"]


def build(directory, candidate_folder, catalog_folder, package_revision, native_paths):
    original_path = directory / "original-calls.json"
    originals_document = read(original_path)
    originals = index(originals_document["calls"])
    count_calls(list(originals.values()))
    source = directory / candidate_folder / "source-manifest.json"
    manifest = read(source)
    verify_source(manifest)
    revision = manifest["base_git_revision"]
    tree_sha256 = manifest["tree_content_sha256"]
    skill_revision = verify_package(package_revision, revision)
    node_path = directory / candidate_folder / "node-stdio.json"
    node = read(node_path)
    verify_node(node)
    require(
        node["source_revision"] == revision and node["source_tree_sha256"] == tree_sha256,
        "Node client source binding",
    )
    joined = {
        key: {
            field: original[field]
            for field in (
                "case",
                "call_index_zero_based",
                "language",
                "profile",
                "category",
                "operation",
                "wire_sha256",
                "raw_row_sha256",
                "public_evidence_sha256",
                "arguments_sha256",
                "original_result_sha256",
            )
        }
        for key, original in originals.items()
    }
    transports = {}
    archive_shas = set()
    versions = {}
    for transport in TRANSPORTS:
        baseline_path = directory / "beta-baseline-bound" / f"{transport}.json"
        preparation_path = directory / candidate_folder / f"{transport}.json"
        catalog_path = directory / catalog_folder / f"{transport}.json"
        resource_path = directory / "resources-beta-baseline" / f"{transport}.json"
        baseline, preparation, catalog = map(read, (baseline_path, preparation_path, catalog_path))
        resources = read(resource_path)
        require(
            baseline["product_git_revision"] == BASELINE
            and baseline["binding_fixture_targets"]
            and not baseline["binding_fixture_received_frames"].get("execute")
            and all(
                item["protocol"] == PROTOCOL and item["transport"] == transport
                for item in (baseline, preparation, catalog)
            )
            and all(
                item["source_revision"] == revision and item["source_tree_sha256"] == tree_sha256
                for item in (preparation, catalog)
            ),
            f"{transport} protocol/baseline fixture",
        )
        archive_shas.add(baseline["server_archive_sha256"])
        versions = baseline["dependency_versions"]
        before = index(baseline["calls"])
        after = index(preparation["original_cases"])
        require(
            before.keys() == after.keys() == originals.keys()
            and not preparation["failed_original_cases"]
            and len(preparation["unsafe_cases"]) == 6
            and preparation["hidden_profile_control"]["is_error"],
            f"{transport} preparation and controls",
        )
        for key, original in originals.items():
            joined[key][transport] = summarize_call(original, before[key], after[key], transport)
        require(
            tuple(check["tool"] for check in catalog["checks"]) == ALIASES
            and all(
                check["next_tool_operation_received"]
                and check["terminal_operation_omits_next_call"]
                and check["read_call"]["method"] == "resources/read"
                and check["read_call"]["params"]["_meta"]["unity_instance"] == check["target"]
                for check in catalog["checks"]
            ),
            f"{transport} executable catalog continuation",
        )
        require(
            resources["transport"] == transport
            and resources["protocol"] == PROTOCOL
            and Counter(item["scheme"] for item in resources["failure_replay"])
            == {"skill": 11, "file": 4}
            and all(
                item["observation"] == {"listed": False, "rpc_code": -32602}
                for item in resources["failure_replay"]
            )
            and len(resources["bundled_examples"]) == 2
            and all(
                item["listed"] and item["parts"] > 0
                for item in resources["bundled_examples"].values()
            ),
            f"{transport} original skill and host file resource routing",
        )
        transports[transport] = {
            "baseline_receipt_sha256": file_sha256(baseline_path),
            "preparation_receipt_sha256": file_sha256(preparation_path),
            "catalog_receipt_sha256": file_sha256(catalog_path),
            "catalog_aliases": list(ALIASES),
            "resource_route_receipt_sha256": file_sha256(resource_path),
            "archived_unadvertised_skill_uri_refusals": 11,
            "archived_host_file_uri_refusals": 4,
            "advertised_bundled_resource_reads": 2,
            "malformed_draft_refusals": len(preparation["unsafe_cases"]),
            "hidden_tool_refused": True,
        }
    require(len(archive_shas) == 1, "baseline source archive")
    report = {
        "version": 1,
        "archived_shipping_result_sha256": originals_document["shipping_results_sha256"],
        "selected_originals_sha256": file_sha256(original_path),
        "baseline_git_revision": BASELINE,
        "baseline_source_archive_sha256": archive_shas.pop(),
        "candidate_git_revision": revision,
        "candidate_package_git_revision": package_revision,
        "candidate_skill_git_revision": skill_revision,
        "candidate_server_src_sha256": tree_sha256,
        "sdk_versions": versions,
        "protocol": PROTOCOL,
        "binding_fixtures": {
            "stdio": "Fresh reloading status files with reserved non-listening loopback ports.",
            "http": "Authenticated loopback sessions that refuse Editor commands and record their wire arguments.",
        },
        "transports": transports,
        "calls": list(joined.values()),
        "native_controls": [summarize_native(path) for path in native_paths],
        "independent_json_rpc_control": {**node, "artifact_sha256": file_sha256(node_path)},
    }
    verify(report)
    return report


def verify(report):
    require(
        report["version"] == 1
        and report["baseline_git_revision"] == BASELINE
        and re.fullmatch(r"[0-9a-f]{40}", report["candidate_git_revision"])
        and re.fullmatch(r"[0-9a-f]{40}", report["candidate_package_git_revision"])
        and re.fullmatch(r"[0-9a-f]{40}", report["candidate_skill_git_revision"])
        and re.fullmatch(r"[0-9a-f]{64}", report["candidate_server_src_sha256"])
        and report["archived_shipping_result_sha256"] == file_sha256(SHIPPING)
        and report["protocol"] == PROTOCOL
        and set(report["transports"]) == set(TRANSPORTS),
        "report provenance",
    )
    node = report["independent_json_rpc_control"]
    verify_node(node)
    require(
        node["source_revision"] == report["candidate_git_revision"]
        and node["source_tree_sha256"] == report["candidate_server_src_sha256"],
        "published Node client source binding",
    )
    public_cases = {case["id"]: case for case in read(SHIPPING)["cases"]}
    count_calls(report["calls"])
    index(report["calls"])
    for call in report["calls"]:
        case = public_cases[call["case"]]
        require(
            case["language"] == call["language"]
            and case["profile"] == call["profile"]
            and case["evidence_sha256"] == call["public_evidence_sha256"]
            and case["calls"][call["call_index_zero_based"]]
            == {"binding": call["operation"], "ok": False},
            f"public shipping reference {identity(call)}",
        )
        category = call["category"]
        for transport in TRANSPORTS:
            result = call[transport]
            expected = "handler_validation" if category == "search_query" else "input_validation"
            require(
                result["direct_error"] == expected, f"{transport} admission stage {identity(call)}"
            )
            expected_follow_up = "catalog_result"
            if category == "inspection_page":
                expected_follow_up = (
                    "no_unity_session"
                    if transport == "stdio"
                    else "editor_command_observed_on_binding_fixture"
                )
            require(
                result["follow_up"] == expected_follow_up, f"{transport} follow-up {identity(call)}"
            )
        require(
            call["stdio"]["adjustment"] == call["http"]["adjustment"]
            and call["stdio"]["prepared_arguments_sha256"]
            == call["http"]["prepared_arguments_sha256"],
            f"cross-transport canonical call {identity(call)}",
        )
    for native in report["native_controls"]:
        require(
            native["camera_type"] == "UnityEngine.Camera"
            and 0 < native["camera_records"] <= 64
            and native["gameobject_error"] == "invalid_target"
            and native["component_inventory_sha256_before"]
            == native["component_inventory_sha256_after"],
            "published native controls",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-from", type=Path, metavar="WIRE_EVIDENCE_DIRECTORY")
    parser.add_argument("--candidate-folder", default="candidate-final")
    parser.add_argument("--catalog-folder", default="candidate-final-catalog")
    parser.add_argument("--package-revision")
    parser.add_argument("--native-control", action="append", type=Path, default=[])
    parser.add_argument("--report", default=REPORT, type=Path)
    args = parser.parse_args()
    if args.rebuild_from:
        if not args.package_revision:
            parser.error("--rebuild-from requires the immutable --package-revision")
        report = build(
            args.rebuild_from,
            args.candidate_folder,
            args.catalog_folder,
            args.package_revision,
            args.native_control,
        )
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    else:
        report = read(args.report)
        verify(report)
    print(
        f"{len(report['calls'])} original calls, {len(report['transports'])} transports, "
        f"{len(report['native_controls'])} native control runs"
    )


if __name__ == "__main__":
    main()
