"""Real CLI read/search regressions at the fake HTTP boundary."""

import base64
import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection
from cli.utils.output import format_as_text
from services.tools import bounded_regex


@pytest.fixture
def script_response(monkeypatch):
    response = {"success": True, "data": {"contents": "first\nsecond\nthird\n"}}
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success", "result": response})

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return response, requests


@pytest.mark.parametrize("group", ["code", "script"])
@pytest.mark.parametrize(
    "bounds,expected",
    [
        ([], "first\nsecond\nthird\n"),
        (["-s", "2", "-n", "1"], "second\n"),
        (["-s", "3", "-n", "10"], "third\n"),
        (["-s", "4"], ""),
        (["-n", "1"], "first\n"),
    ],
)
def test_read_returns_requested_window_as_one_json_document(
    script_response, group, bounds, expected
):
    # Given: Unity returns all lines, including an encoded mirror.
    response, requests = script_response
    response["data"].update(
        contentsEncoded=True,
        encodedContents=base64.b64encode(response["data"]["contents"].encode()).decode(),
    )
    # When: the actual CLI reads a requested window.
    result = CliRunner().invoke(cli, ["--format", "json", group, "read", r"Assets\Foo.cs", *bounds])
    # Then: plain/encoded content agree and script forwards requested bounds.
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["contents"] == expected
    assert base64.b64decode(data["encodedContents"]).decode() == expected
    expected_params = {"action": "read", "name": "Foo", "path": "Assets"}
    if group == "script":
        if "-s" in bounds:
            expected_params["start_line"] = int(bounds[bounds.index("-s") + 1])
        if "-n" in bounds:
            expected_params["line_count"] = int(bounds[bounds.index("-n") + 1])
    assert requests[0]["params"] == expected_params


