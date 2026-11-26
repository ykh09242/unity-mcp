"""Check serialized Unity edit evidence from the fixed first-attempt follow-up."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import verify_compact_edit as previous
from tools.experiments.verify_agent_outcome_holdout import (
    canonical,
    digest,
    is_hash,
    require,
    summary,
)
from tools.experiments.verify_agent_outcome_holdout_confirmation import STRICT_ZERO, transitions

DISK_FIELDS = {"h06": "text", "r03": "tooltip", "n02": "queue", "n05": "preload"}


def masked_digest(source, span):
    start, end = span
    return digest(source[:start] + b"<target>" + source[end:])


def single_field(pattern, source):
    fields = list(re.finditer(pattern, source, re.MULTILINE))
    require(len(fields) == 1, "Missing or ambiguous serialized field")
    return fields[0]


def disk_snapshot(spec, asset, meta):
    task = spec["task"]
    field = DISK_FIELDS[task]
    if task == "n05":
        # Only the default block is editable. Platform override bytes remain protected.
        block = single_field(rb"^  defaultSettings:\r?\n((?:    [^\r\n]*(?:\r?\n|$))+)", meta)
        match = single_field(rb"^    preloadAudioData: ([01])\r?$", block[1])
        start, end = match.span(1)
        other = [digest(asset), masked_digest(meta, (block.start(1) + start, block.start(1) + end))]
        value = bool(int(match[1]))
    elif task == "n02":
        match = single_field(rb"^  m_CustomRenderQueue: (-?\d+)\r?$", asset)
        value = int(match[1])
        other = [masked_digest(asset, match.span(1)), digest(meta)]
    else:
        selector = spec["before"]["element"]
        labels = [
            node for node in ElementTree.fromstring(asset).iter() if node.get("name") == selector
        ]
        require(len(labels) == 1, "Missing or ambiguous UXML selector")
        value = labels[0].attrib[field]
        tag = single_field(
            rb'<ui:Label\b[^>]*?\bname="' + re.escape(selector.encode()) + rb'"[^>]*>', asset
        )
        match = single_field(rb"\s" + field.encode() + rb'="([^"]*)"', tag[0])
        start, end = match.span(1)
        other = [masked_digest(asset, (tag.start() + start, tag.start() + end)), digest(meta)]
    return {
        "value": value,
        "asset_sha256": digest(asset),
        "meta_sha256": digest(meta),
        "protected_bytes_sha256": canonical(other),
    }


def disk_valid(spec, pair):
    if not pair or any(pair.get(phase) is None for phase in ("before", "after")):
        return None
    field = DISK_FIELDS[spec["task"]]
    return (
        all(
            type(pair[phase]["value"]) is type(spec[phase][field])
            and pair[phase]["value"] == spec[phase][field]
            for phase in ("before", "after")
        )
        and pair["before"]["protected_bytes_sha256"] == pair["after"]["protected_bytes_sha256"]
    )


def read_pair(folder, spec):
    phases = {}
    for phase in ("before", "after"):
        asset = folder / f"{phase}-asset.bin"
        meta = folder / f"{phase}-meta.bin"
        phases[phase] = (
            disk_snapshot(spec, asset.read_bytes(), meta.read_bytes())
            if asset.is_file() and meta.is_file()
            else None
        )
    return phases


def rejection_reason(result):
    """Expose a structured domain code or schema field/type; keep raw messages private."""
    if isinstance(result, dict):
        code = result.get("code")
        if (
            type(code) is int
            or isinstance(code, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code)
        ):
            return f"code/{code}"
        if result.get("scope") == "input_schema" and result.get("ready") is False:
            categories = {error.get("category") for error in result.get("validation_errors", [])}
            if categories and categories <= {"additionalProperties", "required", "type", "enum"}:
                return "prepare/" + "+".join(sorted(categories))
        if result.get("error") == "operation is supported only for describe.":
            return "operation_outside_describe"
        if result.get("error") == "arguments is supported only for prepare.":
            return "arguments_outside_prepare"
        if (
            result.get("error")
            == "Unknown family in this scope. Read the current family directory."
        ):
            return "unknown_catalog_family"
    if isinstance(result, str):
        issues = re.fullmatch(r"Invalid arguments for tool '[a-z][a-z0-9_]*': (.+)", result)
        if issues:
            fields = issues[1].split("; ")
            if all(re.fullmatch(r"[a-z][a-z0-9_.]*: [a-z][a-z0-9_]*", field) for field in fields):
                return "schema/" + "+".join(sorted({field.replace(": ", "/") for field in fields}))
    return "unclassified_reply"


def material_operations(raw):
    operations = []
    for index, call in enumerate(raw["calls"]):
        if call["name"] != "manage_material" or call["success"] is not True:
            continue
        arguments = call["arguments"]
        if arguments.get("material_path") != raw["target"]:
            continue
        payload = call["result"].get("data") or {}
        action = arguments.get("action")
        record = {"index": index, "action": action}
        if action in {"get_render_state", "set_render_state"}:
            require(
                payload["material"]["asset_path"] == raw["target"], "Wrong native material path"
            )
            record["asset_guid_sha256"] = digest(payload["material"]["asset_guid"].encode("ascii"))
            record["reported_render_queue"] = payload["render_queue"]
            record["shader_render_queue"] = payload["shader_render_queue"]
        if action == "set_render_state":
            record["requested_render_queue"] = arguments["render_state"]["render_queue"]
            record["persistence"] = payload.get("persistence")
            record["disk_verified"] = payload.get("disk_verified")
        operations.append(record)
    return operations


def call_outcomes(cases):
    """Count wire requests, rejected replies and task outcomes on separate axes."""
    by_profile = {}
    for profile in ("core", "full"):
        rows = [row for row in cases if row["profile"] == profile]
        calls = [call for row in rows for call in row["calls"]]
        groups = {}
        for method in ("tools/call", "resources/read"):
            selected = [call for call in calls if call["method"] == method]
            rejected = [call for call in selected if call["reply"] == "failure"]
            groups[method] = {
                "requests": len(selected),
                "failed": len(rejected),
                "missing": sum(call["reply"] == "missing" for call in selected),
                "rejections": dict(Counter(call["rejection_reason"] for call in rejected)),
            }
        outcomes = Counter()
        for row in rows:
            outcome = "completed" if row["verdict"] == "completed" else "incomplete"
            rejected = any(call["reply"] == "failure" for call in row["calls"])
            suffix = "with_rejection" if rejected else "without_rejection"
            outcomes[outcome + "_" + suffix] += 1
        by_profile[profile] = groups | {"slot_outcomes": dict(outcomes)}
    return by_profile


def verify(data, archive, pins):
    require(canonical(archive) == previous.PUBLISHED, "Archived comparison changed")
    require(
        data["schema"] == 1
        and data["preseal_sha256"] == archive["preseal_sha256"] == previous.SEAL
        and data["specs"] == archive["specs"]
        and data["languages"] == archive["languages"]
        and data["prior_report_sha256"] == previous.PUBLISHED,
        "Prior report, originals, expectations or languages differ",
    )
    run = data["runs"]["followup"]
    prior = archive["runs"]["candidate"]
    require(
        set(data["runs"]) == {"followup"}
        and run["product_pins"] == pins
        and set(run["panels"]) == {"old", "new"}
        and all(
            run[k] == prior[k]
            for k in ("actor", "unity_editor", "native_observer_sha256", "shared_preferences")
        ),
        "The matched trial conditions or frozen revisions differ",
    )
    previous.verify_run(run, data["specs"])
    specs = {spec["task"]: spec for spec in data["specs"]}
    require(set(run["disk_controls"]) == DISK_FIELDS.keys(), "Missing serialized edit control")
    for task, pair in run["disk_controls"].items():
        require(disk_valid(specs[task], pair) is True, "A legal saved edit failed its disk control")
    disk_verdicts = {}
    for name, panel in run["panels"].items():
        require(panel["call_outcomes"] == call_outcomes(panel["cases"]), "Call outcomes differ")
        require(
            data["paired_transitions"][name]
            == transitions(prior["panels"][name]["cases"], panel["cases"]),
            "Paired transitions differ",
        )
        for row in panel["cases"]:
            if row["task"] not in DISK_FIELDS:
                require("disk" not in row, "Unexpected disk observation")
                continue
            pair = row["disk"]
            for item in pair.values():
                if item is not None:
                    require(
                        all(
                            is_hash(item[k])
                            for k in ("asset_sha256", "meta_sha256", "protected_bytes_sha256")
                        ),
                        "Invalid saved asset or protected byte digest",
                    )
            valid = disk_valid(specs[row["task"]], pair)
            require(row["persisted_scope_valid"] is valid, "Verdict differs from on-disk state")
            disk_verdicts[row["id"]] = valid
    require(canonical(data) == PUBLISHED, "Frozen follow-up payload differs")
    return disk_verdicts


def verify_private(data, root, verdicts):
    work = root / "followup"
    run = data["runs"]["followup"]
    specs = {spec["task"]: spec for spec in previous.read(root / "seal/cases.json")["cases"]}
    submissions = {path.name for path in (work / "launches").iterdir()}
    for panel in run["panels"].values():
        for row in panel["cases"]:
            spec = specs[row["task"]]
            raw = previous.read(work / ("receipts-" + spec["panel"]) / row["id"] / "row.json")
            if row["task"] == "n02":
                require(
                    row["material_operations"] == material_operations(raw),
                    "Material replies differ",
                )
            for observed, public in zip(raw["calls"], row["calls"], strict=True):
                if public["reply"] == "failure":
                    require(
                        public["rejection_reason"] == rejection_reason(observed["result"]),
                        "Sanitized rejection reason differs from the original reply",
                    )
            if row["prompt_reached_model"]:
                require(row["id"] in submissions, "Missing original submission")
            if row["id"] in submissions:
                submissions.remove(row["id"])
                require(
                    previous.read(work / "launches" / row["id"] / "submission.json")
                    == {"profile": row["profile"], "original_sha256": row["original_sha256"]},
                    "Submitted profile or original differs",
                )
            if row["task"] in DISK_FIELDS:
                folder = work / "disk" / row["id"]
                require(read_pair(folder, spec) == row["disk"], "Physical copies differ")
                if folder.exists():
                    source = previous.read(folder / "source.json")
                    require(
                        source
                        == {"target": spec["target"], "original_sha256": row["original_sha256"]},
                        "Disk copy provenance differs from the sealed path",
                    )
                    if spec["task"] == "n02":
                        for phase in ("before", "after"):
                            meta = (folder / f"{phase}-meta.bin").read_bytes()
                            guid = single_field(rb"^guid: ([0-9a-f]{32})\r?$", meta)[1]
                            require(
                                all(
                                    call["asset_guid_sha256"] == digest(guid)
                                    for call in row["material_operations"]
                                    if "asset_guid_sha256" in call
                                ),
                                "Native material identity differs from captured metadata",
                            )
    require(not submissions, "An uncommissioned actor was launched")
    for task, pair in run["disk_controls"].items():
        require(
            read_pair(work / "disk-controls" / task, specs[task]) == pair,
            "Disk control bytes differ",
        )
    previous.private_check(data, root, verdicts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    directory = Path(__file__).resolve().parent
    parser.add_argument(
        "report", type=Path, nargs="?", default=directory / "compact_edit_followup_results.json"
    )
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    data = previous.read(args.report)
    archive = previous.read(directory / "compact_edit_results.json")
    verdicts = verify(data, archive, FROZEN_PINS)
    if args.private_root:
        verify_private(data, args.private_root, verdicts)
    totals = {
        panel: summary(records["cases"])
        for panel, records in data["runs"]["followup"]["panels"].items()
    }
    print(json.dumps(totals, indent=2))
    if args.require_perfect and not all(
        group["completed"] == group["scheduled"] == 6
        and all(group[key] == 0 for key in STRICT_ZERO)
        for panel in totals.values()
        for group in panel.values()
    ):
        raise SystemExit(1)


FROZEN_PINS = {
    "package": "33ebf544daf6148cc374334bad47924235cf9e1f",
    "runtime": "01eb2d77045b5d1aec0d77dc05da10c5a5ccaebe",
    "skill": "f808ebff8e0f90e0e3122d2fe1fd1283e2aabfa6",
}
PUBLISHED = "3c8db762099e96428ca382fb2e316df9cb8408c66611715d75992660c141dc78"


if __name__ == "__main__":
    main()
