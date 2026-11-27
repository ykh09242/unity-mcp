"""Replay the separately commissioned language supplement to the frozen edit study."""

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import verify_compact_edit as edit
from tools.experiments.verify_agent_outcome_holdout import canonical, require, summary
from tools.experiments.verify_agent_outcome_holdout_confirmation import STRICT_ZERO
from tools.experiments.verify_compact_edit_confirmation import host_tool_calls
from tools.experiments.verify_compact_edit_followup import disk_valid, read_pair
from tools.experiments.verify_heartbeat_edit_confirmation import (
    ACTION_CONTRACT,
    CONNECTION_GUIDE,
    CONNECTION_OBSERVATION,
    EDITOR_TARGET,
    PREVIEW_CONTINUATION,
    STRUCTURED_SEARCH,
    verified_archives,
)

DIRECTORY = Path(__file__).resolve().parent
PUBLISHED = "4cc9c82322d3875805b2a20fd2ba40b41f0a29f7488e56656d403fc15aa5a01a"
STUDIES = {
    PUBLISHED: ("editor_target_confirmation_results.json", EDITOR_TARGET),
    "66583af47086d808cbde617dd604c36874fbd39162011cd199fb2ad9d2548102": (
        "connection_guide_confirmation_results.json",
        CONNECTION_GUIDE,
    ),
    "f1f548131afba4031d714ffb034321d429f6ad10e7f84398b527882b3158df99": (
        "structured_search_confirmation_results.json",
        STRUCTURED_SEARCH,
    ),
    "08f16d22899f9088883d558a5f833cc56bde05f674b67979dd15ebc96c0852ff": (
        "action_contract_confirmation_results.json",
        ACTION_CONTRACT,
    ),
    "1bb23207f5854999a5ba1904f7161bc53da6a0e421df7694dbcf7285a80ffcd3": (
        "preview_continuation_confirmation_results.json",
        PREVIEW_CONTINUATION,
    ),
    "637bed99326117e4581b452bd473867be4f94e7f305d7e39ca7067cfda9e39a6": (
        "connection_observation_confirmation_results.json",
        CONNECTION_OBSERVATION,
    ),
}


def verify_private(data, request, root):
    work = root / "followup"
    language = request["language"]
    require(edit.digest((root / "seal/cases.json").read_bytes()) == data["seal_sha256"], "Seal")
    require(
        verified_archives(work, data["product_pins"]) == data["verified_product_archives"],
        "Product archives",
    )
    require(edit.read(work / "actor-version.json") == data["actor"], "Installed agent")
    require(edit.read(work / "cleanup.json") == data["shared_preferences"], "Editor cleanup")
    initial = edit.read(work / "shared-tool-preferences.json")
    require(initial["sha256"] == data["shared_preferences"]["shared_prefs"], "Tool preferences")
    reviews = edit.read(work / f"reviews-{language}.json")
    bundle = edit.read(work / "product/Server/src/services/skills_bundle.json")["payload"]
    require(bundle["revision"] == data["product_pins"]["skill"], "Packaged guide revision")
    entries = {entry["path"]: entry["digest"] for entry in bundle["files"]}
    spec = {**request, "task": request["fixture"]}
    require(
        read_pair(work / "disk-controls" / spec["task"], spec) == data["native_control"],
        "Independent saved-file control",
    )
    require({p.name for p in (work / "launches").iterdir()} == set(reviews), "Submissions")
    for row in data["cases"]:
        folder = work / f"receipts-{language}" / row["id"]
        require([p.name for p in folder.glob("attempt-*")] == ["attempt-1"], "Attempt count")
        raw = edit.read(folder / "row.json")
        require(
            raw["query"] == request["query"]
            and raw["prompt_sha256"] == row["original_sha256"]
            and host_tool_calls(raw) == row["host_tool_calls"],
            "Original input or host invocations",
        )
        pair = read_pair(work / "disk" / row["id"], spec)
        require(pair == row["disk"], "Saved bytes")
        reads = edit.verify_private_row(
            work,
            language,
            row,
            reviews[row["id"]],
            entries,
            {"scene_save_rules": {"others": "preserve scene state"}},
            spec,
            {row["id"]: disk_valid(spec, pair)},
        )
        require(
            reads
            == sum(row["calls"][i]["reply"] == "success" for i in row["skill_document_reads"]),
            "Skill documents",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report", nargs="?", type=Path, default=DIRECTORY / "korean_edit_confirmation_results.json"
    )
    parser.add_argument("--private-root", type=Path)
    parser.add_argument(
        "--include-primary",
        action="store_true",
        help="Combine catalogs measured on the same product revision.",
    )
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    request_path = DIRECTORY / "korean_edit_request.json"
    request = edit.read(request_path)
    data = edit.read(args.report)
    frozen = canonical(data)
    require(frozen in STUDIES, "Unknown language supplement")
    primary_file, expected = STUDIES[frozen]
    primary = edit.read(DIRECTORY / primary_file)
    require(data["primary_sha256"] == canonical(primary) == expected, "Primary study")
    if frozen != PUBLISHED:
        require(
            data["product_pins"] == primary["runs"]["followup"]["product_pins"],
            "Matched product revision",
        )
    require(data["request_sha256"] == edit.digest(request_path.read_bytes()), "Frozen request")
    require(
        request["id"] not in primary["languages"]
        and data["languages"] == primary["languages"] | {request["id"]: request["language"]},
        "Language coverage",
    )
    require(data["actor"] == primary["runs"]["followup"]["actor"], "Actor differs")
    rows = data["cases"]
    require(sorted(row["profile"] for row in rows) == ["core", "full"], "Paired profiles")
    for row in rows:
        require(
            row["language_case"] == request["id"]
            and row["attempt_count"] == 1
            and row["original_sha256"]
            == row["prompt_sha256"]
            == edit.digest(request["query"].encode()),
            "First-attempt delivery",
        )
        if row["verdict"] == "completed":
            require(
                row["before_facts"] == request["before"]
                and row["after_facts"] == request["after"]
                and row["host_final_correct"]
                and not row["protected_changes"]
                and disk_valid({**request, "task": request["fixture"]}, row["disk"]),
                "Completed edit differs from the requested saved state",
            )
    if args.private_root:
        verify_private(data, request, args.private_root)
    if args.include_primary:
        run = primary["runs"]["followup"]
        require(
            data["product_pins"] == run["product_pins"],
            "Cannot combine different product revisions",
        )
        rows = [*rows, *(row for panel in run["panels"].values() for row in panel["cases"])]
    totals = summary(rows)
    for profile, counts in totals.items():
        selected = [row for row in rows if row["profile"] == profile]
        calls = [call for row in selected for call in row["calls"]]
        document_reads = [row["calls"][i] for row in selected for i in row["skill_document_reads"]]
        counts["tool_calls"] = sum(call["method"] == "tools/call" for call in calls)
        counts["skill_document_reads"] = len(document_reads)
        counts["failed_skill_document_reads"] = sum(
            call["reply"] != "success" for call in document_reads
        )
    print(json.dumps(totals, indent=2))
    if args.require_perfect and any(
        t["completed"] != t["scheduled"] or any(t[k] for k in STRICT_ZERO) for t in totals.values()
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
