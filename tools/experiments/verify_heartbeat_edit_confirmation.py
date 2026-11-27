"""Verify matched hosted Unity edits against the November 27 confirmation."""

import argparse
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import verify_compact_edit_confirmation as prior
from tools.experiments import verify_compact_edit_followup as matched
from tools.experiments.verify_agent_outcome_holdout import canonical, require, summary
from tools.experiments.verify_compact_edit import read

DIRECTORY = Path(__file__).resolve().parent


def verify(data, archive, pins, published):
    require(
        archive["runs"]["followup"]["product_pins"] == prior.FROZEN_PINS,
        "Comparison must be the previously measured package",
    )
    verdicts = matched.verify_matched_run(data, archive, pins, prior.PUBLISHED, "followup")
    run = data["runs"]["followup"]
    for panel in run["panels"].values():
        for row in panel["cases"]:
            counts = row["host_tool_calls"]
            require(
                set(counts) <= {"completed", "failed"}
                and all(type(value) is int and value > 0 for value in counts.values())
                and sum(counts.values()) == row["host_item_types"].get("mcp_tool_call", 0),
                "Host tool denominator differs",
            )
    require(canonical(data) == published, "Frozen heartbeat edit payload differs")
    return verdicts


def verified_archives(work, pins):
    """Check every extracted member against the independently reconstructed Git archive."""
    observed = prior.verified_archives(work, pins)
    for key, tree in (("package", "MCPForUnity"), ("runtime", "Server")):
        archive = subprocess.check_output(
            ["git", "archive", "--format=zip", pins[key], tree], cwd=DIRECTORY.parents[1]
        )
        with ZipFile(io.BytesIO(archive)) as source:
            members = {
                entry.filename: hashlib.sha256(source.read(entry)).hexdigest()
                for entry in source.infolist()
                if not entry.is_dir()
            }
        require(
            observed[key] == {"files": len(members), "sha256": canonical(members)},
            "Extracted files differ from the committed product",
        )
    return observed


def verify_private(data, root, verdicts):
    matched.verify_private(data, root, verdicts)
    work = root / "followup"
    run = data["runs"]["followup"]
    require(read(work / "actor-version.json") == run["actor"], "Installed actor differs")
    require(
        verified_archives(work, run["product_pins"])
        == run["verified_product_archives"]
        == read(work / "frozen-sources.json"),
        "Pre-submission product archive contents or receipt differs",
    )
    for label, panel in run["panels"].items():
        for row in panel["cases"]:
            folder = work / ("receipts-" + label) / row["id"]
            require(
                [path.name for path in folder.glob("attempt-*")] == ["attempt-1"],
                "Repeated or missing attempt",
            )
            raw = read(folder / "row.json")
            require(
                raw["model"] == run["actor"]["model"]
                and raw["effort"] == run["actor"]["reasoning_effort"]
                and prior.host_tool_calls(raw) == row["host_tool_calls"]
                and prior.discovery_use(raw) == row["discovery_use"],
                "Host execution conditions differ",
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report",
        type=Path,
        nargs="?",
        default=DIRECTORY / "heartbeat_edit_confirmation_results.json",
    )
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    data = read(args.report)
    archive = read(DIRECTORY / "compact_edit_confirmation_results.json")
    verdicts = verify(data, archive, FROZEN_PINS, PUBLISHED)
    if args.private_root:
        verify_private(data, args.private_root, verdicts)
    totals = {
        name: summary(panel["cases"]) for name, panel in data["runs"]["followup"]["panels"].items()
    }
    print(json.dumps(totals, indent=2))
    if args.require_perfect and not matched.perfect(totals):
        raise SystemExit(1)


FROZEN_PINS = {
    "package": "64f66bbe78b5f9e9ef10493018fd4c9556c827cc",
    "runtime": "9c6cbe71b7b0e8d3ba145cb0db81fad9fb779c87",
    "skill": "5875fd66f318b16564e5e8c9760d652e445bf780",
}
PUBLISHED = "e3cdeb5b34d85fd1fc1413a19f586e3bd760e23bad3adf263c704f0ca1353725"

if __name__ == "__main__":
    main()
