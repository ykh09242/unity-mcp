"""Project-local context through the real Click decoder and HTTP request boundary."""

import json
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import config as config_module, connection

A_HASH = "a" * 16
B_HASH = "b" * 16


def project(root, **settings):
    """Create only explicit disposable Unity markers and a secret-free context."""
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.0", encoding="utf-8"
    )
    payload = {"schema_version": "unity-mcp/cli-context@1", "instance": A_HASH, **settings}
    (root / ".unity-mcp.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


@pytest.fixture
def transport(monkeypatch, tmp_path):
    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    monkeypatch.setattr(config_module, "_config", None)
    monkeypatch.chdir(tmp_path)
    requests, auth, options = [], [], []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "instances": []})
        return httpx.Response(200, json={"success": True, "data": {"retained": 0}})

    def client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    def headers(config):
        auth.append(config)
        return {"X-Unity-MCP-Token": "owned-context-auth-sentinel"}

    monkeypatch.setattr(connection.httpx, "AsyncClient", client)
    monkeypatch.setattr(connection, "_auth_headers", headers)
    return requests, auth, options


def invoke(*args):
    return CliRunner().invoke(
        cli, [*args, "--format", "json", "raw", "owned_mutation", '{"zero":0}']
    )


def test_automatic_context_captures_root_and_exact_target_from_nested_cwd(
    transport, monkeypatch, tmp_path
):
    # Given: a nested working directory within one selected Unity project.
    root = project(tmp_path / "A", port=9191)
    nested = root / "Assets/Sub Folder"
    nested.mkdir()
    monkeypatch.chdir(nested)
    # When: the real CLI performs a command without explicit connection flags.
    result = invoke()
    # Then: one guarded request carries the captured root and full target.
    assert result.exit_code == 0, result.output
    requests, _, options = transport
    assert len(requests) == 1
    assert str(requests[0].url) == "http://127.0.0.1:9191/api/context-command"
    assert json.loads(requests[0].content) == {
        "type": "owned_mutation",
        "params": {"zero": 0},
        "unity_instance": A_HASH,
        "expected_project": {"root": str(root.resolve())},
    }
    assert options == [{"trust_env": False, "follow_redirects": False}]


def test_context_selection_is_reloaded_for_each_click_invocation(transport, monkeypatch, tmp_path):
    # Given: same-name projects with different roots, hashes, ports and formats.
    roots = [
        project(tmp_path / "parentA/Same", port=9191),
        project(tmp_path / "parentB/Same", port=9292, instance=B_HASH),
    ]
    # When: the same Python process invokes the CLI from each project in turn.
    for root in roots:
        monkeypatch.chdir(root)
        assert invoke().exit_code == 0
    # Then: cached configuration never transfers A's selection to B.
    requests, _, _ = transport
    assert [request.url.port for request in requests] == [9191, 9292]
    assert [json.loads(request.content)["expected_project"]["root"] for request in requests] == [
        str(r.resolve()) for r in roots
    ]
    assert [json.loads(request.content)["unity_instance"] for request in requests] == [
        A_HASH,
        B_HASH,
    ]


@pytest.mark.parametrize(
    "field, env, configured, environment, flag, explicit",
    [
        ("port", "UNITY_MCP_HTTP_PORT", 9191, "9292", "--port", "8080"),
        ("host", "UNITY_MCP_HOST", "localhost", "::1", "--host", "127.0.0.1"),
        ("timeout", "UNITY_MCP_TIMEOUT", 12, "24", "--timeout", "30"),
        ("instance", "UNITY_MCP_INSTANCE", A_HASH, B_HASH, "--instance", "c" * 16),
    ],
)
def test_explicit_default_valued_flags_beat_environment_and_context(
    transport, monkeypatch, tmp_path, field, env, configured, environment, flag, explicit
):
    # Given: each precedence layer supplies a different valid value.
    root = project(tmp_path / "A", **{field: configured})
    monkeypatch.chdir(root)
    monkeypatch.setenv(env, environment)
    # When: the explicit flag equals a built-in default where one exists.
    result = invoke(flag, explicit)
    # Then: Click's parameter source wins without discarding the expected root.
    assert result.exit_code == 0, result.output
    actual = transport[1][0]
    attribute = "unity_instance" if field == "instance" else field
    assert str(getattr(actual, attribute)) == explicit
    assert json.loads(transport[0][0].content)["expected_project"]["root"] == str(root.resolve())


def test_partial_environment_override_retains_other_context_fields(
    transport, monkeypatch, tmp_path
):
    # Given: environment overrides only the port, and a file provides target/timeout.
    root = project(tmp_path / "A", port=9191, timeout=17)
    monkeypatch.chdir(root)
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "9292")
    # When: the real CLI resolves its settings.
    result = invoke()
    # Then: independent layers remain independent.
    assert result.exit_code == 0, result.output
    assert transport[1][0].port == 9292
    assert transport[1][0].timeout == 17
    assert json.loads(transport[0][0].content)["unity_instance"] == A_HASH


