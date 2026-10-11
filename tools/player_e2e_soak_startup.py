"""Inject an initial checkpoint filesystem failure before one genuine CLI Player launch."""

from pathlib import Path
import sys
from unittest.mock import patch

from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Server/src"))
from cli.main import cli
from cli.utils import play_scenario_player as player


def invoke(arguments: list[str]):
    """Path-check the one native launch, restore Popen, then execute the real child."""
    build = Path(arguments[0]).resolve(strict=True)
    output = Path(arguments[arguments.index("--output-dir") + 1]).resolve()
    if output.is_relative_to(build) or not output.is_relative_to(ROOT):
        raise ValueError(
            "Soak failure output must be repository-owned and outside the immutable build"
        )
    original = player.subprocess.Popen
    launches = []

    def launch(command, *args, **kwargs):
        if launches or "--mcp-scenario-request" not in command:
            raise ValueError("Soak failure requires exactly one owned native launch")
        request = Path(command[command.index("--mcp-scenario-request") + 1]).resolve(strict=True)
        if (
            Path(command[0]).resolve(strict=True).parent != build
            or request.parent.parent != output
            or request.name != "request.json"
        ):
            raise ValueError("Soak startup failure left owned request/build paths")
        (request.parent / "soak.json.tmp").mkdir(exist_ok=False)
        launches.append(request)
        player.subprocess.Popen = original
        return original(command, *args, **kwargs)

    with patch.object(player.subprocess, "Popen", launch):
        result = CliRunner().invoke(
            cli, ["--format", "json", "play-scenario", "player-soak", *arguments]
        )
    if len(launches) != 1:
        raise ValueError("Soak checkpoint failure did not launch one real native Player")
    return result


if __name__ == "__main__":
    outcome = invoke(sys.argv[1:])
    print(outcome.output, end="", flush=True)
    raise SystemExit(outcome.exit_code)
