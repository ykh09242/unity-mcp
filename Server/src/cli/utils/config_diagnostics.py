"""Provenance bound to the current CLI configuration, independent of a server process."""

import os
from dataclasses import asdict
from typing import TYPE_CHECKING, assert_never

import click
from click.core import ParameterSource

from core.config_provenance import ConfigurationTrace
from models.effective_config import ConfigOrigin, ConfigSource, EffectiveConfigReport

if TYPE_CHECKING:
    from cli.utils.config import CLIConfig

_current: tuple["CLIConfig", ConfigurationTrace] | None = None
_ENV = {
    "host": "UNITY_MCP_HOST",
    "port": "UNITY_MCP_HTTP_PORT",
    "timeout": "UNITY_MCP_TIMEOUT",
    "format": "UNITY_MCP_FORMAT",
    "unity_instance": "UNITY_MCP_INSTANCE",
}


def capture_cli_configuration(config: "CLIConfig", context: click.Context) -> None:
    """Use Click's actual source, including explicit flags equal to a built-in default."""
    from cli.utils.config import CLIConfig

    global _current
    trace = ConfigurationTrace(asdict(CLIConfig()))
    for name, value in asdict(config).items():
        parameter = "instance" if name == "unity_instance" else name
        source = context.get_parameter_source(parameter)
        match source:
            case ParameterSource.COMMANDLINE:
                resolved: ConfigSource = "cli"
            case ParameterSource.ENVIRONMENT:
                resolved = "environment"
            case ParameterSource.DEFAULT_MAP:
                resolved = "default_map"
            case ParameterSource.PROMPT:
                resolved = "prompt"
            case ParameterSource.DEFAULT | None:
                resolved = "default"
            case unreachable:
                assert_never(unreachable)
        env = _ENV.get(name)
        raw = os.environ.get(env) if env else None
        overrides = (
            ((env, "built_in") if env and raw else ("built_in",))
            if resolved == "cli"
            else ("built_in",)
            if resolved in {"environment", "default_map", "prompt"}
            else ()
        )
        trace.record(
            name,
            value,
            ConfigOrigin(
                source=resolved,
                source_detail=env
                if resolved == "environment" and env
                else "--" + parameter.replace("_", "-")
                if resolved == "cli"
                else "built_in",
                overrides=overrides,
                diagnostics=("empty_environment_ignored",) if raw == "" else (),
            ),
        )
    _current = (config, trace)


def record_project_configuration(config: "CLIConfig", name: str) -> None:
    """Called only for fields actually merged by the project-context resolver."""
    if _current is not None and _current[0] is config:
        _current[1].record(
            name,
            getattr(config, name),
            ConfigOrigin(
                source="project_context", source_detail="project_context", overrides=("built_in",)
            ),
        )


def local_configuration(config: "CLIConfig") -> EffectiveConfigReport:
    """Return local settings only; reading this never observes the selected server."""
    from cli.utils.config import CLIConfig

    trace = (
        _current[1]
        if _current is not None and _current[0] is config
        else ConfigurationTrace(asdict(CLIConfig()))
    )
    return trace.report("local_cli_configuration", asdict(config))


def record_expected_project(config: "CLIConfig", context_loaded: bool) -> None:
    """Record an explicit project guard without serializing a context file."""
    if _current is not None and _current[0] is config:
        _current[1].record(
            "expected_project_root",
            config.expected_project_root,
            ConfigOrigin(
                source="cli",
                source_detail="--expect-project",
                overrides=("project_context",) if context_loaded else ("built_in",),
            ),
        )


def record_project_override(config: "CLIConfig", name: str) -> None:
    """Record a loaded context default that the actual merge deliberately skipped."""
    if _current is not None and _current[0] is config:
        value = getattr(config, name)
        origin = _current[1].origin(name, value)
        _current[1].record(
            name,
            value,
            origin.model_copy(update={"overrides": (*origin.overrides, "project_context")}),
        )


def capture_environment_configuration(config: "CLIConfig") -> None:
    """Capture the distinct from_env resolver, which treats empty strings as supplied."""
    from cli.utils.config import CLIConfig

    global _current
    trace = ConfigurationTrace(asdict(CLIConfig()))
    for name, value in asdict(config).items():
        environment = _ENV.get(name)
        supplied = bool(environment and environment in os.environ)
        trace.record(
            name,
            value,
            ConfigOrigin(
                source="environment" if supplied else "default",
                source_detail=environment if supplied and environment else "built_in",
                overrides=("built_in",) if supplied else (),
            ),
        )
    _current = (config, trace)
