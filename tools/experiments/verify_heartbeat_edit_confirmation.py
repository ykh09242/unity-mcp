"""Verify a published matched Unity edit study against its frozen predecessor."""

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


def verify(data, archive, pins, published, *, previous_digest=prior.PUBLISHED):
    # The frozen archive digest covers the previous product pins and all per-slot evidence.
    verdicts = matched.verify_matched_run(data, archive, pins, previous_digest, "followup")
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
    require(canonical(data) == published, "Frozen edit confirmation payload differs")
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
    published = canonical(data)
    require(published in STUDIES, "Unknown edit confirmation payload")
    archive_name, previous_digest, pins = STUDIES[published]
    verdicts = verify(
        data, read(DIRECTORY / archive_name), pins, published, previous_digest=previous_digest
    )
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
EDITOR_CATALOG = "8f8b5a294014304475a6c13d075339a7f90a631f2dc3f139a75543e46f3745d6"
CATALOG_DISCOVERY = "4c44cd9f4ea003fe8c4947ef9a573fc34a1731a500bda915404948a772d5723c"
SOURCE_EVIDENCE = "8363b9d00f0ad95682c5d2f5f43a660664ceccf0534d0b11e098ef3d67f1c516"
EDITOR_TARGET = "41cce2d17f0d28a78dc043b8472fc7dcea3ae6498bf0722fe2439c8f038e7489"
CONNECTION_GUIDE = "97a8010ea2eca05d485a7aa11ab0a64318660fe3182014dd688ba8198d1b45e1"
STUDIES = {
    PUBLISHED: ("compact_edit_confirmation_results.json", prior.PUBLISHED, FROZEN_PINS),
    EDITOR_CATALOG: (
        "heartbeat_edit_confirmation_results.json",
        PUBLISHED,
        {
            "package": "79fc892df8d4ea35e49c206fa4bfd7886b68d9fb",
            "runtime": "bf92d33d3853cc1128ece25beddce5fb0b5dfbe3",
            "skill": "dad211b9228fc4f81a291a100e46f88cf5e24ac0",
        },
    ),
    CATALOG_DISCOVERY: (
        "editor_catalog_confirmation_results.json",
        EDITOR_CATALOG,
        {
            "package": "41703d7e9d267817811e15bc68f0260394b1a74f",
            "runtime": "a394fcb081b37a855c97e2d0d630f2ee8468c51b",
            "skill": "a359961d00dbbc0161f9067f8955bd8516010fa0",
        },
    ),
    SOURCE_EVIDENCE: (
        "catalog_discovery_confirmation_results.json",
        CATALOG_DISCOVERY,
        {
            "package": "5405b3f59ef3693bc1a835d38bf855b79add2581",
            "runtime": "30a54c4311ffdfa37eabe41f978e4ae307dd587b",
            "skill": "a359961d00dbbc0161f9067f8955bd8516010fa0",
        },
    ),
    EDITOR_TARGET: (
        "source_evidence_confirmation_results.json",
        SOURCE_EVIDENCE,
        {
            "package": "6e403d15f9edf1821ab557a493cf2f55f7b3bf64",
            "runtime": "bc951d9e2a0f5edce54950243743955888906cb0",
            "skill": "a359961d00dbbc0161f9067f8955bd8516010fa0",
        },
    ),
    CONNECTION_GUIDE: (
        "editor_target_confirmation_results.json",
        EDITOR_TARGET,
        {
            "package": "25f87f770f0a30461d5b9b1e8e18bd8872f4f21a",
            "runtime": "81c1b832f03e9a981ab391477d9a34100586e4f6",
            "skill": "efcc61162191049738999b1f4504960f5db6d0f4",
        },
    ),
}

if __name__ == "__main__":
    main()
