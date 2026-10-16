"""Project the actual Click command tree in an isolated, non-executing worker."""

import argparse
import ast
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import importlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import click

if __package__:
    from .cli_example_validation import (
        ExampleValidationError,
        extract_examples,
        inert_type,
        validate_argv,
    )
else:
    from cli_example_validation import (
        ExampleValidationError,
        extract_examples,
        inert_type,
        validate_argv,
    )


EXAMPLE_PAGES = (
    "website/docs/guides/cli.md",
    "website/docs/guides/cli-examples.md",
    "website/docs/reference/cli.md",
    "website/docs/guides/multi-instance.md",
)


@dataclass(frozen=True)
class CliReference:
    markdown: str
    command_paths: tuple[str, ...]
    example_count: int
    module_origins: dict[str, str]


def walk_commands(root: click.Command):
    """Keep public aliases, support lazy groups and reject ancestry cycles."""

    def visit(command, path, ancestors):
        if id(command) in ancestors:
            raise ValueError(f"CLI command cycle at {' '.join(path)}")
        yield path, command
        if isinstance(command, click.Group):
            with click.Context(command, info_name=path[-1]) as context:
                for name in sorted(command.list_commands(context)):
                    try:
                        child = command.get_command(context, name)
                    except Exception as error:
                        raise ValueError(
                            f"Cannot load advertised command {' '.join((*path, name))}"
                        ) from error
                    if child is None:
                        raise ValueError(f"Missing advertised command {' '.join((*path, name))}")
                    if not child.hidden:
                        yield from visit(child, (*path, name), ancestors | {id(command)})

    return list(visit(root, ("unity-mcp",), frozenset()))


def _text(value: str) -> str:
    return (
        " ".join(value.replace("\\b", "").replace("\b", "").split())
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace("`", "\\`")
    )


def _type_label(kind: click.ParamType) -> str:
    try:
        inert_type(kind)
    except ExampleValidationError:
        return f"{type(kind).__name__} (custom/nonliteral conversion not validated)"
    if type(kind) is click.Choice:
        return "choice: " + ", ".join(
            str(value) for value in kind.choices if type(value) in (str, int, float, bool)
        )
    if type(kind) in (click.IntRange, click.FloatRange):
        interval = (
            f"{'(' if kind.min_open else '['}{kind.min}..{kind.max}{')' if kind.max_open else ']'}"
        )
        return f"{kind.name}: {interval}" + (" (clamped)" if kind.clamp else "")
    if type(kind) in (click.File, click.Path):
        return "file/path (lexical only)"
    if type(kind) is click.Tuple:
        return "tuple: " + ", ".join(_type_label(item) for item in kind.types)
    if type(kind) in {
        type(click.STRING),
        type(click.INT),
        type(click.FLOAT),
        type(click.BOOL),
        type(click.UUID),
        type(click.UNPROCESSED),
        click.DateTime,
    }:
        return kind.name
    return f"{type(kind).__name__} (custom conversion not validated)"


def _default_label(value) -> str:
    if callable(value):
        return "dynamic (not evaluated)"
    if value is None or value is getattr(click.core, "UNSET", None):
        return "—"
    if type(value) in (str, int, float, bool):
        return json.dumps(value, ensure_ascii=True)
    if type(value) in (tuple, list) and all(
        type(item) in (str, int, float, bool) for item in value
    ):
        return json.dumps(value, ensure_ascii=True)
    return "nonliteral (not evaluated)"


