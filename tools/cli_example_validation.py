"""Validate documented CLI syntax using inert copies of Click parameter metadata."""

from dataclasses import dataclass
import copy
from pathlib import Path
import re
import shlex

import click


class ExampleValidationError(ValueError):
    """A documentation example is invalid or needs unsupported runtime behavior."""


@dataclass(frozen=True)
class Example:
    page: Path
    line: int
    argv: tuple[str, ...]


def inert_type(original: click.ParamType) -> click.ParamType:
    """Build trusted scalar converters without calling original/custom converters."""
    primitives = {
        type(click.STRING): click.STRING,
        type(click.INT): click.INT,
        type(click.FLOAT): click.FLOAT,
        type(click.BOOL): click.BOOL,
        type(click.UUID): click.UUID,
        type(click.UNPROCESSED): click.UNPROCESSED,
    }
    if type(original) in primitives:
        return type(primitives[type(original)])()
    if type(original) in (click.File, click.Path):
        return click.types.StringParamType()
    if type(original) is click.Choice:
        if type(original.choices) not in (tuple, list) or not all(
            type(value) in (str, int, float, bool) for value in original.choices
        ):
            raise ExampleValidationError("unsupported nonliteral choices")
        if type(original.case_sensitive) is not bool:
            raise ExampleValidationError("unsupported nonliteral choice configuration")
        return click.Choice(tuple(original.choices), case_sensitive=original.case_sensitive)
    if type(original) in (click.IntRange, click.FloatRange):
        if any(
            value is not None and type(value) not in (int, float, bool)
            for value in (original.min, original.max)
        ) or any(
            type(value) is not bool
            for value in (original.min_open, original.max_open, original.clamp)
        ):
            raise ExampleValidationError("unsupported nonliteral range configuration")
        return type(original)(
            original.min, original.max, original.min_open, original.max_open, original.clamp
        )
    if type(original) is click.Tuple:
        if type(original.types) not in (tuple, list):
            raise ExampleValidationError("unsupported nonliteral tuple configuration")
        return click.Tuple(tuple(inert_type(item) for item in original.types))
    if type(original) is click.DateTime:
        if type(original.formats) not in (tuple, list) or not all(
            type(value) is str for value in original.formats
        ):
            raise ExampleValidationError("unsupported nonliteral datetime configuration")
        return click.DateTime(tuple(original.formats))
    raise ExampleValidationError(f"unsupported parameter type {type(original).__name__}")


def _literal_default(value) -> bool:
    if value is None or type(value) in (str, int, float, bool):
        return True
    return type(value) in (tuple, list) and all(_literal_default(item) for item in value)


def _parameters(command: click.Command, context: click.Context) -> list[click.Parameter]:
    """Strip callbacks, prompts, external defaults and filesystem converters."""
    parameters = []
    help_option = command.get_help_option(context)
    for original in command.get_params(context):
        if type(original) not in (click.Option, click.Argument):
            raise ExampleValidationError("unsupported custom parameter class")
        parameter = copy.copy(original)
        parameter.callback = None
        parameter.type = inert_type(original.type)
        default = parameter.default
        if callable(default):
            raise ExampleValidationError("dynamic default is not evaluated for documentation")
        if not _literal_default(default):
            # Click's unset sentinel is metadata, not an application default factory.
            if default is not getattr(click.core, "UNSET", None):
                raise ExampleValidationError("unsupported nonliteral default")
        parameter.envvar = None
        if isinstance(parameter, click.Option):
            if not _literal_default(parameter.flag_value) and parameter.flag_value is not getattr(
                click.core, "UNSET", None
            ):
                raise ExampleValidationError("unsupported nonliteral flag_value")
            parameter.prompt = None
            parameter.prompt_required = False
            parameter.allow_from_autoenv = False
        if (
            original is help_option
            or parameter.is_eager
            and "--version" in getattr(parameter, "opts", ())
        ):
            parameter.name = "_documentation_help"
            parameter.expose_value = True
        parameters.append(parameter)
    return parameters


