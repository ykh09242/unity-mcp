"""Fork distribution lookup and telemetry defaults."""

from importlib import metadata
from pathlib import Path

import pytest
import tomllib

from core import telemetry
from core.config import ServerConfig, config


def test_distribution_includes_preserved_mit_notice() -> None:
    # Given the server's declared packaging metadata.
    server_root = Path(__file__).parents[1]
    with (server_root / "pyproject.toml").open("rb") as manifest:
        project = tomllib.load(manifest)["project"]

    # When resolving the license files included by the package backend.
    license_files = [server_root / name for name in project["license-files"]]

    # Then the redistributed package includes the complete original MIT notice.
    assert project["license"] == "MIT"
    assert license_files == [server_root / "LICENSE"]
    assert license_files[0].read_bytes() == (server_root.parent / "LICENSE").read_bytes()


def test_version_uses_installed_fork_when_upstream_is_also_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given distinct installed versions for the fork and upstream distribution.
    for name, version in (("ykh09242-unity-mcp-server", "98.7.6"), ("mcpforunityserver", "1.2.3")):
        dist = tmp_path / f"{name.replace('-', '_')}-{version}.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
        )
    monkeypatch.syspath_prepend(str(tmp_path))

    # When the running server resolves its installed version.
    version = telemetry.get_package_version()

    # Then the fork's metadata wins over any upstream installation.
    assert version == "98.7.6"


def test_version_uses_checkout_when_fork_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an uninstalled checkout.
    def unavailable_version(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(telemetry.metadata, "version", unavailable_version)
    with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as manifest:
        expected_version = tomllib.load(manifest)["project"]["version"]

    # When resolving the version without installed metadata.
    version = telemetry.get_package_version()

    # Then the checkout manifest provides the existing release version.
    assert version == expected_version


def test_checkout_version_skips_invalid_nested_toml(tmp_path, monkeypatch):
    core = tmp_path / "src" / "core"
    core.mkdir(parents=True)
    (core / "pyproject.toml").write_text("[project", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ykh09242-unity-mcp-server"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(telemetry, "__file__", str(core / "telemetry.py"))
    assert telemetry._version_from_local_pyproject() == "9.8.7"


@pytest.fixture
def fork_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    defaults = ServerConfig()
    monkeypatch.setattr(config, "telemetry_enabled", defaults.telemetry_enabled)
    monkeypatch.setattr(config, "telemetry_endpoint", defaults.telemetry_endpoint)
    for name in (
        "DISABLE_TELEMETRY",
        "UNITY_MCP_DISABLE_TELEMETRY",
        "MCP_DISABLE_TELEMETRY",
        "UNITY_MCP_TELEMETRY_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in ("APPDATA", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(telemetry.TelemetryCollector, "_send_telemetry", lambda self, record: None)
    return tmp_path / "UnityMCP"


def test_default_collection_does_not_start_worker_or_touch_storage(fork_defaults: Path) -> None:
    # Given the fork's default configuration without a telemetry endpoint.
    collector = telemetry.TelemetryCollector()
    try:
        # When recording an event and a milestone.
        collector.record(telemetry.RecordType.USAGE, {"example": True})
        first = collector.record_milestone(telemetry.MilestoneType.FIRST_STARTUP)

        # Then collection remains inert, including persistent storage.
        assert not collector.config.enabled
        assert collector.config.endpoint == ""
        assert collector._worker is None
        assert collector._queue.empty()
        assert first is False
        assert not fork_defaults.exists()
    finally:
        collector.shutdown()


@pytest.mark.parametrize("endpoint", ["file:///tmp/events", "http://localhost/events", "invalid"])
def test_invalid_override_keeps_collection_disabled(
    fork_defaults: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    # Given an invalid explicit endpoint and no configured fallback.
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", endpoint)

    # When resolving telemetry configuration.
    resolved = telemetry.TelemetryConfig()

    # Then there is no endpoint to receive telemetry.
    assert not resolved.enabled
    assert resolved.endpoint == ""
    assert not fork_defaults.exists()


def test_explicit_endpoint_enables_collection_when_allowed(
    fork_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an explicit endpoint with no opt-out.
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", "https://owned.example/events")

    # When resolving telemetry configuration.
    resolved = telemetry.TelemetryConfig()

    # Then the chosen endpoint enables collection.
    assert resolved.enabled
    assert resolved.endpoint == "https://owned.example/events"


@pytest.mark.parametrize(
    "optout", ["DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"]
)
def test_env_opt_out_overrides_explicit_endpoint(
    fork_defaults: Path, monkeypatch: pytest.MonkeyPatch, optout: str
) -> None:
    # Given an explicit endpoint with an environment opt-out.
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", "https://owned.example/events")
    monkeypatch.setenv(optout, "true")

    # When resolving telemetry configuration.
    resolved = telemetry.TelemetryConfig()

    # Then the endpoint cannot bypass the opt-out.
    assert not resolved.enabled
    assert not fork_defaults.exists()


def test_config_opt_out_overrides_explicit_endpoint(
    fork_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an explicit endpoint with the config opt-out.
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", "https://owned.example/events")
    monkeypatch.setattr(config, "telemetry_enabled", False)

    # When resolving telemetry configuration.
    resolved = telemetry.TelemetryConfig()

    # Then the explicit endpoint cannot bypass the config opt-out.
    assert not resolved.enabled
    assert not fork_defaults.exists()


def test_invalid_config_endpoint_keeps_collection_disabled(
    fork_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an invalid config endpoint without an environment override.
    monkeypatch.setattr(config, "telemetry_endpoint", "file:///tmp/events")

    # When resolving telemetry configuration.
    resolved = telemetry.TelemetryConfig()

    # Then the invalid config cannot become its own validation fallback.
    assert not resolved.enabled
    assert resolved.endpoint == ""
    assert not fork_defaults.exists()
