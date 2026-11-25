"""Lexical lookup keeps the original literal log tag in the request."""

import json

from click.testing import CliRunner
import pytest


@pytest.mark.parametrize("tag", ["_foo._bar", "Assets/रंग/नीला.asset"])
def test_cli_preserves_literal_log_tag_with_an_explicit_console_query(tag):
    from cli.main import cli

    query = f"Get warnings from the console in Unity; {tag} is the log tag"
    outcome = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "tool",
            "search",
            query,
            "--catalog-query",
            "read_console",
            "--intent",
            "observe",
        ],
    )
    assert outcome.exit_code == 0, outcome.output
    result = json.loads(outcome.stdout)
    assert result["query"] == query
    assert result["matched_query"] == "read_console"
    assert result["matching"] == "lexical"
    first = result["route_groups"][0]["representative"]
    assert (first["tool"], first["operation"]) == ("read_console", "get")
    assert first["arguments"] == {"action": "get", "format": "json", "page_size": 10}
