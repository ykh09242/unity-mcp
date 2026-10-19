"""Require fresh Editor PlayMode bodies and the requested domain reload configuration."""

import argparse
from pathlib import Path
import re
from typing import Final
import xml.etree.ElementTree as ET

from check_unity_test_results import check_results

PREFIX: Final = "MCPForUnityTests.PlayMode."
REQUIRED_TESTS: Final = (
    PREFIX + "PlayModeExecutionEvidenceTests.RequiredBodyRunsWithRequestedReloadConfiguration",
    *(
        PREFIX + "PlayScenarioObservationRuntimeTests." + name
        for name in (
            "NativeFramesAreCollectedAndSamplerIsReleased",
            "NativeGcCounterIsMeasuredOrExplicitlyUnavailable",
            "RenderedCheckpointCapturesExactDimensionsOrDeclaresHeadlessUnavailable",
            "CancelledNativeObservationReleasesSamplerAndPendingCapture",
        )
    ),
)


def check(
    results: Path, runner_outcome: str, session: str, reload_mode: str, unity_version: str
) -> int:
    """Reject skipped, missing, stale or differently configured required test bodies."""
    if not re.fullmatch(r"[0-9a-f]{32}", session) or reload_mode not in ("on", "off"):
        print("::error::Invalid PlayMode evidence session or reload selection")
        return 1
    if check_results(results, runner_outcome, REQUIRED_TESTS):
        return 1
    try:
        root = ET.parse(results).getroot()
        cases = [
            case for case in root.iter("test-case") if case.get("fullname") == REQUIRED_TESTS[0]
        ]
        marker = f"MCP_PLAYMODE_BODY session={session} reload={reload_mode} unity={unity_version}"
        if len(cases) != 1 or marker not in (cases[0].findtext("output") or "").splitlines():
            print(
                "::error::Required PlayMode body lacks fresh session, reload and Unity version attribution"
            )
            return 1
    except (OSError, ET.ParseError):
        print("::error::Cannot read PlayMode body evidence")
        return 1
    print(
        f"Required PlayMode bodies passed with domain reload {reload_mode} on Unity {unity_version}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--runner-outcome", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--reload", choices=("on", "off"), required=True)
    parser.add_argument("--unity-version", required=True)
    args = parser.parse_args()
    return check(args.results, args.runner_outcome, args.session, args.reload, args.unity_version)


if __name__ == "__main__":
    raise SystemExit(main())
