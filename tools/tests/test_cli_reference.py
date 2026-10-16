"""CLI reference and example validation must observe metadata without execution."""

from pathlib import Path
import subprocess
import sys

import click
import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from cli_example_validation import ExampleValidationError, extract_examples, validate_argv
from cli_reference import prepare_cli_reference, render_reference, walk_commands


def forbidden(*args, **kwargs):
    raise AssertionError("Documentation must not execute command behavior")


def make_tree():
    root = click.Group("unity-mcp", callback=forbidden)
    root.params = [click.Option(["--port", "-p"], type=click.IntRange(1, 65535), default=8080)]
    group = click.Group("objects", callback=forbidden)
    leaf = click.Command(
        "create",
        callback=forbidden,
        params=[
            click.Argument(["name"], required=True),
            click.Option(["--kind", "-k"], type=click.Choice(["cube", "sphere"]), default="cube"),
            click.Option(["--count"], type=click.IntRange(1, 10), required=True),
            click.Option(["--enabled/--disabled"], default=True),
        ],
    )
    group.add_command(leaf)
    root.add_command(group)
    root.add_command(group, "alias")
    return root, leaf


def test_reference_detects_option_and_argument_drift_and_keeps_alias_paths():
    root, leaf = make_tree()
    original = render_reference(root)
    assert "unity-mcp alias create" in original
    assert "unity-mcp objects create" in original
    assert "--kind" in original and "-k" in original
    assert "--disabled" in original
    assert "65535" in original and "sphere" in original
    assert "required" in original.lower()
    leaf.params.append(click.Option(["--new-option"], type=int, default=3))
    assert render_reference(root) != original
    leaf.params.pop()
    leaf.params[0].required = False
    assert render_reference(root) != original
    leaf.params[0].required = True
    assert render_reference(root) == original


@pytest.mark.parametrize("change", ["remove", "rename", "default", "type"])
def test_each_subordinate_option_contract_change_alters_reference(change):
    root, leaf = make_tree()
    before = render_reference(root)
    if change == "remove":
        leaf.params.pop(1)
    elif change == "rename":
        leaf.params[1].opts = ["--renamed"]
    elif change == "default":
        leaf.params[1].default = "sphere"
    else:
        leaf.params[1].type = click.Choice(["new-shape"])
    assert render_reference(root) != before


def test_metadata_is_escaped_for_mdx_without_evaluating_help_or_value_callbacks():
    root, _ = make_tree()
    root.help = "Use {payload} <Value> | `literal`\b"
    rendered = render_reference(root)
    assert "&#123;payload&#125;" in rendered
    assert "&lt;Value&gt;" in rendered
    assert "\\|" in rendered
    assert "\b" not in rendered


def test_lazy_groups_use_public_discovery_and_reject_incomplete_or_cyclic_trees():
    root, leaf = make_tree()

    class LazyGroup(click.Group):
        def list_commands(self, ctx):
            return ["loaded"]

        def get_command(self, ctx, name):
            return leaf if name == "loaded" else None

    root.add_command(LazyGroup("lazy"))
    assert ("unity-mcp", "lazy", "loaded") in dict(walk_commands(root))

    class BrokenGroup(LazyGroup):
        def get_command(self, ctx, name):
            return None

    root.add_command(BrokenGroup("broken"))
    with pytest.raises(ValueError, match="advertised"):
        render_reference(root)
    root.commands.pop("broken")
    root.add_command(root, "cycle")
    with pytest.raises(ValueError, match="cycle"):
        render_reference(root)