def test_nearest_project_boundary_does_not_inherit_outer_context(transport, monkeypatch, tmp_path):
    # Given: a nested Unity project has no context, unlike its parent.
    outer = project(tmp_path / "Outer", port=9191)
    inner = project(outer / "Nested")
    (inner / ".unity-mcp.json").unlink()
    monkeypatch.chdir(inner)
    # When: the CLI uses ordinary defaults in the nested project.
    result = invoke()
    # Then: the outer project's connection is never inherited.
    assert result.exit_code == 0, result.output
    assert str(transport[0][0].url) == "http://127.0.0.1:8080/api/command"


@pytest.mark.parametrize(
    "contents",
    [
        '{"schema_version":"unity-mcp/cli-context@1","token":"secret-context-sentinel"}',
        '{"schema_version":"unity-mcp/cli-context@1","port":8080,"port":9191}',
        '{"schema_version":"other"}',
        '{"schema_version":"unity-mcp/cli-context@1","port":true}',
        '{"schema_version":"unity-mcp/cli-context@1","timeout":0}',
        '{"schema_version":"unity-mcp/cli-context@1","format":"csv"}',
        '{"schema_version":"unity-mcp/cli-context@1","instance":"partial"}',
        '{"schema_version":"unity-mcp/cli-context@1","host":"https://secret-context-sentinel"}',
        '{"schema_version":"unity-mcp/cli-context@1","port":NaN}',
        '{"schema_version":',
        " " * 8193,
    ],
)
def test_invalid_context_refuses_before_auth_or_http_without_echoing_contents(
    transport, monkeypatch, tmp_path, contents
):
    # Given: a selected file violates its bounded non-executable schema.
    root = project(tmp_path / "A")
    (root / ".unity-mcp.json").write_text(contents, encoding="utf-8")
    monkeypatch.chdir(root)
    # When: the real CLI would otherwise mutate.
    result = invoke()
    # Then: input rejection cannot send credentials or disclose file payloads.
    assert result.exit_code == 2, result.output
    assert transport[0] == transport[1] == []
    assert "secret-context-sentinel" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("selector", ["", "Same", "12345", "Same@abcd", "6401"])
def test_context_requires_full_explicit_target(transport, monkeypatch, tmp_path, selector):
    # Given: context mode has no complete native path hash.
    root = project(tmp_path / "A", instance=selector)
    monkeypatch.chdir(root)
    # When: attempting a command.
    result = invoke()
    # Then: it cannot use first-session or name/prefix selection.
    assert result.exit_code == 2, result.output
    assert transport[0] == transport[1] == []


def test_explicit_context_and_expected_project_override_remain_separate(
    transport, monkeypatch, tmp_path
):
    # Given: A is cwd, but explicit context and expected root both identify B.
    a = project(tmp_path / "A", port=9191)
    b = project(tmp_path / "B", port=9292, instance=B_HASH)
    monkeypatch.chdir(a)
    # When: explicitly selecting B's file.
    result = invoke("--project-context", str(b / ".unity-mcp.json"))
    # Then: captured expectation belongs to B, never to A's cwd.
    assert result.exit_code == 0, result.output
    body = json.loads(transport[0][0].content)
    assert body["expected_project"]["root"] == str(b.resolve())
    assert body["unity_instance"] == B_HASH
    assert transport[0][0].url.port == 9292


def test_instance_override_alone_cannot_disable_expected_root(transport, monkeypatch, tmp_path):
    # Given: A context and an explicit B target.
    a = project(tmp_path / "A")
    monkeypatch.chdir(a)
    # When: only the target is overridden.
    result = invoke("--instance", B_HASH)
    # Then: server-side validation still receives A's expectation.
    assert result.exit_code == 0, result.output
    body = json.loads(transport[0][0].content)
    assert body["unity_instance"] == B_HASH
    assert body["expected_project"]["root"] == str(a.resolve())


def test_explicit_expect_project_overrides_context_without_changing_target(
    transport, monkeypatch, tmp_path
):
    # Given: context A, explicit target B and explicit expected project B.
    a = project(tmp_path / "A")
    b = project(tmp_path / "B", instance=B_HASH)
    monkeypatch.chdir(a)
    # When: deliberately overriding both independent selections.
    result = invoke("--instance", B_HASH, "--expect-project", str(b))
    # Then: only the explicitly verified B expectation reaches transport.
    assert result.exit_code == 0, result.output
    body = json.loads(transport[0][0].content)
    assert body["expected_project"]["root"] == str(b.resolve())
    assert body["unity_instance"] == B_HASH


def test_disabling_automatic_context_keeps_legacy_transport(transport, monkeypatch, tmp_path):
    # Given: an automatic context that is deliberately disabled.
    root = project(tmp_path / "A", port=9191)
    monkeypatch.chdir(root)
    # When: no explicit expectation replaces it.
    result = invoke("--no-project-context")
    # Then: old payload/endpoint semantics remain unchanged.
    assert result.exit_code == 0, result.output
    assert str(transport[0][0].url) == "http://127.0.0.1:8080/api/command"
    assert "expected_project" not in json.loads(transport[0][0].content)