@pytest.mark.parametrize("group", ["code", "script"])
@pytest.mark.parametrize("option", ["--start-line", "--line-count"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_read_rejects_nonpositive_windows_before_http(script_response, group, option, value):
    # Given: a nonpositive line bound.
    _, requests = script_response
    # When: Click parses the command.
    result = CliRunner().invoke(cli, [group, "read", "Assets/Foo.cs", option, value])
    # Then: Unity is never contacted.
    assert result.exit_code == 2
    assert requests == []


@pytest.mark.parametrize(
    "contents,lines", [("", []), ("else\n", []), ("first\n", [1]), ("FIRST\nfirst\n", [1, 2])]
)
def test_search_returns_one_json_document_for_matches_and_empty_sources(
    script_response, contents, lines
):
    # Given: an empty, unmatched, or matched source.
    response, _ = script_response
    response["data"]["contents"] = contents
    # When: the JSON CLI searches case-insensitively.
    result = CliRunner().invoke(
        cli, ["--format", "json", "code", "search", "FIRST", "Assets/Foo.cs"]
    )
    # Then: a successful structured result includes exact 1-based locations.
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert [row["line"] for row in data["matches"]] == lines
    assert data["count"] == len(data["matches"])
    assert data["truncated"] is False


@pytest.mark.parametrize(
    "command", [["read", "Assets/Foo.cs"], ["search", "first", "Assets/Foo.cs"]]
)
def test_code_preserves_domain_error_json(script_response, command):
    # Given: Unity refuses the read.
    response, _ = script_response
    response.clear()
    response.update(success=False, error="Script not found")
    # When: the actual command runs.
    result = CliRunner().invoke(cli, ["--format", "json", "code", *command])
    # Then: the error is exactly one JSON document with failure status.
    assert result.exit_code == 1
    assert json.loads(result.stdout) == response


@pytest.mark.parametrize("pattern", ["[", "x" * (bounded_regex.MAX_PATTERN_CHARS + 1)])
def test_search_reports_invalid_or_oversize_patterns_as_json(script_response, pattern):
    # Given: a pattern rejected by compilation or the shared pattern budget.
    # When: the JSON CLI searches.
    result = CliRunner().invoke(
        cli, ["--format", "json", "code", "search", pattern, "Assets/Foo.cs"]
    )
    # Then: rejection is structured and nonzero.
    assert result.exit_code == 1
    assert json.loads(result.stdout)["success"] is False


def test_search_stops_at_requested_results_and_bounds_single_line_output(script_response):
    # Given: a very wide line containing many individual matches.
    response, _ = script_response
    response["data"]["contents"] = "a" * 100_000
    # When: only one result is requested.
    result = CliRunner().invoke(
        cli, ["--format", "json", "code", "search", "a", "Assets/Foo.cs", "-n", "1"]
    )
    # Then: output is bounded and the sentinel match only signals truncation.
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert len(data["matches"]) == data["count"] == 1
    assert data["truncated"] is True
    assert len(result.stdout) < 3000


@pytest.mark.parametrize("budget", ["source", "time", "work", "engine_time"])
def test_search_enforces_shared_regex_budgets(script_response, monkeypatch, budget):
    # Given: a deterministic exhausted shared source/time/work budget.
    response, _ = script_response
    if budget == "source":
        monkeypatch.setattr(bounded_regex, "MAX_TEXT_CHARS", 2)
    elif budget == "time":
        monkeypatch.setattr(bounded_regex, "TOTAL_TIMEOUT_SECONDS", 0)
    elif budget == "engine_time":
        monkeypatch.setattr(bounded_regex, "TIMEOUT_SECONDS", 0)
    else:
        monkeypatch.setattr(bounded_regex, "MAX_WORK_CHARS", 1)
    response["data"]["contents"] = "abc"
    # When: a search crosses that budget.
    result = CliRunner().invoke(cli, ["--format", "json", "code", "search", ".", "Assets/Foo.cs"])
    # Then: the CLI emits one bounded failure document without timing assertions.
    assert result.exit_code == 1
    assert json.loads(result.stdout)["success"] is False


@pytest.mark.parametrize("value", ["0", "-1"])
def test_search_rejects_nonpositive_max_results_before_http(script_response, value):
    # Given: a nonpositive result limit.
    _, requests = script_response
    # When: Click parses the search.
    result = CliRunner().invoke(cli, ["code", "search", ".", "Assets/Foo.cs", "-n", value])
    # Then: the invalid request never reaches Unity.
    assert result.exit_code == 2
    assert requests == []


def test_clear_history_displays_message_only_success(script_response):
    # Given: ExecuteCode returns a message-only acknowledgement.
    response, _ = script_response
    response.clear()
    response.update(success=True, message="History cleared")
    # When: the actual command runs in text mode.
    result = CliRunner().invoke(cli, ["code", "clear-history"])
    # Then: its meaningful acknowledgement appears once.
    assert result.exit_code == 0
    assert result.stdout.count("History cleared") == 1


@pytest.mark.parametrize("data", [None, {}, []])
def test_text_formatter_preserves_message_when_payload_is_empty(data):
    # Given: a successful response without meaningful payload data.
    response = {"success": True, "message": "History cleared", "data": data}
    # When: the formatter owns the acknowledgement.
    rendered = format_as_text(response)
    # Then: it returns the meaningful message.
    assert rendered == "History cleared"


def test_command_owned_success_message_is_not_duplicated(script_response):
    # Given: an existing command that prints its own success acknowledgement.
    response, _ = script_response
    response.clear()
    response.update(success=True, message="Created script: Foo.cs")
    # When: that command executes.
    result = CliRunner().invoke(cli, ["script", "create", "Foo"])
    # Then: the formatter does not repeat the command's own text.
    assert result.exit_code == 0, result.output
    assert result.stdout.count("Created script: Foo.cs") == 1


@pytest.mark.parametrize("command", [["read", "Assets/Foo.cs"], ["search", ".", "Assets/Foo.cs"]])
def test_code_transport_errors_are_one_json_document(monkeypatch, command):
    # Given: the HTTP boundary cannot connect.
    client_type = httpx.AsyncClient

    def fail(request):
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(connection, "_auth_headers", lambda _cfg: {})
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **options: client_type(transport=httpx.MockTransport(fail), **options),
    )
    # When: the actual CLI executes a read or search.
    result = CliRunner().invoke(cli, ["--format", "json", "code", *command])
    # Then: callers get one structured failure with no human-only error stream.
    assert result.exit_code == 1
    assert json.loads(result.stdout)["success"] is False
    assert result.stderr == ""


def test_bounded_matching_keeps_strict_default_and_truncation_sentinel():
    # Given: matching beyond a caller's collection limit.
    # When: strict callers retain their established overflow behavior.
    with pytest.raises(ValueError, match="match limit"):
        bounded_regex.find_matches("a", "aaaa", limit=1)
    # Then: opt-in truncation collects only one requested match plus the sentinel.
    assert len(bounded_regex.find_matches("a", "aaaa", limit=1, truncate=True)) == 2


@pytest.mark.parametrize("format_type", ["text", "table"])
@pytest.mark.parametrize("group", ["code", "script"])
def test_read_retains_plain_line_output(script_response, format_type, group):
    # Given: a source returned from Unity with a requested line window.
    # When: a human-readable format is selected.
    result = CliRunner().invoke(
        cli, ["--format", format_type, group, "read", "Assets/Foo.cs", "-s", "2", "-n", "1"]
    )
    # Then: both established human formats keep the source text directly.
    assert result.exit_code == 0, result.output
    assert result.stdout.rstrip("\n") == "second"


@pytest.mark.parametrize("format_type", ["text", "table"])
def test_search_retains_line_labels_and_multiline_case_sensitive_matching(
    script_response, format_type
):
    # Given: line anchors and mixed case distinguish the selected regex flags.
    response, _ = script_response
    response["data"]["contents"] = "first\nFIRST\nfirst\n"
    # When: a case-sensitive line-anchored search runs in a human format.
    result = CliRunner().invoke(
        cli, ["--format", format_type, "code", "search", "^FIRST$", "Assets/Foo.cs", "-c"]
    )
    # Then: the matching source line retains its established 1-based label.
    assert result.exit_code == 0, result.output
    assert "Line 2: FIRST" in result.stdout
    assert "Line 1" not in result.stdout
    assert "Line 3" not in result.stdout


def test_search_bounds_match_text_and_marks_excerpt_truncation(script_response):
    # Given: one regex match spans a very wide source line.
    response, _ = script_response
    response["data"]["contents"] = "a" * 100_000
    # When: the CLI searches for the entire line.
    result = CliRunner().invoke(cli, ["--format", "json", "code", "search", "a+", "Assets/Foo.cs"])
    # Then: serialized match/excerpt copies stay bounded, with honest truncation flags.
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["truncated"] is False
    assert data["matches"][0]["matchTruncated"] is True
    assert data["matches"][0]["contentTruncated"] is True
    assert len(result.stdout) < 3000


@pytest.mark.parametrize("group", ["code", "script"])
def test_read_handles_encoded_only_contents(script_response, group):
    # Given: a script content response available only through its encoded field.
    response, _ = script_response
    response["data"] = {
        "contentsEncoded": True,
        "encodedContents": base64.b64encode(b"first\nsecond\n").decode(),
    }
    # When: a single line is requested.
    result = CliRunner().invoke(
        cli, ["--format", "json", group, "read", "Assets/Foo.cs", "-s", "2", "-n", "1"]
    )
    # Then: the shared reader decodes and slices both representations consistently.
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["contents"] == "second\n"
    assert base64.b64decode(data["encodedContents"]) == b"second\n"