@pytest.mark.parametrize(
    "argv",
    [
        ["--port", "42", "objects", "create", "Player", "--count", "2"],
        ["alias", "create", "Two words", "--count=1", "--kind", "sphere", "--disabled"],
        ["objects", "create", "--help"],
        ["objects", "--help"],
    ],
)
def test_valid_parser_examples_never_invoke_callbacks(argv, monkeypatch):
    root, leaf = make_tree()
    for command in (root, leaf):
        for parameter in command.params:
            parameter.callback = forbidden
    monkeypatch.setattr(click.Command, "invoke", forbidden)
    monkeypatch.setattr(click.Group, "invoke", forbidden)
    monkeypatch.setattr(click.Context, "invoke", forbidden)
    monkeypatch.setattr(click.Context, "forward", forbidden)
    assert validate_argv(root, argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["missing", "--help"],
        ["objects", "missing"],
        ["--unknown", "objects"],
        ["--port", "0", "objects"],
        ["objects", "create", "Player", "--count", "11"],
        ["objects", "create", "Player", "--count", "word"],
        ["objects", "create", "Player", "--count", "1", "--kind", "invalid"],
        ["objects", "create", "--count", "1"],
        ["objects", "create", "Player"],
        ["objects", "create", "Player", "--count"],
        ["objects", "create", "Player", "--count", "1", "extra"],
        ["objects", "--port", "42", "create", "Player", "--count", "1"],
    ],
)
def test_invalid_parser_examples_report_actual_click_errors(argv):
    root, _ = make_tree()
    with pytest.raises(ExampleValidationError):
        validate_argv(root, argv)


def test_file_and_path_types_are_lexical_only_without_opening_or_reading_stdin(monkeypatch):
    root = click.Group("unity-mcp", callback=forbidden)
    root.add_command(
        click.Command(
            "file", callback=forbidden, params=[click.Argument(["path"], type=click.File("r"))]
        )
    )
    root.add_command(
        click.Command(
            "path",
            callback=forbidden,
            params=[click.Argument(["path"], type=click.Path(exists=True))],
        )
    )
    monkeypatch.setattr(click.File, "convert", forbidden)
    monkeypatch.setattr(click.Path, "convert", forbidden)
    for value in ("private-nonexistent-file", "-"):
        assert validate_argv(root, ["file", value])
        assert validate_argv(root, ["path", value])
    assert "lexical" in render_reference(root).lower()


def test_unsupported_converter_and_dynamic_default_are_not_executed():
    class CustomType(click.ParamType):
        def convert(self, value, param, ctx):
            forbidden()

    root = click.Group("unity-mcp")
    command = click.Command("custom", params=[click.Argument(["value"], type=CustomType())])
    root.add_command(command)
    with pytest.raises(ExampleValidationError, match="unsupported"):
        validate_argv(root, ["custom", "text"])
    command.params = [click.Option(["--value"], default=forbidden)]
    assert "dynamic" in render_reference(root).lower()
    with pytest.raises(ExampleValidationError, match="dynamic"):
        validate_argv(root, ["custom"])


def test_example_extraction_supports_quotes_wrappers_flags_and_precise_locations():
    page = Path("fixture.md")
    text = """# Example
```bash
unity-mcp --verbose objects create 'Two words' \\
  --count 1
uvx --python '>=3.11' --from 'https://example.invalid/pin.zip#subdirectory=Server' unity-mcp objects --help
```
"""
    examples = extract_examples(text, page)
    assert len(examples) == 2
    assert examples[0].line == 3
    assert examples[0].argv == ("--verbose", "objects", "create", "Two words", "--count", "1")
    assert examples[1].argv == ("objects", "--help")
    with pytest.raises(ExampleValidationError, match="fixture.md:2"):
        extract_examples("```bash\nunity-mcp status | cat\n```\n", page)


@pytest.mark.parametrize("executable", ["unity-mcp-extra", "uvx-other"])
def test_similar_executable_names_cannot_be_certified_as_unity_cli(executable):
    with pytest.raises(ExampleValidationError, match="unsupported executable"):
        extract_examples(f"```bash\n{executable} status\n```\n", Path("fixture.md"))


@pytest.mark.parametrize(
    "suffix", [";", "&", ">/tmp/out", "|cat", "&&echo other", "2>/tmp/out", "<input"]
)
def test_attached_shell_operators_are_rejected_without_execution(suffix):
    with pytest.raises(ExampleValidationError, match="shell operator"):
        extract_examples(f"```bash\nunity-mcp scene Fixture{suffix}\n```\n", Path("fixture.md"))


