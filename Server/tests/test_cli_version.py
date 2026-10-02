from click.testing import CliRunner

from cli.main import cli
from core.telemetry import get_package_version


def test_cli_version_matches_server_package_version():
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"unity-mcp, version {get_package_version()}"