def validate_argv(root: click.Command, argv: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Parse each public command level; never invoke product callbacks or converters.

    The resilient probe is used solely to recognize an eager metadata-only help
    request. Every ordinary example is parsed again strictly, including required
    arguments and scalar types. File/Path semantics are intentionally lexical only.
    """
    command, arguments, parent = root, list(argv), None
    path = ("unity-mcp",)
    contexts = []
    try:
        while True:
            context = click.Context(command, info_name=path[-1], parent=parent)
            parameters = _parameters(command, context)
            group = isinstance(command, click.Group)
            view = click.Command(
                path[-1],
                params=parameters,
                add_help_option=False,
                context_settings={"allow_extra_args": group, "allow_interspersed_args": not group},
            )
            probe = view.make_context(
                path[-1], list(arguments), parent=parent, resilient_parsing=True
            )
            contexts.append(probe)
            if probe.params.get("_documentation_help"):
                return path
            parsed = view.make_context(path[-1], list(arguments), parent=parent)
            contexts.append(parsed)
            if not group:
                return path
            remaining = parsed.args
            if not remaining:
                if command.invoke_without_command:
                    return path
                raise ExampleValidationError(f"{' '.join(path)}: missing command")
            public_name, child, arguments = command.resolve_command(parsed, remaining)
            if child is None:
                raise ExampleValidationError(f"{' '.join(path)}: missing command")
            path += (public_name,)
            command, parent = child, parsed
    except click.ClickException as error:
        raise ExampleValidationError(f"{' '.join(path)}: {error.format_message()}") from error
    finally:
        for context in reversed(contexts):
            context.close()


def extract_examples(text: str, page: Path) -> list[Example]:
    """Extract standalone bash examples and recognized uvx wrappers without execution."""
    examples = []
    for fence in re.finditer(r"^```bash\s*\n(.*?)^```", text, re.MULTILINE | re.DOTALL):
        pending, start = "", 0
        for offset, line in enumerate(fence.group(1).splitlines()):
            if not pending:
                start = text.count("\n", 0, fence.start(1)) + offset + 1
            pending += (
                line.strip().removesuffix("\\") if line.rstrip().endswith("\\") else line.strip()
            )
            if line.rstrip().endswith("\\"):
                pending += " "
                continue
            raw, pending = pending, ""
            # Preserve quote provenance while finding actual shell comments and
            # operators. An embedded hash belongs to its word, unlike a hash
            # starting a new unquoted word.
            quote, escaped, word_start, shell_operator = None, False, True, False
            for position, character in enumerate(raw):
                if escaped:
                    escaped = False
                    word_start = False
                elif character == "\\" and quote != "'":
                    escaped = True
                    word_start = False
                elif quote is not None:
                    if character == quote:
                        quote = None
                elif character in ("'", '"'):
                    quote = character
                    word_start = False
                elif character == "#" and word_start:
                    raw = raw[:position]
                    break
                elif character in ";&|<>":
                    shell_operator = True
                    word_start = True
                else:
                    word_start = character.isspace()
            if "unity-mcp" not in raw and "uvx" not in raw:
                continue
            try:
                words = shlex.split(raw, comments=False)
            except ValueError as error:
                raise ExampleValidationError(f"{page}:{start}: invalid shell quoting") from error
            if not re.match(r"(?:unity-mcp|uvx)\b", raw) and not any(
                word in ("unity-mcp", "uvx") for word in words
            ):
                continue
            if not words or words[0] not in ("unity-mcp", "uvx"):
                raise ExampleValidationError(f"{page}:{start}: unsupported executable wrapper")
            if words and words[0] == "uvx":
                position = 1
                while position < len(words) and words[position] in ("--python", "--from"):
                    position += 2
                if position >= len(words) or words[position] != "unity-mcp":
                    # Server launch examples belong to existing server/pin checks.
                    if position < len(words) and words[position] == "mcp-for-unity":
                        continue
                    raise ExampleValidationError(f"{page}:{start}: unsupported executable wrapper")
                words = words[position:]
            if shell_operator:
                raise ExampleValidationError(f"{page}:{start}: unsupported shell operator")
            examples.append(Example(page, start, tuple(words[1:])))
        if pending:
            raise ExampleValidationError(f"{page}:{start}: unfinished shell continuation")
    return examples