def test_explicit_context_conflicts_with_disabling_context(transport, tmp_path):
    # Given: mutually exclusive explicit selections.
    root = project(tmp_path / "A")
    # When: both are supplied.
    result = invoke("--project-context", str(root / ".unity-mcp.json"), "--no-project-context")
    # Then: local parse failure happens before auth/network.
    assert result.exit_code == 2
    assert transport[0] == transport[1] == []


def test_context_old_server_cannot_fall_back_to_mutation_endpoint(transport, monkeypatch, tmp_path):
    # Given: an old server only implements the unguarded mutation route.
    root = project(tmp_path / "A")
    monkeypatch.chdir(root)
    real_client = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            404 if request.url.path == "/api/context-command" else 200, json={"success": True}
        )

    # Use the original concrete client, not the already patched fixture factory.
    real_client = import_module("httpx._client").AsyncClient
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    # When: the CLI attempts its context-bound operation.
    result = invoke()
    # Then: old-server refusal never permits an unguarded retry.
    assert result.exit_code == 1, result.output
    assert [r.url.path for r in requests] == ["/api/context-command"]
    assert "owned-context-auth-sentinel" not in result.output


def test_context_redirect_refuses_without_credential_forwarding(transport, monkeypatch, tmp_path):
    # Given: a local endpoint redirects to another command URL.
    root = project(tmp_path / "A")
    monkeypatch.chdir(root)
    requests = []
    real_client = import_module("httpx._client").AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "/api/command"})

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    # When: executing a guarded command.
    result = invoke()
    # Then: credentials and commands are never sent to the redirect destination.
    assert result.exit_code == 1, result.output
    assert [r.url.path for r in requests] == ["/api/context-command"]
    assert "owned-context-auth-sentinel" not in result.output


def test_context_verbose_output_redacts_root_and_credentials(transport, monkeypatch, tmp_path):
    # Given: a project path containing a privacy sentinel.
    root = project(tmp_path / "private-root-sentinel")
    monkeypatch.chdir(root)
    # When: verbose mode prints request diagnostics.
    result = invoke("--verbose")
    # Then: new expected-root metadata is redacted; auth is never logged.
    assert result.exit_code == 0, result.output
    assert "private-root-sentinel" not in result.output
    assert "owned-context-auth-sentinel" not in result.output


@pytest.mark.parametrize("mode", ["unreadable", "symlink"])
def test_unsafe_context_file_never_reaches_auth(transport, monkeypatch, tmp_path, mode):
    # Given: the selected regular-file boundary cannot be admitted safely.
    root = project(tmp_path / "A")
    selected = root / ".unity-mcp.json"
    monkeypatch.chdir(root)
    original_open, original_symlink = Path.open, Path.is_symlink

    def guarded_open(path, *args, **kwargs):
        if path == selected and mode == "unreadable":
            raise PermissionError("secret-permission-sentinel")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: (path == selected and mode == "symlink") or original_symlink(path),
    )
    # When: the context would otherwise select a live server.
    result = invoke()
    # Then: no fallback, auth load, native request or underlying error disclosure.
    assert result.exit_code == 2, result.output
    assert transport[0] == transport[1] == []
    assert "secret-permission-sentinel" not in result.output


def test_utf8_bom_context_remains_bounded_and_decodable(transport, monkeypatch, tmp_path):
    # Given: an otherwise valid context with an explicit UTF-8 BOM.
    root = project(tmp_path / "A", port=9191)
    selected = root / ".unity-mcp.json"
    selected.write_bytes(b"\xef\xbb\xbf" + selected.read_bytes())
    monkeypatch.chdir(root)
    # When: the actual CLI decodes the file.
    result = invoke()
    # Then: it sends the ordinary guarded expectation once.
    assert result.exit_code == 0, result.output
    assert transport[0][0].url.port == 9191
    assert json.loads(transport[0][0].content)["expected_project"]["root"] == str(root.resolve())


@pytest.mark.parametrize("command", ["instances", "raw"])
def test_context_without_target_keeps_discovery_but_never_admits_command(
    transport, monkeypatch, tmp_path, command
):
    # Given: a valid context has not yet recorded an instance discovered by the user.
    root = project(tmp_path / "A")
    selected = root / ".unity-mcp.json"
    data = json.loads(selected.read_text(encoding="utf-8"))
    del data["instance"]
    selected.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(root)
    # When: discovering instances or attempting an actual command.
    args = (
        ["--format", "json", "instances"]
        if command == "instances"
        else ["--format", "json", "raw", "owned_mutation"]
    )
    result = CliRunner().invoke(cli, args)
    # Then: discovery stays read-only; native dispatch requires an explicit full target.
    if command == "instances":
        assert result.exit_code == 0, result.output
        assert [request.url.path for request in transport[0]] == ["/api/instances"]
    else:
        assert result.exit_code == 2, result.output
        assert transport[0] == transport[1] == []
