"""Verify archived build-condition and reward observations from the native two-Player fixture."""

import argparse
import json
import re
from pathlib import Path
from uuid import UUID


def verify(pair: dict) -> None:
    """Require two actual report identities and the matching process marker for each build."""
    if type(pair["editor_reward"]) is not int or pair["editor_reward"] != 1:
        raise ValueError("Editor reward observation must be one coin.")
    identities = set()
    build_guids = set()
    for name, coins, exit_code in (("broken", 0, 42), ("repaired", 1, 0)):
        run = pair[name]
        report = run["report"]
        summary = report["summary"]
        conditions = report["build_conditions"]
        report_id = report["report_id"]
        if not re.fullmatch(r"br-[0-9a-f]{32}", report_id) or not re.fullmatch(
            r"[0-9a-f]{64}", report["report_revision"]
        ):
            raise ValueError(f"{name} report identity is invalid.")
        guid = UUID(summary["native_build_guid"])
        if (
            not guid.int
            or summary["native_report_available"] is not True
            or summary["native_result"] != "succeeded"
            or summary["native_platform"] != "StandaloneWindows64"
            or conditions["player_target"] != summary["native_platform"]
            or conditions["editor_active_target"] != "StandaloneWindows64"
        ):
            raise ValueError(f"{name} lacks its native build context.")
        extra = conditions["player_extra_defines"]
        if (
            extra["complete"] is not True
            or ("MCP_FIXTURE_REWARD_DISABLED" in extra["names"]) != (name == "broken")
            or "MCPForUnity.Editor" not in conditions["editor_compilation_only_assemblies"]["names"]
            or "TestAsmdef" not in conditions["player_filter_assemblies"]["names"]
        ):
            raise ValueError(f"{name} lacks the observed compiler inputs or assembly difference.")
        marker = run["player_marker"].split()
        if (
            len(marker) != 3
            or marker[0] != "MCP_BUILD_CONDITION_REWARD"
            or UUID(marker[1]) != guid
            or marker[2] != str(coins)
            or type(run["process_exit"]) is not int
            or run["process_exit"] != exit_code
        ):
            raise ValueError(f"{name} reward observation does not match its native build.")
        identities.add(report_id)
        build_guids.add(guid)
    if len(identities) != 2 or len(build_guids) != 2:
        raise ValueError(
            "The broken and repaired Players require distinct reports and build GUIDs."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        with args.receipt.open("rb") as stream:
            encoded = stream.read(1024 * 1024 + 1)
        if len(encoded) > 1024 * 1024:
            raise ValueError("Receipt exceeds 1 MiB.")
        verify(json.loads(encoded))
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as error:
        parser.exit(1, f"Invalid build condition pair: {error}\n")
    print("Verified Editor and two build-bound Player reward observations.")


if __name__ == "__main__":
    main()