@pytest.mark.parametrize(
    "literal,value",
    [
        ('"|"', "|"),
        ("'x;y'", "x;y"),
        ('"x&y"', "x&y"),
        ('"x>y"', "x>y"),
        (r"Fixture\;", "Fixture;"),
    ],
)
def test_quoted_and_escaped_operator_characters_remain_literal_arguments(literal, value):
    examples = extract_examples(f"```bash\nunity-mcp scene {literal}\n```\n", Path("fixture.md"))
    assert examples[0].argv == ("scene", value)
    root = click.Group("unity-mcp", callback=forbidden)
    root.add_command(
        click.Command("scene", callback=forbidden, params=[click.Argument(["target"])])
    )
    assert validate_argv(root, examples[0].argv) == ("unity-mcp", "scene")


@pytest.mark.parametrize("prefix", ["env UNITY_MCP_HOST=localhost", "uv run", "poetry run"])
def test_unsupported_wrappers_cannot_silently_skip_cli_examples(prefix):
    with pytest.raises(ExampleValidationError, match="unsupported executable wrapper"):
        extract_examples(f"```bash\n{prefix} unity-mcp status\n```\n", Path("fixture.md"))


def test_unrelated_bash_setup_commands_remain_outside_cli_example_scope():
    text = "```bash\nuv sync\npython -m venv .venv\necho ready && echo finished\n```\n"
    assert extract_examples(text, Path("fixture.md")) == []


def test_embedded_hash_preserves_following_unknown_option_for_strict_parsing():
    examples = extract_examples(
        "```bash\nunity-mcp scene Fixture#suffix --not-a-real-option\n```\n", Path("fixture.md")
    )
    assert examples[0].argv == ("scene", "Fixture#suffix", "--not-a-real-option")
    root = click.Group("unity-mcp", callback=forbidden)
    root.add_command(
        click.Command("scene", callback=forbidden, params=[click.Argument(["target"])])
    )
    with pytest.raises(ExampleValidationError, match="No such option"):
        validate_argv(root, examples[0].argv)


def test_embedded_hash_cannot_hide_attached_shell_operator():
    with pytest.raises(ExampleValidationError, match="shell operator"):
        extract_examples(
            "```bash\nunity-mcp scene Fixture#suffix;echo bad\n```\n", Path("fixture.md")
        )


@pytest.mark.parametrize("target", ["Fixture#suffix", '"Fixture#suffix"', r"Fixture\#suffix"])
def test_literal_hash_and_word_boundary_comment_are_distinguished(target):
    examples = extract_examples(
        f"```bash\nunity-mcp scene {target} # comment | > --not-an-option\n```\n",
        Path("fixture.md"),
    )
    assert examples[0].argv == ("scene", "Fixture#suffix")


@pytest.mark.parametrize("argv", [[], ["--mode"]])
def test_nonliteral_flag_values_are_rejected_before_any_scalar_conversion(argv):
    calls = []

    class OpaqueFlag:
        def __str__(self):
            calls.append("str")
            return "opaque"

    command = click.Command(
        "unity-mcp",
        params=[click.Option(["--mode"], is_flag=True, flag_value=OpaqueFlag(), type=str)],
    )
    with pytest.raises(ExampleValidationError, match="nonliteral flag"):
        validate_argv(command, argv)
    assert calls == []


@pytest.mark.parametrize("kind_name", ["range", "choice", "datetime"])
def test_trusted_converter_configuration_contains_only_literal_metadata(kind_name):
    class Opaque:
        def __bool__(self):
            raise AssertionError("Opaque converter metadata must not be evaluated")

        def __le__(self, other):
            raise AssertionError("Opaque bounds must not be compared")

        def __str__(self):
            raise AssertionError("Opaque converter metadata must not be formatted")

    if kind_name == "range":
        kind = click.IntRange()
        kind.min = Opaque()
    elif kind_name == "choice":
        kind = click.Choice(["value"])
        kind.case_sensitive = Opaque()
    else:
        kind = click.DateTime()
        kind.formats = [Opaque()]
    command = click.Command("unity-mcp", params=[click.Option(["--value"], type=kind)])
    with pytest.raises(ExampleValidationError, match="unsupported nonliteral"):
        validate_argv(command, ["--value", "1"])
    assert "not validated" in render_reference(command)


