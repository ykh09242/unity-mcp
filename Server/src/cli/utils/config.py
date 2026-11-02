"""CLI Configuration utilities."""

import os
from dataclasses import dataclass
from typing import Optional

import click


class ConfigurationIntRange(click.IntRange):
    """Keep Click's acceptance/precedence while omitting rejected credential-like inputs."""

    def convert(
        self,
        value: str | int | float | None,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> int:
        try:
            return super().convert(value, param, ctx)
        except click.BadParameter:
            maximum = f" and at most {self.max}" if self.max is not None else ""
            raise click.BadParameter(
                f"Expected an integer at least {self.min}{maximum} (input omitted).",
                ctx=ctx,
                param=param,
            ) from None


class ConfigurationFormat(click.Choice):
    """Output-format errors describe the allowed values without echoing rejected input."""

    def convert(
        self,
        value: str | None,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        try:
            return super().convert(value, param, ctx)
        except click.BadParameter:
            raise click.BadParameter(
                "Choose text, json, or table (input omitted).", ctx=ctx, param=param
            ) from None


HTTP_PORT_TYPE = ConfigurationIntRange(1, 65535)
TIMEOUT_TYPE = ConfigurationIntRange(min=1)
OUTPUT_FORMAT_TYPE = ConfigurationFormat(["text", "json", "table"])


@dataclass
class CLIConfig:
    """Configuration for CLI connection to Unity."""

    host: str = "127.0.0.1"
    port: int = 8080
    timeout: int = 300
    format: str = "text"  # text, json, table
    unity_instance: Optional[str] = None
    verbose: bool = False
    expected_project_root: str | None = None

    @classmethod
    def from_env(cls) -> "CLIConfig":
        port_raw = os.environ.get("UNITY_MCP_HTTP_PORT", "8080")
        try:
            port = HTTP_PORT_TYPE.convert(port_raw, None, None)
        except click.BadParameter as error:
            raise ValueError(f"Invalid UNITY_MCP_HTTP_PORT value. {error.message}") from error

        timeout_raw = os.environ.get("UNITY_MCP_TIMEOUT", "300")
        try:
            timeout = TIMEOUT_TYPE.convert(timeout_raw, None, None)
        except click.BadParameter as error:
            raise ValueError(f"Invalid UNITY_MCP_TIMEOUT value. {error.message}") from error

        format_raw = os.environ.get("UNITY_MCP_FORMAT", "text")
        try:
            output_format = OUTPUT_FORMAT_TYPE.convert(format_raw, None, None)
        except click.BadParameter as error:
            raise ValueError(f"Invalid UNITY_MCP_FORMAT value. {error.message}") from error

        config = cls(
            host=os.environ.get("UNITY_MCP_HOST", "127.0.0.1"),
            port=port,
            timeout=timeout,
            format=output_format,
            unity_instance=os.environ.get("UNITY_MCP_INSTANCE"),
        )
        from cli.utils.config_diagnostics import capture_environment_configuration

        capture_environment_configuration(config)
        return config


# Global config instance
_config: Optional[CLIConfig] = None


def get_config() -> CLIConfig:
    """Get the current CLI configuration."""
    global _config
    if _config is None:
        _config = CLIConfig.from_env()
    return _config


def set_config(config: CLIConfig) -> None:
    """Set the CLI configuration."""
    global _config
    _config = config
