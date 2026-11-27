"""Replay the separately commissioned language supplement to the frozen edit study."""

import argparse
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import verify_compact_edit as edit
from tools.experiments.verify_agent_outcome_holdout import canonical, require, summary
from tools.experiments.verify_agent_outcome_holdout_confirmation import STRICT_ZERO
from tools.experiments.verify_compact_edit_confirmation import host_tool_calls
from tools.experiments.verify_compact_edit_followup import disk_valid, read_pair
from tools.experiments.verify_heartbeat_edit_confirmation import EDITOR_TARGET, verified_archives

DIRECTORY = Path(__file__).resolve().parent
PUBLISHED = "4cc9c82322d3875805b2a20fd2ba40b41f0a29f7488e56656d403fc15aa5a01a"


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
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    request_path = DIRECTORY / "korean_edit_request.json"
    request = edit.read(request_path)
    data = edit.read(DIRECTORY / "korean_edit_confirmation_results.json")
    primary = edit.read(DIRECTORY / "editor_target_confirmation_results.json")
    require(canonical(data) == PUBLISHED, "Published supplement changed")
    require(data["primary_sha256"] == canonical(primary) == EDITOR_TARGET, "Primary study")
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
    totals = summary(rows)
    print(json.dumps(totals, indent=2))
    if args.require_perfect and any(
        t["completed"] != t["scheduled"] or any(t[k] for k in STRICT_ZERO) for t in totals.values()
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