def test_parent_import_isolation_and_real_authored_examples(monkeypatch):
    root = TOOLS.parent
    before = click.Group.resolve_command
    loaded_before = set(sys.modules)
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    first = prepare_cli_reference(root)
    monkeypatch.setenv("PYTHONHASHSEED", "77")
    second = prepare_cli_reference(root)
    assert first.markdown == second.markdown
    assert first.example_count > 0
    assert "unity-mcp play-scenario" in first.command_paths
    assert "unity-mcp custom_tool list" in first.command_paths
    assert "unity-mcp tool list" in first.command_paths
    assert all(
        Path(path).resolve().is_relative_to(root / "Server/src")
        for path in first.module_origins.values()
    )
    assert click.Group.resolve_command is before
    assert not (set(sys.modules) - loaded_before) & {"cli.main", "cli.commands.editor"}


def test_missing_declared_optional_registration_fails_before_reference_generation(tmp_path):
    package = tmp_path / "Server/src/cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text(
        "import click\ncli = click.Group('unity-mcp')\noptional_commands = [('cli.commands.missing', 'missing')]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Incomplete declared CLI registration"):
        prepare_cli_reference(tmp_path)
    assert not (tmp_path / "website").exists()


def test_actual_worker_reports_bad_subordinate_option_with_page_and_line(tmp_path):
    package = tmp_path / "Server/src/cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text(
        "import click\ncli = click.Group('unity-mcp')\noptional_commands = []\n"
        "def forbidden(): raise AssertionError('Command callback ran')\n"
        "cli.add_command(click.Command('show', callback=forbidden, params=[click.Option(['--count'], type=int)]))\n",
        encoding="utf-8",
    )
    for relative in (
        "guides/cli.md",
        "guides/cli-examples.md",
        "reference/cli.md",
        "guides/multi-instance.md",
    ):
        page = tmp_path / "website/docs" / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("", encoding="utf-8")
    page = tmp_path / "website/docs/guides/cli.md"
    page.write_text("```bash\nunity-mcp show --unknown-option 3\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"website.docs.guides.cli.md:2:.*No such option"):
        prepare_cli_reference(tmp_path)
    assert page.read_text(encoding="utf-8").startswith("```bash\n")


def test_importing_helpers_and_generator_does_not_import_cli_or_patch_click():
    program = """
import click, sys
before = click.Group.resolve_command
import tools.generate_docs_reference
import tools.cli_reference
import tools.cli_example_validation
assert 'cli.main' not in sys.modules
assert click.Group.resolve_command is before
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=TOOLS.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_defaults_prompts_and_environment_do_not_run_or_override_examples(monkeypatch):
    command = click.Command(
        "unity-mcp",
        callback=forbidden,
        params=[click.Option(["--value"], type=int, envvar="DXA03_INVALID_EXAMPLE", prompt=True)],
    )
    monkeypatch.setenv("DXA03_INVALID_EXAMPLE", "invalid-integer")
    monkeypatch.setattr(click, "prompt", forbidden)
    assert validate_argv(command, []) == ("unity-mcp",)


def test_hidden_members_are_not_published_and_opaque_defaults_are_not_formatted():
    class PrivateDefault:
        def __str__(self):
            raise AssertionError("Opaque defaults must not be formatted")

        def __repr__(self):
            raise AssertionError("Opaque defaults must not be formatted")

    root, leaf = make_tree()
    root.add_command(click.Command("hidden-command", hidden=True))
    leaf.params.append(click.Option(["--secret-option"], hidden=True))
    leaf.params.append(click.Option(["--opaque"], default=PrivateDefault()))
    rendered = render_reference(root)
    assert "hidden-command" not in rendered
    assert "secret-option" not in rendered
    assert "nonliteral (not evaluated)" in rendered
