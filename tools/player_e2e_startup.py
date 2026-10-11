"""Run the real CLI with one owned native initial-progress write failure."""

import argparse
from pathlib import Path
import sys
from unittest.mock import patch

from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Server/src"))

from cli.main import cli
from cli.utils import play_scenario_player as player


def invoke_startup_failure(build: Path, output: Path, revision: str):
    """Block one diagnostic write immediately before a genuine, owned Player launch."""
    build = build.resolve(strict=True)
    output = output.resolve()
    if output.is_relative_to(build) or not output.is_relative_to(ROOT):
        raise ValueError(
            "Startup failure output must be owned by this repository outside the build"
        )
    original = player.subprocess.Popen
    launches = []

    def launch(command, *args, **kwargs):
        if launches or "--mcp-scenario-request" not in command:
            raise ValueError("Startup failure injection requires exactly one native Player launch")
        request = Path(command[command.index("--mcp-scenario-request") + 1]).resolve(strict=True)
        if (
            Path(command[0]).resolve(strict=True).parent != build
            or request.name != "request.json"
            or request.parent.parent != output
        ):
            raise ValueError("Startup failure injection left the owned build/request paths")
        (request.parent / "progress.json.tmp").mkdir(exist_ok=False)
        launches.append(request)
        # Restore before launching: this hook never observes or replaces another subprocess.
        player.subprocess.Popen = original
        return original(command, *args, **kwargs)

    with patch.object(player.subprocess, "Popen", launch):
        result = CliRunner().invoke(
            cli,
            [
                "--format",
                "json",
                "play-scenario",
                "player-run",
                str(build),
                "--output-dir",
                str(output),
                "--repeat-count",
                "3",
                "--timeout-seconds",
                "30",
                "--cleanup-wait-seconds",
                "10",
                "--expected-build-revision",
                revision,
                "--require-verified-payload",
            ],
        )
    if len(launches) != 1:
        raise ValueError("Startup failure case did not launch exactly one real Player")
    return result


def main() -> int:
    """Preserve the real CLI output and nonzero outcome for the independent checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("revision")
    args = parser.parse_args()
    result = invoke_startup_failure(args.build, args.output, args.revision)
    print(result.output, end="", flush=True)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