def render_reference(root: click.Command) -> str:
    """Render only declared metadata, never invoking defaults or formatting callbacks."""
    lines = [
        "---",
        "id: cli-commands",
        "title: CLI Commands",
        "sidebar_label: CLI Commands",
        "---",
        "",
        "# CLI Commands",
        "",
        "> **Auto-generated** from the actual Click tree by `tools/generate_docs_reference.py`.",
        "",
        "See [CLI setup and operational guidance](./cli.md). Global options precede subcommands.",
        "",
        "Examples are checked for Click syntax and inert scalar types only. File/Path values are lexical only; filesystem checks, callbacks, custom conversion and command behavior are not executed.",
        "",
    ]
    for path, command in walk_commands(root):
        lines.extend(
            [
                f"## `{' '.join(path)}`",
                "",
                _text(command.help or ""),
                "",
                "| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        with click.Context(command, info_name=path[-1]) as context:
            for parameter in command.get_params(context):
                if getattr(parameter, "hidden", False):
                    continue
                names = " / ".join((*parameter.opts, *getattr(parameter, "secondary_opts", ())))
                shape = f"nargs={parameter.nargs}"
                if getattr(parameter, "is_flag", False):
                    shape += ", flag"
                if getattr(parameter, "multiple", False):
                    shape += ", repeatable"
                env = parameter.envvar or "—"
                if isinstance(env, (tuple, list)):
                    env = ", ".join(env)
                cells = [
                    names,
                    _type_label(parameter.type),
                    "required" if parameter.required else "optional",
                    shape,
                    _default_label(parameter.default),
                    env,
                    getattr(parameter, "help", "") or "",
                ]
                lines.append("| " + " | ".join(_text(cell) for cell in cells) + " |")
        lines.append("")
    return "\n".join(lines) + "\n"


def _load_cli(root: Path) -> click.Group:
    source = (root / "Server/src").resolve()
    sys.path.insert(0, str(source))
    diagnostics = io.StringIO()
    with redirect_stderr(diagnostics), redirect_stdout(diagnostics):
        module = importlib.import_module("cli.main")
    if diagnostics.getvalue():
        raise ValueError("CLI registration emitted import diagnostics")
    if not Path(module.__file__).resolve().is_relative_to(source):
        raise ValueError("CLI import escaped the selected repository")
    # register_commands intentionally treats optional imports as optional. A repo
    # reference must not silently omit a declared group when generation is broken.
    declarations = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    registration = next(
        node.value
        for node in ast.walk(declarations)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "optional_commands"
            for target in node.targets
        )
    )
    with click.Context(module.cli) as context:
        for module_name, attribute in ast.literal_eval(registration):
            loaded = sys.modules.get(module_name)
            command = getattr(loaded, attribute, None)
            if command is None or module.cli.get_command(context, command.name) is not command:
                raise ValueError(f"Incomplete declared CLI registration: {module_name}.{attribute}")
    return module.cli


def _worker(root: Path) -> CliReference:
    def forbidden(*_args, **_kwargs):
        raise RuntimeError("CLI documentation cannot invoke commands, processes or network")

    click.Command.invoke = click.Group.invoke = click.Context.invoke = click.Context.forward = (
        forbidden
    )
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = (
        socket.getaddrinfo
    ) = forbidden
    subprocess.Popen.__init__ = forbidden
    cli = _load_cli(root)
    examples = []
    for relative in EXAMPLE_PAGES:
        page = root / relative
        for example in extract_examples(page.read_text(encoding="utf-8"), Path(relative)):
            try:
                validate_argv(cli, example.argv)
            except ExampleValidationError as error:
                raise ExampleValidationError(f"{example.page}:{example.line}: {error}") from error
            examples.append(example)
    origins = {
        name: str(Path(module.__file__).resolve())
        for name, module in tuple(sys.modules.items())
        if (name == "cli" or name.startswith("cli.")) and getattr(module, "__file__", None)
    }
    if not all(Path(value).is_relative_to(root / "Server/src") for value in origins.values()):
        raise ValueError("CLI dependency imports escaped the selected repository")
    return CliReference(
        render_reference(cli),
        tuple(" ".join(path) for path, _ in walk_commands(cli)),
        len(examples),
        origins,
    )


def prepare_cli_reference(root: Path) -> CliReference:
    """Load/validate in a child so CLI imports cannot mutate generator/test state."""
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="unity-mcp-cli-docs-") as temporary:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((str(root / "Server/src"), str(Path(__file__).parent)))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for variable in (
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "XDG_DATA_HOME",
            "UNITY_MCP_LOG_DIR",
            "TEMP",
            "TMP",
        ):
            directory = Path(temporary) / variable
            directory.mkdir()
            env[variable] = str(directory)
        for variable in (
            "UNITY_MCP_HOST",
            "UNITY_MCP_HTTP_PORT",
            "UNITY_MCP_TIMEOUT",
            "UNITY_MCP_FORMAT",
            "UNITY_MCP_INSTANCE",
        ):
            env.pop(variable, None)
        env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(root)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
    if result.returncode:
        raise ValueError(f"CLI reference validation failed:\n{result.stderr.strip()}")
    payload = json.loads(result.stdout)
    return CliReference(
        payload["markdown"],
        tuple(payload["command_paths"]),
        payload["example_count"],
        payload["module_origins"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        reference = _worker(arguments.worker.resolve())
        print(
            json.dumps(
                {
                    "markdown": reference.markdown,
                    "command_paths": reference.command_paths,
                    "example_count": reference.example_count,
                    "module_origins": reference.module_origins,
                }
            )
        )
    except (ValueError, RuntimeError, ImportError) as failure:
        print(str(failure), file=sys.stderr)
        raise SystemExit(1) from failure
